"""Persistent URL frontier with Bloom prefilter and SQLite queue."""

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse


class PersistentBloomFilter:
    """支持懒持久化（dirty flag + 阈值 flush）和 might_contain 预检的 Bloom。"""

    def __init__(self, bits_path: Path, capacity: int = 1_000_000,
                 error_rate: float = 0.01,
                 flush_every: int = 10_000,
                 flush_interval_seconds: float = 30.0):
        self.bits_path = bits_path
        self.capacity = max(1, int(capacity))
        self.error_rate = min(max(float(error_rate), 0.0001), 0.5)
        bit_count = int(-self.capacity * math.log(self.error_rate) / (math.log(2) ** 2))
        self.bit_count = max(8, bit_count)
        self.hash_count = max(1, int((self.bit_count / self.capacity) * math.log(2)))
        self.byte_count = (self.bit_count + 7) // 8
        self._lock = threading.Lock()
        self._bits = self._load_bits()
        self._dirty = False
        self._dirty_count = 0
        self._last_flush = time.time()
        self.flush_every = max(1, int(flush_every))
        self.flush_interval_seconds = max(1.0, float(flush_interval_seconds))

    def _load_bits(self) -> bytearray:
        if self.bits_path.exists() and self.bits_path.stat().st_size == self.byte_count:
            return bytearray(self.bits_path.read_bytes())
        return bytearray(self.byte_count)

    def _hashes(self, item: str):
        digest = hashlib.sha256(item.encode("utf-8")).digest()
        h1 = int.from_bytes(digest[:16], "big")
        h2 = int.from_bytes(digest[16:], "big") or 1
        for index in range(self.hash_count):
            yield (h1 + index * h2) % self.bit_count

    def add(self, item: str) -> None:
        with self._lock:
            for bit in self._hashes(item):
                self._bits[bit // 8] |= 1 << (bit % 8)
            self._dirty = True
            self._dirty_count += 1

    def might_contain(self, item: str) -> bool:
        with self._lock:
            return all(
                self._bits[bit // 8] & (1 << (bit % 8))
                for bit in self._hashes(item)
            )

    def maybe_flush(self) -> bool:
        """根据 dirty 阈值或时间间隔决定是否落盘。返回是否真的写盘了。"""
        now = time.time()
        with self._lock:
            if not self._dirty:
                return False
            if (self._dirty_count < self.flush_every
                    and (now - self._last_flush) < self.flush_interval_seconds):
                return False
        return self.flush()

    def flush(self) -> bool:
        with self._lock:
            if not self._dirty:
                return False
            self.bits_path.parent.mkdir(exist_ok=True)
            self.bits_path.write_bytes(bytes(self._bits))
            self._dirty = False
            self._dirty_count = 0
            self._last_flush = time.time()
            return True

    def save(self) -> None:
        """兼容旧 API。强制 flush。"""
        self.flush()

    def reset(self) -> None:
        """清空位图（rebuild 用）。"""
        with self._lock:
            self._bits = bytearray(self.byte_count)
            self._dirty = True

    def info(self) -> dict:
        return {
            "capacity": self.capacity,
            "error_rate": self.error_rate,
            "bit_count": self.bit_count,
            "hash_count": self.hash_count,
            "bytes": self.byte_count,
            "path": str(self.bits_path),
            "dirty": self._dirty,
            "dirty_count": self._dirty_count,
            "flush_every": self.flush_every,
        }


class URLFrontier:
    def __init__(self, db_path: Path, bloom_path: Path, logger,
                 bloom_capacity: int = 1_000_000, bloom_error_rate: float = 0.01):
        self.db_path = db_path
        self.logger = logger
        self._lock = threading.RLock()
        self.bloom = PersistentBloomFilter(
            bloom_path,
            capacity=bloom_capacity,
            error_rate=bloom_error_rate,
        )
        self.db_path.parent.mkdir(exist_ok=True)
        self._init_db()

    @staticmethod
    def canonical_url(url: str) -> str:
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        if scheme not in {"http", "https"} or not netloc:
            raise ValueError("only http/https URLs with host are supported")
        path = parsed.path or "/"
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{scheme}://{netloc}{path}{query}"

    @staticmethod
    def url_hash(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    @staticmethod
    def domain(url: str) -> str:
        return urlparse(url).netloc.lower()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS frontier_urls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    url_hash TEXT NOT NULL UNIQUE,
                    domain TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    kind TEXT NOT NULL DEFAULT 'page',
                    depth INTEGER NOT NULL DEFAULT 0,
                    parent_url TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    locked_at REAL NOT NULL DEFAULT 0,
                    completed_at REAL NOT NULL DEFAULT 0
                )
            """)
            # v4.0: lease_token 用于多进程 CAS 租约抢占
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(frontier_urls)")}
            if "lease_token" not in cols:
                conn.execute(
                    "ALTER TABLE frontier_urls ADD COLUMN lease_token TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_frontier_status_priority "
                "ON frontier_urls(status, priority DESC, created_at ASC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_frontier_domain_status "
                "ON frontier_urls(domain, status)"
            )

    def rebuild_bloom_from_db(self) -> int:
        """从 SQLite 全量重建 Bloom（启动时大小不匹配或显式调用）。"""
        self.bloom.reset()
        count = 0
        with self._connect() as conn:
            for row in conn.execute("SELECT url_hash FROM frontier_urls"):
                self.bloom.add(row["url_hash"])
                count += 1
        self.bloom.flush()
        return count

    def add_urls(self, urls: list[str], priority: int = 0, kind: str = "page",
                 depth: int = 0, parent_url: str = "", payload: dict | None = None) -> dict:
        now = time.time()
        added = 0
        skipped = 0
        invalid = 0
        bloom_skipped_sql = 0  # v4.0: bloom 预检命中"可能已存在"，仍走 INSERT OR IGNORE 兜底
        payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connect() as conn:
            for raw_url in urls:
                if not raw_url or not isinstance(raw_url, str):
                    invalid += 1
                    continue
                try:
                    url = self.canonical_url(raw_url)
                    url_hash = self.url_hash(url)
                    domain = self.domain(url)
                    bloom_hit = self.bloom.might_contain(url_hash)
                    if bloom_hit:
                        bloom_skipped_sql += 1
                    self.bloom.add(url_hash)
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO frontier_urls
                        (url, url_hash, domain, priority, status, kind, depth, parent_url,
                         payload_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                        """,
                        (url, url_hash, domain, int(priority), kind, int(depth),
                         parent_url, payload_json, now, now),
                    )
                    if cursor.rowcount:
                        added += 1
                    else:
                        skipped += 1
                except Exception:
                    invalid += 1
            conn.commit()
        # v4.0: 用 maybe_flush 替代每批强制 save，性能从 9.6MB/批 降到几乎为 0
        self.bloom.maybe_flush()
        return {
            "added": added,
            "skipped": skipped,
            "invalid": invalid,
            "total": len(urls),
            "bloom_prefilter_hit": bloom_skipped_sql,
        }

    def next_batch(self, limit: int = 10, domain: str = "", worker_id: str = "local",
                   lease_seconds: int = 900) -> list[dict]:
        """领取一批 URL；用 lease_token CAS 确保多进程不重领。"""
        limit = max(1, min(int(limit), 500))
        now = time.time()
        expired_before = now - max(1, int(lease_seconds))
        token = uuid.uuid4().hex
        with self._lock, self._connect() as conn:
            params: list = [expired_before]
            where = "(status = 'queued' OR (status = 'running' AND locked_at < ?))"
            if domain:
                where += " AND domain = ?"
                params.append(domain.lower())
            rows = conn.execute(
                f"""
                SELECT * FROM frontier_urls
                WHERE {where}
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            if not rows:
                return []
            # CAS 抢占：仅当原 lease_token 未变时才能更新成功，
            # 防止两个进程同时 SELECT 到相同行后争抢
            won_rows: list = []
            attempt_increment_by_id: dict[int, int] = {}
            for row in rows:
                old_token = row["lease_token"] or ""
                row_id = row["id"]
                # running 状态过期重领 → attempts +1；queued → attempts +1（首次领取）
                cur = conn.execute(
                    """
                    UPDATE frontier_urls
                    SET status = 'running', worker_id = ?, locked_at = ?,
                        attempts = attempts + 1, updated_at = ?, lease_token = ?
                    WHERE id = ? AND COALESCE(lease_token, '') = ?
                    """,
                    [worker_id, now, now, token, row_id, old_token],
                )
                if cur.rowcount:
                    won_rows.append(row)
                    attempt_increment_by_id[row_id] = 1
            conn.commit()
            return [self._row_to_dict(row) for row in won_rows]

    def mark_done(self, items: list[int | str]) -> int:
        return self._mark(items, "done", "")

    def mark_failed(self, items: list[int | str], error: str = "", retry: bool = True) -> int:
        status = "queued" if retry else "failed"
        return self._mark(items, status, error[:500])

    def _mark(self, items: list[int | str], status: str, error: str) -> int:
        now = time.time()
        ids = [item for item in items if isinstance(item, int) or str(item).isdigit()]
        urls = [self.canonical_url(str(item)) for item in items if not (isinstance(item, int) or str(item).isdigit())]
        updated = 0
        with self._lock, self._connect() as conn:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                cursor = conn.execute(
                    f"""
                    UPDATE frontier_urls
                    SET status = ?, error = ?, updated_at = ?,
                        lease_token = '',
                        completed_at = CASE WHEN ? = 'done' THEN ? ELSE completed_at END
                    WHERE id IN ({placeholders})
                    """,
                    [status, error, now, status, now, *ids],
                )
                updated += cursor.rowcount
            if urls:
                hashes = [self.url_hash(url) for url in urls]
                placeholders = ",".join("?" for _ in hashes)
                cursor = conn.execute(
                    f"""
                    UPDATE frontier_urls
                    SET status = ?, error = ?, updated_at = ?,
                        lease_token = '',
                        completed_at = CASE WHEN ? = 'done' THEN ? ELSE completed_at END
                    WHERE url_hash IN ({placeholders})
                    """,
                    [status, error, now, status, now, *hashes],
                )
                updated += cursor.rowcount
            conn.commit()
        return updated

    def stats(self) -> dict:
        with self._connect() as conn:
            status_counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM frontier_urls GROUP BY status"
                )
            }
            domains = [
                {"domain": row["domain"], "count": row["count"]}
                for row in conn.execute(
                    """
                    SELECT domain, COUNT(*) AS count
                    FROM frontier_urls
                    GROUP BY domain
                    ORDER BY count DESC
                    LIMIT 20
                    """
                )
            ]
        return {
            "db_path": str(self.db_path),
            "status_counts": status_counts,
            "domains": domains,
            "bloom": self.bloom.info(),
        }

    def close(self) -> None:
        """关停时强 flush bloom，确保未持久化位图不会丢失。"""
        try:
            self.bloom.flush()
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"Bloom 关停 flush 失败: {exc}", exc_info=True)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data["payload"] = json.loads(data.pop("payload_json", "{}"))
        except Exception:
            data["payload"] = {}
        return data

"""按域名持久化"成功模式 / 偏好 impersonate / 上次挑战时间"。

dispatcher 用它在 mode=auto 时跳过试错直接走最优路径。
单文件 SQLite，独立于 frontier/crawler_data 库。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlparse


SUCCESS_TTL_SECONDS = 24 * 3600  # 24 小时内的成功记录直接复用
RESET_AFTER_FAILURES = 3
RESET_AFTER_DAYS = 7


class DomainMemory:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS domain_modes (
                    domain TEXT PRIMARY KEY,
                    preferred_mode TEXT NOT NULL DEFAULT '',
                    impersonate TEXT NOT NULL DEFAULT '',
                    last_success_at REAL NOT NULL DEFAULT 0,
                    last_failure_at REAL NOT NULL DEFAULT 0,
                    last_challenge_at REAL NOT NULL DEFAULT 0,
                    success_streak INTEGER NOT NULL DEFAULT 0,
                    fail_streak INTEGER NOT NULL DEFAULT 0,
                    total_success INTEGER NOT NULL DEFAULT 0,
                    total_failure INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                )
                """
            )

    @staticmethod
    def domain_of(url: str) -> str:
        return urlparse(url).netloc.lower()

    def lookup(self, domain: str) -> dict | None:
        """返回该域名的记忆；如果记录过旧或未命中则返回 None。"""
        if not domain:
            return None
        domain = domain.lower()
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM domain_modes WHERE domain = ?", (domain,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        if record["fail_streak"] >= RESET_AFTER_FAILURES:
            return None
        if record["last_success_at"] and now - record["last_success_at"] > RESET_AFTER_DAYS * 86400:
            return None
        if record["last_success_at"] == 0:
            return None
        record["fresh"] = (now - record["last_success_at"]) <= SUCCESS_TTL_SECONDS
        return record

    def record_success(self, domain: str, mode: str, impersonate: str = "") -> None:
        if not domain:
            return
        domain = domain.lower()
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO domain_modes (domain, preferred_mode, impersonate,
                    last_success_at, success_streak, fail_streak, total_success, updated_at)
                VALUES (?, ?, ?, ?, 1, 0, 1, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    preferred_mode = excluded.preferred_mode,
                    impersonate = CASE WHEN excluded.impersonate = '' THEN domain_modes.impersonate ELSE excluded.impersonate END,
                    last_success_at = excluded.last_success_at,
                    success_streak = domain_modes.success_streak + 1,
                    fail_streak = 0,
                    total_success = domain_modes.total_success + 1,
                    updated_at = excluded.updated_at
                """,
                (domain, mode, impersonate or "", now, now),
            )
            conn.commit()

    def record_failure(self, domain: str, mode: str, challenge: str = "") -> None:
        if not domain:
            return
        domain = domain.lower()
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO domain_modes (domain, preferred_mode, last_failure_at,
                    last_challenge_at, fail_streak, success_streak, total_failure, updated_at)
                VALUES (?, ?, ?, ?, 1, 0, 1, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    last_failure_at = excluded.last_failure_at,
                    last_challenge_at = CASE WHEN excluded.last_challenge_at > 0
                                              THEN excluded.last_challenge_at
                                              ELSE domain_modes.last_challenge_at END,
                    fail_streak = domain_modes.fail_streak + 1,
                    success_streak = 0,
                    total_failure = domain_modes.total_failure + 1,
                    updated_at = excluded.updated_at
                """,
                (domain, mode, now, now if challenge else 0, now),
            )
            conn.commit()

    def reset(self, domain: str) -> bool:
        domain = (domain or "").lower()
        if not domain:
            return False
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM domain_modes WHERE domain = ?", (domain,))
            conn.commit()
            return cursor.rowcount > 0

    def all_records(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM domain_modes ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM domain_modes").fetchone()[0]
            mode_rows = conn.execute(
                "SELECT preferred_mode, COUNT(*) AS n FROM domain_modes GROUP BY preferred_mode"
            ).fetchall()
        return {
            "db_path": str(self.db_path),
            "total_domains": total,
            "by_mode": {row[0] or "unknown": row[1] for row in mode_rows},
        }

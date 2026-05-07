"""Cache storage for crawler responses."""

import hashlib
import json
import threading
import time
from pathlib import Path


class CacheStore:
    def __init__(self, directory: Path, ttl_seconds: int, max_size_mb: int,
                 prune_every_writes: int, logger):
        self.directory = directory
        self.ttl_seconds = ttl_seconds
        self.max_size_mb = max_size_mb
        self.prune_every_writes = max(1, prune_every_writes)
        self.logger = logger
        self._prune_lock = threading.Lock()
        self._write_count = 0

    @staticmethod
    def variant(*parts) -> str:
        raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()

    @staticmethod
    def key(url: str, req_type: int = 1, variant: str = "") -> str:
        return hashlib.md5((url + str(req_type) + variant).encode()).hexdigest()

    def _path(self, url: str, req_type: int = 1, variant: str = "") -> Path:
        return self.directory / f"{self.key(url, req_type, variant)}.json"

    def read(self, url: str, req_type: int = 1, variant: str = "") -> str | None:
        cache_file = self._path(url, req_type, variant)
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            ts = data.get("timestamp", 0)
            if self.ttl_seconds > 0 and time.time() - ts > self.ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            cache_file.touch()
            return data.get("text")
        except FileNotFoundError:
            return None
        except Exception as exc:
            self.logger.debug(f"缓存读取失败: {exc}")
            return None

    def write_async(self, executor, url: str, text: str, req_type: int = 1,
                    variant: str = "") -> None:
        executor.submit(self.write, url, text, req_type, variant)

    def write(self, url: str, text: str, req_type: int = 1,
              variant: str = "") -> None:
        cache_file = self._path(url, req_type, variant)
        try:
            cache_file.write_text(
                json.dumps({
                    "url": url,
                    "text": text,
                    "type": req_type,
                    "variant": variant,
                    "timestamp": time.time(),
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            if self.should_prune():
                self.prune_if_needed()
        except Exception as exc:
            self.logger.debug(f"缓存写入失败: {exc}")

    def should_prune(self) -> bool:
        with self._prune_lock:
            self._write_count += 1
            return self._write_count % self.prune_every_writes == 0

    def prune_if_needed(self) -> None:
        if self.max_size_mb <= 0:
            return
        max_bytes = self.max_size_mb * 1024 * 1024
        try:
            with self._prune_lock:
                files = []
                total = 0
                for path in self.directory.glob("*.json"):
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        continue
                    total += stat.st_size
                    files.append((stat.st_mtime, stat.st_size, path))
                if total <= max_bytes:
                    return

                target_bytes = int(max_bytes * 0.9)
                for _mtime, size, path in sorted(files):
                    if total <= target_bytes:
                        break
                    try:
                        path.unlink(missing_ok=True)
                        total -= size
                    except Exception:
                        continue
        except Exception as exc:
            self.logger.debug(f"缓存淘汰失败: {exc}")


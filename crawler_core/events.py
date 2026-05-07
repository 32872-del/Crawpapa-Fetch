"""Structured JSONL event logging."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path


class EventLog:
    def __init__(self, path: Path, tail_lines: int, logger):
        self.path = path
        self.tail_lines = tail_lines
        self.logger = logger
        self._lock = threading.Lock()

    def append(self, event: dict) -> None:
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            **event,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(line + "\n")
        except Exception as exc:
            self.logger.debug(f"事件日志写入失败: {exc}")

    def read_recent(self, limit: int = 50, event_type: str = "",
                    domain: str = "") -> list[dict]:
        if not self.path.exists():
            return []
        limit = max(1, min(int(limit), 500))
        event_type = event_type.strip()
        domain = domain.lower().strip()
        lines = self.tail_file_lines(self.path, self.tail_lines, self.logger)
        result = []
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and event.get("event") != event_type:
                continue
            if domain and domain not in str(event.get("domain", "")).lower():
                continue
            result.append(event)
            if len(result) >= limit:
                break
        result.reverse()
        return result

    @staticmethod
    def tail_file_lines(path: Path, max_lines: int, logger=None) -> list[str]:
        max_lines = max(1, int(max_lines))
        chunk_size = 8192
        try:
            with path.open("rb") as file:
                file.seek(0, os.SEEK_END)
                pos = file.tell()
                buffer = b""
                lines: list[bytes] = []
                while pos > 0 and len(lines) <= max_lines:
                    read_size = min(chunk_size, pos)
                    pos -= read_size
                    file.seek(pos)
                    buffer = file.read(read_size) + buffer
                    lines = buffer.splitlines()
                if pos > 0 and lines:
                    lines = lines[1:]
                decoded = []
                for line in lines[-max_lines:]:
                    try:
                        decoded.append(line.decode("utf-8"))
                    except UnicodeDecodeError:
                        decoded.append(line.decode("utf-8", errors="replace"))
                return decoded
        except Exception as exc:
            if logger is not None:
                logger.debug(f"事件日志 tail 读取失败: {exc}")
            return []


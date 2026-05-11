"""Persistent target analysis memory.

This stores higher-level target understanding, separate from low-level
domain access memory. It is meant to retain reusable analysis conclusions such
as source choice, pagination strategy, field hints, and evidence snapshots.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


MEMORY_VERSION = "1.0"
FRESH_TTL_SECONDS = 30 * 24 * 3600


class TargetMemory:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS target_memory (
                    target_key TEXT PRIMARY KEY,
                    target_type TEXT NOT NULL DEFAULT '',
                    target_name TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    preferred_source TEXT NOT NULL DEFAULT '',
                    preferred_mode TEXT NOT NULL DEFAULT '',
                    menu_source_path TEXT NOT NULL DEFAULT '',
                    list_selector TEXT NOT NULL DEFAULT '',
                    pagination_type TEXT NOT NULL DEFAULT '',
                    detail_selector_text TEXT NOT NULL DEFAULT '',
                    field_hints_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    analysis_json TEXT NOT NULL DEFAULT '{}',
                    confidence REAL NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_success_at REAL NOT NULL DEFAULT 0,
                    last_failure_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                )
                """
            )

    @staticmethod
    def make_key(target_name: str, source_url: str = "", target_type: str = "") -> str:
        raw = "|".join([target_type.strip().lower(), target_name.strip().lower(), source_url.strip().lower()])
        return raw or source_url.strip().lower() or target_name.strip().lower()

    def lookup(self, target_key: str) -> dict[str, Any] | None:
        if not target_key:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM target_memory WHERE target_key = ?",
                (target_key,),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["field_hints"] = _loads(record.pop("field_hints_json", "{}"))
        record["evidence"] = _loads(record.pop("evidence_json", "{}"))
        record["analysis"] = _loads(record.pop("analysis_json", "{}"))
        record["fresh"] = (time.time() - record.get("last_success_at", 0)) <= FRESH_TTL_SECONDS
        return record

    def record_analysis(self, target_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not target_key:
            raise ValueError("target_key is required")
        now = time.time()
        analysis = dict(payload.get("analysis", {}))
        evidence = dict(payload.get("evidence", {}))
        field_hints = dict(payload.get("field_hints", {}))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO target_memory (
                    target_key, target_type, target_name, source_url, preferred_source,
                    preferred_mode, menu_source_path, list_selector, pagination_type,
                    detail_selector_text, field_hints_json, evidence_json, analysis_json,
                    confidence, success_count, failure_count, last_success_at,
                    last_failure_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    target_type = excluded.target_type,
                    target_name = excluded.target_name,
                    source_url = excluded.source_url,
                    preferred_source = excluded.preferred_source,
                    preferred_mode = excluded.preferred_mode,
                    menu_source_path = excluded.menu_source_path,
                    list_selector = excluded.list_selector,
                    pagination_type = excluded.pagination_type,
                    detail_selector_text = excluded.detail_selector_text,
                    field_hints_json = excluded.field_hints_json,
                    evidence_json = excluded.evidence_json,
                    analysis_json = excluded.analysis_json,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    target_key,
                    str(payload.get("target_type", "")),
                    str(payload.get("target_name", "")),
                    str(payload.get("source_url", "")),
                    str(payload.get("preferred_source", "")),
                    str(payload.get("preferred_mode", "")),
                    str(payload.get("menu_source_path", "")),
                    str(payload.get("list_selector", "")),
                    str(payload.get("pagination_type", "")),
                    str(payload.get("detail_selector_text", "")),
                    json.dumps(field_hints, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    json.dumps(analysis, ensure_ascii=False, sort_keys=True),
                    float(payload.get("confidence", 0) or 0),
                    now,
                ),
            )
            conn.commit()
        return self.lookup(target_key) or {"target_key": target_key}

    def record_success(self, target_key: str) -> None:
        if not target_key:
            return
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE target_memory
                SET success_count = success_count + 1,
                    last_success_at = ?,
                    updated_at = ?
                WHERE target_key = ?
                """,
                (now, now, target_key),
            )
            conn.commit()

    def record_failure(self, target_key: str) -> None:
        if not target_key:
            return
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE target_memory
                SET failure_count = failure_count + 1,
                    last_failure_at = ?,
                    updated_at = ?
                WHERE target_key = ?
                """,
                (now, now, target_key),
            )
            conn.commit()

    def list_records(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM target_memory ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            record = dict(row)
            record["field_hints"] = _loads(record.pop("field_hints_json", "{}"))
            record["evidence"] = _loads(record.pop("evidence_json", "{}"))
            record["analysis"] = _loads(record.pop("analysis_json", "{}"))
            result.append(record)
        return result

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM target_memory").fetchone()[0]
            by_type = conn.execute(
                "SELECT target_type, COUNT(*) AS n FROM target_memory GROUP BY target_type"
            ).fetchall()
        return {
            "db_path": str(self.db_path),
            "version": MEMORY_VERSION,
            "total_targets": total,
            "by_type": {row[0] or "unknown": row[1] for row in by_type},
        }

    def reset(self, target_key: str) -> bool:
        if not target_key:
            return False
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM target_memory WHERE target_key = ?", (target_key,))
            conn.commit()
            return cursor.rowcount > 0


def _loads(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}

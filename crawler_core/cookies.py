"""Small JSON cookie store used by crawler sessions."""

import hashlib
import json
import re
import threading
from pathlib import Path


SAFE_PROFILE_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,180}$")


class CookieStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _safe_key(profile: str) -> str:
        profile = (profile or "").strip().lower()
        if not profile:
            raise ValueError("cookie profile 不能为空")
        if SAFE_PROFILE_RE.match(profile):
            return profile.replace(":", "_")
        digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()[:16]
        return f"profile_{digest}"

    def path_for(self, profile: str) -> Path:
        return self.directory / f"{self._safe_key(profile)}.json"

    def load(self, profile: str) -> dict:
        path = self.path_for(profile)
        if not path.exists():
            return {}
        with self._lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies", {})
        return cookies if isinstance(cookies, dict) else {}

    def save(self, profile: str, cookies: dict) -> Path:
        if not isinstance(cookies, dict):
            raise ValueError("cookies 必须是 JSON 对象")
        path = self.path_for(profile)
        payload = {
            "profile": profile,
            "cookies": {str(key): str(value) for key, value in cookies.items()},
        }
        with self._lock:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def merge(self, profile: str, cookies: dict) -> dict:
        current = self.load(profile)
        current.update({str(key): str(value) for key, value in (cookies or {}).items()})
        self.save(profile, current)
        return current

    def clear(self, profile: str = "") -> int:
        paths = [self.path_for(profile)] if profile else list(self.directory.glob("*.json"))
        removed = 0
        with self._lock:
            for path in paths:
                if path.exists():
                    path.unlink()
                    removed += 1
        return removed

    def list_profiles(self) -> list[dict]:
        result = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            cookies = data.get("cookies", {})
            result.append({
                "profile": data.get("profile", path.stem),
                "cookies_count": len(cookies) if isinstance(cookies, dict) else 0,
                "path": str(path),
            })
        return result

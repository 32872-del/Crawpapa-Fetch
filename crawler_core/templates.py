"""Pipeline template storage and variable rendering."""

import json
import re
from pathlib import Path


SAFE_TEMPLATE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,120}$")


class TemplateStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(exist_ok=True)

    def _path(self, name: str) -> Path:
        if not SAFE_TEMPLATE_NAME_RE.match(name):
            raise ValueError("模板名只允许字母数字.-_，长度 1-120")
        return self.directory / f"{name}.json"

    def save(self, name: str, pipeline: dict, description: str = "") -> Path:
        if not isinstance(pipeline, dict):
            raise ValueError("pipeline 必须是 JSON 对象")
        payload = {
            "name": name,
            "description": description,
            "pipeline": pipeline,
        }
        path = self._path(name)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, name: str) -> dict:
        path = self._path(name)
        if not path.exists():
            raise FileNotFoundError(f"模板不存在: {name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        result = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            pipeline = data.get("pipeline") or {}
            result.append({
                "name": data.get("name", path.stem),
                "description": data.get("description", ""),
                "steps": len(pipeline.get("steps", [])) if isinstance(pipeline, dict) else 0,
                "path": str(path),
            })
        return result


def render_template(value, variables: dict):
    if isinstance(value, str):
        rendered = value
        for key, replacement in variables.items():
            rendered = rendered.replace("{{" + str(key) + "}}", str(replacement))
        return rendered
    if isinstance(value, list):
        return [render_template(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: render_template(item, variables) for key, item in value.items()}
    return value


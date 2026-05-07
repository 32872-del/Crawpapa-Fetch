"""Lightweight open-source hygiene audit for tracked files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "possible AWS access key"),
    (re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"\n]{12,}['\"]"), "possible hard-coded secret"),
    (re.compile(r"(?i)authorization\s*[:=]\s*['\"]?bearer\s+[A-Za-z0-9._\-]{16,}"), "possible bearer token"),
    (re.compile(r"(?i)github_pat_[A-Za-z0-9_]{20,}"), "possible GitHub token"),
    (re.compile(r"(?i)sk-[A-Za-z0-9]{20,}"), "possible API token"),
]

LOCAL_PATH_PATTERNS = [
    (re.compile(r"[A-Z]:\\Users\\[^\\\s]+", re.I), "local user path"),
    (re.compile(r"F:\\datawork", re.I), "local datawork path"),
    (re.compile(r"E:\\", re.I), "local drive path"),
]

TRACKED_RUNTIME_PREFIXES = (
    "cache/",
    "cookies/",
    "databases/",
    "frontier/",
    "jobs/",
    "logs/",
    "output/",
    ".crawler-data/",
)

ALLOWED_RUNTIME_FILES = {
    "cache/.gitkeep",
    "cookies/.gitkeep",
    "databases/.gitkeep",
    "frontier/.gitkeep",
    "jobs/.gitkeep",
    "logs/.gitkeep",
    "output/.gitkeep",
}

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".whl",
    ".db",
    ".sqlite",
    ".pyc",
}


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def audit() -> list[str]:
    findings: list[str] = []
    tracked = git_lines("ls-files")
    for rel in tracked:
        normalized = rel.replace("\\", "/")
        if normalized.startswith(TRACKED_RUNTIME_PREFIXES) and normalized not in ALLOWED_RUNTIME_FILES:
            findings.append(f"{rel}: tracked runtime/generated artifact")
        path = ROOT / rel
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                continue
        for pattern, label in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{rel}:{line}: {label}")
        if normalized != "tools/maintenance/secret_audit.py":
            for pattern, label in LOCAL_PATH_PATTERNS:
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append(f"{rel}:{line}: {label}")
    return findings


def main() -> int:
    findings = audit()
    if findings:
        print("Secret audit failed:")
        for item in findings:
            print(f"  - {item}")
        return 1
    print("Secret audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build Crawpapa-Fetch distribution artifacts."""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"

INCLUDE_PATHS = [
    ".env.example",
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "RELEASE_NOTES_v4.0.md",
    "RELEASE_NOTES_v5.0.md",
    "RELEASE_NOTES_v5.1.md",
    "RELEASE_NOTES_v5.2.md",
    "RELEASE_NOTES_v5.3.md",
    "pyproject.toml",
    "uv.lock",
    "main.py",
    "unified_crawler_server.py",
    "setup_mcp_clients.py",
    "start.bat",
    "start.sh",
    "install.bat",
    "install_portable.bat",
    "install.sh",
    "pack.sh",
    "proxy_pool.json",
    "agents",
    "config",
    "crawler_core",
    "docs",
    "schemas",
    "templates",
    "tests",
    "tools",
    "utils",
    "workspace",
]

RUNTIME_DIRS = [
    "cache",
    "cookies",
    "databases",
    "frontier",
    "jobs",
    "logs",
    "output",
]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def build_python_dist() -> None:
    probe = subprocess.run(
        [sys.executable, "-m", "build", "--version"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "Python package build requires the 'build' package. "
            "Install it with 'python -m pip install build', or run with --skip-python-dist."
        )
    run([sys.executable, "-m", "build"])


def add_path(zf: zipfile.ZipFile, src: Path, arc_prefix: str) -> None:
    if src.is_dir():
        for item in src.rglob("*"):
            if item.is_dir():
                continue
            if any(part in {".git", ".venv", "__pycache__", ".pytest_cache"} for part in item.parts):
                continue
            zf.write(item, Path(arc_prefix) / item.relative_to(ROOT))
    elif src.exists():
        zf.write(src, Path(arc_prefix) / src.relative_to(ROOT))


def build_portable_zip(name: str) -> Path:
    DIST.mkdir(exist_ok=True)
    zip_path = DIST / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in INCLUDE_PATHS:
            add_path(zf, ROOT / rel, name)
        for rel in RUNTIME_DIRS:
            keep = ROOT / rel / ".gitkeep"
            if keep.exists():
                zf.write(keep, Path(name) / rel / ".gitkeep")
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Crawpapa-Fetch packages")
    parser.add_argument("--skip-python-dist", action="store_true", help="Skip wheel/sdist build")
    parser.add_argument("--skip-zip", action="store_true", help="Skip portable zip build")
    parser.add_argument("--zip-name", default="Crawpapa-Fetch", help="Portable zip folder/file base name")
    args = parser.parse_args()

    DIST.mkdir(exist_ok=True)
    if not args.skip_python_dist:
        try:
            build_python_dist()
        except RuntimeError as exc:
            print(f"[WARN] {exc}")
            print("[WARN] Continuing with portable zip build.")
            if args.skip_zip:
                return 1
    if not args.skip_zip:
        path = build_portable_zip(args.zip_name)
        print(f"Portable zip: {path}")
    print("Build complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

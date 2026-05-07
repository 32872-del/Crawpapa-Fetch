# Packaging

Crawpapa-Fetch supports two packaging modes:

- Python package artifacts: wheel and source distribution.
- Portable zip: source tree with install scripts and runtime directory placeholders.

## Build Everything

Linux/macOS:

```bash
./pack.sh
```

Windows:

```powershell
.\pack.bat
```

This runs:

1. Secret audit.
2. Python package build.
3. Portable zip build.

Artifacts are written to `dist/`.

If the current Python environment does not include the `build` package, the portable zip is still created. Install `build` when you also need wheel/sdist artifacts:

```powershell
python -m pip install build
python -m build
```

## Python Build Directly

Linux/macOS:

```bash
.venv/bin/python tools/maintenance/build_package.py --skip-zip
```

Windows:

```powershell
.\.venv\Scripts\python.exe tools\maintenance\build_package.py --skip-zip
```

## Portable Zip Only

Linux/macOS:

```bash
.venv/bin/python tools/maintenance/build_package.py --skip-python-dist
```

Windows:

```powershell
.\.venv\Scripts\python.exe tools\maintenance\build_package.py --skip-python-dist
```

## Pre-Release Checklist

Linux/macOS:

```bash
.venv/bin/python tools/maintenance/secret_audit.py
.venv/bin/python -m pytest -q
./pack.sh
```

Windows:

```powershell
.\.venv\Scripts\python.exe tools\maintenance\secret_audit.py
.\.venv\Scripts\python.exe -m pytest -q
.\pack.bat
```

Do not publish runtime outputs, cookies, databases, cache files, or private `.env` values.

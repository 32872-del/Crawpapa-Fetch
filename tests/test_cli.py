import subprocess
import sys


def test_cli_help_runs():
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "analyze" in result.stdout
    assert "diagnose" in result.stdout


def test_cli_analyze_help_mentions_report_format():
    result = subprocess.run(
        [sys.executable, "main.py", "analyze", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--report-format" in result.stdout

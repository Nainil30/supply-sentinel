"""The stdlib-only hand calculation in scripts/ must agree with the engine."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_worked_examples.py"


def test_hand_calculation_matches_engine():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL MATCH" in result.stdout
    assert "FAIL" not in result.stdout

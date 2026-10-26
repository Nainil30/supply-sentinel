#!/usr/bin/env python3
"""PostToolUse hook: format and lint-fix the Python file that was just edited.

Fails open: if uv or ruff is not available yet (before Phase 0 finishes), it does nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path")
    if not raw_path or not raw_path.endswith(".py"):
        return

    path = Path(raw_path)
    if not path.exists() or shutil.which("uv") is None:
        return

    for args in (["uv", "run", "ruff", "format", str(path)], ["uv", "run", "ruff", "check", "--fix", "--quiet", str(path)]):
        try:
            subprocess.run(args, check=False, capture_output=True, timeout=60)
        except Exception:
            return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

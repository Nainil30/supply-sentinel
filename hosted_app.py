"""Entry point for Streamlit Community Cloud: replay-only console on a freshly built snapshot."""

import os
import runpy
from pathlib import Path

import supply_sentinel.app as app_pkg
from supply_sentinel.datagen.generator import build_base

DB = Path("data/snapshots/base.duckdb")
if not DB.exists():
    build_base(seed=42, out_dir=DB.parent, state_db=Path("data/state/decisions.sqlite"))

os.environ.update(
    SENTINEL_DB=str(DB),
    SENTINEL_STATE_DIR="data/state",
    SENTINEL_UI_PROFILE="replay",
    SENTINEL_UI_CASSETTE="",
    SENTINEL_HOSTED="1",
)
runpy.run_path(str(Path(app_pkg.__file__).parent / "streamlit_app.py"), run_name="__main__")

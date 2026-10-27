"""The server's view of one snapshot: read-only DuckDB, engine inputs and the state DB.

One `Snapshot` lives for the whole server process. Tools never take a snapshot argument.
"""

import os
from datetime import date
from functools import cached_property
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from supply_sentinel.engine import loader
from supply_sentinel.engine.config import EngineConfig, load_config
from supply_sentinel.engine.models import ExceptionRecord, PartInputs

DEFAULT_DB = Path("data/snapshots/base.duckdb")
DEFAULT_STATE_DIR = Path("data/state")


class ToolInputError(ToolError, ResourceError, ValueError):
    """Bad tool or resource input. The model sees the message, so it ends with a fix hint."""


class Snapshot:
    def __init__(
        self,
        db_path: Path,
        state_dir: Path = DEFAULT_STATE_DIR,
        config_dir: Path = Path("config"),
    ) -> None:
        if not db_path.exists():
            raise FileNotFoundError(
                f"Snapshot {db_path} not found. Run `uv run sentinel datagen --seed 42` "
                "or set SENTINEL_DB."
            )
        self.db_path = db_path
        self.state_dir = state_dir
        self.config_dir = config_dir
        self.con = loader.open_snapshot(db_path)
        self.cfg: EngineConfig = load_config(config_dir)
        row = self.con.execute("SELECT snapshot_id, as_of_date FROM meta").fetchone()
        assert row is not None
        self.snapshot_id: str = row[0]
        self.as_of: date = row[1]

    @classmethod
    def from_env(cls) -> "Snapshot":
        return cls(
            Path(os.environ.get("SENTINEL_DB", str(DEFAULT_DB))),
            Path(os.environ.get("SENTINEL_STATE_DIR", str(DEFAULT_STATE_DIR))),
        )

    @property
    def state_db(self) -> Path:
        return self.state_dir / "decisions.sqlite"

    @cached_property
    def parts(self) -> dict[str, PartInputs]:
        return {p.part_id: p for p in loader.load_part_inputs(self.con.cursor())}

    @cached_property
    def exceptions(self) -> list[ExceptionRecord]:
        from supply_sentinel.engine.exceptions import detect_exceptions

        return detect_exceptions(list(self.parts.values()), self.cfg)

    def part(self, part_id: str) -> PartInputs:
        try:
            return self.parts[part_id]
        except KeyError:
            raise ToolInputError(
                f"Unknown part_id '{part_id}'. Call list_exceptions to see part ids, "
                "or read a platform BOM resource."
            ) from None

    def query(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        return self.con.cursor().execute(sql, params or []).fetchall()

    def base(self) -> dict[str, Any]:
        """The two fields every tool response carries."""
        return {"as_of_date": self.as_of, "snapshot_id": self.snapshot_id}

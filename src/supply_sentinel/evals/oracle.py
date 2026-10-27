"""Resolver tokens and the oracle (spec 05 section 1).

Tokens appear in scenario YAML as strings:
  {first_open_po:<part>}     first open PO of the part by need week, in the BASE snapshot
  {open_po:<part>:<n>}       the n-th (0-based) open PO of the part by need week
  {oracle:<key>}             a number the engine computes on the scenario snapshot
The oracle never calls an LLM. It calls the same engine functions the MCP tools call.
"""

import re
from collections.abc import Callable
from typing import Any

from supply_sentinel.engine.config import EngineConfig
from supply_sentinel.engine.eo import compute_eo_exposure
from supply_sentinel.engine.ltb import compute_ltb_quantity
from supply_sentinel.engine.models import PartInputs
from supply_sentinel.engine.netting import commit_gaps, net_position, round_to_order_multiple

TOKEN = re.compile(r"\{(first_open_po|open_po|oracle):([^{}]+)\}")
Scalar = float | int | str | None


def _open_pos(parts: dict[str, PartInputs], part_id: str) -> list[str]:
    if part_id not in parts:
        raise KeyError(f"token names unknown part {part_id!r}")
    pos = sorted(parts[part_id].pos, key=lambda p: (p.need_week_idx, p.po_id))
    return [p.po_id for p in pos]


def oracle_values(inp: PartInputs, cfg: EngineConfig) -> dict[str, Callable[[], Scalar]]:
    """Lazy oracle keys for one part (some need an LTB or E&O sizing, so compute on demand)."""

    def slipped() -> str | None:
        late = sorted(
            (p for p in inp.pos if p.promise_week_idx > p.need_week_idx),
            key=lambda p: (p.need_week_idx, p.po_id),
        )
        return late[0].po_id if late else None

    def gaps() -> dict[int, int]:
        return commit_gaps(inp, cfg.commit_gap_weeks)

    def bridge_qty() -> Scalar:
        b = compute_ltb_quantity(inp, cfg).bridge
        return None if b is None else b.sizing.order_qty_units

    def ltb_date() -> Scalar:
        d = inp.eol_notice.ltb_date if inp.eol_notice else None
        return None if d is None else d.isoformat()

    def net_field(name: str) -> Scalar:
        value = getattr(net_position(inp, cfg.horizon_weeks), name)
        return value if value is None else float(value) if name.endswith("units") else int(value)

    def rounded_shortfall() -> Scalar:
        short = net_position(inp, cfg.horizon_weeks).max_shortfall_units
        return round_to_order_multiple(short, inp.order_multiple, inp.moq)

    def ltb_field(name: str) -> Scalar:
        return float(getattr(compute_ltb_quantity(inp, cfg).sizing, name))

    def eo_field(name: str) -> Scalar:
        return float(getattr(compute_eo_exposure(inp, cfg), name))

    return {
        "shortage.max_shortfall_units": lambda: net_field("max_shortfall_units"),
        "shortage.first_shortage_week": lambda: net_field("first_shortage_week"),
        "shortage.recovery_week": lambda: net_field("recovery_week"),
        "shortage.rounded_shortfall_units": rounded_shortfall,
        "shortage.slipped_po_id": slipped,
        "commit.gap_units": lambda: sum(gaps().values()),
        "commit.first_gap_week": lambda: min(gaps()) if gaps() else None,
        "ltb.order_qty": lambda: ltb_field("order_qty_units"),
        "ltb.spend_usd": lambda: ltb_field("spend_usd"),
        "ltb.net_units": lambda: ltb_field("net_units"),
        "ltb.bridge.order_qty": bridge_qty,
        "ltb.ltb_date": ltb_date,
        "eo.excess_units": lambda: eo_field("excess_units"),
        "eo.excess_usd": lambda: eo_field("excess_usd"),
        "eo.cancel_qty": lambda: eo_field("cancel_qty_recommended"),
        "eo.cancellable_qty": lambda: eo_field("cancellable_po_qty"),
        "eo.obsolete_units": lambda: eo_field("obsolete_units"),
        "eo.obsolete_usd": lambda: eo_field("obsolete_usd"),
    }


def oracle_get(inp: PartInputs, cfg: EngineConfig, key: str) -> Scalar:
    table = oracle_values(inp, cfg)
    if key not in table:
        raise KeyError(f"unknown oracle key {key!r}; known: {sorted(table)}")
    return table[key]()


def _fmt(value: Scalar) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def resolve(
    node: Any,
    parts: dict[str, PartInputs],
    *,
    oracle_part: PartInputs | None = None,
    cfg: EngineConfig | None = None,
) -> Any:
    """Replace tokens anywhere in a nested structure. A string that is exactly one token keeps
    the native type (so a number stays a number); otherwise the value is spliced in as text."""
    if isinstance(node, list):
        return [resolve(x, parts, oracle_part=oracle_part, cfg=cfg) for x in node]
    if isinstance(node, dict):
        return {k: resolve(v, parts, oracle_part=oracle_part, cfg=cfg) for k, v in node.items()}
    if not isinstance(node, str):
        return node

    def one(kind: str, arg: str) -> Scalar:
        if kind == "first_open_po":
            return _open_pos(parts, arg.strip())[0]
        if kind == "open_po":
            part, _, n = arg.rpartition(":")
            return _open_pos(parts, part.strip())[int(n)]
        if oracle_part is None or cfg is None:
            raise ValueError(f"oracle token {{oracle:{arg}}} needs a built scenario snapshot")
        return oracle_get(oracle_part, cfg, arg.strip())

    whole = TOKEN.fullmatch(node)
    if whole:
        return one(whole.group(1), whole.group(2))
    return TOKEN.sub(lambda m: _fmt(one(m.group(1), m.group(2))), node)

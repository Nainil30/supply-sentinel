"""quantity (hard when specified): within tolerance of the oracle, an exact value or a range."""

from supply_sentinel.evals.outcome import Score
from supply_sentinel.evals.scorers.base import ScoreContext


def expected_quantity(sc: ScoreContext) -> tuple[float, float] | None:
    """(low, high) acceptable quantity, or None when the scenario sets no quantity rule."""
    rule = sc.scenario.expected.quantity
    if rule is None:
        return None
    if rule.range is not None:
        return rule.range
    if rule.oracle is not None:
        raw = sc.oracle_value(rule.oracle)
        if not isinstance(raw, int | float):
            raise ValueError(f"oracle {rule.oracle} is {raw!r}, not a number")
        centre = float(raw)
    elif rule.exact is not None:
        centre = rule.exact
    else:
        raise ValueError("quantity rule needs oracle, exact or range")
    slack = abs(centre) * rule.tolerance_pct / 100.0
    return centre - slack - 1e-6, centre + slack + 1e-6


def score(sc: ScoreContext) -> Score:
    bounds = expected_quantity(sc)
    if bounds is None:
        return Score(name="quantity", passed=None, hard=True, detail="no quantity rule")
    rec = sc.rec
    if rec is None:
        return Score(name="quantity", passed=False, hard=True, detail="no recommendation")
    exp = sc.scenario.expected
    allowed = set(exp.primary_action.any_of if exp.primary_action else [])
    if exp.secondary_action:
        allowed |= set(exp.secondary_action.any_of)
    actions = [a for a in (rec.primary_action, rec.secondary_action) if a is not None]
    quantities = [
        a.quantity for a in actions if a.quantity is not None and a.action_type.value in allowed
    ]
    lo, hi = bounds
    if any(lo <= q <= hi for q in quantities):
        return Score(
            name="quantity", passed=True, hard=True, detail=f"{quantities} in [{lo:g}, {hi:g}]"
        )
    return Score(
        name="quantity",
        passed=False,
        hard=True,
        detail=f"got {quantities or 'no quantity'}, wanted [{lo:g}, {hi:g}]",
    )

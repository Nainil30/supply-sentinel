"""efficiency (reported only): calls, tokens, latency, estimated cost."""

from supply_sentinel.evals.outcome import Score
from supply_sentinel.evals.scorers.base import ScoreContext


def score(sc: ScoreContext) -> Score:
    u = sc.outcome.usage
    detail = (
        f"{u.llm_calls} llm, {u.tool_calls} tool, {u.input_tokens}/{u.output_tokens} tokens, "
        f"{u.seconds:.1f}s, ${u.cost_usd:.4f}"
    )
    return Score(name="efficiency", passed=None, hard=False, detail=detail)

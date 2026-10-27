"""Context budget (spec 04 section 9): keep every LLM call at or under 7,000 input tokens.

No tokenizer dependency: tokens are estimated as characters / 4, which is close enough to size
a budget. The ledger records the provider's real count next to our estimate.
"""

import json
from typing import Any

from supply_sentinel.llm.types import Msg

MAX_INPUT_TOKENS = 7000
HEADROOM_TOKENS = 500
TOOL_RESULTS_TOKENS = 4000
PER_TOOL_RESULT_TOKENS = 800
SUMMARY_TOKENS = 120


def count_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def messages_tokens(messages: list[Msg], tools: list[dict[str, object]] | None = None) -> int:
    total = sum(count_tokens(m.content) + 4 for m in messages)
    for m in messages:
        for c in m.tool_calls:
            total += count_tokens(json.dumps(c.args)) + count_tokens(c.name)
    if tools:
        total += count_tokens(json.dumps(tools, separators=(",", ":")))
    return total


def _shrink(obj: Any, n: int) -> Any:
    """Keep the first n items of every list and say how many were dropped."""
    if isinstance(obj, dict):
        return {k: _shrink(v, n) for k, v in obj.items()}
    if isinstance(obj, list):
        kept = [_shrink(x, n) for x in obj[:n]]
        if len(obj) > n:
            kept.append(f"...{len(obj) - n} more")
        return kept
    return obj


def compact_result(obj: Any, max_tokens: int = PER_TOOL_RESULT_TOKENS) -> str:
    """Compact JSON for the model. Long lists are cut from the end until it fits."""
    text = json.dumps(obj, separators=(",", ":"), default=str)
    for n in (26, 12, 8, 6, 4, 3, 2, 1, 0):
        if count_tokens(text) <= max_tokens:
            return text
        text = json.dumps(_shrink(obj, n), separators=(",", ":"), default=str)
    return text[: max_tokens * 4]


def summarize_result(name: str, text: str) -> str:
    """Replace an old tool result by its scalar fields (ids and numbers already cited)."""
    try:
        data = json.loads(text)
    except ValueError:
        return f"{name}: {text[: SUMMARY_TOKENS * 4]}"
    keep: dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str | int | float | bool) or v is None:
                keep[k] = v
            elif isinstance(v, list):
                keep[f"{k}_count"] = len(v)
    out = json.dumps(keep, separators=(",", ":"), default=str)
    return f"[summary of {name}] " + out[: SUMMARY_TOKENS * 4]


def fit(messages: list[Msg], tools: list[dict[str, object]]) -> int:
    """Summarize the oldest full tool results until the call fits. Returns the final estimate.

    Mutates `messages` in place. Tool results already summarized are skipped.
    """
    limit = MAX_INPUT_TOKENS - HEADROOM_TOKENS
    for m in messages:
        if messages_tokens(messages, tools) <= limit and _tool_tokens(messages) <= (
            TOOL_RESULTS_TOKENS
        ):
            break
        if m.role == "tool" and not m.content.startswith("[summary of"):
            m.content = summarize_result(m.name or "tool", m.content)
    return messages_tokens(messages, tools)


def _tool_tokens(messages: list[Msg]) -> int:
    return sum(count_tokens(m.content) for m in messages if m.role == "tool")

"""Validation of specialist output (spec 04 section 6). Pure functions, no I/O.

Checks: grounding (evidence exists in the tool output; every number in the text and actions is
backed by evidence), policy (allowed actions, quantities, PO ids) and sanity (quantity limits).
Schema validation is done by Pydantic when the draft is parsed.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from supply_sentinel.agent.models import ExceptionCard, RecommendationDraft
from supply_sentinel.agent.policy import Policy
from supply_sentinel.engine.models import ActionItem, ActionType, Agreement, Grounding

FLOAT_TOLERANCE = 0.005  # +-0.5% for floats and currency
SANITY_FACTOR = 1.25

NEEDS_QUANTITY = {
    ActionType.PLACE_PO,
    ActionType.EXPEDITE,
    ActionType.PULL_IN_PO,
    ActionType.LTB_BUY,
    ActionType.BRIDGE_BUY,
}
NEEDS_PO_IDS = {ActionType.PULL_IN_PO, ActionType.PUSH_OUT_PO, ActionType.CANCEL_PO}

ID_RE = re.compile(r"\b(?:P|PO|ORB|SUP|EXC|REC)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
PO_RE = re.compile(r"\bPO-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUM_RE = re.compile(r"(?<![\d.,A-Za-z-])\$?(\d[\d,]*(?:\.\d+)?)([kKmM])?(?![A-Za-z\d])")


@dataclass
class Issue:
    check: str  # grounding | policy | sanity | schema
    message: str


@dataclass
class ValidationResult:
    grounding: Grounding
    issues: list[Issue] = field(default_factory=list)

    def of(self, check: str) -> list[Issue]:
        return [i for i in self.issues if i.check == check]

    @property
    def ok(self) -> bool:
        return not self.issues

    def describe(self) -> str:
        return "\n".join(f"- [{i.check}] {i.message}" for i in self.issues)


# ----------------------------------------------------------------- evidence paths


_TOKEN = re.compile(r"[^.\[\]]+|\[\d+\]")


def resolve_path(obj: Any, path: str) -> Any:
    """`weeks[3].pab` -> obj["weeks"][3]["pab"]. Raises KeyError with the failing step."""
    path = path.strip()
    if path.startswith("$."):
        path = path[2:]
    cur = obj
    for tok in _TOKEN.findall(path):
        try:
            cur = cur[int(tok[1:-1])] if tok.startswith("[") else cur[tok]
        except (KeyError, IndexError, TypeError):
            raise KeyError(tok) from None
    return cur


def _as_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return None


def numbers_match(expected: Any, actual: Any) -> bool:
    a, b = _as_number(expected), _as_number(actual)
    if a is None or b is None:
        return str(expected).strip().lower() == str(actual).strip().lower()
    if a == int(a) and b == int(b):
        return a == b
    return abs(a - b) <= FLOAT_TOLERANCE * max(abs(a), abs(b), 1e-9)


# ----------------------------------------------------------------- free-text numbers


def extract_numbers(text: str) -> list[float]:
    """Numbers in prose, after removing ids and ISO dates. Handles $, commas, k and M."""
    text = DATE_RE.sub(" ", ID_RE.sub(" ", text))
    text = re.sub(r"\bwk\s?(\d+)", r"week \1", text, flags=re.IGNORECASE)
    out: list[float] = []
    for raw, suffix in NUM_RE.findall(text):
        value = float(raw.replace(",", ""))
        if suffix.lower() == "k":
            value *= 1_000
        elif suffix.lower() == "m":
            value *= 1_000_000
        out.append(value)
    return out


def _action_numbers(a: ActionItem | None) -> list[float]:
    if a is None:
        return []
    nums: list[float] = []
    if a.quantity is not None:
        nums.append(float(a.quantity))
    if a.target_week_idx is not None:
        nums.append(float(a.target_week_idx))
    nums.extend(float(v) for v in (a.platform_allocations or {}).values())
    return nums


def _draft_texts(d: RecommendationDraft) -> list[str]:
    texts = [d.root_cause_explanation, d.draft_message, d.deviation_reason or "", *d.risks]
    texts.extend(alt.why_not for alt in d.alternatives_considered)
    return texts


# ----------------------------------------------------------------- checks


def check_grounding(
    draft: RecommendationDraft, tool_log: dict[str, Any]
) -> tuple[Grounding, list[Issue]]:
    bad: list[str] = []
    cited: list[float] = []
    for ref in draft.evidence:
        if ref.tool_call_id not in tool_log:
            bad.append(f"evidence {ref.tool_call_id}:{ref.field_path} - no such tool call")
            continue
        try:
            actual = resolve_path(tool_log[ref.tool_call_id], ref.field_path)
        except KeyError as exc:
            bad.append(f"evidence {ref.tool_call_id}:{ref.field_path} - path not found at {exc}")
            continue
        if not numbers_match(ref.value, actual):
            bad.append(
                f"evidence {ref.tool_call_id}:{ref.field_path} says {ref.value} "
                f"but the tool returned {actual}"
            )
            continue
        n = _as_number(ref.value)
        if n is not None:
            cited.append(n)
    checked = len(draft.evidence)

    for label, action in (
        ("primary_action", draft.primary_action),
        ("secondary_action", draft.secondary_action),
    ):
        for n in _action_numbers(action):
            checked += 1
            if not any(numbers_match(n, c) for c in cited):
                bad.append(f"{label} number {n:g} has no evidence reference")

    allowed = (
        cited + _action_numbers(draft.primary_action) + _action_numbers(draft.secondary_action)
    )
    for text in _draft_texts(draft):
        for n in extract_numbers(text):
            checked += 1
            if not any(numbers_match(n, a) for a in allowed):
                bad.append(f"number {n:g} in text has no evidence reference")

    grounding = Grounding(
        status="FAILED" if bad else "PASSED", checked=checked, ungrounded_values=bad
    )
    return grounding, [Issue("grounding", b) for b in bad]


def known_po_ids(tool_log: dict[str, Any]) -> set[str]:
    return set(PO_RE.findall(json.dumps(tool_log, default=str)))


def not_cancellable(tool_log: dict[str, Any]) -> set[str]:
    """PO ids that any get_open_pos result marks cancellable == false."""
    found: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if o.get("cancellable") is False and isinstance(o.get("po_id"), str):
                found.add(o["po_id"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(tool_log)
    return found


def check_policy(
    draft: RecommendationDraft, card: ExceptionCard, tool_log: dict[str, Any], policy: Policy
) -> list[Issue]:
    issues: list[Issue] = []
    exc_type = card.exception.type.value
    pos = known_po_ids(tool_log)
    for label, action in (("primary", draft.primary_action), ("secondary", draft.secondary_action)):
        if action is None:
            continue
        name = action.action_type.value
        if not policy.is_allowed(exc_type, action.action_type):
            allowed = policy.allowed_actions.get(exc_type, [])
            issues.append(
                Issue(
                    "policy",
                    f"{label} action {name} is not allowed for {exc_type}; use one of {allowed}",
                )
            )
        if action.action_type in NEEDS_QUANTITY and not (action.quantity and action.quantity > 0):
            issues.append(Issue("policy", f"{label} action {name} needs a quantity greater than 0"))
        if action.action_type in NEEDS_PO_IDS and not action.po_ids:
            issues.append(Issue("policy", f"{label} action {name} needs po_ids"))
        if action.action_type == ActionType.CANCEL_PO:
            for po in action.po_ids or []:
                if po in not_cancellable(tool_log):
                    issues.append(Issue("policy", f"PO {po} is not cancellable (get_open_pos)"))
        for po in action.po_ids or []:
            if po not in pos:
                issues.append(
                    Issue(
                        "policy",
                        f"PO id {po} does not appear in any tool result; known: {sorted(pos)}",
                    )
                )
    return issues


def _walk_values(obj: Any, key: str) -> list[float]:
    found: list[float] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, int | float):
                found.append(float(v))
            else:
                found.extend(_walk_values(v, key))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_walk_values(v, key))
    return found


def _ltb_quantities(tool_log: dict[str, Any], kind: ActionType) -> list[float]:
    """LTB_BUY must match result.sizing; BRIDGE_BUY must match result.bridge.sizing."""
    found: list[float] = []
    for out in tool_log.values():
        res = out.get("result") if isinstance(out, dict) else None
        if not isinstance(res, dict):
            continue
        part = (
            res.get("sizing")
            if kind == ActionType.LTB_BUY
            else (res.get("bridge") or {}).get("sizing")
        )
        found.extend(_walk_values(part, "order_qty_units"))
    return found or _walk_values(tool_log, "order_qty_units")


def check_sanity(
    draft: RecommendationDraft, card: ExceptionCard, tool_log: dict[str, Any]
) -> list[Issue]:
    issues: list[Issue] = []
    exc = card.exception
    reference = exc.max_shortfall_units or float(exc.gap_units or 0)
    explained = bool((draft.deviation_reason or "").strip())
    if draft.baseline_agreement == Agreement.DEVIATE:
        if not explained:
            issues.append(
                Issue("schema", "baseline_agreement is DEVIATE but deviation_reason is empty")
            )
        if not draft.alternatives_considered:
            issues.append(
                Issue("schema", "DEVIATE needs at least one alternatives_considered entry")
            )
    for action in (draft.primary_action, draft.secondary_action):
        if action is None or action.quantity is None:
            continue
        if action.action_type in (ActionType.PULL_IN_PO, ActionType.EXPEDITE) and reference > 0:
            limit = reference * SANITY_FACTOR
            if action.quantity > limit and not explained:
                issues.append(
                    Issue(
                        "sanity",
                        f"{action.action_type.value} quantity {action.quantity:g} exceeds 1.25 x the shortfall ({limit:g}); lower it or explain in deviation_reason",
                    )
                )
        if action.action_type in (ActionType.LTB_BUY, ActionType.BRIDGE_BUY):
            allowed = _ltb_quantities(tool_log, action.action_type)
            if not any(numbers_match(action.quantity, a) for a in allowed):
                issues.append(
                    Issue(
                        "sanity",
                        f"{action.action_type.value} quantity {action.quantity:g} must equal an order_qty_units returned by compute_ltb_quantity ({allowed})",
                    )
                )
    return issues


def validate_draft(
    draft: RecommendationDraft, card: ExceptionCard, tool_log: dict[str, Any], policy: Policy
) -> ValidationResult:
    grounding, g_issues = check_grounding(draft, tool_log)
    issues = [
        *check_policy(draft, card, tool_log, policy),
        *check_sanity(draft, card, tool_log),
        *g_issues,
    ]
    return ValidationResult(grounding=grounding, issues=issues)

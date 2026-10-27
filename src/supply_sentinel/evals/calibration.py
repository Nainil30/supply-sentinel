"""Judge calibration (spec 05 section 5): agreement with reference labels, Cohen's kappa.

Labels live in a YAML file with `labeler: human | ai-reference` and 15 items, each with the same
five yes/no criteria the judge uses. The report says plainly which kind of label it used.
"""

from pathlib import Path
from typing import Any

import yaml

from supply_sentinel.evals.judge import CRITERIA, PASS_AT, judge_message
from supply_sentinel.llm.router import Router

TARGET_KAPPA = 0.6


def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    """Cohen's kappa for two binary raters. 1.0 when both always agree, even on one class."""
    if len(a) != len(b) or not a:
        raise ValueError("kappa needs two equal, non-empty lists")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected >= 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


def load_labels(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data


def labelled_items(labels: dict[str, Any]) -> list[dict[str, Any]]:
    """Items whose five criteria are all filled in with true/false."""
    out = []
    for item in labels.get("items", []):
        marks = item.get("labels") or {}
        if all(isinstance(marks.get(c), bool) for c in CRITERIA):
            out.append(item)
    return out


def calibrate(labels: dict[str, Any], router: Router) -> dict[str, Any]:
    items = labelled_items(labels)
    if not items:
        raise ValueError("no fully labelled items; fill in `labels:` for each item first")
    judged: list[dict[str, bool]] = [
        judge_message(router, it["message"], it.get("facts", {}), it["stakeholder"]) for it in items
    ]
    per_criterion = {
        c: sum(j[c] == it["labels"][c] for j, it in zip(judged, items, strict=True)) / len(items)
        for c in CRITERIA
    }
    human_pass = [sum(it["labels"][c] for c in CRITERIA) >= PASS_AT for it in items]
    judge_pass = [sum(j.values()) >= PASS_AT for j in judged]
    kappa = cohen_kappa(human_pass, judge_pass)
    return {
        "labeler": labels.get("labeler", "unknown"),
        "items": len(items),
        "per_criterion_agreement": per_criterion,
        "pass_agreement": sum(h == j for h, j in zip(human_pass, judge_pass, strict=True))
        / len(items),
        "kappa": kappa,
        "meets_target": kappa >= TARGET_KAPPA,
    }


def render_report(result: dict[str, Any], judge_model: str) -> str:
    provisional = result["labeler"] != "human"
    lines = ["# Judge calibration", ""]
    if provisional:
        lines += [
            "> **Provisional - AI reference labels, owner relabel pending.** "
            "These numbers say how well the judge matches a second model working from the "
            "rubric alone, not how well it matches a planner.",
            "",
        ]
    lines += [
        f"Judge model: `{judge_model}`. Labels: `{result['labeler']}`, {result['items']} messages.",
        "",
        "| Criterion | Agreement |",
        "|---|---|",
    ]
    lines += [f"| {c} | {v:.0%} |" for c, v in result["per_criterion_agreement"].items()]
    verdict = "meets" if result["meets_target"] else "is below"
    lines += [
        "",
        f"Pass/fail agreement: {result['pass_agreement']:.0%}. "
        f"Cohen's kappa: **{result['kappa']:.2f}** ({verdict} the {TARGET_KAPPA} target).",
        "",
    ]
    return "\n".join(lines)

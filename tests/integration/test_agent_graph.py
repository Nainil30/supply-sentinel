"""Graph tests with a scripted model on the base snapshot: pause, approve, resume, fallback."""

import json
import sqlite3

import pytest

from supply_sentinel.agent import runner
from supply_sentinel.agent.models import Decision
from supply_sentinel.datagen import generator
from supply_sentinel.llm.types import LLMResponse, Msg, Role
from tests.fixtures.scripted import ScriptedClient, text, tool_call

SHORTAGE_DRAFT = {
    "primary_action": {
        "action_type": "PULL_IN_PO",
        "quantity": 1049,
        "po_ids": ["PO-10551"],
        "target_week_idx": 2,
    },
    "root_cause": "SUPPLIER_SLIP",
    "root_cause_explanation": "PO-10551 slipped, leaving the part 1049 units short from week 2.",
    "baseline_agreement": "AGREE",
    "evidence": [
        {"tool_call_id": "tc1", "field_path": "max_shortfall_units", "value": 1049},
        {"tool_call_id": "tc1", "field_path": "first_shortage_week", "value": 2},
    ],
    "risks": [],
    "confidence": "HIGH",
    "stakeholder": "GSM",
    "draft_message": "Please pull in PO-10551 to week 2: the part is 1049 units short from week 2.",
}


def respond(role: Role, messages: list[Msg], tools: list[dict[str, object]]) -> LLMResponse:
    card = messages[1].content if len(messages) > 1 else ""
    if role == "writer":
        return text("Two recommendations were produced and need review.")
    if "EXC-SHT-P-PHY-100G" in card:
        if not any(m.role == "tool" for m in messages):
            return tool_call("get_part_position", part_id="P-PHY-100G")
        if tools:
            return text("ready")  # idle turn; the loop then forces the final answer
        return text(json.dumps(SHORTAGE_DRAFT))
    return text("I cannot help with this.")  # the commit-gap exception: unusable output


@pytest.fixture(scope="module")
def base_db(tmp_path_factory):
    d = tmp_path_factory.mktemp("agent")
    db = generator.build_base(seed=42, out_dir=d, state_db=d / "decisions.sqlite")
    return db, d


def test_pause_approve_resume(base_db):
    db, d = base_db
    client = ScriptedClient(respond)
    run_id, pending = runner.start(db, "replay", d, client=client, max_exceptions=12)

    by_part = {p["part_id"]: p for p in pending}
    good, fallback = by_part["P-PHY-100G"], by_part["P-HSINK-L"]
    assert good["source"] == "AGENT" and good["grounding"] == "PASSED"
    assert good["tier"] == 1  # PULL_IN_PO is a tier-1 action
    assert fallback["source"] == "RULES_FALLBACK" and "RULES_FALLBACK" in fallback["flags"]

    runner.save_decision(d, run_id, Decision(rec_id=good["rec_id"], decision="APPROVED"))
    runner.save_decision(
        d, run_id, Decision(rec_id=fallback["rec_id"], decision="REJECTED", reason="no")
    )
    final = runner.resume(d, run_id, client=client)

    assert {o["status"] for o in final["outcomes"]} == {"APPROVED", "REJECTED"}
    brief = (d / "runs" / run_id / "weekly_brief.md").read_text(encoding="utf-8")
    assert "Weekly supply brief" in brief and "P-HSINK-S" in brief  # deferred excess exception
    rows = (
        sqlite3.connect(d / "decisions.sqlite")
        .execute("SELECT status FROM decision_log WHERE run_id = ?", (run_id,))
        .fetchall()
    )
    assert sorted(r[0] for r in rows) == ["APPROVED", "REJECTED"]


def test_ledger_has_token_counts_under_budget(base_db):
    db, d = base_db
    client = ScriptedClient(respond)
    run_id, _ = runner.start(db, "replay", d, client=client)
    events = [
        json.loads(x) for x in (d / "runs" / run_id / "ledger.jsonl").read_text().splitlines()
    ]
    calls = [e for e in events if e["event"] == "llm_call"]
    assert calls and all(e["input_tokens"] > 0 and e["input_tokens"] <= 7000 for e in calls)

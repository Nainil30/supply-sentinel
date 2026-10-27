"""Phase 5 controls on a scripted model: LTB path, extractor, fallback, run budget, reconcile."""

import json
import re
import shutil

import pytest

from supply_sentinel.agent import runner
from supply_sentinel.agent.models import ExceptionCard
from supply_sentinel.agent.nodes import reconcile
from supply_sentinel.datagen import generator, scenarios
from supply_sentinel.engine.models import ActionItem, Recommendation
from supply_sentinel.llm.types import LLMResponse, Msg, ProviderError, Role
from tests.fixtures.scripted import ScriptedClient, text, tool_call

NOTICE = "End of life. Last-time-buy deadline is 2026-11-27, final shipment 2027-02-26."
TABLE_LTB = "2026-11-20"  # the table disagrees with the text on purpose


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("phase5")
    db = generator.build_base(seed=42, out_dir=d, state_db=d / "decisions.sqlite")
    eol, _ = scenarios.build_scenario(
        "eol1",
        [
            {
                "op": "add_eol_notice", "part_id": "P-PHY-100G", "notice_date": "2026-10-20",
                "ltb_date": TABLE_LTB, "last_ship_date": "2027-02-26",
                "replacement_part_id": None, "raw_text": NOTICE,
            }
        ],
        snapshot_dir=d,
        state_db=d / "decisions.sqlite",
    )  # fmt: skip
    return db, eol, d


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setenv("SENTINEL_TODAY", "2026-11-02")


def tool_results(messages: list[Msg]) -> dict[str, dict]:
    out = {}
    for m in messages:
        if m.role == "tool":
            m_id = re.match(r"\[(tc\d+)\] ", m.content)
            if m_id:
                out[m_id.group(1)] = m.content
    return out


def ltb_responder(role: Role, messages: list[Msg], tools) -> LLMResponse:
    if role == "extractor":
        return text(
            '{"ltb_date": "2026-11-27", "last_ship_date": "2027-02-26", "replacement_part_id": null}'
        )
    if role == "writer":
        return text("One last-time buy needs review.")
    card = messages[1].content
    if "EXC-EOL-P-PHY-100G" not in card:
        return text("cannot help")  # other exceptions fall back
    if not any(m.role == "tool" for m in messages):
        return tool_call("compute_ltb_quantity", part_id="P-PHY-100G")
    if tools:
        return text("ready")
    draft = {
        "primary_action": {"action_type": "LTB_BUY", "quantity": 145400},
        "root_cause": "EOL",
        "root_cause_explanation": "The supplier ends production; the engine sized the last buy.",
        "baseline_agreement": "AGREE",
        "evidence": [
            {"tool_call_id": "tc1", "field_path": "result.sizing.order_qty_units", "value": 145400}
        ],
        "confidence": "MEDIUM",
        "stakeholder": "FINANCE",
        "draft_message": "Please approve a last-time buy of 145,400 units.",
    }
    return text(json.dumps(draft))


def test_ltb_specialist_and_extractor_mismatch(env):
    _, eol, d = env
    run_id, pending = runner.start(eol, "replay", d, client=ScriptedClient(ltb_responder))
    by_action = {p["action"]: p for p in pending}
    ltb = by_action["LTB_BUY"]
    assert ltb["source"] == "AGENT" and ltb["grounding"] == "PASSED"
    assert ltb["tier"] == 2 and ltb["quantity"] == 145400
    # The extractor read 2026-11-27 in the text but the table says 2026-11-20: tier-0 data issue.
    ledger = (d / "runs" / run_id / "ledger.jsonl").read_text()
    assert '"event": "extractor", "mismatches": 1' in ledger
    final = runner.resume(d, run_id, client=ScriptedClient(ltb_responder))
    recs = [Recommendation(**r) for r in final["recommendations"]]
    issue = next(r for r in recs if r.primary_action.action_type.value == "DATA_ISSUE_FLAG")
    assert issue.autonomy_tier == 0 and "2026-11-27" in issue.root_cause_explanation
    assert TABLE_LTB in issue.root_cause_explanation


def test_provider_failure_gives_rules_fallback_with_visible_flag(env):
    db, _, d = env

    def broken(role, messages, tools):
        if role == "writer":
            return text("Nothing was investigated by an agent.")
        raise ProviderError("provider down")

    client = ScriptedClient(broken)
    _, pending = runner.start(db, "replay", d, client=client, max_exceptions=3)
    assert pending and all(p["source"] == "RULES_FALLBACK" for p in pending)
    assert all("RULES_FALLBACK" in p["flags"] for p in pending)
    kinds = {p["part_id"] for p in pending}
    assert {"P-PHY-100G", "P-HSINK-L", "P-HSINK-S"} <= kinds  # all three exception types


def test_run_budget_breach_falls_back(env, tmp_path):
    db, _, d = env
    cfg = tmp_path / "config"
    shutil.copytree("config", cfg)
    s = (cfg / "settings.yaml").read_text()
    (cfg / "settings.yaml").write_text(s.replace("max_llm_calls: 80", "max_llm_calls: 0"))

    def never_called(role, messages, tools):
        raise AssertionError("the run budget should stop LLM calls first")

    client = ScriptedClient(never_called)
    _, pending = runner.start(db, "replay", d, client=client, config_dir=cfg, max_exceptions=2)
    assert pending and all(p["source"] == "RULES_FALLBACK" for p in pending)
    assert client.calls == []


def _cards_and_recs(h):
    from supply_sentinel.agent.nodes import intake

    st = intake.detect_exceptions(h.ctx, {})
    cards = {c["exception"]["exception_id"]: c for c in st["cards"]}
    shortage = cards["EXC-SHT-P-PHY-100G"]
    forced = json.loads(json.dumps(cards["EXC-EXS-P-HSINK-S"]))
    forced["exception"]["part_id"] = "P-PHY-100G"
    forced["exception"]["exception_id"] = "EXC-EXS-P-PHY-100G"
    forced["baseline"]["part_id"] = "P-PHY-100G"
    forced["baseline"]["exception_id"] = "EXC-EXS-P-PHY-100G"
    cards["EXC-EXS-P-PHY-100G"] = forced

    def rec(rec_id, card, action):
        b = ExceptionCard(**card).baseline
        return b.model_copy(
            update={"rec_id": rec_id, "primary_action": ActionItem(**action), "autonomy_tier": 1}
        ).model_dump(mode="json")

    r1 = rec("REC-RUNX-1", shortage, {"action_type": "PULL_IN_PO", "quantity": 1049, "po_ids": ["PO-10551"], "target_week_idx": 2})  # fmt: skip
    r2 = rec("REC-RUNX-2", forced, {"action_type": "PUSH_OUT_PO", "po_ids": ["PO-10555"]})
    r2["exception_id"] = "EXC-EXS-P-PHY-100G"
    return {"cards": list(cards.values()), "recommendations": [r1, r2]}


def reconciler_responder(good: bool):
    def respond(role, messages, tools):
        if role == "writer":
            return text("ok")
        if not good:
            return text("I will not answer in JSON.")
        if not any(m.role == "tool" for m in messages):
            return tool_call("get_open_pos", part_id="P-PHY-100G")
        if tools:
            return text("ready")
        draft = {
            "primary_action": {
                "action_type": "PULL_IN_PO",
                "quantity": 1049,
                "po_ids": ["PO-10551"],
                "target_week_idx": 2,
            },  # fmt: skip
            "secondary_action": {"action_type": "PUSH_OUT_PO", "po_ids": ["PO-10555"]},
            "root_cause": "SUPPLIER_SLIP",
            "root_cause_explanation": "Cover the shortage first; the later PO is not needed yet.",
            "baseline_agreement": "DEVIATE",
            "deviation_reason": "Both actions can be done: pull in PO-10551 and push out PO-10555.",
            "alternatives_considered": [
                {"action_type": "CANCEL_PO", "why_not": "the supply is needed later"}
            ],  # fmt: skip
            "evidence": [
                {"tool_call_id": "r1", "field_path": "primary_action.quantity", "value": 1049},
                {"tool_call_id": "r1", "field_path": "primary_action.target_week_idx", "value": 2},
            ],  # fmt: skip
            "confidence": "MEDIUM",
            "stakeholder": "GSM",
            "draft_message": "Pull in PO-10551 by 1,049 units and push out PO-10555.",
        }
        return text(json.dumps(draft))

    return respond


def test_forced_conflict_goes_through_reconcile(env):
    db, _, d = env
    h = runner.open_run("RUNX", db, "replay", d, client=ScriptedClient(reconciler_responder(True)))
    try:
        state = _cards_and_recs(h)
        out = reconcile.reconcile(h.ctx, state)
    finally:
        h.close()
    recs = [Recommendation(**r) for r in out["recommendations"]]
    assert len(recs) == 1
    r = recs[0]
    assert r.source.value == "RECONCILED" and r.autonomy_tier >= 2
    assert any(f.kind == "CONFLICT" for f in r.security_flags)
    assert r.grounding.status == "PASSED", r.grounding


def test_unresolved_conflict_keeps_both_flagged(env):
    db, _, d = env
    h = runner.open_run("RUNX", db, "replay", d, client=ScriptedClient(reconciler_responder(False)))
    try:
        out = reconcile.reconcile(h.ctx, _cards_and_recs(h))
    finally:
        h.close()
    recs = [Recommendation(**r) for r in out["recommendations"]]
    assert len(recs) == 2
    for r in recs:
        assert r.autonomy_tier >= 2
        assert any(f.kind == "CONFLICT" for f in r.security_flags)
    assert out["errors"] and out["errors"][0]["node"] == "reconcile"


def test_no_conflict_means_no_llm_call(env):
    db, _, d = env
    client = ScriptedClient(lambda r, m, t: text("should not be called"))
    h = runner.open_run("RUNX", db, "replay", d, client=client)
    try:
        state = _cards_and_recs(h)
        state["recommendations"] = state["recommendations"][:1]
        assert reconcile.reconcile(h.ctx, state) == {}
    finally:
        h.close()
    assert client.calls == []

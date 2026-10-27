# Demo script (5 minutes)

Setup: `docker compose up` (or `uv run sentinel datagen --seed 42 && uv run sentinel ui`), open http://localhost:8501. Replay profile, no keys.

| Min | Click | Say |
|---|---|---|
| 0:00 | **Overview** | "Orbis Networks is fictional; this is a weekly supply sweep over synthetic data. The engine computes, the agent judges." |
| 0:45 | **Exception queue** | "Deterministic detection, no LLM: shortages, commit gaps, EOL, E&O. Sorted by severity." |
| 1:30 | Open an exception in **Detail** | "Netting by week, the root cause from counterfactual tests, every number from an engine call." |
| 2:15 | **What-if** | "Pull a PO in by two weeks and see the position change. Still the engine, not the model." |
| 2:45 | **Run** (replay) | "The orchestrator routes each exception to a specialist. Replay uses recorded model responses so this works offline." |
| 3:30 | **Approvals** | "The agent can only record a recommendation. I approve, edit the quantity, or reject with a reason. Tier 2 needs a reason." |
| 4:15 | **Evals** | "40 scenarios, rules baseline vs agent, with failures listed. I state the weak spots plainly." |
| 4:45 | **Decisions** | "Decision history feeds the next run: the same exception is escalated, not expedited twice." |

Close: "Evals are the source of truth; the README numbers all link to results files."

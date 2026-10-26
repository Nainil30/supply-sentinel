# Project Brief — Supply Sentinel

## One line

An agentic supply-execution copilot that sweeps a hardware supply plan every week, finds what is about to break, investigates it with tools, recommends the fix with exact quantities, drafts the stakeholder message, and waits for a planner to approve — with an eval suite that proves how often it is right.

## The problem (written from the planner's seat)

A supply execution planner for a data center networking portfolio owns several switch platforms at once, each in a different lifecycle phase: one ramping (NPI), several in sustain, one with an end-of-life announcement, one in service-only tail. Every week the planner has to:

- Net demand against inventory, open purchase orders and supplier commits for dozens of parts, many shared across platforms.
- Spot shortages early enough to expedite, pull in, or allocate.
- React to supplier end-of-life notices with a last-time-buy (LTB) quantity that balances running out against stranded inventory.
- Catch excess and obsolete (E&O) exposure while purchase orders can still be cancelled or pushed out.
- Write clear asks to the global supply manager (GSM), the global commodity manager (GCM), product management (PM), and finance.

Most of the time goes into gathering and reconciling, not deciding. The math is well understood. The judgment — root cause, trade-offs, who to tell and how — is where a good planner earns their pay.

## The solution

| Layer | What it does | Why it matters |
|---|---|---|
| Planning engine | Deterministic netting, exception detection, counterfactual root-cause tests, LTB newsvendor sizing, E&O exposure, what-if simulation, and a rules baseline | Numbers are exact, testable and auditable |
| MCP server | Exposes the engine and data as tools, resources and prompts | One server works in the agent, Claude Code, VS Code Copilot, Claude Desktop |
| Agent | Orchestrator routes each exception to a specialist (Shortage, LTB, E&O); specialists follow Agent Skills playbooks, call tools, and propose a structured recommendation; a validator checks grounding, schema and policy; a human approves | Judgment and communication on top of exact math, with controls |
| Evals | 40 labeled scenarios, deterministic scorers, grounding checker, calibrated LLM judge, pass^k consistency, rules-vs-agent leaderboard, CI gates | Proves value and catches regressions |
| Harness | Autonomy tiers, least-privilege tools per skill, injection defense, budgets, checkpointing, OpenTelemetry tracing, run ledger | Production thinking, not a demo script |

## Why an agent, and not just rules

The rules baseline is built first and stays in the product. The agent earns its place only where the evals show it beats the rules:

| Situation | Rules baseline | Agent |
|---|---|---|
| Simple supplier slip | Correct | Correct, plus a clear message |
| EOL notice with a qualified replacement part | Recommends a full LTB | Recommends qualifying the alternate plus a bridge buy |
| Shortage on a part shared by three platforms | Expedites for one platform | Checks all platforms, proposes allocation |
| Same exception already expedited last week | Expedites again | Reads decision history, escalates to the supplier instead |
| Near-term shortage and long-term excess on the same part | Expedites and cancels at the same time | Pulls in the late PO: one action fixes both |
| Supplier note containing an injected instruction | Ignores text (no flag) | Treats as data, flags it |

## Users and stakeholders (fictional)

| Persona | Uses Supply Sentinel to |
|---|---|
| Supply execution planner (primary) | Review the weekly queue, approve or edit recommendations |
| GSM / GCM | Receive drafted escalations with the numbers attached |
| Product manager | Receive allocation and EOL impact notes |
| Finance | Receive E&O exposure and reserve inputs |

## Scope

In scope: the five blocks above, a Streamlit planner console, Docker Compose, a devcontainer, CI with live evals on the GitHub Models free tier, offline replay mode, and a hosted demo in replay mode.

Out of scope (v1): real ERP/planning-system connectors, sending real emails, optimization solvers, demand forecasting models (the forecast is an input), multi-tenant auth, vector search. Listed as stretch goals in `BUILD_PLAN.md`: MCP Apps UI, an A2A "supplier agent", a GitHub Agentic Workflow for eval triage, an NPI transition specialist, durable execution on Temporal.

## Success criteria (v1.0)

| Criterion | Target |
|---|---|
| One-command start | `docker compose up` or `uv run sentinel ui` works from a fresh clone in replay mode, no keys |
| Engine correctness | All worked examples pass; property tests for conservation (supply − demand = ending balance) |
| Eval: safety scenarios | 100% pass (hard CI gate) |
| Eval: grounding rate | ≥ 95% of numeric claims traceable to a tool result (hard CI gate) |
| Eval: action accuracy | Agent beats rules baseline overall; reported per category with honest losses |
| Eval: consistency | pass^3 reported for the smoke suite |
| Cost | $0 on free paths; tokens and latency reported per run |
| Explainability | Owner can whiteboard the architecture and defend each ADR in under 5 minutes |

## Ground rules for the owner

- Build on a personal machine, on personal time, with personal accounts.
- Use no employer data, documents, templates, terminology, part numbers or naming patterns. Everything is generic industry practice.
- Check your employer's outside-activities and IP policy before publishing.
- Every README claim about performance must come from a committed eval result file.

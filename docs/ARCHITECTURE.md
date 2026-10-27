# Architecture

Supply Sentinel is three systems that share one rule: the engine computes, the agent reasons, a human approves.

## The three harnesses

| Harness | Controls | Where |
|---|---|---|
| Runtime | Tool allowlists per skill, run and specialist budgets, 7k-token context budget, validation with one repair, autonomy tiers, approval gate, SQLite checkpoints, ledger and traces | `src/supply_sentinel/agent/`, `llm/`, `observability/`, `config/` |
| Eval | 40 labeled scenarios, oracle quantities, scorers, independent grounding re-check, LLM judge, CI gates | `src/supply_sentinel/evals/`, `evals/` |
| Development | AGENTS.md, CLAUDE.md, hooks, subagents, skills, protected paths, CI | `.claude/`, `.github/`, root files |

## Data flow

```mermaid
flowchart LR
    DG[datagen<br/>seeded DuckDB snapshot] --> EN[engine<br/>pure functions]
    EN --> MCP[MCP server<br/>14 tools, 5 resources]
    MCP --> AG[agent<br/>LangGraph]
    LLM[LLM provider<br/>live or replay cassette] --> AG
    AG --> DL[(decision_log<br/>SQLite)]
    AG --> LG[runs/ledger.jsonl<br/>runs/spans.jsonl]
    EN --> SV[app/services.py]
    DL --> SV
    LG --> SV
    SV --> UI[Planner Console<br/>Streamlit]
    UI -- staged decisions --> AG
    LG --> EV[evals<br/>scorers read the ledger]
```

## The agent graph

```mermaid
flowchart TD
    A[load_snapshot] --> B[detect_exceptions]
    B --> C[ingest_documents<br/>injection scan + EOL extractor]
    C --> D[prioritize]
    D -->|Send, one per exception| S1[shortage_specialist]
    D -->|Send| S2[ltb_specialist]
    D -->|Send| S3[eo_specialist]
    D -->|empty queue| V
    S1 --> V[validate<br/>grounding, policy, sanity, tier]
    S2 --> V
    S3 --> V
    V --> R[reconcile<br/>one rec per part]
    R --> G{{approval_gate<br/>interrupt}}
    G -->|Command resume| F[finalize<br/>record + apply decisions]
    F --> W[weekly_brief]
```

Specialists run in parallel up to `max_parallel_specialists`. State holds plain data only, so a run that paused in one process resumes in another.

## One exception, detection to approval

```mermaid
sequenceDiagram
    actor P as Planner
    participant UI as Console / CLI
    participant R as runner
    participant G as LangGraph
    participant S as Specialist
    participant M as MCP bridge (in-memory)
    participant E as Engine
    participant L as LLM (router)
    participant D as decision_log

    P->>UI: Start run
    UI->>R: start(snapshot, profile)
    R->>G: invoke (root span invoke_agent)
    G->>E: detect exceptions + rules baseline
    G->>G: scan supplier text, prioritize
    G->>S: Send(exception card)
    loop up to 8 tool calls
        S->>L: chat (span, ledger llm_call)
        L-->>S: tool call
        S->>M: call(tool) allowlisted
        M->>E: compute
        E-->>S: result (ledger tool_call, span execute_tool)
    end
    S->>L: final JSON draft
    S-->>G: Candidate
    G->>G: validate (grounding, policy, sanity), tier, reconcile
    G-->>R: interrupt(pending)
    R-->>UI: waiting at the approval gate
    P->>UI: Approve / edit / reject (reason if tier 2)
    UI->>R: save_decision (staged)
    P->>UI: Apply decisions
    UI->>R: resume
    R->>G: Command(resume=decisions)
    G->>D: record_recommendation, then status (ledger approval)
    G-->>R: weekly brief, run_end
```

## Observability

- `runs/<run_id>/ledger.jsonl` is the system of record: `run_start`, `llm_call`, `tool_call` (full output, latency), `validation`, `approval_gate`, `decision`, `approval`, `run_end`. The grounding scorer reads it.
- `runs/<run_id>/spans.jsonl` holds OpenTelemetry spans: `invoke_agent`, `execute_tool`, `chat` (GenAI conventions) and one span per LangGraph node (OpenInference).
- `otel.exporter: phoenix` also sends the spans to Phoenix: `docker run -p 6006:6006 arizephoenix/phoenix`, then `SENTINEL_OTEL_EXPORTER=phoenix sentinel run`.
- `SENTINEL_CAPTURE_CONTENT=0` keeps prompts and tool payloads off spans. See ADR-0013.

## Planner Console

`sentinel ui` starts Streamlit. Pages: Overview, Exception queue, Exception detail, Approvals, What-if, Decisions log, Evals, Run. Pages call `app/services.py` only; it uses the engine tools, the runner and the decision store. Untrusted supplier text is always rendered with `st.text` inside a labeled box.

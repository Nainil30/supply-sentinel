# ADR-0013: Tracing and console design

Status: accepted (Phase 7)

## Decisions
- **One provider per process, routed by trace id.** `observability/tracing.py` builds its own `TracerProvider` (never the global one) and a file exporter that maps each run's root trace id to `runs/<run_id>/spans.jsonl`. Spans outside a registered run are dropped. A resume is a second root span, so it appends to the same file with a new trace id.
- **Three hand-written GenAI spans, OpenInference for the graph.** `invoke_agent` (run, specialist), `execute_tool` and `chat` follow the GenAI conventions and carry the numbers the ledger carries. OpenInference's LangChain instrumentation adds one span per LangGraph node. The replay client is not a LangChain model, so `chat` spans come from our router; with a live LangChain model OpenInference adds its own LLM span underneath.
- **Ledger stays the system of record.** The ledger now stores full tool output and latency; spans are for debugging and Phoenix. Evals read the ledger only.
- **No tracing context into MCP.** The agent calls the server through an in-memory client in the same process (ADR-0007), so the span context is already shared. Nothing to propagate until the server runs out of process.
- **Phoenix is optional.** `otel.exporter: phoenix` adds an OTLP/HTTP exporter (`SENTINEL_PHOENIX_ENDPOINT`, default `http://localhost:6006/v1/traces`) next to the file exporter. Tested against a fake collector; not tested against a real Phoenix container (no Docker daemon on the build machine).
- **Content capture** is on (data is synthetic). `otel.capture_content: false` or `SENTINEL_CAPTURE_CONTENT=0` keeps prompts and tool payloads off spans.
- **Console shape.** Pages are plain scripts under `app/pages/` registered with `st.navigation`; all data access is `app/services.py`. A page never opens a database. The selected exception travels in `st.session_state`.
- **Decisions are staged, then applied.** Approve, edit and reject write the same `decisions.json` the CLI writes; "Apply decisions" resumes the LangGraph thread. An edited quantity is re-checked with the validator's sanity rules (errors block, warnings need a reason).
- **Dependencies.** `streamlit`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `openinference-instrumentation-langchain` (named by the phase prompt; the stack table lists Streamlit and OpenTelemetry).

## Consequences
Evals that call `open_run` directly have no root span, so they write no `spans.jsonl` (cheaper, ledger unchanged).

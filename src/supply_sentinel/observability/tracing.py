"""OpenTelemetry tracing (spec 06 section 2).

Three span kinds follow the GenAI semantic conventions: `invoke_agent` (a run, a specialist),
`execute_tool` (one MCP tool call) and `chat` (one model call). The OpenInference LangChain
instrumentation adds the LangGraph node spans underneath.

The exporter is chosen by `otel.exporter` in config/settings.yaml (or SENTINEL_OTEL_EXPORTER):
  file     one `runs/<run_id>/spans.jsonl` per run (default, no services needed)
  phoenix  OTLP over HTTP to a local Arize Phoenix (SENTINEL_PHOENIX_ENDPOINT), plus the file
  none     tracing is off; every helper here becomes a no-op

Prompt and tool payloads are attached to spans unless `otel.capture_content: false` in settings
or SENTINEL_CAPTURE_CONTENT=0. The data is synthetic, so capture is on by default.
"""

import json
import os
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, Status, StatusCode, Tracer

PHOENIX_ENDPOINT = "http://localhost:6006/v1/traces"
PROJECT_NAME = "supply-sentinel"

# GenAI semantic-convention attribute names (verified against opentelemetry-semantic-conventions).
OPERATION = "gen_ai.operation.name"
AGENT_NAME = "gen_ai.agent.name"
PROVIDER = "gen_ai.provider.name"
REQUEST_MODEL = "gen_ai.request.model"
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call.id"
TOOL_ARGS = "gen_ai.tool.call.arguments"
TOOL_RESULT = "gen_ai.tool.call.result"
INPUT_TOKENS = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
INPUT_MESSAGES = "gen_ai.input.messages"
OUTPUT_MESSAGES = "gen_ai.output.messages"


class RoutingFileExporter(SpanExporter):
    """Appends each finished span to the spans.jsonl of the run its trace belongs to."""

    def __init__(self) -> None:
        self._paths: dict[int, Path] = {}
        self._lock = threading.Lock()

    def register(self, trace_id: int, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._paths[trace_id] = path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            for s in spans:
                path = self._paths.get(s.context.trace_id)
                if path is None:  # a span outside any registered run: not ours to keep
                    continue
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(span_to_dict(s), default=str) + "\n")
        return SpanExportResult.SUCCESS


def span_to_dict(s: ReadableSpan) -> dict[str, Any]:
    ctx = s.context
    return {
        "name": s.name,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
        "start_ns": s.start_time,
        "end_ns": s.end_time,
        "status": s.status.status_code.name,
        "attributes": dict(s.attributes or {}),
    }


class _State:
    provider: TracerProvider | None = None
    exporter: RoutingFileExporter | None = None
    kind: str = "none"
    instrumented: bool = False
    capture: bool = True


_S = _State()
_NOOP = trace.NoOpTracer()


def exporter_kind(settings: dict[str, Any]) -> str:
    kind = os.environ.get("SENTINEL_OTEL_EXPORTER") or str(
        (settings.get("otel") or {}).get("exporter", "file")
    )
    if kind not in ("file", "phoenix", "none"):
        raise ValueError(f"otel.exporter must be file, phoenix or none, not '{kind}'.")
    return kind


def capture_content(settings: dict[str, Any] | None = None) -> bool:
    env = os.environ.get("SENTINEL_CAPTURE_CONTENT")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off")
    return bool(((settings or {}).get("otel") or {}).get("capture_content", True))


def capturing() -> bool:
    """Whether prompts and tool payloads go onto spans for this process."""
    return _S.capture and capture_content({"otel": {"capture_content": True}})


def init_tracing(settings: dict[str, Any]) -> bool:
    """Set up the process-wide provider once. Returns True when tracing is on."""
    kind = exporter_kind(settings)
    if kind == _S.kind and (_S.provider is not None or kind == "none"):
        return _S.provider is not None
    shutdown_tracing()
    _S.kind = kind
    _S.capture = capture_content(settings)
    if kind == "none":
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": PROJECT_NAME,
                                                        "openinference.project.name": PROJECT_NAME}))  # fmt: skip
    exporter = RoutingFileExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    if kind == "phoenix":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = os.environ.get("SENTINEL_PHOENIX_ENDPOINT", PHOENIX_ENDPOINT)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    _S.provider, _S.exporter = provider, exporter
    _instrument_langchain(provider)
    return True


def _instrument_langchain(provider: TracerProvider) -> None:
    from openinference.instrumentation.langchain import LangChainInstrumentor

    inst = LangChainInstrumentor()
    if inst.is_instrumented_by_opentelemetry:
        inst.uninstrument()
    inst.instrument(tracer_provider=provider)
    _S.instrumented = True


def shutdown_tracing() -> None:
    """Flush and drop the provider. Tests use this to start clean."""
    if _S.instrumented:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().uninstrument()
        _S.instrumented = False
    if _S.provider is not None:
        _S.provider.shutdown()
    _S.provider, _S.exporter, _S.kind = None, None, "none"


def flush() -> None:
    """Push buffered spans out (the Phoenix exporter batches)."""
    if _S.provider is not None:
        _S.provider.force_flush()


def tracer() -> Tracer:
    return _S.provider.get_tracer("supply_sentinel") if _S.provider else _NOOP


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    """A child span of whatever span is current. Exceptions mark it as an error and propagate."""
    with tracer().start_as_current_span(
        name, attributes={k: v for k, v in attrs.items() if v is not None}
    ) as sp:
        try:
            yield sp
        except BaseException as exc:
            sp.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise


@contextmanager
def run_span(run_id: str, run_dir: Path, name: str = "run") -> Iterator[Span]:
    """Root `invoke_agent` span of a run (or of a resume). Routes the trace into the run folder."""
    with span(f"invoke_agent {PROJECT_NAME}", **{OPERATION: "invoke_agent", AGENT_NAME: name,
                                                  "sentinel.run_id": run_id}) as sp:  # fmt: skip
        if _S.exporter is not None and sp.get_span_context().is_valid:
            _S.exporter.register(sp.get_span_context().trace_id, run_dir / "spans.jsonl")
        yield sp


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def as_text(value: Any, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def read_spans(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]

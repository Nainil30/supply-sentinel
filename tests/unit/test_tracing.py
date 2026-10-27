"""Tracing: spans land in the run folder, content capture can be turned off, OTLP reaches a collector."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from supply_sentinel.observability import tracing


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("SENTINEL_OTEL_EXPORTER", raising=False)
    monkeypatch.delenv("SENTINEL_CAPTURE_CONTENT", raising=False)
    tracing.shutdown_tracing()
    yield
    tracing.shutdown_tracing()


def test_spans_go_to_the_run_folder_with_genai_attributes(tmp_path):
    assert tracing.init_tracing({"otel": {"exporter": "file"}})
    with tracing.run_span("RUN1", tmp_path / "RUN1", "run"):
        with tracing.span("execute_tool get_open_pos", **{tracing.OPERATION: "execute_tool"}):
            pass
        with tracing.span("chat m", **{tracing.OPERATION: "chat", tracing.INPUT_TOKENS: 12}):
            pass
    spans = tracing.read_spans(tmp_path / "RUN1" / "spans.jsonl")
    by_name = {s["name"]: s for s in spans}
    assert set(by_name) == {"invoke_agent supply-sentinel", "execute_tool get_open_pos", "chat m"}
    root = by_name["invoke_agent supply-sentinel"]
    assert root["parent_id"] is None
    assert by_name["chat m"]["parent_id"] == root["span_id"]
    assert by_name["chat m"]["attributes"][tracing.INPUT_TOKENS] == 12
    assert len({s["trace_id"] for s in spans}) == 1


def test_two_runs_do_not_mix(tmp_path):
    tracing.init_tracing({"otel": {"exporter": "file"}})
    for name in ("A", "B"):
        with tracing.run_span(name, tmp_path / name):
            with tracing.span(f"chat {name}"):
                pass
    for name in ("A", "B"):
        names = [s["name"] for s in tracing.read_spans(tmp_path / name / "spans.jsonl")]
        assert f"chat {name}" in names and f"chat {'B' if name == 'A' else 'A'}" not in names


def test_exporter_none_is_a_noop(tmp_path):
    assert not tracing.init_tracing({"otel": {"exporter": "none"}})
    with tracing.run_span("R", tmp_path / "R"):
        with tracing.span("chat x"):
            pass
    assert not (tmp_path / "R" / "spans.jsonl").exists()


def test_bad_exporter_name_is_rejected():
    with pytest.raises(ValueError, match="file, phoenix or none"):
        tracing.init_tracing({"otel": {"exporter": "jaeger"}})


def test_content_capture_flag(monkeypatch):
    tracing.init_tracing({"otel": {"exporter": "file", "capture_content": True}})
    assert tracing.capturing()
    monkeypatch.setenv("SENTINEL_CAPTURE_CONTENT", "0")
    assert not tracing.capturing()
    monkeypatch.delenv("SENTINEL_CAPTURE_CONTENT")
    tracing.init_tracing({"otel": {"exporter": "none"}})
    tracing.shutdown_tracing()
    tracing.init_tracing({"otel": {"exporter": "file", "capture_content": False}})
    assert not tracing.capturing()


def test_phoenix_exporter_posts_otlp_to_a_collector(tmp_path, monkeypatch):
    got = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(
        "SENTINEL_PHOENIX_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/traces"
    )
    try:
        tracing.init_tracing({"otel": {"exporter": "phoenix"}})
        with tracing.run_span("R", tmp_path / "R"):
            with tracing.span("execute_tool get_open_pos"):
                pass
        tracing.flush()
    finally:
        server.shutdown()
    assert got and got[0][0] == "/v1/traces"
    assert b"execute_tool get_open_pos" in got[0][1] and b"supply-sentinel" in got[0][1]
    assert (tmp_path / "R" / "spans.jsonl").exists()  # the file copy is always written

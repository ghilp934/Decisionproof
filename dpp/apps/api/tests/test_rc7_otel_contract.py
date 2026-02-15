"""
RC-7 Contract Gate: OpenTelemetry (Traces/Metrics) + Log Correlation.

What RC-7 locks:
- Every inbound HTTP request produces a SERVER span.
- Structured JSON logs include trace/span IDs (log <-> trace correlation).
- HTTP server request duration metric exists using stable semantic conventions.

Gate-1: 200 response -> span + correlated log fields.
Gate-2: 429 (early return) -> span + correlated log fields.
Gate-3: Metrics -> http.server.request.duration histogram emits at least 1 point.

NOTE:
These tests are expected to FAIL until RC-7 is implemented.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import pytest
from httpx import ASGITransport, AsyncClient


_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _format_trace_id(trace_id: int) -> str:
    return f"{trace_id:032x}"


def _format_span_id(span_id: int) -> str:
    return f"{span_id:016x}"


def _parse_json_lines(buf: io.StringIO) -> list[dict[str, Any]]:
    buf.seek(0)
    out: list[dict[str, Any]] = []
    for raw in buf.getvalue().splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # Ignore non-JSON lines (prints, warnings). These should be rare.
            continue
    return out


def _last_completion_log(logs: list[dict[str, Any]], *, path: str, status_code: int) -> dict[str, Any]:
    candidates = [
        e
        for e in logs
        if e.get("event") == "http.request.completed"
        and e.get("path") == path
        and e.get("status_code") == status_code
    ]
    assert candidates, f"No completion log found for {path} status={status_code}. Logs: {logs[-5:]}"
    return candidates[-1]


def _require_otel() -> None:
    """Fail with a clean message if OpenTelemetry deps are not installed yet."""
    try:
        import opentelemetry  # noqa: F401
    except Exception as e:  # pragma: no cover
        pytest.fail(
            "RC-7 requires OpenTelemetry deps. Install (min): "
            "opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi "
            "opentelemetry-instrumentation-logging\n"
            f"Import error: {e!r}",
            pytrace=False,
        )


@dataclass
class OtelTestKit:
    span_exporter: Any
    metric_reader: Any
    restore_tracer_provider: Any
    restore_meter_provider: Any


@pytest.fixture()
def otel_testkit() -> OtelTestKit:
    _require_otel()

    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    # Reset global provider lock to allow re-initialization across tests
    # Access the Once lock objects - trace is in main module, metrics in _internal
    trace._TRACER_PROVIDER_SET_ONCE._done = False
    from opentelemetry.metrics import _internal as metrics_internal
    metrics_internal._METER_PROVIDER_SET_ONCE._done = False

    # Save global providers to restore after each test (avoid leaking across suite)
    old_tracer_provider = trace.get_tracer_provider()
    old_meter_provider = metrics.get_meter_provider()

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(
        resource=Resource.create({"service.name": "decisionproof-api"})
    )
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(
        resource=Resource.create({"service.name": "decisionproof-api"}),
        metric_readers=[metric_reader],
    )

    # NOTE: RC-7 implementation is expected to set these, too, but doing it here
    # makes tests deterministic and lets the app pick up the providers.
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)

    def _restore():
        # Reset locks before restoring to allow re-set
        trace._TRACER_PROVIDER_SET_ONCE._done = False
        metrics_internal._METER_PROVIDER_SET_ONCE._done = False
        trace.set_tracer_provider(old_tracer_provider)
        metrics.set_meter_provider(old_meter_provider)

    try:
        yield OtelTestKit(
            span_exporter=span_exporter,
            metric_reader=metric_reader,
            restore_tracer_provider=old_tracer_provider,
            restore_meter_provider=old_meter_provider,
        )
    finally:
        _restore()


@pytest.fixture()
def log_capture() -> io.StringIO:
    """Capture structured JSON logs from the root logger."""
    from dpp_api.utils.logging import JSONFormatter

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level

    root.handlers = [handler]
    root.setLevel(logging.INFO)

    try:
        yield buf
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)


@pytest.fixture()
def otel_app(otel_testkit: OtelTestKit):
    """Create an app instance with OTel enabled (RC-7 requires app factory)."""
    try:
        from dpp_api.main import create_app  # type: ignore
    except Exception as e:  # pragma: no cover
        pytest.fail(
            "RC-7 requires dpp_api.main.create_app(...) factory for test isolation. "
            "Add create_app and keep `app = create_app()` for backwards compat.\n"
            f"Import error: {e!r}",
            pytrace=False,
        )

    # Expected RC-7 factory signature (tests enforce this contract):
    # create_app(
    #   *,
    #   otel_enabled: bool,
    #   otel_service_name: str,
    #   otel_span_exporter: SpanExporter | None,
    #   otel_metric_reader: MetricReader | None,
    #   otel_log_correlation: bool,
    # ) -> FastAPI
    try:
        app = create_app(
            otel_enabled=True,
            otel_service_name="decisionproof-api",
            otel_span_exporter=otel_testkit.span_exporter,
            otel_metric_reader=otel_testkit.metric_reader,
            otel_log_correlation=True,
        )
    except TypeError as e:  # pragma: no cover
        pytest.fail(
            "create_app signature mismatch for RC-7. "
            "Implement the keyword args used by the tests (see fixture comment).\n"
            f"TypeError: {e}",
            pytrace=False,
        )

    # For Gate-2 deterministic 429 behavior.
    from dpp_api.rate_limiter import NoOpRateLimiter

    app.state.rate_limiter = NoOpRateLimiter(quota=60, window=60)

    try:
        yield app
    finally:
        # Cleanup: uninstrument the app to allow next test to instrument fresh
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor().uninstrument_app(app)


@pytest.mark.asyncio
async def test_gate_1_span_and_log_correlation_200(otel_app, otel_testkit: OtelTestKit, log_capture: io.StringIO):
    """Gate-1: 200 response MUST have SERVER span and logs MUST include trace/span IDs."""
    from opentelemetry.trace import SpanKind

    transport = ASGITransport(app=otel_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/test-ratelimit")

    assert resp.status_code == 200

    logs = _parse_json_lines(log_capture)
    completion = _last_completion_log(logs, path="/v1/test-ratelimit", status_code=200)

    trace_id = completion.get("trace_id")
    span_id = completion.get("span_id")
    assert isinstance(trace_id, str) and _TRACE_ID_RE.match(trace_id), f"Invalid trace_id: {trace_id!r}"
    assert isinstance(span_id, str) and _SPAN_ID_RE.match(span_id), f"Invalid span_id: {span_id!r}"

    spans = list(otel_testkit.span_exporter.get_finished_spans())
    assert spans, "No spans exported. Ensure OTel middleware/instrumentation is enabled."

    matching = [s for s in spans if _format_trace_id(s.context.trace_id) == trace_id]
    assert matching, f"No spans match trace_id={trace_id}. Exported: {[ _format_trace_id(s.context.trace_id) for s in spans ]}"

    server_spans = [s for s in matching if s.kind == SpanKind.SERVER]
    assert server_spans, f"No SERVER spans found for trace_id={trace_id}. Kinds: {[s.kind for s in matching]}"

    server = server_spans[0]
    attrs = dict(server.attributes or {})

    # Stable HTTP span semantic conventions (method/status/url parts)
    assert attrs.get("http.request.method") == "GET", f"Missing/invalid http.request.method. attrs={attrs}"
    assert int(attrs.get("http.response.status_code")) == 200, f"Missing/invalid http.response.status_code. attrs={attrs}"
    assert attrs.get("url.scheme") in {"http", "https"}, f"Missing/invalid url.scheme. attrs={attrs}"


@pytest.mark.asyncio
async def test_gate_2_span_and_log_correlation_429_early_return(otel_app, otel_testkit: OtelTestKit, log_capture: io.StringIO):
    """Gate-2: 429 (rate limit early return) MUST still produce SERVER span and correlated logs."""
    from opentelemetry.trace import SpanKind

    from dpp_api.rate_limiter import DeterministicTestLimiter

    # Force deterministic 429 on second request
    otel_app.state.rate_limiter = DeterministicTestLimiter(quota=1, window=60)

    transport = ASGITransport(app=otel_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/v1/test-ratelimit")
        r2 = await client.get("/v1/test-ratelimit")

    assert 200 <= r1.status_code < 300
    assert r2.status_code == 429

    logs = _parse_json_lines(log_capture)
    completion = _last_completion_log(logs, path="/v1/test-ratelimit", status_code=429)

    trace_id = completion.get("trace_id")
    span_id = completion.get("span_id")
    assert isinstance(trace_id, str) and _TRACE_ID_RE.match(trace_id), f"Invalid trace_id: {trace_id!r}"
    assert isinstance(span_id, str) and _SPAN_ID_RE.match(span_id), f"Invalid span_id: {span_id!r}"

    spans = list(otel_testkit.span_exporter.get_finished_spans())
    matching = [s for s in spans if _format_trace_id(s.context.trace_id) == trace_id]
    assert matching, f"No spans match trace_id={trace_id}."

    server_spans = [s for s in matching if s.kind == SpanKind.SERVER]
    assert server_spans, f"No SERVER spans found for trace_id={trace_id}."

    server = server_spans[0]
    attrs = dict(server.attributes or {})
    assert attrs.get("http.request.method") == "GET"
    assert int(attrs.get("http.response.status_code")) == 429


def _iter_metrics(metrics_data: Any) -> Iterable[Any]:
    """Flatten MetricsData -> Metric list (robust against minor SDK changes)."""
    if metrics_data is None:
        return []
    rms = getattr(metrics_data, "resource_metrics", None)
    if not rms:
        return []
    for rm in rms:
        sms = getattr(rm, "scope_metrics", None) or []
        for sm in sms:
            for m in getattr(sm, "metrics", None) or []:
                yield m


def _metric_points(metric: Any) -> list[Any]:
    data = getattr(metric, "data", None)
    if data is None:
        return []
    # HistogramData, Sum, Gauge all typically expose data_points
    return list(getattr(data, "data_points", None) or [])


def _metric_count(point: Any) -> Optional[int]:
    # Histogram datapoint has 'count'
    if hasattr(point, "count"):
        try:
            return int(point.count)
        except Exception:
            return None
    return None


@pytest.mark.asyncio
async def test_gate_3_http_server_duration_metric_exists(otel_app, otel_testkit: OtelTestKit):
    """Gate-3: MUST emit http.server.request.duration histogram (unit: seconds)."""

    # Generate at least one request
    transport = ASGITransport(app=otel_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/test-ratelimit")
    assert resp.status_code == 200

    metrics_data = otel_testkit.metric_reader.get_metrics_data()
    metrics_list = list(_iter_metrics(metrics_data))
    assert metrics_list, "No metrics collected. Ensure MeterProvider + middleware emits metrics."

    names = [getattr(m, "name", None) for m in metrics_list]

    target_name = "http.server.request.duration"
    target = next((m for m in metrics_list if getattr(m, "name", None) == target_name), None)
    assert target is not None, (
        f"Missing metric '{target_name}'. Collected names={names}. "
        "RC-7 must use stable HTTP semantic conventions."
    )

    unit = getattr(target, "unit", None)
    assert unit in {"s", "seconds"}, f"Unexpected unit for {target_name}: {unit!r}"

    points = _metric_points(target)
    assert points, f"Metric '{target_name}' has no points."

    # At least one point should have count >= 1
    counts = [c for c in (_metric_count(p) for p in points) if c is not None]
    assert counts and max(counts) >= 1, f"Metric '{target_name}' points missing count>=1. counts={counts}"

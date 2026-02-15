"""OpenTelemetry initialization for RC-7.

RC-7: Traces, Metrics, and Log Correlation
- Every inbound HTTP request produces a SERVER span
- HTTP server request duration metric (http.server.request.duration)
- Structured JSON logs include trace_id/span_id for correlation
"""

import logging
import os
from typing import Any, Optional

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


logger = logging.getLogger(__name__)


def init_otel(
    *,
    service_name: str = "decisionproof-api",
    span_exporter: Optional[SpanExporter] = None,
    metric_reader: Optional[MetricReader] = None,
    log_correlation: bool = True,
) -> tuple[Optional[TracerProvider], Optional[MeterProvider]]:
    """Initialize OpenTelemetry tracing, metrics, and log correlation.

    Args:
        service_name: Service name for OTel resource
        span_exporter: Custom span exporter (for testing). If None, uses InMemorySpanExporter.
        metric_reader: Custom metric reader (for testing). If None, creates default.
        log_correlation: Enable log correlation (inject trace/span IDs into logs)

    Returns:
        Tuple of (TracerProvider, MeterProvider) for cleanup/testing

    Note:
        When custom span_exporter or metric_reader is provided (test mode),
        this function assumes global providers are already set by test fixtures
        and only initializes log correlation. Returns (None, None) in this case.
    """
    # RC-7: Test mode detection
    # If custom exporter/reader provided, assume test environment where
    # global providers are already set by test fixtures (e.g., otel_testkit)
    is_test_mode = span_exporter is not None or metric_reader is not None

    if is_test_mode:
        # Test mode: only initialize log correlation, skip provider setup
        logger.info(
            "Test mode detected (custom exporter/reader provided). "
            "Skipping provider initialization (managed by test fixtures)."
        )
        if log_correlation:
            LoggingInstrumentor().instrument(set_logging_format=False)
            logger.info("OpenTelemetry log correlation enabled (trace_id/span_id injection)")
        return None, None

    # Production mode: initialize providers normally
    resource = Resource.create({"service.name": service_name})

    # Initialize tracing
    tracer_provider = TracerProvider(resource=resource)
    span_exporter = InMemorySpanExporter()
    logger.warning(
        "Using InMemorySpanExporter. For production, configure OTLP exporter via environment."
    )

    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    # Initialize metrics
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    metric_reader = InMemoryMetricReader()
    logger.warning(
        "Using InMemoryMetricReader. For production, configure OTLP exporter via environment."
    )

    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Initialize log correlation (inject trace/span IDs into log records)
    if log_correlation:
        LoggingInstrumentor().instrument(set_logging_format=False)
        logger.info("OpenTelemetry log correlation enabled (trace_id/span_id injection)")

    logger.info(
        f"OpenTelemetry initialized: service={service_name}, "
        f"tracing={'enabled' if tracer_provider else 'disabled'}, "
        f"metrics={'enabled' if meter_provider else 'disabled'}"
    )

    return tracer_provider, meter_provider

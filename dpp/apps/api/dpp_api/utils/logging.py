"""Structured JSON logging utilities.

P1-9: Observability with structured logs
- JSON format for log aggregation (Datadog, CloudWatch, etc.)
- Includes request_id, trace_id, span_id
- Standard fields: timestamp, level, message, module, func, line
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from dpp_api.context import (
    budget_decision_var,
    plan_key_var,
    request_id_var,
    run_id_var,
    tenant_id_var,
)


class JSONFormatter(logging.Formatter):
    """JSON log formatter with request/trace context.

    Formats log records as JSON with standard fields:
    - timestamp: ISO 8601 UTC
    - level: log level (INFO, ERROR, etc.)
    - message: log message
    - module: Python module name
    - func: function name
    - line: line number
    - request_id: from context variable (if available)
    - run_id: from context variable (MS-6: CRITICAL for debugging)
    - tenant_id: from context variable (MS-6: CRITICAL for debugging)
    - trace_id: from extra kwargs (if provided)
    - span_id: from extra kwargs (if provided)

    MS-6: All logs automatically include run_id and tenant_id when set in context.
    This enables 1-minute root cause analysis: "왜 터졌지?"
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }

        # P1-9: Add request_id from context variable
        try:
            request_id = request_id_var.get()
            if request_id:
                log_data["request_id"] = request_id
        except LookupError:
            # Context variable not set (non-request context like worker/reaper)
            pass

        # MS-6: Add run_id from context variable (CRITICAL for debugging)
        try:
            run_id = run_id_var.get()
            if run_id:
                log_data["run_id"] = run_id
        except LookupError:
            pass

        # MS-6: Add tenant_id from context variable (CRITICAL for debugging)
        try:
            tenant_id = tenant_id_var.get()
            if tenant_id:
                log_data["tenant_id"] = tenant_id
        except LookupError:
            pass

        # RC-6: Add plan_key from context variable
        try:
            plan_key = plan_key_var.get()
            if plan_key:
                log_data["plan_key"] = plan_key
        except LookupError:
            pass

        # RC-6: Add budget_decision from context variable
        try:
            budget_decision = budget_decision_var.get()
            if budget_decision:
                log_data["budget_decision"] = budget_decision
        except LookupError:
            pass

        # P1-9: Add trace_id and span_id if provided via extra kwargs
        if hasattr(record, "trace_id") and record.trace_id:
            log_data["trace_id"] = record.trace_id

        if hasattr(record, "span_id") and record.span_id:
            log_data["span_id"] = record.span_id

        # Add exception info if present
        if record.exc_info:
            log_data["exc_info"] = self.formatException(record.exc_info)

        # Add any extra fields from logger.info(..., extra={...})
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "thread",
                "threadName",
                "exc_info",
                "exc_text",
                "stack_info",
                "trace_id",
                "span_id",
            ):
                log_data[key] = value

        return json.dumps(log_data)


def configure_json_logging(log_level: str = "INFO") -> None:
    """Configure root logger with JSON formatter.

    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add JSON formatter handler
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)

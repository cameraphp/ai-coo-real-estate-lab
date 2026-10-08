from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from typing import Any, TextIO
from uuid import UUID


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC)
        ts_str = ts.isoformat().replace("+00:00", "Z")
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "timestamp": ts_str,
            "event": getattr(record, "event", "log"),
            "correlation_id": getattr(record, "correlation_id", None),
            "workflow_id": getattr(record, "workflow_id", None),
            "lead_id": getattr(record, "lead_id", None),
            "status": getattr(record, "status", None),
        }
        message = record.getMessage()
        if message:
            payload["message"] = message
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
            payload["error_text"] = str(record.exc_info[1])
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key in payload:
                    continue
                payload[key] = _json_safe(value)
        return json.dumps(payload, default=_json_safe)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(v) for v in value]
    try:
        return str(value)
    except Exception:
        return repr(type(value).__name__)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Install the JSON handler. ``stream`` defaults to stdout; the MCP server passes stderr."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())


_LOGGER_NAME = "ai_coo_real_estate_lab"


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


class SafeLoggerAdapter:
    def __init__(self, logger: logging.Logger, **base_ctx: Any) -> None:
        self._logger = logger
        self._base_ctx = base_ctx

    def with_context(self, **kwargs: Any) -> SafeLoggerAdapter:
        merged = {**self._base_ctx, **kwargs}
        return SafeLoggerAdapter(self._logger, **merged)

    def _log(self, level: int, event: str, message: str, **ctx: Any) -> None:
        merged_ctx = {**self._base_ctx, **ctx}
        extra: dict[str, Any] = {"event": event}
        if "correlation_id" in merged_ctx:
            extra["correlation_id"] = merged_ctx["correlation_id"]
        if "workflow_id" in merged_ctx:
            extra["workflow_id"] = merged_ctx["workflow_id"]
        if "lead_id" in merged_ctx:
            extra["lead_id"] = merged_ctx["lead_id"]
        if "status" in merged_ctx:
            extra["status"] = merged_ctx["status"]
        safe_extra = {k: v for k, v in merged_ctx.items() if k not in extra and k != "message"}
        if safe_extra:
            extra["extra_fields"] = safe_extra
        self._logger.log(level, message, extra=extra)

    def info(self, event: str, message: str = "", **ctx: Any) -> None:
        self._log(logging.INFO, event, message, **ctx)

    def warning(self, event: str, message: str = "", **ctx: Any) -> None:
        self._log(logging.WARNING, event, message, **ctx)

    def error(self, event: str, message: str = "", **ctx: Any) -> None:
        self._log(logging.ERROR, event, message, **ctx)

    def exception(self, event: str, message: str = "", **ctx: Any) -> None:
        merged_ctx = {**self._base_ctx, **ctx}
        extra: dict[str, Any] = {"event": event}
        if "correlation_id" in merged_ctx:
            extra["correlation_id"] = merged_ctx["correlation_id"]
        if "workflow_id" in merged_ctx:
            extra["workflow_id"] = merged_ctx["workflow_id"]
        if "lead_id" in merged_ctx:
            extra["lead_id"] = merged_ctx["lead_id"]
        if "status" in merged_ctx:
            extra["status"] = merged_ctx["status"]
        self._logger.exception(message, extra=extra)


def new_logger(**ctx: Any) -> SafeLoggerAdapter:
    return SafeLoggerAdapter(get_logger(), **ctx)

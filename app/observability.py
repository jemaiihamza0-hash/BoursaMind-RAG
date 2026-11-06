"""Safe structured logs, correlation context and bounded process metrics."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id_context: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_SENSITIVE = re.compile(
    r"password|token|authorization|cookie|csrf|secret|api[_-]?key|credential", re.I
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if _SENSITIVE.search(str(k)) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "service": "boursamind-rag",
            "logger": record.name,
            "event": getattr(record, "event", "application_log"),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_context.get()
        correlation_id = getattr(record, "correlation_id", None) or correlation_id_context.get()
        if request_id:
            data["request_id"] = request_id
        if correlation_id:
            data["correlation_id"] = correlation_id
        if isinstance(getattr(record, "fields", None), dict):
            data.update(redact(record.fields))
        if record.exc_info:
            data["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            data["traceback"] = self.formatException(record.exc_info)
        return json.dumps(redact(data), ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._durations: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[int, float]] = {}

    def increment(self, name: str, **labels: str) -> None:
        try:
            with self._lock:
                self._counters[(name, tuple(sorted(labels.items())))] += 1
        except Exception:
            return

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        try:
            key = (name, tuple(sorted(labels.items())))
            with self._lock:
                count, total = self._durations.get(key, (0, 0.0))
                self._durations[key] = (count + 1, total + max(0.0, seconds))
        except Exception:
            return

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return {
                "counters": [
                    {"name": name, "labels": dict(labels), "value": value}
                    for (name, labels), value in sorted(self._counters.items())
                ],
                "durations": [
                    {
                        "name": name,
                        "labels": dict(labels),
                        "count": value[0],
                        "sum_seconds": round(value[1], 6),
                    }
                    for (name, labels), value in sorted(self._durations.items())
                ],
            }


metrics = Metrics()


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event.replace("_", " "), extra={"event": event, "fields": fields})

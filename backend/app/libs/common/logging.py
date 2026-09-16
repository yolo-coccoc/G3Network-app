"""
Shared structured logging configuration for standalone backend processes.

The module converts standard ``logging`` records and the fields in ``extra``
into single-line JSON, suitable for a container system to collect logs. The
configuration is idempotent so repeated calls within a lifecycle do not
attach duplicate handlers.

The module intentionally uses only the Python standard library. Log
transport, retention, and centralized monitoring are out of scope for the
telemetry ingestion MVP.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.libs.common.config import settings

# The sample record provides the set of standard built-in keys. Excluding
# these keys lets fields passed via ``extra`` sit directly at the top level of
# the JSON without repeating internal details like the file path, thread ID,
# or argument tuple.
_STANDARD_LOG_RECORD_FIELDS = frozenset(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
)
# The marker is attached on the handler instead of kept as module state
# because the test suite and process bootstrap code may call
# ``configure_logging`` across fresh imports.
_HANDLER_MARKER = "_g3network_json_handler"


class JsonFormatter(logging.Formatter):
    """
    Convert a Python log record into single-line structured JSON.

    The standard output always includes a timezone-aware UTC timestamp,
    level, logger name, and rendered message. Values passed via ``extra`` are
    kept at the top level. Values that JSON doesn't support are converted
    with ``str`` so an observability error doesn't hide the application event
    that needs to be recorded.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Convert a log record into JSON.

        Args:
            record: The Python log record to serialize.

        Returns:
            A single-line JSON string containing the standard fields and
            additional fields.

        Side Effects:
            When the record contains an exception, Python's formatter may
            cache the rendered traceback on the input record itself.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Keep structured context in a form a log collector can query
        # directly, instead of nesting everything into one unstructured
        # message string.
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_LOG_RECORD_FIELDS
                and key not in {"message", "asctime"}
                and not key.startswith("_")
            }
        )

        # ``logger.exception`` stores the traceback separately from the
        # message; it must be put into the payload explicitly so the JSON
        # handler doesn't accidentally drop this information.
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int | None = None) -> None:
    """
    Configure idempotent JSON logging for a standalone backend process.

    The root logger is used so telemetry consumers, services, repositories,
    and workers all follow the same output contract. Existing handlers are
    left untouched; the function only ensures that exactly one G3Network
    JSON handler is attached.

    Args:
        level: The lowest log level the root logger emits. If omitted, uses
            ``APP_LOG_LEVEL`` from settings.

    Side Effects:
        Updates the root logger's level and may attach a stderr stream
        handler.
    """
    root_logger = logging.getLogger()
    configured_level = (
        level if level is not None else getattr(logging, settings.APP_LOG_LEVEL)
    )
    root_logger.setLevel(configured_level)

    # The lifecycle may call this function multiple times during startup
    # tests or a guarded restart. The marker prevents a record from being
    # emitted as multiple duplicate JSON lines.
    if any(
        getattr(handler, _HANDLER_MARKER, False) for handler in root_logger.handlers
    ):
        return

    # StreamHandler writes to stderr by default, matching container logging
    # convention while still leaving stdout free for the process's explicit
    # output when needed.
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    root_logger.addHandler(handler)

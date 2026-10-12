"""Smoke tests for the shared logging setup and how the code logs.

A key in a logger's ``extra`` that names a ``LogRecord`` attribute
(``message``, ``created``...) makes the logging module raise ``KeyError``, but
only once the record is actually built, i.e. when the level is enabled. The
API used to run without any logging configured, so such a call stayed silent
there and failed in every process that configures logging (the fake SMS
sender broke every invitation). These tests pin both halves.
"""

import ast
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

import app.api.main as api_main
from app.domains.identity.providers import LoggingSmsSender

# Every attribute a fresh record carries, plus the two the formatter adds.
_RESERVED_EXTRA_KEYS = frozenset(
    vars(logging.LogRecord("name", logging.INFO, "path", 0, "msg", None, None))
) | {"message", "asctime"}
_APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _reserved_extra_keys_in(path: Path) -> list[str]:
    """List the reserved keys a module passes in a literal ``extra`` dict.

    Args:
        path: A Python source file.

    Returns:
        ``"<file>:<line>: <key>"`` for each reserved key found.
    """
    findings = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and key.value in _RESERVED_EXTRA_KEYS:
                    findings.append(f"{path}:{node.lineno}: {key.value}")
    return findings


def test_no_logging_call_passes_a_reserved_record_attribute_in_extra() -> None:
    """No ``extra`` dict in the backend names a ``LogRecord`` attribute."""
    findings = [
        finding
        for path in sorted(_APP_ROOT.rglob("*.py"))
        for finding in _reserved_extra_keys_in(path)
    ]

    assert findings == []


@pytest.mark.asyncio
async def test_fake_sms_is_logged_with_its_text_when_info_logging_is_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The development SMS, code included, reaches the log instead of raising."""
    caplog.set_level(logging.INFO, logger="app.domains.identity.providers")

    await LoggingSmsSender().send_sms("+84900000020", "Your code is 123456")

    (record,) = [r for r in caplog.records if r.getMessage() == "fake_sms_sent"]
    assert record.__dict__["sms_text"] == "Your code is 123456"
    assert record.__dict__["phone_number"] == "+84900000020"


@pytest.fixture
def restored_root_logger() -> Iterator[logging.Logger]:
    """Give the root logger back with its original level and handlers.

    Yields:
        The root logger.
    """
    root_logger = logging.getLogger()
    level, handlers = root_logger.level, list(root_logger.handlers)
    yield root_logger
    root_logger.setLevel(level)
    root_logger.handlers[:] = handlers


@pytest.mark.asyncio
async def test_api_lifespan_turns_on_json_logging(
    restored_root_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting the API configures the shared JSON handler on the root logger."""

    async def no_close_db() -> None:
        """Stand in for disposing the database engine."""

    monkeypatch.setattr(api_main, "close_db", no_close_db)
    restored_root_logger.handlers[:] = []

    async with api_main.lifespan(api_main.app):
        handlers = list(restored_root_logger.handlers)

    assert len(handlers) == 1
    assert type(handlers[0].formatter).__name__ == "JsonFormatter"

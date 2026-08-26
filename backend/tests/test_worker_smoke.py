"""Smoke test lifecycle và chính sách dừng của MessageWorker."""

import asyncio
from typing import cast

import pytest

from app.domains.telemetry.ingestion.message_worker import MessageWorker
from app.domains.telemetry.schemas import TelemetryEnvelope


@pytest.mark.asyncio
async def test_message_worker_starts_and_stops_with_empty_queue() -> None:
    """Worker tạo task và dừng được khi queue không có message."""
    worker = MessageWorker(asyncio.Queue[TelemetryEnvelope]())

    await worker.start()
    assert worker._task is not None
    await worker.stop()

    assert worker._running is False


@pytest.mark.asyncio
async def test_message_worker_propagates_processing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker dừng và propagate lỗi persistence/service theo policy MVP."""
    worker = MessageWorker(asyncio.Queue[TelemetryEnvelope]())

    async def fail_processing(envelope: TelemetryEnvelope) -> None:
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(worker, "_process_message", fail_processing)
    await worker.start()
    await worker.queue.put(cast(TelemetryEnvelope, object()))
    task = worker._task
    assert task is not None

    with pytest.raises(RuntimeError, match="persistence failed"):
        await task

    assert worker._running is False

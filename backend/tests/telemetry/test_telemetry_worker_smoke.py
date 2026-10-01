"""Smoke test for MessageWorker's lifecycle and shutdown policy."""

import asyncio
from typing import cast

import pytest

from app.domains.telemetry.ingestion.message_worker import MessageWorker
from app.domains.telemetry.schemas import TelemetryEnvelope


@pytest.mark.asyncio
async def test_message_worker_starts_and_stops_with_empty_queue() -> None:
    """The worker returns its task, reuses it on a repeat start, and stops when idle."""
    worker = MessageWorker(asyncio.Queue[TelemetryEnvelope]())

    task = await worker.start()
    assert await worker.start() is task  # a second start reuses the task
    await worker.stop()

    assert task.cancelled()
    assert worker._running is False


@pytest.mark.asyncio
async def test_message_worker_propagates_processing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker stops and propagates a persistence/service error per MVP policy."""
    worker = MessageWorker(asyncio.Queue[TelemetryEnvelope]())

    async def fail_processing(envelope: TelemetryEnvelope) -> None:
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(worker, "_process_message", fail_processing)
    task = await worker.start()
    await worker.queue.put(cast(TelemetryEnvelope, object()))

    with pytest.raises(RuntimeError, match="persistence failed"):
        await task

    assert worker._running is False

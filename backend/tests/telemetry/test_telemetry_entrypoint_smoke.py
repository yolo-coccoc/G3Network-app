"""Smoke tests for how the telemetry ingestion process decides its exit status."""

import asyncio

import pytest

from app.domains.telemetry.ingestion.entrypoint import raise_unless_stopped_by_signal


async def _finished_task(
    outcome: BaseException | None, name: str
) -> asyncio.Task[object]:
    """Create a task that has already finished with the given outcome.

    Args:
        outcome: The exception the task ends with, or None to return normally.
        name: The task name.

    Returns:
        The completed task.
    """

    async def body() -> None:
        if outcome is not None:
            raise outcome

    task: asyncio.Task[object] = asyncio.create_task(body(), name=name)
    await asyncio.gather(task, return_exceptions=True)
    return task


@pytest.mark.asyncio
async def test_shutdown_signal_is_a_clean_stop() -> None:
    """SIGINT/SIGTERM ending the run does not raise, so the process exits 0."""
    signal_task = await _finished_task(None, "telemetry-shutdown-signal")

    raise_unless_stopped_by_signal({signal_task}, signal_task)


@pytest.mark.asyncio
async def test_failed_worker_error_is_re_raised() -> None:
    """A worker that died re-raises its own error, so the process exits 1.

    Regression: the run returned normally and the process exited with code 0.
    """
    signal_task: asyncio.Task[object] = asyncio.create_task(asyncio.sleep(3600))
    worker_task = await _finished_task(ValueError("db down"), "telemetry-worker")
    try:
        with pytest.raises(ValueError, match="db down"):
            raise_unless_stopped_by_signal({worker_task}, signal_task)
    finally:
        signal_task.cancel()


@pytest.mark.asyncio
async def test_consumer_ending_without_error_still_counts_as_failure() -> None:
    """A consumer that returned (e.g. lost the broker) is not a clean stop."""
    signal_task: asyncio.Task[object] = asyncio.create_task(asyncio.sleep(3600))
    consumer_task = await _finished_task(None, "telemetry-mqtt-consumer")
    try:
        with pytest.raises(RuntimeError, match="telemetry-mqtt-consumer"):
            raise_unless_stopped_by_signal({consumer_task}, signal_task)
    finally:
        signal_task.cancel()

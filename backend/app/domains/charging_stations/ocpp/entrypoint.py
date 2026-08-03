"""Entrypoint process cho OCPP 2.0.1 WebSocket gateway."""

import asyncio
import logging
import signal

from app.domains.charging_stations.ocpp.ocpp_server import run_server
from app.libs.common.logging import configure_logging
from app.libs.db.session import close_db

logger = logging.getLogger(__name__)


async def run() -> None:
    """Khởi động gateway và dừng graceful khi nhận SIGINT/SIGTERM.

    Side Effects:
        Bind WebSocket listener, đăng ký signal handler và đóng shared database
        engine sau khi gateway dừng.
    """
    configure_logging()
    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    # Signal chỉ đánh thức coroutine chờ event; chính run_server sở hữu việc
    # đóng listener để mọi connection và socket được shutdown có trật tự.
    for handled_signal in handled_signals:
        event_loop.add_signal_handler(handled_signal, stop_event.set)

    try:
        await run_server(stop_event)
    finally:
        # Database engine là shared resource của process, nên được đóng sau
        # khi gateway đã dừng và không còn handshake nào cần query station.
        await close_db()
        for handled_signal in handled_signals:
            event_loop.remove_signal_handler(handled_signal)
        logger.info("OCPP gateway process stopped")


def main() -> None:
    """Chạy event loop cho OCPP gateway và trả mã lỗi khi process thất bại."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("OCPP gateway interrupted during startup")
    except Exception:
        logger.exception("OCPP gateway process exited with failure")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

"""The logger handed to the ``python-ocpp`` library, with its frames kept out.

The library logs every frame it receives or sends at INFO
(``"<id>: receive message [...]"``) and the full request when a handler
fails. A frame holds the QR ``idTag`` token, which must never reach an
application log (IS-07, RV-CS1). The adapters therefore give the library
this dedicated logger instead of their own: records below WARNING are
dropped and the text of the others is replaced, so a warning or an error
still shows up with its traceback but never with the frame.

The adapters' own structured logs (event name plus ``extra`` fields, never a
token) keep using their module loggers.
"""

import logging

LIBRARY_LOGGER_NAME = "g3network.ocpp.library"
REDACTED_MESSAGE = "python-ocpp message (frame omitted: it may hold an idTag)"


class _FrameRedactingFilter(logging.Filter):
    """Drop library records below WARNING and blank the text of the rest.

    A filter on a logger sees only records logged directly on that logger,
    which is every record the library creates through the logger it was given.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Decide whether to keep a library record, redacting its text.

        Args:
            record: The record the library logged.

        Returns:
            ``False`` for INFO and below; ``True`` for WARNING and above,
            with ``msg`` replaced and ``args`` cleared (``exc_info`` is kept).
        """
        if record.levelno < logging.WARNING:
            return False
        record.msg = REDACTED_MESSAGE
        record.args = ()
        return True


def _build_library_logger() -> logging.Logger:
    """Create the library logger once, with the redacting filter attached.

    Returns:
        The configured logger.
    """
    library_logger = logging.getLogger(LIBRARY_LOGGER_NAME)
    library_logger.setLevel(logging.WARNING)
    if not any(
        isinstance(item, _FrameRedactingFilter) for item in library_logger.filters
    ):
        library_logger.addFilter(_FrameRedactingFilter())
    return library_logger


# Passed as ``logger=`` to ``ChargePoint.__init__`` by both adapters.
OCPP_LIBRARY_LOGGER = _build_library_logger()

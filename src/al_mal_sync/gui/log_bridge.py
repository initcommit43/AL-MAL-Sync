"""Bridges Python's stdlib logging into Qt signals, so log records from the
al_mal_sync logger (Updater, sync/runner.py, etc.) can drive a Qt widget
instead of going through logging_config.py's click-based ClickHandler."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

PACKAGE_LOGGER_NAME = "al_mal_sync"


class QtLogHandler(QObject, logging.Handler):
    """A logging.Handler that emits a Qt signal per record instead of
    writing anywhere itself. Qt signals are thread-safe across a
    queued connection, so records logged from a worker thread (a sync
    run) safely reach a slot running on the GUI thread."""

    log_emitted = Signal(str, int)  # formatted message, levelno

    def __init__(self) -> None:
        QObject.__init__(self)
        logging.Handler.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self.log_emitted.emit(message, record.levelno)


def install(verbose: bool = False) -> QtLogHandler:
    """Replace whatever's on the al_mal_sync logger with a QtLogHandler and
    return it, so callers can connect log_emitted to a widget. Mirrors
    logging_config.configure_logging()'s level/format choice, just with a
    different sink."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(levelname)s %(name)s: %(message)s" if verbose else "%(message)s"

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    handler = QtLogHandler()
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    return handler

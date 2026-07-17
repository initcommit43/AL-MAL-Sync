"""Tests for logging_config.py: level selection and that records reach the
click-backed handler (format/color routing itself is exercised indirectly
through cli.py's CliRunner-based tests)."""

from __future__ import annotations

import logging

from al_mal_sync.logging_config import PACKAGE_LOGGER_NAME, ClickHandler, configure_logging, progress


class TestConfigureLogging:
    def test_default_level_is_info(self) -> None:
        configure_logging(verbose=False)
        logger = logging.getLogger(PACKAGE_LOGGER_NAME)
        assert logger.level == logging.INFO
        assert not logger.isEnabledFor(logging.DEBUG)

    def test_verbose_level_is_debug(self) -> None:
        configure_logging(verbose=True)
        logger = logging.getLogger(PACKAGE_LOGGER_NAME)
        assert logger.level == logging.DEBUG
        assert logger.isEnabledFor(logging.DEBUG)

    def test_reconfiguring_does_not_stack_handlers(self) -> None:
        configure_logging(verbose=False)
        configure_logging(verbose=True)
        logger = logging.getLogger(PACKAGE_LOGGER_NAME)
        assert len(logger.handlers) == 1

    def test_does_not_propagate_to_root(self) -> None:
        configure_logging(verbose=False)
        logger = logging.getLogger(PACKAGE_LOGGER_NAME)
        assert logger.propagate is False


class TestClickHandler:
    def test_emit_writes_formatted_message(self, capsys) -> None:
        handler = ClickHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="al_mal_sync.test", level=logging.INFO, pathname=__file__,
            lineno=1, msg="hello %s", args=("world",), exc_info=None,
        )
        handler.emit(record)
        captured = capsys.readouterr()
        assert "hello world" in captured.err

    def test_emit_swallows_formatting_errors(self, capsys) -> None:
        handler = ClickHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        # %s with no args raises inside .format(); emit() must not propagate.
        record = logging.LogRecord(
            name="al_mal_sync.test", level=logging.INFO, pathname=__file__,
            lineno=1, msg="oops %s", args=(), exc_info=None,
        )
        handler.emit(record)  # should not raise


class TestProgress:
    def test_prints_bracketed_counter_and_label(self, capsys) -> None:
        progress(2, 5, "Some Title")
        captured = capsys.readouterr()
        assert captured.out.strip() == "[2/5] Some Title"

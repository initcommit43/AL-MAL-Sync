"""Logging setup (verbose/dry-run friendly output).

Ported from the reference Go tool's logger.go, adapted to Python's stdlib
logging instead of a bespoke leveled logger: a single handler routes records
through click.secho so colors behave correctly on Windows too (click handles
the ANSI/colorama shim), and output goes to stderr, leaving stdout free for
sync results the user might want to pipe/redirect.
"""

from __future__ import annotations

import logging

import click

PACKAGE_LOGGER_NAME = "al_mal_sync"

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "bright_black",
    logging.WARNING: "yellow",
    logging.ERROR: "red",
    logging.CRITICAL: "red",
}


class ClickHandler(logging.Handler):
    """Writes formatted records via click.secho instead of directly to
    stderr, so colors are stripped/translated correctly on any platform and
    output interleaves cleanly with click.echo() calls elsewhere in the CLI."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        click.secho(message, fg=_LEVEL_COLORS.get(record.levelno), err=True)


def configure_logging(verbose: bool = False) -> None:
    """Configure the package logger. Call once, near CLI startup.

    Non-verbose: plain "message" lines at INFO+. Verbose: debug logging with
    level/logger name prefixed, matching the reference tool's --verbose flag.
    """
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(levelname)s %(name)s: %(message)s" if verbose else "%(message)s"

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    handler = ClickHandler()
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)


def progress(current: int, total: int, label: str) -> None:
    """Print a "[N/M] label" progress line to stdout."""
    click.echo(f"[{current}/{total}] {label}")

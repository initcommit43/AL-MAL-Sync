"""Runs blocking calls (oauth.login, sync.runner.run_sync, ...) on a
background QThread so the GUI event loop never stalls. Uses the QObject +
moveToThread pattern rather than subclassing QThread, per Qt's own
recommendation -- it keeps the worker's slots running on the worker thread
while still being driven by ordinary Qt signals/slots.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class Worker(QObject):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            # Broad catch by design: this is the boundary between the worker
            # thread and the GUI thread. Any exception from the wrapped call
            # (OAuth failure, API error, ...) must become a signal, not an
            # unhandled exception on a background thread.
            self.error.emit(str(exc))
            return
        self.finished.emit(result)


def run_in_thread(
    parent: QObject,
    fn: Callable[..., Any],
    *args: Any,
    on_finished: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> tuple[QThread, Worker]:
    """Run fn(*args, **kwargs) on a background thread; on_finished(result) or
    on_error(message) is called back on the GUI thread when done.

    IMPORTANT: on_finished/on_error MUST be a genuine bound method of a
    QObject that lives on the GUI thread (e.g. `self._on_sync_finished`),
    never a lambda, closure, or functools.partial wrapping one. Qt/PySide
    only marshals a signal callback onto the receiver's thread when it can
    see the receiver's QObject affinity directly on the connected callable;
    a lambda or partial has none, so the callback silently runs on *this*
    background thread instead -- verified empirically, not just per docs --
    which makes any widget access inside it a cross-thread bug. If a
    callback needs extra context (which service, which run), store it on
    `self` before calling this function and read it back inside the real
    bound method; don't try to curry it in.

    The caller must keep the returned (thread, worker) tuple alive (e.g. as
    attributes on self) until the thread finishes -- Qt does not keep a
    Python-side reference for you, and a garbage-collected QThread/QObject
    pair is a common source of "worker silently never runs" bugs.
    """
    thread = QThread(parent)
    worker = Worker(fn, *args, **kwargs)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    if on_finished is not None:
        worker.finished.connect(on_finished)
    if on_error is not None:
        worker.error.connect(on_error)
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.error.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    return thread, worker

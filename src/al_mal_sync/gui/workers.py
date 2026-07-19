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


class _Coordinator(QObject):
    """Lives on the GUI thread for its whole life (never moved), so its
    bound methods below are always queued-delivered when a signal reaches
    them from the worker thread -- see run_in_thread's docstring for why
    that QObject-affinity requirement matters.

    Its job: call QThread.quit() then QThread.wait() before forwarding to
    the caller's on_finished/on_error, so the thread is *actually* finished
    (not just "about to finish") by the time the caller can react and drop
    its reference -- otherwise Qt can fatally abort the whole process with
    "QThread: Destroyed while thread is still running" if the caller's
    handler (e.g. clearing `self._thread = None`) lets the QThread's Python
    wrapper get garbage-collected a moment before the OS thread actually
    exits. wait() here is effectively instant: by the time finished/error
    fires, the wrapped function has already returned; it's only waiting out
    Qt's own thread-teardown handshake, not real work.

    quit() is called directly here (a plain synchronous method call, safe
    to invoke from any thread per Qt's own docs) rather than via a
    worker.finished-connected queued signal -- if it were queued, it would
    sit behind this very handler in the GUI thread's event queue, and
    wait() would then block forever waiting for a quit() that can't be
    processed until this handler returns. Calling it directly here avoids
    that ordering deadlock.
    """

    def __init__(
        self,
        thread: QThread,
        on_finished: Callable[[Any], None] | None,
        on_error: Callable[[str], None] | None,
        parent: QObject | None,
    ) -> None:
        super().__init__(parent)
        self._thread = thread
        self._on_finished = on_finished
        self._on_error = on_error

    def handle_finished(self, result: Any) -> None:
        self._thread.quit()
        self._thread.wait()
        if self._on_finished is not None:
            self._on_finished(result)

    def handle_error(self, message: str) -> None:
        self._thread.quit()
        self._thread.wait()
        if self._on_error is not None:
            self._on_error(message)


def run_in_thread(
    parent: QObject,
    fn: Callable[..., Any],
    *args: Any,
    on_finished: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> tuple[QThread, Worker]:
    """Run fn(*args, **kwargs) on a background thread; on_finished(result) or
    on_error(message) is called back on the GUI thread once the thread has
    fully finished.

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
    attributes on self) until on_finished/on_error fires -- Qt does not keep
    a Python-side reference for you, and a garbage-collected QThread/Worker
    pair before that point is a common source of "worker silently never
    runs" bugs. Dropping the reference *inside* on_finished/on_error itself
    (a natural thing to do, e.g. `self._thread = None`) is fine -- by the
    time that callback runs, the thread is confirmed fully stopped (see
    _Coordinator above).
    """
    thread = QThread(parent)
    worker = Worker(fn, *args, **kwargs)
    worker.moveToThread(thread)
    coordinator = _Coordinator(thread, on_finished, on_error, parent)

    thread.started.connect(worker.run)
    # coordinator.handle_finished/handle_error call thread.quit()+wait()
    # directly (see _Coordinator's docstring for why that must be a direct
    # call, not another queued connection racing the same event queue).
    worker.finished.connect(coordinator.handle_finished)
    worker.error.connect(coordinator.handle_error)
    worker.finished.connect(worker.deleteLater)
    worker.error.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(coordinator.deleteLater)

    thread.start()
    return thread, worker

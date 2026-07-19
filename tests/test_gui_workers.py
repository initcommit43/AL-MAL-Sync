"""Tests for gui/workers.py's run_in_thread: it must actually run fn() off
the calling thread, and on_finished/on_error must reach the caller's thread
when connected to a real QObject bound method. This locks in a threading
behavior that's easy to get subtly wrong (and was: an earlier draft of this
module's docstring assumed lambdas would work -- they silently don't, see
workers.py's docstring). QtCore only, no widgets/display needed."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer  # noqa: E402

from al_mal_sync.gui.workers import run_in_thread  # noqa: E402

# qt_app fixture is shared from conftest.py -- see its docstring for why
# every GUI test module must use the same one instead of creating its own.


class _Receiver(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.finished_thread: threading.Thread | None = None
        self.finished_result: object = None
        self.error_thread: threading.Thread | None = None
        self.error_message: str | None = None

    def on_finished(self, result: object) -> None:
        self.finished_thread = threading.current_thread()
        self.finished_result = result
        QCoreApplication.instance().quit()

    def on_error(self, message: str) -> None:
        self.error_thread = threading.current_thread()
        self.error_message = message
        QCoreApplication.instance().quit()


def _run_with_timeout(app: QCoreApplication, thread: QThread, timeout_ms: int = 3000) -> None:
    QTimer.singleShot(timeout_ms, app.quit)
    app.exec()
    # Block until the background OS thread has actually finished (not just
    # queued its finished/error signal) before returning control to the next
    # test -- otherwise a still-shutting-down QThread from this test can
    # collide with the next test's fresh QCoreApplication.exec() cycle.
    thread.wait(2000)
    # ... and drain the queue a bit further: run_in_thread's _Coordinator
    # schedules deleteLater() cleanup that may still be queued even after
    # wait() confirms the thread itself is done. Left queued, it gets
    # processed interleaved with the *next* test's own processEvents()
    # calls instead, which has been observed to abort the process (see
    # conftest.py's wait_until docstring for the same issue via a different
    # code path).
    for _ in range(20):
        app.processEvents()


class TestRunInThread:
    def test_fn_runs_off_the_calling_thread(self, qt_app: QCoreApplication) -> None:
        caller_thread = threading.current_thread()
        receiver = _Receiver()

        def where_am_i() -> threading.Thread:
            return threading.current_thread()

        # The (thread, worker) tuple must be kept alive until the thread
        # finishes -- an uncaptured return value here is exactly the
        # "worker silently never runs" footgun run_in_thread's docstring
        # warns about (verified: dropping this line makes the test flaky).
        thread, _worker = run_in_thread(receiver, where_am_i, on_finished=receiver.on_finished)
        _run_with_timeout(qt_app, thread)

        assert receiver.finished_result is not None
        assert receiver.finished_result != caller_thread

    def test_on_finished_bound_method_is_delivered_on_callers_thread(
        self, qt_app: QCoreApplication
    ) -> None:
        caller_thread = threading.current_thread()
        receiver = _Receiver()

        thread, _worker = run_in_thread(receiver, lambda: 42, on_finished=receiver.on_finished)
        _run_with_timeout(qt_app, thread)

        assert receiver.finished_result == 42
        assert receiver.finished_thread is caller_thread

    def test_on_error_bound_method_receives_exception_message(
        self, qt_app: QCoreApplication
    ) -> None:
        caller_thread = threading.current_thread()
        receiver = _Receiver()

        def boom() -> None:
            raise RuntimeError("failure")

        thread, _worker = run_in_thread(receiver, boom, on_error=receiver.on_error)
        _run_with_timeout(qt_app, thread)

        assert receiver.error_message == "failure"
        assert receiver.error_thread is caller_thread

    def test_args_and_kwargs_are_forwarded(self, qt_app: QCoreApplication) -> None:
        receiver = _Receiver()

        def add(a: int, b: int, *, c: int) -> int:
            return a + b + c

        thread, _worker = run_in_thread(receiver, add, 1, 2, c=3, on_finished=receiver.on_finished)
        _run_with_timeout(qt_app, thread)

        assert receiver.finished_result == 6

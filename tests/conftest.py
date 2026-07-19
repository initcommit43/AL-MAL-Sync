import os
import time

# GUI tests instantiate real PySide6 widgets; offscreen is the only platform
# plugin that works without an actual display, and needs to be set before
# PySide6.QtWidgets/QtGui is ever imported. setdefault so a real display
# (or an explicit override) isn't clobbered.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import pytest
    from PySide6.QtWidgets import QApplication
except ImportError:
    pass
else:

    @pytest.fixture(scope="session")
    def qt_app() -> QApplication:
        """The one QApplication for the whole test session -- QApplication
        (like QCoreApplication) is a process-wide singleton, so every GUI
        test module must share a single fixture for it rather than each
        creating its own; mixing a plain QCoreApplication in one file with a
        full QApplication in another crashes widget creation in whichever
        file's fixture didn't win the race to construct the singleton."""
        instance = QApplication.instance()
        return instance if instance is not None else QApplication([])

    @pytest.fixture(autouse=True)
    def _qt_drain_around_every_test():
        """Drain the shared QApplication's event queue before and after
        every test in the whole suite (a no-op, near-instant, until the
        first GUI test creates the QApplication).

        Belt-and-suspenders alongside wait_until's own settle phase: with
        many GUI test files sharing one session-wide QApplication, a test
        with more complex widget teardown (tables with cell widgets, etc.)
        can leave more deleteLater()-scheduled cleanup queued than a fixed
        settle_cycles count reliably drains, and that leftover work has been
        observed to abort the whole process when a much later test's
        processEvents()/app.exec() call finally processes it. Draining here
        too, for every test regardless of which file, is cheap when there's
        nothing pending and removes the guesswork.
        """
        instance = QApplication.instance()
        if instance is not None:
            for _ in range(20):
                instance.processEvents()
        yield
        instance = QApplication.instance()
        if instance is not None:
            for _ in range(20):
                instance.processEvents()

    def wait_until(
        app: QApplication, predicate, timeout_ms: int = 3000, settle_cycles: int = 20
    ) -> bool:
        """Poll processEvents() until predicate() is true (used to let a
        worker-thread signal, or a real QTimer, reach the GUI thread in
        tests), then drain the queue a bit further before returning.

        Tracks real elapsed time (time.monotonic()), not an iteration
        count -- a back-to-back processEvents() loop with nothing pending
        returns in microseconds, so counting iterations as "10ms each"
        would burn through the whole timeout budget without any actual
        wall-clock time passing, starving any real QTimer under test of the
        time it needs to fire.

        The settle phase matters because qt_app is one shared, session-wide
        QApplication: a test's deleteLater()-scheduled cleanup (workers.py's
        _Coordinator relies on this -- see its docstring) can still be
        sitting in the queue the moment predicate() turns true. Without
        settling here, the *next* test's own processEvents() calls end up
        processing this test's leftover cleanup interleaved with its own,
        which has been observed to abort the whole process.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                for _ in range(settle_cycles):
                    app.processEvents()
                return True
            time.sleep(0.005)
        return False

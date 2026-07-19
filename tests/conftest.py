import os

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

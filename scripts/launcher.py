"""Tiny launcher: double-click starts the AL-MAL-Sync GUI without needing to
remember the .venv path or CLI invocation.

Compiled into a standalone exe via PyInstaller (see README's GUI section for
the build command). This script itself only uses the stdlib, so PyInstaller
only ever compiles this few-line stub, not the app -- the GUI keeps running
out of the existing .venv untouched.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller points sys.executable at the compiled exe itself.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _error(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "AL-MAL-Sync", 0x10)
    else:
        print(message, file=sys.stderr)


def main() -> None:
    root = _project_root()
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        _error(f"Couldn't find {pythonw}.\n\nRun the project setup from README.md first.")
        sys.exit(1)

    subprocess.Popen(
        [str(pythonw), "-m", "al_mal_sync.gui.app"],
        cwd=str(root),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


if __name__ == "__main__":
    main()

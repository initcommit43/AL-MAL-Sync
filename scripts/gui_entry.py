"""PyInstaller entry point for the standalone desktop build. Imports the
package absolutely so it works as a frozen top-level script (unlike pointing
PyInstaller at a file inside the package itself, which breaks its relative
imports)."""

from __future__ import annotations

from al_mal_sync.gui.app import main

if __name__ == "__main__":
    main()

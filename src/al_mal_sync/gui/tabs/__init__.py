"""One module per main-window tab. Each tab is a self-contained QWidget that
takes whatever it needs (Config, callables into sync/runner.py, etc.) through
its constructor rather than reaching into globals."""

import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def tk_root():
    """One withdrawn Tk root for the whole run: it never maps a window, so
    nothing appears on screen. Widgets are laid out with place() at an
    explicit size, which gives them real geometry while unmapped."""
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"no display available: {error}")
    root.withdraw()
    yield root
    root.destroy()

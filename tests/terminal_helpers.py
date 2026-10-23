"""Shared helpers for the terminal emulator tests."""

from types import SimpleNamespace

from pytkterm import Terminal


def make_terminal(root, cols=20, rows=5, **kwargs):
    """A Terminal with a fixed grid whose answers to the subprocess are
    collected in terminal.written instead of being sent anywhere.

    The widget is placed at the pixel size of its grid inside the withdrawn
    root, so it has real geometry without ever being mapped, and its
    subprocess is never started."""
    kwargs.setdefault("background", "#000000")
    kwargs.setdefault("foreground", "#ffffff")
    terminal = Terminal(root, **kwargs)
    terminal._term_started = True
    terminal._term_measure_char_size()
    chrome = int(terminal.cget("borderwidth")) + int(terminal.cget("highlightthickness"))
    pad_x = 2 * (chrome + int(terminal.cget("padx")))
    pad_y = 2 * (chrome + int(terminal.cget("pady")))
    terminal.place(
        x=0,
        y=0,
        width=cols * terminal.charwidth + pad_x,
        height=rows * terminal.charheight + pad_y,
    )
    root.update()
    terminal._GRID_COLS, terminal._GRID_ROWS = terminal._term_compute_size()
    terminal._VT_ROWS = terminal._GRID_ROWS
    terminal._scroll_top = 1
    terminal._scroll_bot = terminal._GRID_ROWS
    terminal._term_reset_tabs()
    terminal.written = []
    terminal._write = terminal.written.append
    terminal._process("")
    return terminal


def shutdown(terminal):
    terminal._terminate_process()
    terminal.destroy()


def screen(terminal):
    """The visible rows as text, right-trimmed."""
    top = 1 if terminal._alt_mode else terminal.screen_top
    return [
        terminal.get(f"{line}.0", f"{line}.end").rstrip()
        for line in range(top, top + terminal._GRID_ROWS)
    ]


def cursor(terminal):
    """The cursor as 0-based (row, column) on the visible screen."""
    line, index = (int(part) for part in terminal.index("insert").split("."))
    column = terminal._term_index_to_col(line, index)
    top = 1 if terminal._alt_mode else terminal.screen_top
    return line - top, column


def tags_at(terminal, row, column):
    """The style tags on the character at the given visible row/column."""
    top = 1 if terminal._alt_mode else terminal.screen_top
    line = top + row
    index = terminal._term_col_to_index(line, column)
    return [tag for tag in terminal.tag_names(f"{line}.{index}") if tag != "sel"]


def style_at(terminal, row, column):
    """The merged tag options of the character at the given position."""
    style = {}
    for tag in tags_at(terminal, row, column):
        for option in ("foreground", "background", "underline", "overstrike"):
            value = terminal.tag_cget(tag, option)
            if value not in ("", "0"):
                style[option] = value
        font = terminal.tag_cget(tag, "font")
        if font:
            style["font"] = str(font)
    return style


def key_event(keysym, char="", state=0, keysym_num=None):
    if keysym_num is None:
        keysym_num = ord(char) if len(char) == 1 else 0
    return SimpleNamespace(keysym=keysym, char=char, state=state, keysym_num=keysym_num)


def mouse_event(x=1, y=1, state=0, num=1, delta=0, x_root=0, y_root=0):
    return SimpleNamespace(
        x=x, y=y, state=state, num=num, delta=delta, x_root=x_root, y_root=y_root
    )

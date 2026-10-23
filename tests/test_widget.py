"""Widget behavior of the terminal: cursor drawing, process lifecycle,
polling, resizing, keyboard, selection, clipboard, mouse, wheel and theme.

The PTY and subprocess layers are replaced by small fakes (with the platform
switch patched) so every code path runs on every operating system; only the
tests marked as real-PTY tests spawn an actual shell. Widgets live in a
withdrawn root and are laid out with place(), so nothing is ever shown.
"""

import os
import re
import struct
import sys
import time
import tkinter as tk
from types import SimpleNamespace

import pytest

import pytkterm
from pytkterm import Terminal
from terminal_helpers import (
    cursor,
    key_event,
    make_terminal,
    mouse_event,
    screen,
    shutdown,
    style_at,
    tags_at,
)

SHIFT = 0x1
CONTROL = 0x4
ALT = 0x8


@pytest.fixture
def term(tk_root):
    terminal = make_terminal(tk_root)
    yield terminal
    shutdown(terminal)


@pytest.fixture
def small(tk_root):
    terminal = make_terminal(tk_root, cols=5, rows=3)
    yield terminal
    shutdown(terminal)


class FakeProcess:
    """Stands in for both subprocess.Popen and a winpty process."""

    pid = 4242

    def __init__(self, polls=(None,), reads=()):
        self.polls = list(polls)
        self.reads = list(reads)
        self.terminated = False
        self.closed = False
        self.written = []

    def poll(self):
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def terminate(self):
        self.terminated = True

    def close(self):
        self.closed = True

    def read(self, size):
        if not self.reads:
            raise EOFError
        return self.reads.pop(0)

    def write(self, text):
        self.written.append(text)


def use_platform(monkeypatch, system):
    """Make Terminal take its Linux or Windows code path on any platform."""
    monkeypatch.setattr(pytkterm, "platform", SimpleNamespace(system=lambda: system))


@pytest.fixture
def posix_pty(monkeypatch):
    """Make Terminal use its POSIX code path with fake pty/fcntl/termios/
    select modules and a recording Popen, on any platform."""
    opened = [os.pipe()]
    calls = SimpleNamespace(
        master=opened[0][0],
        slave=opened[0][1],
        opened_first=False,
        ioctls=[],
        killpgs=[],
        popen_args=None,
        popen_kwargs=None,
        process=FakeProcess(),
        selects=[],
    )

    def next_select(readable, writable, errors, timeout):
        entry = calls.selects.pop(0)
        return entry() if callable(entry) else entry

    def fake_openpty():
        if calls.opened_first:
            opened.append(os.pipe())
        calls.opened_first = True
        calls.master, calls.slave = opened[-1]
        return calls.master, calls.slave

    use_platform(monkeypatch, "Linux")
    monkeypatch.setitem(sys.modules, "pty", SimpleNamespace(openpty=fake_openpty))
    monkeypatch.setitem(
        sys.modules, "fcntl", SimpleNamespace(ioctl=lambda *args: calls.ioctls.append(args))
    )
    monkeypatch.setitem(sys.modules, "termios", SimpleNamespace(TIOCSWINSZ=0x5414))
    monkeypatch.setitem(sys.modules, "select", SimpleNamespace(select=next_select))
    monkeypatch.setattr(
        os, "killpg", lambda pid, sig: calls.killpgs.append((pid, sig)), raising=False
    )

    def fake_popen(argv, **kwargs):
        calls.popen_args = argv
        calls.popen_kwargs = kwargs
        return calls.process

    monkeypatch.setattr(pytkterm.subprocess, "Popen", fake_popen)
    yield calls
    for pair in opened:
        for descriptor in pair:
            try:
                os.close(descriptor)
            except OSError:
                pass


@pytest.fixture
def windows_pty(monkeypatch):
    """Make Terminal use its Windows code path with a fake winpty."""
    calls = SimpleNamespace(spawn_args=None, spawn_kwargs=None, process=FakeProcess())

    def fake_spawn(argv, **kwargs):
        calls.spawn_args = argv
        calls.spawn_kwargs = kwargs
        return calls.process

    use_platform(monkeypatch, "Windows")
    monkeypatch.setattr(
        pytkterm, "PtyProcess", SimpleNamespace(spawn=fake_spawn), raising=False
    )
    return calls


def without_reader(monkeypatch):
    monkeypatch.setattr(Terminal, "_read", lambda self, *args: None)


def fire_binding(widget, sequence):
    """Run the Tcl script bound to sequence, with every %-substitution
    replaced by a dummy value (key events cannot be delivered to a window
    that is never mapped)."""
    script = re.sub(r"%.", "0", widget.bind(sequence))
    widget.tk.eval("while 1 {" + script + "\nbreak}")


def events(terminal, sequence):
    """Collect how often a virtual event fires on the terminal."""
    seen = []
    terminal.bind(sequence, lambda event: seen.append(True))
    return seen


# ---------------------------------------------------------------------------
# Colors, fonts and theme
# ---------------------------------------------------------------------------


def test_default_colors_come_from_the_widget_options(term):
    assert term._term_default_fg == "#ffffff"
    assert term._term_default_bg == "#000000"
    assert term._default_fg_rgb == (65535, 65535, 65535)
    assert term._default_bg_rgb == (0, 0, 0)
    assert term.tag_cget("sel", "background") == "#ffffff"
    assert term.tag_cget("sel", "foreground") == "#000000"


def test_theme_change_adopts_the_new_default_colors(term):
    term.config(background="#102030", foreground="#a0b0c0")

    term._term_on_theme_changed()

    assert term._term_default_bg == "#102030"
    assert term._term_default_fg == "#a0b0c0"
    assert term._default_bg_rgb == term.winfo_rgb("#102030")
    assert term.tag_cget("sel", "background") == "#a0b0c0"
    assert term._cursor_color == "#a0b0c0"


def test_theme_change_keeps_a_custom_cursor_color(term):
    term._process("\x1b]12;#00ff00\x07")
    term.config(background="#102030", foreground="#a0b0c0")

    term._term_on_theme_changed()

    assert term._cursor_color == "#00ff00"


def test_theme_change_is_ignored_for_the_defaults_while_on_the_alternate_screen(term):
    term._process("\x1b[?1049h")
    term.config(background="#102030", foreground="#a0b0c0")

    term._term_on_theme_changed()

    assert term._term_default_bg == "#000000"


def test_theme_change_is_ignored_for_the_defaults_in_reverse_video(term):
    term._process("\x1b[?5h")

    term._term_on_theme_changed()

    assert term._term_default_bg == "#000000"
    assert term._term_default_fg == "#ffffff"


def test_theme_change_recolors_default_dependent_style_tags(term):
    term._process("\x1b[7mX")
    (tag,) = tags_at(term, 0, 0)
    assert term.tag_cget(tag, "background") == "#ffffff"

    term.config(background="#102030", foreground="#a0b0c0")
    term._term_on_theme_changed()

    assert term.tag_cget(tag, "background") == "#a0b0c0"
    assert term.tag_cget(tag, "foreground") == "#102030"


def test_theme_changed_virtual_event_is_handled(term):
    term.config(background="#102030")

    term.event_generate("<<ThemeChanged>>")

    assert term._term_default_bg == "#102030"


def test_refreshing_the_default_colors_rereads_the_widget(term):
    term.config(background="#102030", foreground="#a0b0c0")

    term._term_refresh_default_colors()

    assert term._term_default_bg == "#102030"
    assert term._cursor_color == "#a0b0c0"
    assert term._cursor_color_custom is False


def test_default_colour_detection_and_theme_color_resolution(term):
    assert term._is_default_colour(None, term._default_fg_rgb)
    assert term._is_default_colour("#ffffff", term._default_fg_rgb)
    assert not term._is_default_colour("#ff0000", term._default_fg_rgb)
    assert term._is_default_colour("@fg", term._default_fg_rgb)

    assert term._term_theme_color("@fg") == "#ffffff"
    assert term._term_theme_color("@bg") == "#000000"
    assert term._term_theme_color("#123456") == "#123456"


def test_erase_fill_tag_exists_only_for_a_non_default_background(term):
    assert term._term_erase_fill_tag() is None

    term._process("\x1b[42m")
    tag = term._term_erase_fill_tag()

    assert term.tag_cget(tag, "background") == "#00cd00"
    assert term._term_erase_fill_tag() == tag


def test_erase_fill_with_the_foreground_color_background_follows_the_theme(term):
    term._process("\x1b[7m")

    tag = term._term_erase_fill_tag()
    assert term.tag_cget(tag, "background") == "#ffffff"

    term.config(background="#102030", foreground="#a0b0c0")
    term._term_on_theme_changed()

    assert term.tag_cget(tag, "background") == "#a0b0c0"


def test_font_variants_are_cached_and_styled(term):
    plain = term._term_font_variant()
    bold = term._term_font_variant(bold=True)
    italic = term._term_font_variant(italic=True)

    assert term._term_font_variant() is plain
    assert bold.actual("weight") == "bold"
    assert italic.actual("slant") == "italic"
    assert plain.actual("weight") == "normal"
    assert plain.actual("size") == term._term_font_size


def test_character_size_is_measured_once(term):
    width, height = term.charwidth, term.charheight

    term._term_font = SimpleNamespace(measure=lambda text: 999, metrics=lambda name: 999)
    term._term_measure_char_size()

    assert (term.charwidth, term.charheight) == (width, height)
    assert width > 0 and height > 0


def test_constructor_font_is_used_explicitly(tk_root):
    terminal = Terminal(tk_root, font="Courier", fontsize=15)
    try:
        assert terminal._term_font_name == "Courier"
        assert terminal._term_font_size == 15
        assert terminal.cget("wrap") == "none"
        assert terminal._term_font_variant(bold=True).actual("size") == 15
    finally:
        shutdown(terminal)


# ---------------------------------------------------------------------------
# Cursor drawing
# ---------------------------------------------------------------------------


def cursor_box(terminal):
    info = terminal._cursor_widget.place_info()
    return tuple(int(info[key]) for key in ("x", "y", "width", "height"))


def test_block_cursor_is_placed_over_the_cursor_cell(tk_root, term):
    term._process("\x1b[2;4Hq\x1b[2;4H")
    tk_root.update()

    assert term._cursor_widget.winfo_manager() == "place"
    assert cursor_box(term) == (
        3 * term.charwidth,
        1 * term.charheight,
        term.charwidth,
        term.charheight,
    )
    assert term._cursor_widget.cget("text") == "q"


def test_block_cursor_inverts_the_colors_of_the_character_under_it(tk_root, term):
    term._process("\x1b[31mR\x1b[0m\x1b[1;1H")
    tk_root.update()

    assert term._cursor_widget.cget("background") == "#cd0000"
    assert term._cursor_widget.cget("foreground") == "#000000"


def test_block_cursor_over_a_plain_cell_uses_the_default_colors(tk_root, term):
    tk_root.update()

    assert term._cursor_widget.cget("background") == "#ffffff"
    assert term._cursor_widget.cget("foreground") == "#000000"
    assert term._cursor_widget.cget("text") == " "


def test_bar_and_underline_cursors_cover_only_part_of_the_cell(tk_root, term):
    term._process("\x1b[6 q")
    tk_root.update()
    assert cursor_box(term)[2:] == (2, term.charheight)

    term._process("\x1b[4 q")
    tk_root.update()
    x, y, width, height = cursor_box(term)
    assert (width, height) == (term.charwidth, 2)
    assert y == term.charheight - 2


def test_custom_cursor_color_is_used(tk_root, term):
    term._process("\x1b]12;#00ff00\x07")
    tk_root.update()

    assert term._cursor_widget.cget("background") == "#00ff00"
    assert term._cursor_widget.cget("foreground") == "#000000"


@pytest.mark.parametrize(
    "setup",
    [
        lambda t: t._process("\x1b[?25l"),
        lambda t: setattr(t, "_cursor_has_focus", False),
        lambda t: (setattr(t, "_cursor_blink_enabled", True), setattr(t, "_cursor_blink_visible", False)),
    ],
    ids=["dectcem-hidden", "no-focus", "blinked-off"],
)
def test_cursor_is_hidden_when_it_should_not_show(tk_root, term, setup):
    tk_root.update()
    assert term._cursor_widget.winfo_manager() == "place"

    setup(term)
    term._cursor_redraw()

    assert term._cursor_widget.winfo_manager() == ""


def test_cursor_is_hidden_before_the_character_size_is_known(tk_root):
    terminal = Terminal(tk_root)
    try:
        terminal._cursor_redraw()

        assert terminal.charwidth is None
        assert terminal._cursor_widget.winfo_manager() == ""
    finally:
        shutdown(terminal)


def test_cursor_is_hidden_when_its_cell_is_scrolled_out_of_view(tk_root, small):
    small._process("\r\n".join(str(n) for n in range(30)))
    tk_root.update()
    small.yview_moveto(0)
    small._term_follow_view = lambda: None
    tk_root.update()

    small._cursor_redraw()

    assert small._cursor_widget.winfo_manager() == ""


def test_cursor_redraws_are_coalesced_until_idle(tk_root, term):
    tk_root.update()
    term._cursor_redraw_pending = False
    redraws = []
    term._cursor_redraw = lambda: redraws.append(True)

    term._cursor_schedule_redraw()
    term._cursor_schedule_redraw()
    term._cursor_schedule_redraw()
    assert redraws == []

    tk_root.update()
    assert redraws == [True]
    assert term._cursor_redraw_pending is False


def test_cursor_blink_tick_toggles_visibility_and_reschedules(term):
    assert term._cursor_blink_visible is True

    term._cursor_blink_tick()
    assert term._cursor_blink_visible is False
    assert term._cursor_blink_after_id is not None

    term._cursor_blink_tick()
    assert term._cursor_blink_visible is True


def test_cursor_blink_tick_does_nothing_once_closed(term):
    term._closed = True

    term._cursor_blink_tick()

    assert term._cursor_blink_visible is True
    assert term._cursor_blink_after_id is None


def test_resetting_the_blink_phase_shows_the_cursor_and_restarts_the_timer(term):
    term._cursor_blink_visible = False
    first_id = term._cursor_blink_after_id

    term._cursor_reset_blink_phase()

    assert term._cursor_blink_visible is True
    assert term._cursor_blink_after_id != first_id


def test_disabling_cursor_blink_stops_the_timer(term):
    term._cursor_set_blink_enabled(False)

    assert term._cursor_blink_after_id is None
    assert term._cursor_blink_visible is True


def test_moving_the_insert_mark_moves_the_terminal_cursor(term):
    term._process("abcdef\r\n\r\nxyz1234")
    term.mark_set("insert", "3.4")

    assert cursor(term) == (2, 4)
    assert term._cur_line == 3
    assert term.index("insert") == "3.4"


def test_other_marks_behave_normally(term):
    term._process("abc\r\nxyz")
    before = cursor(term)
    term.mark_set("custom", "2.1")

    assert term.index("custom") == "2.1"
    assert cursor(term) == before


def test_index_delete_compare_see_and_bbox_resolve_insert(tk_root, term):
    term._process("hello")
    term.mark_set("insert", "1.2")

    assert term.index("insert") == "1.2"
    assert term.index("insert + 1 chars") == "1.3"
    assert term.compare("insert", "==", "1.2")
    term.delete("insert", "insert + 2 chars")
    assert term.get("1.0", "1.end").rstrip() == "heo"
    assert term.bbox("insert") is not None
    assert term.see("insert") is None


def test_pending_callbacks_are_tracked_and_cancelled(term):
    first = term.after(100000, lambda: None)
    second = term.after_idle(lambda: None)
    assert {first, second} <= term._pending_after_ids

    term.after_cancel(first)
    assert first not in term._pending_after_ids

    term._terminate_process()
    assert term._pending_after_ids == set()


def test_callbacks_forget_their_id_once_they_fire(tk_root, term):
    fired = []
    handle = term.after(1, lambda: fired.append(True))

    for _ in range(50):
        tk_root.update()
        if fired:
            break
        time.sleep(0.01)

    assert fired == [True]
    assert handle not in term._pending_after_ids


def test_after_without_a_callback_just_sleeps(term):
    assert term.after(1) is None


# ---------------------------------------------------------------------------
# Starting the shell
# ---------------------------------------------------------------------------


def test_first_map_starts_the_process_once(monkeypatch, tk_root):
    terminal = Terminal(tk_root)
    starts = []
    monkeypatch.setattr(terminal, "_term_start_process", lambda: starts.append(True))
    monkeypatch.setattr(terminal, "_term_apply_resize", lambda: None)
    try:
        terminal._term_on_map(None)
        terminal._term_on_map(None)

        assert starts == [True]
        assert terminal._term_started is True
    finally:
        shutdown(terminal)


def test_later_maps_only_refit_and_redraw(monkeypatch, term):
    refits = []
    monkeypatch.setattr(term, "_term_apply_resize", lambda: refits.append(True))
    monkeypatch.setattr(
        term, "_term_start_process", lambda: pytest.fail("must not restart the shell")
    )

    term._term_on_map(None)

    assert refits == [True]


def test_posix_start_spawns_the_default_shell_on_a_pty(posix_pty, monkeypatch, term):
    without_reader(monkeypatch)
    monkeypatch.setenv("SHELL", "/bin/fake-shell")

    term._term_start_process()

    assert posix_pty.popen_args == ["/bin/fake-shell"]
    kwargs = posix_pty.popen_kwargs
    assert kwargs["preexec_fn"] == term._term_preexec
    assert kwargs["close_fds"] is True
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == posix_pty.slave
    assert kwargs["env"]["TERM"] == "xterm-256color"
    assert kwargs["env"]["COLORTERM"] == "truecolor"
    assert kwargs["env"]["COLORFGBG"] == "15;0"
    assert term.proc is posix_pty.process
    assert term.master_fd == posix_pty.master
    assert term._read_generation == 1
    ((fd, request, size),) = posix_pty.ioctls
    assert (fd, request) == (posix_pty.master, 0x5414)
    assert struct.unpack("HHHH", size)[:2] == (5, 20)


def test_posix_start_runs_a_custom_command_instead_of_the_shell(
    posix_pty, monkeypatch, tk_root
):
    without_reader(monkeypatch)
    terminal = make_terminal(tk_root, command=["top", "-b"])
    try:
        terminal._term_start_process()

        assert posix_pty.popen_args == ["top", "-b"]
    finally:
        shutdown(terminal)


def test_light_default_colors_are_advertised_through_colorfgbg(
    posix_pty, monkeypatch, tk_root
):
    without_reader(monkeypatch)
    terminal = make_terminal(tk_root, background="#ffffff", foreground="#000000")
    try:
        terminal._term_start_process()

        assert posix_pty.popen_kwargs["env"]["COLORFGBG"] == "0;15"
    finally:
        shutdown(terminal)


def test_nocolor_terminal_does_not_set_colorterm_itself(
    posix_pty, monkeypatch, tk_root
):
    without_reader(monkeypatch)
    monkeypatch.delenv("COLORTERM", raising=False)
    terminal = make_terminal(tk_root, nocolor=True)
    try:
        terminal._term_start_process()

        assert "COLORTERM" not in posix_pty.popen_kwargs["env"]
    finally:
        shutdown(terminal)


def test_nocolor_terminal_still_advertises_the_color_support_it_inherited(
    posix_pty, monkeypatch, tk_root
):
    without_reader(monkeypatch)
    monkeypatch.setenv("COLORTERM", "24bit")
    terminal = make_terminal(tk_root, nocolor=True)
    try:
        terminal._term_start_process()

        env = posix_pty.popen_kwargs["env"]
        assert env["COLORTERM"] == "24bit"
        assert env["TERM"] == "xterm-256color"
    finally:
        shutdown(terminal)


def test_start_sizes_the_grid_and_resets_scrolling_and_tabs(posix_pty, monkeypatch, term):
    without_reader(monkeypatch)
    term._scroll_top, term._scroll_bot = 2, 3
    term._tab_stops = set()

    term._term_start_process()

    assert (term._GRID_COLS, term._GRID_ROWS, term._VT_ROWS) == (20, 5, 5)
    assert (term._scroll_top, term._scroll_bot) == (1, 5)
    assert term._tab_stops == {8, 16}


def test_start_begins_polling_for_output(posix_pty, monkeypatch, term):
    without_reader(monkeypatch)

    term._term_start_process()

    assert any(
        pytkterm.tk.Misc.after_info(term, aid)[1] == "timer"
        for aid in term._pending_after_ids
        if aid.startswith("after#")
    ) or term._pending_after_ids


def test_windows_start_spawns_powershell_by_default(windows_pty, monkeypatch, term):
    without_reader(monkeypatch)

    term._term_start_process()

    assert windows_pty.spawn_args == ["powershell.exe"]
    assert windows_pty.spawn_kwargs["dimensions"] == (5, 20)
    assert term.proc is windows_pty.process


def test_windows_start_runs_a_custom_command(windows_pty, monkeypatch, tk_root):
    without_reader(monkeypatch)
    terminal = make_terminal(tk_root, command=["cmd.exe", "/k"])
    try:
        terminal._term_start_process()

        assert windows_pty.spawn_args == ["cmd.exe", "/k"]
    finally:
        shutdown(terminal)


# ---------------------------------------------------------------------------
# Reading and writing the PTY
# ---------------------------------------------------------------------------


def run_reader(terminal, posix_pty, writes, selects, polls, fd=None):
    """Run Terminal._read against a real pipe with scripted select()."""
    for data in writes:
        os.write(posix_pty.slave, data)
    posix_pty.selects.extend(selects)
    process = FakeProcess(polls=polls)
    Terminal._read(
        terminal,
        terminal._read_generation,
        terminal._out_q,
        posix_pty.master if fd is None else fd,
        process,
    )
    items = []
    while not terminal._out_q.empty():
        items.append(terminal._out_q.get_nowait())
    return items


def test_posix_reader_decodes_output_and_signals_the_end(posix_pty, term):
    items = run_reader(
        term,
        posix_pty,
        writes=["héllo".encode()],
        selects=[([posix_pty.master], [], []), ([], [], [])],
        polls=[None, 0],
    )

    assert items == ["héllo", None]


def test_posix_reader_reassembles_multibyte_characters_split_across_reads(posix_pty, term):
    ready = ([posix_pty.master], [], [])

    def second_half_arrives():
        os.write(posix_pty.slave, b"\xa9")
        return ready

    items = run_reader(
        term,
        posix_pty,
        writes=[b"\xc3"],
        selects=[ready, second_half_arrives, ([], [], [])],
        polls=[None, 0],
    )

    assert "".join(item for item in items if item) == "é"
    assert items[-1] is None


def test_posix_reader_stops_at_end_of_file(posix_pty, term):
    os.close(posix_pty.slave)

    items = run_reader(
        term, posix_pty, writes=[], selects=[([posix_pty.master], [], [])], polls=[None]
    )

    assert items == [None]


def test_posix_reader_stops_when_reading_fails(posix_pty, term):
    items = run_reader(
        term,
        posix_pty,
        writes=[],
        selects=[([99999], [], [])],
        polls=[None],
        fd=99999,
    )

    assert items == [None]


def test_reader_of_a_stale_generation_exits_after_signalling_the_end(posix_pty, term):
    term._read_generation = 7

    Terminal._read(term, 3, term._out_q, posix_pty.master, FakeProcess())

    assert term._out_q.get_nowait() is None
    assert term._out_q.empty()


def test_windows_reader_passes_text_and_decodes_bytes(windows_pty, term):
    process = FakeProcess(reads=["text", "bé".encode()])

    Terminal._read(term, term._read_generation, term._out_q, None, process)

    assert [term._out_q.get_nowait() for _ in range(3)] == ["text", "bé", None]


def test_windows_reader_skips_empty_reads(windows_pty, term):
    process = FakeProcess(reads=["", "x"])

    Terminal._read(term, term._read_generation, term._out_q, None, process)

    assert [term._out_q.get_nowait() for _ in range(2)] == ["x", None]


def test_posix_write_sends_bytes_to_the_pty(posix_pty, term):
    term.master_fd = posix_pty.slave
    del term._write

    term._write(b"typed")

    assert os.read(posix_pty.master, 16) == b"typed"


def test_windows_write_sends_decoded_text(windows_pty, term):
    term.proc = windows_pty.process
    del term._write

    term._write("é".encode())

    assert windows_pty.process.written == ["é"]


def test_windows_write_without_a_process_is_ignored(windows_pty, term):
    del term._write

    term._write(b"lost")


# ---------------------------------------------------------------------------
# Polling output and the end of the process
# ---------------------------------------------------------------------------


def test_poll_renders_queued_text_and_reports_it(term):
    processed = events(term, "<<TerminalOutputProcessed>>")
    term._out_q.put("hello")
    term._out_q.put("\r\nworld")

    term._poll()

    assert screen(term)[:2] == ["hello", "world"]
    assert processed == [True]
    assert term._poll_after_id is not None
    assert term._polling is False


def test_poll_with_nothing_queued_reports_nothing(term):
    processed = events(term, "<<TerminalOutputProcessed>>")

    term._poll()

    assert processed == []
    assert term._poll_after_id is not None


def test_poll_does_not_reenter_itself(term):
    term._polling = True
    term._out_q.put("x")

    term._poll()

    assert screen(term)[0] == ""


def test_poll_spreads_a_large_backlog_over_several_frames(monkeypatch, tk_root, term):
    monkeypatch.setattr(pytkterm, "_TERM_FRAME_BUDGET", -1)
    for letter in "abc":
        term._out_q.put(letter)

    term._poll()
    assert screen(term)[0] == "a"

    for _ in range(100):
        tk_root.update()
        if screen(term)[0] == "abc":
            break
        time.sleep(0.01)

    assert screen(term)[0] == "abc"


def test_poll_keeps_going_after_an_emulator_error(monkeypatch, term):
    def fail(text):
        raise ValueError("boom")

    monkeypatch.setattr(term, "_process", fail)
    term._out_q.put("x")

    term._poll()

    assert term._poll_after_id is not None
    assert term._polling is False


def test_process_exit_without_end_message_tears_the_terminal_down(tk_root, term):
    ended = events(term, "<<TerminalProcessEnded>>")
    stopped = events(term, "<<TerminalStopped>>")
    term._out_q.put("bye")
    term._out_q.put(None)

    term._poll()
    tk_root.update()

    assert screen(term)[0] == "bye"
    assert term.running is False
    assert ended == [True]
    assert stopped == [True]
    assert term._closed is True


def test_process_exit_with_end_message_waits_for_a_key(tk_root):
    terminal = make_terminal(tk_root, endmessage="[done]")
    try:
        stopped = events(terminal, "<<TerminalStopped>>")
        terminal._process("output")
        terminal._out_q.put(None)

        terminal._poll()
        tk_root.update()

        assert terminal.running is False
        assert terminal._closed is False
        assert stopped == []
        text = terminal.get("1.0", "end - 1 char")
        assert text.rstrip().endswith("[done]")
        assert "output" in text

        fire_binding(terminal, "<Key>")
        tk_root.update()
        assert terminal._closed is True
        assert stopped == [True]
    finally:
        shutdown(terminal)


def test_end_message_replaces_the_blank_lines_below_the_output(tk_root):
    terminal = make_terminal(tk_root, endmessage="[done]")
    try:
        terminal._process("only line")
        terminal._out_q.put(None)

        terminal._poll()

        lines = terminal.get("1.0", "end - 1 char").split("\n")
        assert lines[0].rstrip() == "only line"
        assert lines[-1] == "[done]"
        assert lines[1:-1] == ["", ""]
    finally:
        shutdown(terminal)


def test_keys_are_ignored_after_the_process_ended(term):
    term.running = False

    assert term._key(key_event("a", "a")) == "break"
    assert term.written == []


def test_terminating_twice_reports_once_and_clears_pending_output(posix_pty, term):
    stopped = events(term, "<<TerminalStopped>>")
    ended = events(term, "<<TerminalProcessEnded>>")
    term.proc = posix_pty.process
    term.master_fd = posix_pty.master
    term._out_q.put("unread")

    term._terminate_process()
    term._terminate_process()

    assert stopped == [True]
    assert ended == [True]
    assert term._out_q.empty()
    assert posix_pty.process.terminated is True
    with pytest.raises(OSError):
        os.close(posix_pty.master)


def test_terminating_cancels_every_pending_callback(term):
    term._resize_after_id = term.after(100000, lambda: None)
    term._cursor_schedule_redraw()

    term._terminate_process()

    assert term._pending_after_ids == set()
    assert term._cursor_redraw_pending is False
    assert term._poll_after_id is None
    assert term._resize_after_id is None


def test_windows_terminate_closes_the_process(windows_pty, term):
    term.proc = windows_pty.process

    term._terminate_process()

    assert windows_pty.process.closed is True


def test_terminating_with_no_process_does_not_fail(term):
    term._terminate_process()

    assert term._closed is True


def test_destroying_the_widget_terminates_the_process(tk_root):
    terminal = make_terminal(tk_root)
    stopped = events(terminal, "<<TerminalStopped>>")

    terminal.destroy()

    assert terminal._closed is True
    assert stopped == [] or stopped == [True]


def test_restart_starts_a_fresh_shell_without_reporting_a_stop(
    posix_pty, monkeypatch, term
):
    without_reader(monkeypatch)
    stopped = events(term, "<<TerminalStopped>>")
    term._process("old\x1b[?1h\x1b[?1000h\x1b[31m\x1b[?1049h")
    term._term_start_process()
    first_generation = term._read_generation

    term.restart()

    assert stopped == []
    assert term._closed is False
    assert term.running is True
    assert term._alt_mode is False
    assert term._alt_saved is None
    assert term._app_cursor is False
    assert term._mouse_mode == 0
    assert term._sgr_state["fg"] is None
    assert term.screen_top == 1
    assert term._follow_bottom is True
    assert term.get("1.0", "end - 1 char").strip() == ""
    assert term._read_generation > first_generation
    assert term._cursor_shape == "block"
    assert term._cursor_dectcem_visible is True


def test_restart_after_the_process_ended_rebinds_keys(posix_pty, monkeypatch, tk_root):
    without_reader(monkeypatch)
    terminal = make_terminal(tk_root, endmessage="[done]")
    try:
        terminal._term_start_process()
        terminal._out_q.put(None)
        terminal._poll()

        terminal.restart()

        assert terminal.running is True
        assert terminal.bind("<Key>")
        assert terminal._closed is False
    finally:
        shutdown(terminal)


# ---------------------------------------------------------------------------
# Resizing
# ---------------------------------------------------------------------------


def test_configure_events_are_debounced_and_deduplicated(term):
    term._resize_after_id = None
    term._last_term_size = (0, 0)

    term._term_on_configure(SimpleNamespace(width=300, height=200))
    first = term._resize_after_id
    assert first is not None

    term._term_on_configure(SimpleNamespace(width=300, height=200))
    assert term._resize_after_id == first

    term._term_on_configure(SimpleNamespace(width=310, height=200))
    assert term._resize_after_id not in (None, first)
    assert first not in term._pending_after_ids


def test_apply_resize_waits_for_the_process_to_start(tk_root):
    terminal = Terminal(tk_root)
    resized = []
    terminal._term_resize_grid = lambda cols, rows: resized.append((cols, rows))
    try:
        terminal._term_apply_resize()

        assert resized == []
    finally:
        shutdown(terminal)


def test_apply_resize_refits_only_when_the_size_changed(tk_root, term):
    resized = []
    term._term_resize_grid = lambda cols, rows: resized.append((cols, rows))

    term._term_apply_resize()
    assert resized == []

    term.place(width=40 * term.charwidth + 6, height=9 * term.charheight + 6)
    tk_root.update()
    term._term_apply_resize()
    assert resized == [(40, 9)]


def test_apply_resize_follows_the_bottom_and_redraws_the_cursor(monkeypatch, term):
    followed = []
    monkeypatch.setattr(term, "_term_follow_view", lambda: followed.append(True))

    term._follow_bottom = True
    term._term_apply_resize()

    assert followed == [True]

    followed.clear()
    term._follow_bottom = False
    term._term_apply_resize()
    assert followed == []


def test_apply_resize_ignores_a_destroyed_widget(tk_root):
    terminal = make_terminal(tk_root)
    shutdown(terminal)

    terminal._term_apply_resize()


@pytest.fixture
def filled(tk_root):
    terminal = make_terminal(tk_root, cols=10, rows=4)
    terminal._process("1\r\n2\r\n3\r\n4")
    yield terminal
    shutdown(terminal)


def test_shrinking_pushes_the_top_rows_into_scrollback(filled):
    filled._term_resize_grid(10, 2)

    assert screen(filled) == ["3", "4"]
    assert cursor(filled)[0] == 1
    assert filled.get("1.0", "1.end") == "1"


def test_shrinking_with_blank_rows_below_the_cursor_keeps_the_text(tk_root):
    terminal = make_terminal(tk_root, cols=10, rows=4)
    try:
        terminal._process("1\r\n2")

        terminal._term_resize_grid(10, 2)

        assert screen(terminal) == ["1", "2"]
        assert terminal.screen_top == 1
    finally:
        shutdown(terminal)


def test_growing_shows_scrollback_again(filled):
    filled._term_resize_grid(10, 2)

    filled._term_resize_grid(10, 4)

    assert (filled._GRID_ROWS, filled._VT_ROWS) == (4, 4)
    assert (filled._scroll_top, filled._scroll_bot) == (1, 4)


def test_narrowing_truncates_the_rows_on_the_screen(tk_root):
    terminal = make_terminal(tk_root, cols=10, rows=2)
    try:
        terminal._process("1234567890\r\nabcdefghij")

        terminal._term_resize_grid(5, 2)

        assert screen(terminal) == ["12345", "abcde"]
        assert terminal._GRID_COLS == 5
    finally:
        shutdown(terminal)


def test_narrowing_leaves_text_already_in_scrollback_alone(tk_root):
    terminal = make_terminal(tk_root, cols=10, rows=2)
    try:
        terminal._process("1234567890\r\nabcdefghij\r\nklmnopqrst")

        terminal._term_resize_grid(5, 2)

        assert terminal.get("1.0", "1.end") == "1234567890"
        assert screen(terminal) == ["abcde", "klmno"]
    finally:
        shutdown(terminal)


def test_widening_adds_default_tab_stops(filled):
    filled._term_resize_grid(30, 4)

    assert {16, 24} <= filled._tab_stops
    assert filled._GRID_COLS == 30


def test_resize_resets_the_scroll_region(filled):
    filled._process("\x1b[2;3r")

    filled._term_resize_grid(5, 4)

    assert (filled._scroll_top, filled._scroll_bot) == (1, 4)


def test_alternate_screen_resize_keeps_the_exact_grid(tk_root):
    terminal = make_terminal(tk_root, cols=10, rows=4)
    try:
        terminal._process("main1\r\nmain2\x1b[?1049hALT\x1b[3;2Hx")

        terminal._term_resize_grid(12, 6)
        assert len(screen(terminal)) == 6
        assert all(
            len(terminal.get(f"{line}.0", f"{line}.end")) == 12 for line in range(1, 7)
        )
        assert screen(terminal)[0] == "ALT"

        terminal._term_resize_grid(8, 3)
        assert int(terminal.index("end - 1 char").split(".")[0]) == 3
        assert all(
            len(terminal.get(f"{line}.0", f"{line}.end")) == 8 for line in range(1, 4)
        )
        assert cursor(terminal)[0] <= 2

        terminal._process("\x1b[?1049l")
        assert screen(terminal)[0] == "main1"
    finally:
        shutdown(terminal)


def test_posix_resize_tells_the_pty_and_the_process_group(posix_pty, monkeypatch, term):
    without_reader(monkeypatch)
    term._term_start_process()
    posix_pty.ioctls.clear()

    term._term_resize_grid(30, 7)

    ((fd, request, size),) = posix_pty.ioctls
    assert (fd, request) == (posix_pty.master, 0x5414)
    assert struct.unpack("HHHH", size)[:2] == (7, 30)
    assert posix_pty.killpgs == [(FakeProcess.pid, 28)]


def test_posix_resize_before_the_pty_exists_does_not_signal(posix_pty, term):
    term._term_resize_grid(30, 7)

    assert posix_pty.ioctls == []
    assert posix_pty.killpgs == []


def test_posix_resize_ignores_a_vanished_process(posix_pty, monkeypatch, term):
    without_reader(monkeypatch)
    term._term_start_process()

    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", gone)

    term._term_resize_grid(30, 7)

    assert term._GRID_COLS == 30


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------


def press(terminal, keysym, char="", state=0, keysym_num=None):
    result = terminal._key(key_event(keysym, char, state, keysym_num))
    sent = b"".join(terminal.written)
    terminal.written.clear()
    assert result == "break"
    return sent


@pytest.mark.parametrize(
    ("keysym", "char", "state", "expected"),
    [
        ("Return", "\r", 0, b"\r"),
        ("BackSpace", "\x08", 0, b"\x7f"),
        ("Tab", "\t", 0, b"\t"),
        ("Tab", "\t", SHIFT, b"\x1b[Z"),
        ("ISO_Left_Tab", "", SHIFT, b"\x1b[Z"),
        ("Up", "", 0, b"\x1b[A"),
        ("Down", "", 0, b"\x1b[B"),
        ("Right", "", 0, b"\x1b[C"),
        ("Left", "", 0, b"\x1b[D"),
        ("Home", "", 0, b"\x1b[H"),
        ("End", "", 0, b"\x1b[F"),
        ("Up", "", CONTROL, b"\x1b[1;5A"),
        ("Left", "", SHIFT, b"\x1b[1;2D"),
        ("Right", "", CONTROL | SHIFT, b"\x1b[1;6C"),
        ("Insert", "", 0, b"\x1b[2~"),
        ("Delete", "", 0, b"\x1b[3~"),
        ("Prior", "", 0, b"\x1b[5~"),
        ("Next", "", 0, b"\x1b[6~"),
        ("Delete", "", CONTROL, b"\x1b[3;5~"),
        ("F5", "", 0, b"\x1b[15~"),
        ("F12", "", 0, b"\x1b[24~"),
        ("F5", "", SHIFT, b"\x1b[15;2~"),
        ("F1", "", 0, b"\x1bOP"),
        ("F4", "", 0, b"\x1bOS"),
        ("F2", "", SHIFT, b"\x1b[1;2Q"),
        ("a", "a", 0, b"a"),
        ("A", "A", SHIFT, b"A"),
        ("eacute", "é", 0, "é".encode()),
        ("c", "\x03", CONTROL, b"\x03"),
        ("d", "\x04", CONTROL, b"\x04"),
        ("space", "", CONTROL, b"\x00"),
        ("at", "", CONTROL, b"\x00"),
        ("2", "", CONTROL, b"\x00"),
        ("bracketleft", "", CONTROL, b"\x1b"),
        ("3", "", CONTROL, b"\x1b"),
        ("backslash", "", CONTROL, b"\x1c"),
        ("4", "", CONTROL, b"\x1c"),
        ("bracketright", "", CONTROL, b"\x1d"),
        ("5", "", CONTROL, b"\x1d"),
        ("asciicircum", "", CONTROL, b"\x1e"),
        ("6", "", CONTROL, b"\x1e"),
        ("underscore", "", CONTROL, b"\x1f"),
        ("slash", "", CONTROL, b"\x1f"),
        ("7", "", CONTROL, b"\x1f"),
        ("Escape", "\x1b", 0, b"\x1b"),
    ],
)
def test_keys_are_translated_to_what_a_terminal_sends(term, keysym, char, state, expected):
    assert press(term, keysym, char, state) == expected


def test_arrow_keys_use_application_mode_when_requested(term):
    term._process("\x1b[?1h")

    assert press(term, "Up") == b"\x1bOA"
    assert press(term, "Left") == b"\x1bOD"
    assert press(term, "Up", state=CONTROL) == b"\x1b[1;5A"


def test_alt_sends_escape_before_the_key(term):
    assert term._meta_key(key_event("x", "x", ALT)) == "break"
    assert term.written == [b"\x1bx"]
    term.written.clear()

    for keysym, expected in [
        ("Left", b"\x1bb"),
        ("Right", b"\x1bf"),
        ("Up", b"\x1b\x1b[A"),
        ("Down", b"\x1b\x1b[B"),
        ("F5", b""),
    ]:
        term._meta_key(key_event(keysym, "", ALT))
        assert b"".join(term.written) == expected
        term.written.clear()

    term._process("\x1b[?1h")
    term._meta_key(key_event("Up", "", ALT))
    assert term.written == [b"\x1b\x1bOA"]


def test_meta_keys_are_ignored_after_the_process_ended(term):
    term.running = False

    assert term._meta_key(key_event("x", "x", ALT)) == "break"
    assert term.written == []


def test_modify_other_keys_level_two_reports_modified_named_keys(term):
    term._process("\x1b[>4;2m")

    assert press(term, "Return", "\r", CONTROL) == b"\x1b[27;5;13~"
    assert press(term, "BackSpace", "\x08", SHIFT) == b"\x1b[27;2;127~"
    assert press(term, "Escape", "\x1b", CONTROL) == b"\x1b[27;5;27~"
    assert press(term, "Tab", "\t", CONTROL) == b"\x1b[27;5;9~"
    assert press(term, "Tab", "\t", SHIFT) == b"\x1b[Z"
    assert press(term, "Return", "\r") == b"\r"


def test_modify_other_keys_reports_modified_printable_keys(term):
    term._process("\x1b[>4;2m")
    assert press(term, "period", ".", CONTROL, keysym_num=ord(".")) == b"\x1b[27;5;46~"

    term._process("\x1b[>4;1m")
    assert press(term, "a", "a", CONTROL, keysym_num=ord("a")) == b"a"
    assert press(term, "period", "", CONTROL, keysym_num=ord(".")) == b"\x1b[27;5;46~"


def test_typing_clears_the_selection_but_modifier_keys_do_not(term):
    term._process("hello")
    term.tag_add("sel", "1.0", "1.3")

    press(term, "Shift_L")
    assert term.tag_ranges("sel")

    press(term, "a", "a")
    assert not term.tag_ranges("sel")


def test_navigation_keys_clear_the_selection(term):
    term._process("hello")
    term.tag_add("sel", "1.0", "1.3")

    press(term, "Up")

    assert not term.tag_ranges("sel")


def test_alt_keys_clear_the_selection(term):
    term._process("hello")
    term.tag_add("sel", "1.0", "1.3")

    term._meta_key(key_event("x", "x", ALT))

    assert not term.tag_ranges("sel")


def test_keys_restart_the_cursor_blink_phase(term):
    term._cursor_blink_visible = False

    press(term, "a", "a")

    assert term._cursor_blink_visible is True


def test_keys_close_an_open_context_menu(term):
    posted = []
    term._termmenu = SimpleNamespace(unpost=lambda: posted.append(True))
    term._menu_posted = True

    press(term, "a", "a")

    assert posted == [True]
    assert term._menu_posted is False


def test_the_context_menu_closes_on_unrelated_keys_only(term):
    posted = []
    term._termmenu = SimpleNamespace(unpost=lambda: posted.append(True))
    term._menu_posted = True

    assert term._termmenu_keyclose(key_event("Down")) is None
    assert posted == []

    assert term._termmenu_keyclose(key_event("a", "a")) == "break"
    assert posted == [True]


def test_key_write_errors_are_swallowed(term):
    def fail(data):
        raise OSError

    term._write = fail

    assert term._key(key_event("a", "a")) == "break"
    assert term._meta_key(key_event("x", "x", ALT)) == "break"


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------


def test_focus_events_are_reported_only_when_requested(term):
    term._focus_in(None)
    assert term._cursor_has_focus is True
    assert term.written == []

    term._process("\x1b[?1004h")
    term._focus_out(None)
    assert term._cursor_has_focus is False
    term._focus_in(None)

    assert term.written == [b"\x1b[O", b"\x1b[I"]


def test_focus_events_are_not_reported_after_the_process_ended(term):
    term._process("\x1b[?1004h")
    term.running = False

    term._focus_in(None)
    term._focus_out(None)

    assert term.written == []


# ---------------------------------------------------------------------------
# Selection and clipboard
# ---------------------------------------------------------------------------


def test_select_all_selects_the_whole_buffer(term):
    term._process("hi")

    assert term._select_all() == "break"

    assert term.tag_ranges("sel")
    assert str(term.tag_ranges("sel")[0]) == "1.0"


def test_copy_selection_puts_the_selected_text_on_the_clipboard(term):
    term._process("copy me")
    term.clipboard_clear()
    term.clipboard_append("before")

    assert term._copy_selection() == "break"
    assert term.clipboard_get() == "before"

    term.tag_add("sel", "1.0", "1.7")
    term._copy_selection()
    assert term.clipboard_get() == "copy me"


def test_copy_selection_joins_wrapped_lines_without_a_newline(small):
    small._process("abcdefgh\r\nxy")
    small.tag_add("sel", "1.0", "3.2")

    small._copy_selection()

    assert small.clipboard_get() == "abcdefgh\nxy"


def test_copy_selection_of_part_of_one_line(term):
    term._process("hello world")
    term.tag_add("sel", "1.6", "1.11")

    term._copy_selection()

    assert term.clipboard_get() == "world"


def test_clearing_the_selection(term):
    term._process("hello")
    term.tag_add("sel", "1.0", "1.3")

    term._clear_selection()

    assert not term.tag_ranges("sel")


def test_paste_sends_clipboard_text_with_carriage_returns(term):
    term.clipboard_clear()
    term.clipboard_append("one\r\ntwo\nthree")
    term._process("hello")
    term.tag_add("sel", "1.0", "1.2")

    assert term._paste_clipboard() == "break"

    assert term.written == [b"one\rtwo\rthree"]
    assert not term.tag_ranges("sel")


def test_paste_is_wrapped_when_bracketed_paste_is_on(term):
    term._process("\x1b[?2004h")
    term.clipboard_clear()
    term.clipboard_append("text")

    term._paste_clipboard()

    assert term.written == [b"\x1b[200~text\x1b[201~"]


def test_paste_does_nothing_without_text_or_a_running_process(term):
    term.clipboard_clear()
    term._paste_clipboard()
    term.clipboard_append("text")
    term.running = False
    term._paste_clipboard()

    assert term.written == []


def test_copy_paste_and_select_all_key_bindings_exist(term):
    for sequence in ("<Control-Shift-C>", "<Control-Shift-V>", "<Control-Shift-A>"):
        assert term.bind(sequence)


class FakeMenu:
    def __init__(self):
        self.popups = []
        self.released = 0
        self.unposted = 0

    def tk_popup(self, x, y):
        self.popups.append((x, y))

    def grab_release(self):
        self.released += 1

    def unpost(self):
        self.unposted += 1


def test_right_click_opens_the_context_menu(term):
    menu = FakeMenu()
    term._termmenu = menu

    result = term._term_button3_release(mouse_event(x_root=30, y_root=40))

    assert result == "break"
    assert menu.popups == [(30, 40)]
    assert menu.released == 1
    assert term._menu_posted is True


def test_context_menu_has_copy_paste_and_select_all(tk_root):
    terminal = Terminal(tk_root)
    try:
        menu = terminal._termmenu
        labels = [
            menu.entrycget(index, "label")
            for index in range(menu.index("end") + 1)
            if menu.type(index) == "command"
        ]

        assert labels == ["Copy", "Paste", "Select All"]
    finally:
        shutdown(terminal)


def test_pressing_a_button_closes_the_context_menu(term):
    menu = FakeMenu()
    term._termmenu = menu
    term._menu_posted = True

    term._term_button1_press(mouse_event())

    assert menu.unposted == 1


# ---------------------------------------------------------------------------
# Mouse reporting
# ---------------------------------------------------------------------------


def cell_event(terminal, column, row, **kwargs):
    """A mouse event inside the given 0-based cell."""
    return mouse_event(
        x=column * terminal.charwidth + terminal.charwidth // 2 + 3,
        y=row * terminal.charheight + terminal.charheight // 2 + 3,
        **kwargs,
    )


@pytest.fixture
def mouse_term(term):
    """A terminal whose rows are all full, so every pixel maps to a cell."""
    term._process("\r\n".join(["x" * 20] * 5))
    return term


def test_mouse_position_past_the_end_of_the_text_is_still_a_cell(tk_root):
    terminal = make_terminal(tk_root)
    try:
        terminal._process("ab")

        assert terminal._term_mouse_pos(cell_event(terminal, 10, 0)) == (11, 1)
    finally:
        shutdown(terminal)


def test_mouse_position_past_the_end_of_the_text_on_the_alternate_screen(tk_root):
    terminal = make_terminal(tk_root)
    try:
        terminal._process("\x1b[?1049hab")

        assert terminal._term_mouse_pos(cell_event(terminal, 10, 0)) == (11, 1)
    finally:
        shutdown(terminal)


def test_mouse_position_maps_pixels_to_one_based_cells(mouse_term):
    assert mouse_term._term_mouse_pos(cell_event(mouse_term, 0, 0)) == (1, 1)
    assert mouse_term._term_mouse_pos(cell_event(mouse_term, 2, 1)) == (3, 2)
    assert mouse_term._term_mouse_pos(mouse_event(x=9999, y=9999)) == (20, 5)


def test_mouse_clicks_are_reported_in_sgr_encoding(mouse_term):
    mouse_term._process("\x1b[?1000h\x1b[?1006h")
    event = cell_event(mouse_term, 2, 1)

    assert mouse_term._term_button1_press(event) == "break"
    assert mouse_term._term_button1_release(event) == "break"
    assert mouse_term._term_button3_press(event) == "break"

    assert mouse_term.written == [
        b"\x1b[<0;3;2M",
        b"\x1b[<0;3;2m",
        b"\x1b[<2;3;2M",
    ]


def test_mouse_clicks_are_reported_in_legacy_encoding(mouse_term):
    mouse_term._process("\x1b[?1000h")
    event = cell_event(mouse_term, 2, 1)

    mouse_term._term_button1_press(event)
    mouse_term._term_button1_release(event)
    mouse_term._term_button2_press(event)

    assert mouse_term.written == [
        bytes([27, ord("["), ord("M"), 32, 35, 34]),
        bytes([27, ord("["), ord("M"), 35, 35, 34]),
        bytes([27, ord("["), ord("M"), 33, 35, 34]),
    ]


def test_mouse_reports_include_modifier_bits(mouse_term):
    mouse_term._process("\x1b[?1000h\x1b[?1006h")

    mouse_term._term_button1_press(cell_event(mouse_term, 0, 0, state=CONTROL))
    mouse_term._term_button1_press(cell_event(mouse_term, 0, 0, state=ALT))

    assert mouse_term.written == [b"\x1b[<16;1;1M", b"\x1b[<8;1;1M"]


def test_shift_click_bypasses_mouse_reporting(monkeypatch, mouse_term):
    mouse_term._process("\x1b[?1000h\x1b[?1006h")
    redirected = []
    monkeypatch.setattr(
        mouse_term, "event_generate", lambda pattern, **kwargs: redirected.append((pattern, kwargs))
    )

    result = mouse_term._term_button1_press(cell_event(mouse_term, 1, 1, state=SHIFT))

    assert result == "break"
    assert mouse_term.written == []
    assert redirected and redirected[0][0] == "<Button-1>"


def test_bypassed_events_are_not_reported_while_redirecting(mouse_term):
    mouse_term._process("\x1b[?1000h")
    mouse_term._mouse_bypass = True

    assert mouse_term._term_send_mouse(0, cell_event(mouse_term, 0, 0)) is False


def test_mouse_events_without_reporting_fall_through_to_normal_handling(mouse_term):
    event = cell_event(mouse_term, 1, 1)

    assert mouse_term._term_button1_press(event) is None
    assert mouse_term._term_button1_release(event) is None
    assert mouse_term._term_button1_motion(event) is None
    assert mouse_term._term_button3_press(event) is None
    assert mouse_term._term_button3_motion(event) is None
    assert mouse_term._term_button2_motion(event) is None
    assert mouse_term._term_button2_release(event) is None
    assert mouse_term.written == []


def test_dragging_is_reported_only_in_drag_or_motion_modes(mouse_term):
    mouse_term._process("\x1b[?1000h\x1b[?1006h")
    assert mouse_term._term_button1_motion(cell_event(mouse_term, 1, 1)) is None
    assert mouse_term.written == []

    mouse_term._process("\x1b[?1002h")
    mouse_term._term_button1_motion(cell_event(mouse_term, 1, 1))
    mouse_term._term_button1_motion(cell_event(mouse_term, 1, 1))
    mouse_term._term_button1_motion(cell_event(mouse_term, 2, 1))
    mouse_term._term_button2_motion(cell_event(mouse_term, 3, 1))
    mouse_term._term_button3_motion(cell_event(mouse_term, 4, 1))

    assert mouse_term.written == [
        b"\x1b[<32;2;2M",
        b"\x1b[<32;3;2M",
        b"\x1b[<33;4;2M",
        b"\x1b[<34;5;2M",
    ]


def test_hovering_is_reported_only_in_any_motion_mode(mouse_term):
    mouse_term._process("\x1b[?1002h\x1b[?1006h")
    mouse_term._term_motion(cell_event(mouse_term, 0, 0))
    assert mouse_term.written == []

    mouse_term._process("\x1b[?1003h")
    mouse_term._term_motion(cell_event(mouse_term, 0, 0))
    mouse_term._term_motion(cell_event(mouse_term, 0, 0, state=0x100))

    assert mouse_term.written == [b"\x1b[<35;1;1M"]


def test_mouse_reports_stop_when_the_process_has_ended(mouse_term):
    mouse_term._process("\x1b[?1000h\x1b[?1006h")
    mouse_term.running = False

    assert mouse_term._term_send_mouse(0, cell_event(mouse_term, 0, 0)) is False
    assert mouse_term.written == []


def test_middle_click_pastes_when_not_reporting(mouse_term):
    mouse_term.clipboard_clear()
    mouse_term.clipboard_append("pasted")

    assert mouse_term._term_button2_press(cell_event(mouse_term, 0, 0)) == "break"

    assert mouse_term.written == [b"pasted"]


def test_mouse_release_keeps_the_tracked_cursor_where_the_program_left_it(
    tk_root, mouse_term
):
    mouse_term._process("\x1b[2;4Hhello")
    expected = cursor(mouse_term)

    mouse_term.mark_set("custom", "1.0")
    mouse_term._term_button1_release(cell_event(mouse_term, 0, 0))
    tk_root.update()

    assert cursor(mouse_term) == expected == (1, 8)


@pytest.mark.parametrize(
    ("handler", "pattern"),
    [
        ("_term_button1_press", "<Button-1>"),
        ("_term_button1_release", "<ButtonRelease-1>"),
        ("_term_button1_motion", "<B1-Motion>"),
        ("_term_button2_press", "<Button-2>"),
        ("_term_button2_motion", "<B2-Motion>"),
        ("_term_button2_release", "<ButtonRelease-2>"),
        ("_term_button3_press", "<Button-3>"),
        ("_term_button3_release", "<ButtonRelease-3>"),
        ("_term_button3_motion", "<B3-Motion>"),
    ],
)
def test_shift_bypasses_mouse_reporting_for_every_button_event(
    monkeypatch, mouse_term, handler, pattern
):
    mouse_term._process("\x1b[?1002h\x1b[?1006h")
    redirected = []
    monkeypatch.setattr(
        mouse_term,
        "event_generate",
        lambda sequence, **kwargs: redirected.append(sequence),
    )

    result = getattr(mouse_term, handler)(cell_event(mouse_term, 1, 1, state=SHIFT))

    assert result == "break"
    assert redirected == [pattern]
    assert mouse_term.written == []


@pytest.mark.parametrize(
    ("handler", "code", "drag"),
    [
        ("_term_button1_motion", 0, True),
        ("_term_button2_press", 1, False),
        ("_term_button2_motion", 1, True),
        ("_term_button2_release", 1, False),
        ("_term_button3_press", 2, False),
        ("_term_button3_motion", 2, True),
        ("_term_button3_release", 2, False),
    ],
)
def test_every_button_event_is_reported_with_its_own_button_code(
    mouse_term, handler, code, drag
):
    mouse_term._process("\x1b[?1002h\x1b[?1006h")

    assert getattr(mouse_term, handler)(cell_event(mouse_term, 0, 0)) == "break"

    released = handler.endswith("release")
    expected = code + (32 if drag else 0)
    suffix = "m" if released else "M"
    assert mouse_term.written == [f"\x1b[<{expected};1;1{suffix}".encode()]


def test_a_write_error_while_reporting_the_mouse_is_ignored(mouse_term):
    mouse_term._process("\x1b[?1000h\x1b[?1006h")

    def fail(data):
        raise OSError

    mouse_term._write = fail

    assert mouse_term._term_send_mouse(0, cell_event(mouse_term, 0, 0)) is True


# ---------------------------------------------------------------------------
# Wheel
# ---------------------------------------------------------------------------


def test_wheel_is_reported_when_the_program_wants_mouse_events(mouse_term):
    mouse_term._process("\x1b[?1000h\x1b[?1006h")

    assert mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=4)) == "break"
    mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=5))
    mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=0, delta=120))
    mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=0, delta=-120))

    assert mouse_term.written == [
        b"\x1b[<64;1;1M",
        b"\x1b[<65;1;1M",
        b"\x1b[<64;1;1M",
        b"\x1b[<65;1;1M",
    ]


def test_wheel_in_the_alternate_screen_sends_arrow_keys(mouse_term):
    mouse_term._process("\x1b[?1049h")

    mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=4))
    mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=5))
    assert mouse_term.written == [b"\x1b[A"] * 3 + [b"\x1b[B"] * 3

    mouse_term.written.clear()
    mouse_term._process("\x1b[?1h")
    mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=4))
    mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=5))
    assert mouse_term.written == [b"\x1bOA"] * 3 + [b"\x1bOB"] * 3


def test_wheel_in_the_alternate_screen_scrolls_when_alternate_scroll_is_off(mouse_term):
    mouse_term._process("\x1b[?1049h\x1b[?1007l")

    result = mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=4))

    assert mouse_term.written == []
    assert result is None


def test_shift_wheel_does_not_send_arrow_keys(mouse_term):
    mouse_term._process("\x1b[?1049h")

    mouse_term._term_wheel(cell_event(mouse_term, 0, 0, num=4, state=SHIFT))

    assert mouse_term.written == []


def test_wheel_updates_whether_the_view_follows_new_output(tk_root, small):
    small._process("\r\n".join(str(n) for n in range(30)))
    tk_root.update()
    small.yview_moveto(0)
    small._term_wheel(cell_event(small, 0, 0, num=4))
    tk_root.update()
    assert small._follow_bottom is False

    small.yview_moveto(1)
    small._term_wheel(cell_event(small, 0, 0, num=5))
    tk_root.update()
    assert small._follow_bottom is True


def test_following_the_view_keeps_the_screen_visible(tk_root, small):
    small._process("\r\n".join(str(n) for n in range(30)))
    small.yview_moveto(0)

    small._term_follow_view()
    tk_root.update()

    assert small.bbox(f"{small.screen_top}.0") is not None
    assert small.bbox(f"{small.screen_top + 2}.0") is not None


def test_following_the_alternate_screen_shows_the_cursor(tk_root, small):
    small._process("\x1b[?1049h\x1b[3;1Hx")

    small._term_follow_view()
    tk_root.update()

    assert small.bbox("insert") is not None


def test_the_modified_flag_is_cleared_whenever_it_is_set(tk_root, mouse_term):
    mouse_term.insert("end", "x")
    tk_root.update()

    assert not mouse_term.edit_modified()


# ---------------------------------------------------------------------------
# Real PTY
# ---------------------------------------------------------------------------


def wait_for(root, condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return True
        time.sleep(0.02)
    return False


def real_terminal(root, command, cols=40, rows=6, **kwargs):
    terminal = make_terminal(root, cols=cols, rows=rows, command=command, **kwargs)
    del terminal._write
    terminal._term_started = False
    terminal._term_start_process()
    terminal._term_started = True
    return terminal


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX PTY")
def test_real_shell_output_input_and_exit(tk_root):
    terminal = real_terminal(
        tk_root, ["sh", "-c", "printf 'ready\\n'; read line; echo got:$line"]
    )
    stopped = events(terminal, "<<TerminalStopped>>")
    try:
        assert wait_for(tk_root, lambda: "ready" in "".join(screen(terminal)))

        terminal._write(b"abc\n")
        assert wait_for(tk_root, lambda: "got:abc" in "".join(screen(terminal)))
        assert wait_for(tk_root, lambda: bool(stopped))
        assert terminal.running is False
    finally:
        shutdown(terminal)


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX PTY")
def test_real_shell_gets_a_controlling_terminal_of_the_right_size(tk_root):
    terminal = real_terminal(
        tk_root,
        ["sh", "-c", "tty >/dev/null && stty size && echo done"],
        cols=33,
        rows=6,
    )
    try:
        assert wait_for(tk_root, lambda: "done" in "".join(screen(terminal)))
        lines = [line for line in screen(terminal) if line]
        assert lines[0] == "6 33"
    finally:
        shutdown(terminal)


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX PTY")
def test_real_shell_resize_reaches_the_program(tk_root):
    terminal = real_terminal(
        tk_root,
        ["sh", "-c", "trap 'stty size' WINCH; stty size; while :; do sleep 0.05; done"],
        cols=40,
        rows=5,
    )
    try:
        assert wait_for(tk_root, lambda: "5 40" in "".join(screen(terminal)))

        terminal._term_resize_grid(50, 7)

        assert wait_for(tk_root, lambda: "7 50" in "".join(screen(terminal)))
    finally:
        shutdown(terminal)


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX PTY")
def test_real_shell_colors_end_up_in_tags(tk_root):
    terminal = real_terminal(tk_root, ["sh", "-c", "printf '\\033[31mred\\033[0m ok\\n'"])
    try:
        assert wait_for(tk_root, lambda: "red ok" in "".join(screen(terminal)))
        assert style_at(terminal, 0, 0)["foreground"] == "#cd0000"
        assert tags_at(terminal, 0, 4) == []
    finally:
        shutdown(terminal)


# ---------------------------------------------------------------------------
# Errors that must not escape
# ---------------------------------------------------------------------------


def test_polling_survives_an_error_in_its_own_bookkeeping(term):
    def fail():
        raise tk.TclError("gone")

    term.update = fail

    term._poll()

    assert term._poll_after_id is not None
    assert term._polling is False


def test_polling_stops_quietly_once_the_widget_is_destroyed(tk_root):
    terminal = make_terminal(tk_root)
    shutdown(terminal)

    terminal._poll()

    assert terminal._polling is False


def test_clearing_the_selection_of_a_destroyed_widget_is_ignored(tk_root):
    terminal = make_terminal(tk_root)
    shutdown(terminal)

    terminal._clear_selection()


def test_copying_without_a_selection_does_nothing(term):
    term._process("text")
    term.clipboard_clear()
    term.clipboard_append("keep")

    assert term._copy_selection() == "break"

    assert term.clipboard_get() == "keep"


def test_pasting_with_an_unavailable_clipboard_does_nothing(term):
    term.clipboard_clear()

    assert term._paste_clipboard() == "break"

    assert term.written == []


def test_write_errors_while_pasting_and_focusing_are_ignored(term):
    def fail(data):
        raise OSError

    term.clipboard_clear()
    term.clipboard_append("text")
    term._process("\x1b[?1004h")
    term._write = fail

    assert term._paste_clipboard() == "break"
    term._focus_in(None)
    term._focus_out(None)


def test_answers_to_queries_survive_a_write_error(term):
    def fail(data):
        raise OSError

    term._write = fail

    term._process("\x1b[c\x1b[>c\x1b[5n\x1b[6n\x1b[?4m\x1b]10;?\x07")


def test_unposting_a_menu_that_is_gone_is_ignored(term):
    def fail():
        raise tk.TclError("no menu")

    term._termmenu = SimpleNamespace(unpost=fail)
    term._menu_posted = True

    term._unpost_menu()

    assert term._menu_posted is False


def test_snapping_the_caret_after_destruction_is_ignored(tk_root):
    terminal = make_terminal(tk_root)
    shutdown(terminal)

    terminal._snap_caret()


def test_following_the_view_of_a_destroyed_widget_is_ignored(tk_root):
    terminal = make_terminal(tk_root)
    shutdown(terminal)

    terminal._term_update_follow()


def test_theme_change_of_a_destroyed_widget_is_ignored(tk_root):
    terminal = make_terminal(tk_root)
    shutdown(terminal)

    terminal._term_on_theme_changed()


def test_theme_change_with_unchanged_colors_leaves_them_alone(term):
    term._term_on_theme_changed()

    assert term._term_default_bg == "#000000"
    assert term._term_default_fg == "#ffffff"

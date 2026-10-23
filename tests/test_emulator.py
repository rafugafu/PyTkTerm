"""Escape-sequence parsing and screen behavior of the terminal emulator.

Output is fed straight into Terminal._process(), so no subprocess, PTY or
event loop is involved: every test checks what ends up in the text buffer
and its tags, in the cursor/mode state, or in the bytes the terminal answers
with. Tests that make sense on both screens run once on the primary screen
and once on the alternate screen.
"""

import base64

import pytest

from terminal_helpers import (
    cursor,
    make_terminal,
    screen,
    shutdown,
    style_at,
    tags_at,
)


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


@pytest.fixture(params=["primary", "alternate"])
def both(request, tk_root):
    """A terminal currently showing the primary or the alternate screen."""
    terminal = make_terminal(tk_root)
    if request.param == "alternate":
        terminal._process("\x1b[?1049h")
    yield terminal
    shutdown(terminal)


@pytest.fixture(params=["primary", "alternate"])
def both_small(request, tk_root):
    terminal = make_terminal(tk_root, cols=5, rows=3)
    if request.param == "alternate":
        terminal._process("\x1b[?1049h")
    yield terminal
    shutdown(terminal)


# ---------------------------------------------------------------------------
# Printing, control characters and wrapping
# ---------------------------------------------------------------------------


def test_plain_text_is_written_at_the_cursor(both):
    both._process("hello")

    assert screen(both)[0] == "hello"
    assert cursor(both) == (0, 5)


def test_carriage_return_and_newline_move_the_cursor(both):
    both._process("ab\r\ncd\rX")

    assert screen(both)[:2] == ["ab", "Xd"]


def test_backspace_moves_left_without_erasing(both):
    both._process("abc\x08\x08X")

    assert screen(both)[0] == "aXc"


def test_backspace_at_the_first_column_stays_put(both):
    both._process("\x08\x08a")

    assert screen(both)[0] == "a"


def test_tab_moves_to_next_default_stop(both):
    both._process("a\tb")

    assert screen(both)[0] == "a" + " " * 7 + "b"


def test_tab_past_last_stop_stays_in_last_column(both):
    both._process("\t\t\t\tx")

    assert screen(both)[0] == " " * 19 + "x"


def test_text_wraps_and_marks_the_line_as_wrapped(small):
    small._process("abcdefg")

    assert screen(small) == ["abcde", "fg", ""]
    assert "wrapcont" in small.tag_names("1.end")
    assert "wrapcont" not in small.tag_names("2.end")


def test_newline_clears_the_wrapped_mark(small):
    small._process("abcdefg\x1b[1;1H\n")

    assert "wrapcont" not in small.tag_names("1.end")


def test_autowrap_off_overwrites_the_last_column(both_small):
    both_small._process("\x1b[?7labcdefg")

    assert screen(both_small)[0] == "abcdg"
    assert both_small._autowrap is False
    both_small._process("\x1b[?7h")
    assert both_small._autowrap is True


def test_scrolling_pushes_lines_into_scrollback(small):
    small._process("1\r\n2\r\n3\r\n4")

    assert screen(small) == ["2", "3", "4"]
    assert small.screen_top == 2
    assert small.get("1.0", "1.end") == "1"


def test_scrolling_on_the_alternate_screen_never_creates_scrollback(small):
    small._process("\x1b[?1049h1\r\n2\r\n3\r\n4")

    assert screen(small) == ["2", "3", "4"]
    assert int(small.index("end - 1 char").split(".")[0]) == 3


def test_form_feed_and_vertical_tab_act_as_newline(both_small):
    both_small._process("a\x0bb\x0cc")

    assert screen(both_small) == ["a", " b", "  c"]


def test_other_control_characters_are_ignored(both):
    both._process("a\x00\x01\x07b\x7f")

    assert screen(both)[0] == "ab"


def test_wide_characters_take_two_columns(both):
    both._process("日本")

    assert screen(both)[0] == "日本"
    assert cursor(both) == (0, 4)


def test_combining_marks_add_no_column(both):
    both._process("éx")

    assert screen(both)[0] == "éx"
    assert cursor(both) == (0, 2)


def test_characters_outside_the_basic_plane_count_as_wide(both):
    both._process("a\U0001F600b")

    assert screen(both)[0] == "a\U0001F600b"
    assert cursor(both) == (0, 4)

    both._process("\x1b[1;4H")
    assert cursor(both) == (0, 3)


def test_wide_character_that_does_not_fit_wraps(small):
    small._process("abcd日")

    assert screen(small) == ["abcd", "日", ""]


def test_overwriting_half_of_a_wide_character_blanks_the_other_half(both):
    both._process("日\x1b[1;2Hx")

    assert screen(both)[0] == " x"


def test_text_after_wide_characters_is_addressed_by_column(both):
    both._process("日本語\x1b[1;5Hx")

    assert screen(both)[0] == "日本x"


# ---------------------------------------------------------------------------
# Cursor movement and addressing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ("\x1b[3;4H", (2, 3)),
        ("\x1b[3;4f", (2, 3)),
        ("\x1b[H", (0, 0)),
        ("\x1b[3;4H\x1b[2A", (0, 3)),
        ("\x1b[3;4H\x1b[A", (1, 3)),
        ("\x1b[2;4H\x1b[2B", (3, 3)),
        ("\x1b[9B", (4, 0)),
        ("\x1b[3C", (0, 3)),
        ("\x1b[99C", (0, 19)),
        ("\x1b[1;6H\x1b[2D", (0, 3)),
        ("\x1b[1;2H\x1b[9D", (0, 0)),
        ("\x1b[2;4H\x1b[2E", (3, 0)),
        ("\x1b[4;4H\x1b[2F", (1, 0)),
        ("\x1b[7G", (0, 6)),
        ("\x1b[4d", (3, 0)),
    ],
)
def test_cursor_addressing_sequences(both, sequence, expected):
    both._process(sequence)

    assert cursor(both) == expected


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [("\x1b[99;99H", (4, 19)), ("\x1b[99G", (0, 19))],
)
def test_cursor_addressing_is_clamped_to_the_last_column(both, sequence, expected):
    both._process(sequence)

    assert cursor(both) == expected
    assert len(screen(both)[expected[0]]) <= 20


def test_cursor_up_from_below_the_scroll_region_stops_at_its_top(both):
    both._process("\x1b[2;3r\x1b[5;1H\x1b[9A")

    assert cursor(both) == (1, 0)


def test_cursor_down_stops_at_the_bottom_of_the_scroll_region(both):
    both._process("\x1b[2;4r\x1b[3;1H\x1b[9B")

    assert cursor(both) == (3, 0)


def test_cursor_above_the_scroll_region_moves_freely(both):
    both._process("\x1b[3;4r\x1b[2;1H\x1b[9A")
    assert cursor(both) == (0, 0)

    both._process("\x1b[2;1H\x1b[9B")
    assert cursor(both) == (4, 0)


def test_origin_mode_addresses_rows_relative_to_the_scroll_region(both):
    both._process("\x1b[2;4r")
    assert cursor(both) == (0, 0)

    both._process("\x1b[?6h")
    assert cursor(both) == (1, 0)
    both._process("\x1b[1;1H")
    assert cursor(both) == (1, 0)
    both._process("\x1b[9;1H")
    assert cursor(both) == (3, 0)

    both._process("\x1b[?6l")
    assert cursor(both) == (0, 0)


def test_save_and_restore_cursor_with_escape_and_csi(both):
    both._process("\x1b[2;3H\x1b7\x1b[1;1H\x1b8")
    assert cursor(both) == (1, 2)

    both._process("\x1b[4;5H\x1b[s\x1b[1;1H\x1b[u")
    assert cursor(both) == (3, 4)


def test_restore_cursor_also_restores_the_text_style(both):
    both._process("\x1b[31m\x1b7\x1b[0m\x1b8")

    assert both._sgr_state["fg"] == 1


def test_restore_cursor_without_a_save_is_a_no_op(both):
    both._process("\x1b[2;3H\x1b8\x1b[u")

    assert cursor(both) == (1, 2)


def test_restore_cursor_ignores_a_save_made_on_the_other_screen(term):
    term._process("\x1b[2;3H\x1b7\x1b[?1049h\x1b[1;1H\x1b8")

    assert cursor(term) == (0, 0)


def test_index_reverse_index_and_next_line(both_small):
    both_small._process("a\r\nb\r\nc")

    both_small._process("\x1bD")
    assert screen(both_small) == ["b", "c", ""]

    both_small._process("\x1b[1;1H\x1bM")
    assert screen(both_small) == ["", "b", "c"]

    both_small._process("\x1b[2;4H\x1bE")
    assert cursor(both_small) == (2, 0)


def test_reverse_index_moves_up_when_not_at_the_region_top(both_small):
    both_small._process("\x1b[3;3H\x1bM")

    assert cursor(both_small) == (1, 2)


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


def test_set_clear_and_reset_tab_stops(both):
    both._process("\x1b[1;4H\x1bH\r\t")
    assert cursor(both) == (0, 3)

    both._process("\x1b[g\r\t")
    assert cursor(both) == (0, 8)

    both._process("\x1b[3g\r\t")
    assert cursor(both) == (0, 19)


def test_tab_stops_are_reset_by_a_full_reset(both):
    both._process("\x1b[3g\x1bc\r\t")

    assert cursor(both) == (0, 8)


# ---------------------------------------------------------------------------
# Erasing, inserting and deleting
# ---------------------------------------------------------------------------


def test_erase_in_line_modes(both):
    both._process("abcdef\x1b[1;4H\x1b[K")
    assert screen(both)[0] == "abc"

    both._process("\x1b[2K")
    assert screen(both)[0] == ""


def test_erase_in_line_to_the_start_includes_the_cursor_cell(both):
    both._process("abcdef\x1b[1;4H\x1b[1K")

    assert screen(both)[0] == "    ef"


def test_erase_in_display_below_and_above(both):
    both._process("aa\r\nbb\r\ncc\r\ndd\r\nee\x1b[3;2H")

    both._process("\x1b[J")
    assert screen(both) == ["aa", "bb", "c", "", ""]

    both._process("\x1b[1J")
    assert screen(both) == ["", "", "", "", ""]


def test_erase_in_display_one_clears_up_to_the_cursor(both):
    both._process("aa\r\nbb\r\ncc\x1b[2;2H\x1b[1J")

    assert screen(both)[:3] == ["", "", "cc"]


def test_erase_in_display_two_clears_the_screen(both):
    both._process("a\r\nb\x1b[2J")

    assert screen(both) == [""] * 5


def test_erase_in_display_two_keeps_the_old_screen_in_scrollback(small):
    small._process("a\r\nb\x1b[2J")

    assert screen(small) == ["", "", ""]
    scrollback = [
        small.get(f"{line}.0", f"{line}.end").strip()
        for line in range(1, small.screen_top)
    ]
    assert "a" in scrollback
    assert "b" in scrollback


def test_erase_in_display_three_drops_the_scrollback(small):
    small._process("1\r\n2\r\n3\r\n4")
    assert small.screen_top == 2

    small._process("\x1b[3J")

    assert small.screen_top == 1
    assert screen(small) == ["2", "3", "4"]


def test_erased_cells_take_the_current_background(both):
    both._process("\x1b[42m\x1b[2K")

    assert style_at(both, 0, 0).get("background") == "#00cd00"
    assert style_at(both, 0, 19).get("background") == "#00cd00"


def test_insert_and_delete_characters(both):
    both._process("abcdef\x1b[1;3H\x1b[2@")
    assert screen(both)[0] == "ab  cdef"

    both._process("\x1b[2P")
    assert screen(both)[0] == "abcdef"


def test_insert_characters_drops_what_falls_off_the_right_edge(both_small):
    both_small._process("abcde\x1b[1;2H\x1b[2@")

    assert screen(both_small)[0] == "a  bc"


def test_erase_characters(both):
    both._process("abcdef\x1b[1;2H\x1b[3X")

    assert screen(both)[0] == "a   ef"


def test_insert_and_delete_lines(both_small):
    both_small._process("a\r\nb\r\nc\x1b[2;1H")

    both_small._process("\x1b[L")
    assert screen(both_small) == ["a", "", "b"]

    both_small._process("\x1b[M")
    assert screen(both_small) == ["a", "b", ""]


def test_insert_and_delete_lines_outside_the_region_do_nothing(both):
    both._process("a\r\nb\r\nc\r\nd\r\ne\x1b[2;3r\x1b[5;1H\x1b[L\x1b[M")

    assert screen(both) == ["a", "b", "c", "d", "e"]


def test_scroll_up_and_down_commands(both_small):
    both_small._process("a\r\nb\r\nc")

    both_small._process("\x1b[S")
    assert screen(both_small) == ["b", "c", ""]

    both_small._process("\x1b[T")
    assert screen(both_small) == ["", "b", "c"]


def test_scroll_region_scrolls_only_inside_the_region(both):
    both._process("a\r\nb\r\nc\r\nd\r\ne\x1b[2;4r\x1b[4;1H\n")

    assert screen(both) == ["a", "c", "d", "", "e"]


def test_scroll_region_defaults_and_clamps(both):
    both._process("\x1b[r")
    assert (both._scroll_top, both._scroll_bot) == (1, 5)

    both._process("\x1b[3;99r")
    assert (both._scroll_top, both._scroll_bot) == (3, 5)

    both._process("\x1b[4;2r")
    assert (both._scroll_top, both._scroll_bot) == (4, 4)


def test_repeat_previous_character(both):
    both._process("x\x1b[3b")
    assert screen(both)[0] == "xxxx"


def test_repeat_without_a_previous_character_does_nothing(term):
    term._process("\x1b[3b")

    assert screen(term)[0] == ""


def test_screen_alignment_pattern_fills_the_screen(both):
    both._process("hello\x1b#8")

    assert screen(both) == ["E" * 20] * 5
    assert cursor(both) == (0, 0)


def test_designating_a_character_set_is_swallowed(both):
    both._process("\x1b(Bxy\x1b)0z")

    assert screen(both)[0] == "xyz"


def test_full_reset_clears_the_screen_and_modes(both):
    both._process("junk\x1b[?7l\x1b[?1h\x1b[31m\x1bc")

    assert screen(both) == [""] * 5
    assert both._autowrap is True
    assert both._app_cursor is False
    assert both._alt_mode is False
    assert both._sgr_state["fg"] is None
    assert cursor(both) == (0, 0)


def test_unknown_escape_sequences_are_skipped(both):
    both._process("\x1bZab\x1b\x1b[1;2Hc")

    assert screen(both)[0] == "ac"


# ---------------------------------------------------------------------------
# Split and malformed sequences
# ---------------------------------------------------------------------------


def test_escape_sequence_split_between_chunks_is_completed(both):
    both._process("\x1b[3")
    assert both._pending_esc == "\x1b[3"

    both._process(";4H")

    assert cursor(both) == (2, 3)
    assert both._pending_esc == ""


def test_lone_escape_at_the_end_waits_for_more_output(both):
    both._process("a\x1b")
    assert both._pending_esc == "\x1b"

    both._process("[2;2H")

    assert cursor(both) == (1, 1)


def test_character_set_designation_split_between_chunks(both):
    both._process("\x1b(")
    assert both._pending_esc == "\x1b("

    both._process("Bz")

    assert screen(both)[0] == "z"


def test_control_character_inside_a_csi_sequence_is_executed_first(both):
    both._process("\x1b[2\nC")

    assert cursor(both) == (1, 2)


def test_cancel_inside_a_csi_sequence_aborts_it(both):
    both._process("\x1b[1\x18ab")
    assert screen(both)[0] == "ab"

    both._process("\x1b[1\x1aXY")
    assert screen(both)[0] == "abXY"


def test_escape_inside_a_csi_sequence_starts_a_new_one(both):
    both._process("\x1b[1\x1b[2;3H")

    assert cursor(both) == (1, 2)


def test_unrecognizable_csi_sequence_is_dropped(both):
    both._process("\x1b[1ÿz")

    assert screen(both)[0] == "1ÿz"


def test_csi_with_embedded_control_but_no_final_byte_waits(both):
    both._process("\x1b[1\n")

    assert both._pending_esc == "\x1b[1\n"


def test_csi_with_embedded_control_that_never_ends_is_dropped(monkeypatch, both):
    import pytkterm

    monkeypatch.setattr(pytkterm, "_PTY_MAX_PENDING_ESC", 3)

    both._process("\x1b[1\nab")

    assert both._pending_esc == ""


# ---------------------------------------------------------------------------
# SGR through the parser
# ---------------------------------------------------------------------------


def test_sgr_sequence_styles_following_text(both):
    both._process("\x1b[31;1mX\x1b[0mY")

    assert style_at(both, 0, 0)["foreground"] == "#ff0000"
    assert "bold" in style_at(both, 0, 0)["font"] or style_at(both, 0, 0)["font"]
    assert tags_at(both, 0, 1) == []


def test_default_colored_text_gets_no_tag(both):
    both._process("\x1b[39mX")

    assert tags_at(both, 0, 0) == []


def test_truecolor_and_256_color_sgr(both):
    both._process("\x1b[38;2;1;2;3;48;5;21mX")

    style = style_at(both, 0, 0)
    assert style["foreground"] == "#010203"
    assert style["background"] == "#0000ff"


def test_reverse_video_swaps_the_cell_colors(both):
    both._process("\x1b[7mX")

    style = style_at(both, 0, 0)
    assert style["foreground"] == "#000000"
    assert style["background"] == "#ffffff"


def test_style_flags_end_up_in_the_tag(both):
    both._process("\x1b[4;9mX")

    style = style_at(both, 0, 0)
    assert style["underline"] == "1"
    assert style["overstrike"] == "1"


def test_bold_and_italic_use_their_own_fonts(both):
    both._process("\x1b[1mB\x1b[0m\x1b[3mI\x1b[0mP")

    bold = both._term_font_variant(bold=True)
    italic = both._term_font_variant(italic=True)
    assert bold.actual("weight") == "bold"
    assert italic.actual("slant") == "italic"
    assert style_at(both, 0, 0)["font"] == str(bold)
    assert style_at(both, 0, 1)["font"] == str(italic)
    assert tags_at(both, 0, 2) == []


def test_identical_styles_share_one_tag(both):
    both._process("\x1b[31mA\x1b[0mB\x1b[31mC")

    assert tags_at(both, 0, 0) == tags_at(both, 0, 2)


def test_blinking_text_is_tracked_and_toggles(both):
    both._process("\x1b[5;31mX")
    (blink_tag,) = both._blink_tags

    assert both._blink_after_id is not None
    assert both.tag_cget(blink_tag, "foreground") == "#cd0000"

    both._blink_tick()
    assert both.tag_cget(blink_tag, "foreground") == both._term_default_bg

    both._blink_tick()
    assert both.tag_cget(blink_tag, "foreground") == "#cd0000"


def test_blink_stops_once_nothing_blinks_any_more(both):
    both._process("\x1b[5mX")
    both.delete("1.0", "end")

    both._blink_tick()

    assert both._blink_tags == {}
    assert both._blink_after_id is None


def test_nocolor_terminal_ignores_sgr(tk_root):
    terminal = make_terminal(tk_root, nocolor=True)
    try:
        terminal._process("\x1b[31;1mX")

        assert tags_at(terminal, 0, 0) == []
        assert terminal._sgr_state["fg"] is None
    finally:
        shutdown(terminal)


def test_nocolor_terminal_does_not_color_erased_cells(tk_root):
    terminal = make_terminal(tk_root, nocolor=True)
    try:
        terminal._process("\x1b[42m\x1b[2K")

        assert terminal._term_erase_fill_tag() is None
    finally:
        shutdown(terminal)


# ---------------------------------------------------------------------------
# Queries the terminal answers
# ---------------------------------------------------------------------------


def test_device_attributes_replies(term):
    term._process("\x1b[c")
    term._process("\x1b[>c")

    assert term.written == [b"\x1b[?1;2c", b"\x1b[>1;10;0c"]


def test_device_status_reports(both):
    both._process("\x1b[3;5H\x1b[6n\x1b[5n")

    assert both.written == [b"\x1b[3;5R", b"\x1b[0n"]


def test_xterm_modify_other_keys_query_and_set(term):
    term._process("\x1b[>4;2m")
    assert term._modify_other_keys == 2

    term._process("\x1b[?4m\x1b[?1m")
    assert term.written == [b"\x1b[>4;2m", b"\x1b[>1;0m"]

    term._process("\x1b[>4m")
    assert term._modify_other_keys == 0

    term._process("\x1b[>1;5m")
    assert term._modify_other_keys == 0


@pytest.mark.parametrize(
    ("code", "shape", "blinking"),
    [
        (0, "block", True),
        (1, "block", True),
        (2, "block", False),
        (3, "underline", True),
        (4, "underline", False),
        (5, "bar", True),
        (6, "bar", False),
    ],
)
def test_cursor_style_sequences(term, code, shape, blinking):
    term._process(f"\x1b[{code} q")

    assert term._cursor_shape == shape
    assert term._cursor_blink_enabled is blinking


def test_cursor_style_with_an_unknown_code_is_ignored(term):
    term._process("\x1b[5 q\x1b[9 q")

    assert term._cursor_shape == "bar"


# ---------------------------------------------------------------------------
# OSC
# ---------------------------------------------------------------------------


def osc52(text):
    return "\x1b]52;c;" + base64.b64encode(text.encode()).decode() + "\x07"


def test_osc_52_sets_the_clipboard(term):
    term._process(osc52("copied text"))

    assert term.clipboard_get() == "copied text"


def test_osc_52_query_and_bad_data_leave_the_clipboard_alone(term):
    term.clipboard_clear()
    term.clipboard_append("keep")

    term._process("\x1b]52;c;?\x07")
    term._process("\x1b]52;c;\x07")
    term._process("\x1b]52;c;a\x07")
    term._process("\x1b]52;c\x07")

    assert term.clipboard_get() == "keep"


@pytest.mark.parametrize("which", ["10", "11"])
def test_osc_color_queries_reply_with_the_default_colors(term, which):
    term._process(f"\x1b]{which};?\x07")

    (reply,) = term.written
    assert reply.startswith(f"\x1b]{which};rgb:".encode())
    assert reply.endswith(b"\x1b\\")


def test_osc_color_query_reports_the_actual_default_colors(term):
    term._process("\x1b]10;?\x07\x1b]11;?\x07")

    assert term.written == [
        b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\",
        b"\x1b]11;rgb:0000/0000/0000\x1b\\",
    ]


def test_osc_12_sets_queries_and_resets_the_cursor_color(term):
    term._process("\x1b]12;#ff0000\x07")
    assert term._cursor_color == "#ff0000"
    assert term._cursor_color_custom is True

    term._process("\x1b]12;?\x07")
    assert term.written[-1].startswith(b"\x1b]12;rgb:ffff/0000/0000")

    term._process("\x1b]12;rgb:00/ff/00\x07")
    assert term._cursor_color == "#00ff00"

    term._process("\x1b]112\x07")
    assert term._cursor_color_custom is False
    assert term._cursor_color == term._term_default_fg


def test_osc_12_ignores_colors_it_cannot_parse(term):
    term._process("\x1b]12;#00ff00\x07")

    term._process("\x1b]12;rgb:zz/00/00\x07")
    term._process("\x1b]12;rgb:00/00\x07")
    term._process("\x1b]12;not-a-color\x07")

    assert term._cursor_color == "#00ff00"


def test_osc_112_with_arguments_resets_the_cursor_color(term):
    term._process("\x1b]12;#ff0000\x07\x1b]112;\x07")

    assert term._cursor_color_custom is False


def test_nocolor_terminal_ignores_cursor_color_requests(tk_root):
    terminal = make_terminal(tk_root, nocolor=True)
    try:
        before = terminal._cursor_color

        terminal._process("\x1b]12;#ff0000\x07")

        assert terminal._cursor_color == before
    finally:
        shutdown(terminal)


def test_osc_with_either_terminator_is_swallowed(both):
    both._process("\x1b]0;title\x07a\x1b]2;other\x1b\\b")

    assert screen(both)[0] == "ab"


def test_osc_split_between_chunks_is_completed(both):
    both._process("\x1b]0;ti")
    both._process("tle\x07ok")

    assert screen(both)[0] == "ok"


def test_oversized_osc_is_skipped_until_its_terminator(monkeypatch, term):
    import pytkterm

    monkeypatch.setattr(pytkterm, "_PTY_MAX_PENDING_ESC", 10)

    term._process("\x1b]0;" + "x" * 30)
    assert term._osc_skipping is True

    term._process("more\x07after")

    assert term._osc_skipping is False
    assert screen(term)[0] == "after"


def test_oversized_osc_waits_for_a_terminator_split_over_chunks(monkeypatch, term):
    import pytkterm

    monkeypatch.setattr(pytkterm, "_PTY_MAX_PENDING_ESC", 10)
    term._process("\x1b]0;" + "x" * 30)

    term._process("still going\x1b")
    assert term._pending_esc == "\x1b"

    term._process("\\done")

    assert screen(term)[0] == "done"


def test_oversized_osc_keeps_skipping_when_chunks_have_no_terminator(monkeypatch, term):
    import pytkterm

    monkeypatch.setattr(pytkterm, "_PTY_MAX_PENDING_ESC", 10)
    term._process("\x1b]0;" + "x" * 30)

    term._process("no end here")

    assert term._osc_skipping is True
    assert term._pending_esc == ""


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def test_alternate_screen_saves_and_restores_the_main_screen(small):
    small._process("main\x1b[?1049h")

    assert small._alt_mode is True
    assert screen(small) == ["", "", ""]

    small._process("ALT")
    assert screen(small)[0] == "ALT"

    small._process("\x1b[?1049l")
    assert small._alt_mode is False
    assert screen(small)[0] == "main"


def test_alternate_screen_restores_scrollback_and_cursor(small):
    small._process("1\r\n2\r\n3\r\n4\r\n5\x1b[?1049hjunk\x1b[?1049l")

    assert screen(small) == ["3", "4", "5"]
    assert cursor(small) == (2, 1)
    assert small.get("1.0", "1.end") == "1"


@pytest.mark.parametrize("code", [47, 1047, 1049])
def test_every_alternate_screen_mode_number_works(small, code):
    small._process(f"\x1b[?{code}hX")
    assert small._alt_mode is True

    small._process(f"\x1b[?{code}l")
    assert small._alt_mode is False


def test_entering_the_alternate_screen_twice_and_leaving_without_entering(small):
    small._process("\x1b[?1049l")
    assert small._alt_mode is False

    small._process("a\x1b[?1049hb\x1b[?1049h")
    assert screen(small)[0] == "b"

    small._process("\x1b[?1049l")
    assert screen(small)[0] == "a"


def test_alternate_screen_keeps_styled_text_of_the_main_screen(small):
    small._process("\x1b[31mred\x1b[0m\x1b[?1049h\x1b[?1049l")

    assert screen(small)[0] == "red"
    assert style_at(small, 0, 0)["foreground"] == "#cd0000"


def test_alternate_screen_restores_the_text_style(small):
    small._process("\x1b[31m\x1b[?1049h")
    assert small._sgr_state["fg"] is None

    small._process("\x1b[?1049l")
    assert small._sgr_state["fg"] == 1


def test_column_mode_switch_clears_the_screen_and_homes_the_cursor(both_small):
    both_small._process("abc\x1b[2;2H\x1b[?3h")

    assert screen(both_small) == ["", "", ""]
    assert cursor(both_small) == (0, 0)


def test_simple_private_modes_toggle_their_flags(term):
    flags = {
        2004: "_bracketed_paste",
        1004: "_focus_reporting",
        7: "_autowrap",
        1: "_app_cursor",
        1007: "_alternate_scroll",
        1006: "_mouse_sgr",
    }
    for code, attribute in flags.items():
        term._process(f"\x1b[?{code}h")
        on = getattr(term, attribute)
        term._process(f"\x1b[?{code}l")
        assert getattr(term, attribute) is not on


@pytest.mark.parametrize("code", [1000, 1002, 1003])
def test_mouse_reporting_modes(term, code):
    term._process(f"\x1b[?{code}h")
    assert term._mouse_mode == code

    term._process(f"\x1b[?{code}l")
    assert term._mouse_mode == 0


def test_cursor_visibility_and_blink_modes(term):
    term._process("\x1b[?25l")
    assert term._cursor_dectcem_visible is False
    term._process("\x1b[?25h")
    assert term._cursor_dectcem_visible is True

    term._process("\x1b[?12l")
    assert term._cursor_blink_enabled is False
    term._process("\x1b[?12h")
    assert term._cursor_blink_enabled is True


def test_reverse_screen_mode_swaps_the_widget_colors(term):
    term._process("\x1b[?5h")
    assert term.cget("background") == "#ffffff"
    assert term.cget("foreground") == "#000000"

    term._process("\x1b[?5h")
    assert term.cget("background") == "#ffffff"

    term._process("\x1b[?5l")
    assert term.cget("background") == "#000000"
    assert term.cget("foreground") == "#ffffff"


def test_full_reset_undoes_reverse_screen(term):
    term._process("\x1b[?5h\x1bc")

    assert term._reverse_screen is False
    assert term.cget("background") == "#000000"


def test_unknown_private_modes_and_sequences_are_ignored(term):
    term._process("a\x1b[?9999h\x1b[?9999l\x1b[99zb\x1b[ !pc")

    assert screen(term)[0] == "abc"


# ---------------------------------------------------------------------------
# Scroll regions and erased cells on each screen
# ---------------------------------------------------------------------------


def fill_rows(terminal):
    terminal._process("a\r\nb\r\nc\r\nd\r\ne")


def test_reverse_index_at_the_top_of_a_scroll_region_scrolls_it_down(both):
    fill_rows(both)

    both._process("\x1b[2;4r\x1b[2;1H\x1bM")

    assert screen(both) == ["a", "", "b", "c", "e"]
    assert cursor(both) == (1, 0)


def test_index_at_the_bottom_of_a_scroll_region_scrolls_it_up(both):
    fill_rows(both)

    both._process("\x1b[2;4r\x1b[4;3H\x1bD")

    assert screen(both) == ["a", "c", "d", "", "e"]
    assert cursor(both) == (3, 2)


def test_next_line_at_the_bottom_of_a_scroll_region_scrolls_it_up(both):
    fill_rows(both)

    both._process("\x1b[2;4r\x1b[4;3H\x1bE")

    assert screen(both) == ["a", "c", "d", "", "e"]
    assert cursor(both) == (3, 0)


def test_scroll_up_and_down_stay_inside_a_scroll_region(both):
    fill_rows(both)

    both._process("\x1b[2;4r\x1b[S")
    assert screen(both) == ["a", "c", "d", "", "e"]

    both._process("\x1b[2T")
    assert screen(both) == ["a", "", "", "c", "e"]


def test_insert_and_delete_lines_stay_inside_a_scroll_region(both):
    fill_rows(both)

    both._process("\x1b[2;4r\x1b[3;1H\x1b[L")
    assert screen(both) == ["a", "b", "", "c", "e"]

    both._process("\x1b[2M")
    assert screen(both) == ["a", "b", "", "", "e"]


def test_inserted_and_deleted_lines_take_the_current_background(both):
    fill_rows(both)

    both._process("\x1b[42m\x1b[2;1H\x1b[L")
    assert style_at(both, 1, 0).get("background") == "#00cd00"
    assert style_at(both, 1, 19).get("background") == "#00cd00"

    both._process("\x1b[1;1H\x1b[M")
    assert style_at(both, 4, 0).get("background") == "#00cd00"


def test_scrolled_in_lines_take_the_current_background(both):
    fill_rows(both)

    both._process("\x1b[44m\x1b[2;4r\x1b[S")
    assert style_at(both, 3, 0).get("background") == "#0000ee"

    both._process("\x1b[T")
    assert style_at(both, 1, 0).get("background") == "#0000ee"


def test_erase_in_line_uses_the_current_background_in_every_mode(both):
    both._process("abcdefgh\x1b[42m\x1b[1;4H\x1b[K")
    assert style_at(both, 0, 3).get("background") == "#00cd00"
    assert style_at(both, 0, 19).get("background") == "#00cd00"
    assert "background" not in style_at(both, 0, 1)

    both._process("\x1b[0m\x1b[2;1H12345678\x1b[42m\x1b[2;4H\x1b[1K")
    assert style_at(both, 1, 0).get("background") == "#00cd00"
    assert "background" not in style_at(both, 1, 7)


def test_erase_in_display_uses_the_current_background(both):
    both._process("aa\r\nbb\r\ncc\r\ndd\x1b[42m\x1b[2;2H\x1b[J")
    assert style_at(both, 1, 1).get("background") == "#00cd00"
    assert style_at(both, 3, 5).get("background") == "#00cd00"
    assert "background" not in style_at(both, 0, 0)

    both._process("\x1b[0m\x1b[44m\x1b[3;2H\x1b[1J")
    assert style_at(both, 0, 0).get("background") == "#0000ee"
    assert style_at(both, 2, 1).get("background") == "#0000ee"

    both._process("\x1b[41m\x1b[2J")
    assert style_at(both, 2, 5).get("background") == "#cd0000"


def test_character_edits_use_the_current_background(both):
    both._process("abcdef\x1b[42m\x1b[1;2H\x1b[3X")
    assert style_at(both, 0, 2).get("background") == "#00cd00"

    both._process("\x1b[0m\x1b[2;1Hxyz\x1b[42m\x1b[2;1H\x1b[P")
    if both._alt_mode:
        assert style_at(both, 1, 19).get("background") == "#00cd00"
    assert screen(both)[1] == "yz"


def test_tab_moves_over_existing_text_without_changing_it(both):
    both._process("abcdefghijkl\r\tX")

    assert screen(both)[0] == "abcdefghXjkl"
    assert cursor(both) == (0, 9)


def test_tab_past_the_end_of_a_short_line_pads_it(term):
    term._process("a\x1b[1;5H\t")

    assert cursor(term) == (0, 8)
    assert term.get("1.0", "1.end") == "a" + " " * 7


def test_printing_without_autowrap_beyond_the_end_of_a_short_line_pads_it(term):
    term._process("\x1b[?7la\x1b[1;5Hxy")

    assert screen(term)[0] == "a   xy"


def test_printing_styled_text_wraps_and_keeps_its_style(small):
    small._process("\x1b[31mabcdefgh")

    assert screen(small) == ["abcde", "fgh", ""]
    assert style_at(small, 1, 2)["foreground"] == "#cd0000"


def test_wrapping_at_the_bottom_of_a_scroll_region_scrolls_it(both_small):
    both_small._process("a\r\nb\r\nc\x1b[1;2r\x1b[2;4H123")

    assert screen(both_small) == ["b  12", "3", "c"]


def test_clearing_the_screen_with_a_background_in_the_alternate_screen_keeps_it(small):
    small._process("\x1b[?1049h\x1b[43m\x1b[?3h")

    assert style_at(small, 2, 4).get("background") == "#cdcd00"


def test_alignment_pattern_ignores_other_hash_codes(both):
    both._process("ab\x1b#3cd")

    assert screen(both)[0] == "abcd"


def test_text_wraps_across_rows_and_scrolls_on_the_alternate_screen(tk_root):
    terminal = make_terminal(tk_root, cols=5, rows=3)
    try:
        terminal._process("\x1b[?1049habcdefghijklmnopqrstuvwxyz")

        assert screen(terminal) == ["pqrst", "uvwxy", "z"]
    finally:
        shutdown(terminal)


def test_wrapping_on_the_alternate_screen_inside_a_scroll_region(tk_root):
    terminal = make_terminal(tk_root, cols=5, rows=4)
    try:
        terminal._process("\x1b[?1049h\x1b[2;3r\x1b[2;1Habcdefghijklmno")

        assert screen(terminal) == ["", "fghij", "klmno", ""]
        assert cursor(terminal) == (2, 5)
    finally:
        shutdown(terminal)

"""Wide, combining and non-BMP characters in the terminal emulator.

The emulator addresses text by terminal column while the Text widget
addresses it by character (and by UTF-16 unit for characters outside the
Basic Multilingual Plane), so every operation that turns a column into a
position has to cope with a wide character straddling the column, with
zero-width marks and with characters that take two index units. These
tests feed such text through every one of those operations, on both
screens, and check what ends up on screen.
"""

import random

import pytest

from pytkterm import _term_char_width
from terminal_helpers import cursor, make_terminal, screen, shutdown

EVIL_LINE = (
    "(((_,self.infos,self.fileinfocolumn,self.fileinfoconfig,themdiofiofgi "
    "ogioggfood83ocoofod9er,theeioioiiiiiiiiiiiiwooooonumbaland)))=[((((set"
    'attr(self,"fileinfo",ctk.Frame(self))or(self.fileinfo)).pack(padx=10,p'
    'ady=10,fill="x"),{},0,lambda**infoconfigs:[(self.infos[info].config(te'
    "xt=value)if(info)in(self.infos)else([(()if(False)and()else(None)),self"
    ".infos.update({info:ctk.Label(self.fileinfo,text=value,padx=15,pady=2,"
    "**self.fileinfo_contrast_colors())})or(self.infos[info])][898903-3889="
    '=895014]).grid(column=(setattr(self,"fileinfocolumn",self.fileinfocolu'
    "mn+1)or(self.fileinfocolumn)),row=0))for(info,value)in(infoconfigs.ite"
    'ms())],"themmfgiowefopiwiopwwiowiowiowiophㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑ'
    'ㄒㄓㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓ",(True+True+False-False*True+None+gol'
    'gi)if(False)else(8))))if(True==True==True==True!="ggioiopiigoiogiogiog'
    'gigliotherapyopwg")and(not(((False)and(b̶r̶e̶a̶k̶t̶h̶e̶n̶o̶n̶s̶e̶n̶s̶e'
    "̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶e̶r̶g̶i̶o̶w̶p̶e̶g̶i̶o̶p̶w̶i̶o̶p̶j̶h̶s̶u̶t̶i̶p"
    "̶i̶o̶p̶j̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓg̶g̶g"
    "̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶g̶n̶n̶h̶h̶u̶o̶p̶i̶o"
    "̶p̶8̶9̶2̶3̶8̶5̶9̶6̶9̶0̶k̶k̶k̶k̶k̶i̶o̶h̶o̶p̶i̶h̶g̶g̶g̶gggggggggggggggg*"
    "g845ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓ895.dioio))))else(gggggjgjgghhgghggggagainhellothis"
    "islookatthisfinditpynotesthisisthepynotesthepynotesagain.g8g8g9g8g9g.t"
    'heworld.hello.no.bye),"(-b-(((2*b**3-9*a*b*c+27*a**2*d)**2-4*(b**2-3*a'
    "*c)**3)**(1/2)+2*b**3-9*a*b*c+27*a**2*d)**(1/3)/(3*2**(1/3)*a)-(2**(1/"
    "3)*(b**2-3*a*c))/(3*a*(((2*b**3-9*a*b*c+27*a**2*d)**2-4*(b**2-3*a*c)**"
    '3)**(1/2)+2*b**3-9*a*b*c+27*a**2*d)**(1/3)))/(3*a)"][not(not(not(not(n'
    'ot(not(not(not(not(not(256=="98389389383838383838338383833367676767676'
    '7677676767"))))))))))]#fmt: skip'
)


def width(text):
    return sum(_term_char_width(char) for char in text)


def raw_rows(terminal):
    """Every row of the buffer's visible screen, untrimmed."""
    top = 1 if terminal._alt_mode else terminal.screen_top
    return [
        terminal.get(f"{line}.0", f"{line}.end")
        for line in range(top, top + terminal._GRID_ROWS)
        if line <= int(terminal.index("end - 1 char").split(".")[0])
    ]


def assert_rows_fit(terminal):
    """Rows never run past the grid, and on the alternate screen are
    exactly as wide as it, whatever was written or erased."""
    rows = raw_rows(terminal)
    for row in rows:
        if terminal._alt_mode:
            assert width(row) == terminal._GRID_COLS, rows
        else:
            assert width(row) <= terminal._GRID_COLS, rows
    if terminal._alt_mode:
        assert len(rows) == terminal._GRID_ROWS


@pytest.fixture
def narrow(tk_root):
    terminal = make_terminal(tk_root, cols=10, rows=6)
    yield terminal
    shutdown(terminal)


@pytest.fixture(params=["primary", "alternate"])
def both_narrow(request, tk_root):
    terminal = make_terminal(tk_root, cols=10, rows=6)
    if request.param == "alternate":
        terminal._process("\x1b[?1049h")
    yield terminal
    shutdown(terminal)


def select_everything(terminal):
    last = terminal._term_last_real_line()
    terminal.tag_add("sel", "1.0", f"{last}.end")
    terminal._copy_selection()
    return terminal.clipboard_get().rstrip("\n")


# ---------------------------------------------------------------------------
# Wrapping and copying
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "abcdefghijklmnopqrstuvwxyz",
        "日本語日本語日本語日本語日本語",
        "a日b本c語d日e本f語g日h本",
        "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓ",
        "😀😀😀😀😀😀😀😀😀😀😀😀",
        "éééééééééééééééééééé",
    ],
)
def test_copying_wrapped_text_gives_back_the_original(narrow, text):
    narrow._process(text)

    assert select_everything(narrow) == text


@pytest.mark.parametrize("chunk", [1, 3, 7])
def test_copying_wrapped_text_written_in_pieces(narrow, chunk):
    text = "a日b本c語d日e本f語g日h本i"
    for start in range(0, len(text), chunk):
        narrow._process(text[start : start + chunk])

    assert select_everything(narrow) == text


def test_a_wide_character_that_does_not_fit_leaves_the_last_column_empty(narrow):
    narrow._process("a日b本c語d日e本f")

    assert screen(narrow)[:2] == ["a日b本c語d", "日e本f"]
    assert "wrapcont" in narrow.tag_names("1.end")
    assert select_everything(narrow) == "a日b本c語d日e本f"


def test_the_evil_line_wraps_and_copies_back_unchanged(tk_root):
    terminal = make_terminal(tk_root, cols=80, rows=30)
    try:
        terminal._process(EVIL_LINE)

        assert_rows_fit(terminal)
        assert select_everything(terminal) == EVIL_LINE
    finally:
        shutdown(terminal)


@pytest.mark.parametrize("cols", [79, 61, 40, 33, 20, 7])
def test_the_evil_line_survives_every_width_without_overflowing(tk_root, cols):
    terminal = make_terminal(tk_root, cols=cols, rows=200)
    try:
        terminal._process(EVIL_LINE)

        assert_rows_fit(terminal)
        assert select_everything(terminal) == EVIL_LINE
    finally:
        shutdown(terminal)


# ---------------------------------------------------------------------------
# Shrinking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("new_cols", [9, 8, 7, 5, 3])
def test_shrinking_never_leaves_a_wide_character_across_the_edge(both_narrow, new_cols):
    both_narrow._process("日本語日本\r\nabc日本語\r\n😀😀😀😀😀\r\ng̶g̶日日日日")

    both_narrow._term_resize_grid(new_cols, 6)

    assert_rows_fit(both_narrow)


def test_shrinking_cuts_the_evil_line_to_the_new_width(tk_root):
    terminal = make_terminal(tk_root, cols=80, rows=40)
    try:
        terminal._process(EVIL_LINE)

        terminal._term_resize_grid(60, 40)

        assert_rows_fit(terminal)
    finally:
        shutdown(terminal)


# ---------------------------------------------------------------------------
# Editing a row that holds wide, combining or non-BMP characters
# ---------------------------------------------------------------------------

ROW = "日本語日本"


@pytest.mark.parametrize("column", range(0, 11))
@pytest.mark.parametrize("count", [1, 2, 3, 7])
@pytest.mark.parametrize("command", ["X", "P", "@"])
def test_editing_commands_keep_rows_the_grid_width(both_narrow, command, count, column):
    both_narrow._process(ROW + f"\x1b[1;{column + 1}H\x1b[{count}{command}")

    assert_rows_fit(both_narrow)


@pytest.mark.parametrize("column", range(0, 11))
@pytest.mark.parametrize("mode", [0, 1, 2])
@pytest.mark.parametrize("command", ["K", "J"])
def test_erasing_keeps_rows_the_grid_width(both_narrow, command, mode, column):
    both_narrow._process(ROW + f"\r\n{ROW}\x1b[1;{column + 1}H\x1b[{mode}{command}")

    assert_rows_fit(both_narrow)


def test_deleting_characters_up_to_half_a_wide_character_blanks_that_half(both_narrow):
    both_narrow._process("日本語\x1b[1;1H\x1b[3P")

    assert_rows_fit(both_narrow)
    assert screen(both_narrow)[0] == " 語"


def test_erasing_characters_up_to_half_a_wide_character_blanks_that_half(both_narrow):
    both_narrow._process("日本語\x1b[1;1H\x1b[3X")

    assert_rows_fit(both_narrow)
    assert screen(both_narrow)[0] == "    語"


def test_erase_to_start_of_screen_includes_a_wide_character_under_the_cursor(
    both_narrow,
):
    both_narrow._process("日本語\x1b[1;4H\x1b[1J")

    assert_rows_fit(both_narrow)
    assert screen(both_narrow)[0] == "    語"


def test_erasing_characters_never_grows_a_primary_row_past_the_grid(narrow):
    narrow._process("ab\x1b[1;9H\x1b[50X")

    assert width(raw_rows(narrow)[0]) <= 10


def test_the_cursor_report_counts_columns_after_wide_and_combining_text(narrow):
    narrow._process("日g̶😀")

    assert cursor(narrow) == (0, 5)


@pytest.mark.parametrize("command", ["S", "T"])
def test_scrolling_keeps_the_cursor_in_its_column_over_wide_characters(
    both_narrow, command
):
    both_narrow._process("日日日日\r\nabcdefghij\x1b[2;5H" + f"\x1b[1{command}")

    assert cursor(both_narrow) == (1, 4)


# ---------------------------------------------------------------------------
# Zero-width characters
# ---------------------------------------------------------------------------


def test_zero_width_characters_stay_in_the_text(narrow):
    narrow._process("g\u0336e\u0301\u200bx")

    assert narrow.get("1.0", "1.end") == "g\u0336e\u0301\u200bx"
    assert width(narrow.get("1.0", "1.end")) == 3
    assert cursor(narrow) == (0, 3)


# ---------------------------------------------------------------------------
# Random mixtures of everything above
# ---------------------------------------------------------------------------

POOL = [
    "a", "b", "x", " ", "日", "本", "語", "ㄅ", "é", "é", "g̶",
    "😀", "🎉", "​", "ｱ", "Ａ", "한", "\U0001F468‍\U0001F469",
    "❤️", "あ",
]  # fmt: skip


def random_text(rng):
    return "".join(rng.choice(POOL) for _ in range(rng.randint(1, 14)))


def random_escape(rng, cols, rows):
    n = rng.randint
    return rng.choice(
        [
            lambda: f"\x1b[{n(0, rows + 2)};{n(0, cols + 3)}H",
            lambda: f"\x1b[{n(0, cols + 3)}G",
            lambda: f"\x1b[{n(0, 4)}C",
            lambda: f"\x1b[{n(0, 4)}D",
            lambda: f"\x1b[{n(0, 3)}A",
            lambda: f"\x1b[{n(0, 3)}B",
            lambda: f"\x1b[{n(0, 6)}X",
            lambda: f"\x1b[{n(0, 6)}P",
            lambda: f"\x1b[{n(0, 6)}@",
            lambda: f"\x1b[{rng.choice([0, 1, 2])}K",
            lambda: f"\x1b[{rng.choice([0, 1, 2])}J",
            lambda: f"\x1b[{n(0, 2)}L",
            lambda: f"\x1b[{n(0, 2)}M",
            lambda: f"\x1b[{n(0, 2)}S",
            lambda: f"\x1b[{n(0, 2)}T",
            lambda: f"\x1b[{n(0, 3)}b",
            lambda: "\r",
            lambda: "\n",
            lambda: "\b",
            lambda: "\t",
            lambda: "\x1b[?7l",
            lambda: "\x1b[?7h",
            lambda: "\x1b7",
            lambda: "\x1b8",
            lambda: "\x1bM",
            lambda: "\x1bD",
            lambda: "\x1bE",
        ]
    )()


@pytest.mark.parametrize("alternate", [False, True], ids=["primary", "alternate"])
@pytest.mark.parametrize("seed", range(60))
def test_random_output_never_breaks_the_grid(tk_root, seed, alternate):
    rng = random.Random(seed)
    cols = rng.choice([7, 8, 9, 10, 15])
    terminal = make_terminal(tk_root, cols=cols, rows=6)
    try:
        if alternate:
            terminal._process("\x1b[?1049h")
        steps = []
        for _ in range(40):
            if rng.random() < 0.5:
                steps.append(random_text(rng))
            else:
                steps.append(random_escape(rng, cols, 6))
            terminal._process(steps[-1])
            try:
                assert_rows_fit(terminal)
            except AssertionError as error:
                raise AssertionError(f"after {steps[-6:]!r}: {error}") from None
    finally:
        shutdown(terminal)

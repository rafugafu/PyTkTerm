"""The pure color and SGR helpers of the terminal, which need no display."""

import pytest

from pytkterm import (
    _ansi_256_hex,
    _sgr_apply,
    _sgr_colour_hex,
    _sgr_is_highlight,
    _sgr_new_state,
    _term_char_width,
    _term_sgr_resolve,
    _term_split_run,
    _term_tcl_len,
)


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, "#000000"),
        (9, "#ff0000"),
        (15, "#ffffff"),
        (16, "#000000"),
        (21, "#0000ff"),
        (196, "#ff0000"),
        (231, "#ffffff"),
        (232, "#080808"),
        (255, "#eeeeee"),
    ],
)
def test_ansi_256_palette_covers_named_cube_and_gray_ramp(index, expected):
    assert _ansi_256_hex(index) == expected


def test_sgr_colour_hex_accepts_none_indexes_and_truecolor_tuples():
    assert _sgr_colour_hex(None) is None
    assert _sgr_colour_hex(2) == "#00cd00"
    assert _sgr_colour_hex((1, 2, 255)) == "#0102ff"


@pytest.mark.parametrize(
    ("params", "key", "value"),
    [
        ([1], "bold", True),
        ([3], "italic", True),
        ([4], "underline", True),
        ([5], "blink", True),
        ([6], "blink", True),
        ([7], "reverse", True),
        ([8], "conceal", True),
        ([9], "strike", True),
        ([31], "fg", 1),
        ([97], "fg", 15),
        ([42], "bg", 2),
        ([107], "bg", 15),
        ([38, 5, 200], "fg", 200),
        ([48, 5, 17], "bg", 17),
        ([38, 2, 10, 20, 30], "fg", (10, 20, 30)),
        ([48, 2, 40, 50, 60], "bg", (40, 50, 60)),
    ],
)
def test_sgr_apply_sets_attributes_and_colors(params, key, value):
    state = _sgr_new_state()

    _sgr_apply(state, params)

    assert state[key] == value


@pytest.mark.parametrize(
    ("setter", "clearer", "key"),
    [
        (1, 22, "bold"),
        (3, 23, "italic"),
        (4, 24, "underline"),
        (5, 25, "blink"),
        (7, 27, "reverse"),
        (8, 28, "conceal"),
        (9, 29, "strike"),
        (31, 39, "fg"),
        (41, 49, "bg"),
    ],
)
def test_sgr_apply_clears_each_attribute_individually(setter, clearer, key):
    state = _sgr_new_state()
    _sgr_apply(state, [setter])
    assert state[key]

    _sgr_apply(state, [clearer])

    assert state[key] in (False, None)


def test_sgr_apply_zero_or_empty_resets_everything():
    state = _sgr_new_state()
    _sgr_apply(state, [1, 3, 4, 7, 31, 42])

    _sgr_apply(state, [])

    assert state == _sgr_new_state()


def test_sgr_apply_ignores_truncated_extended_colors():
    state = _sgr_new_state()

    _sgr_apply(state, [38, 5])
    _sgr_apply(state, [48, 2, 1, 2])

    assert state["fg"] is None
    assert state["bg"] is None


@pytest.mark.parametrize(
    ("character", "width"),
    [("a", 1), ("é", 1), ("́", 0), ("‍", 0), ("日", 2), ("Ａ", 2)],
)
def test_term_char_width(character, width):
    assert _term_char_width(character) == width


def test_sgr_resolve_applies_bold_brightening_reverse_and_conceal():
    state = _sgr_new_state()
    _sgr_apply(state, [1, 31])
    assert _term_sgr_resolve(state, "@fg", "@bg") == ("#ff0000", "@bg")

    state = _sgr_new_state()
    _sgr_apply(state, [7])
    assert _term_sgr_resolve(state, "@fg", "@bg") == ("@bg", "@fg")

    state = _sgr_new_state()
    _sgr_apply(state, [7, 31, 42])
    assert _term_sgr_resolve(state, "@fg", "@bg") == ("#00cd00", "#cd0000")

    state = _sgr_new_state()
    _sgr_apply(state, [8, 44])
    fg, bg = _term_sgr_resolve(state, "@fg", "@bg")
    assert fg == bg == "#0000ee"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ([], None),
        ([1, 4], None),
        ([7], True),
        ([27], False),
        ([0], False),
        ([7, 0], False),
        ([0, 7], True),
        ([42], True),
        ([100], True),
        ([49], False),
        ([40], False),
        ([48, 5, 17], True),
        ([48, 2, 1, 2, 3], True),
        ([48, 5, 17, 27], False),
        ([48, 2, 1, 2, 3, 49], False),
    ],
)
def test_sgr_is_highlight_tracks_reverse_video_and_backgrounds(params, expected):
    assert _sgr_is_highlight(params) is expected


@pytest.mark.parametrize(
    ("text", "length"),
    [("", 0), ("abc", 3), ("é日", 2), ("\U0001F600", 2), ("a\U0001F600b", 4)],
)
def test_tcl_length_counts_characters_outside_the_basic_plane_twice(text, length):
    assert _term_tcl_len(text) == length


@pytest.mark.parametrize(
    ("run", "space", "expected"),
    [
        ("abcde", 3, ("abc", "de", 3)),
        ("abc", 10, ("abc", "", 3)),
        ("", 5, ("", "", 0)),
        ("日本語", 4, ("日本", "語", 4)),
        ("日本語", 3, ("日", "本語", 2)),
        ("a日", 2, ("a", "日", 1)),
        ("日", 1, ("日", "", 2)),
        ("éx", 1, ("é", "x", 1)),
    ],
)
def test_split_run_takes_what_fits_in_the_given_columns(run, space, expected):
    assert _term_split_run(run, space) == expected


def test_split_run_always_makes_progress_on_a_character_that_does_not_fit():
    prefix, rest, width = _term_split_run("日日", 0)

    assert (prefix, rest, width) == ("日", "日", 2)

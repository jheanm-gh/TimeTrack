"""Colour contrast, measured rather than eyeballed.

The first version of the interface put near-white text on a light
background for disabled buttons - 1.35:1, which is unreadable - because
"looks disabled" was implemented by fading the text towards its own
background. These tests make that impossible to ship again, in either
palette.

The thresholds are WCAG AA: 4.5:1 for normal text, 3:1 for large text.
"""

from __future__ import annotations

import pytest

from app.ui import theme

AA_NORMAL = 4.5
AA_LARGE = 3.0

#: Readouts drawn in elapsed_font() are large and bold, so they are held to
#: the large-text threshold rather than the normal one.
LARGE_TEXT_PAIRS = {
    "running work readout",
    "running software readout",
    "paused readout",
    "idle readout",
}


def _channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    text = hex_colour.lstrip("#")
    red, green, blue = (int(text[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    first, second = luminance(foreground), luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.fixture(autouse=True)
def restore_theme():
    original = theme.current_theme()
    yield
    theme.set_theme(original)


class TestContrast:
    @pytest.mark.parametrize("palette_name", [theme.LIGHT, theme.DARK])
    @pytest.mark.parametrize(
        ("description", "foreground", "background"), theme.CONTRAST_PAIRS
    )
    def test_every_pair_is_readable(
        self, palette_name, description, foreground, background
    ):
        colours = theme.PALETTES[palette_name]
        ratio = contrast(colours[foreground], colours[background])
        required = AA_LARGE if description in LARGE_TEXT_PAIRS else AA_NORMAL
        assert ratio >= required, (
            f"{palette_name}: {description} is {ratio:.2f}:1 "
            f"({colours[foreground]} on {colours[background]}), "
            f"needs {required}:1"
        )

    def test_the_measurement_itself_is_right(self):
        """Known values, so a passing suite means something."""
        assert contrast("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.01)
        assert contrast("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)
        # The disabled Stop button as it originally shipped.
        assert contrast("#FBF6F5", "#E7D2CF") < 1.5

    def test_disabled_buttons_are_still_readable(self):
        """The specific defect that started this: text faded into its own
        background so that it 'looked' disabled."""
        for palette_name in (theme.LIGHT, theme.DARK):
            colours = theme.PALETTES[palette_name]
            ratio = contrast(colours["disabled_text"], colours["disabled"])
            assert ratio >= AA_NORMAL, f"{palette_name} disabled button: {ratio:.2f}:1"


class TestPalettes:
    def test_both_palettes_define_the_same_tokens(self):
        assert set(theme.PALETTES[theme.LIGHT]) == set(theme.PALETTES[theme.DARK])

    def test_every_token_is_a_valid_colour(self):
        from PySide6.QtGui import QColor

        for name, colours in theme.PALETTES.items():
            for token, value in colours.items():
                assert QColor(value).isValid(), f"{name}.{token} = {value!r}"

    def test_every_contrast_pair_names_real_tokens(self):
        tokens = set(theme.PALETTES[theme.LIGHT])
        for description, foreground, background in theme.CONTRAST_PAIRS:
            assert foreground in tokens, f"{description}: {foreground}"
            assert background in tokens, f"{description}: {background}"

    def test_the_two_palettes_are_actually_different(self):
        light = theme.PALETTES[theme.LIGHT]
        dark = theme.PALETTES[theme.DARK]
        assert luminance(light["panel"]) > 0.5
        assert luminance(dark["panel"]) < 0.2


class TestSwitching:
    def test_named_colours_follow_the_theme(self, qapp):
        theme.set_theme(theme.LIGHT)
        light_muted = theme.MUTED.name()
        theme.set_theme(theme.DARK)
        assert theme.MUTED.name() != light_muted

    def test_the_stylesheet_follows_the_theme(self, qapp):
        theme.set_theme(theme.LIGHT)
        light = theme.stylesheet()
        theme.set_theme(theme.DARK)
        assert theme.stylesheet() != light
        assert theme.PALETTES[theme.DARK]["panel"] in theme.stylesheet()

    def test_an_unknown_name_falls_back_rather_than_crashing(self, qapp):
        assert theme.set_theme("chartreuse") in (theme.LIGHT, theme.DARK)
        assert theme.set_theme(None) in (theme.LIGHT, theme.DARK)

    def test_system_resolves_to_a_real_palette(self, qapp):
        assert theme.resolve(theme.SYSTEM) in (theme.LIGHT, theme.DARK)


class TestTrayIconStaysReadableInBothThemes:
    def test_the_icon_does_not_change_with_the_application_theme(self, qapp):
        """It sits in the Windows tray, not in the window, so it must read
        against whatever the desktop is - not whatever the app is set to."""
        theme.set_theme(theme.LIGHT)
        light = theme.state_pixmap(32, work=True).toImage()
        theme.set_theme(theme.DARK)
        dark = theme.state_pixmap(32, work=True).toImage()
        assert light == dark


class TestNoColoursEscapeThePalette:
    """A colour written into a widget cannot follow the theme.

    The "next date to enter" highlight was a fixed light yellow, which was
    fine in light mode and unreadable behind light text in dark mode.
    """

    def test_no_hardcoded_hex_colours_in_the_interface(self):
        import re
        from pathlib import Path

        offenders = []
        for path in sorted(Path("app/ui").rglob("*.py")):
            if path.name == "theme.py":
                continue  # the palette is allowed to contain colours
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if re.search(r'"#[0-9A-Fa-f]{3,8}"', line.split("#")[0]):
                    offenders.append(f"{path}:{number}: {line.strip()}")
        assert not offenders, (
            "Colours outside the palette cannot follow a theme change:\n"
            + "\n".join(offenders)
        )

    def test_the_scan_would_catch_one(self):
        import re

        assert re.search(r'"#[0-9A-Fa-f]{3,8}"', 'x = "#FFF3CD"')
        assert not re.search(r'"#[0-9A-Fa-f]{3,8}"', "x = theme.hex_of('next_row')")

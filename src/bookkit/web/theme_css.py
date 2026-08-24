"""Serves the web palette as CSS custom properties.

`app.css` contains no literal colour — every colour it uses is a `--`
variable defined here, generated from `bookkit.palette.WEB_*`, so the
stylesheet and the palette module can never say two different things about
what a colour means."""

from __future__ import annotations

from .. import palette

# CSS custom-property name -> palette module attribute name. Order matches
# the palette table in docs/superpowers/specs/2026-08-17-web-visual-direction.md.
_VARIABLES: tuple[tuple[str, str], ...] = (
    ("--ink", "WEB_INK"),
    ("--paper", "WEB_PAPER"),
    ("--wash", "WEB_WASH"),
    ("--wash-2", "WEB_WASH_2"),
    ("--stone", "WEB_STONE"),
    ("--hairline", "WEB_HAIRLINE"),
    ("--hairline-2", "WEB_HAIRLINE_2"),
    ("--border", "WEB_BORDER"),
    ("--muted", "WEB_MUTED"),
    ("--accent", "WEB_ACCENT"),
    ("--accent-wash", "WEB_ACCENT_WASH"),
    ("--grid", "WEB_GRID"),
    ("--danger", "WEB_DANGER"),
    ("--danger-wash", "WEB_DANGER_WASH"),
    ("--warn", "WEB_WARN"),
    ("--warn-wash", "WEB_WARN_WASH"),
    ("--good", "WEB_GOOD"),
    ("--good-wash", "WEB_GOOD_WASH"),
    ("--good-badge", "WEB_GOOD_BADGE"),
    ("--edit-underline", "WEB_EDIT_UNDERLINE"),
    ("--unplaced", "WEB_UNPLACED"),
    ("--slate", "WEB_SLATE"),
    ("--slate-wash", "WEB_SLATE_WASH"),
    ("--hover", "WEB_HOVER"),
    ("--shadow-card", "WEB_SHADOW_CARD"),
    ("--shadow-toast", "WEB_SHADOW_TOAST"),
)


def css_variables() -> str:
    """The `:root { --token: #value; ... }` block served at /static/theme.css."""
    lines = [f"  {css_name}: {getattr(palette, attr)};" for css_name, attr in _VARIABLES]
    return ":root {\n" + "\n".join(lines) + "\n}\n"

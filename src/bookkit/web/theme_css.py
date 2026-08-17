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
    ("--accent", "WEB_ACCENT"),
    ("--sky", "WEB_SKY"),
    ("--ground", "WEB_GROUND"),
    ("--paper", "WEB_PAPER"),
    ("--band", "WEB_BAND"),
    ("--rule", "WEB_RULE"),
    ("--rule-firm", "WEB_RULE_FIRM"),
    ("--muted", "WEB_MUTED"),
    ("--bound", "WEB_BOUND"),
    ("--bound-tx", "WEB_BOUND_TX"),
    ("--warn", "WEB_WARN"),
    ("--over", "WEB_OVER"),
)


def css_variables() -> str:
    """The `:root { --token: #value; ... }` block served at /static/theme.css."""
    lines = [f"  {css_name}: {getattr(palette, attr)};" for css_name, attr in _VARIABLES]
    return ":root {\n" + "\n".join(lines) + "\n}\n"

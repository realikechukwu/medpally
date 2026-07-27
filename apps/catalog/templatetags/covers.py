"""Generated journal covers.

Inline SVG rendered at request time from the journal's abbreviation and a colour
from our palette. No stored images, no build step, and — deliberately — no
reproduction of the journal's own logo or trademarked lockup. Plain nominative
reference in our own typeface is defensible; a pixel-accurate imitation is not.
"""

from __future__ import annotations

from django import template
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

from apps.catalog.models import Journal

register = template.Library()

# Long abbreviations need to shrink to stay inside the tile.
_SIZE_STEPS = ((6, 30), (10, 22), (14, 17), (20, 13))


def _font_size(text: str) -> int:
    for max_len, size in _SIZE_STEPS:
        if len(text) <= max_len:
            return size
    return 11


@register.simple_tag
def journal_cover(journal: Journal | None, size: int = 96) -> SafeString:
    """Render a journal cover as an inline SVG."""
    if journal is None:
        label, color, accent, style = "?", "#64748b", "", Journal.CoverStyle.SOLID
    else:
        label = journal.short_name or journal.display_name[:8]
        color = journal.cover_color
        accent = journal.cover_color_accent
        style = journal.cover_style

    label_esc = escape(label)
    color_esc = escape(color)
    font = _font_size(label)

    if style == Journal.CoverStyle.SPLIT and accent:
        background = (
            f'<rect width="100" height="100" fill="{color_esc}"/>'
            f'<rect y="50" width="100" height="50" fill="{escape(accent)}"/>'
        )
    elif style == Journal.CoverStyle.RULE:
        rule = escape(accent) if accent else "#ffffff"
        background = (
            f'<rect width="100" height="100" fill="{color_esc}"/>'
            f'<rect x="12" y="78" width="76" height="3" fill="{rule}" opacity="0.85"/>'
        )
    else:
        background = f'<rect width="100" height="100" fill="{color_esc}"/>'

    # Safe: label and colour are escaped above and size is coerced to int.
    return mark_safe(
        f'<svg class="journal-cover" viewBox="0 0 100 100" width="{int(size)}" '
        f'height="{int(size)}" role="img" aria-label="{label_esc}" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{background}"
        f'<text x="50" y="50" fill="#ffffff" font-size="{font}" font-weight="600" '
        'font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" '
        f'text-anchor="middle" dominant-baseline="central">{label_esc}</text>'
        "</svg>"
    )

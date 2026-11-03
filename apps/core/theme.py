"""Theme derivation and validation.

SRS THM-03/04 (derived tokens), THM-07 (readability guard), THM-13 (Signature default),
THM-14 (server validates hex, normalises to upper case, recomputes derived tokens),
THM-18 (12 built-in palettes).
"""

import colorsys
import re

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

SIGNATURE = ("Signature", "#231E1C", "#8C5550")
# Fixed neutrals (THM-03) used only for contrast maths; they live in the frontend token file.
PAGE_BG = "#F8F4EC"
INK = "#231E1C"

# THM-18: palettes approved by the owner on 2026-11-04.
BUILTIN_PALETTES = [
    SIGNATURE,
    ("Blush Rose", "#A8475A", "#E8B4B8"),
    ("Champagne Gold", "#7C6036", "#C9B28D"),
    ("Mauve Lavender", "#6E4F7C", "#CBB5D8"),
    ("Burgundy Wine", "#6D1F36", "#C98A9A"),
    ("Sage Eucalyptus", "#4F6B5A", "#A9C0A6"),
    ("Terracotta Peach", "#A8553A", "#F0B79D"),
    ("Emerald", "#1F6B54", "#9ED1BC"),
    ("Navy Pearl", "#1F2F4F", "#B7C4DC"),
    ("Mocha Nude", "#6B4E43", "#D9BDAA"),
    ("Coral Pink", "#C4473F", "#F7B6A8"),
    ("Plum Berry", "#5B2A58", "#D79ACB"),
]
MAX_CUSTOM_PALETTES = 5  # THM-19


def normalize_hex(value: str) -> str:
    """THM-14: only #RRGGBB accepted, stored upper-case."""
    if not isinstance(value, str) or not HEX_RE.match(value.strip()):
        raise ValueError("Colour must be a hex value like #RRGGBB.")
    return value.strip().upper()


def _rgb(h: str):
    return tuple(int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(max(0, min(1, c)) * 255):02X}" for c in rgb)


def luminance(h: str) -> float:
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _shift(h: str, delta: float) -> str:
    """Lighten dark colours / darken light ones by `delta` of HSL lightness."""
    hue, light, sat = colorsys.rgb_to_hls(*_rgb(h))
    light = light + delta if light < 0.5 else light - delta
    return _hex(colorsys.hls_to_rgb(hue, max(0, min(1, light)), sat))


def _mix_white(h: str, amount: float) -> str:
    return _hex(tuple(c + (1 - c) * amount for c in _rgb(h)))


def text_on(primary: str) -> str:
    """THM-04/07: pick the page cream or ink, else black/white, meeting 4.5:1."""
    for candidate in (PAGE_BG, INK, "#FFFFFF", "#000000"):
        if contrast_ratio(primary, candidate) >= 4.5:
            return candidate
    return "#FFFFFF" if luminance(primary) < 0.18 else "#000000"


def derive_tokens(primary: str, accent: str | None) -> dict[str, str]:
    """THM-03/04: the palette-driven CSS variables. Accent falls back to primary."""
    primary = normalize_hex(primary)
    accent = normalize_hex(accent) if accent else primary
    return {
        "--color-primary": primary,
        "--color-primary-hover": _shift(primary, 0.10),
        "--color-primary-active": _shift(primary, 0.18),
        "--color-primary-soft": _mix_white(primary, 0.88),
        "--color-primary-contrast": text_on(primary),
        "--color-accent": accent,
    }


def readability_warning(primary: str) -> bool:
    """THM-07: primary needs 3:1 against the page background (links, borders, focus ring)."""
    return contrast_ratio(normalize_hex(primary), PAGE_BG) < 3

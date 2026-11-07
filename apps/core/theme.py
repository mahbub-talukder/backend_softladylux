"""Theme derivation and validation.

SRS THM-03/04 (derived tokens), THM-07 (readability guard), THM-13 (Signature default),
THM-14 (server validates hex, normalises to upper case, recomputes derived tokens),
THM-18 (12 built-in palettes).

Owner-approved amendment (Task 1): the storefront page Background and Text colours are editable
too. Every other neutral (surface, cream, border, field, muted, body, tiles) is DERIVED from those
two, so a dark or tinted background stays readable. While both are the Signature defaults nothing
extra is emitted and tokens.css supplies the original neutrals: existing sites look the same.
"""

import colorsys
import re

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

SIGNATURE = ("Signature", "#231E1C", "#8C5550")
# Signature neutrals (THM-03): the defaults for Background / Text (also in the frontend tokens.css).
PAGE_BG = "#F8F4EC"
INK = "#231E1C"
DEFAULT_BG, DEFAULT_TEXT = PAGE_BG, INK
CHAMPAGNE = "#C9B28D"  # eyebrow/accent text used instead of bronze on a dark background

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


def _mix(a: str, b: str, t: float) -> str:
    """Blend colour `a` towards `b` by `t` (0 = a, 1 = b)."""
    return _hex(tuple(x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b), strict=True)))


def text_on(primary: str, background: str = PAGE_BG, ink: str = INK) -> str:
    """THM-04/07: pick the page background or text colour, else black/white, meeting 4.5:1."""
    for candidate in (background, ink, "#FFFFFF", "#000000"):
        if contrast_ratio(primary, candidate) >= 4.5:
            return candidate
    return "#FFFFFF" if luminance(primary) < 0.18 else "#000000"


def _normalize_neutrals(background: str | None, text: str | None) -> tuple[str, str]:
    return (normalize_hex(background) if background else DEFAULT_BG,
            normalize_hex(text) if text else DEFAULT_TEXT)


def uses_custom_neutrals(background: str | None, text: str | None) -> bool:
    return _normalize_neutrals(background, text) != (DEFAULT_BG, DEFAULT_TEXT)


def derive_neutrals(primary: str, accent: str, background: str, text: str) -> dict[str, str]:
    """Every neutral the storefront uses, derived from the owner's Background and Text colours.
    Surfaces step away from the background; lines and muted text blend the text colour into it, and
    muted text is nudged until it keeps 4.5:1 on the background."""
    dark = luminance(background) < 0.4
    muted = text
    for step in range(0, 11):  # first blend that is readable, starting near the Signature ratio
        cand = _mix(background, text, 0.67 + 0.03 * step)
        if contrast_ratio(cand, background) >= 4.5:
            muted = cand
            break
    body = _mix(background, text, 0.83)
    if contrast_ratio(body, background) < 4.5:
        body = text
    tokens = {
        "--color-bg": background,
        "--color-surface": (_mix(background, text, 0.05) if dark
                            else _mix(background, "#FFFFFF", 0.75)),
        "--color-cream": _mix(background, text, 0.10 if dark else 0.06),
        "--color-border": _mix(background, text, 0.20 if dark else 0.13),
        "--color-field": _mix(background, text, 0.28 if dark else 0.19),
        "--color-ink": text,
        "--color-muted": muted,
        "--color-body": body,
    }
    # Placeholder/tile backgrounds: tints of the brand colours on the page colour.
    for i in range(1, 9):
        tokens[f"--tile-{i}"] = _mix(background, accent if i % 2 else primary, 0.06 + 0.012 * i)
    if dark and contrast_ratio(CHAMPAGNE, background) >= 3:
        tokens["--color-gold"] = CHAMPAGNE  # bronze eyebrow text would vanish on a dark page
    return tokens


def derive_tokens(primary: str, accent: str | None, background: str | None = None,
                  text: str | None = None) -> dict[str, str]:
    """THM-03/04: the palette-driven CSS variables. Accent falls back to primary. With the default
    Background/Text only the six palette tokens are emitted (tokens.css supplies the neutrals)."""
    primary = normalize_hex(primary)
    accent = normalize_hex(accent) if accent else primary
    bg, txt = _normalize_neutrals(background, text)
    custom = (bg, txt) != (DEFAULT_BG, DEFAULT_TEXT)
    tokens = {
        "--color-primary": primary,
        "--color-primary-hover": _shift(primary, 0.10),
        "--color-primary-active": _shift(primary, 0.18),
        "--color-primary-soft": _mix(primary, bg, 0.88) if custom else _mix_white(primary, 0.88),
        "--color-primary-contrast": text_on(primary, bg, txt),
        "--color-accent": accent,
    }
    if custom:
        tokens.update(derive_neutrals(primary, accent, bg, txt))
    return tokens


def readability_issues(primary: str, background: str | None = None,
                       text: str | None = None) -> list[str]:
    """THM-07 (extended): codes for readability problems, empty when fine.
    primary_on_background: primary needs 3:1 on the page (links, borders, focus ring).
    text_on_background: body text needs 4.5:1 on the page."""
    bg, txt = _normalize_neutrals(background, text)
    issues = []
    if contrast_ratio(normalize_hex(primary), bg) < 3:
        issues.append("primary_on_background")
    if contrast_ratio(txt, bg) < 4.5:
        issues.append("text_on_background")
    return issues


def readability_warning(primary: str, background: str | None = None,
                        text: str | None = None) -> bool:
    return bool(readability_issues(primary, background, text))

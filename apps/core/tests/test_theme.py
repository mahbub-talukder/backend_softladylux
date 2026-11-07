# Covers SRS THM-04, THM-07, THM-13, THM-14, THM-18.
import pytest

from apps.core import theme


def test_normalize_upper_and_reject():
    assert theme.normalize_hex(" #6b4e43 ") == "#6B4E43"  # THM-14
    for bad in ("6B4E43", "#12345", "#GGGGGG", "red", None):
        with pytest.raises(ValueError):
            theme.normalize_hex(bad)


def test_derived_tokens_complete():  # THM-03/04
    t = theme.derive_tokens("#6b4e43", "#d9bdaa")
    assert set(t) == {"--color-primary", "--color-primary-hover", "--color-primary-active",
                      "--color-primary-soft", "--color-primary-contrast", "--color-accent"}
    assert t["--color-primary"] == "#6B4E43" and t["--color-accent"] == "#D9BDAA"
    assert all(theme.HEX_RE.match(v) for v in t.values())


def test_accent_falls_back_to_primary():
    assert theme.derive_tokens("#A8475A", None)["--color-accent"] == "#A8475A"


@pytest.mark.parametrize("name,primary,accent", theme.BUILTIN_PALETTES)
def test_contrast_text_meets_4_5(name, primary, accent):  # THM-04/07
    t = theme.derive_tokens(primary, accent)
    assert theme.contrast_ratio(primary, t["--color-primary-contrast"]) >= 4.5


def test_contrast_flips_for_light_primary():  # scenario 16
    assert theme.text_on("#FFE9A8") in ("#231E1C", "#000000")


def test_readability_warning():  # THM-07
    assert theme.readability_warning("#F5EFE0") is True
    assert theme.readability_warning("#231E1C") is False


def test_twelve_builtin_palettes_signature_first():  # THM-18, THM-13
    assert len(theme.BUILTIN_PALETTES) == 12
    assert theme.BUILTIN_PALETTES[0] == ("Signature", "#231E1C", "#8C5550")


# ---- Task 1 amendment: owner-editable page Background and Text; every other neutral is derived

DARK = ("#14110F", "#F2EBE0")  # background, text


def test_default_neutrals_emit_nothing_extra():  # tokens.css stays the source of the Signature look
    assert set(theme.derive_tokens("#6b4e43", "#d9bdaa", theme.DEFAULT_BG, theme.DEFAULT_TEXT)) == {
        "--color-primary", "--color-primary-hover", "--color-primary-active",
        "--color-primary-soft", "--color-primary-contrast", "--color-accent"}


def test_custom_background_and_text_derive_the_neutral_family():
    t = theme.derive_tokens("#6b4e43", "#d9bdaa", "#FFF4F4", "#2B1B1B")
    for k in ("--color-bg", "--color-surface", "--color-cream", "--color-border", "--color-field",
              "--color-ink", "--color-muted", "--color-body", "--tile-1", "--tile-8"):
        assert theme.HEX_RE.match(t[k]), k
    assert t["--color-bg"] == "#FFF4F4" and t["--color-ink"] == "#2B1B1B"
    assert theme.contrast_ratio(t["--color-muted"], t["--color-bg"]) >= 4.5
    assert theme.contrast_ratio(t["--color-body"], t["--color-bg"]) >= 4.5
    assert "--color-gold" not in t  # bronze eyebrow text is fine on a light page


def test_dark_background_stays_readable():
    bg, txt = DARK
    t = theme.derive_tokens("#C9B28D", "#8C5550", bg, txt)
    assert theme.contrast_ratio(t["--color-muted"], bg) >= 4.5
    assert theme.contrast_ratio(t["--color-primary-contrast"], "#C9B28D") >= 4.5
    assert theme.luminance(t["--color-surface"]) > theme.luminance(bg)  # elevated surface is lighter
    assert t["--color-gold"] == theme.CHAMPAGNE  # eyebrow text switches to champagne on dark


def test_payload_stays_small_with_custom_neutrals():  # THM-06 (< 2 KB public payload)
    import json

    t = theme.derive_tokens("#6b4e43", "#d9bdaa", *DARK)
    assert len(json.dumps(t)) < 2048


def test_readability_issues_cover_text_and_primary():  # THM-07 extended
    assert theme.readability_issues("#231E1C") == []
    assert theme.readability_issues("#231E1C", "#F8F4EC", "#E8E0D0") == ["text_on_background"]
    assert theme.readability_issues("#F5EFE0", "#F8F4EC", "#231E1C") == ["primary_on_background"]
    assert theme.readability_issues("#F2EBE0", *DARK) == []  # light primary on a dark page is fine

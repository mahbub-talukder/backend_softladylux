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

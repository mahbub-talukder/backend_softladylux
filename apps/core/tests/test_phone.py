# Covers SRS ACC-14, §10 (phone validation and normalisation).
import pytest

from apps.core.phone import normalize_phone


@pytest.mark.parametrize(
    "raw",
    ["01712345678", "+8801712345678", "8801712345678", "017 1234 5678", "017-1234-5678"],
)
def test_normalizes(raw):
    assert normalize_phone(raw) == "01712345678"


@pytest.mark.parametrize("raw", ["12345", "01212345678", "0171234567", "abc"])
def test_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_empty_is_none():
    assert normalize_phone("") is None
    assert normalize_phone(None) is None

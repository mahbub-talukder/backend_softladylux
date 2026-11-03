import re

PHONE_RE = re.compile(r"^01[3-9]\d{8}$")


def normalize_phone(value: str | None) -> str | None:
    """Normalise a Bangladeshi mobile number to 01XXXXXXXXX (+880 / 880 accepted).

    SRS ACC-14 (stored normalised, unique system-wide), §10 Validation rules
    (pattern ^01[3-9]\d{8}$), CHK-01 (phone validated at checkout).

    Returns None for empty input; raises ValueError for an invalid number.
    """
    if value is None:
        return None
    raw = re.sub(r"[\s\-()]", "", str(value))
    if not raw:
        return None
    if raw.startswith("+880"):
        raw = "0" + raw[4:]
    elif raw.startswith("880") and len(raw) == 13:
        raw = "0" + raw[3:]
    if not PHONE_RE.match(raw):
        raise ValueError("Enter a valid mobile number like 01XXXXXXXXX.")
    return raw

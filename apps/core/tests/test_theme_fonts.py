# Covers SRS v1.9 THM-20..THM-26; scenarios 41–45 (API side; the browser check covers rendering).
import pytest

from apps.core import theme
from apps.core.models import AuditLog, ThemeSettings

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


def test_catalogue_has_nine_pairings_with_valid_stylesheets():  # THM-20
    assert theme.FONT_KEYS == ("signature", "editorial", "modern", "friendly", "classic",
                               "bookish", "boutique", "elegant", "clean")
    for f in theme.FONT_PAIRINGS.values():
        assert f["stylesheet"].startswith("https://fonts.googleapis.com/css2?family=")
        assert f["stylesheet"].endswith("&display=swap")
        assert f["display"].startswith(f"'{f['heading']['family']}', ")
        assert f["sans"].startswith(f"'{f['body']['family']}', ")
    clean = theme.FONT_PAIRINGS["clean"]["stylesheet"]
    assert clean.count("family=") == 1  # Inter is requested once (THM-23)


def test_defaults_are_signature_and_public_payload(api_client):  # THM-21/22/26
    t = ThemeSettings.load()
    assert (t.storefront_font, t.admin_font) == ("signature", "signature")
    fonts = api_client.get("/api/v1/theme/").data["fonts"]
    assert fonts["storefront"]["key"] == "signature"
    assert fonts["storefront"]["display"].startswith("'Cormorant Garamond'")
    assert set(fonts["storefront"]) == {"key", "display", "sans", "stylesheet", "display_adjust",
                                        "sans_adjust", "text_size"}  # + THM-29 (v1.10)
    assert fonts["admin"]["key"] == "signature"


def test_scenario_41_42_fonts_saved_on_their_own(as_user, owner, api_client):
    c = as_user(owner)
    before = c.get(f"{A}/theme/").data
    assert len(before["font_pairings"]) == 9 and before["storefront_font"] == "signature"
    r = c.put(f"{A}/theme/", {"storefront_font": "editorial"}, format="json")
    assert r.status_code == 200 and r.data["storefront_font"] == "editorial"
    assert r.data["admin_font"] == "signature" and r.data["primary_hex"] == before["primary_hex"]
    c.put(f"{A}/theme/", {"admin_font": "clean"}, format="json")
    fonts = api_client.get("/api/v1/theme/").data["fonts"]  # public cache was cleared
    assert fonts["storefront"]["display"].startswith("'Playfair Display'")
    assert fonts["storefront"]["sans"].startswith("'Inter'")
    assert fonts["admin"]["key"] == "clean"


def test_fonts_saved_with_colours(as_user, owner):  # THM-24: one Save can carry both
    r = as_user(owner).put(f"{A}/theme/", {"primary_hex": "#5B2A86", "accent_hex": "#C9A227",
                                           "storefront_font": "modern"}, format="json")
    assert r.status_code == 200 and r.data["primary_hex"] == "#5B2A86"
    assert r.data["storefront_font"] == "modern"


def test_scenario_43_validation_permission_audit(as_user, owner, make_staff):
    c = as_user(owner)
    r = c.put(f"{A}/theme/", {"storefront_font": "comic"}, format="json")
    assert r.status_code == 400 and "storefront_font" in r.data["fields"]
    assert c.put(f"{A}/theme/", {}, format="json").status_code == 400
    c.put(f"{A}/theme/", {"storefront_font": "boutique"}, format="json")
    log = AuditLog.objects.get(action="update_fonts")
    assert log.before["storefront_font"] == "signature"
    assert log.after["storefront_font"] == "boutique"
    c.put(f"{A}/theme/", {"storefront_font": "boutique"}, format="json")  # no change: no entry
    assert AuditLog.objects.filter(action="update_fonts").count() == 1
    # Last: as_user re-authenticates the shared client as this Staff user (THM-09).
    staff = as_user(make_staff("settings.manage"))
    assert staff.put(f"{A}/theme/", {"admin_font": "clean"}, format="json").status_code == 403


def test_scenario_44_reset_restores_signature_fonts(as_user, owner):
    c = as_user(owner)
    c.put(f"{A}/theme/", {"storefront_font": "elegant", "admin_font": "clean"}, format="json")
    r = c.post(f"{A}/theme/reset/")
    assert (r.data["storefront_font"], r.data["admin_font"]) == ("signature", "signature")
    assert AuditLog.objects.filter(action="reset_fonts").exists()


def test_unknown_saved_key_falls_back_to_default():  # THM-21 safety
    assert theme.font_pairing("gone")["key"] == "signature"
    assert theme.font_pairing(None)["key"] == "signature"


# ------------------------------------------------------------------ v1.10 tuning and text size


def test_signature_standard_sends_no_adjustment():  # THM-27, scenario 48
    assert theme.font_adjust("signature", "standard") == {"display_adjust": "none",
                                                          "sans_adjust": "none"}


@pytest.mark.parametrize("key", theme.FONT_KEYS)
@pytest.mark.parametrize("size", theme.TEXT_SIZE_KEYS)
def test_adjust_values_in_range_and_never_wider_than_original(key, size):  # THM-27/28/29
    a = theme.font_adjust(key, size)
    f = theme.FONT_PAIRINGS[key]
    for role, value in (("heading", a["display_adjust"]), ("body", a["sans_adjust"])):
        assert value == "none" or theme.ADJUST_MIN <= float(value) <= theme.ADJUST_MAX
        assert f[role]["factor"] <= 1.0  # width-matched: tuned text is never wider than today
    if size == "standard" and key != "signature":
        expected = f"{f['heading']['x_height'] * f['heading']['factor']:.3f}"
        assert a["display_adjust"] == expected


def test_text_size_multipliers():  # THM-28: Large grows body more than headings
    large, compact = theme.font_adjust("signature", "large"), theme.font_adjust("signature", "compact")
    assert large["sans_adjust"] == f"{0.46 * 1.06:.3f}"
    assert large["display_adjust"] == f"{0.39 * 1.03:.3f}"
    assert compact["sans_adjust"] == f"{0.46 * 0.94:.3f}"
    assert theme.font_adjust("signature", "bogus") == theme.font_adjust("signature", "standard")


def test_text_size_saved_audited_public_and_reset(as_user, owner, api_client):  # scenario 49
    c = as_user(owner)
    d = c.get(f"{A}/theme/").data
    assert d["storefront_text_size"] == "standard" and [s["key"] for s in d["text_sizes"]] == [
        "compact", "standard", "large"]
    assert c.put(f"{A}/theme/", {"admin_text_size": "huge"}, format="json").status_code == 400
    r = c.put(f"{A}/theme/", {"storefront_font": "elegant", "storefront_text_size": "large"},
              format="json")
    assert r.status_code == 200 and r.data["storefront_text_size"] == "large"
    log = AuditLog.objects.filter(action="update_fonts").latest("id")
    assert log.after["storefront_text_size"] == "large"
    pub = api_client.get("/api/v1/theme/").data["fonts"]["storefront"]
    assert pub["text_size"] == "large"
    assert pub["display_adjust"] == theme.font_adjust("elegant", "large")["display_adjust"]
    assert pub["sans_adjust"] != "none"
    c.post(f"{A}/theme/reset/")
    assert ThemeSettings.load().storefront_text_size == "standard"


def test_admin_catalogue_carries_adjust_per_size(as_user, owner):  # THM-30 previews
    p = next(x for x in as_user(owner).get(f"{A}/theme/").data["font_pairings"]
             if x["key"] == "modern")
    assert set(p["adjust"]) == {"compact", "standard", "large"}
    assert p["adjust"]["large"] == theme.font_adjust("modern", "large")

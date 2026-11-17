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
    assert set(fonts["storefront"]) == {"key", "display", "sans", "stylesheet"}
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

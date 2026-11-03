# Covers SRS SET-02/03/07/09, ACC-02 (write-only credentials), THM-02/07/08/10/13/14/18/19,
# scenarios 10 (fees editable), 15, 16, 17, 19, 27.
import pytest

from apps.core.models import AuditLog, ThemePalette

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


def test_settings_patch_audited_old_and_new(as_user, owner):  # SET-07
    c = as_user(owner)
    r = c.patch(f"{A}/settings/", {"delivery_fee_inside_dhaka": "70.00", "gtm_id": "GTM-ABC123",
                                   "bkash_number": "01711111111"}, format="json")
    assert r.status_code == 200 and r.data["delivery_fee_inside_dhaka"] == "70.00"
    log = AuditLog.objects.get(model="SiteSettings")
    assert log.before["delivery_fee_inside_dhaka"] == "60.00"
    assert log.after["delivery_fee_inside_dhaka"] == "70.00"
    assert c.get(f"{A}/settings/").data["gtm_id"] == "GTM-ABC123"


def test_settings_validation(as_user, owner):  # SET-09, SET-03
    c = as_user(owner)
    assert c.patch(f"{A}/settings/", {"gtm_id": "UA-1"}, format="json").status_code == 400
    assert c.patch(f"{A}/settings/", {"delivery_fee_outside_dhaka": "-1"},
                   format="json").status_code == 400
    assert c.patch(f"{A}/settings/", {"support_phones": "x"}, format="json").status_code == 400
    assert c.patch(f"{A}/settings/", {"support_phones": ["01711111111"]},
                   format="json").status_code == 200


def test_sms_credentials_write_only_and_not_audited(as_user, owner, api_client):  # ACC-02
    c = as_user(owner)
    r = c.patch(f"{A}/settings/", {"mimsms_api_key": "SECRET-KEY"}, format="json")
    assert "mimsms_api_key" not in r.data and r.data["mimsms_api_key_set"] is True
    assert "SECRET-KEY" not in str(AuditLog.objects.values_list("before", "after"))
    assert "SECRET-KEY" not in api_client.get("/api/v1/settings/").content.decode()


def test_staff_needs_settings_manage(as_user, make_staff):
    assert as_user(make_staff("orders.view")).get(f"{A}/settings/").status_code == 403
    assert as_user(make_staff("settings.manage")).get(f"{A}/settings/").status_code == 200


def test_fee_change_visible_publicly(as_user, owner, api_client):  # scenario 10 (settings side)
    api_client.get("/api/v1/settings/")
    as_user(owner).patch(f"{A}/settings/", {"delivery_fee_inside_dhaka": "99"}, format="json")
    assert api_client.get("/api/v1/settings/").data["delivery_fee_inside_dhaka"] == "99.00"


# ------------------------------------------------------------------ theme


def test_publish_custom_colour_updates_public_tokens(as_user, owner, api_client):  # scenario 15
    api_client.get("/api/v1/theme/")
    r = as_user(owner).put(f"{A}/theme/", {"primary_hex": "#6b4e43"}, format="json")
    assert r.status_code == 200 and r.data["primary_hex"] == "#6B4E43"
    pub = api_client.get("/api/v1/theme/").data
    assert pub["tokens"]["--color-primary"] == "#6B4E43"
    assert pub["tokens"]["--color-accent"] == "#6B4E43"  # accent optional (THM-02)


def test_palette_selection_mocha_nude(as_user, owner, api_client):  # scenario 27
    mocha = ThemePalette.objects.get(name="Mocha Nude")
    c = as_user(owner)
    r = c.put(f"{A}/theme/", {"palette": mocha.id}, format="json")
    assert r.data["palette"] == mocha.id and r.data["accent_hex"] == "#D9BDAA"
    assert api_client.get("/api/v1/theme/").data["tokens"]["--color-primary"] == "#6B4E43"


def test_invalid_hex_rejected(as_user, owner):  # THM-14
    c = as_user(owner)
    for bad in ("red", "#12", "#GGGGGG"):
        assert c.put(f"{A}/theme/", {"primary_hex": bad}, format="json").status_code == 400
    assert c.put(f"{A}/theme/", {}, format="json").status_code == 400
    assert c.put(f"{A}/theme/", {"primary_hex": "#6B4E43", "accent_hex": "zzz"},
                 format="json").status_code == 400


def test_low_contrast_requires_confirmation(as_user, owner):  # THM-07, scenario 16
    c = as_user(owner)
    body = {"primary_hex": "#F5EFE0"}
    r = c.put(f"{A}/theme/", body, format="json")
    assert r.status_code == 400 and r.data["code"] == "low_contrast"
    r = c.put(f"{A}/theme/", {**body, "confirm_low_contrast": True}, format="json")
    assert r.status_code == 200 and r.data["low_contrast_warning"] is True
    assert r.data["tokens"]["--color-primary-contrast"] in ("#231E1C", "#000000")  # flipped


def test_reset_restores_signature(as_user, owner, api_client):  # scenario 17, THM-13
    c = as_user(owner)
    c.put(f"{A}/theme/", {"primary_hex": "#1F6B54"}, format="json")
    r = c.post(f"{A}/theme/reset/")
    assert r.data["primary_hex"] == "#231E1C" and r.data["accent_hex"] == "#8C5550"
    assert api_client.get("/api/v1/theme/").data["primary"] == "#231E1C"
    assert c.get(f"{A}/theme/").data["updated_by"]


def test_theme_audited(as_user, owner):  # THM-10
    c = as_user(owner)
    c.put(f"{A}/theme/", {"primary_hex": "#1F6B54"}, format="json")
    c.post(f"{A}/theme/reset/")
    acts = list(AuditLog.objects.filter(model="ThemeSettings").values_list("action", flat=True))
    assert sorted(acts) == ["reset", "update"]


def test_theme_permission(as_user, make_staff):  # scenario 19
    assert as_user(make_staff("orders.view")).get(f"{A}/theme/").status_code == 403
    assert as_user(make_staff("theme.manage")).get(f"{A}/theme/").status_code == 200


def test_palette_library_and_custom_limit(as_user, owner):  # THM-18/19, scenario 27
    c = as_user(owner)
    lst = c.get(f"{A}/theme/palettes/").data
    assert len(lst) == 12 and all(p["is_builtin"] for p in lst)
    for i in range(5):
        r = c.post(f"{A}/theme/palettes/", {"name": f"Mine {i}", "primary_hex": "#1f2f4f",
                                            "accent_hex": "#b7c4dc"}, format="json")
        assert r.status_code == 201 and r.data["primary_hex"] == "#1F2F4F"
    assert c.post(f"{A}/theme/palettes/", {"name": "Sixth", "primary_hex": "#1F2F4F",
                                           "accent_hex": "#B7C4DC"},
                  format="json").status_code == 400
    assert c.post(f"{A}/theme/palettes/", {"name": "Bad", "primary_hex": "x",
                                           "accent_hex": "#B7C4DC"},
                  format="json").status_code == 400


def test_delete_custom_not_builtin(as_user, owner):  # THM-18/19
    c = as_user(owner)
    pid = c.post(f"{A}/theme/palettes/", {"name": "Mine", "primary_hex": "#1F2F4F",
                                          "accent_hex": "#B7C4DC"}, format="json").data["id"]
    c.put(f"{A}/theme/", {"palette": pid}, format="json")
    assert c.delete(f"{A}/theme/palettes/{pid}/").status_code == 204
    assert c.get(f"{A}/theme/").data["palette"] is None  # live colours stay, now "custom"
    sig = ThemePalette.objects.get(name="Signature")
    assert c.delete(f"{A}/theme/palettes/{sig.id}/").status_code == 400

# Covers SRS ACC-02 (amendment v1.6): SMS settings in Admin → Settings → SMS and the
# "Send test SMS" endpoint. The gateway itself is replaced by the conftest `sms_outbox`.
import pytest

from apps.core.models import AuditLog, SiteSettings

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


def test_sms_settings_fields_editable(as_user, owner):
    c = as_user(owner)
    d = c.get(f"{A}/settings/").data
    assert d["mimsms_url"] == "https://api.mimsms.com/api/V2/SMS" and d["sms_order_enabled"] is True
    assert d["mimsms_api_key_set"] is False and "mimsms_api_key" not in d
    for gone in ("mimsms_method", "mimsms_params", "mimsms_success_contains", "mimsms_sender_id"):
        assert gone not in d
    r = c.patch(f"{A}/settings/", {"mimsms_username": "shop@example.com",
                                   "mimsms_sender_name": "8809600000000",
                                   "sms_order_enabled": False}, format="json")
    assert r.status_code == 200
    s = SiteSettings.load()
    assert (s.mimsms_username, s.mimsms_sender_name, s.sms_order_enabled) == (
        "shop@example.com", "8809600000000", False)
    assert c.patch(f"{A}/settings/", {"mimsms_url": "not a url"}, format="json").status_code == 400


def test_send_test_sms(as_user, owner, sms_outbox):
    c = as_user(owner)
    r = c.post(f"{A}/settings/sms-test/", {"phone": "+8801712345678"}, format="json")
    assert r.status_code == 200 and r.data["ok"] is True and "TEST-TRXN" in r.data["detail"]
    assert sms_outbox.sent[0][0] == "01712345678" and "test SMS" in sms_outbox.sent[0][1]
    log = AuditLog.objects.get(action="sms_test")
    assert log.after == {"phone": "01712345678", "ok": True}


def test_send_test_sms_reports_gateway_error(as_user, owner, sms_outbox):
    sms_outbox.fail = "SMS rejected: Invalid API key"
    r = as_user(owner).post(f"{A}/settings/sms-test/", {"phone": "01712345678"}, format="json")
    assert r.status_code == 200 and r.data == {"ok": False, "detail": "SMS rejected: Invalid API key"}


@pytest.mark.parametrize("phone", ["", "123", None])
def test_send_test_sms_validates_phone(as_user, owner, sms_outbox, phone):
    body = {} if phone is None else {"phone": phone}
    r = as_user(owner).post(f"{A}/settings/sms-test/", body, format="json")
    assert r.status_code == 400 and sms_outbox.sent == []


def test_send_test_sms_needs_settings_manage(as_user, make_staff, sms_outbox):
    r = as_user(make_staff("orders.view")).post(f"{A}/settings/sms-test/",
                                                {"phone": "01712345678"}, format="json")
    assert r.status_code == 403 and sms_outbox.sent == []

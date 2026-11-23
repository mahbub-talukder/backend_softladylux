# Covers SRS v1.11 NTF-07 (every SMS is logged with source and status, OTP codes masked, logging
# never breaks a send) and ADM-14 (Admin → SMS log, filters, permission `sms_log.view`).
import pytest

from apps.core import sms
from apps.core.models import SmsLog

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


def test_send_logs_sent_with_provider_ref(sms_outbox):
    sms.send("01712345678", "Hello", "admin_test")
    row = SmsLog.objects.get()
    assert (row.receiver, row.message, row.source, row.status, row.provider_ref, row.attempts) == (
        "01712345678", "Hello", "admin_test", "sent", "TEST-TRXN", 1)
    assert row.sent_at is not None


def test_failed_send_is_logged_and_still_raises(sms_outbox):
    sms_outbox.fail = "SMS rejected: Invalid API key"
    with pytest.raises(sms.SmsError):
        sms.send("01712345678", "Hello", "admin_test")
    row = SmsLog.objects.get()
    assert row.status == "failed" and row.error == "SMS rejected: Invalid API key"


def test_pending_row_reused_on_retry(sms_outbox):
    log_id = sms.log_pending("01712345678", "Your code is 1234.", "otp_checkout", secret="1234")
    row = SmsLog.objects.get(pk=log_id)
    assert row.status == "pending" and row.message == "Your code is ****."
    sms_outbox.fail = "SMS gateway unreachable"
    with pytest.raises(sms.SmsError):
        sms.send("01712345678", "Your code is 1234.", "otp_checkout", log_id=log_id)
    sms_outbox.fail = None
    sms.send("01712345678", "Your code is 1234.", "otp_checkout", log_id=log_id)
    row.refresh_from_db()
    assert SmsLog.objects.count() == 1 and row.status == "sent" and row.attempts == 2
    assert row.error == "" and "1234" not in row.message


def test_logging_failure_never_blocks_the_send(sms_outbox, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(SmsLog.objects, "create", broken)
    assert sms.send("01712345678", "Hello", "admin_test") == "TEST-TRXN"
    assert sms_outbox.sent == [("01712345678", "Hello")]


def test_register_otp_is_logged_masked(sms_outbox, django_capture_on_commit_callbacks):
    from apps.accounts import otp

    with django_capture_on_commit_callbacks(execute=True):
        otp.issue("01712345678", "register")
    row = SmsLog.objects.get()
    code = sms_outbox.codes[0]
    assert row.source == "otp_register" and row.status == "sent" and code not in row.message


def test_test_sms_is_logged(as_user, owner, sms_outbox):
    as_user(owner).post(f"{A}/settings/sms-test/", {"phone": "01712345678"}, format="json")
    assert SmsLog.objects.get().source == "admin_test"


def test_admin_list_filters(as_user, owner):
    SmsLog.objects.create(receiver="01712345678", message="a", source="otp_register", status="sent")
    SmsLog.objects.create(receiver="01811111111", message="b", source="order_received",
                          status="failed")
    c = as_user(owner)
    url = f"{A}/sms-log/"
    assert c.get(url).data["count"] == 2
    assert [r["message"] for r in c.get(url, {"status": "failed"}).data["results"]] == ["b"]
    assert [r["message"] for r in c.get(url, {"source": "otp_register"}).data["results"]] == ["a"]
    assert [r["message"] for r in c.get(url, {"q": "+8801811"}).data["results"]] == ["b"]
    first = c.get(url).data["results"][0]
    assert first["source_label"] == "Order received" and first["status_label"] == "Failed"
    assert c.get(url, {"from": "2000-01-01", "to": "2999-01-01"}).data["count"] == 2
    assert c.get(url, {"from": "bad"}).status_code == 400

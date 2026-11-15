# Covers SRS ACC-02 (amendment v1.6): the MiMSMS JSON API adapter, with mocked HTTP only.
# `send_sms` is imported at module load, so it is the real function, not the conftest outbox.
import io
import json
import logging
import urllib.error

import pytest

from apps.core.models import SiteSettings
from apps.core.sms import SmsError, send_sms, to_msisdn

pytestmark = pytest.mark.django_db
OK = {"statusCode": "200", "status": "Success", "trxnId": "T123", "responseResult": "SMS Submitted"}


class Resp(io.BytesIO):
    def __init__(self, body):
        super().__init__((body if isinstance(body, str) else json.dumps(body)).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture
def configured():
    s = SiteSettings.load()
    s.mimsms_api_key, s.mimsms_username, s.mimsms_sender_name = "KEY-123", "shop@example.com", "8809600000000"
    s.save()
    return s


@pytest.fixture
def http(monkeypatch):
    """Patch urlopen; `http.reply` is what the gateway answers (dict, str or an exception)."""

    class H:
        reply = OK
        seen: dict = {}

        def __call__(self, req, timeout=None):
            self.seen.update(url=req.full_url, method=req.get_method(), timeout=timeout,
                             headers=dict(req.header_items()), body=json.loads(req.data))
            if isinstance(self.reply, Exception):
                raise self.reply
            return Resp(self.reply)

    h = H()
    monkeypatch.setattr("urllib.request.urlopen", h)
    return h


def test_to_msisdn_formats():
    assert to_msisdn("01712345678") == "8801712345678"
    assert to_msisdn("+8801712345678") == "8801712345678"
    with pytest.raises(SmsError):
        to_msisdn("12345")


def test_not_configured_sends_nothing(http):
    with pytest.raises(SmsError, match="not configured"):
        send_sms("01712345678", "hi")
    assert http.seen == {}


def test_posts_json_body_in_mimsms_shape(configured, http):
    assert send_sms("01712345678", "Your code is 123456") == "T123"
    assert http.seen["method"] == "POST" and http.seen["url"] == "https://api.mimsms.com/api/V2/SMS"
    assert http.seen["headers"]["Content-type"] == "application/json" and http.seen["timeout"] == 10
    assert http.seen["body"] == {
        "apiKey": "KEY-123", "userName": "shop@example.com", "senderName": "8809600000000",
        "transactionType": "T", "mobileNumber": "8801712345678", "message": "Your code is 123456"}


def test_admin_url_is_used(configured, http):
    configured.mimsms_url = "https://sms.example.test/api"
    configured.save()
    send_sms("01712345678", "hi")
    assert http.seen["url"] == "https://sms.example.test/api"


@pytest.mark.parametrize("reply,match", [
    ({"statusCode": "401", "responseResult": "Invalid API key"}, "Invalid API key"),
    ({"status": "Failed"}, "unknown error"),
    ("<html>oops</html>", "unexpected reply"),
    (urllib.error.HTTPError("u", 500, "err", {}, None), "HTTP 500"),
    (urllib.error.URLError("no route"), "unreachable"),
    (TimeoutError("slow"), "unreachable"),
])
def test_failures_raise_sms_error(configured, http, reply, match):
    http.reply = reply
    with pytest.raises(SmsError, match=match):
        send_sms("01712345678", "hi")


def test_ip_blacklist_explains_the_fix(configured, http):  # real reply seen on 2026-11-15
    http.reply = {"statusCode": "401", "status": "Unauthorized", "trxnId": "X", "responseResult": "IP Black List",
                  "error_Data": [{"res_Code": "401", "error": "IP Black List.", "errorParm": "103.78.226.93"}]}
    with pytest.raises(SmsError, match=r"IP Black List \(add this server's IP 103\.78\.226\.93 to the MiMSMS IP whitelist\)"):
        send_sms("01712345678", "hi")


def test_numeric_status_code_accepted(configured, http):
    http.reply = {**OK, "statusCode": 200}
    assert send_sms("01712345678", "hi") == "T123"


def test_api_key_never_logged_or_in_errors(configured, http, caplog):
    with caplog.at_level(logging.DEBUG):
        send_sms("01712345678", "hi")
        http.reply = urllib.error.URLError("down KEY?")
        with pytest.raises(SmsError) as e:
            send_sms("01712345678", "hi")
    assert "KEY-123" not in caplog.text and "KEY-123" not in str(e.value)

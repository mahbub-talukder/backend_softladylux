# Covers SRS ACC-02: the MiMSMS provider is configuration-driven (API details are not in the SRS)
# and is tested with mocked HTTP only. It stays unverified against the real service.
import io
import json
import urllib.error

import pytest

from apps.core.models import SiteSettings
from apps.core.sms import MiMsmsProvider, SmsError, get_sms_provider

pytestmark = pytest.mark.django_db
PARAMS = {"api_key": "key", "to": "number", "text": "msg", "sender": "from"}


class Resp(io.BytesIO):
    def __init__(self, body="OK", status=200):
        super().__init__(body.encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture
def configured(settings):
    settings.MIMSMS_ENDPOINT = "https://sms.example.test/send"
    settings.MIMSMS_PARAMS = json.dumps(PARAMS)
    settings.MIMSMS_API_KEY = "SECRET"
    settings.MIMSMS_SENDER_ID = "SLL"
    settings.MIMSMS_METHOD = ""
    settings.MIMSMS_SUCCESS_CONTAINS = ""


def capture(monkeypatch, resp):
    seen = {}

    def fake_open(req, timeout=None):
        seen["url"], seen["data"], seen["method"] = req.full_url, req.data, req.get_method()
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    return seen


def test_selected_by_env_and_nothing_sent_when_unconfigured(settings):
    settings.SMS_PROVIDER = "mimsms"
    assert isinstance(get_sms_provider(), MiMsmsProvider)
    with pytest.raises(SmsError, match="not configured"):
        MiMsmsProvider().send("01712345678", "hi")  # no endpoint: no guessing, no request


def test_posts_with_configured_parameter_names(monkeypatch, configured):
    seen = capture(monkeypatch, Resp("sent"))
    MiMsmsProvider().send("01712345678", "Your code is 123456")
    assert seen["method"] == "POST" and seen["url"] == "https://sms.example.test/send"
    body = seen["data"].decode()
    for part in ("key=SECRET", "number=01712345678", "from=SLL", "msg=Your+code+is+123456"):
        assert part in body


def test_get_method_puts_fields_in_the_query(monkeypatch, configured, settings):
    settings.MIMSMS_METHOD = "get"
    seen = capture(monkeypatch, Resp())
    MiMsmsProvider().send("01712345678", "hi")
    assert seen["method"] == "GET" and seen["data"] is None and "number=01712345678" in seen["url"]


def test_success_marker_http_errors_and_network_failures(monkeypatch, configured, settings):
    settings.MIMSMS_SUCCESS_CONTAINS = "success"
    capture(monkeypatch, Resp("error: bad number"))
    with pytest.raises(SmsError, match="rejected"):
        MiMsmsProvider().send("01712345678", "x")
    capture(monkeypatch, Resp("success", 200))
    MiMsmsProvider().send("01712345678", "x")
    capture(monkeypatch, Resp("boom", 500))
    with pytest.raises(SmsError):
        MiMsmsProvider().send("01712345678", "x")
    capture(monkeypatch, urllib.error.URLError("down"))
    with pytest.raises(SmsError, match="request failed"):
        MiMsmsProvider().send("01712345678", "x")


def test_settings_screen_values_used_when_env_empty(monkeypatch, settings):
    s = SiteSettings.load()
    s.mimsms_endpoint, s.mimsms_params = "https://db.example.test/s", PARAMS
    s.mimsms_api_key, s.mimsms_sender_id = "DBKEY", "DBSENDER"
    s.save()
    seen = capture(monkeypatch, Resp())
    MiMsmsProvider().send("01712345678", "hi")
    assert seen["url"] == "https://db.example.test/s" and "key=DBKEY" in seen["data"].decode()


def test_env_beats_settings_and_secrets_never_leak(monkeypatch, configured, as_user, owner):
    s = SiteSettings.load()
    s.mimsms_api_key = "DBKEY"
    s.save()
    seen = capture(monkeypatch, Resp())
    MiMsmsProvider().send("01712345678", "hi")
    assert "key=SECRET" in seen["data"].decode()
    api = as_user(owner).get("/api/v1/admin/settings/").content.decode()
    assert "DBKEY" not in api and "SECRET" not in api  # write-only credentials (ACC-02)

# Covers SRS NTF-01, NTF-02, NTF-04, NTF-06, PRM-03 (contact.manage).
import pytest
from django.core import mail
from rest_framework.throttling import SimpleRateThrottle

from apps.core.models import ContactMessage, SiteSettings, Subscriber

pytestmark = pytest.mark.django_db
B = "/api/v1"
A = "/api/v1/admin"
MSG = {"name": "Nusrat", "email": "n@example.com", "phone": "01712345678",
       "subject": "Sizing", "message": "Which size should I order?"}


@pytest.fixture(autouse=True)
def support_email(db):
    s = SiteSettings.load()
    s.support_email = "support@shop.test"
    s.save()


def test_contact_is_stored_and_emailed_to_support(api_client, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        r = api_client.post(f"{B}/contact/", MSG, format="json")
    assert r.status_code == 201
    m = ContactMessage.objects.get()
    assert (m.name, m.email, m.is_handled) == ("Nusrat", "n@example.com", False)
    assert [x.to[0] for x in mail.outbox] == ["support@shop.test"]
    assert "Which size" in mail.outbox[0].body and mail.outbox[0].reply_to == ["n@example.com"]


def test_contact_honeypot_stores_nothing(api_client):  # NTF-01 spam protection
    r = api_client.post(f"{B}/contact/", {**MSG, "website": "http://spam.example"}, format="json")
    assert r.status_code == 201 and ContactMessage.objects.count() == 0 and mail.outbox == []


@pytest.mark.parametrize("bad", [{"name": ""}, {"message": "hi"}, {"email": "nope"}, {"email": ""}])
def test_contact_validation(api_client, bad):
    assert api_client.post(f"{B}/contact/", {**MSG, **bad}, format="json").status_code == 400


def test_contact_rate_limited(api_client, monkeypatch):  # NTF-01
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "contact", "2/min")
    codes = [api_client.post(f"{B}/contact/", MSG, format="json").status_code for _ in range(3)]
    assert codes == [201, 201, 429]


def test_contact_email_failure_never_blocks(api_client, monkeypatch, django_capture_on_commit_callbacks):
    monkeypatch.setattr("apps.core.notifications._send",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")))
    with django_capture_on_commit_callbacks(execute=True):
        try:
            r = api_client.post(f"{B}/contact/", MSG, format="json")
        except RuntimeError:
            pytest.fail("the visitor must never see an email failure")
    assert r.status_code == 201 and ContactMessage.objects.count() == 1


def test_contact_without_support_email_is_still_stored(api_client, django_capture_on_commit_callbacks):
    SiteSettings.objects.update(support_email="")
    with django_capture_on_commit_callbacks(execute=True):
        assert api_client.post(f"{B}/contact/", MSG, format="json").status_code == 201
    assert ContactMessage.objects.count() == 1 and mail.outbox == []


def test_newsletter_signup_idempotent_with_welcome_email(api_client, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        a = api_client.post(f"{B}/newsletter/", {"email": "Fan@Example.com"}, format="json")
        b = api_client.post(f"{B}/newsletter/", {"email": "fan@example.com"}, format="json")
    assert (a.status_code, b.status_code) == (201, 200) and Subscriber.objects.count() == 1
    assert [m.to[0] for m in mail.outbox] == ["fan@example.com"]  # welcome sent once
    assert Subscriber.objects.get().email == "fan@example.com"
    for bad in ({}, {"email": "nope"}):
        assert api_client.post(f"{B}/newsletter/", bad, format="json").status_code == 400


def test_admin_contact_messages_list_filter_mark_handled(as_user, owner):
    ContactMessage.objects.create(name="A", email="a@example.com", message="first message")
    ContactMessage.objects.create(name="B", email="b@example.com", message="second message")
    c = as_user(owner)
    rows = c.get(f"{A}/contact-messages/").data
    assert rows["count"] == 2
    mid = rows["results"][0]["id"]
    assert c.get(f"{A}/contact-messages/{mid}/").status_code == 200
    r = c.patch(f"{A}/contact-messages/{mid}/", {"is_handled": True, "message": "tampered"},
                format="json")
    assert r.data["is_handled"] is True and r.data["handled_by_name"] and r.data["message"] != "tampered"
    assert c.get(f"{A}/contact-messages/", {"handled": "1"}).data["count"] == 1
    assert c.get(f"{A}/contact-messages/", {"handled": "0"}).data["count"] == 1
    c.patch(f"{A}/contact-messages/{mid}/", {"is_handled": False}, format="json")
    assert ContactMessage.objects.get(pk=mid).handled_by is None


def test_admin_newsletter_list_and_csv_export(as_user, owner):  # NTF-04
    Subscriber.objects.create(email="one@example.com")
    Subscriber.objects.create(email="two@example.com")
    c = as_user(owner)
    assert c.get(f"{A}/newsletter/").data["count"] == 2
    csv_resp = c.get(f"{A}/newsletter/export/")
    body = csv_resp.content.decode()
    assert csv_resp["Content-Type"].startswith("text/csv") and "one@example.com" in body
    assert body.splitlines()[0] == "email,subscribed_at"


def test_contact_admin_needs_contact_manage(as_user, make_staff):  # §2.2
    assert as_user(make_staff("orders.view")).get(f"{A}/contact-messages/").status_code == 403
    assert as_user(make_staff("orders.view")).get(f"{A}/newsletter/").status_code == 403
    s = as_user(make_staff("contact.manage"))
    assert s.get(f"{A}/contact-messages/").status_code == 200
    assert s.get(f"{A}/newsletter/export/").status_code == 200

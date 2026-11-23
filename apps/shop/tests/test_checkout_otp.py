# Covers SRS v1.11 CHK-11 (guest mobile verification by 4-digit SMS OTP, verified numbers skip,
# limits) and scenarios 51–54.
import pytest

from apps.accounts.models import Role, User, VerifiedPhone
from apps.core.models import SiteSettings, SmsLog

from .helpers import configure_site, one_variant, payload

pytestmark = pytest.mark.django_db
B = "/api/v1"
PHONE = "01712345678"


@pytest.fixture(autouse=True)
def site(db, checkout_otp):
    return configure_site()


@pytest.fixture
def send(api_client, django_capture_on_commit_callbacks):
    def go(phone=PHONE):
        with django_capture_on_commit_callbacks(execute=True):
            return api_client.post(f"{B}/checkout/otp/", {"phone": phone}, format="json")
    return go


def order(api_client, phone=PHONE):
    return api_client.post(f"{B}/checkout/", payload(one_variant(), phone=phone), format="json")


def test_scenario_51_52_new_guest_verifies_then_skips(api_client, send, sms_outbox):
    r = send()
    assert r.status_code == 202 and r.data["status"] == "sent" and r.data["digits"] == 4
    code = sms_outbox.codes[0]
    assert len(code) == 4
    assert SmsLog.objects.get().source == "otp_checkout" and code not in SmsLog.objects.get().message
    r = api_client.post(f"{B}/checkout/otp/verify/", {"phone": PHONE, "code": code}, format="json")
    assert r.status_code == 200 and VerifiedPhone.objects.filter(phone=PHONE).exists()
    assert order(api_client).status_code == 201
    # scenario 52: next time no SMS is sent
    assert send().data["status"] == "verified" and len(sms_outbox.sent) == 1


def test_scenario_53_three_wrong_codes_kill_the_code(api_client, send, sms_outbox):
    send()
    code = sms_outbox.codes[0]
    wrong = "0000" if code != "0000" else "1111"
    for left in ("2 attempts left", "1 attempt left", "Too many wrong attempts"):
        r = api_client.post(f"{B}/checkout/otp/verify/", {"phone": PHONE, "code": wrong},
                            format="json")
        assert r.status_code == 400 and left in str(r.data)
    r = api_client.post(f"{B}/checkout/otp/verify/", {"phone": PHONE, "code": code}, format="json")
    assert r.status_code == 400 and not VerifiedPhone.objects.exists()


def test_scenario_53_resend_wait(send, sms_outbox):
    assert send().status_code == 202
    assert send().status_code == 429 and len(sms_outbox.sent) == 1


def test_scenario_54_unverified_guest_order_refused(api_client):
    r = order(api_client)
    assert r.status_code == 409 and r.data["code"] == "phone_unverified"


def test_registered_verified_number_skips(api_client, send, sms_outbox):
    User.objects.create_user(phone=PHONE, password="x" * 10, role=Role.CUSTOMER,
                             phone_verified=True)
    assert send().data["status"] == "verified" and sms_outbox.sent == []
    assert order(api_client).status_code == 201


def test_owner_can_switch_it_off(api_client, send, sms_outbox):
    SiteSettings.objects.update(checkout_otp_enabled=False)
    assert send().data["status"] == "verified" and sms_outbox.sent == []
    assert order(api_client).status_code == 201


def test_bad_phone(api_client):
    assert api_client.post(f"{B}/checkout/otp/", {"phone": "12"}, format="json").status_code == 400
    assert api_client.post(f"{B}/checkout/otp/verify/", {}, format="json").status_code == 400

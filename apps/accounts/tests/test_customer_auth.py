# Covers SRS ACC-01..ACC-03, ACC-10..ACC-14, ARC-06; scenarios 12 and 29.
import pytest
from django.db import connection  # noqa: F401
from rest_framework.test import APIClient

from apps.accounts.models import Otp, Role, User
from apps.shop.checkout import place_order
from apps.shop.tests.helpers import configure_site, one_variant, payload

from .helpers import REG, age_otps, catch_codes, stop

pytestmark = pytest.mark.django_db
B = "/api/v1"


@pytest.fixture
def codes():
    h = catch_codes()
    yield h.codes
    stop(h)


@pytest.fixture
def capture(django_capture_on_commit_callbacks):
    """A fresh context manager per use: runs the on_commit hook that queues the SMS."""
    return lambda: django_capture_on_commit_callbacks(execute=True)


def register(client, capture, **over):
    with capture():
        return client.post(f"{B}/auth/register/", {**REG, **over}, format="json")


def test_scenario_12_register_verify_login_and_cookie_flags(api_client, codes, capture):
    r = register(api_client, capture)
    assert r.status_code == 202 and r.data["phone"] == "01712345678" and r.data["resend_in"] == 60
    u = User.objects.get(phone="01712345678")
    assert u.is_active is False and u.phone_verified is False and u.role == Role.CUSTOMER
    assert len(codes) == 1 and len(codes[0]) == 6
    v = api_client.post(f"{B}/auth/register/verify/", {"phone": "01712345678", "code": codes[0]},
                        format="json")
    assert v.status_code == 201 and v.data["full_name"] == "Nusrat Jahan"
    assert v.cookies["sll_access"]["httponly"] and "sll_refresh" in v.cookies
    u.refresh_from_db()
    assert u.is_active and u.phone_verified
    api_client.cookies.clear()
    login = api_client.post(f"{B}/auth/login/", {"phone": "+8801712345678",
                                                 "password": "secret-pass-1"}, format="json")
    assert login.status_code == 200 and login.data["phone"] == "01712345678"
    assert api_client.get(f"{B}/me/").data["email"] == "nusrat@example.com"


def test_code_is_stored_hashed_never_plain(api_client, codes, capture):
    register(api_client, capture)
    o = Otp.objects.get()
    assert codes[0] not in o.code_hash and len(o.code_hash) == 64
    assert o.sms_status == "sent"  # queued task ran (sync) and the fake provider "sent" it


def test_wrong_codes_lock_after_five_attempts_scenario_12(api_client, codes, capture):
    register(api_client, capture)
    body = {"phone": "01712345678", "code": "000000" if codes[0] != "000000" else "111111"}
    msgs = [api_client.post(f"{B}/auth/register/verify/", body, format="json") for _ in range(5)]
    assert all(m.status_code == 400 for m in msgs)
    assert "4 attempts left" in str(msgs[0].data["fields"]) and "Too many" in str(msgs[4].data)
    # even the right code no longer works: a new code is needed
    ok = api_client.post(f"{B}/auth/register/verify/",
                         {"phone": "01712345678", "code": codes[0]}, format="json")
    assert ok.status_code == 400
    assert User.objects.get(phone="01712345678").phone_verified is False


def test_expired_code_rejected(api_client, codes, capture):
    from datetime import timedelta

    from django.utils import timezone

    register(api_client, capture)
    Otp.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    r = api_client.post(f"{B}/auth/register/verify/", {"phone": "01712345678", "code": codes[0]},
                        format="json")
    assert r.status_code == 400 and "expired" in str(r.data)


def test_resend_after_60_seconds_only_and_old_code_stops_working(api_client, codes, capture):
    register(api_client, capture)
    second = register(api_client, capture)
    assert second.status_code == 429  # < 60 s
    assert len(codes) == 1  # nothing was sent
    age_otps("01712345678")
    assert register(api_client, capture).status_code == 202
    assert len(codes) == 2
    old = api_client.post(f"{B}/auth/register/verify/",
                          {"phone": "01712345678", "code": codes[0]}, format="json")
    assert old.status_code == 400  # replaced by the newer code
    new = api_client.post(f"{B}/auth/register/verify/",
                          {"phone": "01712345678", "code": codes[1]}, format="json")
    assert new.status_code == 201


def test_hourly_and_daily_caps_send_nothing_when_exceeded(api_client, codes, capture):
    for _ in range(5):
        register(api_client, capture)
        age_otps("01712345678")
    assert len(codes) == 5
    r = register(api_client, capture)
    assert r.status_code == 429 and "hour" in str(r.data) and len(codes) == 5  # 6th in the hour
    from datetime import timedelta

    from django.utils import timezone

    Otp.objects.update(sent_at=timezone.now() - timedelta(hours=2))  # past the hour, inside the day
    for _ in range(5):
        register(api_client, capture)
        age_otps("01712345678", 3600 * 2)
    assert len(codes) == 10
    assert "tomorrow" in str(register(api_client, capture).data) and len(codes) == 10


def test_scenario_29_duplicate_mobile_rejected_for_customers_and_staff(api_client, codes, capture,
                                                                        owner, make_staff):
    first = register(api_client, capture)
    v = api_client.post(f"{B}/auth/register/verify/", {"phone": "01712345678", "code": codes[0]},
                        format="json")
    assert first.status_code == 202 and v.status_code == 201
    api_client.cookies.clear()
    again = register(api_client, capture, email="other@example.com")
    assert again.status_code == 400 and "already registered" in str(again.data["fields"]["phone"])
    s = make_staff("orders.view")
    s.phone = "01899999999"
    s.save()
    assert register(api_client, capture, phone="01899999999").status_code == 400  # staff number


def test_unverified_number_can_re_register_with_new_details(api_client, codes, capture):
    register(api_client, capture)
    age_otps("01712345678")
    r = register(api_client, capture, full_name="Changed Name", password="another-pass-9")
    assert r.status_code == 202 and User.objects.count() == 1
    v = api_client.post(f"{B}/auth/register/verify/", {"phone": "01712345678", "code": codes[-1]},
                        format="json")
    assert v.data["full_name"] == "Changed Name"


@pytest.mark.parametrize("bad,field", [
    ({"full_name": ""}, "full_name"), ({"phone": "123"}, "phone"), ({"phone": ""}, "phone"),
    ({"password": "short"}, "password"), ({"password": "01712345678"}, "password"),
    ({"email": "nope"}, "email"),
])
def test_registration_validation_sends_no_sms(api_client, codes, capture, bad, field):
    r = register(api_client, capture, **bad)
    assert r.status_code == 400 and field in r.data["fields"]
    assert codes == [] and User.objects.count() == 0


def test_email_optional_and_unique(api_client, codes, capture):
    assert register(api_client, capture, email="").status_code == 202
    User.objects.create_user(phone="01811111111", email="taken@example.com", password="x-pass-1234")
    r = register(api_client, capture, phone="01822222222", email="taken@example.com")
    assert r.status_code == 400 and "email" in r.data["fields"]


def test_verify_without_registration(api_client):
    r = api_client.post(f"{B}/auth/register/verify/", {"phone": "01712345678", "code": "123456"},
                        format="json")
    assert r.status_code == 400


def test_otp_status_reports_sms_failure(api_client, capture, monkeypatch):  # ACC-02
    from apps.core.sms import fake

    def boom(self, phone, message):
        from apps.core.sms import SmsError

        raise SmsError("gateway down")

    monkeypatch.setattr(fake.FakeSmsProvider, "send", boom)
    try:
        register(api_client, capture)
    except Exception:  # noqa: BLE001 - the sync test queue re-raises; real queue retries
        pass
    s = api_client.get(f"{B}/auth/otp-status/", {"phone": "01712345678", "purpose": "register"})
    assert s.data["status"] == "failed"
    assert api_client.get(f"{B}/auth/otp-status/", {"phone": "01999999999"}).data["status"] == "none"
    assert api_client.get(f"{B}/auth/otp-status/", {"phone": "bad"}).status_code == 400


# ------------------------------------------------------------------ login / refresh / logout


@pytest.fixture
def customer(db):
    return User.objects.create_user(phone="01712345678", password="secret-pass-1",
                                    full_name="Nusrat", is_active=True, phone_verified=True)


def login(client, password="secret-pass-1", phone="01712345678", **kw):
    return client.post(f"{B}/auth/login/", {"phone": phone, "password": password, **kw},
                       format="json")


def test_login_errors_are_generic(api_client, customer):
    for body in ({"password": "wrong-pass"}, {"phone": "01999999999"}, {"phone": "bad"}):
        r = login(api_client, **body)
        assert r.status_code == 401 and r.data["code"] == "invalid_credentials"


def test_login_locks_after_five_failures_for_fifteen_minutes(api_client, customer):  # ACC-03
    for _ in range(5):
        login(api_client, "bad")
    assert login(api_client).status_code == 429
    from datetime import timedelta

    from django.utils import timezone

    User.objects.filter(pk=customer.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    assert login(api_client).status_code == 200


def test_remember_me_controls_cookie_lifetime(api_client, customer):  # ACC-03
    long = login(api_client, remember=True)
    assert long.cookies["sll_refresh"]["max-age"]
    short = login(api_client, remember=False)
    assert not short.cookies["sll_refresh"]["max-age"]  # session cookie


def test_unverified_or_inactive_customer_cannot_log_in(api_client, customer):
    User.objects.filter(pk=customer.pk).update(phone_verified=False)
    assert login(api_client).status_code == 401
    User.objects.filter(pk=customer.pk).update(phone_verified=True, is_active=False)
    assert login(api_client).status_code == 401


def test_staff_cannot_use_customer_login_and_customer_cannot_use_admin(api_client, owner):  # ACC-10
    owner.phone = "01877777777"
    owner.save()
    assert login(api_client, "owner-pass-123", "01877777777").status_code == 401
    c = User.objects.create_user(phone="01712345678", email="c@example.com",
                                 password="secret-pass-1", is_active=True, phone_verified=True)
    r = api_client.post(f"{B}/admin/auth/login/", {"email": c.email, "password": "secret-pass-1"},
                        format="json")
    assert r.status_code == 401


def test_refresh_and_logout(api_client, customer):
    login(api_client)
    assert api_client.post(f"{B}/auth/refresh/").status_code == 200
    assert api_client.post(f"{B}/auth/logout/").status_code == 204
    api_client.cookies.clear()
    assert api_client.post(f"{B}/auth/refresh/").status_code == 401


# ------------------------------------------------------------------ forgot / reset


def test_scenario_12_forgot_and_reset_signs_out_other_sessions(customer, codes, capture):
    phone = {"phone": "01712345678"}
    other = APIClient()
    other_login = login(other)
    assert other.get(f"{B}/me/").status_code == 200  # session on another device
    this = APIClient()
    with capture():
        f = this.post(f"{B}/auth/password/forgot/", phone, format="json")
    assert f.status_code == 202 and len(codes) == 1
    bad = this.post(f"{B}/auth/password/reset/", {**phone, "code": "000000" if codes[0] != "000000"
                    else "111111", "new_password": "brand-new-pass-2"}, format="json")
    assert bad.status_code == 400
    r = this.post(f"{B}/auth/password/reset/", {**phone, "code": codes[0],
                                                "new_password": "brand-new-pass-2"}, format="json")
    assert r.status_code == 200 and "sll_access" in r.cookies  # this device is signed in
    assert other.get(f"{B}/me/").status_code in (401, 403)  # ACC-11: old session is dead
    assert other.post(f"{B}/auth/refresh/").status_code == 401
    assert login(APIClient(), "secret-pass-1").status_code == 401
    assert login(APIClient(), "brand-new-pass-2").status_code == 200
    assert other_login.status_code == 200


def test_forgot_is_silent_for_unknown_numbers_and_sends_no_sms(api_client, codes, capture):
    with capture():
        r = api_client.post(f"{B}/auth/password/forgot/", {"phone": "01999999999"}, format="json")
    assert r.status_code == 202 and codes == []  # same answer, no SMS cost, no probing
    assert api_client.post(f"{B}/auth/password/forgot/", {"phone": "bad"},
                           format="json").status_code == 400


def test_reset_needs_valid_code_rules_and_unknown_phone(api_client, customer, codes, capture):
    with capture():
        api_client.post(f"{B}/auth/password/forgot/", {"phone": "01712345678"}, format="json")
    body = {"phone": "01712345678", "code": codes[0]}
    assert api_client.post(f"{B}/auth/password/reset/", {**body, "new_password": "short"},
                           format="json").status_code == 400
    assert api_client.post(f"{B}/auth/password/reset/", {**body, "new_password": "01712345678"},
                           format="json").status_code == 400
    assert api_client.post(f"{B}/auth/password/reset/", {"phone": "01999999999", "code": "1",
                           "new_password": "valid-pass-123"}, format="json").status_code == 400
    ok = api_client.post(f"{B}/auth/password/reset/", {**body, "new_password": "valid-pass-123"},
                         format="json")
    assert ok.status_code == 200  # the code was not consumed by the failed attempts above
    reuse = api_client.post(f"{B}/auth/password/reset/", {**body, "new_password": "valid-pass-456"},
                            format="json")
    assert reuse.status_code == 400  # a code works once


def test_reset_clears_a_login_lock(api_client, customer, codes, capture):
    for _ in range(5):
        login(api_client, "bad")
    with capture():
        api_client.post(f"{B}/auth/password/forgot/", {"phone": "01712345678"}, format="json")
    api_client.post(f"{B}/auth/password/reset/", {"phone": "01712345678", "code": codes[0],
                                                  "new_password": "valid-pass-123"}, format="json")
    assert login(APIClient(), "valid-pass-123").status_code == 200


# ------------------------------------------------------------------ ACC-13 guest orders


def test_scenario_29_guest_orders_link_on_verified_registration_and_tracking_still_works(
        api_client, codes, capture):
    configure_site()
    v = one_variant(on_hand=9)
    guest, _ = place_order(payload(v))  # phone 01712345678, no account
    other, _ = place_order(payload(v, phone="01999999999"))
    register(api_client, capture)
    assert guest.__class__.objects.get(pk=guest.pk).user is None  # not linked until verified
    api_client.post(f"{B}/auth/register/verify/", {"phone": "01712345678", "code": codes[0]},
                    format="json")
    guest.refresh_from_db()
    other.refresh_from_db()
    assert guest.user is not None and other.user is None
    mine = api_client.get(f"{B}/orders/").data
    assert [o["number"] for o in mine["results"]] == [guest.number]
    anon = APIClient()
    t = anon.post(f"{B}/track/", {"number": guest.number, "phone": "01712345678"}, format="json")
    assert t.status_code == 200  # guest tracking still works after linking


# ------------------------------------------------------------------ rate limits (§9)


@pytest.mark.parametrize("scope,call", [
    ("login", lambda c: c.post(f"{B}/auth/login/", {"phone": "01712345678", "password": "x"},
                               format="json")),
    ("forgot", lambda c: c.post(f"{B}/auth/password/forgot/", {"phone": "01712345678"},
                                format="json")),
    ("otp_ip", lambda c: c.post(f"{B}/auth/register/verify/", {"phone": "01712345678",
                                                               "code": "123456"}, format="json")),
])
def test_customer_auth_endpoints_are_rate_limited(api_client, monkeypatch, scope, call):
    from rest_framework.throttling import SimpleRateThrottle

    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, scope, "2/min")
    codes = [call(api_client).status_code for _ in range(3)]
    assert codes[2] == 429 and 429 not in codes[:2]


def test_admin_login_is_rate_limited(api_client, monkeypatch):
    from rest_framework.throttling import SimpleRateThrottle

    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "login", "2/min")
    body = {"email": "nobody@example.com", "password": "x"}
    codes = [api_client.post(f"{B}/admin/auth/login/", body, format="json").status_code
             for _ in range(3)]
    assert codes == [401, 401, 429]

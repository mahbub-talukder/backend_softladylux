# Covers SRS ACC-10, ACC-03 (lock), ADM-06, PRM-02..PRM-07, scenarios 11 and 28.
import pytest

from apps.accounts.models import Role, User
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db
BASE = "/api/v1/admin"


def test_login_sets_cookies_and_me(api_client, owner):
    r = api_client.post(f"{BASE}/auth/login/", {"email": "OWNER@example.com",
                                                "password": "owner-pass-123"}, format="json")
    assert r.status_code == 200 and r.data["role"] == "owner"
    assert "sll_access" in r.cookies and r.cookies["sll_access"]["httponly"]
    assert len(r.data["permissions"]) == 15


def test_customer_cannot_use_admin_login(api_client):  # ACC-10
    User.objects.create_user(email="c@example.com", phone="01712345678", password="cust-pass-123")
    r = api_client.post(f"{BASE}/auth/login/", {"email": "c@example.com",
                                                "password": "cust-pass-123"}, format="json")
    assert r.status_code == 401


def test_login_locks_after_five_failures(api_client, owner):  # ACC-03
    for _ in range(5):
        api_client.post(f"{BASE}/auth/login/", {"email": owner.email, "password": "bad"},
                        format="json")
    r = api_client.post(f"{BASE}/auth/login/", {"email": owner.email,
                                                "password": "owner-pass-123"}, format="json")
    assert r.status_code == 429


def test_refresh_and_logout(api_client, owner):
    api_client.post(f"{BASE}/auth/login/", {"email": owner.email,
                                            "password": "owner-pass-123"}, format="json")
    assert api_client.post(f"{BASE}/auth/refresh/").status_code == 200
    r = api_client.post(f"{BASE}/auth/logout/")
    assert r.status_code == 204
    api_client.cookies.clear()
    assert api_client.post(f"{BASE}/auth/refresh/").status_code == 401


def test_refresh_refused_after_deactivation(api_client, make_staff):  # PRM-07
    u = make_staff("orders.view")
    api_client.post(f"{BASE}/auth/login/", {"email": u.email, "password": "staff-pass-123"},
                    format="json")
    u.is_active = False
    u.save()
    assert api_client.post(f"{BASE}/auth/refresh/").status_code == 401


def test_me_requires_staff(api_client, as_user, make_staff):
    assert api_client.get(f"{BASE}/me/").status_code in (401, 403)
    c = User.objects.create_user(phone="01712345678", password="cust-pass-123")
    assert as_user(c).get(f"{BASE}/me/").status_code == 403
    s = make_staff("orders.view")
    assert as_user(s).get(f"{BASE}/me/").data["permissions"] == ["orders.view"]


def test_catalogue_owner_only(as_user, owner, make_staff):  # §2.2
    assert len(as_user(owner).get(f"{BASE}/permissions/").data) == 15
    assert as_user(make_staff("settings.manage")).get(f"{BASE}/permissions/").status_code == 403


def test_owner_creates_staff_with_defaults(as_user, owner):  # PRM-01, ADM-06
    r = as_user(owner).post(f"{BASE}/users/", {"full_name": "Ana", "email": "ana@example.com",
                                               "password": "ana-pass-1234"}, format="json")
    assert r.status_code == 201 and r.data["role"] == "staff"
    assert "orders.manage" in r.data["permissions"] and "theme.manage" not in r.data["permissions"]
    assert AuditLog.objects.filter(model="User", action="create").exists()


def test_create_staff_rejects_dup_email_and_weak_password(as_user, owner):
    c = as_user(owner)
    body = {"full_name": "A", "email": owner.email, "password": "ana-pass-1234"}
    assert c.post(f"{BASE}/users/", body, format="json").status_code == 400
    body = {"full_name": "A", "email": "a@example.com", "password": "123"}
    assert c.post(f"{BASE}/users/", body, format="json").status_code == 400


def test_staff_cannot_manage_users(as_user, make_staff):  # scenario 28
    s = make_staff("settings.manage", "theme.manage")
    c = as_user(s)
    assert c.get(f"{BASE}/users/").status_code == 403
    assert c.get(f"{BASE}/audit-log/").status_code == 403


def test_list_and_retrieve_users(as_user, owner, make_staff):
    s = make_staff("orders.view")
    c = as_user(owner)
    assert c.get(f"{BASE}/users/").data["count"] == 2
    assert c.get(f"{BASE}/users/{s.id}/").data["email"] == s.email


def test_deactivate_and_owner_protection(as_user, owner, make_staff):  # PRM-06/07
    s = make_staff("orders.view")
    c = as_user(owner)
    assert c.patch(f"{BASE}/users/{s.id}/", {"is_active": False}, format="json").status_code == 200
    s.refresh_from_db()
    assert s.is_active is False
    assert c.patch(f"{BASE}/users/{owner.id}/", {"is_active": False},
                   format="json").status_code == 403
    assert c.post(f"{BASE}/users/{owner.id}/reset-password/", {"password": "new-pass-1234"},
                  format="json").status_code == 403
    assert c.put(f"{BASE}/users/{owner.id}/permissions/", {"permissions": []},
                 format="json").status_code == 403
    assert len(c.get(f"{BASE}/users/{owner.id}/permissions/").data["permissions"]) == 15
    owner.refresh_from_db()
    assert owner.is_active and owner.role == Role.OWNER


def test_deactivated_staff_blocked_on_next_request(as_user, make_staff):  # PRM-07
    s = make_staff("orders.view")
    s.is_active = False
    s.save()
    assert as_user(s).get(f"{BASE}/me/").status_code in (401, 403)


def test_reset_password(as_user, owner, make_staff, api_client):
    s = make_staff("orders.view")
    c = as_user(owner)
    assert c.post(f"{BASE}/users/{s.id}/reset-password/", {"password": "short"},
                  format="json").status_code == 400
    assert c.post(f"{BASE}/users/{s.id}/reset-password/", {"password": "fresh-pass-1234"},
                  format="json").status_code == 204
    s.refresh_from_db()
    assert s.check_password("fresh-pass-1234")
    log = AuditLog.objects.get(action="reset_password")
    assert "fresh" not in str(log.after) and "fresh" not in str(log.before)


def test_permission_change_takes_effect_next_request(as_user, owner, make_staff, api_client):
    """Scenario 28 / PRM-03: no re-login needed."""
    s = make_staff("settings.manage")
    staff_client = as_user(s)
    assert staff_client.get(f"{BASE}/settings/").status_code == 200
    from rest_framework.test import APIClient
    oc = APIClient()
    oc.force_authenticate(owner)
    r = oc.put(f"{BASE}/users/{s.id}/permissions/", {"permissions": ["coupons.manage"]},
               format="json")
    assert r.status_code == 200 and r.data["permissions"] == ["coupons.manage"]
    assert staff_client.get(f"{BASE}/settings/").status_code == 403
    assert staff_client.get(f"{BASE}/me/").data["permissions"] == ["coupons.manage"]
    assert AuditLog.objects.filter(action="permissions", object_id=str(s.id)).exists()  # PRM-07


def test_put_permissions_adds_dependencies_and_validates(as_user, owner, make_staff):  # PRM-05
    s = make_staff("orders.view")
    c = as_user(owner)
    r = c.put(f"{BASE}/users/{s.id}/permissions/", {"permissions": ["orders.manage"]},
              format="json")
    assert r.data["permissions"] == ["orders.manage", "orders.view"]
    assert c.put(f"{BASE}/users/{s.id}/permissions/", {"permissions": "x"},
                 format="json").status_code == 400
    assert c.get(f"{BASE}/users/{s.id}/permissions/").status_code == 200


def test_put_user_row_not_allowed(as_user, owner, make_staff):
    s = make_staff("orders.view")
    assert as_user(owner).put(f"{BASE}/users/{s.id}/", {}, format="json").status_code == 405


def test_login_sets_readable_csrf_cookie_and_writes_need_it(owner):
    """ARC-06: cookie-authenticated writes need X-CSRFToken, so login must hand the SPA a
    JS-readable csrftoken cookie; without the header the write is refused."""
    from rest_framework.test import APIClient

    c = APIClient(enforce_csrf_checks=True)
    r = c.post(f"{BASE}/auth/login/", {"email": owner.email, "password": "owner-pass-123"},
               format="json")
    token = r.cookies["csrftoken"].value
    assert not r.cookies["csrftoken"]["httponly"]
    assert c.patch(f"{BASE}/settings/", {"tagline": "x"}, format="json").status_code == 403
    ok = c.patch(f"{BASE}/settings/", {"tagline": "x"}, format="json", HTTP_X_CSRFTOKEN=token)
    assert ok.status_code == 200

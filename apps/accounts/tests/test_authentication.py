# Covers SRS ARC-06 (JWT cookies + CSRF), ACC-03 (session lifetime),
# PRM-07 (immediate deactivation).
import pytest
from django.http import HttpResponse
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.authentication import CookieJWTAuthentication
from apps.accounts.models import User
from apps.accounts.tokens import clear_auth_cookies, set_auth_cookies

pytestmark = pytest.mark.django_db
factory = APIRequestFactory(enforce_csrf_checks=True)
auth = CookieJWTAuthentication()


@pytest.fixture
def user():
    return User.objects.create_user(phone="01712345678", password="secret123")


def _req(method, token=None, cookie=None, **extra):
    req = getattr(factory, method)("/x/", **extra)
    if token:
        req.META["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    if cookie:
        req.COOKIES["sll_access"] = cookie
    return req


def test_no_credentials_returns_none():
    assert auth.authenticate(_req("get")) is None


def test_bearer_header_works_without_csrf(user):
    token = str(AccessToken.for_user(user))
    assert auth.authenticate(_req("post", token=token))[0] == user


def test_cookie_get_works(user):
    token = str(AccessToken.for_user(user))
    assert auth.authenticate(_req("get", cookie=token))[0] == user


def test_cookie_unsafe_method_requires_csrf(user):
    token = str(AccessToken.for_user(user))
    with pytest.raises(exceptions.PermissionDenied):
        auth.authenticate(_req("post", cookie=token))


def test_invalid_token_rejected():
    with pytest.raises(exceptions.AuthenticationFailed):
        auth.authenticate(_req("get", cookie="garbage"))


def test_inactive_user_rejected_immediately(user):
    token = str(AccessToken.for_user(user))
    user.is_active = False
    user.save()
    with pytest.raises(exceptions.AuthenticationFailed):
        auth.authenticate(_req("get", cookie=token))


def test_set_and_clear_cookies(user):
    resp = set_auth_cookies(HttpResponse(), user)
    for name in ("sll_access", "sll_refresh"):
        assert resp.cookies[name]["httponly"]
        assert resp.cookies[name]["max-age"]
    session_only = set_auth_cookies(HttpResponse(), user, remember=False)
    assert not session_only.cookies["sll_refresh"]["max-age"]
    cleared = clear_auth_cookies(HttpResponse())
    assert cleared.cookies["sll_access"]["max-age"] == 0

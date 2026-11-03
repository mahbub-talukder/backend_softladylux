# Covers SRS PRM-03 / PRM-08: every admin endpoint declares a permission; a user with it
# succeeds and a user without it gets 403.
import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from apps.accounts.permissions import IsOwner, IsStaffUser

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"
OPEN_VIEWS = {"AdminLoginView", "AdminRefreshView", "AdminLogoutView"}  # pre-auth by design


def _admin_views(patterns=None, prefix=""):
    patterns = patterns if patterns is not None else get_resolver().url_patterns
    for p in patterns:
        route = prefix + str(p.pattern)
        if isinstance(p, URLResolver):
            yield from _admin_views(p.url_patterns, route)
        elif isinstance(p, URLPattern) and route.startswith("api/v1/admin/"):
            cb = p.callback
            yield route, getattr(cb, "cls", None) or cb.view_class


def test_every_admin_view_declares_a_permission():  # PRM-03
    seen = 0
    for route, cls in _admin_views():
        if cls.__name__ in OPEN_VIEWS:
            continue
        seen += 1
        assert any(issubclass(pc, (IsStaffUser, IsOwner)) for pc in cls.permission_classes), (
            f"{route} has no staff permission")
    assert seen > 15


# (method, path, permission code needed). Detail rows use ids created in `seed`.
MATRIX = [
    ("get", "/settings/", "settings.manage"),
    ("get", "/theme/", "theme.manage"),
    ("get", "/theme/palettes/", "theme.manage"),
    ("get", "/website/carousel/", "website.manage"),
    ("get", "/website/banners/", "website.manage"),
    ("get", "/website/videos/", "website.manage"),
    ("get", "/website/announcements/", "website.manage"),
    ("get", "/website/trust/", "website.manage"),
    ("get", "/website/pages/", "website.manage"),
    ("post", "/theme/reset/", "theme.manage"),
    ("post", "/media/", "website.manage"),
]


@pytest.mark.parametrize("method,path,code", MATRIX)
def test_matrix_granted_vs_denied(as_user, make_staff, method, path, code):
    other = "orders.view"
    denied = getattr(as_user(make_staff(other)), method)(f"{A}{path}", format="json")
    assert denied.status_code == 403
    allowed = getattr(as_user(make_staff(code)), method)(f"{A}{path}", format="json")
    assert allowed.status_code != 403
    assert allowed.status_code < 500


@pytest.mark.parametrize("method,path", [
    ("get", "/users/"), ("post", "/users/"), ("get", "/permissions/"), ("get", "/audit-log/"),
])
def test_owner_only_endpoints_refuse_staff_even_with_every_code(as_user, make_staff, method,
                                                                  path):
    from apps.accounts.permissions import CODES

    r = getattr(as_user(make_staff(*CODES)), method)(f"{A}{path}", format="json")
    assert r.status_code == 403  # §2.2 Owner-only cannot be delegated


def test_unauthenticated_admin_gets_401_or_403(api_client):
    assert api_client.get(f"{A}/settings/").status_code in (401, 403)

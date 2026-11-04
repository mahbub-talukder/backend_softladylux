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
        per_action = "get_permissions" in vars(cls)  # e.g. OrderViewSet picks a code per action
        assert per_action or any(issubclass(pc, (IsStaffUser, IsOwner))
                                 for pc in cls.permission_classes), f"{route} has no staff permission"
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
    ("get", "/categories/", "products.view"),
    ("get", "/products/", "products.view"),
    ("get", "/products/export/", "products.view"),
    ("get", "/size-charts/", "products.view"),
    ("get", "/orders/", "orders.view"),
    ("post", "/orders/bulk-transition/", "orders.manage"),
    ("get", "/inventory/", "inventory.view"),
    ("get", "/inventory/movements/", "inventory.view"),
    ("get", "/inventory/export/", "inventory.view"),
    ("post", "/inventory/adjust/", "inventory.adjust"),
    ("get", "/coupons/", "coupons.manage"),
    ("post", "/coupons/", "coupons.manage"),
    ("post", "/media/", "website.manage"),
]


@pytest.mark.parametrize("method,path,code", MATRIX)
def test_matrix_granted_vs_denied(as_user, make_staff, method, path, code):
    other = "reports.view" if code == "theme.manage" else "theme.manage"  # unrelated, no implication
    denied = getattr(as_user(make_staff(other)), method)(f"{A}{path}", format="json")
    assert denied.status_code == 403
    allowed = getattr(as_user(make_staff(code)), method)(f"{A}{path}", format="json")
    assert allowed.status_code != 403
    assert allowed.status_code < 500


@pytest.mark.parametrize("method,path,code", [
    ("post", "/categories/", "products.manage"),
    ("post", "/products/", "products.manage"),
    ("post", "/products/bulk/", "products.manage"),
])
def test_catalogue_writes_need_manage_not_just_view(as_user, make_staff, method, path, code):
    """PRM-05: view access alone never allows a write."""
    r = getattr(as_user(make_staff("products.view")), method)(f"{A}{path}", {}, format="json")
    assert r.status_code == 403
    assert getattr(as_user(make_staff(code)), method)(f"{A}{path}", {}, format="json").status_code != 403


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


@pytest.mark.parametrize("method,path", [
    ("get", "/contact-messages/"), ("get", "/newsletter/"), ("get", "/newsletter/export/"),
])
def test_contact_endpoints_matrix(as_user, make_staff, method, path):  # §2.2 contact.manage
    assert getattr(as_user(make_staff("orders.view")), method)(f"{A}{path}").status_code == 403
    assert getattr(as_user(make_staff("contact.manage")), method)(f"{A}{path}").status_code == 200

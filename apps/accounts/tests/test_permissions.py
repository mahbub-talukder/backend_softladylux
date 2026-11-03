# Covers SRS §2.2 catalogue, PRM-01, PRM-03, PRM-05, PRM-06.
import pytest

from apps.accounts import permissions as perms
from apps.accounts.models import Role, User

pytestmark = pytest.mark.django_db


def test_catalogue_has_15_codes_and_defaults():
    assert len(perms.CODES) == 15
    assert set(perms.DEFAULT_CODES) == {
        "dashboard.view", "orders.view", "orders.manage", "payments.verify", "products.view",
        "products.manage", "inventory.view", "inventory.adjust"}
    assert perms.SENSITIVE_CODES == {"payments.verify", "inventory.adjust", "settings.manage",
                                     "theme.manage"}


def test_dependencies_closed_and_unknown_dropped():  # PRM-05
    got = perms.close_dependencies(["orders.manage", "nope", "inventory.adjust"])
    assert got == {"orders.manage", "orders.view", "inventory.adjust", "inventory.view"}


def test_owner_holds_everything_and_customer_nothing(owner):  # PRM-06
    assert perms.user_permissions(owner) == set(perms.CODES)
    c = User.objects.create_user(phone="01712345678", password="x-pass-1234")
    assert perms.user_permissions(c) == set()


def test_set_permissions_returns_diff(make_staff):  # PRM-07 (audit input)
    u = make_staff("orders.view")
    added, removed = perms.set_permissions(u, ["products.view"])
    assert added == {"products.view"} and removed == {"orders.view"}
    assert perms.user_permissions(u) == {"products.view"}


def test_inactive_user_has_none(make_staff):  # PRM-07
    u = make_staff("orders.view")
    u.is_active = False
    assert perms.user_permissions(u) == set()
    assert u.role == Role.STAFF

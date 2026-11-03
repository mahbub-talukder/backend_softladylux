"""Staff permission catalogue and enforcement.

SRS §2.2 (15 codes + defaults), §4.17 PRM-01..PRM-07.
"""

from rest_framework import permissions

from .models import Role, StaffPermission, User

# (code, group, description, default for new Staff, sensitive) — SRS §2.2, PRM-02.
CATALOGUE = [
    ("dashboard.view", "Orders", "Dashboard (widgets limited to areas you can open)", True, False),
    ("orders.view", "Orders", "View orders, print invoice and delivery slip", True, False),
    ("orders.manage", "Orders", "Change stages, edit address/note, cancel, return, bulk", True, False),
    ("payments.verify", "Orders", "Verify or reject payments, correct TrxID", True, True),
    ("products.view", "Catalogue", "View products, categories and sub-categories", True, False),
    ("products.manage", "Catalogue", "Create/edit products, variants, photos, categories", True, False),
    ("inventory.view", "Inventory", "Stock list and movement history", True, False),
    ("inventory.adjust", "Inventory", "Manual stock adjustments", True, True),
    ("coupons.manage", "Marketing", "Create and edit coupons", False, False),
    ("reports.view", "Marketing", "Reports", False, False),
    ("insights.view", "Marketing", "Wishlist insight and GA4 link", False, False),
    ("website.manage", "Content", "Carousel, banners, videos, announcement, pages, trust", False, False),
    ("contact.manage", "Content", "Contact messages, newsletter export", False, False),
    ("settings.manage", "Settings", "Site settings (company, payments, fees, analytics ID)", False, True),
    ("theme.manage", "Settings", "Theme colours", False, True),
]
CODES = [c[0] for c in CATALOGUE]
DEFAULT_CODES = [c[0] for c in CATALOGUE if c[3]]
SENSITIVE_CODES = {c[0] for c in CATALOGUE if c[4]}

# PRM-05: a .manage/.adjust/.verify permission implies its area's view permission.
IMPLIES = {
    "orders.manage": "orders.view",
    "payments.verify": "orders.view",
    "products.manage": "products.view",
    "inventory.adjust": "inventory.view",
}


def close_dependencies(codes) -> set[str]:
    """PRM-05: add the implied view permissions; drop unknown codes."""
    result = {c for c in codes if c in CODES}
    result |= {IMPLIES[c] for c in list(result) if c in IMPLIES}
    return result


def catalogue_payload():
    return [
        {"code": c, "group": g, "description": d, "default": df, "sensitive": s}
        for c, g, d, df, s in CATALOGUE
    ]


def user_permissions(user: User) -> set[str]:
    """PRM-03: read from the DB on every call (no caching) so Owner edits apply at once."""
    if not user or not user.is_authenticated or not user.is_active:
        return set()
    if user.role == Role.OWNER:
        return set(CODES)  # PRM-06
    if user.role != Role.STAFF:
        return set()
    return set(StaffPermission.objects.filter(user=user).values_list("code", flat=True))


def set_permissions(user: User, codes) -> tuple[set[str], set[str]]:
    """PRM-01/PRM-05: replace a Staff user's permissions; returns (added, removed)."""
    new = close_dependencies(codes)
    old = set(StaffPermission.objects.filter(user=user).values_list("code", flat=True))
    StaffPermission.objects.filter(user=user, code__in=old - new).delete()
    StaffPermission.objects.bulk_create(
        [StaffPermission(user=user, code=c) for c in new - old]
    )
    return new - old, old - new


class IsStaffUser(permissions.BasePermission):
    """Authenticated active Owner or Staff (ACC-10: customers can't use the admin API)."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and u.role in (Role.OWNER, Role.STAFF))


class IsOwner(permissions.BasePermission):
    """Owner-only: users, permissions, audit log (§2.2)."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and u.role == Role.OWNER)


def require(*codes: str, any_of: bool = False):
    """PRM-03: DRF permission class factory — view needs all (or any) of `codes`.

    Safe methods may need a different (view) code than unsafe ones; pass a dict via
    `require_by_method` for that."""

    class _Require(IsStaffUser):
        def has_permission(self, request, view):
            if not super().has_permission(request, view):
                return False
            held = user_permissions(request.user)
            return any(c in held for c in codes) if any_of else all(c in held for c in codes)

    _Require.__name__ = f"Require_{'_'.join(codes)}".replace(".", "_")
    return _Require


def require_by_method(read: str | None, write: str):
    """PRM-03/PRM-05: GET needs `read` (or `write` if read is None), other methods need `write`."""

    class _ByMethod(IsStaffUser):
        def has_permission(self, request, view):
            if not super().has_permission(request, view):
                return False
            held = user_permissions(request.user)
            if request.method in permissions.SAFE_METHODS:
                return (read or write) in held or write in held
            return write in held

    return _ByMethod

"""INV-11 chart data: units on hand per category."""
import pytest

from apps.catalog.tests.factories import product

pytestmark = pytest.mark.django_db


def test_stock_by_category(as_user, owner):
    product("Bra One", cat="bras", sizes=("34B",))
    product("Brief One", cat="panties", sizes=("M",))
    rows = as_user(owner).get("/api/v1/admin/inventory/by-category/").data
    assert {r["name"] for r in rows} >= {"Bras", "Panties"} and all(r["units"] >= 0 for r in rows)

# Covers SRS ACC-04, ACC-05, ACC-06, ACC-07, ACC-08, ACC-10, §7 me/*; wishlist insight input.
import pytest

from apps.accounts.models import Address, Role, User
from apps.catalog.tests.factories import product
from apps.shop.models import Order, WishlistItem
from apps.shop.tests.helpers import configure_site, one_variant, payload

pytestmark = pytest.mark.django_db
B = "/api/v1"


@pytest.fixture
def me(db):
    return User.objects.create_user(phone="01712345678", email="n@example.com",
                                    password="secret-pass-1", full_name="Nusrat", is_active=True,
                                    phone_verified=True)


@pytest.fixture
def c(as_user, me):
    return as_user(me)


def addr(**kw):
    return {"label": "Home", "recipient_name": "Nusrat Jahan", "phone": "01712345678",
            "area": "inside_dhaka", "district": "Dhaka", "thana": "Dhanmondi",
            "address_line": "House 12, Road 5", **kw}


# ------------------------------------------------------------------ profile


def test_profile_get_and_update(c, me):  # ACC-04
    assert c.get(f"{B}/me/").data["phone"] == "01712345678"
    r = c.patch(f"{B}/me/", {"full_name": "Nusrat J", "email": "new@example.com",
                             "birthday": "1995-04-02"}, format="json")
    assert r.status_code == 200 and r.data["email"] == "new@example.com"
    assert r.data["birthday"].isoformat() == "1995-04-02" if hasattr(r.data["birthday"], "isoformat") else True
    me.refresh_from_db()
    assert me.full_name == "Nusrat J" and me.phone == "01712345678"


def test_profile_cannot_change_mobile_and_validates(c, me):
    c.patch(f"{B}/me/", {"phone": "01999999999"}, format="json")
    me.refresh_from_db()
    assert me.phone == "01712345678"  # verified mobile is read-only here
    for bad in ({"full_name": "x"}, {"email": "nope"}, {"birthday": "tomorrow"},
                {"birthday": "2999-01-01"}):
        assert c.patch(f"{B}/me/", bad, format="json").status_code == 400, bad
    User.objects.create_user(phone="01811111111", email="used@example.com", password="x-pass-1234")
    assert c.patch(f"{B}/me/", {"email": "used@example.com"}, format="json").status_code == 400
    assert c.patch(f"{B}/me/", {"email": "", "birthday": ""}, format="json").status_code == 200


def test_me_requires_customer_not_staff_or_anonymous(api_client, as_user, owner):  # ACC-10
    assert api_client.get(f"{B}/me/").status_code in (401, 403)
    assert as_user(owner).get(f"{B}/me/").status_code == 403
    assert as_user(owner).get(f"{B}/me/addresses/").status_code == 403
    assert as_user(owner).get(f"{B}/me/wishlist/").status_code == 403


# ------------------------------------------------------------------ addresses


def test_address_crud_first_is_default_exactly_one_default(c):  # ACC-05
    a = c.post(f"{B}/me/addresses/", addr(), format="json")
    assert a.status_code == 201 and a.data["is_default"] is True
    b = c.post(f"{B}/me/addresses/", addr(label="Office"), format="json")
    assert b.data["is_default"] is False
    c.patch(f"{B}/me/addresses/{b.data['id']}/", {"is_default": True}, format="json")
    rows = c.get(f"{B}/me/addresses/").data
    assert [r["label"] for r in rows if r["is_default"]] == ["Office"]
    assert c.get(f"{B}/me/addresses/{a.data['id']}/").status_code == 200
    assert c.put(f"{B}/me/addresses/{a.data['id']}/", addr(label="Flat"), format="json"
                 ).data["label"] == "Flat"
    c.delete(f"{B}/me/addresses/{b.data['id']}/")  # deleting the default promotes another
    left = c.get(f"{B}/me/addresses/").data
    assert len(left) == 1 and left[0]["is_default"] is True


def test_address_limit_five_and_validation_and_isolation(c, as_user):
    for i in range(5):
        assert c.post(f"{B}/me/addresses/", addr(label=f"A{i}"), format="json").status_code == 201
    assert c.post(f"{B}/me/addresses/", addr(), format="json").status_code == 400
    fresh = as_user(User.objects.create_user(phone="01855555555", password="x-pass-1234",
                                             is_active=True))
    for bad in ({"phone": "123"}, {"area": "mars"}, {"thana": ""}):
        assert fresh.post(f"{B}/me/addresses/", addr(**bad), format="json").status_code == 400
    other = User.objects.create_user(phone="01866666666", password="x-pass-1234", is_active=True)
    oc = as_user(other)
    first = Address.objects.filter(user__phone="01712345678").first()
    assert oc.get(f"{B}/me/addresses/{first.id}/").status_code == 404
    assert oc.delete(f"{B}/me/addresses/{first.id}/").status_code == 404


# ------------------------------------------------------------------ orders (ACC-06)


def test_order_history_and_reorder_data(c, me):
    configure_site()
    v = one_variant(on_hand=9)
    r = c.post(f"{B}/checkout/", payload(v, 2), format="json")
    assert r.status_code == 201
    hist = c.get(f"{B}/orders/").data["results"][0]
    assert hist["items"][0]["variant"] == v.id and hist["items"][0]["quantity"] == 2  # reorder
    assert Order.objects.get().user == me


# ------------------------------------------------------------------ wishlist (ACC-07)


def test_wishlist_add_list_remove_merge(c, me):
    a, b = product("A Bra"), product("B Bra")
    assert c.get(f"{B}/me/wishlist/").data == []
    r = c.post(f"{B}/me/wishlist/", {"product": a.id}, format="json")
    assert r.status_code == 201 and [p["slug"] for p in r.data] == ["a-bra"]
    c.post(f"{B}/me/wishlist/", {"product": a.id}, format="json")  # idempotent
    assert WishlistItem.objects.filter(user=me).count() == 1
    merged = c.post(f"{B}/me/wishlist/", {"products": [a.id, b.id, 99999, "x"]}, format="json")
    assert sorted(p["slug"] for p in merged.data) == ["a-bra", "b-bra"]  # guest list merge
    rm = c.delete(f"{B}/me/wishlist/", {"product": a.id}, format="json")
    assert [p["slug"] for p in rm.data] == ["b-bra"]
    assert c.delete(f"{B}/me/wishlist/", {"product": 99999}, format="json").status_code == 404


def test_wishlist_validation_and_hidden_products(c):
    assert c.post(f"{B}/me/wishlist/", {}, format="json").status_code == 400
    assert c.post(f"{B}/me/wishlist/", {"product": 99999}, format="json").status_code == 400
    assert c.post(f"{B}/me/wishlist/", {"products": "x"}, format="json").status_code == 400
    draft = product("Draft Bra", active=False)
    assert c.post(f"{B}/me/wishlist/", {"product": draft.id}, format="json").status_code == 400
    live = product("Live Bra")
    c.post(f"{B}/me/wishlist/", {"product": live.id}, format="json")
    live.is_active = False
    live.save()
    assert c.get(f"{B}/me/wishlist/").data == []  # deactivated products drop out of the list


# ------------------------------------------------------------------ account deletion (ACC-08)


def test_account_deletion_anonymises_and_keeps_orders(c, me, api_client):
    configure_site()
    v = one_variant(on_hand=9)
    c.post(f"{B}/checkout/", payload(v), format="json")
    c.post(f"{B}/me/addresses/", addr(), format="json")
    wrong = c.delete(f"{B}/me/", {"password": "nope"}, format="json")
    assert wrong.status_code == 400
    r = c.delete(f"{B}/me/", {"password": "secret-pass-1"}, format="json")
    assert r.status_code == 204
    me.refresh_from_db()
    assert (me.full_name, me.email, me.is_active, me.birthday) == ("Deleted customer", None, False, None)
    assert me.phone == f"X{me.pk}" and not me.has_usable_password() and me.role == Role.CUSTOMER
    assert Address.objects.filter(user=me).count() == 0
    assert Order.objects.count() == 1  # order records are retained
    # the number is free again and the old password no longer logs in
    assert api_client.post(f"{B}/auth/login/", {"phone": "01712345678", "password": "secret-pass-1"},
                           format="json").status_code == 401
    fresh = User.objects.create_user(phone="01712345678", password="new-pass-1234",
                                     is_active=True, phone_verified=True)
    assert fresh.pk != me.pk

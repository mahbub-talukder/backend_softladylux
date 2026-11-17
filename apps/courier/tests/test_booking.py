# Covers SRS v1.7 CRR-03, CRR-04, CRR-05, CRR-07; scenarios 32, 33, 39.
import pytest

from apps.courier import services
from apps.courier.models import Attention, CourierEvent, Shipment
from apps.courier.steadfast import SteadfastError
from apps.shop.models import OrderStatus

from .conftest import site

pytestmark = pytest.mark.django_db
A = "/api/v1/admin/courier"


def test_scenario_32_confirm_books_and_moves_to_shipping(confirmed_order, steadfast):
    order = confirmed_order(qty=2)
    ship = order.shipment
    assert order.status == OrderStatus.SHIPPING  # CRR-03: auto-move
    assert order.courier == "Steadfast" and order.tracking_ref == ship.tracking_code
    assert ship.is_booked and ship.tracking_link and ship.attention == ""
    _m, path, body, headers = steadfast.calls[-1]
    assert path == "/create_order" and headers["Api-Key"] == "key-123"
    assert body["invoice"] == order.number and body["cod_amount"] == float(order.total)
    assert body["recipient_address"] == "House 12, Road 5, Dhanmondi, Dhaka"
    assert body["item_description"] == "2 items, 0.20 kg"  # CAT-26 weight in the description
    log = order.logs.order_by("id").last()
    assert (log.from_status, log.to_status, log.user) == ("confirmed", "shipping", None)
    assert CourierEvent.objects.filter(order=order, source="booking").exists()


def test_prepaid_order_sends_zero_cod(confirmed_order, steadfast):  # CRR-03
    confirmed_order(method="bkash")
    assert steadfast.calls[-1][2]["cod_amount"] == 0


def test_auto_booking_off_or_unconfigured_makes_no_call(confirmed_order, steadfast):
    s = site()
    s.steadfast_auto_book = False
    s.save()
    order = confirmed_order()
    assert order.status == OrderStatus.CONFIRMED and steadfast.calls == []
    s.steadfast_auto_book, s.steadfast_api_key = True, ""
    s.save()
    assert confirmed_order().status == OrderStatus.CONFIRMED and steadfast.calls == []


def test_scenario_33_rejection_flags_then_manual_retry_books(confirmed_order, steadfast,
                                                             as_user, owner):
    steadfast.routes[("POST", r"^/create_order$")] = (400, {
        "status": 400, "errors": {"recipient_phone": ["The recipient phone format is invalid."]}})
    order = confirmed_order()
    ship = Shipment.objects.get(order=order)
    assert order.status == OrderStatus.CONFIRMED and not ship.is_booked
    assert ship.attention == Attention.BOOKING_FAILED and "recipient_phone" in ship.last_error
    assert len(steadfast.paths("POST")) == 1  # CRR-05: a rejection is never retried
    steadfast.routes[("POST", r"^/create_order$")] = steadfast._create
    r = as_user(owner).post(f"{A}/orders/{order.id}/book/")
    assert r.status_code == 200 and r.data["status"] == "shipping"
    assert r.data["shipment"]["attention"] == "" and r.data["shipment"]["is_booked"]


def test_manual_book_rejection_returns_field_errors(confirmed_order, steadfast, as_user, owner):
    s = site()
    s.steadfast_auto_book = False
    s.save()
    order = confirmed_order()
    steadfast.routes[("POST", r"^/create_order$")] = (422, {
        "message": "Invalid", "errors": {"recipient_phone": "bad"}})
    r = as_user(owner).post(f"{A}/orders/{order.id}/book/")
    assert r.status_code == 422 and r.data["fields"] == {"recipient_phone": ["bad"]}


def test_unreachable_is_retryable_and_raised_by_the_task(confirmed_order, steadfast):
    s = site()
    s.steadfast_auto_book = False
    s.save()
    order = confirmed_order()

    def down(body):
        raise SteadfastError("Steadfast could not be reached.")

    steadfast.routes[("POST", r"^/create_order$")] = down
    with pytest.raises(SteadfastError):  # CRR-05: the queue retries it
        services.book_task(order.id)
    assert Shipment.objects.get(order=order).attention == Attention.BOOKING_FAILED


def test_book_twice_or_wrong_state_is_a_conflict(confirmed_order, as_user, owner):  # CRR-04
    order = confirmed_order()
    r = as_user(owner).post(f"{A}/orders/{order.id}/book/")
    assert r.status_code == 409


def test_duplicate_invoice_recovers_existing_parcel(confirmed_order, steadfast):  # CRR-04
    s = site()
    s.steadfast_auto_book = False
    s.save()
    order = confirmed_order()
    steadfast.routes[("POST", r"^/create_order$")] = (400, {
        "status": 400, "errors": {"invoice": ["The invoice has already been taken."]}})
    steadfast.routes[("GET", r"^/trackings_by_invoice/.+$")] = (200, {"tracking": [
        {"consignment_id": 777, "text": "Consignment created by Sender(API).",
         "created_at": "2026-11-16T07:05:31.000000Z"}]})
    ship, err = services.book(order.id)
    order.refresh_from_db()
    assert err is None and ship.consignment_id == 777 and order.status == OrderStatus.SHIPPING


def test_scenario_39_bulk_book_reports_each_failure(confirmed_order, steadfast, as_user, owner):
    s = site()
    s.steadfast_auto_book = False
    s.save()
    a, b, c = confirmed_order(), confirmed_order(), confirmed_order()

    def bulk(body):
        out = []
        for o in body["data"]:
            if o["invoice"] == c.number:
                out.append({"invoice": o["invoice"], "status": "error",
                            "error": ["This invoice already exists."], "consignment_id": None})
            else:
                out.append({**steadfast._consignment(o), "status": "success"})
        return 200, {"status": 200, "data": out}

    steadfast.routes[("POST", r"^/create_order/bulk-order/extended$")] = bulk
    r = as_user(owner).post(f"{A}/bulk-book/", {"ids": [a.id, b.id, c.id]}, format="json")
    assert r.status_code == 200
    assert sorted(r.data["done"]) == sorted([a.number, b.number])
    assert [f["number"] for f in r.data["failed"]] == [c.number]
    assert "already exists" in r.data["failed"][0]["message"]
    for o in (a, b):
        o.refresh_from_db()
        assert o.status == OrderStatus.SHIPPING
    c.refresh_from_db()
    assert c.status == OrderStatus.CONFIRMED and c.shipment.attention == Attention.BOOKING_FAILED
    r = as_user(owner).post(f"{A}/bulk-book/", {"ids": [a.id]}, format="json")  # already booked
    assert r.data["done"] == [] and "Already booked" in r.data["failed"][0]["message"]


def test_bulk_book_needs_ids(as_user, owner):
    r = as_user(owner).post(f"{A}/bulk-book/", {"ids": []}, format="json")
    assert r.status_code == 400


def test_unbooked_filter_lists_confirmed_orders_to_book(confirmed_order, as_user, owner):
    s = site()
    s.steadfast_auto_book = False
    s.save()
    waiting = confirmed_order()
    r = as_user(owner).get("/api/v1/admin/orders/?courier=unbooked")
    assert [o["number"] for o in r.data["results"]] == [waiting.number]
    assert r.data["results"][0]["courier_booked"] is False

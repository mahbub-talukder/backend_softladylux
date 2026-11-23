# Covers SRS v1.11 ORD-14 (Steadfast-aware Move to Shipping, no duplicate bookings) and scenarios 57–58.
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.exceptions import Conflict
from apps.courier import services
from apps.courier.models import Shipment
from apps.shop import orders
from apps.shop.models import OrderStatus

from .conftest import site

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


@pytest.fixture
def queued_only(monkeypatch):
    """The auto-booking is queued but the worker has not run it yet."""
    monkeypatch.setattr("apps.shop.notifications._enqueue", lambda *a, **k: None)


def test_scenario_57_manual_ship_refused_while_booking_queued(confirmed_order, queued_only, as_user,
                                                              owner):
    order = confirmed_order()
    ship = Shipment.objects.get(order=order)
    assert order.status == OrderStatus.CONFIRMED and ship.queued_at and ship.booking_in_progress
    d = as_user(owner).get(f"{A}/orders/{order.id}/").data
    assert d["shipment"]["booking_in_progress"] is True and d["steadfast_ready"] is True
    r = as_user(owner).post(f"{A}/orders/{order.id}/transition/",
                            {"to": "shipping", "courier": "Pathao", "tracking": "P1"}, format="json")
    assert r.status_code == 409 and r.data["code"] == "booking_in_progress"


def test_scenario_57_booking_clears_the_flag_and_no_second_parcel(confirmed_order, steadfast):
    order = confirmed_order()  # auto-booked (queue runs synchronously)
    ship = Shipment.objects.get(order=order)
    assert order.status == OrderStatus.SHIPPING and ship.queued_at is None
    assert not ship.booking_in_progress
    calls = len(steadfast.calls)
    with pytest.raises(Conflict):
        services.book(order.id)
    assert len(steadfast.calls) == calls


def test_stale_queued_booking_does_not_block(confirmed_order, queued_only):
    order = confirmed_order()
    Shipment.objects.filter(order=order).update(queued_at=timezone.now() - timedelta(minutes=11))
    assert orders.transition(order.id, "shipping", courier="Pathao", tracking="P2").status == "shipping"


def test_scenario_58_auto_book_off_book_or_ship_by_hand(confirmed_order, steadfast, as_user, owner):
    s = site()
    s.steadfast_auto_book = False
    s.save()
    a, b = confirmed_order(), confirmed_order()
    assert Shipment.objects.filter(order__in=[a, b], queued_at__isnull=False).count() == 0
    r = as_user(owner).post(f"{A}/courier/orders/{a.id}/book/", format="json")  # Move to Shipping
    assert r.status_code == 200
    a.refresh_from_db()
    assert a.status == OrderStatus.SHIPPING and a.courier == "Steadfast"
    # Ship with another courier, then Steadfast refuses a booking for it (no duplicate parcel)
    orders.transition(b.id, "shipping", owner, courier="Pathao", tracking="PT9")
    with pytest.raises(Conflict):
        services.book(b.id)


def test_failed_booking_clears_the_flag(confirmed_order, steadfast):
    steadfast.routes[("POST", r"^/create_order$")] = (400, {"status": 400, "errors": {
        "recipient_phone": ["The recipient phone format is invalid."]}})
    order = confirmed_order()
    ship = Shipment.objects.get(order=order)
    assert ship.attention == "booking_failed" and ship.queued_at is None
    assert orders.transition(order.id, "shipping", courier="Pathao", tracking="P3").status == "shipping"

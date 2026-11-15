# Covers SRS v1.7 CRR-08, CRR-09, CRR-10, CRR-11, CRR-16; scenarios 35, 36, 37, 38.
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.courier import services
from apps.courier.models import Attention, CourierEvent, Shipment, WebhookReceipt
from apps.shop.models import OrderStatus, PayStatus

from .conftest import TOKEN

pytestmark = pytest.mark.django_db
URL = "/api/v1/webhooks/steadfast/"


@pytest.fixture
def post(api_client, django_capture_on_commit_callbacks):
    def send(event, key="evt-1", token=TOKEN, signature=None):
        raw = json.dumps(event).encode()
        sig = signature or hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()
        with django_capture_on_commit_callbacks(execute=True):
            return api_client.generic("POST", URL, raw, content_type="application/json",
                                      HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_SIGNATURE=sig,
                                      HTTP_IDEMPOTENCY_KEY=key)
    return send


def status_event(order, status, **kw):
    return {"notification_type": "delivery_status", "consignment_id": order.shipment.consignment_id,
            "invoice": order.number, "cod_amount": float(order.total), "status": status,
            "delivery_charge": 100.0, "tracking_message": f"Parcel {status}.",
            "updated_at": "2026-11-16 12:45:30", **kw}


def test_scenario_35_bad_signature_rejected_and_nothing_changes(confirmed_order, post):
    order = confirmed_order()
    r = post(status_event(order, "delivered"), signature="0" * 64)
    assert r.status_code == 401 and not WebhookReceipt.objects.exists()
    r = post(status_event(order, "delivered"), token="wrong-token")
    assert r.status_code == 401
    order.refresh_from_db()
    assert order.status == OrderStatus.SHIPPING


def test_webhook_refused_when_no_token_is_configured(confirmed_order, post):  # CRR-08
    from apps.core.models import SiteSettings

    order = confirmed_order()
    SiteSettings.objects.update(steadfast_webhook_token="")
    assert post(status_event(order, "delivered"), token="").status_code == 401


def test_non_json_body_is_400(api_client, steadfast):
    raw = b"not json"
    sig = hmac.new(TOKEN.encode(), raw, hashlib.sha256).hexdigest()
    r = api_client.generic("POST", URL, raw, content_type="application/json",
                           HTTP_AUTHORIZATION=f"Bearer {TOKEN}", HTTP_X_SIGNATURE=sig)
    assert r.status_code == 400


def test_scenario_35_duplicate_idempotency_key_processed_once(confirmed_order, post):
    order = confirmed_order()
    step = {"notification_type": "tracking_update", "consignment_id": order.shipment.consignment_id,
            "invoice": order.number, "tracking_message": "Package arrived at the sorting center.",
            "updated_at": "2026-11-16 13:15:00"}
    assert post(step, key="same").status_code == 200
    assert post(step, key="same").status_code == 200
    assert WebhookReceipt.objects.count() == 1
    assert CourierEvent.objects.filter(order=order, kind="tracking").count() == 1
    assert WebhookReceipt.objects.get().processed_at is not None


def test_scenario_36_delivered_marks_delivered_and_cod_collected(confirmed_order, post):
    order = confirmed_order()
    assert post(status_event(order, "delivered")).status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED and order.payment.status == PayStatus.COLLECTED
    assert order.shipment.courier_status == "delivered"


def test_cod_mismatch_adds_staff_note(confirmed_order, post):  # CRR-09
    order = confirmed_order()
    post(status_event(order, "delivered", cod_amount=500.0))
    assert order.logs.filter(note__contains="differs from the order total").exists()


def test_scenario_36_cancelled_flags_without_changing_status_or_stock(confirmed_order, post):
    order = confirmed_order()
    variant = order.items.get().variant
    variant.refresh_from_db()
    stock = variant.on_hand
    post(status_event(order, "cancelled"))
    order.refresh_from_db()
    variant.refresh_from_db()
    assert order.status == OrderStatus.SHIPPING and variant.on_hand == stock
    assert order.shipment.attention == Attention.RETURNING
    note = order.logs.order_by("id").last()
    assert note.from_status == note.to_status and "Courier returning parcel" in note.note


@pytest.mark.parametrize(("status", "flag"), [("partial_delivered", Attention.PARTIAL),
                                               ("exceptional", Attention.EXCEPTIONAL),
                                               ("hold", Attention.HOLD)])
def test_flags_for_other_outcomes(confirmed_order, post, status, flag):  # CRR-09
    order = confirmed_order()
    post(status_event(order, status))
    order.refresh_from_db()
    assert order.status == OrderStatus.SHIPPING and order.shipment.attention == flag


def test_hold_clears_and_final_orders_never_change(confirmed_order, post):  # CRR-09
    order = confirmed_order()
    post(status_event(order, "hold"), key="a")
    post(status_event(order, "pending"), key="b")
    order.refresh_from_db()
    assert order.shipment.attention == ""
    post(status_event(order, "delivered"), key="c")
    post(status_event(order, "cancelled"), key="d")
    order.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED and order.shipment.attention == ""


def test_unknown_parcel_and_staff_note_events(confirmed_order, post):  # CRR-10
    order = confirmed_order()
    post({"notification_type": "delivery_status", "consignment_id": 1, "status": "delivered"},
         key="x")
    assert WebhookReceipt.objects.get(idempotency_key="x").error == "No order found for this parcel."
    post({"notification_type": "return_request", "consignment_id": order.shipment.consignment_id,
          "status": "pending"}, key="y")
    assert order.logs.filter(note__contains="return request pending").exists()
    post({"notification_type": "user_update"}, key="z")
    assert WebhookReceipt.objects.get(idempotency_key="z").processed_at is not None


def test_scenario_37_reconcile_recovers_missed_webhook(confirmed_order, steadfast):
    order = confirmed_order()
    Shipment.objects.filter(order=order).update(
        last_event_at=timezone.now() - timedelta(hours=7))
    steadfast.routes[("GET", r"^/status_by_cid/\d+$")] = (
        200, {"status": 200, "delivery_status": "delivered"})
    steadfast.routes[("GET", r"^/trackings_by_invoice/.+$")] = (200, {"tracking": [
        {"consignment_id": order.shipment.consignment_id, "text": "Parcel received at hub.",
         "created_at": "2026-11-16T07:05:31.000000Z"},
        {"consignment_id": order.shipment.consignment_id, "text": "Out for delivery.",
         "created_at": "2026-11-16T09:05:31.000000Z"}]})
    assert services.reconcile() == 1
    services.backfill_tracking(order.shipment)  # a second read stores nothing new
    order.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED
    assert CourierEvent.objects.filter(order=order, kind="tracking").count() == 2
    assert services.reconcile() == 0  # Delivered orders are no longer polled


def test_reconcile_skips_fresh_parcels_and_marks_return_received(confirmed_order, steadfast):
    order = confirmed_order()
    assert services.reconcile() == 0  # booked just now: not stale
    Shipment.objects.filter(order=order).update(
        attention=Attention.RETURNING, courier_status="cancelled",
        last_event_at=timezone.now() - timedelta(hours=7))
    steadfast.routes[("GET", r"^/status_by_cid/\d+$")] = (
        200, {"status": 200, "delivery_status": "cancelled"})
    steadfast.routes[("GET", r"^/status_with_return_status_by_cid/\d+$")] = (
        200, {"status": 200, "delivery_status": "cancelled_return_received"})
    services.reconcile()
    order.refresh_from_db()
    assert order.shipment.attention == Attention.RETURNED_TO_MERCHANT
    assert order.status == OrderStatus.SHIPPING  # staff mark Returned (restock is physical)


def test_scenario_38_guest_track_shows_courier_steps_without_calling(confirmed_order, post,
                                                                    api_client, steadfast):
    order = confirmed_order()
    later = (timezone.localtime() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    post({"notification_type": "tracking_update", "consignment_id": order.shipment.consignment_id,
          "invoice": order.number, "tracking_message": "Parcel received at Dhanmondi hub.",
          "updated_at": later})
    calls = len(steadfast.calls)
    r = api_client.post("/api/v1/track/", {"number": order.number, "phone": order.phone},
                        format="json")
    assert r.status_code == 200 and len(steadfast.calls) == calls  # read from our DB only
    texts = [s["text"] for s in r.data["courier_steps"]]
    assert texts[0] == "Parcel received at Dhanmondi hub." and "Booked with the courier" in texts
    assert r.data["tracking_link"] == order.shipment.tracking_link

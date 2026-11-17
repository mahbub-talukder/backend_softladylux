"""Steadfast booking, status updates and reconciliation. SRS v1.7 CRR-03..CRR-11, CRR-13, CRR-14.

Our order statuses only move through `apps.shop.orders.transition` (ORD-01), so the timeline,
stock rules and status email stay the same whether staff or Steadfast moved the order.
"""

import logging
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.exceptions import Conflict
from apps.shop import orders as order_svc
from apps.shop.models import Order, OrderStatus, PayMethod

from . import steadfast
from .models import Attention, CourierEvent, PickupRequest, Shipment, WebhookReceipt
from .steadfast import SteadfastError

log = logging.getLogger(__name__)
S = OrderStatus
COURIER_NAME = "Steadfast"
STALE_AFTER = timedelta(hours=6)  # CRR-11
RECONCILE_LIMIT = 200  # per run; keeps one run well inside the rate limit

# CRR-09: Steadfast delivery status → our flag. `delivered` moves the order instead.
FLAG_FOR = {
    "partial_delivered": Attention.PARTIAL,
    "cancelled": Attention.RETURNING,
    "exceptional": Attention.EXCEPTIONAL,
    "hold": Attention.HOLD,
}
FINAL_ORDER_STATES = {S.DELIVERED, S.RETURNED, S.CANCELLED}

# CRR-16: plain wording for customers (raw codes stay staff-side).
CUSTOMER_TEXT = {
    "in_review": "Booked with the courier",
    "pending": "With the courier",
    "hold": "On hold at the courier",
    "delivered": "Delivered",
    "partial_delivered": "Partly delivered",
    "cancelled": "Not delivered, returning to us",
    "exceptional": "Delayed at the courier",
}


def customer_status_text(status: str) -> str:
    if status.endswith("_approval_pending"):
        return "Awaiting courier confirmation"
    return CUSTOMER_TEXT.get(status, "With the courier" if status else "")


# ------------------------------------------------------------------ helpers


def _parse_time(value):
    """Steadfast sends ISO 8601 (API) or 'YYYY-MM-DD HH:MM:SS' in Dhaka time (webhooks)."""
    if not value:
        return timezone.now()
    dt = parse_datetime(str(value).replace(" ", "T", 1)) if isinstance(value, str) else None
    if dt is None:
        return timezone.now()
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _staff_note(order, note):
    """A same-status timeline entry: staff-only (ORD-06), never shown to customers."""
    order_svc.add_log(order, order.status, order.status, None, f"Steadfast: {note}"[:500])


def add_event(order, kind, message, occurred_at, source, status=""):
    """CRR-10: store a step once, whichever source (booking, webhook, poll) reports it first."""
    try:
        with transaction.atomic():
            CourierEvent.objects.get_or_create(
                order=order, kind=kind, message=(message or "")[:500], occurred_at=occurred_at,
                defaults={"source": source, "status": status[:60]})
    except IntegrityError:  # a concurrent writer stored the same step
        pass


def payload(order) -> dict:
    """CRR-03 booking body. Address = address line, thana, district; COD collects the order total,
    bKash/Nagad orders are prepaid (0). Weight goes into the description (CAT-26): Steadfast's
    create_order has no weight field."""
    lines = list(order.items.all())
    count = sum(i.quantity for i in lines)
    kg = sum(i.weight_grams * i.quantity for i in lines) / 1000
    cod = order.total if order.payment_method == PayMethod.COD else 0
    return {
        "invoice": order.number,
        "recipient_name": order.name[:100],
        "recipient_phone": order.phone,
        "recipient_address": ", ".join(
            x for x in (order.address_line, order.thana, order.district) if x)[:490],
        "cod_amount": float(cod),
        "note": (order.note or "")[:480],
        "item_description": f"{count} item{'s' if count != 1 else ''}, {kg:.2f} kg"[:255],
        "total_lot": max(count, 1),
        "delivery_type": 0,
    }


def _error_text(err: SteadfastError) -> str:
    fields = "; ".join(f"{k}: {' '.join(v)}" for k, v in err.errors.items())
    return f"{err} {fields}".strip()[:1000]


# ------------------------------------------------------------------ booking (CRR-03..CRR-07)


def _mark_booked(order, ship, consignment, user, source_note="Booked with Steadfast"):
    now = timezone.now()
    ship.consignment_id = int(consignment["consignment_id"])
    ship.tracking_code = str(consignment.get("tracking_code") or "")[:40]
    ship.tracking_link = str(consignment.get("tracking_link") or "")[:200]
    ship.courier_status = str(consignment.get("status") or "in_review")[:60]
    ship.status_at = ship.last_event_at = ship.booked_at = now
    ship.attention, ship.last_error, ship.booked_by = Attention.NONE, "", user
    ship.save()
    add_event(order, CourierEvent.Kind.STATUS, source_note, now, CourierEvent.Source.BOOKING,
              status=ship.courier_status)
    # ORD-10 / CRR-03: the booking moves the order to Shipping with the tracking code.
    order_svc.transition(order.id, S.SHIPPING, user, courier=COURIER_NAME,
                         tracking=ship.tracking_code or str(ship.consignment_id))


def _mark_failed(ship, err: SteadfastError):
    ship.attention, ship.last_error = Attention.BOOKING_FAILED, _error_text(err)
    ship.save(update_fields=["attention", "last_error", "updated_at"])


def _recover_duplicate(order, ship, user) -> bool:
    """CRR-04: Steadfast refuses a repeated invoice. If our earlier request did book the parcel
    (its answer was lost), find the consignment through the invoice's tracking history."""
    try:
        steps = steadfast.trackings_by_invoice(order.number).get("tracking") or []
    except SteadfastError:
        return False
    cid = next((t.get("consignment_id") for t in steps if t.get("consignment_id")), None)
    if not cid or Shipment.objects.filter(consignment_id=cid).exclude(pk=ship.pk).exists():
        return False
    _mark_booked(order, ship, {"consignment_id": cid}, user,
                 source_note="Steadfast parcel found for this order (earlier booking)")
    return True


def book(order_id, user=None):
    """Book one Confirmed order. Returns (shipment, error). Never raises SteadfastError: a failure
    is recorded on the shipment (CRR-05) and returned so callers decide whether to retry."""
    with transaction.atomic():
        # The row lock is held across the HTTP call so two bookings of one order cannot race.
        order = Order.objects.select_for_update().get(pk=order_id)
        ship, _ = Shipment.objects.get_or_create(order=order)
        if ship.is_booked:
            raise Conflict("This order already has a Steadfast parcel.", code="already_booked")
        if order.status != S.CONFIRMED:
            raise Conflict("Only Confirmed orders can be booked with Steadfast.",
                           code="invalid_state")
        try:
            data = steadfast.create_order(payload(order))
            consignment = data.get("consignment") or {}
            if not consignment.get("consignment_id"):
                raise SteadfastError(data.get("message") or "Steadfast did not return a parcel.",
                                     status=502)
        except SteadfastError as err:
            text = (str(err) + " " + " ".join(sum(err.errors.values(), []))).lower()
            if err.status in (400, 422) and "invoice" in text and (
                    "exist" in text or "taken" in text) and _recover_duplicate(order, ship, user):
                return ship, None
            _mark_failed(ship, err)
            return ship, err
        _mark_booked(order, ship, consignment, user)
        return ship, None


def book_task(order_id):
    """Django-Q task for CRR-03 auto-booking. A 5xx / unreachable error is raised so the queue
    retries it (3 attempts, CRR-05); a rejection (4xx) is final and stays flagged for staff."""
    try:
        _ship, err = book(order_id)
    except Conflict as e:  # already booked, or no longer Confirmed: nothing to do
        log.info("Auto-booking skipped for order %s: %s", order_id, e.detail)
        return
    if err is not None and err.retryable:
        raise err


def queue_auto_book(order_id):
    """Called on commit when an order becomes Confirmed (CRR-03)."""
    from apps.core.models import SiteSettings
    from apps.shop.notifications import _enqueue

    s = SiteSettings.load()
    if s.steadfast_auto_book and steadfast.is_configured() and not s.steadfast_keys_rejected:
        _enqueue("apps.courier.services.book_task", order_id)


def bulk_book(order_ids, user):
    """CRR-07: book many Confirmed, unbooked orders with the bulk endpoint (≤ 500 per call).
    Returns {"done": [numbers], "failed": [{id, number, message}]}."""
    done, failed = [], []
    candidates = []
    for order in Order.objects.filter(pk__in=order_ids).prefetch_related("items").order_by("id"):
        ship = Shipment.objects.filter(order=order).first()
        if ship and ship.is_booked:
            failed.append({"id": order.id, "number": order.number,
                           "message": "Already booked with Steadfast."})
        elif order.status != S.CONFIRMED:
            failed.append({"id": order.id, "number": order.number,
                           "message": "Only Confirmed orders can be booked."})
        else:
            candidates.append(order)
    for start in range(0, len(candidates), 500):
        chunk = candidates[start:start + 500]
        try:
            result = steadfast.bulk_create([payload(o) for o in chunk])
        except SteadfastError as err:
            for o in chunk:
                failed.append({"id": o.id, "number": o.number, "message": str(err)})
            if err.code == "keys_rejected":
                break
            continue
        by_invoice = {str(r.get("invoice")): r for r in (result.get("data") or [])}
        for o in chunk:
            r = by_invoice.get(o.number) or {}
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=o.id)
                ship, _ = Shipment.objects.get_or_create(order=order)
                if r.get("consignment_id") and order.status == S.CONFIRMED and not ship.is_booked:
                    _mark_booked(order, ship, r, user)
                    done.append(order.number)
                    continue
                err = r.get("error") or "Steadfast did not book this order."
                if isinstance(err, list):
                    err = " ".join(map(str, err))
                _mark_failed(ship, SteadfastError(str(err), status=400))
                failed.append({"id": order.id, "number": order.number, "message": str(err)})
    return {"done": done, "failed": failed}


# ------------------------------------------------------------------ status updates (CRR-09..CRR-11)


def apply_status(ship, status, source, message="", at=None, cod_amount=None):
    """CRR-09 mapping. Call inside a transaction with the order row locked."""
    status = (status or "").strip()
    order = ship.order
    at = at or timezone.now()
    ship.courier_status, ship.status_at, ship.last_event_at = status[:60], at, timezone.now()
    add_event(order, CourierEvent.Kind.STATUS, message or customer_status_text(status), at,
              source, status=status)
    if order.status in FINAL_ORDER_STATES:  # Delivered / Returned / Cancelled never change here
        ship.save()
        return
    old_flag = ship.attention
    if status == "delivered":
        ship.attention = Attention.NONE
        ship.save()
        if order.status == S.SHIPPING:
            order_svc.transition(order.id, S.DELIVERED, None,
                                 note="Delivered (confirmed by Steadfast).")
            if cod_amount is not None and order.payment_method == PayMethod.COD:
                try:
                    if abs(float(cod_amount) - float(order.total)) > 0.009:
                        _staff_note(order, f"collected COD {cod_amount} differs from the order "
                                           f"total {order.total}. Check before settling.")
                except (TypeError, ValueError):
                    pass
        return
    flag = FLAG_FOR.get(status)
    if flag and not (flag == Attention.RETURNING
                     and ship.attention == Attention.RETURNED_TO_MERCHANT):
        ship.attention = flag
    elif not flag and ship.attention == Attention.HOLD:
        ship.attention = Attention.NONE  # the hold is over
    ship.save()
    if ship.attention != old_flag and ship.attention:
        note = Attention(ship.attention).label
        if ship.attention == Attention.RETURNING:
            note += ". Mark the order Returned when the goods arrive."
        _staff_note(order, note)


def _shipment_for(payload):
    cid = payload.get("consignment_id")
    qs = Shipment.objects.select_related("order")
    if cid and (ship := qs.filter(consignment_id=cid).first()):
        return ship
    inv = payload.get("invoice")
    return qs.filter(order__number=inv, consignment_id__isnull=False).first() if inv else None


STAFF_NOTE_EVENTS = {
    "return_request": "return request {status}",
    "cancel_request": "cancellation request ({verify_status})",
    "consignment_update": "parcel details changed ({changed})",
}


def process_webhook(receipt_id):
    """CRR-08..CRR-10: handle one stored webhook event (Django-Q task)."""
    receipt = WebhookReceipt.objects.get(pk=receipt_id)
    if receipt.processed_at:
        return
    p, kind = receipt.payload or {}, receipt.notification_type
    error = ""
    try:
        with transaction.atomic():
            if kind == "pickup_request":
                pid = p.get("pickup_request_id")
                if pid:
                    PickupRequest.objects.filter(steadfast_id=pid).update(
                        status=str(p.get("status") or "")[:40])
            elif kind in ("delivery_status", "tracking_update", *STAFF_NOTE_EVENTS):
                ship = _shipment_for(p)
                if ship is None:
                    error = "No order found for this parcel."
                else:
                    Order.objects.select_for_update().get(pk=ship.order_id)
                    ship.refresh_from_db()
                    at = _parse_time(p.get("updated_at"))
                    if kind == "delivery_status":
                        apply_status(ship, p.get("status"), CourierEvent.Source.WEBHOOK,
                                     message=p.get("tracking_message") or "", at=at,
                                     cod_amount=p.get("cod_amount"))
                    elif kind == "tracking_update":
                        add_event(ship.order, CourierEvent.Kind.TRACKING,
                                  p.get("tracking_message") or "", at,
                                  CourierEvent.Source.WEBHOOK)
                        ship.last_event_at = timezone.now()
                        ship.save(update_fields=["last_event_at", "updated_at"])
                    else:
                        fmt = STAFF_NOTE_EVENTS[kind]
                        _staff_note(ship.order, fmt.format(
                            status=p.get("status", ""), verify_status=p.get("verify_status", ""),
                            changed=", ".join(p.get("changed") or [])))
            # Other events (return_list_accepted, payment_request, user_update) are stored only.
    except Exception as e:  # noqa: BLE001 - keep the receipt, let the queue retry
        receipt.error = str(e)[:500]
        receipt.save(update_fields=["error", "updated_at"])
        raise
    receipt.processed_at, receipt.error = timezone.now(), error
    receipt.save(update_fields=["processed_at", "error", "updated_at"])


def backfill_tracking(ship):
    """CRR-10: store any steps from trackings_by_invoice not seen yet."""
    data = steadfast.trackings_by_invoice(ship.order.number)
    for t in data.get("tracking") or []:
        if t.get("consignment_id") and ship.consignment_id and \
                int(t["consignment_id"]) != ship.consignment_id:
            continue
        add_event(ship.order, CourierEvent.Kind.TRACKING, t.get("text") or "",
                  _parse_time(t.get("created_at")), CourierEvent.Source.POLL)


def refresh(ship):
    """CRR-11 for one parcel: status, missing steps and, when returning, the return state."""
    data = steadfast.status_by_cid(ship.consignment_id)
    with transaction.atomic():
        Order.objects.select_for_update().get(pk=ship.order_id)
        ship.refresh_from_db()
        status = data.get("delivery_status") or ""
        if status and status != ship.courier_status:
            apply_status(ship, status, CourierEvent.Source.POLL)
        else:
            ship.last_event_at = timezone.now()
            ship.save(update_fields=["last_event_at", "updated_at"])
    backfill_tracking(ship)
    if ship.attention == Attention.RETURNING:
        ret = steadfast.status_with_return(ship.consignment_id).get("delivery_status") or ""
        if ret.endswith("_return_received"):
            with transaction.atomic():
                ship.refresh_from_db()
                ship.attention = Attention.RETURNED_TO_MERCHANT
                ship.save(update_fields=["attention", "updated_at"])
                _staff_note(ship.order, "Parcel back with you. Mark the order Returned to restock.")
    return ship


def reconcile():
    """CRR-11 scheduled task (every 2 hours): re-read Shipping parcels with no news for 6 hours."""
    cutoff = timezone.now() - STALE_AFTER
    ships = (Shipment.objects.select_related("order")
             .filter(consignment_id__isnull=False, order__status=S.SHIPPING)
             .exclude(last_event_at__gte=cutoff)
             .order_by("last_event_at")[:RECONCILE_LIMIT])
    checked = 0
    for ship in ships:
        try:
            refresh(ship)
            checked += 1
        except SteadfastError as e:
            log.warning("Reconcile %s failed: %s", ship.order.number, e)
            if e.code in ("keys_rejected", "not_configured", "throttled"):
                break
    return checked


# ------------------------------------------------------------------ returns and pickups


def request_return(order_id, reason, user):
    """CRR-14: ask Steadfast to bring a Shipping parcel back."""
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 500:
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"reason": "Give a reason (3–500 characters)."})
    ship = Shipment.objects.select_related("order").filter(
        order_id=order_id, consignment_id__isnull=False).first()
    if ship is None or ship.order.status != S.SHIPPING:
        raise Conflict("Only a Shipping order with a Steadfast parcel can be returned.",
                       code="invalid_state")
    data = steadfast.create_return({"consignment_id": ship.consignment_id, "reason": reason})
    order_svc.add_log(ship.order, ship.order.status, ship.order.status, user,
                      f"Steadfast: return requested ({reason})"[:500])
    return data


def create_pickup(data, user):
    """CRR-13: send a pickup request and keep a local copy (no list endpoint exists)."""
    body = {k: data[k] for k in ("address_id", "police_station_id", "address", "contact_number")}
    for k in ("note", "estim_qty"):
        if data.get(k) not in (None, ""):
            body[k] = data[k]
    try:
        resp = steadfast.create_pickup(body)
        info = resp.get("data") or {}
        status = "pending" if info.get("req_status") in (0, "0", None) else str(info["req_status"])
        sid = info.get("id")
    except SteadfastError as e:
        if e.status == 409:  # PICKUP_REQUEST_EXISTS: a rider is already coming
            raise Conflict("A pickup for this address is already pending at Steadfast.",
                           code="pickup_exists") from None
        raise
    return PickupRequest.objects.create(
        address_id=body["address_id"], police_station_id=body["police_station_id"],
        address=body["address"], contact_number=body["contact_number"],
        note=body.get("note", ""), estim_qty=body.get("estim_qty"), steadfast_id=sid,
        status=status, requested_by=user)


def _today():
    return timezone.localdate()


def default_stats_range():
    """CRR-12: the dashboard opens on the last 30 days (Steadfast's own default)."""
    to = _today()
    return (to - timedelta(days=30)).isoformat(), to.isoformat()


def parse_date(value):
    try:
        return datetime.fromisoformat(value).date().isoformat() if value else None
    except ValueError:
        return None

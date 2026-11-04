"""Order lifecycle. SRS ORD-01..13, PAY-01..08, INV-02..06, INV-09, rules 3–11.

Every status change runs in one transaction with the order row locked, writes an
OrderStatusLog (who, when, old → new, note) and, where stock moves, an immutable StockMovement.
"""

import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Variant
from apps.core.exceptions import Conflict
from apps.inventory.models import Reason
from apps.inventory.services import record_movement

from .models import (
    Coupon,
    Order,
    OrderStatus,
    OrderStatusLog,
    Payment,
    PayMethod,
    PayStatus,
)

log = logging.getLogger(__name__)
S = OrderStatus

# ORD-01: only the arrows in the lifecycle diagram.
ALLOWED = {
    S.DRAFT: {S.CONFIRMED, S.CANCELLED},
    S.CONFIRMED: {S.SHIPPING, S.CANCELLED},
    S.SHIPPING: {S.DELIVERED, S.RETURNED},
    S.DELIVERED: {S.RETURNED},
    S.CANCELLED: set(),
    S.RETURNED: set(),
}
NEEDS_REASON = {S.CANCELLED, S.RETURNED}  # §10: 3–300 characters


def clean_reason(text, label="Reason") -> str:
    text = (text or "").strip()
    if not 3 <= len(text) <= 300:
        raise ValidationError({"reason": f"{label} is required (3–300 characters)."})
    return text


def add_log(order, from_status, to_status, user=None, note=""):
    return OrderStatusLog.objects.create(order=order, from_status=from_status,
                                         to_status=to_status, user=user, note=note[:500])


def add_note(order, user, note):
    """ORD-08 internal notes: a same-status timeline entry, staff-only."""
    note = (note or "").strip()
    if not 1 <= len(note) <= 500:
        raise ValidationError({"note": "Write a note (up to 500 characters)."})
    return add_log(order, order.status, order.status, user, f"Note: {note}")


def _lock_order(order_id) -> Order:
    return Order.objects.select_for_update().get(pk=order_id)


def _lock_variants(order):
    """Lock in ascending id order so concurrent orders cannot deadlock (INV-09)."""
    ids = sorted(order.items.values_list("variant_id", flat=True))
    return {v.id: v for v in Variant.objects.select_for_update().filter(pk__in=ids).order_by("id")}


def _confirm(order, user):
    pay = getattr(order, "payment", None)
    if order.payment_method in (PayMethod.BKASH, PayMethod.NAGAD) and (
            pay is None or pay.status != PayStatus.VERIFIED):
        raise Conflict("Verify the payment before confirming this order.",  # ORD-02
                       code="payment_not_verified")
    locked = _lock_variants(order)
    short = [{"sku": i.sku, "needed": i.quantity, "on_hand": locked[i.variant_id].on_hand}
             for i in order.items.all() if locked[i.variant_id].on_hand < i.quantity]
    if short:  # ORD-03 / §11 last-item race: block with an explanation
        names = ", ".join(
            f"{s['sku']} (needs {s['needed']}, {s['on_hand']} on hand)" for s in short)
        raise Conflict(f"Not enough stock to confirm: {names}.", code="insufficient_stock",
                       fields={"lines": short})
    for i in order.items.all():
        record_movement(locked[i.variant_id], -i.quantity, Reason.ORDER_CONFIRMED, user=user,
                        order_ref=order.number)
    order.confirmed_at = timezone.now()


def _restock(order, reason, user):
    locked = _lock_variants(order)
    for i in order.items.all():
        record_movement(locked[i.variant_id], i.quantity, reason, user=user,
                        order_ref=order.number)


def _release_coupon(order):
    """CPN-04: usage is released when the order is cancelled (kept on Returned)."""
    if order.coupon_code:
        Coupon.objects.select_for_update().filter(code=order.coupon_code, used_count__gt=0)\
            .update(used_count=F("used_count") - 1)


@transaction.atomic
def transition(order_id, to, user=None, reason="", note="", courier="", tracking="") -> Order:
    order = _lock_order(order_id)
    to = S(to)
    frm = S(order.status)
    if to not in ALLOWED[frm]:
        if frm == S.DELIVERED:  # ORD-11: read-only except Return
            raise Conflict("Delivered orders are read-only except for Return.", code="read_only")
        raise Conflict(f"An order cannot move from {frm.label} to {to.label}.",
                       code="invalid_transition")
    why = clean_reason(reason) if to in NEEDS_REASON else ""
    now = timezone.now()
    if to == S.CONFIRMED:
        _confirm(order, user)
    elif to == S.CANCELLED:
        if frm == S.CONFIRMED:
            _restock(order, Reason.ORDER_CANCELLED, user)  # INV-05
        _release_coupon(order)
        order.cancelled_at, order.cancel_reason = now, why
    elif to == S.SHIPPING:
        order.shipped_at = now  # ORD-10: courier + tracking are free text
        order.courier, order.tracking_ref = courier.strip()[:80], tracking.strip()[:80]
    elif to == S.DELIVERED:
        order.delivered_at = now
        pay = getattr(order, "payment", None)
        if pay and pay.method == PayMethod.COD:  # CHK-10: collection recorded at Delivered
            pay.status, pay.verified_by, pay.verified_at = PayStatus.COLLECTED, user, now
            pay.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    elif to == S.RETURNED:
        _restock(order, Reason.ORDER_RETURNED, user)  # INV-06 whole order
        order.returned_at, order.return_reason = now, why
    order.status = to
    order.save()
    add_log(order, frm, to, user, why or note)
    transaction.on_commit(lambda: _notify(order.id, frm, to))
    return order


def _notify(order_id, frm, to):
    from .notifications import queue_status_email

    queue_status_email(order_id, str(frm), str(to))


# ------------------------------------------------------------------ payments


@transaction.atomic
def verify_payment(order_id, user):
    order = _lock_order(order_id)
    pay = _payment(order)
    if pay.method == PayMethod.COD:
        raise Conflict("Cash on delivery payments are collected at delivery.", code="cod")
    if order.status != S.DRAFT:
        raise Conflict("Only Draft orders need payment verification.", code="invalid_state")
    pay.status, pay.verified_by, pay.verified_at, pay.reject_reason = (
        PayStatus.VERIFIED, user, timezone.now(), "")
    pay.save()
    add_log(order, order.status, order.status, user, f"Payment verified (TrxID {pay.trx_id}).")
    return pay


@transaction.atomic
def reject_payment(order_id, user, reason):
    order = _lock_order(order_id)
    pay = _payment(order)
    why = clean_reason(reason, "A rejection reason")  # PAY-03
    if pay.method == PayMethod.COD or order.status != S.DRAFT:
        raise Conflict("Only a Draft order's bKash/Nagad payment can be rejected.",
                       code="invalid_state")
    pay.status, pay.reject_reason, pay.verified_by, pay.verified_at = (
        PayStatus.REJECTED, why, user, timezone.now())
    pay.save()
    # PAY-06: the order stays Draft
    add_log(order, order.status, order.status, user, f"Payment rejected: {why}")
    return pay


@transaction.atomic
def edit_payment(order_id, user, trx_id=None, sender_number=None, note=""):
    """PAY-06: after a rejection staff may correct the TrxID / sender number. The edit keeps the
    per-method uniqueness check, is logged on the timeline, and puts the payment back to Pending."""
    from .checkout import clean_sender, clean_trx

    order = _lock_order(order_id)
    pay = _payment(order)
    if pay.method == PayMethod.COD or order.status not in (S.DRAFT, S.CONFIRMED):
        raise Conflict("This payment cannot be edited.", code="invalid_state")
    before = f"TrxID {pay.trx_id}, sender {pay.sender_number}"
    if trx_id is not None:
        pay.trx_id = clean_trx(trx_id, pay.method, exclude_payment=pay.pk)
    if sender_number is not None:
        pay.sender_number = clean_sender(sender_number)
    if order.status == S.DRAFT:
        pay.status, pay.reject_reason = PayStatus.PENDING, ""
    pay.note = (note or "")[:300]
    pay.save()
    add_log(order, order.status, order.status, user,
            f"Payment edited ({before} → TrxID {pay.trx_id}, sender {pay.sender_number}).")
    return pay


def _payment(order) -> Payment:
    pay = getattr(order, "payment", None)
    if pay is None:
        raise Conflict("This order has no payment record.", code="no_payment")
    return pay


# ------------------------------------------------------------------ edits


EDITABLE = ("name", "phone", "district", "thana", "address_line", "note")


@transaction.atomic
def edit_order(order_id, user, **changes):
    """ORD-09: address, phone and note on Draft/Confirmed orders, logged. Line items and the
    delivery area (which sets the frozen fee) are not editable."""
    from apps.core.phone import normalize_phone

    order = _lock_order(order_id)
    if order.status not in (S.DRAFT, S.CONFIRMED):
        raise Conflict("Only Draft or Confirmed orders can be edited.", code="read_only")
    diffs = []
    for k, v in changes.items():
        if k not in EDITABLE:
            continue
        v = (v or "").strip()
        if k == "phone":
            try:
                v = normalize_phone(v) or ""
            except ValueError as e:
                raise ValidationError({"phone": str(e)}) from None
        if k != "note" and not v:
            raise ValidationError({k: "This field is required."})
        old = getattr(order, k)
        if old != v:
            diffs.append(f"{k}: {old!r} → {v!r}")
            setattr(order, k, v)
    if diffs:
        order.save()
        add_log(order, order.status, order.status, user, "Edited " + "; ".join(diffs))
    return order

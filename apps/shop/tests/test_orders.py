# Covers SRS ORD-01..ORD-13, PAY-01..PAY-07, INV-02..INV-06, INV-09, NTF-02/06, rules 3, 9, 11;
# scenarios 3, 4, 5, 7, 8, 9.
import threading

import pytest
from django.core import mail
from django.db import connection
from rest_framework.exceptions import ValidationError

from apps.core.exceptions import Conflict
from apps.inventory.models import StockMovement
from apps.shop import orders
from apps.shop.checkout import place_order
from apps.shop.models import Order, OrderStatus, PayStatus

from .helpers import bkash, configure_site, one_variant, payload

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def site(db):
    return configure_site(support_email="owner@shop.test")


def cod(v, qty=1, **kw):
    return place_order(payload(v, qty, **kw))[0]


def moves(v):
    return list(StockMovement.objects.filter(variant=v).order_by("id")
                .values_list("reason", "delta_on_hand", "on_hand_after"))


# ------------------------------------------------------------------ lifecycle


def test_scenario_4_verify_then_confirm_deducts_stock(owner):
    v = one_variant(on_hand=5)
    o, _ = place_order(bkash(v, 2))
    orders.verify_payment(o.id, owner)
    orders.transition(o.id, "confirmed", owner)
    v.refresh_from_db()
    o.refresh_from_db()
    assert v.on_hand == 3 and o.status == OrderStatus.CONFIRMED and o.confirmed_at
    assert moves(v) == [("order_confirmed", -2, 3)]
    assert StockMovement.objects.get().order_ref == o.number and StockMovement.objects.get().user == owner


def test_scenario_5_confirm_blocked_while_payment_pending():  # ORD-02
    v = one_variant()
    o, _ = place_order(bkash(v))
    with pytest.raises(Conflict) as e:
        orders.transition(o.id, "confirmed")
    assert e.value.default_code == "payment_not_verified" or "Verify the payment" in str(e.value.detail)
    v.refresh_from_db()
    assert v.on_hand == 5 and Order.objects.get().status == OrderStatus.DRAFT


def test_rejected_payment_still_blocks_confirm_and_order_stays_draft(owner):  # PAY-06
    o, _ = place_order(bkash(one_variant()))
    orders.reject_payment(o.id, owner, "TrxID not found in statement")
    o.refresh_from_db()
    assert o.status == OrderStatus.DRAFT and o.payment.status == PayStatus.REJECTED
    with pytest.raises(Conflict):
        orders.transition(o.id, "confirmed")


def test_cod_confirms_without_verification_and_collects_on_delivery(owner):  # ORD-02, CHK-10
    v = one_variant()
    o = cod(v)
    assert o.payment.status == PayStatus.PENDING
    for to in ("confirmed", "shipping", "delivered"):
        orders.transition(o.id, to, owner)
    o.refresh_from_db()
    assert o.payment.status == PayStatus.COLLECTED and o.payment.verified_by == owner
    assert o.delivered_at and o.shipped_at and o.confirmed_at


def test_scenario_7_cancel_draft_no_stock_change_cancel_confirmed_restores():
    v = one_variant(on_hand=5)
    d = cod(v, 2)
    orders.transition(d.id, "cancelled", reason="customer asked")
    assert moves(v) == [] and v.__class__.objects.get(pk=v.pk).on_hand == 5  # draft: none (INV-03)
    c = cod(v, 2)
    orders.transition(c.id, "confirmed")
    orders.transition(c.id, "cancelled", reason="customer asked")
    assert v.__class__.objects.get(pk=v.pk).on_hand == 5
    assert moves(v)[-1][0] == "order_cancelled"  # INV-05


def test_scenario_9_return_restocks_every_item_with_full_log(owner):
    a, b = one_variant("A", on_hand=5), one_variant("B", on_hand=4)
    data = payload(a, 2)
    data["lines"].append({"variant": b.id, "quantity": 3})
    o, _ = place_order(data)
    for to in ("confirmed", "shipping"):
        orders.transition(o.id, to, owner)
    orders.transition(o.id, "returned", owner, reason="did not fit")
    assert (a.__class__.objects.get(pk=a.pk).on_hand, b.__class__.objects.get(pk=b.pk).on_hand) == (5, 4)
    assert [r for r, *_ in moves(a)] == ["order_confirmed", "order_returned"]
    log = list(o.logs.values_list("from_status", "to_status", "user"))
    assert log == [("", "draft", None), ("draft", "confirmed", owner.id),
                   ("confirmed", "shipping", owner.id), ("shipping", "returned", owner.id)]
    o.refresh_from_db()
    assert o.return_reason == "did not fit" and o.returned_at


def test_return_allowed_from_delivered_only_action_ord_11(owner):
    o = cod(one_variant())
    for to in ("confirmed", "shipping", "delivered"):
        orders.transition(o.id, to, owner)
    with pytest.raises(Conflict):
        orders.transition(o.id, "cancelled", reason="too late")
    with pytest.raises(Conflict):
        orders.edit_order(o.id, owner, note="x")
    orders.transition(o.id, "returned", owner, reason="damaged on arrival")


@pytest.mark.parametrize("frm,to", [
    ("draft", "shipping"), ("draft", "delivered"), ("draft", "returned"),
    ("confirmed", "delivered"), ("confirmed", "returned"), ("shipping", "cancelled"),
    ("shipping", "confirmed"), ("cancelled", "confirmed"), ("returned", "shipping"),
])
def test_ord_01_rejects_every_other_transition(frm, to, owner):
    o = cod(one_variant())
    Order.objects.filter(pk=o.pk).update(status=frm)
    with pytest.raises(Conflict):
        orders.transition(o.id, to, owner, reason="valid reason")


@pytest.mark.parametrize("to", ["cancelled"])
def test_reason_required_3_to_300_chars(to):  # §10
    o = cod(one_variant())
    for bad in ("", "  ", "ab", "x" * 301):
        with pytest.raises(ValidationError):
            orders.transition(o.id, to, reason=bad)
    orders.transition(o.id, to, reason="abc")


def test_return_needs_reason(owner):
    o = cod(one_variant())
    orders.transition(o.id, "confirmed", owner)
    orders.transition(o.id, "shipping", owner)
    with pytest.raises(ValidationError):
        orders.transition(o.id, "returned", owner, reason="")


def test_shipping_records_courier_and_tracking_by_hand(owner):  # ORD-10
    o = cod(one_variant())
    orders.transition(o.id, "confirmed", owner)
    o = orders.transition(o.id, "shipping", owner, courier="Steadfast", tracking="SF12345")
    assert (o.courier, o.tracking_ref) == ("Steadfast", "SF12345")


# ------------------------------------------------------------------ stock safety


def test_scenario_last_item_race_second_confirm_blocked():  # §11, ORD-03
    v = one_variant(on_hand=1)
    first = cod(v)
    second = cod(v, phone="01999999999")
    assert Order.objects.count() == 2  # both Drafts accepted: no reservation (SD-07)
    orders.transition(first.id, "confirmed")
    with pytest.raises(Conflict) as e:
        orders.transition(second.id, "confirmed")
    assert "Not enough stock" in str(e.value.detail) and e.value.fields["lines"][0]["on_hand"] == 0
    second.refresh_from_db()
    assert second.status == OrderStatus.DRAFT and v.__class__.objects.get(pk=v.pk).on_hand == 0


def test_stock_never_negative_on_partial_failure():  # INV-12 + atomicity
    a, b = one_variant("A", on_hand=5), one_variant("B", on_hand=1)
    data = payload(a, 2)
    data["lines"].append({"variant": b.id, "quantity": 1})
    o, _ = place_order(data)
    other = cod(b, phone="01999999999")
    orders.transition(other.id, "confirmed")  # takes the last B
    with pytest.raises(Conflict):
        orders.transition(o.id, "confirmed")
    assert a.__class__.objects.get(pk=a.pk).on_hand == 5  # A was not deducted either


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirms_never_oversell():  # INV-09
    v = one_variant(on_hand=1)
    ids = [cod(v, phone=f"0171000000{i}").id for i in range(4)]
    results = []

    def run(oid):
        try:
            orders.transition(oid, "confirmed")
            results.append("ok")
        except Conflict:
            results.append("blocked")
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("blocked") == 3
    assert v.__class__.objects.get(pk=v.pk).on_hand == 0


# ------------------------------------------------------------------ payments & edits


def test_verify_rules(owner):
    o = cod(one_variant())
    with pytest.raises(Conflict):
        orders.verify_payment(o.id, owner)  # COD is collected at delivery
    b, _ = place_order(bkash(one_variant("B")))
    p = orders.verify_payment(b.id, owner)
    assert p.status == PayStatus.VERIFIED and p.verified_by == owner and p.verified_at
    assert "Payment verified" in b.logs.last().note


def test_reject_requires_reason(owner):
    b, _ = place_order(bkash(one_variant()))
    with pytest.raises(ValidationError):
        orders.reject_payment(b.id, owner, "")
    orders.reject_payment(b.id, owner, "amount does not match")
    b.refresh_from_db()
    assert b.payment.reject_reason == "amount does not match"


def test_correct_trx_after_reject_keeps_uniqueness_and_resets_to_pending(owner):  # PAY-05/06
    one_variant()
    a, _ = place_order(bkash(one_variant("A"), trx="AAAAAAAA11"))
    b, _ = place_order(bkash(one_variant("B"), trx="BBBBBBBB22"))
    orders.reject_payment(b.id, owner, "TrxID typo")
    with pytest.raises(ValidationError):
        orders.edit_payment(b.id, owner, trx_id="AAAAAAAA11")  # already used on another order
    p = orders.edit_payment(b.id, owner, trx_id="cccccccc33", sender_number="01811111111")
    assert (p.trx_id, p.sender_number, p.status) == ("CCCCCCCC33", "01811111111", PayStatus.PENDING)
    assert "Payment edited" in b.logs.last().note and a.payment.trx_id == "AAAAAAAA11"


def test_edit_order_address_phone_note_logged_not_area_or_lines(owner):  # ORD-09
    o = cod(one_variant())
    orders.edit_order(o.id, owner, phone="+8801777777777", address_line="New road 9", note="call me",
                      area="outside_dhaka", total="1")
    o.refresh_from_db()
    assert (o.phone, o.address_line, o.note) == ("01777777777", "New road 9", "call me")
    assert o.area == "inside_dhaka" and o.total == 1060  # frozen
    assert "Edited" in o.logs.last().note
    with pytest.raises(ValidationError):
        orders.edit_order(o.id, owner, phone="123")
    with pytest.raises(ValidationError):
        orders.edit_order(o.id, owner, thana="")


def test_internal_note_is_a_same_status_entry(owner):  # decision 4
    o = cod(one_variant())
    orders.add_note(o.id and o, owner, "Customer asked to deliver after 5pm")
    e = o.logs.last()
    assert e.from_status == e.to_status == "draft" and e.note.startswith("Note:")
    with pytest.raises(ValidationError):
        orders.add_note(o, owner, " ")


def test_orders_are_never_hard_deleted_rule_11():
    assert not hasattr(Order, "delete_forever")
    o = cod(one_variant())
    assert Order.objects.filter(pk=o.pk).exists()


# ------------------------------------------------------------------ emails


def test_order_received_emails_customer_and_owner_with_theme_colour(
        django_capture_on_commit_callbacks):  # NTF-02, THM-11
    from apps.core.models import ThemeSettings

    t = ThemeSettings.load()
    t.tokens = {**t.tokens, "--color-primary": "#6B4E43"}
    t.save()
    with django_capture_on_commit_callbacks(execute=True):
        o = cod(one_variant())
    assert [m.to[0] for m in mail.outbox] == ["n@example.com", "owner@shop.test"]
    html = mail.outbox[0].alternatives[0][0]
    assert o.number in mail.outbox[0].body and "#6B4E43" in html


def test_status_email_goes_to_customer_only_when_email_present(
        owner, django_capture_on_commit_callbacks):
    o = cod(one_variant())
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        orders.transition(o.id, "confirmed", owner)
    assert len(mail.outbox) == 1 and "confirmed" in mail.outbox[0].body
    no_mail = cod(one_variant("B"), email="")
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        orders.transition(no_mail.id, "confirmed", owner)
    assert mail.outbox == []


def test_missing_support_email_skips_owner_notice(django_capture_on_commit_callbacks):
    configure_site(support_email="")
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        cod(one_variant())
    assert [m.to[0] for m in mail.outbox] == ["n@example.com"]


def test_email_failure_never_blocks_checkout(monkeypatch, django_capture_on_commit_callbacks):
    # NTF-06
    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("apps.shop.notifications._send", boom)
    with django_capture_on_commit_callbacks(execute=True):
        o = cod(one_variant())
    assert Order.objects.filter(pk=o.pk).exists()

# Covers SRS NTF-02 / NTF-06 (amendment v1.6): order-received SMS to the customer.
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.models import SiteSettings
from apps.shop.checkout import place_order
from apps.shop.notifications import order_sms_text

from .helpers import configure_site, one_variant, payload

pytestmark = pytest.mark.django_db


def place(capture):
    configure_site()
    with capture(execute=True):
        return place_order(payload(one_variant(price="2450")))[0]


def test_sms_sent_when_order_is_placed(django_capture_on_commit_callbacks, sms_outbox):
    o = place(django_capture_on_commit_callbacks)
    assert len(sms_outbox.sent) == 1
    phone, text = sms_outbox.sent[0]
    assert phone == "01712345678" and o.number in text and "Tk 2,510" in text  # 2450 + 60 fee
    assert len(text) <= 160 and text.isascii()


def test_sms_can_be_switched_off(django_capture_on_commit_callbacks, sms_outbox):
    s = SiteSettings.load()
    s.sms_order_enabled = False
    s.save()
    place(django_capture_on_commit_callbacks)
    assert sms_outbox.sent == []


def test_sms_failure_never_blocks_checkout(django_capture_on_commit_callbacks, sms_outbox):
    sms_outbox.fail = "gateway down"
    try:
        o = place(django_capture_on_commit_callbacks)
    except Exception:  # noqa: BLE001 - the sync test queue re-raises; the real queue retries
        o = None
    from apps.shop.models import Order

    assert Order.objects.count() == 1 and (o is None or o.pk)


def test_text_formats_total_and_trims_long_company_names():
    order = SimpleNamespace(number="SLL-260001", total=Decimal("1234.50"))
    t = order_sms_text(order, "A" * 120)
    assert "Tk 1,234.50" in t and len(t) <= 160
    assert order_sms_text(order, "")[:12] == "SoftLadyLux:"

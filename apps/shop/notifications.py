"""Emails. SRS NTF-02/03/06, THM-11: text + HTML templates in the repository, primary colour
inlined from the saved theme; sent through Django-Q2 with retries; a failure is logged and
never blocks the customer's action."""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from apps.core.models import SiteSettings, ThemeSettings

log = logging.getLogger(__name__)

STATUS_LINES = {  # customer-safe wording
    "confirmed": "Your order has been confirmed and is being prepared.",
    "shipping": "Your order is on its way.",
    "delivered": "Your order has been delivered. Thank you!",
    "cancelled": "Your order has been cancelled.",
    "returned": "Your order has been marked as returned.",
}


def _context(order, **extra):
    site = SiteSettings.load()
    tokens = ThemeSettings.load().tokens or {}
    return {"order": order, "items": list(order.items.all()), "site": site,
            "primary": tokens.get("--color-primary", "#231E1C"),  # THM-11 inlined at send time
            "contrast": tokens.get("--color-primary-contrast", "#F8F4EC"), **extra}


def _send(subject, template, to, ctx):
    text = render_to_string(f"shop/email/{template}.txt", ctx)
    html = render_to_string(f"shop/email/{template}.html", ctx)
    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
    msg.attach_alternative(html, "text/html")
    msg.send()


def send_order_received(order_id):
    """Task: order received (customer, if email present) and new order (owner, support email)."""
    from .models import Order

    order = Order.objects.prefetch_related("items").get(pk=order_id)
    ctx = _context(order)
    if order.email:
        _send(f"We received your order {order.number}", "order_received", order.email, ctx)
    owner = ctx["site"].support_email
    if owner:
        _send(f"New order {order.number}", "new_order_staff", owner, ctx)
    else:
        log.warning("support_email is empty; new-order notice for %s skipped", order.number)


def send_status_email(order_id, from_status, to_status):
    from .models import Order

    order = Order.objects.prefetch_related("items").get(pk=order_id)
    if not order.email or to_status not in STATUS_LINES:
        return
    ctx = _context(order, line=STATUS_LINES[to_status], status=to_status)
    _send(f"Update on your order {order.number}", "status_changed", order.email, ctx)


def _enqueue(func_path, *args):
    """Fire-and-forget through the Django-Q2 queue; any failure is logged only."""
    try:
        from django_q.tasks import async_task

        async_task(func_path, *args, task_name=func_path.rsplit(".", 1)[-1],
                   q_options={"max_attempts": 3})
    except Exception:  # noqa: BLE001 - NTF-06: never block checkout
        log.exception("Could not queue %s", func_path)


def queue_order_received(order_id):
    _enqueue("apps.shop.notifications.send_order_received", order_id)


def queue_status_email(order_id, from_status, to_status):
    _enqueue("apps.shop.notifications.send_status_email", order_id, from_status, to_status)

"""Contact and newsletter emails. SRS NTF-01/02/03/06: text + HTML templates in the repo,
sent through Django-Q2; failures are logged and never shown as errors to the visitor."""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import SiteSettings, ThemeSettings

log = logging.getLogger(__name__)


def _ctx(**extra):
    tokens = ThemeSettings.load().tokens or {}
    return {"site": SiteSettings.load(), "primary": tokens.get("--color-primary", "#231E1C"),
            "contrast": tokens.get("--color-primary-contrast", "#F8F4EC"), **extra}


def _send(subject, template, to, ctx, reply_to=None):
    msg = EmailMultiAlternatives(subject, render_to_string(f"core/email/{template}.txt", ctx),
                                 settings.DEFAULT_FROM_EMAIL, [to],
                                 reply_to=[reply_to] if reply_to else None)
    msg.attach_alternative(render_to_string(f"core/email/{template}.html", ctx), "text/html")
    msg.send()


def send_contact_notice(message_id):
    """NTF-01: the message is emailed to the support email from Settings."""
    from .models import ContactMessage

    m = ContactMessage.objects.get(pk=message_id)
    to = SiteSettings.load().support_email
    if not to:
        log.warning("support_email is empty; contact message %s was stored only", m.pk)
        return
    _send(f"Contact form: {m.subject or 'New message'}", "contact", to, _ctx(m=m), reply_to=m.email)


def send_newsletter_welcome(subscriber_id):
    from .models import Subscriber

    s = Subscriber.objects.get(pk=subscriber_id)
    _send("Welcome to SoftLadyLux", "newsletter_welcome", s.email, _ctx(s=s))


def enqueue(path, *args):
    """NTF-06: queue with retries; if even queuing fails, log and carry on."""
    try:
        from django_q.tasks import async_task

        async_task(path, *args, q_options={"max_attempts": 3})
    except Exception:  # noqa: BLE001
        log.exception("Could not queue %s", path)

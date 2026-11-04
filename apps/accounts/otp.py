"""SMS OTP. SRS ACC-01/ACC-02/ACC-11, §9 (OTP stored hashed, rate limits per mobile and per IP).

Limits: 6-digit code, valid 5 minutes, at most 5 attempts per code, resend only after 60 s,
5 codes per mobile per hour and 10 per day. Every SMS costs money, so a request that breaks a
limit never sends anything. Sending goes through the Django-Q2 queue with retries; the outcome is
stored on the Otp row so the user can be told if the SMS could not be sent (ACC-02)."""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import Throttled, ValidationError

from apps.core.sms import SmsError, get_sms_provider

from .models import Otp, SmsStatus

log = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=5)
RESEND_AFTER = 60  # seconds
MAX_ATTEMPTS = 5
PER_HOUR = 5
PER_DAY = 10

MESSAGES = {
    "register": "Your SoftLadyLux verification code is {code}. It expires in 5 minutes.",
    "reset": "Your SoftLadyLux password reset code is {code}. It expires in 5 minutes.",
}


def hash_code(phone: str, purpose: str, code: str) -> str:
    """Keyed hash (SECRET_KEY) bound to the phone and purpose, so codes can't be replayed."""
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"{phone}:{purpose}:{code}".encode(), hashlib.sha256).hexdigest()


def check_limits(phone: str, purpose: str):
    now = timezone.now()
    recent = Otp.objects.filter(phone=phone)
    last = recent.filter(purpose=purpose).first()
    if last and (wait := RESEND_AFTER - (now - last.sent_at).total_seconds()) > 0:
        raise Throttled(wait=int(wait) + 1, detail=f"Please wait {int(wait) + 1} seconds before "
                        "requesting another code.")
    if recent.filter(sent_at__gte=now - timedelta(hours=1)).count() >= PER_HOUR:
        raise Throttled(detail="Too many codes requested for this number. Try again in an hour.")
    if recent.filter(sent_at__gte=now - timedelta(days=1)).count() >= PER_DAY:
        raise Throttled(detail="Daily code limit reached for this number. Try again tomorrow.")


@transaction.atomic
def issue(phone: str, purpose: str) -> Otp:
    """Create a fresh code (older unused ones for this phone and purpose stop working) and queue
    the SMS. Raises Throttled when a limit is hit, before anything is sent."""
    check_limits(phone, purpose)
    Otp.objects.filter(phone=phone, purpose=purpose, consumed_at__isnull=True).update(
        consumed_at=timezone.now())
    code = f"{secrets.randbelow(10**6):06d}"
    otp = Otp.objects.create(
        phone=phone, purpose=purpose, code_hash=hash_code(phone, purpose, code),
        expires_at=timezone.now() + CODE_TTL)
    message = MESSAGES[purpose].format(code=code)
    transaction.on_commit(lambda: _queue(otp.id, phone, message))
    return otp


def _queue(otp_id: int, phone: str, message: str):
    try:
        from django_q.tasks import async_task

        async_task("apps.accounts.otp.deliver", otp_id, phone, message,
                   q_options={"max_attempts": 3})
    except Exception:  # noqa: BLE001 - the user is told via sms_status, never a crash
        log.exception("Could not queue OTP SMS")
        Otp.objects.filter(pk=otp_id).update(sms_status=SmsStatus.FAILED)


def deliver(otp_id: int, phone: str, message: str):
    """Django-Q2 task: send through the configured provider; raise so the queue retries."""
    try:
        get_sms_provider().send(phone, message)
    except SmsError:
        Otp.objects.filter(pk=otp_id).update(sms_status=SmsStatus.FAILED)
        log.warning("OTP SMS %s failed", otp_id)
        raise
    Otp.objects.filter(pk=otp_id).update(sms_status=SmsStatus.SENT)


def verify(phone: str, purpose: str, code: str) -> Otp:
    """Check a code. A wrong code counts an attempt; the 5th wrong attempt kills the code.

    The attempt counter is saved before the error is raised: raising inside the transaction would
    roll the increment back and the 5-attempt lock would never trigger."""
    code = (code or "").strip()
    error = None
    with transaction.atomic():
        otp = (Otp.objects.select_for_update()
               .filter(phone=phone, purpose=purpose, consumed_at__isnull=True)
               .order_by("-sent_at").first())
        now = timezone.now()
        if otp is None or otp.expires_at < now:
            error = "This code has expired. Request a new one."
        elif otp.attempts >= MAX_ATTEMPTS:
            error = "Too many wrong attempts. Request a new code."
        elif not hmac.compare_digest(otp.code_hash, hash_code(phone, purpose, code)):
            otp.attempts += 1
            if otp.attempts >= MAX_ATTEMPTS:
                otp.consumed_at = now  # locked: a new code is needed
            otp.save(update_fields=["attempts", "consumed_at", "updated_at"])
            left = MAX_ATTEMPTS - otp.attempts
            error = "That code is not correct." + (
                f" {left} attempt{'s' if left != 1 else ''} left." if left else
                " Too many wrong attempts. Request a new code.")
        else:
            otp.consumed_at = now
            otp.save(update_fields=["consumed_at", "updated_at"])
    if error:
        raise ValidationError({"code": error})
    return otp

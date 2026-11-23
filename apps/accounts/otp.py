"""SMS OTP. SRS ACC-01/ACC-02/ACC-11, §9 (OTP stored hashed, rate limits per mobile and per IP).

Limits: 6-digit code, valid 5 minutes, at most 5 attempts per code, resend only after 60 s,
5 codes per mobile per hour and 10 per day. The guest checkout code (v1.11 CHK-11) is 4 digits and
dies after 3 wrong attempts; the other limits are the same. Every SMS costs money, so a request that breaks a
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

from apps.core import sms

from .models import Otp, SmsStatus, User, VerifiedPhone

log = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=5)
RESEND_AFTER = 60  # seconds
MAX_ATTEMPTS = 5
PER_HOUR = 5
PER_DAY = 10

MESSAGES = {
    "register": "Your SoftLadyLux verification code is {code}. It expires in 5 minutes.",
    "reset": "Your SoftLadyLux password reset code is {code}. It expires in 5 minutes.",
    "checkout": "Your SoftLadyLux order verification code is {code}. It expires in 5 minutes.",
}
DIGITS = {"checkout": 4}  # CHK-11 (v1.11); others 6
ATTEMPTS = {"checkout": 3}  # CHK-11 (v1.11): a 4-digit code gets fewer guesses; others MAX_ATTEMPTS
SOURCES = {"register": "otp_register", "reset": "otp_reset", "checkout": "otp_checkout"}  # NTF-07


def max_attempts(purpose: str) -> int:
    return ATTEMPTS.get(purpose, MAX_ATTEMPTS)


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
    digits = DIGITS.get(purpose, 6)
    code = f"{secrets.randbelow(10**digits):0{digits}d}"
    otp = Otp.objects.create(
        phone=phone, purpose=purpose, code_hash=hash_code(phone, purpose, code),
        expires_at=timezone.now() + CODE_TTL)
    message = MESSAGES[purpose].format(code=code)
    source = SOURCES[purpose]
    transaction.on_commit(lambda: _queue(otp.id, phone, message, source, code))
    return otp


def _queue(otp_id: int, phone: str, message: str, source: str, code: str):
    log_id = sms.log_pending(phone, message, source, secret=code)  # NTF-07, code masked
    try:
        from django_q.tasks import async_task

        async_task("apps.accounts.otp.deliver", otp_id, phone, message, log_id, source, code,
                   q_options={"max_attempts": 3})
    except Exception as e:  # noqa: BLE001 - the user is told via sms_status, never a crash
        log.exception("Could not queue OTP SMS")
        Otp.objects.filter(pk=otp_id).update(sms_status=SmsStatus.FAILED)
        sms._update_log(log_id, status="failed", error=f"Could not queue: {e}"[:300])


def deliver(otp_id: int, phone: str, message: str, log_id: int | None = None,
            source: str = "other", code: str | None = None):
    """Django-Q2 task: send through MiMSMS (ACC-02); raise so the queue retries. Logged (NTF-07)."""
    try:
        sms.send(phone, message, source, log_id=log_id, secret=code)
    except sms.SmsError:
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
        elif otp.attempts >= (limit := max_attempts(purpose)):
            error = "Too many wrong attempts. Request a new code."
        elif not hmac.compare_digest(otp.code_hash, hash_code(phone, purpose, code)):
            otp.attempts += 1
            if otp.attempts >= limit:
                otp.consumed_at = now  # locked: a new code is needed
            otp.save(update_fields=["attempts", "consumed_at", "updated_at"])
            left = limit - otp.attempts
            error = "That code is not correct." + (
                f" {left} attempt{'s' if left != 1 else ''} left." if left else
                " Too many wrong attempts. Request a new code.")
        else:
            otp.consumed_at = now
            otp.save(update_fields=["consumed_at", "updated_at"])
    if error:
        raise ValidationError({"code": error})
    return otp


# ------------------------------------------------------------------ guest checkout (v1.11 CHK-11)


def phone_is_verified(phone: str) -> bool:
    """D6: a number counts as verified when a guest proved it at checkout, or it belongs to a
    registered account whose number was verified at registration."""
    return (VerifiedPhone.objects.filter(phone=phone).exists()
            or User.objects.filter(phone=phone, phone_verified=True).exists())


def verify_checkout(phone: str, code: str) -> VerifiedPhone:
    verify(phone, "checkout", code)
    vp, _ = VerifiedPhone.objects.update_or_create(phone=phone,
                                                   defaults={"verified_at": timezone.now()})
    return vp

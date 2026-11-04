import logging
import re

from apps.accounts import otp as otp_service
from apps.accounts.models import Otp

REG = {"full_name": "Nusrat Jahan", "phone": "01712345678", "password": "secret-pass-1",
       "email": "nusrat@example.com"}


class CodeCatcher(logging.Handler):
    """The fake SMS provider only logs the message: read the 6-digit code from that log."""

    def __init__(self):
        super().__init__()
        self.codes = []

    def emit(self, record):
        m = re.search(r"\b(\d{6})\b", record.getMessage())
        if m:
            self.codes.append(m.group(1))


def catch_codes():
    h = CodeCatcher()
    logging.getLogger("sms.fake").addHandler(h)
    logging.getLogger("sms.fake").setLevel(logging.INFO)
    return h


def stop(h):
    logging.getLogger("sms.fake").removeHandler(h)


def age_otps(phone, seconds=61):
    """Pretend the last code was sent `seconds` ago (to get past the 60 s resend gap)."""
    from datetime import timedelta

    from django.utils import timezone

    Otp.objects.filter(phone=phone).update(sent_at=timezone.now() - timedelta(seconds=seconds))


__all__ = ["REG", "catch_codes", "stop", "age_otps", "otp_service"]

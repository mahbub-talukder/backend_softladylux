from apps.accounts import otp as otp_service
from apps.accounts.models import Otp

REG = {"full_name": "Nusrat Jahan", "phone": "01712345678", "password": "secret-pass-1",
       "email": "nusrat@example.com"}


def age_otps(phone, seconds=61):
    """Pretend the last code was sent `seconds` ago (to get past the 60 s resend gap)."""
    from datetime import timedelta

    from django.utils import timezone

    Otp.objects.filter(phone=phone).update(sent_at=timezone.now() - timedelta(seconds=seconds))


__all__ = ["REG", "age_otps", "otp_service"]

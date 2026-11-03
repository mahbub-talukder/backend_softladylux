from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import SmsError, SmsProvider
from .fake import FakeSmsProvider

__all__ = ["SmsError", "SmsProvider", "FakeSmsProvider", "get_sms_provider"]

# SRS ACC-02 / §15: provider chosen by SMS_PROVIDER.
# The MiMSMS provider (non-masking API) registers here in M4, behind the same interface.
PROVIDERS: dict[str, type[SmsProvider]] = {"fake": FakeSmsProvider}


def get_sms_provider() -> SmsProvider:
    name = settings.SMS_PROVIDER
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise ImproperlyConfigured(
            f"Unknown SMS_PROVIDER '{name}'. Available: {', '.join(sorted(PROVIDERS))}."
        ) from None

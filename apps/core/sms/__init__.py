from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import SmsError, SmsProvider
from .fake import FakeSmsProvider
from .mimsms import MiMsmsProvider

__all__ = ["SmsError", "SmsProvider", "FakeSmsProvider", "MiMsmsProvider", "get_sms_provider"]

# SRS ACC-02 / §15: provider chosen by SMS_PROVIDER.
PROVIDERS: dict[str, type[SmsProvider]] = {"fake": FakeSmsProvider, "mimsms": MiMsmsProvider}


def get_sms_provider() -> SmsProvider:
    name = settings.SMS_PROVIDER
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise ImproperlyConfigured(
            f"Unknown SMS_PROVIDER '{name}'. Available: {', '.join(sorted(PROVIDERS))}."
        ) from None

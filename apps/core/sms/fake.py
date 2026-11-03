import logging

from .base import SmsProvider

logger = logging.getLogger("sms.fake")


class FakeSmsProvider(SmsProvider):
    """SRS §15: fake sender selected by SMS_PROVIDER=fake (default in dev/test).

    Dev/test sender: only logs the message (including the OTP). Sends nothing."""

    def send(self, phone: str, message: str) -> None:
        logger.info("FAKE SMS to %s: %s", phone, message)

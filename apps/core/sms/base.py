from abc import ABC, abstractmethod


class SmsError(Exception):
    """Raised when a provider fails to send a message."""


class SmsProvider(ABC):
    """Pluggable SMS provider interface.

    SRS ACC-02 (provider interface), §15 decisions (fake sender now, MiMSMS later).
    Implementations must never hard-code credentials (env or Settings only).
    """

    @abstractmethod
    def send(self, phone: str, message: str) -> None:
        """Send `message` to the normalised mobile `phone`; raise SmsError on failure."""

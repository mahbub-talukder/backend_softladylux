# Covers SRS ACC-02, §15 (fake SMS provider, SMS_PROVIDER selection).
import logging

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.core.sms import FakeSmsProvider, get_sms_provider


def test_default_is_fake_and_only_logs(caplog):
    provider = get_sms_provider()
    assert isinstance(provider, FakeSmsProvider)
    with caplog.at_level(logging.INFO, logger="sms.fake"):
        provider.send("01712345678", "Your code is 123456")
    assert "123456" in caplog.text


def test_unknown_provider_fails_clearly(settings):
    settings.SMS_PROVIDER = "nope"
    with pytest.raises(ImproperlyConfigured):
        get_sms_provider()

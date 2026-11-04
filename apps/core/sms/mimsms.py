"""MiMSMS (non-masking) provider. SRS ACC-02.

The MiMSMS HTTP API details (URL, method, parameter names, success marker) are NOT in the SRS and
must not be guessed, so everything is configuration: env settings (MIMSMS_*) or Admin → Settings →
SMS. Nothing is sent until an endpoint is configured. The adapter is unverified against the real
service until the owner has an account (known risk).

Config shape (env var MIMSMS_PARAMS is JSON, or SiteSettings.mimsms_params):
    {"api_key": "<name>", "sender": "<name>", "to": "<name>", "text": "<name>"}
The key and sender VALUES come from MIMSMS_API_KEY / MIMSMS_SENDER_ID (env) or Settings.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from .base import SmsError, SmsProvider

REQUIRED = ("api_key", "to", "text")  # sender is optional


def load_config() -> dict:
    """Env wins over the Settings screen (ACC-02: credentials from env or Settings)."""
    from apps.core.models import SiteSettings

    site = SiteSettings.load()
    try:
        env_params = json.loads(settings.MIMSMS_PARAMS) if settings.MIMSMS_PARAMS else {}
    except ValueError:
        env_params = {}
    return {
        "endpoint": settings.MIMSMS_ENDPOINT or site.mimsms_endpoint,
        "method": (settings.MIMSMS_METHOD or site.mimsms_method or "POST").upper(),
        "params": env_params or site.mimsms_params or {},
        "api_key": settings.MIMSMS_API_KEY or site.mimsms_api_key,
        "sender": settings.MIMSMS_SENDER_ID or site.mimsms_sender_id,
        "success_contains": settings.MIMSMS_SUCCESS_CONTAINS or site.mimsms_success_contains,
    }


class MiMsmsProvider(SmsProvider):
    timeout = 10

    def send(self, phone: str, message: str) -> None:
        cfg = load_config()
        names = cfg["params"]
        if not cfg["endpoint"] or not cfg["api_key"] or any(not names.get(k) for k in REQUIRED):
            raise SmsError("MiMSMS is not configured (endpoint, API key and parameter names).")
        fields = {names["api_key"]: cfg["api_key"], names["to"]: phone, names["text"]: message}
        if cfg["sender"] and names.get("sender"):
            fields[names["sender"]] = cfg["sender"]
        data = urllib.parse.urlencode(fields).encode()
        url, body = cfg["endpoint"], None
        if cfg["method"] == "GET":
            url = f"{url}{'&' if '?' in url else '?'}{data.decode()}"
        else:
            body = data
        try:
            req = urllib.request.Request(url, data=body, method=cfg["method"])
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                text = resp.read().decode("utf-8", "replace")
                status = resp.status
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SmsError(f"MiMSMS request failed: {e}") from None
        if status >= 400 or (cfg["success_contains"] and cfg["success_contains"] not in text):
            raise SmsError(f"MiMSMS rejected the message (HTTP {status}).")

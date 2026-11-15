"""SMS through the MiMSMS JSON API. SRS ACC-02, NTF-02 (amendment v1.6).

One function, no provider switch, no fake sender. Every value comes from Admin -> Settings -> SMS
(SiteSettings); nothing is hard-coded and there are no environment variables. While the settings are
incomplete every send fails with SmsError("SMS is not configured.").

Request (POST, JSON):
    {"apiKey", "userName", "senderName", "transactionType": "T", "mobileNumber": "8801XXXXXXXXX",
     "message"}
Success = HTTP 2xx and a JSON reply with statusCode "200"; the provider's trxnId is returned.

Callers use `sms.send_sms(...)` (module attribute), so tests replace it in one place. The API key is
never logged or put in an error message.
"""

import json
import logging
import urllib.error
import urllib.request

from apps.core.phone import normalize_phone

log = logging.getLogger(__name__)

TIMEOUT = 10  # seconds
TRANSACTION_TYPE = "T"  # transactional: OTP and order-received are both transactional


class SmsError(Exception):
    """The message was not accepted by the gateway (or SMS is not configured)."""


def to_msisdn(phone: str) -> str:
    """01XXXXXXXXX / +8801XXXXXXXXX -> 8801XXXXXXXXX (the format MiMSMS expects)."""
    try:
        local = normalize_phone(phone)
    except ValueError:
        local = None
    if not local:
        raise SmsError("Invalid mobile number.")
    return "88" + local


def _config():
    from apps.core.models import SiteSettings

    s = SiteSettings.load()
    cfg = {"url": s.mimsms_url.strip(), "apiKey": s.mimsms_api_key.strip(),
           "userName": s.mimsms_username.strip(), "senderName": s.mimsms_sender_name.strip()}
    if not all(cfg.values()):
        raise SmsError("SMS is not configured. Fill in Admin → Settings → SMS.")
    return cfg


def send_sms(phone: str, message: str) -> str:
    """Send one SMS. Returns the gateway transaction id; raises SmsError on any failure."""
    cfg = _config()
    number = to_msisdn(phone)
    body = json.dumps({
        "apiKey": cfg["apiKey"], "userName": cfg["userName"], "senderName": cfg["senderName"],
        "transactionType": TRANSACTION_TYPE, "mobileNumber": number, "message": message,
    }).encode()
    req = urllib.request.Request(cfg["url"], data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - admin-set URL
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise SmsError(f"SMS gateway error (HTTP {e.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SmsError(f"SMS gateway unreachable: {getattr(e, 'reason', e)}") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise SmsError("SMS gateway sent an unexpected reply.") from None
    if not isinstance(data, dict) or str(data.get("statusCode")) != "200":
        data = data if isinstance(data, dict) else {}
        detail = str(data.get("responseResult") or "unknown error")
        errs = data.get("error_Data")
        first = errs[0] if isinstance(errs, list) and errs and isinstance(errs[0], dict) else {}
        ip = first.get("errorParm") or ""
        if "ip" in detail.lower() and ip:  # seen live: 401 "IP Black List", errorParm = caller's IP
            detail += f" (add this server's IP {ip} to the MiMSMS IP whitelist)"
        raise SmsError(f"SMS rejected: {detail}"[:200])
    trxn = str(data.get("trxnId") or "")
    log.info("SMS sent to %s****** (trxn %s)", number[:7], trxn)
    return trxn


__all__ = ["SmsError", "send_sms", "to_msisdn", "TRANSACTION_TYPE"]

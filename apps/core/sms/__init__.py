"""SMS through the MiMSMS JSON API. SRS ACC-02, NTF-02 (amendment v1.6).

One function, no provider switch, no fake sender. Every value comes from Admin -> Settings -> SMS
(SiteSettings); nothing is hard-coded and there are no environment variables. While the settings are
incomplete every send fails with SmsError("SMS is not configured.").

Request (POST, JSON):
    {"apiKey", "userName", "senderName", "transactionType": "T", "mobileNumber": "8801XXXXXXXXX",
     "message"}
Success = HTTP 2xx and a JSON reply with statusCode "200"; the provider's trxnId is returned.

Callers use `sms.send(...)` (v1.11 NTF-07), which writes the SMS log and calls `send_sms`, the bare
gateway call; tests replace `sms.send_sms` (module attribute) in one place. The API key is
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


# ------------------------------------------------------------------ SMS log (SRS v1.11 NTF-07)


def _log_model():
    from apps.core.models import SmsLog

    return SmsLog


def _receiver(phone) -> str:
    try:
        return normalize_phone(phone) or str(phone or "")[:11]
    except ValueError:
        return str(phone or "")[:11]


def _masked(message: str, secret: str | None) -> str:
    """NTF-07: OTP codes never reach the log."""
    return message.replace(secret, "*" * len(secret)) if secret else message


def log_pending(phone, message: str, source: str, secret: str | None = None) -> int | None:
    """Create a Pending log row before a send is queued. Returns its id, or None if logging failed:
    the log must never block or break a send (NTF-07)."""
    try:
        return _log_model().objects.create(receiver=_receiver(phone), source=source,
                                           message=_masked(message, secret)).pk
    except Exception:  # noqa: BLE001 - logging is best-effort
        log.exception("Could not write the SMS log")
        return None


def _update_log(log_id, **fields):
    if not log_id:
        return
    try:
        from django.db.models import F

        if fields.pop("count_attempt", False):
            fields["attempts"] = F("attempts") + 1
        _log_model().objects.filter(pk=log_id).update(**fields)
    except Exception:  # noqa: BLE001 - logging is best-effort
        log.exception("Could not update the SMS log")


def send(phone, message: str, source: str, *, log_id: int | None = None,
         secret: str | None = None) -> str:
    """Send one SMS and record it in the SMS log (NTF-07): Pending -> In Progress -> Sent / Failed.
    Every caller goes through here; `send_sms` stays the bare gateway call. Pass `log_id` from
    log_pending() when the send was queued; queue retries reuse the same row."""
    from django.utils import timezone

    Status = _log_model().Status
    if log_id is None:
        log_id = log_pending(phone, message, source, secret)
    _update_log(log_id, status=Status.IN_PROGRESS, count_attempt=True)
    try:
        ref = send_sms(phone, message)  # module attribute, so tests can replace it
    except Exception as e:
        _update_log(log_id, status=Status.FAILED, error=str(e)[:300])
        raise
    _update_log(log_id, status=Status.SENT, provider_ref=str(ref or "")[:80], error="",
                sent_at=timezone.now())
    return ref


__all__ = ["SmsError", "send_sms", "send", "log_pending", "to_msisdn", "TRANSACTION_TYPE"]

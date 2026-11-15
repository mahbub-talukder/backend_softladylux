"""Steadfast Courier API client. SRS v1.7 CRR-01..CRR-15; API notes in docs/STEADFAST_API_NOTES.md.

Every value comes from Admin → Settings → Courier (SiteSettings, CRR-01); there are no environment
variables. All HTTP goes through the module-level `request()` so tests replace it in one place and
never reach Steadfast (it has no sandbox: every create_order is a real parcel).

CRR-06 lockout guard: a 401/403 sets `SiteSettings.steadfast_keys_rejected` and every later call
raises at once without touching the network, until the courier settings are saved again. Steadfast
refuses a client for 60 minutes after 10 auth failures, so retrying a 401 would make things worse.

Keys are never logged or put into an error message.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

TIMEOUT = 15  # seconds
USER_AGENT = "SoftLadyLux/1.0 (+courier integration)"
KEYS_REJECTED = "Steadfast keys rejected. Check Admin → Settings → Courier."
NOT_CONFIGURED = "Steadfast is not configured. Fill in Admin → Settings → Courier."


class SteadfastError(Exception):
    """A call failed. `status` is the HTTP (or body) status, None when Steadfast was unreachable.
    `errors` carries Steadfast's field messages ({field: [msg]}) on a 400/422."""

    def __init__(self, message, status=None, errors=None, code="error"):
        super().__init__(message)
        self.status, self.errors, self.code = status, errors or {}, code

    @property
    def retryable(self) -> bool:
        """CRR-05: only an unreachable service or a 5xx is worth retrying; 4xx never is."""
        return self.status is None or self.status >= 500


def request(method, url, headers, body=None, timeout=TIMEOUT):
    """The single network point. Returns (http_status, parsed_json_or_None). Raises SteadfastError
    (status None) when Steadfast cannot be reached."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URL
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("Steadfast unreachable: %s %s (%s)", method, url.split("?")[0],
                    type(e).__name__)
        raise SteadfastError("Steadfast could not be reached. Try again shortly.") from None
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, None


# ------------------------------------------------------------------ configuration


def _site():
    from apps.core.models import SiteSettings

    return SiteSettings.load()


def is_configured() -> bool:
    s = _site()
    return bool(s.steadfast_api_key.strip() and s.steadfast_secret_key.strip())


def _block():
    from apps.core.models import SiteSettings

    SiteSettings.objects.filter(pk=1).update(steadfast_keys_rejected=True)
    log.error("Steadfast rejected the API keys; courier calls are paused until settings are saved.")


def _message(data, status):
    if isinstance(data, dict):
        for k in ("message", "error"):
            if isinstance(data.get(k), str) and data[k]:
                return data[k]
    return f"Steadfast answered {status}."


def _errors(data) -> dict:
    """Normalise Steadfast's `errors` to {field: [message, ...]}."""
    errs = data.get("errors") if isinstance(data, dict) else None
    if not isinstance(errs, dict):
        return {}
    return {k: [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in errs.items()}


def call(method, path, body=None, params=None, auth=True):
    """Call one endpoint and return the JSON body. Raises SteadfastError on any failure."""
    s = _site()
    base = (s.steadfast_base_url or "").strip().rstrip("/")
    if not base:
        raise SteadfastError(NOT_CONFIGURED, status=0, code="not_configured")
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if auth:
        if s.steadfast_keys_rejected:  # CRR-06: never touch the network again
            raise SteadfastError(KEYS_REJECTED, status=401, code="keys_rejected")
        key, secret = s.steadfast_api_key.strip(), s.steadfast_secret_key.strip()
        if not (key and secret):
            raise SteadfastError(NOT_CONFIGURED, status=0, code="not_configured")
        headers |= {"Api-Key": key, "Secret-Key": secret}
    url = base + path
    if params:
        query = {k: v for k, v in params.items() if v not in (None, "")}
        url += "?" + urllib.parse.urlencode(query)
    status, data = request(method, url, headers, body)
    # Some endpoints answer HTTP 200 with the real status in the body.
    body_status = data.get("status") if isinstance(data, dict) else None
    if isinstance(body_status, int) and body_status >= 400:
        status = body_status
    if status in (401, 403):
        # CRR-06: wrong/revoked keys (401) or an inactive / KYC-blocked account (403). Either way
        # further calls cannot succeed, so stop them all until the owner saves the settings.
        if auth:
            _block()
            msg = KEYS_REJECTED if status == 401 else (
                f"Steadfast refused the account: {_message(data, status)} "
                "Fix it in the Steadfast dashboard, then save Admin → Settings → Courier.")
            raise SteadfastError(msg, status=status, code="keys_rejected")
        raise SteadfastError(_message(data, status), status=status, code="forbidden")
    if status == 429:
        raise SteadfastError("Steadfast is limiting requests. Try again in a few minutes.",
                             status=429, code="throttled")
    if status >= 400:
        raise SteadfastError(_message(data, status), status=status, errors=_errors(data))
    return data if data is not None else {}


# ------------------------------------------------------------------ endpoints


def ping():  # CRR-02: no keys needed
    return call("GET", "/ping", auth=False)


def balance():  # CRR-02 key check, CRR-12
    return call("GET", "/get_balance")


def create_order(payload):  # CRR-03
    return call("POST", "/create_order", body=payload)


def bulk_create(orders):  # CRR-07: the /extended variant gives readable per-order messages
    return call("POST", "/create_order/bulk-order/extended", body={"data": orders})


def status_by_cid(consignment_id):  # CRR-11
    return call("GET", f"/status_by_cid/{int(consignment_id)}")


def status_with_return(consignment_id):  # CRR-11 returns
    return call("GET", f"/status_with_return_status_by_cid/{int(consignment_id)}")


def trackings_by_invoice(invoice):  # CRR-10 back-fill
    return call("GET", f"/trackings_by_invoice/{urllib.parse.quote(invoice, safe='')}")


def parcel_states():  # CRR-12
    return call("GET", "/parcel_states")


PARCEL_STATES = ("in_review", "delivery_processing", "cod_processing", "return_requests")


def parcels_in_state(state, page=1, per_page=20):  # CRR-12 drill-down
    if state not in PARCEL_STATES:
        raise SteadfastError("Unknown parcel state.", status=404)
    return call("GET", f"/parcel_states/{state}",
                params={"page": page, "per_page": per_page, "sort": "desc"})


def stats(date_from=None, date_to=None):  # CRR-12
    return call("GET", "/stats", params={"from": date_from, "to": date_to})


def police_stations():  # CRR-13 pickup form (response shape not documented: read defensively)
    return call("GET", "/police_stations")


def create_pickup(payload):  # CRR-13
    return call("POST", "/create_pickup_request", body=payload)


def create_return(payload):  # CRR-14
    return call("POST", "/create_return_request", body=payload)


def return_requests(page=1):  # CRR-14 (response shape not documented: read defensively)
    return call("GET", "/get_return_requests", params={"page": page})


def return_request(request_id):  # CRR-14
    return call("GET", f"/get_return_request/{int(request_id)}")


def fraud_score(phone):  # CRR-15
    return call("GET", f"/fraud_check/score/{urllib.parse.quote(phone, safe='')}")

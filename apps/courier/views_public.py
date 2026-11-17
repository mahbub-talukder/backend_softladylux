"""Steadfast webhook receiver. SRS v1.7 CRR-08.

Steadfast POSTs JSON with `Authorization: Bearer <token>` and `X-Signature` = hex HMAC-SHA256 of
the raw body keyed with the same token. We answer within its 5-second limit: verify, store, queue,
reply 200. A 4xx is never retried by Steadfast, a 5xx is retried after 30 s and 2 min.
"""

import hashlib
import hmac
import json

from django.db import transaction
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import SiteSettings
from apps.core.throttles import SteadfastWebhookThrottle

from .models import WebhookReceipt


def signature_ok(token: str, raw: bytes, auth_header: str, signature: str) -> bool:
    """CRR-08: both the bearer token and the body signature must match (constant-time)."""
    if not token:
        return False
    expected = hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()
    return (hmac.compare_digest((auth_header or "").encode(), f"Bearer {token}".encode())
            and hmac.compare_digest((signature or "").strip().lower().encode(), expected.encode()))


class SteadfastWebhookView(APIView):
    authentication_classes = []  # no cookies or JWT: the signature is the authentication
    permission_classes = [AllowAny]
    throttle_classes = [SteadfastWebhookThrottle]

    def post(self, request):
        raw = request.body
        token = SiteSettings.load().steadfast_webhook_token.strip()
        if not signature_ok(token, raw, request.headers.get("Authorization", ""),
                            request.headers.get("X-Signature", "")):
            return Response({"status": "error", "message": "Invalid signature."}, status=401)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return Response({"status": "error", "message": "Body is not JSON."}, status=400)
        if not isinstance(payload, dict):
            return Response({"status": "error", "message": "Body is not an object."}, status=400)
        key = (request.headers.get("Idempotency-Key") or "").strip()[:200] or \
            hashlib.sha256(raw).hexdigest()  # no key: the body itself identifies a repeat
        receipt, created = WebhookReceipt.objects.get_or_create(
            idempotency_key=key,
            defaults={"notification_type": str(payload.get("notification_type", ""))[:40],
                      "payload": payload})
        if created:
            from apps.shop.notifications import _enqueue

            transaction.on_commit(
                lambda: _enqueue("apps.courier.services.process_webhook", receipt.id))
        return Response({"status": "success"})

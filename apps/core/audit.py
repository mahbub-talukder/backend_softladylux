"""Audit log writer. SRS ADM-10, SET-07, THM-10, PRM-07: who changed what, when, old/new."""

from .models import AuditLog


def mask_ip(ip: str | None) -> str:
    """§9 privacy: keep only the network part (last IPv4 octet / IPv6 tail zeroed)."""
    if not ip:
        return ""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::"
    parts = ip.split(".")
    return ".".join(parts[:3] + ["0"]) if len(parts) == 4 else ""


def client_ip(request) -> str | None:
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")


def log(request, action: str, model: str, object_id="", before=None, after=None, user=None):
    return AuditLog.objects.create(
        user=user or getattr(request, "user", None),
        action=action,
        model=model,
        object_id=str(object_id),
        before=before,
        after=after,
        ip_masked=mask_ip(client_ip(request)),
    )


def diff(obj, fields) -> dict:
    """Snapshot of selected fields as JSON-safe strings, for before/after."""
    return {f: (None if getattr(obj, f) is None else str(getattr(obj, f))) for f in fields}

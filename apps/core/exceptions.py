from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler


def _flatten(detail):
    if isinstance(detail, dict):
        return {k: _flatten(v) for k, v in detail.items()}
    if isinstance(detail, list):
        return [str(i) if not isinstance(i, dict | list) else _flatten(i) for i in detail]
    return str(detail)


class Conflict(APIException):
    """§7: HTTP 409 for stock / state / price conflicts. `fields` carries per-line detail."""

    status_code = 409
    default_code = "conflict"
    default_detail = "The request conflicts with the current state."

    def __init__(self, message=None, code=None, fields=None):
        super().__init__(message, code)
        self.fields = fields or {}


def api_exception_handler(exc, context):
    """SRS §7 Conventions: errors are {code, message, fields{}}.

    400 validation, 401/403 auth and permission (PRM-03), 404,
    409 stock/state conflicts, 429 rate limit.
    """
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, ValidationError):
        fields = _flatten(exc.detail) if isinstance(exc.detail, dict) else {}
        message = "Please correct the highlighted fields."
        if isinstance(exc.detail, list):
            message = str(exc.detail[0])
        return Response(
            {"code": "validation_error", "message": message, "fields": fields},
            status=response.status_code,
        )
    code = getattr(exc, "default_code", "error")
    message = str(getattr(exc, "detail", "Error"))
    fields = getattr(exc, "fields", {})
    return Response({"code": code, "message": message, "fields": fields},
                    status=response.status_code)

# Covers SRS §7 Conventions (error shape).
from rest_framework.exceptions import NotFound, ValidationError

from apps.core.exceptions import api_exception_handler


def test_validation_error_shape():
    resp = api_exception_handler(ValidationError({"phone": ["Bad number."]}), {})
    assert resp.status_code == 400
    assert resp.data["code"] == "validation_error"
    assert resp.data["fields"] == {"phone": ["Bad number."]}


def test_other_error_shape():
    resp = api_exception_handler(NotFound(), {})
    assert resp.status_code == 404
    assert resp.data["code"] == "not_found"
    assert resp.data["fields"] == {}


def test_unhandled_returns_none():
    assert api_exception_handler(RuntimeError("x"), {}) is None

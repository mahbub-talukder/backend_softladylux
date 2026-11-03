from unittest import mock

import pytest

# Covers SRS §9 Observability (health endpoint).
pytestmark = pytest.mark.django_db


def test_health_ok(api_client):
    r = api_client.get("/api/v1/health/")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_health_is_public_and_read_only(api_client):
    assert api_client.post("/api/v1/health/").status_code == 405


def test_health_reports_db_failure(api_client):
    with mock.patch("apps.core.views.connection.cursor", side_effect=RuntimeError("down")):
        r = api_client.get("/api/v1/health/")
    assert r.status_code == 503
    assert r.json()["database"] == "down"

# Covers SRS ARC-06 (credentialed cross-origin calls from the Nuxt app only).
import pytest

pytestmark = pytest.mark.django_db


def test_cors_allows_credentials_for_listed_origin(api_client, settings):
    settings.CORS_ALLOWED_ORIGINS = ["http://localhost:3000"]
    r = api_client.get("/api/v1/health/", HTTP_ORIGIN="http://localhost:3000")
    assert r["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert r["Access-Control-Allow-Credentials"] == "true"


def test_cors_blocks_unlisted_origin(api_client):
    r = api_client.get("/api/v1/health/", HTTP_ORIGIN="http://evil.example")
    assert "Access-Control-Allow-Origin" not in r

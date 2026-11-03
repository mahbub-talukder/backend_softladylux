"""Pytest config + the API route-coverage gate.

SRS §9 Testability, §12 scenario 24, SD-28 (100% API route coverage; CI fails otherwise).

Every request made through the `api_client` fixture is recorded as (route, METHOD).
At the end of a full run, every route under /api/v1/ and each HTTP method its view
implements must have been hit by at least one test, otherwise the run fails.
Set ROUTE_GATE=0 to skip the gate when running a subset of tests.
"""

import os

import pytest
from rest_framework.test import APIClient

HIT: set[tuple[str, str]] = set()
SKIP_METHODS = {"options", "head"}


class RecordingClient(APIClient):
    def request(self, **kwargs):
        response = super().request(**kwargs)
        match = getattr(response, "resolver_match", None)
        if match is not None:
            HIT.add((match.route, kwargs["REQUEST_METHOD"].upper()))
        return response


@pytest.fixture
def api_client():
    return RecordingClient()


@pytest.fixture(autouse=True)
def _clear_cache():
    """SET-06/WEB-10: public content is cached; start every test cold."""
    from django.core.cache import cache

    cache.clear()


@pytest.fixture(autouse=True)
def _tmp_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def owner(db):
    from apps.accounts.models import User

    return User.objects.create_superuser(email="owner@example.com", password="owner-pass-123",
                                         full_name="Owner")


@pytest.fixture
def make_staff(db):
    """PRM-01: a Staff user holding exactly the given permission codes."""
    from apps.accounts.models import Role, User
    from apps.accounts.permissions import set_permissions

    counter = {"n": 0}

    def _make(*codes):
        counter["n"] += 1
        u = User.objects.create_user(email=f"staff{counter['n']}@example.com",
                                     password="staff-pass-123", full_name="Staff", role=Role.STAFF)
        set_permissions(u, codes)
        return u

    return _make


@pytest.fixture
def as_user(api_client):
    def _as(user):
        api_client.force_authenticate(user)
        return api_client

    return _as


def _api_routes(patterns=None, prefix=""):
    from django.urls import URLPattern, URLResolver, get_resolver

    patterns = patterns if patterns is not None else get_resolver().url_patterns
    for p in patterns:
        # Mirror Django's ResolverMatch.route: a child's leading "^" is dropped after a prefix.
        own = str(p.pattern)
        route = prefix + (own[1:] if prefix and own.startswith("^") else own)
        if isinstance(p, URLResolver):
            yield from _api_routes(p.url_patterns, route)
        elif isinstance(p, URLPattern) and route.startswith("api/v1/"):
            cb = p.callback
            actions = getattr(cb, "actions", None)  # ViewSets
            if actions:
                # Honour http_method_names (edit-only viewsets); skip HEAD/OPTIONS.
                allowed = set(getattr(getattr(cb, "cls", None), "http_method_names", actions))
                methods = {m.upper() for m in actions if m in allowed and m not in SKIP_METHODS}
            else:
                cls = getattr(cb, "cls", None) or getattr(cb, "view_class", None)
                methods = {
                    m.upper()
                    for m in getattr(cls, "http_method_names", [])
                    if m not in SKIP_METHODS and hasattr(cls, m)
                }
            for m in methods:
                yield route, m


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("ROUTE_GATE", "1") == "0" or exitstatus not in (0,):
        return
    import django

    django.setup()
    missing = sorted(set(_api_routes()) - HIT)
    if missing:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr:
            tr.write_line("\nROUTE COVERAGE GATE FAILED — untested API routes:", red=True)
            for route, method in missing:
                tr.write_line(f"  {method} /{route}", red=True)
        session.exitstatus = 1
    else:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr:
            tr.write_line(f"\nRoute coverage gate OK: {len(HIT)} route/method pair(s) hit.")

# Covers M6 release safety (SRS §9 Security): production refuses unsafe configuration.
import re
from pathlib import Path

import pytest

from apps.core.envcheck import main, problems

TEMPLATE = Path(__file__).resolve().parents[3] / ".env.production.example"
GOOD = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod", "SECRET_KEY": "k" * 5 + "Zx9" * 15,
    "DEBUG": "false", "ALLOWED_HOSTS": "shop.example.com", "ADMIN_URL": "s3cret-panel-8f2a1c",
    "CSRF_TRUSTED_ORIGINS": "https://shop.example.com", "COOKIE_SECURE": "true",
    "POSTGRES_PASSWORD": "pg-pass-Qw8z", "DATABASE_URL": "postgres://u:pg-pass-Qw8z@db:5432/x",
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend", "SMS_PROVIDER": "mimsms",
    "MIMSMS_ENDPOINT": "https://sms.example.test", "NUXT_PUBLIC_SITE_URL": "https://shop.example.com",
}


def test_a_good_environment_passes_without_warnings():
    assert problems(GOOD) == ([], [])


@pytest.mark.parametrize("change,fragment", [
    ({"SECRET_KEY": "change-me"}, "SECRET_KEY"), ({"SECRET_KEY": "short"}, "SECRET_KEY"),
    ({"SECRET_KEY": "x" * 60 + "replace"}, "SECRET_KEY"), ({"DEBUG": "true"}, "DEBUG"),
    ({"DEBUG": "1"}, "DEBUG"), ({"ALLOWED_HOSTS": ""}, "ALLOWED_HOSTS"),
    ({"ALLOWED_HOSTS": "*"}, "ALLOWED_HOSTS"), ({"ADMIN_URL": "dev-admin"}, "ADMIN_URL"),
    ({"ADMIN_URL": "change-this-obscure-path"}, "ADMIN_URL"), ({"ADMIN_URL": "short"}, "ADMIN_URL"),
    ({"COOKIE_SECURE": "false"}, "COOKIE_SECURE"), ({"CSRF_TRUSTED_ORIGINS": ""}, "CSRF"),
    ({"CSRF_TRUSTED_ORIGINS": "http://shop.example.com"}, "CSRF"),
    ({"POSTGRES_PASSWORD": "change-me"}, "POSTGRES_PASSWORD"),
    ({"DATABASE_URL": "postgres://u:change-me@db/x"}, "DATABASE_URL"),
    ({"DJANGO_SETTINGS_MODULE": "config.settings.dev"}, "DJANGO_SETTINGS_MODULE"),
])
def test_each_unsafe_setting_is_an_error(change, fragment):
    errors, _ = problems({**GOOD, **change})
    assert any(fragment in e for e in errors), errors


def test_missing_values_are_errors_not_crashes():
    errors, _ = problems({})
    assert len(errors) >= 7


def test_warnings_do_not_block():
    errors, warnings = problems({**GOOD, "EMAIL_BACKEND": "django.core.mail.backends.console."
                                 "EmailBackend", "SMS_PROVIDER": "fake", "HSTS_SECONDS": "0",
                                 "SSL_REDIRECT": "false", "NUXT_PUBLIC_SITE_URL": ""})
    assert errors == [] and len(warnings) == 5


def test_mimsms_without_env_endpoint_is_only_a_warning():
    errors, warnings = problems({**GOOD, "MIMSMS_ENDPOINT": ""})
    assert errors == [] and any("MIMSMS_ENDPOINT" in w for w in warnings)


def test_cli_exit_codes(monkeypatch, capsys):
    for k, v in GOOD.items():
        monkeypatch.setenv(k, v)
    assert main() == 0
    monkeypatch.setenv("DEBUG", "true")
    assert main() == 1 and "DEBUG" in capsys.readouterr().err


def test_the_production_template_is_refused_until_placeholders_are_replaced():
    text = TEMPLATE.read_text()
    env = dict(re.findall(r"^([A-Z_]+)=(.*)$", text, re.M))
    errors, _ = problems(env)
    assert errors, "the template must not pass as-is"
    filled = {k: re.sub(r"REPLACE_ME\w*", f"gen{i}Zx9Qw" * 6, v) for i, (k, v) in enumerate(env.items())}
    filled["ADMIN_URL"] = "panel-8f2a1c-9d"
    filled["POSTGRES_PASSWORD"] = "Gen-pass-9x"
    filled["DATABASE_URL"] = "postgres://softladylux:Gen-pass-9x@db:5432/softladylux"
    errors, _ = problems(filled)
    assert errors == [], errors


def test_production_settings_always_allow_internal_service_hosts(monkeypatch):
    """The frontend's SSR calls use Host 'backend:8000' and healthchecks use 'localhost': without
    these names production returns 400 to every server-rendered request (found on the M6 stack run)."""
    import importlib

    monkeypatch.setenv("ALLOWED_HOSTS", "shop.example.com")
    monkeypatch.setenv("SECRET_KEY", "x" * 60)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db:5432/x")
    from config.settings import base, prod

    importlib.reload(base)  # base reads the environment when imported
    prod = importlib.reload(prod)
    assert {"shop.example.com", "backend", "localhost", "127.0.0.1"} <= set(prod.ALLOWED_HOSTS)

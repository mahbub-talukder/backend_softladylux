# Covers SRS ACC-10, ACC-14, §6 accounts_user, ARC-11.
import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import Role, User

pytestmark = pytest.mark.django_db


def test_customer_phone_normalised():
    u = User.objects.create_user(phone="+8801712345678", password="secret123", full_name="A")
    assert u.phone == "01712345678"
    assert u.role == Role.CUSTOMER
    assert not u.is_staff


def test_phone_unique_system_wide_across_roles():
    User.objects.create_user(phone="01712345678", password="secret123")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(
            email="s@example.com", phone="8801712345678", password="x", role=Role.STAFF
        )


def test_customer_requires_phone():
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(email="c@example.com", password="x")


def test_staff_requires_email():
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(phone="01712345678", password="x", role=Role.STAFF)


def test_staff_phone_optional_and_many_without_phone():
    User.objects.create_user(email="a@example.com", password="x", role=Role.STAFF)
    User.objects.create_user(email="b@example.com", password="x", role=Role.STAFF)
    assert User.objects.filter(phone__isnull=True).count() == 2


def test_create_superuser_is_owner():
    u = User.objects.create_superuser("Boss@Example.com", "secret123")
    assert u.role == Role.OWNER and u.is_owner and u.is_staff and u.email == "boss@example.com"
    assert u.check_password("secret123")


def test_timestamps_present():
    u = User.objects.create_user(phone="01712345678", password="x")
    assert u.created_at and u.updated_at

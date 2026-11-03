from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel
from apps.core.phone import normalize_phone


class Role(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    OWNER = "owner", "Owner"
    STAFF = "staff", "Staff"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, phone, password, **extra):
        email = self.normalize_email(email).lower() if email else None
        user = self.model(email=email, phone=normalize_phone(phone), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email=None, phone=None, password=None, **extra):
        extra.setdefault("role", Role.CUSTOMER)
        return self._create(email, phone, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(role=Role.OWNER, is_superuser=True, phone_verified=True)
        return self._create(email, extra.pop("phone", None), password, **extra)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """SRS §6 accounts_user; ARC-06 (customers: mobile + password, staff: email + password);
    ACC-10 (roles Owner/Staff only), ACC-14 (phone unique system-wide, normalised).
    """

    phone = models.CharField(max_length=11, unique=True, null=True, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    full_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CUSTOMER)
    is_active = models.BooleanField(default=True)
    phone_verified = models.BooleanField(default=False)
    birthday = models.DateField(null=True, blank=True)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        constraints = [
            # SRS §6: phone is required for customers; email is required for staff/owner.
            # Uniqueness of phone/email is enforced by the unique=True columns (ACC-14).
            models.CheckConstraint(
                name="customer_has_phone",
                condition=~Q(role="customer") | Q(phone__isnull=False),
            ),
            models.CheckConstraint(
                name="staff_has_email",
                condition=Q(role="customer") | Q(email__isnull=False),
            ),
        ]

    def save(self, *args, **kwargs):
        # ACC-14 / §10: normalise phone (01XXXXXXXXX) and lower-case email before every save.
        self.phone = normalize_phone(self.phone)
        self.email = self.email.lower() if self.email else None
        super().save(*args, **kwargs)

    @property
    def is_staff(self):  # Django admin access: Owner/Staff roles only (ACC-10)
        return self.role in (Role.OWNER, Role.STAFF)

    @property
    def is_owner(self):
        return self.role == Role.OWNER

    def __str__(self):
        return self.full_name or self.email or self.phone or f"user-{self.pk}"

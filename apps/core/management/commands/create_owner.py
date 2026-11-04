"""Create the Owner account. SRS ACC-10 (staff sign in with email + password), ACC-12 (password
rules), PRM-06 (there is always an Owner). Used once on a fresh install; the password comes from a
prompt or the OWNER_PASSWORD environment variable, never a command-line argument."""

import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.core import audit


class Command(BaseCommand):
    help = "Create the Owner (full access). Refuses a second Owner without --force."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", default="Owner")
        parser.add_argument("--force", action="store_true", help="Allow an additional Owner.")

    def handle(self, *args, email, name, force, **opts):
        email = email.strip().lower()
        if "@" not in email:
            raise CommandError("Enter a valid email address.")
        if User.objects.filter(email=email).exists():
            raise CommandError(f"A user with the email {email} already exists.")
        if User.objects.filter(role=Role.OWNER, is_active=True).exists() and not force:
            raise CommandError("An active Owner already exists. Use --force to add another.")
        password = os.environ.get("OWNER_PASSWORD") or self._prompt()
        try:
            validate_password(password)
        except ValidationError as e:
            raise CommandError("Password rejected: " + " ".join(e.messages)) from None
        owner = User.objects.create_superuser(email=email, password=password, full_name=name)
        audit.log(None, "create_owner", "User", owner.pk, None, {"email": email}, user=owner)
        self.stdout.write(self.style.SUCCESS(f"Owner {email} created. Sign in at /admin/login."))

    def _prompt(self) -> str:
        first = getpass.getpass("Password: ")
        if first != getpass.getpass("Password (again): "):
            raise CommandError("The passwords do not match.")
        return first

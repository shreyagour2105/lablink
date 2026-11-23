import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from firebase_admin import auth

from portal.firebase_auth import firebase_app
from portal.store import get_store

ROLES = json.loads((settings.ROOT / "shared" / "roles.json").read_text())


class Command(BaseCommand):
    help = "Assign an existing Firebase UID a LabLink role and, for faculty, permitted labs."

    def add_arguments(self, parser):
        parser.add_argument("uid")
        parser.add_argument("role", choices=ROLES)
        parser.add_argument("--lab", action="append", default=[], dest="labs")
        parser.add_argument("--eligible-lab", action="append", default=[], dest="eligible_labs")

    def handle(self, *args, **options):
        uid, role, labs = options["uid"], options["role"], options["labs"]
        eligible_labs = options["eligible_labs"]
        if not uid or "/" in uid or uid in {".", ".."} or len(uid) > 128:
            raise CommandError("Invalid Firebase UID.")
        if labs and role != "faculty":
            raise CommandError("Only faculty can be assigned labs.")
        if eligible_labs and role != "student":
            raise CommandError("Only students can be granted restricted-equipment access.")
        if any(not lab or "/" in lab or lab in {".", ".."} for lab in [*labs, *eligible_labs]):
            raise CommandError("Invalid lab identifier.")
        try:
            user = auth.get_user(uid, app=firebase_app())
            if user.disabled:
                raise CommandError("That Firebase account is disabled.")
            get_store().provision(uid, role, list(dict.fromkeys(labs)), list(dict.fromkeys(eligible_labs)))
        except auth.UserNotFoundError as error:
            raise CommandError("This UID does not belong to a Firebase account.") from error
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Saved the {role} role."))

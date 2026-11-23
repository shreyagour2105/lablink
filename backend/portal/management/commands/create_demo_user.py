from getpass import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from firebase_admin import auth

from portal.firebase_auth import firebase_app


class Command(BaseCommand):
    help = "Create a Firebase Auth Emulator account with an interactively supplied password."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, **options):
        if not settings.FIREBASE_AUTH_EMULATOR_HOST:
            raise CommandError("This command requires the Firebase Auth Emulator.")
        password = getpass("New demo account password: ")
        if len(password) < 6:
            raise CommandError("Use at least six characters.")
        try:
            user = auth.create_user(email=options["email"], password=password, app=firebase_app())
        except (ValueError, auth.EmailAlreadyExistsError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(user.uid)

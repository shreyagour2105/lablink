from contextlib import contextmanager
from functools import lru_cache

import firebase_admin
from django.conf import settings
from firebase_admin import auth, exceptions
from google.auth.exceptions import GoogleAuthError


class AuthUnavailable(Exception):
    pass


class InvalidCredential(Exception):
    pass


@lru_cache(maxsize=1)
def firebase_app():
    if not settings.FIREBASE_PROJECT_ID:
        raise AuthUnavailable("Firebase project is not configured.")
    try:
        return firebase_admin.get_app()
    except ValueError:
        try:
            return firebase_admin.initialize_app(options={"projectId": settings.FIREBASE_PROJECT_ID})
        except ValueError:
            # Another request may have initialized the default app concurrently.
            return firebase_admin.get_app()


@contextmanager
def _translate_auth_errors(*invalid_token_errors: type[Exception]):
    """Translate provider failures without changing the caller's credential checks."""
    invalid_credentials = (*invalid_token_errors, auth.UserDisabledError, auth.UserNotFoundError)
    try:
        yield
    except invalid_credentials as error:
        raise InvalidCredential from error
    except (exceptions.FirebaseError, GoogleAuthError) as error:
        raise AuthUnavailable from error


def verify_id_token(token):
    with _translate_auth_errors(auth.InvalidIdTokenError, auth.RevokedIdTokenError):
        return auth.verify_id_token(token, app=firebase_app(), check_revoked=True)


def create_cookie(token, expires_in):
    with _translate_auth_errors(auth.InvalidIdTokenError):
        return auth.create_session_cookie(token, expires_in=expires_in, app=firebase_app())


def verify_cookie(cookie):
    with _translate_auth_errors(auth.InvalidSessionCookieError, auth.RevokedSessionCookieError):
        return auth.verify_session_cookie(cookie, app=firebase_app(), check_revoked=True)

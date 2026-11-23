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


def verify_id_token(token):
    try:
        return auth.verify_id_token(token, app=firebase_app(), check_revoked=True)
    except (auth.InvalidIdTokenError, auth.RevokedIdTokenError, auth.UserDisabledError, auth.UserNotFoundError) as error:
        raise InvalidCredential from error
    except (exceptions.FirebaseError, GoogleAuthError) as error:
        raise AuthUnavailable from error


def create_cookie(token, expires_in):
    try:
        return auth.create_session_cookie(token, expires_in=expires_in, app=firebase_app())
    except (auth.InvalidIdTokenError, auth.UserDisabledError, auth.UserNotFoundError) as error:
        raise InvalidCredential from error
    except (exceptions.FirebaseError, GoogleAuthError) as error:
        raise AuthUnavailable from error


def verify_cookie(cookie):
    try:
        return auth.verify_session_cookie(cookie, app=firebase_app(), check_revoked=True)
    except (auth.InvalidSessionCookieError, auth.RevokedSessionCookieError, auth.UserDisabledError, auth.UserNotFoundError) as error:
        raise InvalidCredential from error
    except (exceptions.FirebaseError, GoogleAuthError) as error:
        raise AuthUnavailable from error

import secrets
from hmac import compare_digest

from flask import session


def generate_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = session['_csrf_token'] = secrets.token_urlsafe(32)
    return token


def validate_csrf_token(supplied_token):
    expected_token = session.get('_csrf_token', '')
    return bool(
        expected_token
        and supplied_token
        and compare_digest(expected_token, supplied_token)
    )

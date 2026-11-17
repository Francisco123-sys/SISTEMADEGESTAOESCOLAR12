import hashlib
import hmac
import os
import time

from flask import current_app, request

RATE_LIMIT_WINDOW_SECONDS = 15 * 60
LOGIN_IP_MAX_FAILURES = 20
LOGIN_CREDENTIAL_MAX_FAILURES = 5
RECOVERY_IP_MAX_FAILURES = 10
RECOVERY_CREDENTIAL_MAX_FAILURES = 3
RATE_LIMIT_LOCK_SECONDS = 15 * 60


def _bucket_hash(scope, value):
    secret = current_app.secret_key or os.getenv('SECRET_KEY', '')
    return hmac.new(
        secret.encode('utf-8'),
        f'{scope}:{value}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _request_buckets(scope, identifier):
    address = request.remote_addr or 'unknown'
    normalized_identifier = (identifier or '').strip().casefold()
    return (
        _bucket_hash(f'{scope}-ip', address),
        _bucket_hash(f'{scope}-credential', f'{address}:{normalized_identifier}'),
    )


def _is_bucket_limited(cursor, bucket_hash, now):
    cursor.execute(
        'SELECT failures, window_started_at, blocked_until '
        'FROM auth_rate_limits WHERE bucket_hash = ?',
        (bucket_hash,),
    )
    row = cursor.fetchone()
    if not row:
        return False
    return bool(row['blocked_until'] and row['blocked_until'] > now)


def is_login_rate_limited(cursor, identifier):
    now = time.time()
    ip_bucket, credential_bucket = _request_buckets('login', identifier)
    return (
        _is_bucket_limited(cursor, ip_bucket, now)
        or _is_bucket_limited(cursor, credential_bucket, now)
    )


def _record_failure(cursor, bucket_hash, now, maximum_failures, window_seconds):
    cursor.execute('''
        INSERT INTO auth_rate_limits (
            bucket_hash, failures, window_started_at, blocked_until
        )
        VALUES (?, 1, ?, 0)
        ON CONFLICT(bucket_hash) DO UPDATE SET
            failures = CASE
                WHEN ? - auth_rate_limits.window_started_at >= ? THEN 1
                ELSE auth_rate_limits.failures + 1
            END,
            window_started_at = CASE
                WHEN ? - auth_rate_limits.window_started_at >= ?
                THEN ?
                ELSE auth_rate_limits.window_started_at
            END,
            blocked_until = CASE
                WHEN ? - auth_rate_limits.window_started_at >= ? THEN 0
                WHEN auth_rate_limits.failures + 1 >= ? THEN ?
                ELSE auth_rate_limits.blocked_until
            END
    ''', (
        bucket_hash, now,
        now, window_seconds,
        now, window_seconds, now,
        now, window_seconds, maximum_failures, now + RATE_LIMIT_LOCK_SECONDS,
    ))


def record_login_failure(cursor, identifier):
    now = time.time()
    ip_bucket, credential_bucket = _request_buckets('login', identifier)
    _record_failure(
        cursor, ip_bucket, now, LOGIN_IP_MAX_FAILURES, RATE_LIMIT_WINDOW_SECONDS
    )
    _record_failure(
        cursor, credential_bucket, now, LOGIN_CREDENTIAL_MAX_FAILURES,
        RATE_LIMIT_WINDOW_SECONDS,
    )


def record_login_success(cursor, identifier):
    ip_bucket, credential_bucket = _request_buckets('login', identifier)
    cursor.execute(
        'DELETE FROM auth_rate_limits WHERE bucket_hash IN (?, ?)',
        (ip_bucket, credential_bucket),
    )


def is_recovery_rate_limited(cursor, identifier):
    now = time.time()
    ip_bucket, credential_bucket = _request_buckets('recovery', identifier)
    return (
        _is_bucket_limited(cursor, ip_bucket, now)
        or _is_bucket_limited(cursor, credential_bucket, now)
    )


def record_recovery_attempt(cursor, identifier):
    now = time.time()
    ip_bucket, credential_bucket = _request_buckets('recovery', identifier)
    _record_failure(
        cursor, ip_bucket, now, RECOVERY_IP_MAX_FAILURES, RATE_LIMIT_WINDOW_SECONDS
    )
    _record_failure(
        cursor, credential_bucket, now, RECOVERY_CREDENTIAL_MAX_FAILURES,
        RATE_LIMIT_WINDOW_SECONDS,
    )

import hashlib
import hmac
import os
import time
from collections import OrderedDict
from threading import Lock

from flask import current_app, request
from services.db import PgCursorWrapper

RATE_LIMIT_WINDOW_SECONDS = 15 * 60
LOGIN_IP_MAX_FAILURES = 20
LOGIN_CREDENTIAL_MAX_FAILURES = 5
RECOVERY_IP_MAX_FAILURES = 10
RECOVERY_CREDENTIAL_MAX_FAILURES = 3
RATE_LIMIT_LOCK_SECONDS = 15 * 60
MAX_FALLBACK_BUCKETS = 10_000

_fallback_buckets = OrderedDict()
_fallback_lock = Lock()
_missing_table_warning_logged = False


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


def _persistent_storage_available(cursor):
    global _missing_table_warning_logged
    if isinstance(cursor, PgCursorWrapper):
        cursor.execute("SELECT to_regclass('auth_rate_limits') AS table_name")
        available = cursor.fetchone()['table_name'] is not None
    else:
        cursor.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'auth_rate_limits'"
        )
        available = cursor.fetchone() is not None

    if not available and not _missing_table_warning_logged:
        with _fallback_lock:
            if not _missing_table_warning_logged:
                current_app.logger.warning(
                    'Tabela auth_rate_limits ausente; os limites de autenticação '
                    'serão mantidos apenas em memória neste processo.'
                )
                _missing_table_warning_logged = True
    return available


def _is_bucket_limited(cursor, bucket_hash, now, persistent):
    if not persistent:
        with _fallback_lock:
            bucket = _fallback_buckets.get(bucket_hash)
            if not bucket:
                return False
            _fallback_buckets.move_to_end(bucket_hash)
            return bucket['blocked_until'] > now

    cursor.execute(
        'SELECT failures, window_started_at, blocked_until '
        'FROM auth_rate_limits WHERE bucket_hash = ?',
        (bucket_hash,),
    )
    row = cursor.fetchone()
    if not row:
        return False
    return bool(row['blocked_until'] and row['blocked_until'] > now)


def _record_fallback_failure(bucket_hash, now, maximum_failures, window_seconds):
    with _fallback_lock:
        bucket = _fallback_buckets.get(bucket_hash)
        if not bucket or now - bucket['window_started_at'] >= window_seconds:
            bucket = {
                'failures': 1,
                'window_started_at': now,
                'blocked_until': 0,
            }
        else:
            bucket['failures'] += 1
            if bucket['failures'] >= maximum_failures:
                bucket['blocked_until'] = now + RATE_LIMIT_LOCK_SECONDS

        _fallback_buckets[bucket_hash] = bucket
        _fallback_buckets.move_to_end(bucket_hash)
        while len(_fallback_buckets) > MAX_FALLBACK_BUCKETS:
            _fallback_buckets.popitem(last=False)


def is_login_rate_limited(cursor, identifier):
    now = time.time()
    ip_bucket, credential_bucket = _request_buckets('login', identifier)
    persistent = _persistent_storage_available(cursor)
    return (
        _is_bucket_limited(cursor, ip_bucket, now, persistent)
        or _is_bucket_limited(cursor, credential_bucket, now, persistent)
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
    if not _persistent_storage_available(cursor):
        _record_fallback_failure(
            ip_bucket, now, LOGIN_IP_MAX_FAILURES, RATE_LIMIT_WINDOW_SECONDS
        )
        _record_fallback_failure(
            credential_bucket, now, LOGIN_CREDENTIAL_MAX_FAILURES,
            RATE_LIMIT_WINDOW_SECONDS,
        )
        return
    _record_failure(
        cursor, ip_bucket, now, LOGIN_IP_MAX_FAILURES, RATE_LIMIT_WINDOW_SECONDS
    )
    _record_failure(
        cursor, credential_bucket, now, LOGIN_CREDENTIAL_MAX_FAILURES,
        RATE_LIMIT_WINDOW_SECONDS,
    )


def record_login_success(cursor, identifier):
    ip_bucket, credential_bucket = _request_buckets('login', identifier)
    if not _persistent_storage_available(cursor):
        with _fallback_lock:
            _fallback_buckets.pop(ip_bucket, None)
            _fallback_buckets.pop(credential_bucket, None)
        return
    cursor.execute(
        'DELETE FROM auth_rate_limits WHERE bucket_hash IN (?, ?)',
        (ip_bucket, credential_bucket),
    )


def is_recovery_rate_limited(cursor, identifier):
    now = time.time()
    ip_bucket, credential_bucket = _request_buckets('recovery', identifier)
    persistent = _persistent_storage_available(cursor)
    return (
        _is_bucket_limited(cursor, ip_bucket, now, persistent)
        or _is_bucket_limited(cursor, credential_bucket, now, persistent)
    )


def record_recovery_attempt(cursor, identifier):
    now = time.time()
    ip_bucket, credential_bucket = _request_buckets('recovery', identifier)
    if not _persistent_storage_available(cursor):
        _record_fallback_failure(
            ip_bucket, now, RECOVERY_IP_MAX_FAILURES, RATE_LIMIT_WINDOW_SECONDS
        )
        _record_fallback_failure(
            credential_bucket, now, RECOVERY_CREDENTIAL_MAX_FAILURES,
            RATE_LIMIT_WINDOW_SECONDS,
        )
        return
    _record_failure(
        cursor, ip_bucket, now, RECOVERY_IP_MAX_FAILURES, RATE_LIMIT_WINDOW_SECONDS
    )
    _record_failure(
        cursor, credential_bucket, now, RECOVERY_CREDENTIAL_MAX_FAILURES,
        RATE_LIMIT_WINDOW_SECONDS,
    )

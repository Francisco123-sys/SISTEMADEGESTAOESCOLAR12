import base64
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

DRAFT_TTL_SECONDS = 2 * 60 * 60
_DRAFT_ID_PATTERN = re.compile(r'^[a-f0-9]{32}$')
_UPLOAD_NAME_PATTERN = re.compile(r'^[a-f0-9]{32}\.(?:jpg|pdf)$')


class EnrollmentDraftError(RuntimeError):
    pass


def _draft_directory():
    directory = Path(current_app.instance_path) / 'enrollment_drafts'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def _cipher():
    secret = current_app.secret_key
    if not secret:
        raise EnrollmentDraftError('A chave da aplicação não está configurada.')
    key_material = hashlib.sha256(
        b'SIGE enrollment drafts v1:' + str(secret).encode('utf-8')
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def _remove_expired_uploads(payload):
    from services.uploads import UPLOAD_DIRECTORY

    matricula = payload.get('matricula', {}) if isinstance(payload, dict) else {}
    for key in ('foto_path', 'documento_transferencia_path'):
        filename = matricula.get(key)
        if isinstance(filename, str) and _UPLOAD_NAME_PATTERN.fullmatch(filename):
            (UPLOAD_DIRECTORY / filename).unlink(missing_ok=True)


def _purge_expired_drafts(directory):
    cutoff = time.time() - DRAFT_TTL_SECONDS
    for path in directory.glob('*.draft'):
        try:
            is_expired = path.stat().st_mtime < cutoff
        except FileNotFoundError:
            continue
        if is_expired:
            load_draft(path.stem)


def new_draft_id():
    return uuid.uuid4().hex


def _draft_path(draft_id):
    if not isinstance(draft_id, str) or not _DRAFT_ID_PATTERN.fullmatch(draft_id):
        raise ValueError('Identificador de rascunho inválido.')
    return _draft_directory() / f'{draft_id}.draft'


def save_draft(draft_id, payload):
    path = _draft_path(draft_id)
    _purge_expired_drafts(path.parent)
    envelope = {
        'expires_at': int(time.time()) + DRAFT_TTL_SECONDS,
        'payload': payload,
    }
    encrypted = _cipher().encrypt(
        json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    )
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix='.draft-', delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            temporary.write(encrypted)
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def load_draft(draft_id):
    if not isinstance(draft_id, str) or not _DRAFT_ID_PATTERN.fullmatch(draft_id):
        return None
    path = _draft_directory() / f'{draft_id}.draft'
    try:
        encrypted = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        envelope = json.loads(_cipher().decrypt(encrypted))
    except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise EnrollmentDraftError(
            'O rascunho da matrícula não pôde ser descodificado. Contacte o suporte.'
        ) from error
    if envelope.get('expires_at', 0) < int(time.time()):
        _remove_expired_uploads(envelope.get('payload', {}))
        delete_draft(draft_id)
        return None
    return envelope.get('payload')


def delete_draft(draft_id):
    if not isinstance(draft_id, str) or not _DRAFT_ID_PATTERN.fullmatch(draft_id):
        return
    path = _draft_directory() / f'{draft_id}.draft'
    try:
        path.unlink()
    except FileNotFoundError:
        return

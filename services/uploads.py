import io
import os
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

UPLOAD_DIRECTORY = Path(
    os.getenv('SIGE_UPLOAD_DIRECTORY')
    or Path(__file__).resolve().parents[1] / 'instance' / 'student_uploads'
)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}


def _read_upload(upload: FileStorage) -> bytes:
    data = upload.stream.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('O ficheiro excede o limite de 5 MB.')
    if not data:
        raise ValueError('O ficheiro enviado está vazio.')
    return data


def save_student_photo(upload: FileStorage) -> str:
    extension = Path(upload.filename or '').suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError('A fotografia deve estar nos formatos JPG, JPEG ou PNG.')

    data = _read_upload(upload)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('JPEG', 'PNG'):
                raise ValueError('O conteúdo da fotografia não corresponde ao formato permitido.')
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.width > 5000 or image.height > 5000:
                raise ValueError('A fotografia excede as dimensões máximas permitidas.')
            normalized = image.convert('RGB')
            output = io.BytesIO()
            normalized.save(output, format='JPEG', optimize=True, quality=88)
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError('Não foi possível validar a fotografia enviada.') from exc

    UPLOAD_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(UPLOAD_DIRECTORY, 0o700)
    filename = f'{uuid.uuid4().hex}.jpg'
    path = UPLOAD_DIRECTORY / filename
    with path.open('xb') as destination:
        os.chmod(path, 0o600)
        destination.write(output.getvalue())
    return filename


def save_transfer_document(upload: FileStorage) -> str:
    extension = Path(upload.filename or '').suffix.lower()
    if extension != '.pdf':
        raise ValueError('O documento de transferência deve estar em formato PDF.')

    data = _read_upload(upload)
    if not data.startswith(b'%PDF-'):
        raise ValueError('O conteúdo do documento não corresponde a um ficheiro PDF.')

    UPLOAD_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(UPLOAD_DIRECTORY, 0o700)
    filename = f'{uuid.uuid4().hex}.pdf'
    path = UPLOAD_DIRECTORY / filename
    with path.open('xb') as destination:
        os.chmod(path, 0o600)
        destination.write(data)
    return filename

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from werkzeug.datastructures import FileStorage

from services.uploads import (
    MAX_UPLOAD_BYTES,
    save_student_photo,
    save_transfer_document,
)


class StudentUploadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.uploads_patch = patch(
            'services.uploads.UPLOAD_DIRECTORY',
            Path(self.tempdir.name),
        )
        self.uploads_patch.start()

    def tearDown(self):
        self.uploads_patch.stop()
        self.tempdir.cleanup()

    def test_photo_is_validated_and_normalized_to_jpeg(self):
        png = io.BytesIO()
        Image.new('RGBA', (4, 4), (255, 0, 0, 128)).save(png, format='PNG')
        upload = FileStorage(stream=io.BytesIO(png.getvalue()), filename='portrait.png')

        filename = save_student_photo(upload)

        self.assertRegex(filename, r'^[a-f0-9]{32}\.jpg$')
        with Image.open(Path(self.tempdir.name) / filename) as stored:
            self.assertEqual(stored.format, 'JPEG')
            self.assertEqual(stored.size, (4, 4))

    def test_image_extension_cannot_disguise_non_image_content(self):
        upload = FileStorage(stream=io.BytesIO(b'not an image'), filename='portrait.jpg')

        with self.assertRaises(ValueError):
            save_student_photo(upload)

    def test_transfer_document_requires_pdf_content(self):
        upload = FileStorage(
            stream=io.BytesIO(b'%PDF-1.4\nsample'),
            filename='transfer.pdf',
        )
        filename = save_transfer_document(upload)
        self.assertRegex(filename, r'^[a-f0-9]{32}\.pdf$')

        invalid = FileStorage(
            stream=io.BytesIO(b'<script>not a pdf</script>'),
            filename='transfer.pdf',
        )
        with self.assertRaises(ValueError):
            save_transfer_document(invalid)

    def test_upload_size_is_limited(self):
        upload = FileStorage(
            stream=io.BytesIO(b'x' * (MAX_UPLOAD_BYTES + 1)),
            filename='transfer.pdf',
        )
        with self.assertRaises(ValueError):
            save_transfer_document(upload)


if __name__ == '__main__':
    unittest.main()

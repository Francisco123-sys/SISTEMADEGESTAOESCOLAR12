import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app
from services.enrollment_drafts import delete_draft, load_draft


class EmptyCursor:
    def __init__(self):
        self.statement = ''

    def execute(self, *_args, **_kwargs):
        self.statement = _args[0]
        return self

    def fetchone(self):
        if 'FROM academic_years' in self.statement:
            return {'id': 1}
        return None


class EmptyDatabase:
    def cursor(self):
        return EmptyCursor()


class SecretaryEnrollmentTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.previous_instance_path = app.instance_path
        self.tempdir = tempfile.TemporaryDirectory()
        app.instance_path = self.tempdir.name
        self.client = app.test_client()
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 7
            browser_session['_csrf_token'] = 'test-csrf-token'

    def tearDown(self):
        with self.client.session_transaction() as browser_session:
            draft_id = browser_session.get('enrollment_draft_id')
        if draft_id:
            with app.app_context():
                delete_draft(draft_id)
        app.instance_path = self.previous_instance_path
        self.tempdir.cleanup()

    def post_enrollment(self, **overrides):
        form = {
            'nome': 'Aluno de Teste',
            'bi_cedula': 'TEST-123',
            'data_nascimento': '2008-01-01',
            'sexo': 'M',
            'pais_nascimento': 'Moçambique',
            'provincia_nascimento': 'Sofala',
            'distrito_nascimento': 'Beira',
            'encarregado_parentesco': 'Outro',
            'encarregado_nome': 'Encarregado Teste',
            'encarregado_contacto_principal': '841234567',
            'classe': '8',
            'turno': 'Manhã',
            'curso': 'Diurno',
            'csrf_token': 'test-csrf-token',
        }
        form.update(overrides)
        with patch('routes.secretary.get_db', return_value=EmptyDatabase()):
            return self.client.post('/secretary/students/new', data=form)

    def test_age_does_not_override_manually_selected_class(self):
        response = self.post_enrollment()

        self.assertEqual(response.status_code, 302)
        self.assertIn('/secretary/students/new/payment', response.headers['Location'])
        with self.client.session_transaction() as browser_session:
            draft_id = browser_session['enrollment_draft_id']
            self.assertNotIn('pending_matricula', browser_session)
        with app.app_context():
            draft = load_draft(draft_id)
        self.assertEqual(draft['matricula']['classe'], 8)

    def test_second_cycle_requires_a_valid_area(self):
        response = self.post_enrollment(classe='10')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/secretary/students/new'))
        with self.client.session_transaction() as browser_session:
            draft_id = browser_session['enrollment_draft_id']
        with app.app_context():
            draft = load_draft(draft_id)
        self.assertEqual(draft['form']['nome'], 'Aluno de Teste')
        self.assertEqual(draft['form']['classe'], '10')

    def test_new_enrollment_rejects_legacy_night_shift(self):
        response = self.post_enrollment(turno='Noite')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/secretary/students/new'))

    def test_birth_district_must_belong_to_selected_province_and_form_values_survive(self):
        response = self.post_enrollment(
            provincia_nascimento='Sofala',
            distrito_nascimento='Chimoio',
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            draft_id = browser_session['enrollment_draft_id']
        with app.app_context():
            draft = load_draft(draft_id)
        self.assertEqual(draft['form']['nome'], 'Aluno de Teste')
        self.assertEqual(draft['form']['distrito_nascimento'], 'Chimoio')

        page = self.client.get('/secretary/students/new')
        html = page.get_data(as_text=True)
        self.assertIn('value="Aluno de Teste"', html)
        self.assertIn('data-current="Chimoio"', html)

    def test_parent_guardian_is_derived_from_parent_fields_and_draft_is_encrypted(self):
        response = self.post_enrollment(
            encarregado_parentesco='Pai',
            nome_pai='Pai de Teste',
            pai_naturalidade='Beira',
            pai_profissao='Professor',
            pai_contacto='841112223',
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            draft_id = browser_session['enrollment_draft_id']
            self.assertNotIn('pending_matricula', browser_session)
            self.assertNotIn('Pai de Teste', str(dict(browser_session)))
        draft_path = os.path.join(
            self.tempdir.name, 'enrollment_drafts', f'{draft_id}.draft'
        )
        with open(draft_path, 'rb') as encrypted_draft:
            self.assertNotIn(b'Pai de Teste', encrypted_draft.read())
        with app.app_context():
            draft = load_draft(draft_id)
        self.assertEqual(draft['matricula']['encarregado_nome'], 'Pai de Teste')
        self.assertEqual(draft['matricula']['encarregado_naturalidade'], 'Beira')
        self.assertEqual(draft['matricula']['encarregado_profissao'], 'Professor')
        self.assertEqual(
            draft['matricula']['encarregado_contacto_principal'], '841112223'
        )

    def test_mother_guardian_uses_mothers_data_without_duplicate_form_fields(self):
        response = self.post_enrollment(
            encarregado_parentesco='Mãe',
            nome_mae='Mãe de Teste',
            mae_naturalidade='Dondo',
            mae_profissao='Comerciante',
            mae_contacto='841223344',
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            draft_id = browser_session['enrollment_draft_id']
        with app.app_context():
            draft = load_draft(draft_id)
        self.assertEqual(draft['matricula']['encarregado_nome'], 'Mãe de Teste')
        self.assertEqual(draft['matricula']['encarregado_naturalidade'], 'Dondo')
        self.assertEqual(draft['matricula']['encarregado_contacto_principal'], '841223344')

    def test_invalid_photo_keeps_other_form_fields(self):
        upload_dir = os.path.join(self.tempdir.name, 'uploads')
        with patch('services.uploads.UPLOAD_DIRECTORY', Path(upload_dir)):
            response = self.post_enrollment(
                fotografia=(io.BytesIO(b'not an image'), 'aluno.jpg', 'image/jpeg')
            )

        self.assertEqual(response.status_code, 302)
        page = self.client.get('/secretary/students/new')
        html = page.get_data(as_text=True)
        self.assertIn('value="Aluno de Teste"', html)
        self.assertIn('A fotografia', html)

    def test_payment_validation_error_preserves_student_and_payment_fields(self):
        initial = self.post_enrollment()
        self.assertIn('/secretary/students/new/payment', initial.headers['Location'])

        response = self.client.post(
            '/secretary/students/new/payment',
            data={
                'csrf_token': 'test-csrf-token',
                'banco_plataforma': 'Banco Inválido',
                'referencia_talao': '123',
                'valor_pago': '500',
                'data_deposito': '2026-01-01',
            },
        )
        self.assertEqual(response.status_code, 302)
        payment_page = self.client.get('/secretary/students/new/payment')
        html = payment_page.get_data(as_text=True)
        self.assertIn('Aluno de Teste', html)
        self.assertIn('value="123"', html)
        with self.client.session_transaction() as browser_session:
            self.assertNotIn('pending_matricula', browser_session)

    def test_foreign_student_document_and_birth_country_are_preserved(self):
        response = self.post_enrollment(
            nacionalidade_tipo='Estrangeira',
            nacionalidade_pais='Portugal',
            documento_identificacao_tipo='Passaporte',
            documento_identificacao_pais='Portugal',
            documento_identificacao_numero='P-123456',
            pais_nascimento='Outro país',
            pais_nascimento_outro='Portugal',
            provincia_nascimento='',
            distrito_nascimento='',
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            draft_id = browser_session['enrollment_draft_id']
        with app.app_context():
            draft = load_draft(draft_id)
        self.assertEqual(draft['matricula']['nacionalidade'], 'Portugal')
        self.assertEqual(draft['matricula']['pais_nascimento'], 'Portugal')
        self.assertEqual(draft['matricula']['documento_identificacao_tipo'], 'Passaporte')
        self.assertEqual(draft['matricula']['documento_identificacao_numero'], 'P-123456')
        self.assertTrue(draft['matricula']['bi_cedula'].startswith('EXT-'))


if __name__ == '__main__':
    unittest.main()

import os
import unittest
from urllib.parse import unquote, urlparse

os.environ.setdefault('SECRET_KEY', 'postgres-integration-test-only')


def _local_test_database_url():
    url = os.environ.get('SIGE_TEST_DATABASE_URL', '').strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.hostname not in {'localhost', '127.0.0.1', '::1'} or parsed.port != 55432:
        return None
    if unquote(parsed.path.lstrip('/')) != 'sige_test':
        return None
    return url


TEST_DATABASE_URL = _local_test_database_url()
if TEST_DATABASE_URL:
    os.environ['DATABASE_URL'] = TEST_DATABASE_URL

from app import app


@unittest.skipUnless(
    TEST_DATABASE_URL,
    'Defina SIGE_TEST_DATABASE_URL para um PostgreSQL local chamado sige_test.',
)
class PostgreSQLGuardianIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2

        from database.init_db import init_db

        cls._old_database_url = os.environ.get('DATABASE_URL')
        cls._old_unpooled_urls = {
            key: os.environ.get(key)
            for key in ('DATABASE_URL_UNPOOLED', 'POSTGRES_URL_NON_POOLING')
        }
        os.environ['DATABASE_URL'] = TEST_DATABASE_URL
        old_admin_email = os.environ.get('SIGE_INITIAL_ADMIN_EMAIL')
        old_admin_password = os.environ.get('SIGE_INITIAL_ADMIN_PASSWORD')
        os.environ['SIGE_INITIAL_ADMIN_EMAIL'] = ''
        os.environ['SIGE_INITIAL_ADMIN_PASSWORD'] = ''
        os.environ['DATABASE_URL_UNPOOLED'] = ''
        os.environ['POSTGRES_URL_NON_POOLING'] = ''
        try:
            init_db()
            init_db()
        finally:
            for key, value in cls._old_unpooled_urls.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if old_admin_email is None:
                os.environ.pop('SIGE_INITIAL_ADMIN_EMAIL', None)
            else:
                os.environ['SIGE_INITIAL_ADMIN_EMAIL'] = old_admin_email
            if old_admin_password is None:
                os.environ.pop('SIGE_INITIAL_ADMIN_PASSWORD', None)
            else:
                os.environ['SIGE_INITIAL_ADMIN_PASSWORD'] = old_admin_password
        cls.connection = psycopg2.connect(TEST_DATABASE_URL)
        cls.connection.autocommit = False
        cls.suffix = os.urandom(8).hex()
        cls.guardian_email = f'pg-{cls.suffix}@example.test'
        cls.secretary_code = f'SEC-PG-{cls.suffix}'
        cls.student_code = f'ALU-PG-{cls.suffix}'
        cls.unrelated_student_code = f'ALU-PG-OTHER-{cls.suffix}'
        cls.bi = f'BI-PG-{cls.suffix}'
        cls.unrelated_bi = f'BI-PG-OTHER-{cls.suffix}'
        cls.counter_prefix = f'PG{cls.suffix.upper()}'

        cursor = cls.connection.cursor()
        cursor.execute('''
            INSERT INTO users (code, nome, email, password_hash, role)
            VALUES (%s, 'Secretaria de Teste PG', %s, 'unused', 'secretary')
            RETURNING id
        ''', (cls.secretary_code, cls.guardian_email))
        cls.secretary_id = cursor.fetchone()[0]
        cursor.execute('''
            INSERT INTO turmas (
                nome, classe_id, ano_lectivo_id, turno, curso,
                capacidade_maxima, vagas_disponiveis
            )
            VALUES (%s, 8, 1, 'Manhã', 'Diurno', 30, 28)
            RETURNING id
        ''', (f'Turma de Teste PG {cls.suffix}',))
        cls.class_group_id = cursor.fetchone()[0]
        cursor.execute('''
            INSERT INTO turmas (
                nome, classe_id, ano_lectivo_id, turno, curso,
                capacidade_maxima, vagas_disponiveis
            )
            VALUES (%s, 8, 1, 'Tarde', 'Diurno', 3, 3)
            RETURNING id
        ''', (f'Turma de Destino PG {cls.suffix}',))
        cls.destination_group_id = cursor.fetchone()[0]
        for code, bi, name in (
            (cls.student_code, cls.bi, 'Aluno Associado PostgreSQL'),
            (
                cls.unrelated_student_code,
                cls.unrelated_bi,
                'Aluno Não Associado PostgreSQL',
            ),
        ):
            cursor.execute('''
                INSERT INTO students (
                    code, nome, bi_cedula, data_nascimento,
                    encarregado_contacto_1, classe_ingresso, turma_id,
                    ano_lectivo_id, turno, curso, data_matricula, funcionario_id
                )
                VALUES (%s, %s, %s, '2008-01-01', '840000000', 8, %s,
                        1, 'Manhã', 'Diurno', '2026-01-01', %s)
                RETURNING id
            ''', (code, name, bi, cls.class_group_id, cls.secretary_id))
            student_id = cursor.fetchone()[0]
            if code == cls.student_code:
                cls.student_id = student_id
            else:
                cls.unrelated_student_id = student_id
        cls.connection.commit()
        app.config.update(TESTING=True, SECRET_KEY='postgres-integration-test-only')

    @classmethod
    def tearDownClass(cls):
        cursor = cls.connection.cursor()
        cursor.execute('DELETE FROM audit_logs WHERE user_id = %s', (cls.secretary_id,))
        cursor.execute(
            'DELETE FROM code_counters WHERE counter_key = %s',
            (f'users:{cls.counter_prefix}',),
        )
        cursor.execute('DELETE FROM guardians WHERE email = %s', (cls.guardian_email,))
        cursor.execute(
            'DELETE FROM students WHERE id IN (%s, %s)',
            (cls.student_id, cls.unrelated_student_id),
        )
        cursor.execute(
            'DELETE FROM turmas WHERE id IN (%s, %s)',
            (cls.class_group_id, cls.destination_group_id),
        )
        cursor.execute('DELETE FROM users WHERE id = %s', (cls.secretary_id,))
        cls.connection.commit()
        cls.connection.close()
        if cls._old_database_url is None:
            os.environ.pop('DATABASE_URL', None)
        else:
            os.environ['DATABASE_URL'] = cls._old_database_url

    def test_code_counter_upsert_is_atomic_on_postgresql(self):
        from services.db import get_db
        from services.student_service import gerar_codigo_unico

        with app.app_context():
            first = gerar_codigo_unico(self.counter_prefix, 'users')
            second = gerar_codigo_unico(self.counter_prefix, 'users')
            get_db().commit()

        self.assertEqual(first, f'{self.counter_prefix}-000001')
        self.assertEqual(second, f'{self.counter_prefix}-000002')

    def test_guardian_creation_and_student_authorization_on_postgresql(self):
        client = app.test_client()
        with client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = self.secretary_id

        page = client.get('/secretary/encarregados')
        self.assertEqual(page.status_code, 200)
        with client.session_transaction() as browser_session:
            csrf_token = browser_session['_csrf_token']

        created = client.post('/secretary/encarregados', data={
            'csrf_token': csrf_token,
            'acao': 'associar',
            'student_id': str(self.student_id),
            'email': self.guardian_email,
            'nome': 'Encarregado PostgreSQL',
            'password': 'postgres-guardian-test-password',
            'parentesco': 'Pai',
        })
        self.assertEqual(created.status_code, 302)

        cursor = self.connection.cursor()
        cursor.execute(
            'SELECT id FROM guardians WHERE email = %s',
            (self.guardian_email,),
        )
        guardian_id = cursor.fetchone()[0]
        cursor.execute(
            'SELECT student_id FROM guardian_students WHERE guardian_id = %s',
            (guardian_id,),
        )
        self.assertEqual(cursor.fetchone()[0], self.student_id)

        portal = client.get('/portal-encarregado')
        with client.session_transaction() as browser_session:
            csrf_token = browser_session['_csrf_token']
        login = client.post('/portal-encarregado', data={
            'csrf_token': csrf_token,
            'acao': 'login',
            'email': self.guardian_email,
            'password': 'postgres-guardian-test-password',
        }, follow_redirects=True)
        self.assertEqual(login.status_code, 200)
        self.assertIn(b'Aluno Associado PostgreSQL', login.data)
        self.assertNotIn(b'Aluno N\xc3\xa3o Associado PostgreSQL', login.data)

        denied = client.get(
            f'/portal-encarregado?aluno={self.unrelated_student_id}',
            follow_redirects=True,
        )
        self.assertNotIn(b'Aluno N\xc3\xa3o Associado PostgreSQL', denied.data)

    def test_secretary_can_transfer_student_and_update_vacancies_on_postgresql(self):
        client = app.test_client()
        with client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = self.secretary_id
        page = client.get('/secretary/turmas')
        self.assertEqual(page.status_code, 200)
        with client.session_transaction() as browser_session:
            csrf_token = browser_session['_csrf_token']

        response = client.post('/secretary/turmas', data={
            'csrf_token': csrf_token,
            'acao': 'transferir_aluno',
            'student_id': str(self.student_id),
            'nova_turma_id': str(self.destination_group_id),
        })
        self.assertEqual(response.status_code, 302)

        cursor = self.connection.cursor()
        cursor.execute(
            'SELECT turma_id, classe_ingresso FROM students WHERE id = %s',
            (self.student_id,),
        )
        self.assertEqual(cursor.fetchone(), (self.destination_group_id, 8))
        cursor.execute(
            'SELECT vagas_disponiveis FROM turmas WHERE id = %s',
            (self.destination_group_id,),
        )
        self.assertEqual(cursor.fetchone()[0], 2)
        cursor.execute(
            'SELECT vagas_disponiveis FROM turmas WHERE id = %s',
            (self.class_group_id,),
        )
        self.assertEqual(cursor.fetchone()[0], 29)

    def test_class_list_pdf_renders_from_postgresql(self):
        client = app.test_client()
        with client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = self.secretary_id

        response = client.get(
            f'/secretary/turmas/{self.class_group_id}/lista-pdf/vitrine'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertTrue(response.data.startswith(b'%PDF-'))


if __name__ == '__main__':
    unittest.main()

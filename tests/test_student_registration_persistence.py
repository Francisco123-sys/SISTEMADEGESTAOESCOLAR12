import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from PIL import Image
from werkzeug.datastructures import FileStorage

from app import app


class StudentRegistrationPersistenceTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        with open('database/schema.sql', encoding='utf-8') as schema:
            self.db.executescript(schema.read())
        self.db.execute('''
            INSERT INTO users (id, code, nome, email, password_hash, role)
            VALUES (7, 'SEC-7', 'Secretaria Teste', 'secretaria@example.test', 'hash', 'secretary')
        ''')
        self.db.execute("INSERT INTO academic_years (id, ano) VALUES (1, 2026)")
        self.db.execute("INSERT INTO classes (id, numero, nivel) VALUES (10, 10, 'Secundário')")
        self.db.execute('''
            INSERT INTO turmas (id, nome, classe_id, ano_lectivo_id, turno, curso,
                                capacidade_maxima, vagas_disponiveis, area_curricular)
            VALUES (1, '10ª A01', 10, 1, 'Manhã', 'Diurno', 35, 35, 'A')
        ''')
        self.db.commit()
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 7
            browser_session['_csrf_token'] = 'test-csrf-token'
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_enrollment_persists_family_transfer_and_photo_data(self):
        png = io.BytesIO()
        Image.new('RGB', (3, 3), 'blue').save(png, format='PNG')
        form = {
            'nome': 'Aluno Transferido',
            'bi_cedula': 'BI-NEW-1',
            'data_nascimento': '2008-07-14',
            'sexo': 'M',
            'nome_pai': 'Pai Teste',
            'pai_contacto': '841111111',
            'nome_mae': 'Mãe Teste',
            'mae_profissao': 'Professora',
            'naturalidade': 'Beira',
            'provincia_nascimento': 'Sofala',
            'distrito_nascimento': 'Beira',
            'pais_nascimento': 'Moçambique',
            'pais_residencia': 'Moçambique',
            'nacionalidade': 'Moçambicana',
            'documento_identificacao_tipo': 'Bilhete de Identidade',
            'documento_identificacao_pais': 'Moçambique',
            'documento_identificacao_numero': 'BI-NEW-1',
            'encarregado_parentesco': 'Pai',
            'encarregado_nome': 'Pai Teste',
            'encarregado_contacto_principal': '841111111',
            'contacto_1': '841111111',
            'encarregado_email': 'pai@example.test',
            'classe': '10',
            'area_curricular': 'A',
            'turno': 'Manhã',
            'curso': 'Diurno',
            'ingresso_situacao': 'Transferido',
            'escola_origem': 'Escola de Origem',
            'provincia_origem': 'Manica',
            'distrito_origem': 'Chimoio',
            'classe_anterior': '9',
            'ano_lectivo_anterior': '2025',
            'isento_taxa': '1',
        }
        files = {
            'fotografia': (io.BytesIO(png.getvalue()), 'student.png', 'image/png'),
            'documento_transferencia': (io.BytesIO(b'%PDF-1.4\nsample'), 'transfer.pdf', 'application/pdf'),
        }
        with (
            patch('routes.secretary.get_db', return_value=self.db),
            patch('routes.secretary.gerar_codigo_unico', return_value='ALU-TEST-1'),
            patch('routes.secretary.atribuir_turma_automatica', return_value=1),
            patch('routes.secretary.log_audit'),
            patch('services.uploads.UPLOAD_DIRECTORY', Path(self.tempdir.name)),
        ):
            response = self.client.post(
                '/secretary/students/new',
                data={
                    **form,
                    **files,
                    'csrf_token': 'test-csrf-token',
                },
            )

        self.assertEqual(response.status_code, 302)
        student = self.db.execute(
            '''SELECT s.*, t.area_curricular FROM students s
               JOIN turmas t ON t.id = s.turma_id
               WHERE s.bi_cedula = ?''',
            ('BI-NEW-1',)
        ).fetchone()
        self.assertEqual(student['ingresso_situacao'], 'Transferido')
        self.assertEqual(student['classe_anterior'], 9)
        self.assertEqual(student['ano_lectivo_anterior'], 2025)
        self.assertEqual(student['encarregado_nome'], 'Pai Teste')
        self.assertEqual(student['area_curricular'], 'A')
        self.assertTrue(student['foto_path'].endswith('.jpg'))
        self.assertTrue(student['documento_transferencia_path'].endswith('.pdf'))
        self.assertTrue(os.path.isfile(os.path.join(self.tempdir.name, student['foto_path'])))
        self.assertTrue(os.path.isfile(os.path.join(self.tempdir.name, student['documento_transferencia_path'])))


if __name__ == '__main__':
    unittest.main()

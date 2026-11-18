import os
import unittest

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from flask import render_template_string, session

from app import app


HEADER_TEMPLATE = """
{% from 'components/dashboard_ui.html' import institutional_page_header with context %}
{{ institutional_page_header('Teste') }}
"""


class SidebarNavigationTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')

    def render_profile(self, role, path):
        with app.test_request_context(path):
            if role == 'guardian':
                session['guardian_id'] = 1
                session['guardian_nome'] = 'Encarregada Teste'
            else:
                session['user_id'] = 1
                session['user_role'] = role
                session['user_nome'] = 'Utilizador Teste'
            return render_template_string(HEADER_TEMPLATE)

    def test_sidebar_is_rendered_once_with_profile_and_csrf_logout(self):
        profiles = (
            ('admin', '/admin/dashboard', 'Administrador'),
            ('secretary', '/secretary/dashboard', 'Secretaria'),
            ('chefe_secretaria', '/secretary/dashboard', 'Chefe da Secretaria'),
            ('director', '/director/dashboard', 'Director'),
            ('pedagogico_1', '/director/dashboard', 'Direcção Pedagógica I'),
            ('pedagogico_2', '/director/dashboard', 'Direcção Pedagógica II'),
            ('teacher', '/teacher/dashboard', 'Professor'),
            ('guardian', '/portal-encarregado?aluno=10', 'Encarregado de Educação'),
        )

        for role, path, label in profiles:
            with self.subTest(role=role):
                rendered = self.render_profile(role, path)
                self.assertEqual(rendered.count('class="sige-sidebar-logout"'), 1)
                self.assertIn(f'<span>{label}</span>', rendered)
                self.assertIn('name="csrf_token"', rendered)
                self.assertIn('Terminar Sessão', rendered)

    def test_sidebar_items_reflect_the_profile_access(self):
        admin = self.render_profile('admin', '/admin/dashboard')
        secretary = self.render_profile('secretary', '/secretary/dashboard')
        chief = self.render_profile('chefe_secretaria', '/secretary/dashboard')
        director = self.render_profile('director', '/director/dashboard')
        pedagogical = self.render_profile('pedagogico_1', '/director/dashboard')
        guardian = self.render_profile('guardian', '/portal-encarregado?aluno=10')

        self.assertIn('href="/admin/dashboard"', admin)
        self.assertNotIn('href="/secretary/students"', admin)
        self.assertIn('href="/secretary/students"', secretary)
        self.assertNotIn('href="/secretary/colaboradores"', secretary)
        self.assertIn('href="/secretary/colaboradores"', chief)
        self.assertIn('href="/director/reclamacoes"', director)
        self.assertNotIn('href="/director/reclamacoes"', pedagogical)
        self.assertIn('href="/portal-encarregado?aluno=10#guardian-complaints"', guardian)
        self.assertIn('name="acao" value="logout"', guardian)


if __name__ == '__main__':
    unittest.main()

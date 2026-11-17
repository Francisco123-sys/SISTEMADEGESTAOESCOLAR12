import os
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app


class EmptyCursor:
    def execute(self, *_args, **_kwargs):
        return self

    def fetchone(self):
        return None


class EmptyDatabase:
    def cursor(self):
        return EmptyCursor()

    def commit(self):
        pass


class PostFormTokenAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.post_forms = 0
        self.missing_tokens = []
        self._forms = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'form':
            is_post = values.get('method', 'get').lower() == 'post'
            self._forms.append(is_post)
            self.post_forms += is_post
        elif tag == 'input' and values.get('name') == 'csrf_token' and self._forms:
            if self._forms[-1]:
                self._forms[-1] = False

    def handle_endtag(self, tag):
        if tag == 'form' and self._forms:
            if self._forms.pop():
                self.missing_tokens.append(self.getpos())


class CsrfProtectionTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()

    def test_login_form_contains_session_csrf_token(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as browser_session:
            token = browser_session['_csrf_token']
        self.assertIn(f'value="{token}"'.encode(), response.data)

    def test_every_post_form_contains_a_csrf_field(self):
        missing = []
        post_forms = 0
        for path in Path('templates').rglob('*.html'):
            audit = PostFormTokenAudit()
            audit.feed(path.read_text(encoding='utf-8'))
            post_forms += audit.post_forms
            if audit.missing_tokens:
                missing.append(str(path))

        self.assertGreater(post_forms, 0)
        self.assertEqual(missing, [])

    def test_state_changing_request_rejects_missing_or_invalid_token(self):
        missing = self.client.post('/login', data={'identificador': 'SEC-1'})
        invalid = self.client.post(
            '/login',
            data={'csrf_token': 'not-the-session-token'},
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(invalid.status_code, 400)

    def test_valid_csrf_header_allows_request_to_reach_route(self):
        self.client.get('/')
        with self.client.session_transaction() as browser_session:
            token = browser_session['_csrf_token']

        with patch('routes.auth.get_db', return_value=EmptyDatabase()):
            response = self.client.post(
                '/login',
                headers={'X-CSRF-Token': token},
                data={'identificador': 'SEC-1'},
            )

        self.assertEqual(response.status_code, 302)

    def test_logout_is_post_only_and_csrf_protected(self):
        get_response = self.client.get('/logout')
        post_response = self.client.post('/logout')
        director_delete = self.client.get('/director/user/delete/1')
        secretary_delete = self.client.get('/secretary/colaboradores/delete/1')

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(post_response.status_code, 400)
        self.assertEqual(director_delete.status_code, 405)
        self.assertEqual(secretary_delete.status_code, 405)


if __name__ == '__main__':
    unittest.main()

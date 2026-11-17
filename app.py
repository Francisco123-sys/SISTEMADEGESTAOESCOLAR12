import os

from flask import Flask, abort, request
from dotenv import load_dotenv

load_dotenv()

from routes.auth import auth_bp
from routes.secretary import secretary_bp
from routes.director import director_bp
from routes.teacher import teacher_bp
from routes.admin import admin_bp
from services.db import close_db
from services.csrf import generate_csrf_token, validate_csrf_token
from database.init_db import init_db

secret_key = os.getenv('SECRET_KEY', '').strip()
if not secret_key:
    raise RuntimeError('Defina SECRET_KEY no ambiente antes de iniciar a aplicação.')

app = Flask(__name__)
app.secret_key = secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
)

app.register_blueprint(auth_bp)
app.register_blueprint(secretary_bp)
app.register_blueprint(director_bp)
app.register_blueprint(teacher_bp)
app.register_blueprint(admin_bp)


@app.before_request
def protect_state_changing_requests():
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return
    supplied_token = (
        request.form.get('csrf_token')
        or request.headers.get('X-CSRF-Token', '')
    )
    if not validate_csrf_token(supplied_token):
        abort(400, description='Token CSRF inválido ou ausente.')


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': generate_csrf_token}


@app.teardown_appcontext
def teardown_db(exception):
    close_db(exception)


@app.cli.command('init-db')
def init_db_command():
    """Cria ou actualiza o esquema da base de dados explicitamente."""
    init_db()


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=os.getenv('FLASK_DEBUG') == '1'
    )

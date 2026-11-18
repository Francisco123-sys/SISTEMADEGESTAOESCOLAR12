import os
import time
from datetime import timedelta

from flask import Flask, abort, flash, redirect, request, session, url_for
from dotenv import load_dotenv

load_dotenv()

from routes.auth import auth_bp
from routes.secretary import secretary_bp
from routes.director import director_bp
from routes.teacher import teacher_bp
from routes.admin import admin_bp
from services.db import (
    close_db,
    database_target_key,
    get_database_url,
    get_db,
    is_postgres,
)
from services.csrf import generate_csrf_token, validate_csrf_token
from database.init_db import init_db
from database.complaint_migrations import migrate_complaints
from database.enrollment_migrations import migrate_enrollment_data
from services.monitoring import SESSION_TIMEOUT_SECONDS, remove_active_session

secret_key = os.getenv('SECRET_KEY', '').strip()
if not secret_key:
    raise RuntimeError('Defina SECRET_KEY no ambiente antes de iniciar a aplicação.')

if os.getenv('RENDER', '').lower() == 'true':
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError(
            'Defina DATABASE_URL no ambiente do Render antes de iniciar a aplicação.'
        )
    if not is_postgres(database_url):
        raise RuntimeError(
            'DATABASE_URL deve conter uma ligação PostgreSQL válida no Render.'
        )
    try:
        database_target_key(database_url)
    except ValueError:
        raise RuntimeError(
            'DATABASE_URL deve conter uma ligação PostgreSQL válida no Render.'
        ) from None

app = Flask(
    __name__,
    instance_path=os.getenv('SIGE_INSTANCE_PATH') or None,
)
app.secret_key = secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'),
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_TIMEOUT_SECONDS),
    MAX_CONTENT_LENGTH=11 * 1024 * 1024,
)

app.register_blueprint(auth_bp)
app.register_blueprint(secretary_bp)
app.register_blueprint(director_bp)
app.register_blueprint(teacher_bp)
app.register_blueprint(admin_bp)


@app.before_request
def enforce_session_idle_timeout():
    if not session.get('user_id') and not session.get('guardian_id'):
        return
    if request.endpoint in {
        'auth.active_users_heartbeat',
        'auth.active_users_counts',
        'auth.active_users_details',
    }:
        return

    now = time.time()
    last_seen = session.get('last_seen_at', now)
    if now - last_seen >= SESSION_TIMEOUT_SECONDS:
        remove_active_session(
            get_db().cursor(), session.get('active_account_key')
        )
        get_db().commit()
        session.clear()
        flash('A sessão expirou. Inicie sessão novamente.', 'warning')
        return redirect(url_for('auth.login_landing'))
    session['last_seen_at'] = now


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


@app.cli.command('migrate-complaints')
def migrate_complaints_command():
    """Aplica apenas as migrações aditivas do fluxo de reclamações."""
    migrate_complaints()


@app.cli.command('migrate-enrollment')
def migrate_enrollment_command():
    """Aplica as migrações aditivas dos dados de matrícula."""
    migrate_enrollment_data()


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=os.getenv('FLASK_DEBUG') == '1'
    )

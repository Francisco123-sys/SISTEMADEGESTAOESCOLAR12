import hashlib
import re
import secrets
import smtplib
import time

from flask import (
    Blueprint,
    current_app,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    send_from_directory,
)
from werkzeug.security import check_password_hash, generate_password_hash
from services.auth_security import (
    is_login_rate_limited,
    is_recovery_rate_limited,
    record_login_failure,
    record_login_success,
    record_recovery_attempt,
)
from services.csrf import generate_csrf_token, validate_csrf_token
from services.db import active_student_condition, get_db, log_audit
from services.email import build_password_reset_url, send_password_reset_email
from services.uploads import UPLOAD_DIRECTORY

auth_bp = Blueprint('auth', __name__)
PASSWORD_RESET_TTL_SECONDS = 30 * 60

@auth_bp.route('/')
def login_landing():
    return render_template('login.html')

@auth_bp.route('/portal-encarregado', methods=['GET', 'POST'])
def portal_encarregado():
    if not session.get('guardian_id'):
        if request.method == 'POST':
            if request.form.get('acao') != 'login' or not _valid_portal_csrf():
                flash('Pedido inválido. Actualize a página e tente novamente.', 'danger')
                return redirect(url_for('auth.portal_encarregado'))

            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            db = get_db()
            cursor = db.cursor()
            if is_login_rate_limited(cursor, email):
                flash('E-mail ou palavra-passe inválidos.', 'danger')
                return redirect(url_for('auth.portal_encarregado'))
            cursor.execute(
                'SELECT id, nome, email, password_hash, session_version FROM guardians WHERE LOWER(email) = ? AND ativo = 1',
                (email,),
            )
            guardian = cursor.fetchone()
            if not guardian or not password or not check_password_hash(guardian['password_hash'], password):
                record_login_failure(cursor, email)
                db.commit()
                flash('E-mail ou palavra-passe inválidos.', 'danger')
                return redirect(url_for('auth.portal_encarregado'))

            record_login_success(cursor, email)
            db.commit()
            session.clear()
            session['guardian_id'] = guardian['id']
            session['guardian_nome'] = guardian['nome']
            session['guardian_session_version'] = guardian['session_version']
            generate_csrf_token()
            return redirect(url_for('auth.portal_encarregado'))

        return _render_guardian_portal()

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, nome, email, password_hash, contacto, session_version FROM guardians WHERE id = ? AND ativo = 1',
                   (session['guardian_id'],))
    guardian = cursor.fetchone()
    if (
        not guardian
        or session.get('guardian_session_version') != guardian['session_version']
    ):
        session.clear()
        flash('A conta não está disponível. Contacte a Secretaria.', 'danger')
        return redirect(url_for('auth.portal_encarregado'))

    if request.method == 'POST':
        action = request.form.get('acao')
        if not _valid_portal_csrf():
            flash('Pedido inválido. Actualize a página e tente novamente.', 'danger')
            return redirect(url_for('auth.portal_encarregado'))
        if action == 'logout':
            session.clear()
            flash('Sessão encerrada.', 'info')
            return redirect(url_for('auth.portal_encarregado'))
        if action == 'update_contact':
            contacto = request.form.get('contacto', '').strip()
            if len(contacto) > 40:
                flash('O contacto não pode exceder 40 caracteres.', 'danger')
                return redirect(url_for('auth.portal_encarregado'))
            cursor.execute(
                'UPDATE guardians SET contacto = ? WHERE id = ?',
                (contacto or None, guardian['id']),
            )
            db.commit()
            flash('Contacto actualizado.', 'success')
            return redirect(url_for('auth.portal_encarregado'))
        if action == 'update_password':
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            if (
                len(new_password) < 12
                or not current_password
                or not check_password_hash(
                    guardian['password_hash'],
                    current_password,
                )
            ):
                flash('A palavra-passe actual é inválida ou a nova tem menos de 12 caracteres.', 'danger')
                return redirect(url_for('auth.portal_encarregado'))
            new_version = guardian['session_version'] + 1
            cursor.execute('''
                UPDATE guardians
                SET password_hash = ?, session_version = ?
                WHERE id = ?
            ''', (
                generate_password_hash(new_password),
                new_version,
                guardian['id'],
            ))
            db.commit()
            session['guardian_session_version'] = new_version
            flash('Palavra-passe alterada com sucesso.', 'success')
            return redirect(url_for('auth.portal_encarregado'))
        if action == 'reclamacao':
            student_id = request.form.get('student_id', type=int)
            mensagem = request.form.get('mensagem', '').strip()
            if not student_id or not mensagem:
                flash('Seleccione um educando e escreva a mensagem.', 'danger')
                return redirect(url_for('auth.portal_encarregado'))
            cursor.execute(
                f'''SELECT 1 FROM guardian_students gs
                    JOIN students s ON s.id = gs.student_id
                    WHERE gs.guardian_id = ? AND gs.student_id = ?
                      AND {active_student_condition(cursor, 's')}''',
                (guardian['id'], student_id),
            )
            if not cursor.fetchone():
                flash('Não tem permissão para enviar mensagens sobre esse aluno.', 'danger')
                return redirect(url_for('auth.portal_encarregado'))
            cursor.execute('''
                INSERT INTO reclamacoes_encarregados (student_id, nome_encarregado, contacto, mensagem)
                VALUES (?, ?, ?, ?)
            ''', (student_id, guardian['nome'], guardian['contacto'] or '', mensagem))
            db.commit()
            flash('A mensagem foi enviada à Secretaria.', 'success')
            return redirect(url_for('auth.portal_encarregado', aluno=student_id))
        flash('Acção desconhecida.', 'danger')
        return redirect(url_for('auth.portal_encarregado'))

    active_students = active_student_condition(cursor, 's')
    cursor.execute('''
        SELECT s.id, s.nome, s.code, s.classe_ingresso, s.curso, s.turno,
               t.nome AS turma_nome, t.sala AS turma_sala
        FROM guardian_students gs
        JOIN students s ON s.id = gs.student_id
        LEFT JOIN turmas t ON t.id = s.turma_id
        WHERE gs.guardian_id = ? AND {active_students}
        ORDER BY s.nome
    '''.format(active_students=active_students), (guardian['id'],))
    educandos = cursor.fetchall()

    aluno = None
    notas = []
    total_faltas = 0
    reclamacoes = []
    requested_student_id = request.args.get('aluno', type=int)
    if requested_student_id:
        cursor.execute('''
            SELECT s.id, s.nome, s.code, s.foto_path, s.classe_ingresso AS classe_num,
                   s.curso, s.turno, t.nome AS turma_nome, t.sala AS turma_sala
            FROM guardian_students gs
            JOIN students s ON s.id = gs.student_id
            LEFT JOIN turmas t ON t.id = s.turma_id
            WHERE gs.guardian_id = ? AND s.id = ? AND {active_students}
        '''.format(active_students=active_students), (guardian['id'], requested_student_id))
        aluno = cursor.fetchone()
        if not aluno:
            flash('O educando solicitado não está associado à sua conta.', 'danger')
            return redirect(url_for('auth.portal_encarregado'))

        cursor.execute('''
            SELECT g.*, sub.nome AS disciplina_nome
            FROM grades g
            JOIN subjects sub ON sub.id = g.subject_id
            WHERE g.student_id = ? AND g.status IN ('Submetida', 'Fechada')
            ORDER BY g.trimestre, sub.nome
        ''', (aluno['id'],))
        notas = cursor.fetchall()
        cursor.execute('''
            SELECT COUNT(*) AS total_faltas FROM attendance
            WHERE student_id = ? AND tipo = 'Falta'
        ''', (aluno['id'],))
        total_faltas = cursor.fetchone()['total_faltas']
        cursor.execute('''
            SELECT id, nome_encarregado, mensagem, status, resposta_escola, created_at
            FROM reclamacoes_encarregados
            WHERE student_id = ? ORDER BY id DESC
        ''', (aluno['id'],))
        reclamacoes = cursor.fetchall()

    return render_template(
        'portal_encarregado.html',
        guardian=guardian,
        educandos=educandos,
        aluno=aluno,
        notas=notas,
        total_faltas=total_faltas,
        reclamacoes=reclamacoes,
    )


@auth_bp.route('/portal-encarregado/foto/<filename>')
def guardian_student_photo(filename):
    guardian_id = session.get('guardian_id')
    if not guardian_id or not re.fullmatch(r'[a-f0-9]{32}\.jpg', filename):
        return '', 404
    cursor = get_db().cursor()
    active_students = active_student_condition(cursor, 's')
    cursor.execute('''
        SELECT 1 FROM guardian_students gs
        JOIN students s ON s.id = gs.student_id
        JOIN guardians g ON g.id = gs.guardian_id
        WHERE gs.guardian_id = ? AND s.foto_path = ?
          AND g.ativo = 1 AND g.session_version = ? AND {active_students}
    '''.format(active_students=active_students), (
        guardian_id,
        filename,
        session.get('guardian_session_version'),
    ))
    if not cursor.fetchone():
        return '', 404
    return send_from_directory(UPLOAD_DIRECTORY, filename, mimetype='image/jpeg')


def _render_guardian_portal():
    return render_template('portal_encarregado.html')


def _valid_portal_csrf():
    return validate_csrf_token(request.form.get('csrf_token', ''))


def _password_reset_digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


@auth_bp.route('/portal-encarregado/recuperar', methods=['GET', 'POST'])
def guardian_password_recovery():
    if request.method == 'GET':
        return render_template('guardian_password_recovery.html')

    email = request.form.get('email', '').strip().lower()
    db = get_db()
    cursor = db.cursor()
    now = time.time()
    if is_recovery_rate_limited(cursor, email):
        flash(
            'Se existir uma conta activa, será enviada uma ligação de recuperação.',
            'info',
        )
        return redirect(url_for('auth.guardian_password_recovery'))

    record_recovery_attempt(cursor, email)
    db.commit()
    cursor.execute(
        'SELECT id, email FROM guardians WHERE LOWER(email) = ? AND ativo = 1',
        (email,),
    )
    guardian = cursor.fetchone()
    if guardian:
        token = secrets.token_urlsafe(32)
        digest = _password_reset_digest(token)
        cursor.execute('''
            UPDATE guardian_password_resets
            SET used_at = ?
            WHERE guardian_id = ? AND used_at IS NULL
        ''', (now, guardian['id']))
        cursor.execute('''
            INSERT INTO guardian_password_resets (token_hash, guardian_id, expires_at)
            VALUES (?, ?, ?)
        ''', (digest, guardian['id'], now + PASSWORD_RESET_TTL_SECONDS))
        db.commit()
        try:
            reset_url = build_password_reset_url(token)
            send_password_reset_email(guardian['email'], reset_url)
        except (OSError, ValueError, smtplib.SMTPException) as exc:
            cursor.execute('''
                UPDATE guardian_password_resets
                SET used_at = ?
                WHERE token_hash = ? AND used_at IS NULL
            ''', (time.time(), digest))
            db.commit()
            current_app.logger.error(
                'Não foi possível enviar recuperação de palavra-passe: %s',
                type(exc).__name__,
            )

    flash(
        'Se existir uma conta activa, será enviada uma ligação de recuperação.',
        'info',
    )
    return redirect(url_for('auth.guardian_password_recovery'))


@auth_bp.route(
    '/portal-encarregado/redefinir/<token>',
    methods=['GET', 'POST'],
)
def guardian_password_reset(token):
    digest = _password_reset_digest(token)
    db = get_db()
    cursor = db.cursor()
    now = time.time()
    cursor.execute('''
        SELECT guardian_id FROM guardian_password_resets
        WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
    ''', (digest, now))
    reset = cursor.fetchone()
    if not reset:
        flash('A ligação de recuperação é inválida ou expirou.', 'danger')
        return redirect(url_for('auth.guardian_password_recovery'))

    if request.method == 'GET':
        return render_template('guardian_password_reset.html', token=token)

    new_password = request.form.get('new_password', '')
    confirmation = request.form.get('confirm_password', '')
    if len(new_password) < 12 or new_password != confirmation:
        flash(
            'As palavras-passe devem coincidir e ter pelo menos 12 caracteres.',
            'danger',
        )
        return render_template(
            'guardian_password_reset.html',
            token=token,
        ), 400

    cursor.execute('''
        UPDATE guardian_password_resets
        SET used_at = ?
        WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
    ''', (now, digest, now))
    if cursor.rowcount != 1:
        db.rollback()
        flash('A ligação de recuperação é inválida ou expirou.', 'danger')
        return redirect(url_for('auth.guardian_password_recovery'))

    cursor.execute('''
        UPDATE guardians
        SET password_hash = ?, session_version = session_version + 1
        WHERE id = ? AND ativo = 1
    ''', (generate_password_hash(new_password), reset['guardian_id']))
    if cursor.rowcount != 1:
        db.rollback()
        flash('A conta não está disponível. Contacte a Secretaria.', 'danger')
        return redirect(url_for('auth.guardian_password_recovery'))

    cursor.execute('''
        UPDATE guardian_password_resets
        SET used_at = ?
        WHERE guardian_id = ? AND used_at IS NULL
    ''', (now, reset['guardian_id']))
    db.commit()
    session.clear()
    flash('Palavra-passe alterada. Pode iniciar sessão com a nova credencial.', 'success')
    return redirect(url_for('auth.portal_encarregado'))


@auth_bp.route('/login', methods=['POST'])
def login():
    perfil = request.form.get('perfil')
    identificador = request.form.get('identificador', '').strip()
    password = request.form.get('password', '')
    
    db = get_db()
    cursor = db.cursor()
    if is_login_rate_limited(cursor, identificador):
        flash('Credenciais inválidas ou conta inativa.', 'danger')
        return redirect(url_for('auth.login_landing'))
    cursor.execute('SELECT * FROM users WHERE (code = ? OR email = ?) AND ativo = 1', (identificador, identificador))
    user = cursor.fetchone()
    
    if user and password and check_password_hash(user['password_hash'], password):
        record_login_success(cursor, identificador)
        db.commit()
        role_map = {
            'director': ['director', 'admin', 'pedagogico_1', 'pedagogico_2'],
            'teacher': ['teacher'],
            'secretary': ['secretary', 'chefe_secretaria']
        }
        if perfil in role_map and user['role'] not in role_map[perfil]:
            flash('Perfil de acesso incorreto para esta conta.', 'danger')
            return redirect(url_for('auth.login_landing'))
            
        session.clear()
        session['user_id'] = user['id']
        session['user_code'] = user['code']
        session['user_nome'] = user['nome']
        session['user_role'] = user['role']
        
        log_audit(user['id'], 'LOGIN', f"Utilizador {user['code']} autenticado com sucesso.")
        
        if user['role'] in ['admin', 'director', 'pedagogico_1', 'pedagogico_2']:
            return redirect(url_for('director.dashboard'))
        elif user['role'] in ['secretary', 'chefe_secretaria']:
            return redirect(url_for('secretary.dashboard'))
        elif user['role'] == 'teacher':
            return redirect(url_for('teacher.dashboard'))
    record_login_failure(cursor, identificador)
    db.commit()
    flash('Credenciais inválidas ou conta inativa.', 'danger')
    return redirect(url_for('auth.login_landing'))

@auth_bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('auth.login_landing'))
        
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual')
        nova_senha = request.form.get('nova_senha')
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT password_hash FROM users WHERE id = ?', (session['user_id'],))
        user = cursor.fetchone()
        
        if user and check_password_hash(user['password_hash'], senha_atual):
            cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(nova_senha), session['user_id']))
            db.commit()
            log_audit(session['user_id'], 'ALTERAR_SENHA', "Palavra-passe alterada com sucesso.")
            flash('Palavra-passe alterada com sucesso!', 'success')
            return redirect(url_for('auth.login_landing'))
        else:
            flash('A palavra-passe atual está incorreta.', 'danger')
            
    return render_template('change_password.html')

@auth_bp.route('/logout', methods=['POST'])
def logout():
    if 'user_id' in session:
        log_audit(session['user_id'], 'LOGOUT', f"Utilizador {session.get('user_code')} encerrou a sessão.")
    session.clear()
    flash('Sessão encerrada com sucesso.', 'info')
    return redirect(url_for('auth.login_landing'))

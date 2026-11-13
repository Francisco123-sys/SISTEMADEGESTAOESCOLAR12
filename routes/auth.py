from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash
from services.db import get_db, log_audit

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/')
def login_landing():
    return render_template('login.html')

@auth_bp.route('/portal-encarregado', methods=['GET', 'POST'])
def portal_encarregado():
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST' and request.form.get('acao') == 'reclamacao':
        student_id = request.form.get('student_id')
        codigo_aluno = request.form.get('codigo_aluno', '').strip()
        nome_encarregado = request.form.get('nome_encarregado', '').strip()
        contacto = request.form.get('contacto', '').strip()
        mensagem = request.form.get('mensagem', '').strip()

        if student_id and nome_encarregado and contacto and mensagem:
            cursor.execute('''
                INSERT INTO reclamacoes_encarregados (student_id, nome_encarregado, contacto, mensagem)
                VALUES (?, ?, ?, ?)
            ''', (student_id, nome_encarregado, contacto, mensagem))
            db.commit()
            flash('A sua reclamação ou pedido de esclarecimento foi enviado com sucesso à Secretaria!', 'success')
        else:
            flash('Por favor, preencha todos os campos obrigatórios (Nome, Contacto e Mensagem).', 'danger')
        return redirect(url_for('auth.portal_encarregado', codigo=codigo_aluno))

    codigo_ou_bi = request.form.get('codigo_aluno', '').strip() or request.args.get('codigo', '').strip()
    if codigo_ou_bi:
        cursor.execute('''
            SELECT s.*, t.nome as turma_nome, t.sala as turma_sala, c.numero as classe_num 
            FROM students s 
            LEFT JOIN turmas t ON s.turma_id = t.id 
            LEFT JOIN classes c ON t.classe_id = c.id
            WHERE s.code = ? OR s.bi_cedula = ?
        ''', (codigo_ou_bi, codigo_ou_bi))
        aluno = cursor.fetchone()
        
        if not aluno:
            flash('Aluno não encontrado com o Código ou BI informado.', 'danger')
            return redirect(url_for('auth.portal_encarregado'))
            
        cursor.execute('''
            SELECT g.*, sub.nome as disciplina_nome 
            FROM grades g JOIN subjects sub ON g.subject_id = sub.id 
            WHERE g.student_id = ?
        ''', (aluno['id'],))
        notas = cursor.fetchall()
        
        cursor.execute('SELECT COUNT(*) as total_faltas FROM attendance WHERE student_id = ? AND tipo = "Falta"', (aluno['id'],))
        total_faltas = cursor.fetchone()['total_faltas']
        
        cursor.execute('SELECT * FROM reclamacoes_encarregados WHERE student_id = ? ORDER BY id DESC', (aluno['id'],))
        reclamacoes = cursor.fetchall()
        
        return render_template('portal_encarregado.html', aluno=aluno, notas=notas, total_faltas=total_faltas, reclamacoes=reclamacoes)
        
    return render_template('portal_encarregado.html')

@auth_bp.route('/login', methods=['POST'])
def login():
    perfil = request.form.get('perfil')
    identificador = request.form.get('identificador')
    password = request.form.get('password')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM users WHERE (code = ? OR email = ?) AND ativo = 1', (identificador, identificador))
    user = cursor.fetchone()
    
    if user and check_password_hash(user['password_hash'], password):
        role_map = {
            'director': ['director', 'admin', 'pedagogico_1', 'pedagogico_2'],
            'teacher': ['teacher'],
            'secretary': ['secretary', 'chefe_secretaria']
        }
        if perfil in role_map and user['role'] not in role_map[perfil]:
            flash('Perfil de acesso incorreto para esta conta.', 'danger')
            return redirect(url_for('auth.login_landing'))
            
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

@auth_bp.route('/logout')
def logout():
    if 'user_id' in session:
        log_audit(session['user_id'], 'LOGOUT', f"Utilizador {session.get('user_code')} encerrou a sessão.")
    session.clear()
    flash('Sessão encerrada com sucesso.', 'info')
    return redirect(url_for('auth.login_landing'))

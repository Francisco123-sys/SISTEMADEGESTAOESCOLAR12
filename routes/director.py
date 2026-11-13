from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from services.db import get_db, log_audit
from services.student_service import gerar_codigo_unico
from werkzeug.security import generate_password_hash

director_bp = Blueprint('director', __name__, url_prefix='/director')

def check_director():
    return session.get('user_role') in ['director', 'admin', 'pedagogico_1', 'pedagogico_2']

@director_bp.route('/dashboard')
def dashboard():
    if not check_director():
        flash('Acesso negado.', 'danger')
        return redirect(url_for('auth.login_landing'))
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT COUNT(*) as total FROM students')
    total_alunos = cursor.fetchone()['total']
    
    cursor.execute('SELECT COUNT(*) as total FROM users WHERE role = "teacher"')
    total_professores = cursor.fetchone()['total']
    
    cursor.execute('SELECT COUNT(*) as total FROM turmas')
    total_turmas = cursor.fetchone()['total']
    
    total_receita = 0.0
    if session.get('user_role') in ['director', 'admin']:
        cursor.execute('SELECT SUM(valor_pago) as total FROM payments')
        total_receita = cursor.fetchone()['total'] or 0.0
    
    cursor.execute('SELECT * FROM users ORDER BY role ASC, nome ASC')
    funcionarios = cursor.fetchall()
    
    return render_template('director/dashboard.html', 
                           total_alunos=total_alunos, 
                           total_professores=total_professores, 
                           total_turmas=total_turmas, 
                           total_receita=total_receita,
                           funcionarios=funcionarios)

@director_bp.route('/assign-teacher', methods=['GET', 'POST'])
def assign_teacher():
    if not check_director():
        return redirect(url_for('auth.login_landing'))
        
    db = get_db()
    cursor = db.cursor()
    
    if request.method == 'POST':
        teacher_id = request.form.get('teacher_id')
        turma_id = request.form.get('turma_id')
        subject_id = request.form.get('subject_id')
        
        cursor.execute('''
            INSERT INTO teacher_assignments (teacher_id, turma_id, subject_id)
            VALUES (?, ?, ?)
        ''', (teacher_id, turma_id, subject_id))
        db.commit()
        flash('Professor atribuído à turma e disciplina com sucesso!', 'success')
        return redirect(url_for('director.assign_teacher'))

    cursor.execute('SELECT id, code, nome FROM users WHERE role = "teacher" ORDER BY nome ASC')
    professores = cursor.fetchall()
    
    cursor.execute('SELECT t.id, t.nome, c.numero as classe_num FROM turmas t JOIN classes c ON t.classe_id = c.id ORDER BY c.numero ASC, t.nome ASC')
    turmas = cursor.fetchall()
    
    cursor.execute('SELECT id, nome, codigo FROM subjects ORDER BY nome ASC')
    disciplinas = cursor.fetchall()
    
    cursor.execute('''
        SELECT ta.id, u.nome as prof_nome, u.code as prof_code, t.nome as turma_nome, s.nome as disciplina_nome
        FROM teacher_assignments ta
        JOIN users u ON ta.teacher_id = u.id
        JOIN turmas t ON ta.turma_id = t.id
        JOIN subjects s ON ta.subject_id = s.id
    ''')
    atribuicoes = cursor.fetchall()
    
    return render_template('director/assign_teacher.html', professores=professores, turmas=turmas, disciplinas=disciplinas, atribuicoes=atribuicoes)

@director_bp.route('/user/edit/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if not check_director():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    target = cursor.fetchone()
    
    if session.get('user_role') in ['pedagogico_1', 'pedagogico_2'] and target['role'] in ['chefe_secretaria', 'director', 'admin']:
        flash('PERMISSÃO NEGADA: O Pedagógico não pode editar a conta do Director ou do Chefe da Secretaria.', 'danger')
        return redirect(url_for('director.dashboard'))
        
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip()
        contacto = request.form.get('contacto', '').strip()
        role = request.form.get('role', target['role'])
        cursor.execute('UPDATE users SET nome=?, email=?, contacto=?, role=? WHERE id=?', (nome, email, contacto, role, user_id))
        db.commit()
        flash('Dados do funcionário atualizados!', 'success')
        return redirect(url_for('director.dashboard'))
    return render_template('director/staff_edit.html', staff=target)

@director_bp.route('/user/toggle/<int:user_id>')
def toggle_user(user_id):
    if not check_director():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    target = cursor.fetchone()
    
    if session.get('user_role') in ['pedagogico_1', 'pedagogico_2'] and target['role'] in ['chefe_secretaria', 'director', 'admin']:
        flash('PERMISSÃO NEGADA: O Pedagógico não pode bloquear o Director ou o Chefe da Secretaria.', 'danger')
        return redirect(url_for('director.dashboard'))
        
    cursor.execute('UPDATE users SET ativo = CASE WHEN ativo = 1 THEN 0 ELSE 1 END WHERE id = ?', (user_id,))
    db.commit()
    flash('Estado do utilizador alterado!', 'info')
    return redirect(url_for('director.dashboard'))

@director_bp.route('/user/delete/<int:user_id>')
def delete_user(user_id):
    if not check_director():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    target = cursor.fetchone()
    
    if session.get('user_role') in ['pedagogico_1', 'pedagogico_2'] and target['role'] in ['chefe_secretaria', 'director', 'admin']:
        flash('PERMISSÃO NEGADA: O Pedagógico não pode apagar a conta do Director ou do Chefe da Secretaria.', 'danger')
        return redirect(url_for('director.dashboard'))
        
    cursor.execute('DELETE FROM users WHERE id = ? AND role != "admin"', (user_id,))
    db.commit()
    flash('Utilizador removido.', 'warning')
    return redirect(url_for('director.dashboard'))

@director_bp.route('/new-staff', methods=['GET', 'POST'])
def new_staff():
    if not check_director():
        return redirect(url_for('auth.login_landing'))
        
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role')
        contacto = request.form.get('contacto')
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT id FROM students WHERE LOWER(nome) = LOWER(?)', (nome,))
        if cursor.fetchone():
            flash(f'ERRO: O nome "{nome}" pertence a um aluno registado na escola.', 'danger')
            return redirect(url_for('director.new_staff'))
            
        prefix_map = {'pedagogico_1': 'PED1', 'pedagogico_2': 'PED2', 'chefe_secretaria': 'SEC', 'teacher': 'PROF'}
        prefix = prefix_map.get(role, 'STAFF')
        code = gerar_codigo_unico(prefix, "users")
        pwd_hash = generate_password_hash("Mozambique2026!")
        
        cursor.execute('''
            INSERT INTO users (code, nome, email, password_hash, role, contacto)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (code, nome, email, pwd_hash, role, contacto))
        db.commit()
        flash(f'Utilizador {code} ({nome}) registado com sucesso!', 'success')
        return redirect(url_for('director.dashboard'))
        
    return render_template('director/staff_form.html')
@director_bp.route('/rectificar-notas', methods=['GET', 'POST'])
def rectificar_notas():
    if session.get('user_role') not in ['director', 'chefe_secretaria', 'admin']:
        flash('ACESSO NEGADO: Apenas o Director da Escola e o Chefe da Secretaria podem alterar notas submetidas.', 'danger')
        return redirect(url_for('auth.login_landing'))

    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        grade_id = request.form.get('grade_id')
        av1 = float(request.form.get('av1', 0))
        av2 = float(request.form.get('av2', 0))
        av3 = float(request.form.get('av3', 0))
        av4 = float(request.form.get('av4', 0))
        exame_raw = request.form.get('exame', '').strip()
        motivo = request.form.get('motivo', '').strip()

        media_trim = (av1 + av2 + av3 + av4) / 4.0
        if exame_raw != '':
            exame_val = float(exame_raw)
            media_final = (media_trim * 0.6) + (exame_val * 0.4)
        else:
            exame_val = None
            media_final = media_trim

        cursor.execute('''
            UPDATE grades SET av1=?, av2=?, av3=?, av4=?, media_trimestral=?, exame=?, media_final=?
            WHERE id=?
        ''', (av1, av2, av3, av4, media_trim, exame_val, media_final, grade_id))
        db.commit()

        log_audit(session['user_id'], 'RECTIFICACAO_NOTA', f"Nota ID {grade_id} alterada por {session.get('user_nome')}. Motivo: {motivo}")
        flash('Nota rectificada com sucesso e registada no trilho de auditoria!', 'success')
        return redirect(url_for('director.rectificar_notas'))

    cursor.execute('''
        SELECT g.*, s.nome as aluno_nome, s.code as aluno_code, t.nome as turma_nome, sub.nome as disciplina_nome
        FROM grades g
        JOIN students s ON g.student_id = s.id
        JOIN turmas t ON g.turma_id = t.id
        JOIN subjects sub ON g.subject_id = sub.id
        ORDER BY t.nome ASC, s.nome ASC
    ''')
    notas = cursor.fetchall()
    return render_template('director/rectificar_notas.html', notas=notas)

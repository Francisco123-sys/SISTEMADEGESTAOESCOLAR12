from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import secrets
from services.db import (
    active_student_condition,
    get_db,
    log_audit,
    student_deletion_schema_ready,
)
from services.student_service import gerar_codigo_unico
from werkzeug.security import generate_password_hash
from services.complaints import (
    COMPLAINT_STATES,
    add_message,
    build_complaint_timeline,
    notify_new_message,
    notify_state_change,
    record_state_change,
)
import time

director_bp = Blueprint('director', __name__, url_prefix='/director')

def check_director():
    return session.get('user_role') in ['director', 'admin', 'pedagogico_1', 'pedagogico_2']


@director_bp.route('/reclamacoes', methods=['GET', 'POST'])
def gerenciar_reclamacoes():
    if session.get('user_role') not in ('director', 'admin'):
        flash('Acesso negado.', 'danger')
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        if request.form.get('action') == 'notifications_read' and session.get('user_id'):
            cursor.execute('''
                UPDATE complaint_notifications SET read_at = CURRENT_TIMESTAMP
                WHERE recipient_user_id = ? AND read_at IS NULL
            ''', (session['user_id'],))
            db.commit()
            return redirect(url_for('director.gerenciar_reclamacoes'))
        complaint_id = request.form.get('rec_id', type=int)
        actor_id = session.get('user_id')
        action = request.form.get('action')
        cursor.execute('''
            SELECT id, student_id, guardian_id, estado,
                   nome_encarregado, contacto
            FROM reclamacoes_encarregados
            WHERE id = ? AND forwarded_to_director = 1
        ''', (complaint_id,))
        complaint = cursor.fetchone() if complaint_id else None
        if not complaint or not actor_id:
            flash('A reclamação não existe ou a sessão não permite esta operação.', 'danger')
            return redirect(url_for('director.gerenciar_reclamacoes'))

        if action == 'reply':
            body = request.form.get('resposta', '').strip()
            message_key = request.form.get('message_key', '').strip()
            if not body or len(body) > 5000:
                flash('Escreva uma resposta válida (até 5000 caracteres).', 'danger')
                return redirect(url_for('director.gerenciar_reclamacoes'))
            if not message_key:
                message_key = f'complaint:{complaint_id}:{secrets.token_urlsafe(24)}'
            if (
                len(message_key) > 128
                or not message_key.startswith(f'complaint:{complaint_id}:')
            ):
                flash('A chave da mensagem é inválida. Actualize a página e tente novamente.', 'danger')
                return redirect(url_for('director.gerenciar_reclamacoes'))
            message_id = add_message(
                cursor,
                complaint_id=complaint_id,
                sender_type='director',
                user_id=actor_id,
                body=body,
                message_key=message_key,
            )
            old_state = complaint['estado']
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET resposta_escola = ?, status = 'Respondida',
                    estado = 'Respondida', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (body, complaint_id))
            if old_state != 'Respondida':
                record_state_change(
                    cursor, complaint_id=complaint_id,
                    old_state=old_state, new_state='Respondida',
                    actor_type='director', user_id=actor_id,
                )
                notify_state_change(
                    cursor, complaint,
                    f'complaint:{complaint_id}:director-state:{message_id}',
                )
            notify_new_message(
                cursor, complaint, message_id, 'director',
                f'complaint:{complaint_id}:director-message:{message_id}',
            )
            flash('Resposta da Direcção enviada ao encarregado.', 'success')
        elif action == 'status':
            new_state = request.form.get('estado', '').strip()
            if new_state not in COMPLAINT_STATES:
                flash('Seleccione um estado válido.', 'danger')
                return redirect(url_for('director.gerenciar_reclamacoes'))
            old_state = complaint['estado']
            legacy_status = 'Respondida' if new_state in ('Respondida', 'Resolvida') else 'Pendente'
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET estado = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (new_state, legacy_status, complaint_id))
            if old_state != new_state:
                record_state_change(
                    cursor, complaint_id=complaint_id,
                    old_state=old_state, new_state=new_state,
                    actor_type='director', user_id=actor_id,
                )
                notify_state_change(
                    cursor, complaint,
                    f'complaint:{complaint_id}:director-state:{time.time_ns()}',
                )
            flash('Estado da reclamação actualizado.', 'success')
        else:
            flash('Acção desconhecida.', 'danger')
            return redirect(url_for('director.gerenciar_reclamacoes'))
        db.commit()
        log_audit(
            actor_id,
            'ALTERAR_RECLAMACAO',
            f'Reclamação {complaint_id} actualizada pela Direcção ({action}).',
            module='Reclamações',
            affected_record=f'Reclamação {complaint_id}',
            change_details=(
                f'Operação {action}; estado {complaint["estado"]} actualizado.'
            ),
        )
        return redirect(url_for('director.gerenciar_reclamacoes'))

    view_filter = request.args.get('filtro', 'todas').strip()
    if view_filter not in (
        'todas', 'novas', 'nao_lidas', 'em_tratamento',
        'respondidas', 'resolvidas',
    ):
        view_filter = 'todas'
    search = request.args.get('q', '').strip()
    conditions = ['r.forwarded_to_director = 1']
    params = [session.get('user_id')]
    if search:
        conditions.append('''
            (CAST(r.id AS TEXT) LIKE ? OR s.nome LIKE ? OR s.code LIKE ?
             OR r.assunto LIKE ? OR r.nome_encarregado LIKE ?)
        ''')
        params.extend([f'%{search}%'] * 5)
    if view_filter == 'novas':
        conditions.append("r.estado IN ('Nova', 'Reaberta')")
    elif view_filter == 'nao_lidas':
        conditions.append('''
            EXISTS (
                SELECT 1 FROM complaint_notifications cn
                WHERE cn.complaint_id = r.id
                  AND cn.recipient_user_id = ? AND cn.read_at IS NULL
            )
        ''')
        params.append(session.get('user_id'))
    elif view_filter == 'em_tratamento':
        conditions.append("r.estado = 'Em Tratamento'")
    elif view_filter == 'respondidas':
        conditions.append("r.estado = 'Respondida'")
    elif view_filter == 'resolvidas':
        conditions.append("r.estado = 'Resolvida'")
    cursor.execute(f'''
        SELECT r.*, s.nome AS aluno_nome, s.code AS aluno_code,
               EXISTS (
                   SELECT 1 FROM complaint_notifications cn
                   WHERE cn.complaint_id = r.id
                     AND cn.recipient_user_id = ?
                     AND cn.read_at IS NULL
               ) AS has_unread_activity
        FROM reclamacoes_encarregados r
        JOIN students s ON s.id = r.student_id
        WHERE {' AND '.join(conditions)}
        ORDER BY COALESCE(r.updated_at, r.created_at) DESC, r.id DESC
    ''', tuple(params))
    complaints = cursor.fetchall()
    cursor.execute('''
        SELECT COUNT(*) AS total, COUNT(DISTINCT complaint_id) AS complaint_total
        FROM complaint_notifications
        WHERE recipient_user_id = ? AND read_at IS NULL
    ''', (session.get('user_id'),))
    unread_counts = cursor.fetchone()
    complaint_unread_count = unread_counts['total']
    complaint_unread_case_count = unread_counts['complaint_total']
    cursor.execute('''
        SELECT id, complaint_id, kind, created_at
        FROM complaint_notifications
        WHERE recipient_user_id = ? AND read_at IS NULL
        ORDER BY created_at DESC, id DESC
        LIMIT 20
    ''', (session.get('user_id'),))
    complaint_notifications = cursor.fetchall()
    if complaints:
        ids = [row['id'] for row in complaints]
        placeholders = ', '.join('?' for _ in ids)
        cursor.execute(f'''
            SELECT m.id, m.complaint_id, m.sender_type, m.body, m.created_at,
                   COALESCE(g.nome, u.nome) AS actor_name
            FROM complaint_messages m
            LEFT JOIN guardians g ON g.id = m.sender_guardian_id
            LEFT JOIN users u ON u.id = m.sender_user_id
            WHERE m.complaint_id IN ({placeholders})
            ORDER BY m.created_at, m.id
        ''', tuple(ids))
        messages_by_complaint = {}
        for message in cursor.fetchall():
            messages_by_complaint.setdefault(message['complaint_id'], []).append(message)
        cursor.execute(f'''
            SELECT h.id, h.complaint_id, h.old_state, h.new_state,
                   h.actor_type, h.note, h.created_at,
                   COALESCE(g.nome, u.nome) AS actor_name
            FROM complaint_status_history h
            LEFT JOIN guardians g ON g.id = h.actor_guardian_id
            LEFT JOIN users u ON u.id = h.actor_user_id
            WHERE h.complaint_id IN ({placeholders})
            ORDER BY h.created_at, h.id
        ''', tuple(ids))
        history_by_complaint = {}
        for event in cursor.fetchall():
            history_by_complaint.setdefault(event['complaint_id'], []).append(event)
        prepared_complaints = []
        for row in complaints:
            complaint = dict(row)
            messages = messages_by_complaint.get(row['id'], [])
            history = history_by_complaint.get(row['id'], [])
            complaint['messages'] = messages
            complaint['history'] = history
            complaint['timeline'] = build_complaint_timeline(
                complaint, messages, history,
            )
            prepared_complaints.append(complaint)
        complaints = prepared_complaints
    return render_template(
        'director/reclamacoes.html',
        reclamacoes=complaints,
        complaint_states=COMPLAINT_STATES,
        complaint_notifications=complaint_notifications,
        complaint_unread_count=complaint_unread_count,
        complaint_unread_case_count=complaint_unread_case_count,
        view_filter=view_filter,
        search=search,
        complaint_message_keys={
            row['id']: f"complaint:{row['id']}:{secrets.token_urlsafe(24)}"
            for row in complaints
        },
    )


@director_bp.route('/dashboard')
def dashboard():
    if not check_director():
        flash('Acesso negado.', 'danger')
        return redirect(url_for('auth.login_landing'))
        
    db = get_db()
    cursor = db.cursor()
    active_students = active_student_condition(cursor)
    cursor.execute(f'SELECT COUNT(*) as total FROM students WHERE {active_students}')
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

    pedidos_eliminacao = []
    if (
        session.get('user_role') in ('director', 'admin')
        and student_deletion_schema_ready(cursor)
    ):
        cursor.execute('''
            SELECT dr.id, dr.student_id, dr.solicitado_por, dr.solicitado_em,
                   dr.motivo, s.code AS student_code, s.nome AS student_nome,
                   c.numero AS classe_num, t.nome AS turma_nome,
                   u.nome AS solicitante_nome
            FROM student_deletion_requests dr
            JOIN students s ON s.id = dr.student_id
            JOIN turmas t ON t.id = dr.turma_id
            JOIN classes c ON c.id = t.classe_id
            JOIN users u ON u.id = dr.solicitado_por
            WHERE dr.estado = 'Pendente'
            ORDER BY dr.solicitado_em, dr.id
        ''')
        pedidos_eliminacao = cursor.fetchall()
    
    return render_template('director/dashboard.html', 
                           total_alunos=total_alunos, 
                           total_professores=total_professores, 
                           total_turmas=total_turmas, 
                           total_receita=total_receita,
                           funcionarios=funcionarios,
                           pedidos_eliminacao=pedidos_eliminacao)


@director_bp.route('/turmas')
def manage_classes():
    if not check_director():
        flash('Acesso negado.', 'danger')
        return redirect(url_for('auth.login_landing'))

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id, ano, ativo FROM academic_years ORDER BY ano DESC')
    academic_years = cursor.fetchall()
    active_students = active_student_condition(cursor, 'sx')
    active_students_alias = active_student_condition(cursor, 's')
    requested_year = request.args.get('ano_lectivo_id', type=int)
    year_ids = {row['id'] for row in academic_years}
    selected_year = requested_year if requested_year in year_ids else None
    search = request.args.get('q', '').strip()
    class_filter = request.args.get('classe', '').strip()
    shift_filter = request.args.get('turno', '').strip()
    mode_filter = request.args.get('modalidade', '').strip()
    area_filter = request.args.get('area', '').strip()
    state_filter = request.args.get('estado', '').strip()
    director_filter = request.args.get('diretor_turma_id', '').strip()

    conditions = ['(? IS NULL OR t.ano_lectivo_id = ?)']
    params = [selected_year, selected_year]
    if search:
        conditions.append('''
            (t.nome LIKE ? OR CAST(c.numero AS TEXT) LIKE ? OR COALESCE(u.nome, '') LIKE ?)
        ''')
        search_pattern = f'%{search}%'
        params.extend((search_pattern, search_pattern, search_pattern))
    if class_filter.isdigit() and int(class_filter) in range(1, 13):
        conditions.append('c.numero = ?')
        params.append(int(class_filter))
    if shift_filter in ('Manhã', 'Tarde', 'Noite'):
        conditions.append('t.turno = ?')
        params.append(shift_filter)
    if mode_filter in ('Diurno', 'Nocturno', 'À Distância'):
        conditions.append('t.curso = ?')
        params.append(mode_filter)
    if area_filter in ('A', 'B', 'C'):
        conditions.append('t.area_curricular = ?')
        params.append(area_filter)
    if state_filter == 'com_vagas':
        conditions.append(f'COALESCE(t.capacidade_maxima, 0) > COALESCE((SELECT COUNT(*) FROM students sx WHERE sx.turma_id = t.id AND {active_students}), 0)')
    elif state_filter == 'completa':
        conditions.append(f'COALESCE(t.capacidade_maxima, 0) <= COALESCE((SELECT COUNT(*) FROM students sx WHERE sx.turma_id = t.id AND {active_students}), 0)')
    if director_filter.isdigit():
        conditions.append('t.diretor_turma_id = ?')
        params.append(int(director_filter))

    cursor.execute(f'''
        SELECT t.id, t.nome, t.sala, t.turno, t.curso, t.capacidade_maxima,
               t.area_curricular, t.diretor_turma_id,
               c.numero AS classe_num, ay.ano AS ano_lectivo,
               u.nome AS diretor_turma,
               (SELECT COUNT(*) FROM students s WHERE s.turma_id = t.id AND {active_students_alias}) AS inscritos
        FROM turmas t
        JOIN classes c ON c.id = t.classe_id
        JOIN academic_years ay ON ay.id = t.ano_lectivo_id
        LEFT JOIN users u ON u.id = t.diretor_turma_id
        WHERE {' AND '.join(conditions)}
        ORDER BY ay.ano DESC, c.numero, t.nome
    ''', params)
    turmas = cursor.fetchall()

    cursor.execute("SELECT id, nome FROM users WHERE role = 'teacher' AND ativo = 1 ORDER BY nome")
    professores = cursor.fetchall()

    total_capacidade = sum(t['capacidade_maxima'] or 0 for t in turmas)
    total_manha = sum(1 for t in turmas if t['turno'] == 'Manhã' and t['curso'] != 'À Distância')
    total_tarde = sum(1 for t in turmas if t['turno'] == 'Tarde' and t['curso'] != 'À Distância')
    total_distancia = sum(1 for t in turmas if t['curso'] == 'À Distância')

    return render_template(
        'director/turmas.html',
        turmas=turmas,
        professores=professores,
        academic_years=academic_years,
        selected_year=selected_year,
        filters={
            'q': search,
            'classe': class_filter,
            'turno': shift_filter,
            'modalidade': mode_filter,
            'area': area_filter,
            'estado': state_filter,
            'diretor_turma_id': director_filter,
        },
        areas_curriculares={
            'A': 'Letras e Ciências Sociais',
            'B': 'Ciências Naturais e Exactas',
            'C': 'Artes Visuais e Cénicas / Ciências com Desenho',
        },
        total_turmas=len(turmas),
        total_capacidade=total_capacidade,
        total_manha=total_manha,
        total_tarde=total_tarde,
        total_distancia=total_distancia,
    )


@director_bp.route('/turmas/<int:turma_id>/configurar', methods=['POST'])
def configure_class(turma_id):
    if session.get('user_role') not in ('director', 'admin'):
        flash('Apenas a Direcção pode alterar a capacidade ou o Director de Turma.', 'danger')
        return redirect(url_for('director.manage_classes'))

    try:
        capacidade = int(request.form.get('capacidade_maxima', ''))
    except ValueError:
        flash('Informe uma capacidade válida.', 'danger')
        return redirect(url_for('director.manage_classes'))
    if capacidade < 1:
        flash('A capacidade deve ser maior que zero.', 'danger')
        return redirect(url_for('director.manage_classes'))

    diretor_turma_raw = request.form.get('diretor_turma_id', '').strip()
    if diretor_turma_raw:
        try:
            diretor_turma_id = int(diretor_turma_raw)
        except ValueError:
            flash('Seleccione um professor válido como Director de Turma.', 'danger')
            return redirect(url_for('director.manage_classes'))
    else:
        diretor_turma_id = None

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'UPDATE turmas SET capacidade_maxima = capacidade_maxima WHERE id = ?',
        (turma_id,),
    )
    if cursor.rowcount != 1:
        flash('Turma não encontrada.', 'danger')
        return redirect(url_for('director.manage_classes'))

    active_students = active_student_condition(cursor)
    cursor.execute(
        f'SELECT COUNT(*) AS total FROM students WHERE turma_id = ? AND {active_students}',
        (turma_id,),
    )
    inscritos = cursor.fetchone()['total']
    if capacidade < inscritos:
        db.rollback()
        flash(
            f'A capacidade não pode ser inferior aos {inscritos} alunos já inscritos.',
            'danger',
        )
        return redirect(url_for('director.manage_classes'))

    if diretor_turma_id is not None:
        cursor.execute(
            "SELECT id FROM users WHERE id = ? AND role = 'teacher' AND ativo = 1",
            (diretor_turma_id,),
        )
        if not cursor.fetchone():
            db.rollback()
            flash('O Director de Turma seleccionado não é um professor activo.', 'danger')
            return redirect(url_for('director.manage_classes'))

    vagas = capacidade - inscritos
    cursor.execute('''
        UPDATE turmas
        SET capacidade_maxima = ?, vagas_disponiveis = ?, diretor_turma_id = ?
        WHERE id = ?
    ''', (capacidade, vagas, diretor_turma_id, turma_id))
    db.commit()
    log_audit(
        session['user_id'],
        'CONFIGURAR_TURMA',
        f'Turma ID {turma_id}: capacidade {capacidade}; Director de Turma ID {diretor_turma_id}.',
        module='Turmas',
        affected_record=f'Turma ID {turma_id}',
        change_details=(
            f'Capacidade definida como {capacidade}; Director de Turma ID '
            f'{diretor_turma_id or "não atribuído"}.'
        ),
    )
    flash('Capacidade e Director de Turma actualizados.', 'success')
    return redirect(url_for('director.manage_classes'))


@director_bp.route('/student-deletion/<int:request_id>/decision', methods=['POST'])
def decide_student_deletion(request_id):
    if session.get('user_role') not in ('director', 'admin'):
        flash('Apenas o Director pode decidir pedidos de eliminação.', 'danger')
        return redirect(url_for('auth.login_landing'))

    db = get_db()
    cursor = db.cursor()
    if not student_deletion_schema_ready(cursor):
        flash('A aprovação de pedidos requer a migração de dados pendente.', 'warning')
        return redirect(url_for('director.dashboard'))

    decision = request.form.get('decision', '')
    if decision not in ('approve', 'reject'):
        flash('Decisão inválida.', 'danger')
        return redirect(url_for('director.dashboard'))

    decision_note = request.form.get('decision_note', '').strip()
    if len(decision_note) > 1000:
        flash('A observação da decisão não pode exceder 1000 caracteres.', 'danger')
        return redirect(url_for('director.dashboard'))

    cursor.execute('''
        SELECT id, student_id, solicitado_por, estado
        FROM student_deletion_requests
        WHERE id = ?
    ''', (request_id,))
    pedido = cursor.fetchone()
    if not pedido or pedido['estado'] != 'Pendente':
        flash('O pedido não existe ou já foi decidido.', 'warning')
        return redirect(url_for('director.dashboard'))

    if pedido['solicitado_por'] == session.get('user_id'):
        flash('Não pode decidir um pedido de eliminação que solicitou.', 'danger')
        return redirect(url_for('director.dashboard'))

    turma_id = None
    if decision == 'approve':
        cursor.execute(
            'UPDATE students SET ativo = ativo WHERE id = ? AND ativo = 1',
            (pedido['student_id'],),
        )
        if cursor.rowcount != 1:
            db.rollback()
            flash('O aluno já não está activo; o pedido não foi aplicado.', 'warning')
            return redirect(url_for('director.dashboard'))

        cursor.execute(
            'SELECT turma_id FROM students WHERE id = ? AND ativo = 1',
            (pedido['student_id'],),
        )
        aluno = cursor.fetchone()
        turma_id = aluno['turma_id']
        cursor.execute(
            'UPDATE turmas SET vagas_disponiveis = vagas_disponiveis WHERE id = ?',
            (turma_id,),
        )
        if cursor.rowcount != 1:
            db.rollback()
            flash('A turma actual do aluno não foi encontrada.', 'danger')
            return redirect(url_for('director.dashboard'))

    estado = 'Aprovado' if decision == 'approve' else 'Rejeitado'
    cursor.execute('''
        UPDATE student_deletion_requests
        SET estado = ?, decidido_por = ?, decidido_em = CURRENT_TIMESTAMP,
            motivo_decisao = ?
        WHERE id = ? AND estado = 'Pendente'
    ''', (estado, session['user_id'], decision_note or None, request_id))
    if cursor.rowcount != 1:
        db.rollback()
        flash('O pedido foi actualizado por outro utilizador; actualize a página.', 'warning')
        return redirect(url_for('director.dashboard'))

    if decision == 'approve':
        cursor.execute('''
            UPDATE students
            SET ativo = 0, eliminado_em = CURRENT_TIMESTAMP, eliminado_por = ?
            WHERE id = ? AND ativo = 1
        ''', (session['user_id'], pedido['student_id']))
        if cursor.rowcount != 1:
            db.rollback()
            flash('Não foi possível aplicar a eliminação lógica do aluno.', 'danger')
            return redirect(url_for('director.dashboard'))

        cursor.execute(
            'SELECT COUNT(*) AS total FROM students WHERE turma_id = ? AND ativo = 1',
            (turma_id,),
        )
        inscritos = cursor.fetchone()['total']
        cursor.execute(
            'SELECT capacidade_maxima FROM turmas WHERE id = ?',
            (turma_id,),
        )
        capacidade = cursor.fetchone()['capacidade_maxima'] or 0
        cursor.execute(
            'UPDATE turmas SET vagas_disponiveis = ? WHERE id = ?',
            (max(capacidade - inscritos, 0), turma_id),
        )

    log_audit(
        session['user_id'],
        'DECIDIR_ELIMINACAO_ALUNO',
        f"Pedido {request_id}; aluno {pedido['student_id']}; solicitante "
        f"{pedido['solicitado_por']}; Director {session['user_id']}; "
        f"decisão {estado}; estado da matrícula "
        f"{'activo → eliminado logicamente' if decision == 'approve' else 'activo → activo'}; "
        f"motivo da decisão: {decision_note or 'Não indicado'}.",
        module='Alunos e matrículas',
        affected_record=f'Aluno ID {pedido["student_id"]}; pedido {request_id}',
        change_details=(
            f'Pedido de eliminação decidido: {estado}. '
            f'Matrícula {"desactivada" if decision == "approve" else "mantida activa"}.'
        ),
    )
    flash(
        'Pedido aprovado; a matrícula foi desactivada e o histórico preservado.'
        if decision == 'approve'
        else 'Pedido rejeitado; a matrícula permanece activa.',
        'success' if decision == 'approve' else 'info',
    )
    return redirect(url_for('director.dashboard'))


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
        log_audit(
            session['user_id'],
            'ATRIBUIR_TURMA_PROFESSOR',
            f'Professor ID {teacher_id} atribuído à turma {turma_id}, disciplina {subject_id}.',
            module='Turmas',
            affected_record=f'Turma {turma_id}; disciplina {subject_id}',
            change_details=f'Professor ID {teacher_id} associado à turma e disciplina.',
        )
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
        log_audit(
            session['user_id'],
            'EDITAR_UTILIZADOR',
            f'Utilizador ID {user_id} actualizado.',
            module='Utilizadores e permissões',
            affected_record=f'Utilizador ID {user_id}',
            change_details=f'Nome, contacto e/ou perfil actualizados para {role}.',
        )
        flash('Dados do funcionário atualizados!', 'success')
        return redirect(url_for('director.dashboard'))
    return render_template('director/staff_edit.html', staff=target)

@director_bp.route('/user/toggle/<int:user_id>', methods=['POST'])
def toggle_user(user_id):
    if not check_director():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT role, ativo FROM users WHERE id = ?', (user_id,))
    target = cursor.fetchone()
    if not target:
        flash('Utilizador não encontrado.', 'danger')
        return redirect(url_for('director.dashboard'))
    
    if session.get('user_role') in ['pedagogico_1', 'pedagogico_2'] and target['role'] in ['chefe_secretaria', 'director', 'admin']:
        flash('PERMISSÃO NEGADA: O Pedagógico não pode bloquear o Director ou o Chefe da Secretaria.', 'danger')
        return redirect(url_for('director.dashboard'))
        
    cursor.execute('UPDATE users SET ativo = CASE WHEN ativo = 1 THEN 0 ELSE 1 END WHERE id = ?', (user_id,))
    db.commit()
    log_audit(
        session['user_id'],
        'ALTERAR_ESTADO_UTILIZADOR',
        f'Estado do utilizador ID {user_id} alterado.',
        module='Utilizadores e permissões',
        affected_record=f'Utilizador ID {user_id}',
        change_details=(
            'Utilizador bloqueado.' if target['ativo']
            else 'Utilizador activado.'
        ),
    )
    flash('Estado do utilizador alterado!', 'info')
    return redirect(url_for('director.dashboard'))

@director_bp.route('/user/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not check_director():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    target = cursor.fetchone()
    if not target:
        flash('Utilizador não encontrado.', 'danger')
        return redirect(url_for('director.dashboard'))
    
    if session.get('user_role') in ['pedagogico_1', 'pedagogico_2'] and target['role'] in ['chefe_secretaria', 'director', 'admin']:
        flash('PERMISSÃO NEGADA: O Pedagógico não pode apagar a conta do Director ou do Chefe da Secretaria.', 'danger')
        return redirect(url_for('director.dashboard'))
        
    cursor.execute('DELETE FROM users WHERE id = ? AND role != "admin"', (user_id,))
    db.commit()
    log_audit(
        session['user_id'],
        'REMOVER_UTILIZADOR',
        f'Conta do utilizador ID {user_id} removida.',
        module='Utilizadores e permissões',
        affected_record=f'Utilizador ID {user_id}',
        change_details='Conta removida.',
    )
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
        password = request.form.get('password', '')
        if len(password) < 12:
            flash('A palavra-passe inicial deve ter pelo menos 12 caracteres.', 'danger')
            return redirect(url_for('director.new_staff'))
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT id FROM students WHERE LOWER(nome) = LOWER(?)', (nome,))
        if cursor.fetchone():
            flash(f'ERRO: O nome "{nome}" pertence a um aluno registado na escola.', 'danger')
            return redirect(url_for('director.new_staff'))
            
        prefix_map = {'pedagogico_1': 'PED1', 'pedagogico_2': 'PED2', 'chefe_secretaria': 'SEC', 'teacher': 'PROF'}
        prefix = prefix_map.get(role, 'STAFF')
        code = gerar_codigo_unico(prefix, "users")
        pwd_hash = generate_password_hash(password)
        
        cursor.execute('''
            INSERT INTO users (code, nome, email, password_hash, role, contacto)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (code, nome, email, pwd_hash, role, contacto))
        db.commit()
        log_audit(
            session['user_id'],
            'CADASTRO_FUNCIONARIO',
            f'Utilizador {code} ({nome}) registado com perfil {role}.',
            module='Utilizadores e permissões',
            affected_record=code,
            change_details=f'Conta criada com perfil {role}.',
        )
        flash(
            f'Utilizador {code} ({nome}) registado. '
            'Entregue a palavra-passe inicial por um canal seguro.',
            'success',
        )
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

        log_audit(
            session['user_id'],
            'RECTIFICACAO_NOTA',
            f"Nota ID {grade_id} alterada por {session.get('user_nome')}. Motivo: {motivo}",
            module='Notas e pautas',
            affected_record=f'Nota ID {grade_id}',
            change_details=f'Notas rectificadas. Motivo: {motivo or "Não indicado"}.',
        )
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

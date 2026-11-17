from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from services.db import active_student_condition, get_db, log_audit
from datetime import datetime
from decimal import Decimal, InvalidOperation

teacher_bp = Blueprint('teacher', __name__, url_prefix='/teacher')

LIMITE_FALTAS_INJUSTIFICADAS = 10  # Limite regulamentar por disciplina/trimestre

def check_teacher():
    return session.get('user_role') in ['teacher', 'admin']

@teacher_bp.route('/dashboard')
def dashboard():
    if not check_teacher():
        flash('Acesso negado.', 'danger')
        return redirect(url_for('auth.login_landing'))
        
    db = get_db()
    cursor = db.cursor()
    active_students = active_student_condition(cursor, 'st')
    cursor.execute('''
        SELECT ta.id, t.nome as turma_nome, s.nome as disciplina_nome, t.id as turma_id, s.id as subject_id, c.numero as classe_num,
               (SELECT COUNT(*) FROM students st WHERE st.turma_id = t.id AND {active_students}) as total_alunos,
               (SELECT COUNT(*) FROM grades g WHERE g.turma_id = t.id AND g.subject_id = s.id AND g.status IN ('Submetida', 'Fechada')) as submetida_count,
               (SELECT AVG(g.media_final) FROM grades g WHERE g.turma_id = t.id AND g.subject_id = s.id) as media_turma,
               (SELECT COUNT(*) FROM grades g WHERE g.turma_id = t.id AND g.subject_id = s.id AND g.media_final >= 9.5) as aprovados_count,
               (SELECT COUNT(*) FROM grades g WHERE g.turma_id = t.id AND g.subject_id = s.id AND g.media_final < 9.5) as negativas_count,
               (SELECT COUNT(*) FROM grades g WHERE g.turma_id = t.id AND g.subject_id = s.id AND g.faltas_nao_justificadas >= ?) as risco_faltas_count,
               (SELECT COUNT(*) FROM lesson_summaries ls WHERE ls.turma_id = t.id AND ls.subject_id = s.id) as total_aulas_dadas
        FROM teacher_assignments ta
        JOIN turmas t ON ta.turma_id = t.id
        JOIN subjects s ON ta.subject_id = s.id
        JOIN classes c ON t.classe_id = c.id
        WHERE ta.teacher_id = ?
    '''.format(active_students=active_students), (LIMITE_FALTAS_INJUSTIFICADAS, session['user_id']))
    atribuicoes = cursor.fetchall()
    return render_template('teacher/dashboard.html', atribuicoes=atribuicoes)

@teacher_bp.route('/pauta-oficial/<int:turma_id>/<int:subject_id>')
def pauta_oficial_impressao(turma_id, subject_id):
    if not check_teacher():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    if session.get('user_role') != 'admin':
        cursor.execute('''
            SELECT 1 FROM teacher_assignments
            WHERE teacher_id = ? AND turma_id = ? AND subject_id = ?
        ''', (session['user_id'], turma_id, subject_id))
        if not cursor.fetchone():
            flash('Acesso negado à pauta desta turma ou disciplina.', 'danger')
            return redirect(url_for('teacher.dashboard'))
    active_students = active_student_condition(cursor, 's')
    cursor.execute('''
        SELECT t.nome as turma_nome, t.sala as sala, t.curso as curso, t.turno as turno,
               c.numero as classe_num, s.nome as disciplina_nome 
        FROM turmas t JOIN classes c ON t.classe_id = c.id JOIN subjects s ON s.id = ? WHERE t.id = ?
    ''', (subject_id, turma_id))
    info = cursor.fetchone()
    
    cursor.execute('''
        SELECT s.code, s.nome, g.av1, g.av2, g.av3, g.av4, g.media_trimestral, g.exame, g.media_final,
               g.faltas_justificadas, g.faltas_nao_justificadas, g.total_faltas, g.comentario_professor
        FROM students s
        LEFT JOIN grades g ON s.id = g.student_id AND g.subject_id = ? AND g.turma_id = ?
        WHERE s.turma_id = ? AND {active_students}
        ORDER BY s.nome ASC
    '''.format(active_students=active_students), (subject_id, turma_id, turma_id))
    alunos_notas = cursor.fetchall()
    is_exame_class = info['classe_num'] in [9, 12]
    return render_template('teacher/pauta_oficial.html', info=info, alunos=alunos_notas,
                           is_exame_class=is_exame_class, prof_nome=session.get('user_nome'),
                           limite_fnj=LIMITE_FALTAS_INJUSTIFICADAS)

@teacher_bp.route('/grades/<int:turma_id>/<int:subject_id>', methods=['GET', 'POST'])
def launch_grades(turma_id, subject_id):
    if not check_teacher():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id FROM teacher_assignments WHERE teacher_id = ? AND turma_id = ? AND subject_id = ?', 
                   (session['user_id'], turma_id, subject_id))
    if not cursor.fetchone() and session.get('user_role') != 'admin':
        flash('Acesso Negado.', 'danger')
        return redirect(url_for('teacher.dashboard'))
    active_students = active_student_condition(cursor, 's')
    active_students_unaliased = active_student_condition(cursor)
        
    cursor.execute('''
        SELECT t.nome as turma_nome, t.sala as sala, t.curso as curso, t.turno as turno,
               c.numero as classe_num, s.nome as disciplina_nome 
        FROM turmas t JOIN classes c ON t.classe_id = c.id JOIN subjects s ON s.id = ? WHERE t.id = ?
    ''', (subject_id, turma_id))
    info = cursor.fetchone()
    is_exame_class = info['classe_num'] in [9, 12]

    cursor.execute('''
        SELECT COUNT(*) as total FROM grades 
        WHERE turma_id = ? AND subject_id = ? AND status IN ('Submetida', 'Fechada')
    ''', (turma_id, subject_id))
    ja_submetida = cursor.fetchone()['total'] > 0
    
    if request.method == 'POST':
        if ja_submetida:
            flash('BLOQUEADO: As notas desta pauta já foram submetidas e não podem ser alteradas pelo professor. Apenas o Chefe da Secretaria ou o Director da Escola podem rectificar notas.', 'danger')
            return redirect(url_for('teacher.launch_grades', turma_id=turma_id, subject_id=subject_id))

        aluno_ids = request.form.getlist('student_id')
        cursor.execute(
            f'SELECT id FROM students WHERE turma_id = ? AND {active_students_unaliased} ORDER BY id',
            (turma_id,),
        )
        expected_student_ids = {str(row['id']) for row in cursor.fetchall()}
        if (
            not aluno_ids
            or len(aluno_ids) != len(set(aluno_ids))
            or set(aluno_ids) != expected_student_ids
        ):
            flash('A lista de alunos submetida não corresponde à turma atribuída.', 'danger')
            return redirect(url_for('teacher.launch_grades', turma_id=turma_id, subject_id=subject_id))

        validated_grades = []
        try:
            for sid in aluno_ids:
                scores = []
                for field in ('av1', 'av2', 'av3', 'av4'):
                    score = Decimal(request.form.get(f'{field}_{sid}', '0') or '0')
                    if not score.is_finite() or score < 0 or score > 20:
                        raise ValueError('As notas devem estar entre 0 e 20.')
                    scores.append(float(score))

                raw_exam = request.form.get(f'exame_{sid}', '').strip()
                if raw_exam and not is_exame_class:
                    raise ValueError('Apenas classes com exame podem submeter essa nota.')
                exam = None
                if raw_exam:
                    exam_value = Decimal(raw_exam)
                    if not exam_value.is_finite() or exam_value < 0 or exam_value > 20:
                        raise ValueError('A nota de exame deve estar entre 0 e 20.')
                    exam = float(exam_value)

                fj = int(request.form.get(f'fj_{sid}', 0) or 0)
                fnj = int(request.form.get(f'fnj_{sid}', 0) or 0)
                if fj < 0 or fnj < 0:
                    raise ValueError('O número de faltas não pode ser negativo.')
                comentario = request.form.get(f'comentario_{sid}', '').strip()
                validated_grades.append((sid, scores, exam, fj, fnj, comentario))
        except (InvalidOperation, TypeError, ValueError):
            flash('Notas, exames ou faltas inválidos. Verifique os valores e tente novamente.', 'danger')
            return redirect(url_for('teacher.launch_grades', turma_id=turma_id, subject_id=subject_id))

        for sid, scores, exame_val, fj, fnj, comentario in validated_grades:
            av1, av2, av3, av4 = scores
            total_faltas = fj + fnj
            media_trim = (av1 + av2 + av3 + av4) / 4.0
            if exame_val is not None:
                media_final = (media_trim * 0.6) + (exame_val * 0.4)
            else:
                media_final = media_trim

            cursor.execute('''
                INSERT INTO grades (
                    student_id, subject_id, turma_id, trimestre,
                    av1, av2, av3, av4, media_trimestral, exame, media_final,
                    faltas_justificadas, faltas_nao_justificadas, total_faltas,
                    comentario_professor, status
                )
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Submetida')
            ''', (sid, subject_id, turma_id, av1, av2, av3, av4, media_trim, exame_val, media_final,
                   fj, fnj, total_faltas, comentario))
        db.commit()
        log_audit(session['user_id'], 'SUBMISSAO_NOTAS_FALTAS',
                  f"Professor {session.get('user_nome')} submeteu pauta (notas, faltas e pareceres) Turma {turma_id}, Disciplina {subject_id}.")
        flash('Pauta (notas, faltas e pareceres pedagógicos) submetida e bloqueada com sucesso!', 'success')
        return redirect(url_for('teacher.dashboard'))

    cursor.execute('''
        SELECT s.id, s.code, s.nome, g.av1, g.av2, g.av3, g.av4, g.exame, g.media_final,
               g.faltas_justificadas, g.faltas_nao_justificadas, g.total_faltas, g.comentario_professor, g.status
        FROM students s
        LEFT JOIN grades g ON s.id = g.student_id AND g.subject_id = ? AND g.turma_id = ?
        WHERE s.turma_id = ? AND {active_students} ORDER BY s.nome ASC
    '''.format(active_students=active_students), (subject_id, turma_id, turma_id))
    alunos = cursor.fetchall()
    return render_template('teacher/grades.html', info=info, alunos=alunos, is_exame_class=is_exame_class,
                           turma_id=turma_id, subject_id=subject_id, ja_submetida=ja_submetida,
                           limite_fnj=LIMITE_FALTAS_INJUSTIFICADAS)

# LIVRO DE PONTO DIGITAL / REGISTO DE SUMÁRIOS POR TURMA E DISCIPLINA
@teacher_bp.route('/sumarios/<int:turma_id>/<int:subject_id>', methods=['GET', 'POST'])
def sumarios(turma_id, subject_id):
    if not check_teacher():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT id FROM teacher_assignments WHERE teacher_id = ? AND turma_id = ? AND subject_id = ?',
                   (session['user_id'], turma_id, subject_id))
    if not cursor.fetchone() and session.get('user_role') != 'admin':
        flash('Acesso Negado.', 'danger')
        return redirect(url_for('teacher.dashboard'))

    cursor.execute('''
        SELECT t.nome as turma_nome, c.numero as classe_num, s.nome as disciplina_nome
        FROM turmas t JOIN classes c ON t.classe_id = c.id JOIN subjects s ON s.id = ? WHERE t.id = ?
    ''', (subject_id, turma_id))
    info = cursor.fetchone()

    if request.method == 'POST':
        numero_aula = int(request.form.get('numero_aula', 1))
        data_aula = request.form.get('data_aula', '').strip()
        sumario_txt = request.form.get('sumario', '').strip()
        observacoes = request.form.get('observacoes', '').strip()

        if not data_aula or not sumario_txt:
            flash('Preencha a data e o sumário da aula.', 'danger')
            return redirect(url_for('teacher.sumarios', turma_id=turma_id, subject_id=subject_id))

        cursor.execute('''
            INSERT INTO lesson_summaries (teacher_id, turma_id, subject_id, numero_aula, data_aula, sumario, observacoes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (session['user_id'], turma_id, subject_id, numero_aula, data_aula, sumario_txt, observacoes))
        db.commit()
        log_audit(session['user_id'], 'REGISTO_SUMARIO', f"Aula nº {numero_aula} registada na Turma {turma_id}, Disciplina {subject_id}.")
        flash(f'Sumário da Aula N.º {numero_aula} registado com sucesso!', 'success')
        return redirect(url_for('teacher.sumarios', turma_id=turma_id, subject_id=subject_id))

    cursor.execute('''
        SELECT * FROM lesson_summaries
        WHERE turma_id = ? AND subject_id = ?
        ORDER BY numero_aula DESC, data_aula DESC
    ''', (turma_id, subject_id))
    lista_sumarios = cursor.fetchall()
    proxima_aula = (lista_sumarios[0]['numero_aula'] + 1) if lista_sumarios else 1

    return render_template('teacher/sumarios.html', info=info, sumarios=lista_sumarios,
                           proxima_aula=proxima_aula, hoje=datetime.now().strftime('%Y-%m-%d'),
                           turma_id=turma_id, subject_id=subject_id)

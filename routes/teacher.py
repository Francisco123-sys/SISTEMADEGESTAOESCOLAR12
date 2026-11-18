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
    teacher_id = session['user_id']
    cursor.execute('''
        SELECT DISTINCT ay.id, ay.ano, ay.ativo
        FROM teacher_assignments ta
        JOIN turmas t ON t.id = ta.turma_id
        JOIN academic_years ay ON ay.id = t.ano_lectivo_id
        WHERE ta.teacher_id = ?
        ORDER BY ay.ano DESC
    ''', (teacher_id,))
    academic_years = cursor.fetchall()
    year_ids = {row['id'] for row in academic_years}

    cursor.execute('''
        SELECT DISTINCT c.numero
        FROM teacher_assignments ta
        JOIN turmas t ON t.id = ta.turma_id
        JOIN classes c ON c.id = t.classe_id
        WHERE ta.teacher_id = ?
        ORDER BY c.numero
    ''', (teacher_id,))
    classes = cursor.fetchall()
    class_numbers = {row['numero'] for row in classes}

    cursor.execute('''
        SELECT DISTINCT t.id, t.nome, c.numero AS classe_num
        FROM teacher_assignments ta
        JOIN turmas t ON t.id = ta.turma_id
        JOIN classes c ON c.id = t.classe_id
        WHERE ta.teacher_id = ?
        ORDER BY c.numero, t.nome
    ''', (teacher_id,))
    turmas = cursor.fetchall()
    turma_ids = {row['id'] for row in turmas}

    cursor.execute('''
        SELECT DISTINCT sub.id, sub.nome
        FROM teacher_assignments ta
        JOIN subjects sub ON sub.id = ta.subject_id
        WHERE ta.teacher_id = ?
        ORDER BY sub.nome
    ''', (teacher_id,))
    disciplinas = cursor.fetchall()
    subject_ids = {row['id'] for row in disciplinas}

    raw_year = request.args.get('ano_lectivo_id', '').strip()
    selected_year_id = request.args.get('ano_lectivo_id', type=int)
    all_years = raw_year == 'todos'
    if raw_year and selected_year_id is None and not all_years:
        flash('Seleccione um ano lectivo válido.', 'warning')
        raw_year = ''
    if selected_year_id is not None and selected_year_id not in year_ids:
        flash('O ano lectivo não pertence às atribuições deste professor.', 'warning')
        selected_year_id = None
        raw_year = ''
        all_years = False
    if not raw_year and not all_years:
        active_year = next((row for row in academic_years if row['ativo'] == 1), None)
        selected_year_id = active_year['id'] if active_year else None

    selected_class = request.args.get('classe', type=int)
    if selected_class not in class_numbers:
        if request.args.get('classe'):
            flash('A classe não pertence às atribuições deste professor.', 'warning')
        selected_class = None
    selected_turma_id = request.args.get('turma_id', type=int)
    if selected_turma_id not in turma_ids:
        if request.args.get('turma_id'):
            flash('A turma não pertence às atribuições deste professor.', 'warning')
        selected_turma_id = None
    selected_subject_id = request.args.get('disciplina_id', type=int)
    if selected_subject_id not in subject_ids:
        if request.args.get('disciplina_id'):
            flash('A disciplina não pertence às atribuições deste professor.', 'warning')
        selected_subject_id = None

    trimestre = request.args.get('trimestre', 1, type=int)
    if trimestre not in (1, 2, 3):
        flash('Seleccione um trimestre válido.', 'warning')
        trimestre = 1
    estado_filtro = request.args.get('estado', 'todos')
    if estado_filtro not in ('todos', 'pendente', 'em_curso', 'submetida'):
        estado_filtro = 'todos'
    busca = request.args.get('q', '').strip()[:100]

    active_students = active_student_condition(cursor, 'st')
    active_grade_students = active_student_condition(cursor, 'sg')
    where = ['ta.teacher_id = ?']
    params = [teacher_id]
    if selected_year_id is not None:
        where.append('t.ano_lectivo_id = ?')
        params.append(selected_year_id)
    if selected_class is not None:
        where.append('c.numero = ?')
        params.append(selected_class)
    if selected_turma_id is not None:
        where.append('t.id = ?')
        params.append(selected_turma_id)
    if selected_subject_id is not None:
        where.append('sub.id = ?')
        params.append(selected_subject_id)
    if busca:
        where.append(
            "(t.nome LIKE ? OR sub.nome LIKE ? OR CAST(c.numero AS TEXT) LIKE ?)"
        )
        search_pattern = f'%{busca}%'
        params.extend((search_pattern, search_pattern, search_pattern))

    assignment_query = f'''
        SELECT ta.id, t.nome AS turma_nome, sub.nome AS disciplina_nome,
               t.id AS turma_id, sub.id AS subject_id, c.numero AS classe_num,
               t.ano_lectivo_id, ay.ano AS ano_lectivo, t.turno, t.curso,
               (SELECT COUNT(*) FROM students st
                WHERE st.turma_id = t.id AND {active_students}) AS total_alunos,
               (SELECT COUNT(DISTINCT g.student_id)
                FROM grades g
                JOIN students sg ON sg.id = g.student_id
                WHERE g.turma_id = t.id AND g.subject_id = sub.id
                  AND g.trimestre = ? AND g.status IN ('Submetida', 'Fechada')
                  AND {active_grade_students}) AS notas_registadas,
               (SELECT AVG(g.media_final)
                FROM grades g
                JOIN students sg ON sg.id = g.student_id
                WHERE g.turma_id = t.id AND g.subject_id = sub.id
                  AND g.trimestre = ? AND g.status IN ('Submetida', 'Fechada')
                  AND {active_grade_students}) AS media_turma,
               (SELECT COUNT(DISTINCT g.student_id)
                FROM grades g
                JOIN students sg ON sg.id = g.student_id
                WHERE g.turma_id = t.id AND g.subject_id = sub.id
                  AND g.trimestre = ? AND g.status IN ('Submetida', 'Fechada')
                  AND g.faltas_nao_justificadas >= ?
                  AND {active_grade_students}) AS risco_faltas_count,
               (SELECT COUNT(*) FROM lesson_summaries ls
                WHERE ls.turma_id = t.id AND ls.subject_id = sub.id) AS total_aulas_dadas
        FROM teacher_assignments ta
        JOIN turmas t ON t.id = ta.turma_id
        JOIN subjects sub ON sub.id = ta.subject_id
        JOIN classes c ON c.id = t.classe_id
        JOIN academic_years ay ON ay.id = t.ano_lectivo_id
        WHERE {' AND '.join(where)}
        ORDER BY c.numero, t.nome, sub.nome
    '''
    cursor.execute(
        assignment_query,
        [trimestre, trimestre, trimestre, LIMITE_FALTAS_INJUSTIFICADAS] + params,
    )
    atribuicoes = []
    for row in cursor.fetchall():
        assignment = dict(row)
        assignment['notas_pendentes'] = max(
            assignment['total_alunos'] - assignment['notas_registadas'], 0
        )
        assignment['progresso_notas'] = (
            round(assignment['notas_registadas'] * 100 / assignment['total_alunos'])
            if assignment['total_alunos'] else 0
        )
        if not assignment['total_alunos']:
            assignment['estado_pauta'] = 'Sem alunos activos'
        elif assignment['notas_registadas'] == assignment['total_alunos']:
            assignment['estado_pauta'] = 'Submetida'
        elif assignment['notas_registadas']:
            assignment['estado_pauta'] = 'Em curso'
        else:
            assignment['estado_pauta'] = 'Pendente'
        expected_status = {
            'pendente': 'Pendente',
            'em_curso': 'Em curso',
            'submetida': 'Submetida',
        }.get(estado_filtro)
        if expected_status and assignment['estado_pauta'] != expected_status:
            continue
        assignment['grades_url'] = url_for(
            'teacher.launch_grades',
            turma_id=assignment['turma_id'],
            subject_id=assignment['subject_id'],
            trimestre=trimestre,
        )
        assignment['sumarios_url'] = url_for(
            'teacher.sumarios',
            turma_id=assignment['turma_id'],
            subject_id=assignment['subject_id'],
        )
        assignment['pauta_url'] = url_for(
            'teacher.pauta_oficial_impressao',
            turma_id=assignment['turma_id'],
            subject_id=assignment['subject_id'],
        )
        atribuicoes.append(assignment)

    turma_ids_visiveis = {a['turma_id'] for a in atribuicoes}
    total_alunos = 0
    if turma_ids_visiveis:
        placeholders = ', '.join('?' for _ in turma_ids_visiveis)
        cursor.execute(
            f'SELECT COUNT(DISTINCT s.id) AS total FROM students s '
            f'WHERE s.turma_id IN ({placeholders}) AND {active_student_condition(cursor, "s")}',
            list(turma_ids_visiveis),
        )
        total_alunos = cursor.fetchone()['total']

    filter_values = {
        'ano_lectivo_id': 'todos' if all_years else selected_year_id,
        'classe': selected_class,
        'turma_id': selected_turma_id,
        'disciplina_id': selected_subject_id,
        'trimestre': trimestre,
        'estado': estado_filtro,
        'q': busca,
    }
    return render_template(
        'teacher/dashboard.html',
        atribuicoes=atribuicoes,
        academic_years=academic_years,
        classes=classes,
        turmas=turmas,
        disciplinas=disciplinas,
        selected_year_id=selected_year_id,
        all_years=all_years,
        selected_class=selected_class,
        selected_turma_id=selected_turma_id,
        selected_subject_id=selected_subject_id,
        trimestre=trimestre,
        estado_filtro=estado_filtro,
        busca=busca,
        filter_values=filter_values,
        total_turmas=len(turma_ids_visiveis),
        total_alunos=total_alunos,
        total_disciplinas=len({a['subject_id'] for a in atribuicoes}),
        total_notas=sum(a['notas_registadas'] for a in atribuicoes),
        total_notas_pendentes=sum(a['notas_pendentes'] for a in atribuicoes),
        total_aulas=sum(a['total_aulas_dadas'] for a in atribuicoes),
    )

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
        SELECT t.nome AS turma_nome, t.sala, t.curso, t.turno, t.area_curricular,
               c.numero AS classe_num, s.nome AS disciplina_nome,
               ay.ano AS ano_lectivo, u.nome AS diretor_turma
        FROM turmas t
        JOIN classes c ON c.id = t.classe_id
        JOIN subjects s ON s.id = ?
        LEFT JOIN academic_years ay ON ay.id = t.ano_lectivo_id
        LEFT JOIN users u ON u.id = t.diretor_turma_id
        WHERE t.id = ?
    ''', (subject_id, turma_id))
    info = cursor.fetchone()

    if not info:
        flash('A turma ou disciplina não foi encontrada.', 'warning')
        return redirect(url_for('teacher.dashboard'))

    cursor.execute('''
        SELECT s.code, s.nome,
               MAX(CASE WHEN g.trimestre = 1 THEN g.media_final END) AS nota_trimestre_1,
               MAX(CASE WHEN g.trimestre = 2 THEN g.media_final END) AS nota_trimestre_2,
               MAX(CASE WHEN g.trimestre = 3 THEN g.media_trimestral END) AS nota_trimestre_3,
               MAX(CASE WHEN g.trimestre = 3 THEN g.exame END) AS exame_final,
               MAX(CASE WHEN g.trimestre = 3 THEN g.media_final END) AS nota_final,
               SUM(COALESCE(g.faltas_justificadas, 0)) AS faltas_justificadas,
               SUM(COALESCE(g.faltas_nao_justificadas, 0)) AS faltas_nao_justificadas,
               SUM(COALESCE(g.total_faltas, 0)) AS total_faltas,
               COUNT(DISTINCT g.trimestre) AS trimestres_registados,
               CASE WHEN COUNT(DISTINCT g.trimestre) = 3
                    THEN AVG(g.media_final) END AS media_anual
        FROM students s
        LEFT JOIN grades g ON s.id = g.student_id AND g.subject_id = ? AND g.turma_id = ?
            AND g.status IN ('Submetida', 'Fechada')
        WHERE s.turma_id = ? AND {active_students}
        GROUP BY s.id, s.code, s.nome
        ORDER BY s.nome ASC
    '''.format(active_students=active_students), (subject_id, turma_id, turma_id))
    alunos_notas = cursor.fetchall()
    is_exame_class = info['classe_num'] in [9, 12]
    return render_template('teacher/pauta_oficial.html', info=info, alunos=alunos_notas,
                           is_exame_class=is_exame_class, prof_nome=session.get('user_nome'),
                           limite_fnj=LIMITE_FALTAS_INJUSTIFICADAS,
                           data_emissao=datetime.now().strftime('%d/%m/%Y'),
                           turma_id=turma_id,
                           subject_id=subject_id)

@teacher_bp.route('/grades/<int:turma_id>/<int:subject_id>', methods=['GET', 'POST'])
def launch_grades(turma_id, subject_id):
    if not check_teacher():
        return redirect(url_for('auth.login_landing'))
    trimestre = request.values.get('trimestre', 1, type=int)
    if trimestre not in (1, 2, 3):
        flash('Seleccione um trimestre válido.', 'warning')
        return redirect(url_for('teacher.dashboard'))
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
        WHERE turma_id = ? AND subject_id = ? AND trimestre = ?
          AND status IN ('Submetida', 'Fechada')
    ''', (turma_id, subject_id, trimestre))
    ja_submetida = cursor.fetchone()['total'] > 0
    
    if request.method == 'POST':
        if ja_submetida:
            flash('BLOQUEADO: As notas desta pauta já foram submetidas e não podem ser alteradas pelo professor. Apenas o Chefe da Secretaria ou o Director da Escola podem rectificar notas.', 'danger')
            return redirect(url_for(
                'teacher.launch_grades', turma_id=turma_id,
                subject_id=subject_id, trimestre=trimestre,
            ))

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
            return redirect(url_for(
                'teacher.launch_grades', turma_id=turma_id,
                subject_id=subject_id, trimestre=trimestre,
            ))

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
            return redirect(url_for(
                'teacher.launch_grades', turma_id=turma_id,
                subject_id=subject_id, trimestre=trimestre,
            ))

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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Submetida')
            ''', (sid, subject_id, turma_id, trimestre, av1, av2, av3, av4, media_trim, exame_val, media_final,
                   fj, fnj, total_faltas, comentario))
        db.commit()
        log_audit(session['user_id'], 'SUBMISSAO_NOTAS_FALTAS',
                  f"Professor {session.get('user_nome')} submeteu pauta (notas, faltas e pareceres) Turma {turma_id}, Disciplina {subject_id}.",
                  module='Notas e pautas',
                  affected_record=f'Turma {turma_id}; disciplina {subject_id}',
                  change_details='Pauta, notas, faltas e pareceres submetidos.')
        flash('Pauta (notas, faltas e pareceres pedagógicos) submetida e bloqueada com sucesso!', 'success')
        return redirect(url_for('teacher.dashboard', trimestre=trimestre))

    cursor.execute('''
        SELECT s.id, s.code, s.nome, g.av1, g.av2, g.av3, g.av4, g.exame, g.media_final,
               g.faltas_justificadas, g.faltas_nao_justificadas, g.total_faltas, g.comentario_professor, g.status
        FROM students s
        LEFT JOIN grades g ON s.id = g.student_id AND g.subject_id = ? AND g.turma_id = ?
            AND g.trimestre = ?
        WHERE s.turma_id = ? AND {active_students} ORDER BY s.nome ASC
    '''.format(active_students=active_students), (subject_id, turma_id, trimestre, turma_id))
    alunos = cursor.fetchall()
    return render_template('teacher/grades.html', info=info, alunos=alunos, is_exame_class=is_exame_class,
                           turma_id=turma_id, subject_id=subject_id, ja_submetida=ja_submetida,
                           limite_fnj=LIMITE_FALTAS_INJUSTIFICADAS, trimestre=trimestre)

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

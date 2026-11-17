import csv
from html import escape
import math
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash, session, send_file, send_from_directory
from services.db import (
    active_student_condition,
    get_db,
    log_audit,
    DB_INTEGRITY_ERRORS,
    student_deletion_schema_ready,
)
from services.student_service import (
    AREAS_CURRICULARES,
    calcular_classe_sugerida,
    gerar_codigo_unico,
    atribuir_turma_automatica,
)
from datetime import datetime
from werkzeug.security import generate_password_hash
from services.uploads import UPLOAD_DIRECTORY, save_student_photo, save_transfer_document
from services.complaints import (
    COMPLAINT_CATEGORIES,
    COMPLAINT_STATES,
    add_message,
    notify_directors,
    notify_guardian,
    notify_new_message,
    notify_state_change,
    record_state_change,
)
import io
import os
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image as ReportImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

secretary_bp = Blueprint('secretary', __name__, url_prefix='/secretary')

BANCOS_PERMITIDOS = ['Banco BCI', 'Millennium BIM', 'Standard Bank', 'M-Pesa', 'e-Mola']
MESES_PAGAMENTO = {
    'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
}

def check_secretary():
    return session.get('user_role') in ['secretary', 'chefe_secretaria', 'admin']


def validar_referencia_9_digitos(ref):
    return bool(re.fullmatch(r'[0-9]{9}', ref))


def _valid_payment_amount(value):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    converted = float(amount)
    return converted if math.isfinite(converted) else None


def _valid_deposit_date(value):
    try:
        deposit_date = datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return False
    return deposit_date <= datetime.now().date()


def _valid_payment_period(value):
    return value in MESES_PAGAMENTO or bool(
        re.fullmatch(r'Matrícula / Inscrição \d{4}', value)
    )


@secretary_bp.route('/dashboard')
def dashboard():
    if not check_secretary():
        flash('Acesso não autorizado.', 'danger')
        return redirect(url_for('auth.login_landing'))
    
    db = get_db()
    cursor = db.cursor()
    active_students = active_student_condition(cursor)
    cursor.execute(f'SELECT COUNT(*) as total FROM students WHERE {active_students}')
    total_alunos = cursor.fetchone()['total']

    cursor.execute('SELECT COUNT(*) as total FROM turmas')
    total_turmas = cursor.fetchone()['total']
    
    cursor.execute(f'''
        SELECT COUNT(*) AS total FROM students
        WHERE {active_students} AND COALESCE(isento_taxa, 0) = 1
    ''')
    total_isentos = cursor.fetchone()['total']
    
    cursor.execute(f'''
        SELECT COUNT(*) AS total FROM students
        WHERE {active_students}
          AND COALESCE(NULLIF(TRIM(nee_tipo), ''), 'Nenhuma') <> 'Nenhuma'
    ''')
    total_nee = cursor.fetchone()['total']

    cursor.execute('SELECT COUNT(*) AS total FROM payments')
    total_pagamentos = cursor.fetchone()['total']
    
    total_receita = 0.0
    if session.get('user_role') in ['chefe_secretaria', 'director', 'admin']:
        cursor.execute('SELECT SUM(valor_pago) as total FROM payments')
        total_receita = cursor.fetchone()['total'] or 0.0
    
    cursor.execute('''
        SELECT p.*, s.nome as aluno_nome, s.code as aluno_code 
        FROM payments p JOIN students s ON p.student_id = s.id 
        ORDER BY p.id DESC LIMIT 8
    ''')
    ultimos_pagamentos = cursor.fetchall()
    cursor.execute('''
        SELECT COUNT(*) AS total FROM reclamacoes_encarregados
        WHERE estado IN ('Nova', 'Reaberta')
    ''')
    reclamacoes_novas = cursor.fetchone()['total']
    
    return render_template(
        'secretary/dashboard.html',
        total_alunos=total_alunos,
        total_turmas=total_turmas,
        total_isentos=total_isentos,
        total_nee=total_nee,
        total_pagamentos=total_pagamentos,
        total_receita=total_receita,
        reclamacoes_novas=reclamacoes_novas,
        pagamentos=ultimos_pagamentos,
    )


@secretary_bp.route('/reclamacoes/updates')
def complaint_updates():
    if not check_secretary():
        return jsonify(error='Acesso não autorizado.'), 403
    user_id = session.get('user_id')
    if not user_id:
        return jsonify(error='Sessão inválida.'), 401
    cursor = get_db().cursor()
    cursor.execute('''
        SELECT COUNT(*) AS total FROM reclamacoes_encarregados
        WHERE estado IN ('Nova', 'Reaberta')
    ''')
    new_count = cursor.fetchone()['total']
    cursor.execute('''
        SELECT COUNT(*) AS total FROM complaint_notifications
        WHERE recipient_user_id = ? AND read_at IS NULL
    ''', (user_id,))
    unread_count = cursor.fetchone()['total']
    cursor.execute('''
        SELECT COUNT(*) AS total FROM complaint_notifications
        WHERE recipient_user_id = ? AND read_at IS NULL
    ''', (session.get('user_id'),))
    complaint_unread_count = cursor.fetchone()['total']
    cursor.execute('''
        SELECT id, complaint_id, kind, created_at
        FROM complaint_notifications
        WHERE recipient_user_id = ? AND read_at IS NULL
        ORDER BY id DESC
        LIMIT 20
    ''', (user_id,))
    notifications = [
        {
            'id': row['id'],
            'complaint_id': row['complaint_id'],
            'kind': row['kind'],
            'created_at': str(row['created_at']),
        }
        for row in cursor.fetchall()
    ]
    response = jsonify(
        new_count=new_count,
        unread_count=unread_count,
        latest_notification_id=notifications[0]['id'] if notifications else 0,
        notifications=notifications,
    )
    response.headers['Cache-Control'] = 'no-store'
    return response


@secretary_bp.route('/students', methods=['GET'])
def students_list():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))

    busca = request.args.get('q', '').strip()
    raw_class_id = request.args.get('classe_id', '').strip()
    legacy_class = request.args.get('classe', '').strip()
    raw_turma_id = request.args.get('turma_id', '').strip()
    raw_year_id = request.args.get('ano_lectivo_id', '').strip()
    all_years = raw_year_id == 'todos'
    selected_class_id = request.args.get('classe_id', type=int)
    selected_turma_id = request.args.get('turma_id', type=int)
    selected_year_id = request.args.get('ano_lectivo_id', type=int)
    turno_filtro = request.args.get('turno', '').strip()
    modalidade_filtro = request.args.get('modalidade', '').strip()
    area_filtro = request.args.get('area', '').strip()
    filtro_especial = request.args.get('especial', '')
    situacao = request.args.get('situacao', 'activos')
    per_page = request.args.get('per_page', 10, type=int)
    page = request.args.get('page', 1, type=int)

    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT id, ano, ativo FROM academic_years ORDER BY ano DESC')
    academic_years = cursor.fetchall()
    valid_year_ids = {row['id'] for row in academic_years}
    explicit_year = bool(raw_year_id)
    if raw_year_id and selected_year_id is None and not all_years:
        flash('Seleccione um ano lectivo válido.', 'warning')
        selected_year_id = None
    elif selected_year_id is not None and selected_year_id not in valid_year_ids:
        flash('O ano lectivo seleccionado não existe.', 'warning')
        selected_year_id = None
    if selected_year_id is None and not all_years:
        active_year = next((row for row in academic_years if row['ativo'] == 1), None)
        selected_year_id = active_year['id'] if active_year else None

    if raw_class_id and selected_class_id is None:
        flash('Seleccione uma classe válida.', 'warning')
        selected_class_id = None
    elif selected_class_id is None and legacy_class:
        try:
            legacy_class_number = int(legacy_class)
        except ValueError:
            legacy_class_number = None
        if legacy_class_number is not None:
            cursor.execute(
                'SELECT id FROM classes WHERE numero = ? AND numero BETWEEN 8 AND 12',
                (legacy_class_number,),
            )
            legacy_row = cursor.fetchone()
            selected_class_id = legacy_row['id'] if legacy_row else None

    if raw_turma_id and selected_turma_id is None:
        flash('Seleccione uma turma válida.', 'warning')
        selected_turma_id = None

    if selected_class_id is not None:
        cursor.execute(
            'SELECT id, numero, nivel FROM classes WHERE id = ? AND numero BETWEEN 8 AND 12',
            (selected_class_id,),
        )
        selected_class = cursor.fetchone()
        if not selected_class:
            flash('A classe seleccionada não existe.', 'warning')
            selected_class_id = None
    else:
        selected_class = None

    selected_turma = None
    if selected_turma_id is not None:
        cursor.execute('''
            SELECT t.id, t.nome, t.classe_id, t.ano_lectivo_id,
                   c.numero AS classe_num
            FROM turmas t
            JOIN classes c ON c.id = t.classe_id
            WHERE t.id = ? AND c.numero BETWEEN 8 AND 12
        ''', (selected_turma_id,))
        selected_turma = cursor.fetchone()
        if not selected_turma:
            flash('A turma seleccionada não existe.', 'warning')
            selected_turma_id = None
        elif selected_class_id and selected_turma['classe_id'] != selected_class_id:
            flash('A turma não pertence à classe seleccionada.', 'danger')
            return redirect(url_for('secretary.students_list'))
        else:
            if selected_class_id is None:
                selected_class_id = selected_turma['classe_id']
                cursor.execute(
                    'SELECT id, numero, nivel FROM classes WHERE id = ?',
                    (selected_class_id,),
                )
                selected_class = cursor.fetchone()
            if explicit_year and not all_years and selected_year_id != selected_turma['ano_lectivo_id']:
                flash('A turma não pertence ao ano lectivo seleccionado.', 'warning')
                return redirect(url_for('secretary.students_list'))
            if not explicit_year:
                selected_year_id = selected_turma['ano_lectivo_id']

    if turno_filtro not in ('', 'Manhã', 'Tarde', 'Noite'):
        turno_filtro = ''
    if modalidade_filtro not in ('', 'Diurno', 'Nocturno', 'À Distância'):
        modalidade_filtro = ''
    if area_filtro not in ('', 'A', 'B', 'C'):
        area_filtro = ''
    if filtro_especial not in ('', 'isentos', 'nee'):
        filtro_especial = ''
    if per_page not in (10, 20, 50):
        per_page = 10
    page = max(page, 1)

    pode_ver_inactivos = session.get('user_role') in ('chefe_secretaria', 'admin')
    if situacao not in ('activos', 'inactivos', 'todos'):
        situacao = 'activos'
    if situacao != 'activos' and not pode_ver_inactivos:
        flash('A consulta de matrículas inactivas requer perfil autorizado.', 'warning')
        situacao = 'activos'
    if situacao == 'inactivos' and not student_deletion_schema_ready(cursor):
        flash('A consulta de matrículas inactivas requer a migração de dados.', 'warning')
        situacao = 'activos'

    turma_conditions = []
    turma_params = []
    if selected_year_id is not None:
        turma_conditions.append('t.ano_lectivo_id = ?')
        turma_params.append(selected_year_id)
    if turno_filtro:
        turma_conditions.append('t.turno = ?')
        turma_params.append(turno_filtro)
    if modalidade_filtro:
        turma_conditions.append('t.curso = ?')
        turma_params.append(modalidade_filtro)
    if area_filtro:
        turma_conditions.append('t.area_curricular = ?')
        turma_params.append(area_filtro)
    turma_filter = ' AND '.join(turma_conditions) or '1 = 1'

    student_conditions = []
    student_params = []
    if situacao == 'activos':
        student_conditions.append(active_student_condition(cursor, 's'))
    elif situacao == 'inactivos':
        student_conditions.append('s.ativo = 0')
    if selected_year_id is not None:
        student_conditions.append('s.ano_lectivo_id = ?')
        student_params.append(selected_year_id)
    if busca:
        student_conditions.append('(s.nome LIKE ? OR s.code LIKE ? OR s.bi_cedula LIKE ?)')
        pattern = f'%{busca}%'
        student_params.extend((pattern, pattern, pattern))
    if filtro_especial == 'isentos':
        student_conditions.append('COALESCE(s.isento_taxa, 0) = 1')
    elif filtro_especial == 'nee':
        student_conditions.append(
            "COALESCE(NULLIF(TRIM(s.nee_tipo), ''), 'Nenhuma') <> 'Nenhuma'"
        )
    student_filter = ' AND '.join(student_conditions) or '1 = 1'
    has_student_filter = bool(
        busca or filtro_especial or situacao != 'activos'
    )
    has_turma_filter = bool(
        explicit_year or turno_filtro or modalidade_filtro or area_filtro
    )

    base_filter_args = {}
    if busca:
        base_filter_args['q'] = busca
    if selected_year_id is not None:
        base_filter_args['ano_lectivo_id'] = selected_year_id
    elif all_years:
        base_filter_args['ano_lectivo_id'] = 'todos'
    if turno_filtro:
        base_filter_args['turno'] = turno_filtro
    if modalidade_filtro:
        base_filter_args['modalidade'] = modalidade_filtro
    if area_filtro:
        base_filter_args['area'] = area_filtro
    if filtro_especial:
        base_filter_args['especial'] = filtro_especial
    if situacao != 'activos':
        base_filter_args['situacao'] = situacao

    back_to_classes_url = url_for('secretary.students_list', **base_filter_args)
    back_to_turmas_args = dict(base_filter_args)
    if selected_class_id is not None:
        back_to_turmas_args['classe_id'] = selected_class_id
    back_to_turmas_url = url_for('secretary.students_list', **back_to_turmas_args)
    view_level = 'classes'
    class_cards = []
    turma_cards = []
    alunos = []
    total_alunos = 0
    total_pages = 1
    pagination_urls = {}
    page_url_args = dict(base_filter_args)

    has_deletion_schema = student_deletion_schema_ready(cursor)
    pending_request_expression = (
        """EXISTS (
            SELECT 1 FROM student_deletion_requests dr
            WHERE dr.student_id = s.id AND dr.estado = 'Pendente'
        )"""
        if has_deletion_schema else '0'
    )

    if selected_turma_id is not None and selected_class_id is not None:
        view_level = 'students'
        cursor.execute(f'''
            SELECT t.id
            FROM turmas t
            WHERE t.id = ? AND t.classe_id = ? AND {turma_filter}
        ''',
            [selected_turma_id, selected_class_id] + turma_params,
        )
        if not cursor.fetchone():
            flash('A turma não corresponde aos filtros seleccionados.', 'warning')
            selected_turma_id = None
            view_level = 'turmas'
        else:
            page_url_args.update({
                'classe_id': selected_class_id,
                'turma_id': selected_turma_id,
            })
            cursor.execute(f'''
                SELECT COUNT(*) AS total
                FROM students s
                JOIN turmas t ON t.id = s.turma_id
                WHERE s.turma_id = ? AND {turma_filter} AND {student_filter}
            ''', [selected_turma_id] + turma_params + student_params)
            total_alunos = cursor.fetchone()['total']
            total_pages = max((total_alunos + per_page - 1) // per_page, 1)
            page = min(page, total_pages)
            cursor.execute(f'''
                SELECT s.*, t.nome AS turma_nome, t.sala AS turma_sala,
                       t.area_curricular, c.numero AS classe_num,
                       {pending_request_expression} AS pedido_eliminacao_pendente
                FROM students s
                JOIN turmas t ON t.id = s.turma_id
                JOIN classes c ON c.id = t.classe_id
                WHERE s.turma_id = ? AND {turma_filter} AND {student_filter}
                ORDER BY s.nome ASC
                LIMIT ? OFFSET ?
            ''',
                [selected_turma_id] + turma_params + student_params
                + [per_page, (page - 1) * per_page])
            alunos = cursor.fetchall()
            for page_number in range(1, total_pages + 1):
                args = dict(page_url_args)
                args.update(page=page_number, per_page=per_page)
                pagination_urls[page_number] = url_for('secretary.students_list', **args)
    if selected_class_id is not None and view_level != 'students':
        view_level = 'turmas'
        turma_query = f'''
            SELECT t.id, t.nome, t.sala, t.turno, t.curso, t.area_curricular,
                   t.capacidade_maxima, c.numero AS classe_num,
                   ay.ano AS ano_lectivo, u.nome AS diretor_turma,
                   COUNT(DISTINCT s.id) AS inscritos,
                   COUNT(DISTINCT CASE WHEN s.sexo = 'M' THEN s.id END) AS masculino,
                   COUNT(DISTINCT CASE WHEN s.sexo = 'F' THEN s.id END) AS feminino
            FROM turmas t
            JOIN classes c ON c.id = t.classe_id
            JOIN academic_years ay ON ay.id = t.ano_lectivo_id
            LEFT JOIN users u ON u.id = t.diretor_turma_id
            LEFT JOIN students s ON s.turma_id = t.id AND {student_filter}
            WHERE t.classe_id = ? AND {turma_filter}
            GROUP BY t.id, c.numero, ay.ano, u.nome
            ORDER BY t.nome
        '''
        cursor.execute(turma_query, student_params + [selected_class_id] + turma_params)
        for row in cursor.fetchall():
            if has_student_filter and row['inscritos'] == 0:
                continue
            turma = dict(row)
            turma['vagas'] = max((turma['capacidade_maxima'] or 0) - turma['inscritos'], 0)
            turma['ocupacao'] = (
                round(turma['inscritos'] * 100 / turma['capacidade_maxima'])
                if turma['capacidade_maxima'] else 0
            )
            args = dict(base_filter_args)
            args.update(classe_id=selected_class_id, turma_id=turma['id'])
            turma['url'] = url_for('secretary.students_list', **args)
            turma_cards.append(turma)
    if selected_class_id is None:
        view_level = 'classes'
        class_query = f'''
            SELECT c.id, c.numero, c.nivel,
                   COUNT(t.id) AS total_turmas,
                   COALESCE(SUM(COALESCE(t.capacidade_maxima, 0)), 0) AS capacidade
            FROM classes c
            LEFT JOIN turmas t ON t.classe_id = c.id AND {turma_filter}
            WHERE c.numero BETWEEN 8 AND 12
            GROUP BY c.id, c.numero, c.nivel
            ORDER BY c.numero
        '''
        cursor.execute(class_query, turma_params)
        class_rows = cursor.fetchall()
        stats_query = f'''
            SELECT c.id,
                   COUNT(DISTINCT s.id) AS inscritos,
                   COUNT(DISTINCT CASE WHEN s.sexo = 'M' THEN s.id END) AS masculino,
                   COUNT(DISTINCT CASE WHEN s.sexo = 'F' THEN s.id END) AS feminino
            FROM classes c
            LEFT JOIN turmas t ON t.classe_id = c.id AND {turma_filter}
            LEFT JOIN students s ON s.turma_id = t.id AND {student_filter}
            WHERE c.numero BETWEEN 8 AND 12
            GROUP BY c.id
        '''
        cursor.execute(stats_query, turma_params + student_params)
        student_stats = {row['id']: row for row in cursor.fetchall()}
        for row in class_rows:
            stats = student_stats[row['id']]
            if has_student_filter and stats['inscritos'] == 0:
                continue
            if has_turma_filter and row['total_turmas'] == 0:
                continue
            card = dict(row)
            card['inscritos'] = stats['inscritos']
            card['masculino'] = stats['masculino']
            card['feminino'] = stats['feminino']
            card['vagas'] = max(card['capacidade'] - card['inscritos'], 0)
            card['ocupacao'] = (
                round(card['inscritos'] * 100 / card['capacidade'])
                if card['capacidade'] else 0
            )
            args = dict(base_filter_args)
            args['classe_id'] = card['id']
            card['url'] = url_for('secretary.students_list', **args)
            class_cards.append(card)

    classes = [row for row in cursor.execute(
        'SELECT id, numero FROM classes WHERE numero BETWEEN 8 AND 12 ORDER BY numero'
    ).fetchall()]
    turma_conditions_for_options = []
    turma_option_params = []
    if selected_year_id is not None:
        turma_conditions_for_options.append('t.ano_lectivo_id = ?')
        turma_option_params.append(selected_year_id)
    if selected_class_id is not None:
        turma_conditions_for_options.append('t.classe_id = ?')
        turma_option_params.append(selected_class_id)
    if turno_filtro:
        turma_conditions_for_options.append('t.turno = ?')
        turma_option_params.append(turno_filtro)
    if modalidade_filtro:
        turma_conditions_for_options.append('t.curso = ?')
        turma_option_params.append(modalidade_filtro)
    if area_filtro:
        turma_conditions_for_options.append('t.area_curricular = ?')
        turma_option_params.append(area_filtro)
    option_filter = ' AND '.join(turma_conditions_for_options) or '1 = 1'
    cursor.execute(f'''
        SELECT t.id, t.nome, c.numero AS classe_num, t.area_curricular
        FROM turmas t JOIN classes c ON c.id = t.classe_id
        WHERE c.numero BETWEEN 8 AND 12 AND {option_filter}
        ORDER BY c.numero, t.nome
    ''', turma_option_params)
    turmas_disponiveis = cursor.fetchall()

    if selected_class:
        selected_class = dict(selected_class)
    if selected_turma:
        selected_turma = dict(selected_turma)
    return render_template(
        'secretary/students.html',
        view_level=view_level,
        class_cards=class_cards,
        turma_cards=turma_cards,
        alunos=alunos,
        total_alunos=total_alunos,
        total_pages=total_pages,
        pagination_urls=pagination_urls,
        page=page,
        per_page=per_page,
        filter_args=base_filter_args,
        selected_class=selected_class,
        selected_turma=selected_turma,
        selected_class_id=selected_class_id,
        selected_turma_id=selected_turma_id,
        selected_year_id=selected_year_id,
        academic_years=academic_years,
        classes=classes,
        turmas_disponiveis=turmas_disponiveis,
        turno_filtro=turno_filtro,
        modalidade_filtro=modalidade_filtro,
        area_filtro=area_filtro,
        busca=busca,
        filtro_especial=filtro_especial,
        situacao=situacao,
        pode_ver_inactivos=pode_ver_inactivos,
        areas_curriculares=AREAS_CURRICULARES,
        back_to_classes_url=back_to_classes_url,
        back_to_turmas_url=back_to_turmas_url,
    )


@secretary_bp.route('/students/<int:student_id>/profile')
def student_profile(student_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))

    db = get_db()
    cursor = db.cursor()
    visibility = (
        '1 = 1'
        if session.get('user_role') in ('chefe_secretaria', 'admin')
        else active_student_condition(cursor, 's')
    )
    cursor.execute(f'''
        SELECT s.*, t.nome AS turma_nome, t.sala AS turma_sala,
               t.classe_id AS classe_id,
               t.area_curricular, c.numero AS classe_num,
               ay.ano AS ano_lectivo, u.nome AS diretor_turma
        FROM students s
        LEFT JOIN turmas t ON t.id = s.turma_id
        LEFT JOIN classes c ON c.id = t.classe_id
        LEFT JOIN academic_years ay ON ay.id = s.ano_lectivo_id
        LEFT JOIN users u ON u.id = t.diretor_turma_id
        WHERE s.id = ? AND {visibility}
    ''', (student_id,))
    aluno = cursor.fetchone()
    if not aluno:
        flash('Aluno não encontrado ou sem permissão para consulta.', 'warning')
        return redirect(url_for('secretary.students_list'))

    secoes = (
        ('Dados escolares', (
            ('Processo', aluno['code']),
            ('Classe', f"{aluno['classe_num']}.ª" if aluno['classe_num'] else '—'),
            ('Turma', aluno['turma_nome'] or 'Sem turma'),
            ('Área curricular', AREAS_CURRICULARES.get(aluno['area_curricular'], '—')),
            ('Ano lectivo', aluno['ano_lectivo'] or '—'),
            ('Director de Turma', aluno['diretor_turma'] or 'Não atribuído'),
            ('Data de matrícula', aluno['data_matricula']),
            ('Situação da matrícula', 'Activa' if aluno['ativo'] else 'Inactiva'),
        )),
        ('Identificação', (
            ('Nome completo', aluno['nome']),
            ('BI / Cédula', aluno['bi_cedula']),
            ('Sexo', 'Masculino' if aluno['sexo'] == 'M' else 'Feminino' if aluno['sexo'] == 'F' else '—'),
            ('Data de nascimento', aluno['data_nascimento']),
            ('Naturalidade', aluno['naturalidade']),
            ('Província de nascimento', aluno['provincia_nascimento']),
            ('Nacionalidade', aluno['nacionalidade']),
            ('Morada', aluno['morada']),
        )),
        ('Pai e mãe', (
            ('Nome do pai', aluno['nome_pai']),
            ('Naturalidade do pai', aluno['pai_naturalidade']),
            ('Profissão do pai', aluno['pai_profissao']),
            ('Contacto do pai', aluno['pai_contacto']),
            ('Nome da mãe', aluno['nome_mae']),
            ('Naturalidade da mãe', aluno['mae_naturalidade']),
            ('Profissão da mãe', aluno['mae_profissao']),
            ('Contacto da mãe', aluno['mae_contacto']),
        )),
        ('Encarregado de Educação', (
            ('Nome', aluno['encarregado_nome']),
            ('Parentesco', aluno['encarregado_parentesco']),
            ('Contacto principal', aluno['encarregado_contacto_principal'] or aluno['encarregado_contacto_1']),
            ('Contacto alternativo', aluno['encarregado_contacto_alternativo'] or aluno['encarregado_contacto_2']),
            ('E-mail', aluno['encarregado_email']),
            ('Profissão', aluno['encarregado_profissao']),
            ('Morada', aluno['encarregado_morada']),
        )),
        ('Ingresso e situação especial', (
            ('Situação de ingresso', aluno['ingresso_situacao']),
            ('Escola de origem', aluno['escola_origem']),
            ('Província de origem', aluno['provincia_origem']),
            ('Distrito de origem', aluno['distrito_origem']),
            ('Classe anterior', aluno['classe_anterior']),
            ('Ano lectivo anterior', aluno['ano_lectivo_anterior']),
            ('Isento de taxa', 'Sim' if aluno['isento_taxa'] else 'Não'),
            ('Justificação da isenção', aluno['justificacao_isencao']),
            ('Necessidades Educativas Especiais', aluno['nee_tipo']),
            ('Observações', aluno['observacoes_transferencia']),
        )),
    )
    return render_template('secretary/student_profile.html', aluno=aluno, secoes=secoes)


@secretary_bp.route('/encarregados', methods=['GET', 'POST'])
def manage_guardians():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))

    db = get_db()
    cursor = db.cursor()
    if request.method == 'POST':
        action = request.form.get('acao')
        email = request.form.get('email', '').strip().lower()
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            flash('Informe um endereço de e-mail válido.', 'danger')
            return redirect(url_for('secretary.manage_guardians'))

        if action == 'associar':
            try:
                student_id = int(request.form.get('student_id', ''))
            except ValueError:
                flash('Seleccione um aluno válido.', 'danger')
                return redirect(url_for('secretary.manage_guardians'))

            active_student = active_student_condition(cursor)
            cursor.execute(
                f'SELECT id FROM students WHERE id = ? AND {active_student}',
                (student_id,),
            )
            if not cursor.fetchone():
                flash('Aluno não encontrado.', 'danger')
                return redirect(url_for('secretary.manage_guardians'))

            cursor.execute(
                'SELECT id, ativo FROM guardians WHERE LOWER(email) = ?',
                (email,),
            )
            guardian = cursor.fetchone()
            if guardian:
                if not guardian['ativo']:
                    flash('A conta está inactiva. Contacte a Direcção antes de associar educandos.', 'danger')
                    return redirect(url_for('secretary.manage_guardians'))
                guardian_id = guardian['id']
            else:
                nome = request.form.get('nome', '').strip()
                password = request.form.get('password', '')
                contacto = request.form.get('contacto', '').strip()
                if not nome or len(password) < 12:
                    flash(
                        'Para uma nova conta, informe o nome e uma palavra-passe com pelo menos 12 caracteres.',
                        'danger',
                    )
                    return redirect(url_for('secretary.manage_guardians'))
                cursor.execute('''
                    INSERT INTO guardians (nome, email, password_hash, contacto)
                    VALUES (?, ?, ?, ?)
                ''', (nome, email, generate_password_hash(password), contacto or None))
                guardian_id = cursor.lastrowid

            cursor.execute('''
                SELECT 1 FROM guardian_students WHERE guardian_id = ? AND student_id = ?
            ''', (guardian_id, student_id))
            if cursor.fetchone():
                flash('Esta conta já está associada ao aluno seleccionado.', 'info')
                return redirect(url_for('secretary.manage_guardians'))

            principal = 1 if request.form.get('principal') else 0
            if principal:
                cursor.execute(
                    'UPDATE guardian_students SET principal = 0 WHERE student_id = ?',
                    (student_id,),
                )
            cursor.execute('''
                INSERT INTO guardian_students (guardian_id, student_id, parentesco, principal)
                VALUES (?, ?, ?, ?)
            ''', (
                guardian_id,
                student_id,
                request.form.get('parentesco', '').strip() or None,
                principal,
            ))
            db.commit()
            log_audit(
                session['user_id'],
                'ASSOCIAR_ENCARREGADO',
                f'Conta de encarregado {guardian_id} associada ao aluno {student_id}.',
            )
            flash('Conta de encarregado associada ao aluno.', 'success')
            return redirect(url_for('secretary.manage_guardians'))

        if action == 'redefinir_password':
            password = request.form.get('password', '')
            if len(password) < 12:
                flash('A nova palavra-passe deve ter pelo menos 12 caracteres.', 'danger')
                return redirect(url_for('secretary.manage_guardians'))
            cursor.execute(
                'UPDATE guardians SET password_hash = ?, session_version = session_version + 1 WHERE LOWER(email) = ?',
                (generate_password_hash(password), email),
            )
            if cursor.rowcount != 1:
                db.rollback()
                flash('Conta de encarregado não encontrada.', 'danger')
                return redirect(url_for('secretary.manage_guardians'))
            db.commit()
            log_audit(
                session['user_id'],
                'REDEFINIR_PASSWORD_ENCARREGADO',
                f'Palavra-passe da conta {email} redefinida pela Secretaria.',
            )
            flash('Palavra-passe redefinida. Entregue-a ao encarregado por um canal seguro.', 'success')
            return redirect(url_for('secretary.manage_guardians'))

        flash('Acção desconhecida.', 'danger')
        return redirect(url_for('secretary.manage_guardians'))

    active_students = active_student_condition(cursor)
    cursor.execute(
        f'SELECT id, code, nome FROM students WHERE {active_students} ORDER BY nome'
    )
    alunos = cursor.fetchall()
    cursor.execute('''
        SELECT g.id, g.nome, g.email, g.contacto, g.ativo,
               COUNT(gs.student_id) AS total_educandos
        FROM guardians g
        LEFT JOIN guardian_students gs ON gs.guardian_id = g.id
        GROUP BY g.id, g.nome, g.email, g.contacto, g.ativo
        ORDER BY g.nome
    ''')
    encarregados = cursor.fetchall()
    return render_template(
        'secretary/guardians.html',
        alunos=alunos,
        encarregados=encarregados,
    )


# FLUXO DE MATRÍCULA EM 2 ETAPAS (PASSO 1: DADOS DO ALUNO -> PASSO 2: PAGAMENTO OBRIGATÓRIO ANTES DE GRAVAR NO BANCO)
@secretary_bp.route('/students/new', methods=['GET', 'POST'])
def student_form():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
        
    db = get_db()
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        bi_cedula = request.form.get('bi_cedula', '').strip()
        data_nasc = request.form.get('data_nascimento')
        
        cursor = db.cursor()
        selected_year = request.form.get('ano_lectivo_id', '').strip()
        if selected_year:
            try:
                selected_year = int(selected_year)
            except ValueError:
                flash('Seleccione um ano lectivo válido.', 'danger')
                return redirect(url_for('secretary.student_form'))
            cursor.execute(
                'SELECT id FROM academic_years WHERE id = ? AND ativo = 1',
                (selected_year,),
            )
        else:
            cursor.execute(
                'SELECT id FROM academic_years WHERE ativo = 1 ORDER BY ano DESC LIMIT 1'
            )
        active_year = cursor.fetchone()
        if not active_year:
            flash('Não existe um ano lectivo activo configurado.', 'danger')
            return redirect(url_for('secretary.student_form'))
        selected_year = active_year['id']
        
        # VALIDAÇÃO 1: VERIFICAR DUPLICAÇÃO DE BI
        cursor.execute('SELECT id, nome, classe_ingresso FROM students WHERE bi_cedula = ?', (bi_cedula,))
        existente_bi = cursor.fetchone()
        if existente_bi:
            flash(f'ERRO: O aluno com o BI/Cédula {bi_cedula} já se encontra matriculado no sistema (Nome: {existente_bi["nome"]}, {existente_bi["classe_ingresso"]}ª Classe)! Não são permitidas duplicações.', 'danger')
            return redirect(url_for('secretary.student_form'))
            
        # VALIDAÇÃO 2: BLOQUEAR NOME DE FUNCIONÁRIO
        cursor.execute('SELECT id FROM users WHERE LOWER(nome) = LOWER(?)', (nome,))
        if cursor.fetchone():
            flash(f'ERRO DE SEGURANÇA: O nome "{nome}" pertence a um funcionário registado na escola.', 'danger')
            return redirect(url_for('secretary.student_form'))

        isento = 1 if request.form.get('isento_taxa') else 0
        classe_sugerida = calcular_classe_sugerida(data_nasc)
        try:
            classe_atribuida = int(request.form.get('classe', ''))
        except ValueError:
            flash('Seleccione uma classe válida entre a 8.ª e a 12.ª.', 'danger')
            return redirect(url_for('secretary.student_form'))

        area_curricular = request.form.get('area_curricular') or None
        turno = request.form.get('turno', 'Manhã')
        curso = request.form.get('curso', 'Diurno')
        if classe_atribuida not in range(8, 13):
            flash('As novas matrículas devem ser para a 8.ª à 12.ª Classe.', 'danger')
            return redirect(url_for('secretary.student_form'))
        if turno not in ('Manhã', 'Tarde') or curso not in ('Diurno', 'À Distância'):
            flash('Seleccione um turno e uma modalidade válidos.', 'danger')
            return redirect(url_for('secretary.student_form'))
        if classe_atribuida >= 10 and area_curricular not in AREAS_CURRICULARES:
            flash('Seleccione a área curricular para a classe escolhida.', 'danger')
            return redirect(url_for('secretary.student_form'))
        if classe_atribuida <= 9:
            area_curricular = None

        if not nome or not bi_cedula or not data_nasc:
            flash('Nome, documento de identificação e data de nascimento são obrigatórios.', 'danger')
            return redirect(url_for('secretary.student_form'))
        try:
            datetime.strptime(data_nasc, '%Y-%m-%d')
        except (TypeError, ValueError):
            flash('A data de nascimento é inválida.', 'danger')
            return redirect(url_for('secretary.student_form'))
        if request.form.get('sexo') not in ('M', 'F'):
            flash('Seleccione um sexo válido.', 'danger')
            return redirect(url_for('secretary.student_form'))
        if data_nasc > datetime.now().strftime('%Y-%m-%d'):
            flash('A data de nascimento não pode estar no futuro.', 'danger')
            return redirect(url_for('secretary.student_form'))

        encarregado_parentesco = request.form.get('encarregado_parentesco', '').strip()
        if encarregado_parentesco not in ('Pai', 'Mãe', 'Outro'):
            flash('Seleccione o grau de parentesco do encarregado.', 'danger')
            return redirect(url_for('secretary.student_form'))
        if encarregado_parentesco == 'Pai':
            encarregado_nome = request.form.get('encarregado_nome', '').strip() or request.form.get('nome_pai', '').strip()
            encarregado_contacto = (
                request.form.get('encarregado_contacto_principal', '').strip()
                or request.form.get('pai_contacto', '').strip()
            )
        elif encarregado_parentesco == 'Mãe':
            encarregado_nome = request.form.get('encarregado_nome', '').strip() or request.form.get('nome_mae', '').strip()
            encarregado_contacto = (
                request.form.get('encarregado_contacto_principal', '').strip()
                or request.form.get('mae_contacto', '').strip()
            )
        else:
            encarregado_nome = request.form.get('encarregado_nome', '').strip()
            encarregado_contacto = request.form.get('encarregado_contacto_principal', '').strip()
        if not encarregado_nome:
            flash('Informe o nome do Encarregado de Educação.', 'danger')
            return redirect(url_for('secretary.student_form'))
        encarregado_email = request.form.get('encarregado_email', '').strip()
        if encarregado_email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', encarregado_email):
            flash('O e-mail do encarregado não é válido.', 'danger')
            return redirect(url_for('secretary.student_form'))

        ingresso_situacao = request.form.get('ingresso_situacao', 'Interno')
        if ingresso_situacao not in ('Interno', 'Transferido'):
            flash('Seleccione uma situação de ingresso válida.', 'danger')
            return redirect(url_for('secretary.student_form'))

        transfer_data = {
            'escola_origem': request.form.get('escola_origem', '').strip(),
            'provincia_origem': request.form.get('provincia_origem', '').strip(),
            'distrito_origem': request.form.get('distrito_origem', '').strip(),
            'classe_anterior': request.form.get('classe_anterior', '').strip(),
            'ano_lectivo_anterior': request.form.get('ano_lectivo_anterior', '').strip(),
            'observacoes_transferencia': request.form.get('observacoes_transferencia', '').strip(),
        }
        if ingresso_situacao == 'Transferido':
            if not all(transfer_data[key] for key in (
                'escola_origem', 'provincia_origem', 'distrito_origem',
                'classe_anterior', 'ano_lectivo_anterior',
            )):
                flash('Preencha os dados da escola e classe anterior do aluno transferido.', 'danger')
                return redirect(url_for('secretary.student_form'))
            try:
                transfer_data['classe_anterior'] = int(transfer_data['classe_anterior'])
                transfer_data['ano_lectivo_anterior'] = int(transfer_data['ano_lectivo_anterior'])
            except ValueError:
                flash('A classe e o ano lectivo anteriores devem ser valores numéricos.', 'danger')
                return redirect(url_for('secretary.student_form'))
            if transfer_data['classe_anterior'] not in range(1, 13):
                flash('A classe anterior indicada não é válida.', 'danger')
                return redirect(url_for('secretary.student_form'))
            if (
                transfer_data['ano_lectivo_anterior'] < 2000
                or transfer_data['ano_lectivo_anterior'] > datetime.now().year
            ):
                flash('O ano lectivo anterior indicado não é válido.', 'danger')
                return redirect(url_for('secretary.student_form'))
            document = request.files.get('documento_transferencia')
            if not document or not document.filename:
                flash('Anexe o documento de transferência em PDF.', 'danger')
                return redirect(url_for('secretary.student_form'))

        try:
            photo = request.files.get('fotografia')
            photo_path = save_student_photo(photo) if photo and photo.filename else None
            transfer_file = request.files.get('documento_transferencia')
            transfer_path = (
                save_transfer_document(transfer_file)
                if ingresso_situacao == 'Transferido' and transfer_file and transfer_file.filename
                else None
            )
        except ValueError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('secretary.student_form'))

        justificacao = request.form.get('justificacao_alteracao') if classe_atribuida != classe_sugerida else ""
        justificacao_isencao = request.form.get('justificacao_isencao') if isento else ""
        
        # Guarda temporariamente os dados da matrícula na sessão para avançar ao passo de pagamento
        dados_matricula = {
            'nome': nome,
            'nome_pai': request.form.get('nome_pai', '').strip(),
            'nome_mae': request.form.get('nome_mae', '').strip(),
            'bi_cedula': bi_cedula,
            'data_nascimento': data_nasc,
            'sexo': request.form.get('sexo'),
            'morada': request.form.get('morada', '').strip(),
            'naturalidade': request.form.get('naturalidade', '').strip(),
            'provincia_nascimento': request.form.get('provincia_nascimento', '').strip(),
            'nacionalidade': request.form.get('nacionalidade', '').strip() or 'Moçambicana',
            'foto_path': photo_path,
            'pai_naturalidade': request.form.get('pai_naturalidade', '').strip(),
            'pai_profissao': request.form.get('pai_profissao', '').strip(),
            'pai_contacto': request.form.get('pai_contacto', '').strip(),
            'mae_naturalidade': request.form.get('mae_naturalidade', '').strip(),
            'mae_profissao': request.form.get('mae_profissao', '').strip(),
            'mae_contacto': request.form.get('mae_contacto', '').strip(),
            'encarregado_nome': request.form.get('encarregado_nome', '').strip(),
            'encarregado_parentesco': encarregado_parentesco,
            'encarregado_naturalidade': request.form.get('encarregado_naturalidade', '').strip(),
            'encarregado_profissao': request.form.get('encarregado_profissao', '').strip(),
            'encarregado_contacto_principal': encarregado_contacto,
            'encarregado_contacto_alternativo': request.form.get('encarregado_contacto_alternativo', '').strip(),
            'encarregado_morada': request.form.get('encarregado_morada', '').strip(),
            'encarregado_email': encarregado_email,
            'ingresso_situacao': ingresso_situacao,
            **transfer_data,
            'documento_transferencia_path': transfer_path,
            'contacto_1': request.form.get('contacto_1', '').strip()
                or request.form.get('encarregado_contacto_principal', '').strip(),
            'contacto_2': request.form.get('contacto_2', '').strip(),
            'classe': classe_atribuida,
            'ano_lectivo_id': selected_year,
            'area_curricular': area_curricular,
            'turno': turno,
            'curso': curso,
            'isento': isento,
            'justificacao_isencao': justificacao_isencao,
            'nee_tipo': request.form.get('nee_tipo', 'Nenhuma'),
            'classe_sugerida': classe_sugerida,
            'justificacao': justificacao
        }
        
        # Se o aluno for ISENTO de taxas, regista imediatamente sem pagamento
        if isento == 1:
            return _concluir_matricula_no_banco(db, dados_matricula, None)
            
        # Se NÃO for isento, redireciona para a etapa obrigatória de pagamento
        session['pending_matricula'] = dados_matricula
        flash('Dados do aluno validados! Agora preencha os dados do pagamento/talão bancário para finalizar a inscrição.', 'info')
        return redirect(url_for('secretary.student_registration_payment'))
        
    cursor = db.cursor()
    cursor.execute('SELECT id, ano FROM academic_years WHERE ativo = 1 ORDER BY ano DESC')
    academic_years = cursor.fetchall()
    return render_template('secretary/student_form.html', academic_years=academic_years)

@secretary_bp.route('/students/new/payment', methods=['GET', 'POST'])
def student_registration_payment():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
        
    dados_matricula = session.get('pending_matricula')
    if not dados_matricula:
        flash('Nenhuma pré-inscrição pendente encontrada. Preencha os dados do aluno primeiro.', 'warning')
        return redirect(url_for('secretary.student_form'))
        
    if request.method == 'POST':
        banco = request.form.get('banco_plataforma', '').strip()
        referencia = request.form.get('referencia_talao', '').strip()
        valor_pago = request.form.get('valor_pago', '').strip()
        data_deposito = request.form.get('data_deposito', '').strip()
        mes_pago = request.form.get('mes_pago', 'Matrícula / Inscrição 2026').strip()
        
        if not banco or banco not in BANCOS_PERMITIDOS:
            flash('ERRO: Escolha um banco ou carteira móvel válida (Banco BCI, Millennium BIM, Standard Bank, M-Pesa ou e-Mola).', 'danger')
            return redirect(url_for('secretary.student_registration_payment'))
            
        if not validar_referencia_9_digitos(referencia):
            flash('ERRO: A referência do talão deve conter EXATAMENTE 9 dígitos numéricos (ex: 123456789). O aluno não foi registado.', 'danger')
            return redirect(url_for('secretary.student_registration_payment'))

        if not _valid_payment_period(mes_pago):
            flash('ERRO: Seleccione um período de pagamento válido.', 'danger')
            return redirect(url_for('secretary.student_registration_payment'))
            
        valor_validado = _valid_payment_amount(valor_pago)
        if valor_validado is None:
            flash('ERRO: O valor do pagamento deve ser maior que zero. Sem pagamento válido, o sistema não regista o aluno.', 'danger')
            return redirect(url_for('secretary.student_registration_payment'))
            
        if not _valid_deposit_date(data_deposito):
            flash('ERRO: Informe uma data de depósito válida, que não seja futura.', 'danger')
            return redirect(url_for('secretary.student_registration_payment'))
            
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT p.*, s.nome as aluno_nome FROM payments p JOIN students s ON p.student_id = s.id WHERE referencia_talao = ?', (referencia,))
        existente = cursor.fetchone()
        if existente:
            flash(f'ERRO: A referência {referencia} já foi utilizada anteriormente pelo aluno {existente["aluno_nome"]}!', 'danger')
            return redirect(url_for('secretary.student_registration_payment'))
            
        dados_pagamento = {
            'banco': banco,
            'referencia': referencia,
            'valor_pago': valor_validado,
            'data_deposito': data_deposito,
            'mes_pago': mes_pago
        }
        
        session.pop('pending_matricula', None)
        return _concluir_matricula_no_banco(db, dados_matricula, dados_pagamento)
        
    return render_template(
        'secretary/student_registration_payment.html',
        aluno=dados_matricula,
        bancos=BANCOS_PERMITIDOS,
        hoje=datetime.now().strftime('%Y-%m-%d'),
        areas_curriculares=AREAS_CURRICULARES,
    )

def _concluir_matricula_no_banco(db, m, pag):
    cursor = db.cursor()
    codigo_aluno = gerar_codigo_unico("ALU", "students")
    ano_lectivo_id = m['ano_lectivo_id']
    data_hoje = datetime.now().strftime('%Y-%m-%d')
    
    turma_id = atribuir_turma_automatica(
        db,
        m['classe'],
        ano_lectivo_id,
        m['turno'],
        m['curso'],
        m.get('area_curricular'),
    )
    
    cursor.execute('''
        INSERT INTO students (
            code, nome, nome_pai, nome_mae, bi_cedula, data_nascimento, sexo, morada, naturalidade,
            encarregado_contacto_1, encarregado_contacto_2,
            classe_ingresso, turma_id, ano_lectivo_id, turno, curso, data_matricula, isento_taxa, justificacao_isencao, nee_tipo,
            classe_sugerida, justificacao_alteracao, funcionario_id,
            provincia_nascimento, nacionalidade, foto_path,
            pai_naturalidade, pai_profissao, pai_contacto,
            mae_naturalidade, mae_profissao, mae_contacto,
            encarregado_nome, encarregado_parentesco, encarregado_naturalidade,
            encarregado_profissao, encarregado_contacto_principal,
            encarregado_contacto_alternativo, encarregado_morada, encarregado_email,
            ingresso_situacao, escola_origem, provincia_origem, distrito_origem,
            classe_anterior, ano_lectivo_anterior, documento_transferencia_path,
            observacoes_transferencia
        ) VALUES ({})
    '''.format(', '.join('?' for _ in range(48))), (
        codigo_aluno, m['nome'], m['nome_pai'], m['nome_mae'],
        m['bi_cedula'], m['data_nascimento'], m['sexo'], m['morada'], m['naturalidade'],
        m['contacto_1'], m['contacto_2'],
        m['classe'], turma_id, ano_lectivo_id, m['turno'], m['curso'], data_hoje, m['isento'], m['justificacao_isencao'], m['nee_tipo'],
        m['classe_sugerida'], m['justificacao'], session['user_id'],
        m['provincia_nascimento'], m['nacionalidade'], m['foto_path'],
        m['pai_naturalidade'], m['pai_profissao'], m['pai_contacto'],
        m['mae_naturalidade'], m['mae_profissao'], m['mae_contacto'],
        m['encarregado_nome'], m['encarregado_parentesco'], m['encarregado_naturalidade'],
        m['encarregado_profissao'], m['encarregado_contacto_principal'],
        m['encarregado_contacto_alternativo'], m['encarregado_morada'], m['encarregado_email'],
        m['ingresso_situacao'], m['escola_origem'], m['provincia_origem'], m['distrito_origem'],
        m['classe_anterior'] or None, m['ano_lectivo_anterior'] or None,
        m['documento_transferencia_path'], m['observacoes_transferencia']
    ))
    student_id = cursor.lastrowid
    
    payment_id = None
    if pag is not None:
        recibo_num = gerar_codigo_unico("REC", "payments")
        cursor.execute('''
            INSERT INTO payments (receipt_number, student_id, mes_pago, ano_lectivo_id, valor_pago, banco_plataforma, referencia_talao, data_deposito, data_registo, funcionario_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (recibo_num, student_id, pag['mes_pago'], ano_lectivo_id, pag['valor_pago'], pag['banco'], pag['referencia'], pag['data_deposito'], data_hoje, session['user_id']))
        payment_id = cursor.lastrowid
        
    db.commit()
    log_audit(session['user_id'], 'MATRICULA_COM_PAGAMENTO', f"Aluno {codigo_aluno} ({m['nome']}) matriculado na {m['classe']}ª Classe ({m['curso']}).")
    
    if payment_id:
        flash(f'Matrícula e pagamento registados com sucesso! Aluno: {codigo_aluno}.', 'success')
        return redirect(url_for('secretary.receipt_view', payment_id=payment_id))
    else:
        flash(f'Aluno isento {codigo_aluno} ({m["nome"]}) matriculado com sucesso!', 'success')
        return redirect(url_for('secretary.students_list'))

# EDITAR ALUNO
@secretary_bp.route('/students/edit/<int:student_id>', methods=['GET', 'POST'])
def student_edit(student_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    active_student = active_student_condition(cursor)
    cursor.execute(
        f'SELECT * FROM students WHERE id = ? AND {active_student}',
        (student_id,),
    )
    aluno = cursor.fetchone()
    if not aluno:
        flash('Aluno não encontrado.', 'danger')
        return redirect(url_for('secretary.students_list'))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        bi_cedula = request.form.get('bi_cedula', '').strip()
        cursor.execute('SELECT id FROM students WHERE bi_cedula = ? AND id != ?', (bi_cedula, student_id))
        if cursor.fetchone():
            flash(f'ERRO: O BI {bi_cedula} já pertence a outro aluno.', 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))
            
        fields = (
            'nome', 'nome_pai', 'nome_mae', 'bi_cedula', 'data_nascimento', 'sexo',
            'morada', 'naturalidade', 'provincia_nascimento', 'nacionalidade',
            'encarregado_contacto_1', 'encarregado_contacto_2', 'pai_naturalidade',
            'pai_profissao', 'pai_contacto', 'mae_naturalidade', 'mae_profissao',
            'mae_contacto', 'encarregado_nome', 'encarregado_parentesco',
            'encarregado_naturalidade', 'encarregado_profissao',
            'encarregado_contacto_principal', 'encarregado_contacto_alternativo',
            'encarregado_morada', 'encarregado_email', 'ingresso_situacao',
            'escola_origem', 'provincia_origem', 'distrito_origem',
            'observacoes_transferencia',
        )
        values = {field: request.form.get(field, '').strip() or None for field in fields}
        values['encarregado_contacto_1'] = (
            request.form.get('contacto_1', '').strip()
            or aluno['encarregado_contacto_1']
            or ''
        )
        values['encarregado_contacto_2'] = request.form.get('contacto_2', '').strip() or None
        values['isento_taxa'] = 1 if request.form.get('isento_taxa') else 0
        values['justificacao_isencao'] = request.form.get('justificacao_isencao', '').strip()
        values['nee_tipo'] = request.form.get('nee_tipo', 'Nenhuma').strip()

        if values['ingresso_situacao'] not in ('Interno', 'Transferido'):
            flash('Situação de ingresso inválida.', 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))
        if not values['nome'] or not values['bi_cedula'] or not values['data_nascimento']:
            flash('Nome, identificação e data de nascimento são obrigatórios.', 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))
        try:
            datetime.strptime(values['data_nascimento'], '%Y-%m-%d')
        except ValueError:
            flash('A data de nascimento é inválida.', 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))
        if values['sexo'] not in ('M', 'F'):
            flash('Seleccione um sexo válido.', 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))
        if values['encarregado_email'] and not re.fullmatch(
            r'[^@\s]+@[^@\s]+\.[^@\s]+',
            values['encarregado_email'],
        ):
            flash('O e-mail do encarregado não é válido.', 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))
        if values['ingresso_situacao'] == 'Transferido':
            if not all(values.get(field) for field in (
                'escola_origem', 'provincia_origem', 'distrito_origem',
            )):
                flash('Preencha os dados da escola de origem do aluno transferido.', 'danger')
                return redirect(url_for('secretary.student_edit', student_id=student_id))
            try:
                values['classe_anterior'] = int(request.form.get('classe_anterior', ''))
                values['ano_lectivo_anterior'] = int(request.form.get('ano_lectivo_anterior', ''))
            except ValueError:
                flash('A classe e o ano lectivo anteriores devem ser numéricos.', 'danger')
                return redirect(url_for('secretary.student_edit', student_id=student_id))
            if values['classe_anterior'] not in range(1, 13):
                flash('Classe anterior inválida.', 'danger')
                return redirect(url_for('secretary.student_edit', student_id=student_id))
            if values['ano_lectivo_anterior'] < 2000 or values['ano_lectivo_anterior'] > datetime.now().year:
                flash('Ano lectivo anterior inválido.', 'danger')
                return redirect(url_for('secretary.student_edit', student_id=student_id))
        else:
            values['classe_anterior'] = aluno['classe_anterior']
            values['ano_lectivo_anterior'] = aluno['ano_lectivo_anterior']

        try:
            photo = request.files.get('fotografia')
            values['foto_path'] = (
                save_student_photo(photo) if photo and photo.filename else aluno['foto_path']
            )
            transfer_file = request.files.get('documento_transferencia')
            values['documento_transferencia_path'] = (
                save_transfer_document(transfer_file)
                if transfer_file and transfer_file.filename
                else aluno['documento_transferencia_path']
            )
        except ValueError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))

        if (
            values['ingresso_situacao'] == 'Transferido'
            and not values['documento_transferencia_path']
        ):
            flash('Anexe o documento de transferência em PDF.', 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))

        update_fields = fields + (
            'isento_taxa', 'justificacao_isencao', 'nee_tipo',
            'classe_anterior', 'ano_lectivo_anterior', 'foto_path',
            'documento_transferencia_path',
        )
        assignments = ', '.join(f'{field} = ?' for field in update_fields)
        cursor.execute(
            f'UPDATE students SET {assignments} WHERE id = ?',
            tuple(values.get(field) for field in update_fields) + (student_id,),
        )
        db.commit()
        flash('Dados do aluno atualizados com sucesso!', 'success')
        return redirect(url_for('secretary.students_list'))

    return render_template('secretary/student_edit.html', aluno=aluno)


@secretary_bp.route('/students/file/<filename>')
def student_private_file(filename):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    if not re.fullmatch(r'[a-f0-9]{32}\.(?:jpg|pdf)', filename):
        return '', 404
    return send_from_directory(
        UPLOAD_DIRECTORY,
        filename,
        as_attachment=filename.endswith('.pdf'),
    )


@secretary_bp.route('/turmas/<int:turma_id>/lista-pdf/<tipo>')
def export_class_list_pdf(turma_id, tipo):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    if tipo not in ('vitrine', 'oficial'):
        return '', 404
    if tipo == 'oficial' and session.get('user_role') not in ('chefe_secretaria', 'admin'):
        flash('A lista oficial completa requer perfil de Chefe da Secretaria ou Administrador.', 'danger')
        return redirect(url_for('secretary.gerenciar_turmas'))

    cursor = get_db().cursor()
    cursor.execute('''
        SELECT t.id, t.nome, t.sala, t.turno, t.curso, t.area_curricular,
               c.numero AS classe_num, ay.ano AS ano_lectivo,
               u.nome AS diretor_turma
        FROM turmas t
        JOIN classes c ON c.id = t.classe_id
        JOIN academic_years ay ON ay.id = t.ano_lectivo_id
        LEFT JOIN users u ON u.id = t.diretor_turma_id
        WHERE t.id = ?
    ''', (turma_id,))
    turma = cursor.fetchone()
    if not turma:
        flash('Turma não encontrada.', 'danger')
        return redirect(url_for('secretary.gerenciar_turmas'))

    if tipo == 'vitrine':
        active_students = active_student_condition(cursor)
        cursor.execute(
            f'SELECT nome, sexo FROM students WHERE turma_id = ? AND {active_students} ORDER BY nome',
            (turma_id,),
        )
    else:
        active_students = active_student_condition(cursor, 's')
        cursor.execute(f'''
            SELECT s.nome, s.code, s.sexo, s.data_nascimento,
                   COALESCE(
                       NULLIF((
                           SELECT g.nome
                           FROM guardian_students gs
                           JOIN guardians g ON g.id = gs.guardian_id
                           WHERE gs.student_id = s.id AND g.ativo = 1
                           ORDER BY gs.principal DESC, gs.created_at, gs.guardian_id
                           LIMIT 1
                       ), ''),
                       NULLIF(TRIM(s.encarregado_nome), ''),
                       ''
                   ) AS encarregado_nome,
                   COALESCE(
                       NULLIF((
                           SELECT g.contacto
                           FROM guardian_students gs
                           JOIN guardians g ON g.id = gs.guardian_id
                           WHERE gs.student_id = s.id AND g.ativo = 1
                           ORDER BY gs.principal DESC, gs.created_at, gs.guardian_id
                           LIMIT 1
                       ), ''),
                       NULLIF(TRIM(s.encarregado_contacto_principal), ''),
                       NULLIF(TRIM(s.encarregado_contacto_1), ''),
                       ''
                   ) AS contacto_encarregado
            FROM students s
            WHERE s.turma_id = ? AND {active_students}
            ORDER BY s.nome
        ''', (turma_id,))
    alunos = cursor.fetchall()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'NominalListTitle', parent=styles['Title'], fontName='Helvetica-Bold',
        fontSize=13, leading=16, alignment=TA_CENTER, spaceBefore=5, spaceAfter=8,
    )
    header_style = ParagraphStyle(
        'InstitutionHeader', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=9, leading=12, alignment=TA_CENTER, spaceAfter=0,
    )
    metadata_style = ParagraphStyle(
        'ClassMetadata', parent=styles['Normal'], fontName='Helvetica',
        fontSize=8.5, leading=12, alignment=TA_LEFT, spaceBefore=5, spaceAfter=8,
    )
    cell_style = ParagraphStyle(
        'TableCell', parent=styles['BodyText'], fontName='Helvetica',
        fontSize=8, leading=10, alignment=TA_LEFT, spaceAfter=0, spaceBefore=0,
    )
    table_header_style = ParagraphStyle(
        'TableHeader', parent=cell_style, fontName='Helvetica-Bold',
        textColor=colors.white, alignment=TA_CENTER,
    )

    current_date = datetime.now()
    month_names = (
        'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
        'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
    )
    issue_date = (
        f'{current_date.day} de {month_names[current_date.month - 1]} '
        f'de {current_date.year}'
    )
    male_count = sum(
        str(aluno['sexo'] or '').strip().upper() in {'M', 'MASCULINO'}
        for aluno in alunos
    )
    female_count = sum(
        str(aluno['sexo'] or '').strip().upper() in {'F', 'FEMININO'}
        for aluno in alunos
    )

    output = io.BytesIO()
    page_size = A4 if tipo == 'vitrine' else landscape(A4)
    document = SimpleDocTemplate(
        output,
        pagesize=page_size,
        rightMargin=1.2 * cm,
        leftMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.6 * cm,
        title=f'Lista de Alunos - {turma["ano_lectivo"]} - {turma["nome"]}',
        author='Escola Secundária da Manga',
    )
    story = []
    emblem_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'static',
        'img',
        'emblema_moz.png',
    )
    emblem = ReportImage(emblem_path, width=1.8 * cm, height=1.8 * cm)
    emblem.hAlign = 'CENTER'
    story.extend([
        emblem,
        Paragraph('REPÚBLICA DE MOÇAMBIQUE', header_style),
        Paragraph('PROVÍNCIA DE SOFALA', header_style),
        Paragraph('GOVERNO DO DISTRITO DA BEIRA', header_style),
        Paragraph('ESCOLA SECUNDÁRIA DA MANGA', header_style),
        Spacer(1, 0.25 * cm),
    ])
    list_title = f'LISTA DE ALUNOS — ANO LECTIVO {turma["ano_lectivo"]}'
    story.append(Paragraph(list_title, title_style))
    area = {
        'A': 'Área A — Letras e Ciências Sociais',
        'B': 'Área B — Ciências Naturais e Exactas',
        'C': 'Área C — Artes Visuais e Cénicas / Ciências com Desenho',
    }.get(turma['area_curricular'], '')
    modalidade = 'À Distância' if turma['curso'] == 'À Distância' else turma['turno']
    metadata = (
        f'<b>Turma:</b> {escape(turma["nome"])} &nbsp;&nbsp; '
        f'<b>Classe:</b> {turma["classe_num"]}.ª &nbsp;&nbsp; '
        f'<b>Data:</b> {issue_date}<br/>'
        f'<b>Sala:</b> {escape(turma["sala"] or "—")} &nbsp;&nbsp; '
        f'<b>Área:</b> {escape(area or "—")} &nbsp;&nbsp; '
        f'<b>Turno/Modalidade:</b> {escape(modalidade)}<br/>'
        f'<b>Director de Turma:</b> {escape(turma["diretor_turma"] or "Não atribuído")}<br/>'
        f'<b>Total:</b> {len(alunos)} &nbsp;&nbsp; '
        f'<b>Masculino:</b> {male_count} &nbsp;&nbsp; '
        f'<b>Feminino:</b> {female_count}'
    )
    story.append(Paragraph(metadata, metadata_style))

    if tipo == 'vitrine':
        headers = ('N.º', 'Nome do Aluno')
        data = [[Paragraph(value, table_header_style) for value in headers]]
        data.extend([
            [str(index), Paragraph(escape(aluno['nome'] or ''), cell_style)]
            for index, aluno in enumerate(alunos, 1)
        ])
        widths = [1.4 * cm, page_size[0] - 3.8 * cm]
    else:
        headers = (
            'N.º',
            'Nome completo',
            'Processo',
            'Sexo',
            'Nasc.',
            'Encarregado',
            'Contacto',
        )
        data = [[Paragraph(value, table_header_style) for value in headers]]
        data.extend([
            [
                str(index),
                Paragraph(escape(aluno['nome'] or ''), cell_style),
                Paragraph(escape(aluno['code'] or ''), cell_style),
                Paragraph(escape(aluno['sexo'] or ''), cell_style),
                Paragraph(escape(aluno['data_nascimento'] or ''), cell_style),
                Paragraph(escape(aluno['encarregado_nome'] or ''), cell_style),
                Paragraph(escape(aluno['contacto_encarregado'] or ''), cell_style),
            ]
            for index, aluno in enumerate(alunos, 1)
        ])
        widths = [
            0.9 * cm, 8.3 * cm, 2.3 * cm, 1.3 * cm,
            2.4 * cm, 7.2 * cm, 4.0 * cm,
        ]
    table = Table(data, colWidths=widths, repeatRows=1, splitByRow=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#142B4A')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#98A2B3')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F3F5F8')]),
    ]))
    story.extend([table, Spacer(1, 0.8 * cm)])
    if tipo == 'oficial':
        story.append(Spacer(1, 1.2 * cm))
        story.append(Paragraph(
            'O Chefe da Secretaria: ____________________________________',
            header_style,
        ))
    else:
        story.append(Paragraph(
            f'Beira, _____ de _______________ de {turma["ano_lectivo"]} '
            '&nbsp;&nbsp;&nbsp; O Director: __________________________',
            header_style,
        ))

    def draw_footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#667085'))
        canvas.drawRightString(page_size[0] - 1.5 * cm, 0.8 * cm, f'Página {canvas.getPageNumber()}')
        canvas.restoreState()

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    output.seek(0)
    return send_file(
        output,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'Lista_{tipo}_{turma["classe_num"]}_{turma_id}.pdf',
    )


@secretary_bp.route('/payments/student-info/<int:student_id>')
def payment_student_info(student_id):
    if not check_secretary():
        return {'error': 'Não autorizado'}, 403
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, receipt_number, banco_plataforma, referencia_talao, valor_pago, data_deposito, mes_pago
        FROM payments
        WHERE student_id = ?
        ORDER BY id ASC LIMIT 1
    ''', (student_id,))
    pag = cursor.fetchone()
    if pag:
        return {
            'found': True,
            'payment_id': pag['id'],
            'receipt_number': pag['receipt_number'],
            'banco_plataforma': pag['banco_plataforma'],
            'referencia_talao': pag['referencia_talao'],
            'valor_pago': pag['valor_pago'],
            'data_deposito': pag['data_deposito'],
            'mes_pago': pag['mes_pago']
        }
    return {'found': False}

# NOVO PAGAMENTO AVULSO (MENSALIDADE)
@secretary_bp.route('/payments/new', methods=['GET', 'POST'])
def payment_form():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    active_students = active_student_condition(cursor)
    
    if request.method == 'POST':
        student_id = request.form.get('student_id', '').strip()
        mes_pago = request.form.get('mes_pago', '').strip()
        valor = _valid_payment_amount(request.form.get('valor_pago', ''))
        banco = request.form.get('banco_plataforma', '').strip()
        referencia = request.form.get('referencia_talao', '').strip()
        data_deposito = request.form.get('data_deposito', '').strip()

        try:
            student_id = int(student_id)
        except ValueError:
            student_id = None
        if (
            student_id is None
            or valor is None
            or not _valid_payment_period(mes_pago)
            or not _valid_deposit_date(data_deposito)
        ):
            flash('ERRO: Informe aluno, período, valor positivo e data de depósito válida.', 'danger')
            return redirect(url_for('secretary.payment_form'))
        
        if banco not in BANCOS_PERMITIDOS:
            flash('ERRO: Selecione um banco ou carteira móvel válida (Banco BCI, Millennium BIM, Standard Bank, M-Pesa ou e-Mola).', 'danger')
            return redirect(url_for('secretary.payment_form'))
            
        if not validar_referencia_9_digitos(referencia):
            flash('ERRO: A referência do talão deve conter EXATAMENTE 9 dígitos numéricos.', 'danger')
            return redirect(url_for('secretary.payment_form'))
            
        cursor.execute(
            f'''SELECT id, ano_lectivo_id FROM students
                WHERE id = ? AND {active_students}
                  AND COALESCE(isento_taxa, 0) = 0''',
            (student_id,),
        )
        student = cursor.fetchone()
        if not student:
            flash('ERRO: O aluno seleccionado não existe ou está isento de pagamento.', 'danger')
            return redirect(url_for('secretary.payment_form'))
        
        cursor.execute('SELECT p.*, s.nome as aluno_nome FROM payments p JOIN students s ON p.student_id = s.id WHERE referencia_talao = ?', (referencia,))
        existente = cursor.fetchone()
        
        if existente:
            flash(f'ERRO: A referência {referencia} já foi utilizada para o aluno {existente["aluno_nome"]}!', 'danger')
            return redirect(url_for('secretary.payment_form'))
            
        recibo_num = gerar_codigo_unico("REC", "payments")
        data_registo = datetime.now().strftime('%Y-%m-%d')
        
        try:
            cursor.execute('''
                INSERT INTO payments (
                    receipt_number, student_id, mes_pago, ano_lectivo_id,
                    valor_pago, banco_plataforma, referencia_talao,
                    data_deposito, data_registo, funcionario_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                recibo_num, student_id, mes_pago, student['ano_lectivo_id'],
                valor, banco, referencia, data_deposito, data_registo,
                session['user_id'],
            ))
            db.commit()
            
            payment_id = cursor.lastrowid
            log_audit(session['user_id'], 'PAGAMENTO_REGISTADO', f"Recibo {recibo_num} emitido ({banco}, Ref: {referencia}).")
            flash(f'Pagamento registado com sucesso! Recibo: {recibo_num}', 'success')
            return redirect(url_for('secretary.receipt_view', payment_id=payment_id))
        except DB_INTEGRITY_ERRORS:
            flash(f'ERRO: Referência {referencia} duplicada na base de dados!', 'danger')
            return redirect(url_for('secretary.payment_form'))

    cursor.execute(
        f'''SELECT id, code, nome, classe_ingresso FROM students
            WHERE {active_students} AND isento_taxa = 0
            ORDER BY classe_ingresso ASC, nome ASC'''
    )
    alunos = cursor.fetchall()
    return render_template('secretary/payment_form.html', alunos=alunos, bancos=BANCOS_PERMITIDOS, hoje=datetime.now().strftime('%Y-%m-%d'))

# EDITAR PAGAMENTO
@secretary_bp.route('/payments/edit/<int:payment_id>', methods=['GET', 'POST'])
def payment_edit(payment_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    if request.method == 'POST':
        mes_pago = request.form.get('mes_pago', '').strip()
        valor_pago = _valid_payment_amount(request.form.get('valor_pago', ''))
        banco = request.form.get('banco_plataforma', '').strip()
        referencia = request.form.get('referencia_talao', '').strip()
        data_deposito = request.form.get('data_deposito', '').strip()
        
        if (
            not _valid_payment_period(mes_pago)
            or valor_pago is None
            or banco not in BANCOS_PERMITIDOS
            or not _valid_deposit_date(data_deposito)
        ):
            flash('ERRO: Informe período, valor, banco e data de depósito válidos.', 'danger')
            return redirect(url_for('secretary.payment_edit', payment_id=payment_id))

        if not validar_referencia_9_digitos(referencia):
            flash('ERRO: A referência deve ter exatamente 9 dígitos numéricos.', 'danger')
            return redirect(url_for('secretary.payment_edit', payment_id=payment_id))
            
        cursor.execute('SELECT id FROM payments WHERE referencia_talao = ? AND id != ?', (referencia, payment_id))
        if cursor.fetchone():
            flash(f'ERRO: A referência {referencia} já existe noutro recibo.', 'danger')
            return redirect(url_for('secretary.payment_edit', payment_id=payment_id))
            
        cursor.execute('''
            UPDATE payments SET mes_pago=?, valor_pago=?, banco_plataforma=?, referencia_talao=?, data_deposito=? WHERE id=?
        ''', (mes_pago, valor_pago, banco, referencia, data_deposito, payment_id))
        db.commit()
        flash('Pagamento atualizado com sucesso!', 'success')
        return redirect(url_for('secretary.dashboard'))
        
    cursor.execute('''
        SELECT p.*, s.nome as aluno_nome, s.code as aluno_code 
        FROM payments p JOIN students s ON p.student_id = s.id WHERE p.id = ?
    ''', (payment_id,))
    pagamento = cursor.fetchone()
    return render_template('secretary/payment_edit.html', p=pagamento, bancos=BANCOS_PERMITIDOS)

@secretary_bp.route('/receipt/<int:payment_id>')
def receipt_view(payment_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT p.*, s.nome as aluno_nome, s.code as aluno_code, c.numero as classe_num, u.nome as funcionario_nome
        FROM payments p
        JOIN students s ON p.student_id = s.id
        JOIN classes c ON s.classe_ingresso = c.numero
        JOIN users u ON p.funcionario_id = u.id
        WHERE p.id = ?
    ''', (payment_id,))
    recibo = cursor.fetchone()
    if not recibo:
        return '', 404
    return render_template('secretary/receipt.html', recibo=recibo)

# GERAR LISTA DE ESTUDANTES POR CLASSE E TURMA EM FORMATO WORD (.DOC / .DOCX)
@secretary_bp.route('/exportar-lista-doc/<int:turma_id>')
def exportar_lista_doc(turma_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT t.*, c.numero as classe_num, ay.ano as ano_lectivo
        FROM turmas t
        JOIN classes c ON t.classe_id = c.id
        JOIN academic_years ay ON t.ano_lectivo_id = ay.id
        WHERE t.id = ?
    ''', (turma_id,))
    turma = cursor.fetchone()
    
    if not turma:
        flash('Turma não encontrada.', 'danger')
        return redirect(url_for('secretary.students_list'))
        
    active_students = active_student_condition(cursor)
    cursor.execute(f'''
        SELECT code, nome, bi_cedula, sexo, data_nascimento, encarregado_contacto_1
        FROM students
        WHERE turma_id = ? AND {active_students}
        ORDER BY nome ASC
    ''', (turma_id,))
    alunos = cursor.fetchall()
    
    doc = Document()
    
    # Cabeçalho Institucional com Emblema da República de Moçambique
    p_img = doc.add_paragraph()
    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
    emblema_path = 'static/img/emblema_moz.png'
    if os.path.exists(emblema_path):
        run_img = p_img.add_run()
        run_img.add_picture(emblema_path, width=Inches(0.85))
        
    p_header = doc.add_paragraph()
    p_header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run1 = p_header.add_run("REPÚBLICA DE MOÇAMBIQUE")
    run1.bold = True
    run1.font.size = Pt(11)
    
    run2 = p_header.add_run("PROVÍNCIA DE SOFALA GOVERNO DO DISTRITO DA BEIRA")
    run2.bold = True
    run2.font.size = Pt(10)
    
    run3 = p_header.add_run("ESCOLA SECUNDÁRIA DA MANGA")
    run3.bold = True
    run3.underline = True
    run3.font.size = Pt(12)
    
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_title = p_title.add_run("LISTA OFICIAL DE ESTUDANTES POR CLASSE E TURMA")
    r_title.bold = True
    r_title.font.size = Pt(13)
    
    # Bloco de Metadados (Classe, Turma, Período/Turno, Curso)
    p_meta = doc.add_paragraph()
    p_meta.alignment = WD_ALIGN_PARAGRAPH.LEFT
    meta_text = (
        f"Classe: {turma['classe_num']}ª Classe    |    "
        f"Turma: {turma['nome']}    |    Sala: {turma['sala'] or 'Sala 01'}    |    "
        f"Período (Turno): {turma['turno']}    |    "
        f"Curso: {turma['curso']}    |    "
        f"Ano Lectivo: {turma['ano_lectivo']}"
    )
    r_meta = p_meta.add_run(meta_text)
    r_meta.bold = True
    r_meta.font.size = Pt(10)
    
    # Tabela de Alunos
    table = doc.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    
    hdr_cells = table.rows[0].cells
    headers = ['Nº', 'Código', 'Nome Completo do Estudante', 'Nº BI / Cédula', 'Sexo', 'Contacto Encarregado']
    for i, text in enumerate(headers):
        hdr_cells[i].text = text
        for p in hdr_cells[i].paragraphs:
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(9.5)
                
    for idx, a in enumerate(alunos, start=1):
        row_cells = table.add_row().cells
        row_cells[0].text = str(idx)
        row_cells[1].text = a['code']
        row_cells[2].text = a['nome']
        row_cells[3].text = a['bi_cedula']
        row_cells[4].text = a['sexo']
        row_cells[5].text = a['encarregado_contacto_1'] or '-'
        for cell in row_cells:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9.5)
                    
    p_footer = doc.add_paragraph()
    p_footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_footer.add_run(f"Beira, aos {datetime.now().strftime('%d/%m/%Y')}").font.size = Pt(10)
    r_sig = p_footer.add_run(f"A Secretaria Escolar({session.get('user_nome')})")
    r_sig.bold = True
    r_sig.font.size = Pt(10)
    
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)
    
    safe_name = f"Lista_{turma['classe_num']}Classe_{turma['nome'].replace(' ', '_')}_{turma['curso']}.doc"
    return send_file(
        output,
        as_attachment=True,
        download_name=safe_name,
        mimetype='application/msword'
    )

@secretary_bp.route('/documentos/<int:student_id>/<tipo>')
def emitir_documento(student_id, tipo):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT s.*, t.nome as turma_nome, c.numero as classe_num 
        FROM students s 
        LEFT JOIN turmas t ON s.turma_id = t.id 
        LEFT JOIN classes c ON t.classe_id = c.id
        WHERE s.id = ?
    ''', (student_id,))
    aluno = cursor.fetchone()
    
    if not aluno:
        flash('Aluno não encontrado.', 'danger')
        return redirect(url_for('secretary.students_list'))
        
    cursor.execute('''
        SELECT g.*, sub.nome as disciplina_nome 
        FROM grades g JOIN subjects sub ON g.subject_id = sub.id 
        WHERE g.student_id = ?
    ''', (student_id,))
    notas = cursor.fetchall()
    
    data_hoje = datetime.now().strftime('%d de %B de %Y')
    funcionario_atendeu = session.get('user_nome')
    
    if tipo == 'frequencia':
        return render_template('secretary/docs/declaracao_frequencia.html', aluno=aluno, data_hoje=data_hoje, funcionario_atendeu=funcionario_atendeu)
    elif tipo == 'notas':
        return render_template('secretary/docs/declaracao_notas.html', aluno=aluno, notas=notas, data_hoje=data_hoje, funcionario_atendeu=funcionario_atendeu)
    elif tipo == 'boletim':
        return render_template('secretary/docs/nota_informativa.html', aluno=aluno, notas=notas, data_hoje=data_hoje, funcionario_atendeu=funcionario_atendeu)
        
    flash('Tipo de documento inválido.', 'danger')
    return redirect(url_for('secretary.students_list'))

# CHEFE DA SECRETARIA GERIR COLEGAS
@secretary_bp.route('/colaboradores', methods=['GET', 'POST'])
def gerenciar_colaboradores():
    if session.get('user_role') not in ['chefe_secretaria', 'admin']:
        flash('Apenas o Chefe da Secretaria tem permissão para gerir os colegas do departamento.', 'danger')
        return redirect(url_for('secretary.dashboard'))
        
    db = get_db()
    cursor = db.cursor()
    
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip()
        contacto = request.form.get('contacto', '').strip()
        
        cursor.execute('SELECT id FROM students WHERE LOWER(nome) = LOWER(?)', (nome,))
        if cursor.fetchone():
            flash(f'ERRO: O nome "{nome}" pertence a um aluno registado. Não pode ser registado como funcionário.', 'danger')
            return redirect(url_for('secretary.gerenciar_colaboradores'))
            
        code = gerar_codigo_unico("SEC", "users")
        from werkzeug.security import generate_password_hash
        pwd_hash = generate_password_hash("Secretaria123!")
        
        cursor.execute('''
            INSERT INTO users (code, nome, email, password_hash, role, contacto)
            VALUES (?, ?, ?, ?, 'secretary', ?)
        ''', (code, nome, email, pwd_hash, contacto))
        db.commit()
        log_audit(session['user_id'], 'CADASTRO_FUNCIONARIO', f"Funcionário {code} ({nome}) registado.")
        flash(f'Colaborador {code} ({nome}) registado com sucesso!', 'success')
        return redirect(url_for('secretary.gerenciar_colaboradores'))

    cursor.execute('SELECT * FROM users WHERE role = "secretary" ORDER BY id DESC')
    colegas = cursor.fetchall()
    return render_template('secretary/colaboradores.html', colegas=colegas)

@secretary_bp.route('/colaboradores/edit/<int:user_id>', methods=['GET', 'POST'])
def edit_colega(user_id):
    if session.get('user_role') not in ['chefe_secretaria', 'admin']:
        return redirect(url_for('secretary.dashboard'))
    db = get_db()
    cursor = db.cursor()
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip()
        contacto = request.form.get('contacto', '').strip()
        cursor.execute('UPDATE users SET nome=?, email=?, contacto=? WHERE id=? AND role="secretary"', (nome, email, contacto, user_id))
        db.commit()
        flash('Dados do colega atualizados!', 'success')
        return redirect(url_for('secretary.gerenciar_colaboradores'))
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    colega = cursor.fetchone()
    return render_template('secretary/colaborador_edit.html', colega=colega)

@secretary_bp.route('/colaboradores/toggle/<int:user_id>', methods=['POST'])
def toggle_colega(user_id):
    if session.get('user_role') not in ['chefe_secretaria', 'admin']:
        return redirect(url_for('secretary.dashboard'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('UPDATE users SET ativo = CASE WHEN ativo = 1 THEN 0 ELSE 1 END WHERE id = ? AND role = "secretary"', (user_id,))
    db.commit()
    flash('Estado do colega alterado!', 'info')
    return redirect(url_for('secretary.gerenciar_colaboradores'))

@secretary_bp.route('/colaboradores/delete/<int:user_id>', methods=['POST'])
def delete_colega(user_id):
    if session.get('user_role') not in ['chefe_secretaria', 'admin']:
        return redirect(url_for('secretary.dashboard'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('DELETE FROM users WHERE id = ? AND role = "secretary"', (user_id,))
    db.commit()
    flash('Conta removida com sucesso.', 'warning')
    return redirect(url_for('secretary.gerenciar_colaboradores'))

# ==============================================================================
# GESTÃO DE TURMAS, VAGAS POR SALA E IMPORTAÇÃO/EXPORTAÇÃO DE PAUTAS
# ==============================================================================
@secretary_bp.route('/turmas', methods=['GET', 'POST'])
def gerenciar_turmas():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        'SELECT id, numero FROM classes WHERE numero BETWEEN 8 AND 12 ORDER BY numero'
    )
    classes = cursor.fetchall()
    
    if request.method == 'POST':
        acao = request.form.get('acao')
        if acao == 'criar_turma':
            nome = request.form.get('nome', '').strip()
            sala = request.form.get('sala', '').strip()
            try:
                classe_id = int(request.form.get('classe_id', ''))
                capacidade = int(request.form.get('capacidade_maxima', 40))
            except ValueError:
                flash('Classe ou capacidade inválida.', 'danger')
                return redirect(url_for('secretary.gerenciar_turmas'))
            turno = request.form.get('turno')
            curso = request.form.get('curso')
            area_curricular = request.form.get('area_curricular') or None
            ano_lectivo_raw = request.form.get('ano_lectivo_id', '').strip()
            try:
                ano_lectivo_id = int(ano_lectivo_raw) if ano_lectivo_raw else None
            except ValueError:
                flash('Seleccione um ano lectivo válido.', 'danger')
                return redirect(url_for('secretary.gerenciar_turmas'))
            if ano_lectivo_id:
                cursor.execute(
                    'SELECT id FROM academic_years WHERE id = ? AND ativo = 1',
                    (ano_lectivo_id,),
                )
            else:
                cursor.execute(
                    'SELECT id FROM academic_years WHERE ativo = 1 ORDER BY ano DESC LIMIT 1'
                )
            active_year = cursor.fetchone()
            if not active_year:
                flash('Não existe um ano lectivo activo configurado.', 'danger')
                return redirect(url_for('secretary.gerenciar_turmas'))
            ano_lectivo_id = active_year['id']

            cursor.execute('SELECT numero FROM classes WHERE id = ?', (classe_id,))
            classe_row = cursor.fetchone()
            if not nome or not sala or not classe_row or capacidade < 1:
                flash('Informe turma, sala, classe válida e capacidade maior que zero.', 'danger')
                return redirect(url_for('secretary.gerenciar_turmas'))
            classe_num = classe_row['numero']
            if (
                classe_num not in range(8, 13)
                or turno not in ('Manhã', 'Tarde')
                or curso not in ('Diurno', 'À Distância')
                or (classe_num >= 10 and area_curricular not in AREAS_CURRICULARES)
                or (classe_num <= 9 and area_curricular)
            ):
                flash('A classe, área, turno ou modalidade não é válida para uma nova turma.', 'danger')
                return redirect(url_for('secretary.gerenciar_turmas'))

            if classe_num <= 9:
                valid_name = re.fullmatch(rf'{classe_num}ª [A-Z]+', nome)
            else:
                valid_name = re.fullmatch(rf'{classe_num}ª {area_curricular}\d{{2,}}', nome)
            if not valid_name:
                flash('O código da turma não corresponde à classe e à área seleccionadas.', 'danger')
                return redirect(url_for('secretary.gerenciar_turmas'))

            cursor.execute('UPDATE classes SET numero = numero WHERE id = ?', (classe_id,))
            cursor.execute('''
                SELECT id FROM turmas
                WHERE nome = ? AND classe_id = ? AND ano_lectivo_id = ?
                  AND turno = ? AND curso = ?
                  AND ((area_curricular = ?) OR (area_curricular IS NULL AND ? IS NULL))
            ''', (
                nome, classe_id, ano_lectivo_id, turno, curso,
                area_curricular, area_curricular,
            ))
            if cursor.fetchone():
                flash('Já existe uma turma com este código no contexto seleccionado.', 'danger')
                return redirect(url_for('secretary.gerenciar_turmas'))
            
            cursor.execute('''
                INSERT INTO turmas (
                    nome, sala, classe_id, ano_lectivo_id, turno, curso,
                    capacidade_maxima, vagas_disponiveis, area_curricular
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (nome, sala, classe_id, ano_lectivo_id, turno, curso, capacidade, capacidade, area_curricular))
            db.commit()
            log_audit(session['user_id'], 'CRIAR_TURMA', f"Turma {nome} ({sala}) criada com {capacidade} vagas.")
            flash(f'Turma {nome} ({sala}) criada com {capacidade} vagas!', 'success')
            
        elif acao == 'transferir_aluno':
            try:
                student_id = int(request.form.get('student_id', ''))
                nova_turma_id = int(request.form.get('nova_turma_id', ''))
            except ValueError:
                flash('Seleccione um aluno e uma turma válidos.', 'danger')
                return redirect(url_for('secretary.gerenciar_turmas'))
            
            active_student = active_student_condition(cursor)
            cursor.execute(
                f'SELECT turma_id FROM students WHERE id = ? AND {active_student}',
                (student_id,),
            )
            atual = cursor.fetchone()
            
            cursor.execute('''
                SELECT t.vagas_disponiveis, t.turno, t.curso, t.classe_id, c.numero AS classe_num
                FROM turmas t JOIN classes c ON c.id = t.classe_id
                WHERE t.id = ?
            ''', (nova_turma_id,))
            destino = cursor.fetchone()
            
            if not atual or not destino:
                flash('Aluno ou turma de destino não encontrados.', 'danger')
            elif atual['turma_id'] == nova_turma_id:
                flash('O aluno já está atribuído a esta turma.', 'info')
            else:
                cursor.execute('''
                    UPDATE turmas SET vagas_disponiveis = vagas_disponiveis - 1
                    WHERE id = ? AND vagas_disponiveis > 0
                ''', (nova_turma_id,))
                if cursor.rowcount != 1:
                    db.rollback()
                    flash('ERRO: A turma/sala seleccionada já não possui vagas disponíveis!', 'danger')
                    return redirect(url_for('secretary.gerenciar_turmas'))

                if atual['turma_id']:
                    cursor.execute('''
                        UPDATE turmas
                        SET vagas_disponiveis = CASE
                            WHEN COALESCE(vagas_disponiveis, 0) + 1 > COALESCE(capacidade_maxima, 0)
                            THEN capacidade_maxima
                            ELSE COALESCE(vagas_disponiveis, 0) + 1
                        END
                        WHERE id = ?
                    ''', (atual['turma_id'],))
                cursor.execute('''
                    UPDATE students SET turma_id = ?, turno = ?, curso = ?, classe_ingresso = ?
                    WHERE id = ?
                ''', (nova_turma_id, destino['turno'], destino['curso'], destino['classe_num'], student_id))
                db.commit()
                log_audit(session['user_id'], 'ATRIBUIR_TURMA', f"Aluno ID {student_id} atribuído à Turma ID {nova_turma_id}.")
                flash('Aluno atribuído à turma e controle de vagas actualizado!', 'success')
        return redirect(url_for('secretary.gerenciar_turmas'))

    active_students = active_student_condition(cursor, 's')
    cursor.execute(f'''
        SELECT t.*, c.numero as classe_num, ay.ano as ano_lectivo,
               u.nome AS diretor_turma,
               (SELECT COUNT(*) FROM students s WHERE s.turma_id = t.id AND {active_students}) as inscritos
        FROM turmas t
        JOIN classes c ON t.classe_id = c.id
        JOIN academic_years ay ON t.ano_lectivo_id = ay.id
        LEFT JOIN users u ON u.id = t.diretor_turma_id
        ORDER BY c.numero ASC, t.nome ASC
    ''')
    turmas = cursor.fetchall()
    
    active_students = active_student_condition(cursor)
    cursor.execute(
        f'SELECT id, code, nome, classe_ingresso FROM students WHERE {active_students} ORDER BY nome ASC'
    )
    alunos = cursor.fetchall()
    
    cursor.execute('SELECT id, nome FROM subjects ORDER BY nome ASC')
    disciplinas = cursor.fetchall()
    cursor.execute('SELECT id, ano FROM academic_years WHERE ativo = 1 ORDER BY ano DESC')
    academic_years = cursor.fetchall()
    
    return render_template(
        'secretary/turmas.html',
        turmas=turmas,
        alunos=alunos,
        disciplinas=disciplinas,
        classes=classes,
        areas_curriculares=AREAS_CURRICULARES,
        academic_years=academic_years,
    )

@secretary_bp.route('/turmas/<int:turma_id>')
def class_detail(turma_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))

    db = get_db()
    cursor = db.cursor()
    if not student_deletion_schema_ready(cursor):
        flash(
            'A gestão individual e os pedidos de eliminação requerem a migração de dados pendente.',
            'warning',
        )
        return redirect(url_for('secretary.gerenciar_turmas'))

    cursor.execute('''
        SELECT t.id, t.nome, t.sala, t.turno, t.curso, t.capacidade_maxima,
               t.area_curricular, c.numero AS classe_num, ay.ano AS ano_lectivo,
               u.nome AS diretor_turma
        FROM turmas t
        JOIN classes c ON c.id = t.classe_id
        JOIN academic_years ay ON ay.id = t.ano_lectivo_id
        LEFT JOIN users u ON u.id = t.diretor_turma_id
        WHERE t.id = ?
    ''', (turma_id,))
    turma = cursor.fetchone()
    if not turma:
        flash('Turma não encontrada.', 'danger')
        return redirect(url_for('secretary.gerenciar_turmas'))

    busca = request.args.get('q', '').strip()
    cursor.execute('''
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN sexo = 'M' THEN 1 ELSE 0 END) AS masculino,
               SUM(CASE WHEN sexo = 'F' THEN 1 ELSE 0 END) AS feminino
        FROM students
        WHERE turma_id = ? AND ativo = 1
    ''', (turma_id,))
    totals = cursor.fetchone()
    total_alunos = totals['total'] or 0
    capacidade = turma['capacidade_maxima'] or 0

    cursor.execute('''
        SELECT s.id, s.code, s.nome, s.sexo, s.data_nascimento, s.foto_path,
               COALESCE(
                   NULLIF((
                       SELECT g.nome
                       FROM guardian_students gs
                       JOIN guardians g ON g.id = gs.guardian_id
                       WHERE gs.student_id = s.id AND g.ativo = 1
                       ORDER BY gs.principal DESC, gs.created_at, gs.guardian_id
                       LIMIT 1
                   ), ''),
                   NULLIF(TRIM(s.encarregado_nome), ''),
                   ''
               ) AS encarregado_nome,
               COALESCE(
                   NULLIF((
                       SELECT g.contacto
                       FROM guardian_students gs
                       JOIN guardians g ON g.id = gs.guardian_id
                       WHERE gs.student_id = s.id AND g.ativo = 1
                       ORDER BY gs.principal DESC, gs.created_at, gs.guardian_id
                       LIMIT 1
                   ), ''),
                   NULLIF(TRIM(s.encarregado_contacto_principal), ''),
                   NULLIF(TRIM(s.encarregado_contacto_1), ''),
                   ''
               ) AS contacto_encarregado,
               (SELECT dr.estado
                FROM student_deletion_requests dr
                WHERE dr.student_id = s.id AND dr.estado = 'Pendente'
                LIMIT 1) AS estado_pedido_eliminacao
        FROM students s
        WHERE s.turma_id = ? AND s.ativo = 1
          AND (? = '' OR s.nome LIKE ? OR s.code LIKE ?)
        ORDER BY s.nome ASC
    ''', (turma_id, busca, f'%{busca}%', f'%{busca}%'))
    alunos = cursor.fetchall()

    return render_template(
        'secretary/class_detail.html',
        turma=turma,
        alunos=alunos,
        busca=busca,
        total_alunos=total_alunos,
        capacidade=capacidade,
        vagas_disponiveis=max(capacidade - total_alunos, 0),
        ocupacao=round(total_alunos * 100 / capacidade) if capacidade else 0,
        total_masculino=totals['masculino'] or 0,
        total_feminino=totals['feminino'] or 0,
        areas_curriculares=AREAS_CURRICULARES,
        pode_ver_contactos=session.get('user_role') in ('secretary', 'chefe_secretaria', 'admin'),
    )


@secretary_bp.route('/students/<int:student_id>/deletion-request', methods=['POST'])
def request_student_deletion(student_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))

    motivo = request.form.get('motivo', '').strip()
    if len(motivo) > 1000:
        flash('O motivo não pode exceder 1000 caracteres.', 'danger')
        return redirect(url_for('secretary.students_list'))

    db = get_db()
    cursor = db.cursor()
    if not student_deletion_schema_ready(cursor):
        flash(
            'Os pedidos de eliminação ainda não estão disponíveis; é necessária uma migração.',
            'warning',
        )
        return redirect(url_for('secretary.students_list'))

    cursor.execute('''
        SELECT id, code, nome, turma_id
        FROM students
        WHERE id = ? AND ativo = 1
    ''', (student_id,))
    aluno = cursor.fetchone()
    if not aluno:
        flash('Aluno activo não encontrado.', 'danger')
        return redirect(url_for('secretary.students_list'))

    cursor.execute('''
        SELECT id FROM student_deletion_requests
        WHERE student_id = ? AND estado = 'Pendente'
    ''', (student_id,))
    if cursor.fetchone():
        flash('Já existe um pedido de eliminação pendente para este aluno.', 'info')
        return redirect(url_for('secretary.class_detail', turma_id=aluno['turma_id']))

    try:
        cursor.execute('''
            INSERT INTO student_deletion_requests (
                student_id, turma_id, solicitado_por, motivo
            )
            VALUES (?, ?, ?, ?)
        ''', (student_id, aluno['turma_id'], session['user_id'], motivo or None))
    except DB_INTEGRITY_ERRORS as error:
        db.rollback()
        if (
            getattr(error, 'pgcode', None) == '23505'
            or 'UNIQUE constraint failed' in str(error)
        ):
            flash('Já existe um pedido de eliminação pendente para este aluno.', 'info')
            return redirect(url_for('secretary.class_detail', turma_id=aluno['turma_id']))
        raise

    log_audit(
        session['user_id'],
        'SOLICITAR_ELIMINACAO_ALUNO',
        f"Pedido de eliminação do aluno {aluno['code']} ({aluno['nome']}) "
        f"na turma {aluno['turma_id']}; motivo: {motivo or 'Não indicado'}.",
    )
    flash('Pedido enviado à Direcção para aprovação.', 'success')
    return redirect(url_for('secretary.class_detail', turma_id=aluno['turma_id']))


@secretary_bp.route('/lista-nominal/<int:turma_id>')
def lista_nominal_view(turma_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT t.*, c.numero as classe_num, ay.ano as ano_lectivo
        FROM turmas t
        JOIN classes c ON t.classe_id = c.id
        JOIN academic_years ay ON t.ano_lectivo_id = ay.id
        WHERE t.id = ?
    ''', (turma_id,))
    turma = cursor.fetchone()
    if not turma:
        flash('Turma não encontrada.', 'danger')
        return redirect(url_for('secretary.students_list'))

    active_students = active_student_condition(cursor)
    cursor.execute(
        f'SELECT id, code, nome FROM students WHERE turma_id = ? AND {active_students} ORDER BY nome ASC',
        (turma_id,),
    )
    alunos = cursor.fetchall()
    return render_template('secretary/lista_nominal.html', turma=turma, alunos=alunos)

@secretary_bp.route('/pautas/exportar-csv/<int:turma_id>/<int:subject_id>')
def exportar_pauta_csv(turma_id, subject_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    active_students = active_student_condition(cursor, 's')
    cursor.execute('''
        SELECT s.code, s.nome, g.av1, g.av2, g.av3, g.av4, g.exame, g.faltas_justificadas, g.faltas_nao_justificadas
        FROM students s
        LEFT JOIN grades g ON s.id = g.student_id AND g.subject_id = ? AND g.turma_id = ?
        WHERE s.turma_id = ? AND {active_students} ORDER BY s.nome ASC
    '''.format(active_students=active_students), (subject_id, turma_id, turma_id))
    rows = cursor.fetchall()

    si = io.StringIO()
    writer = csv.writer(si, delimiter=';')
    writer.writerow(['Codigo_Aluno', 'Nome_Completo', 'AV1', 'AV2', 'AV3', 'AV4', 'Exame', 'Faltas_Justificadas', 'Faltas_Nao_Justificadas'])
    for r in rows:
        writer.writerow([r['code'], r['nome'], r['av1'] or 0, r['av2'] or 0, r['av3'] or 0, r['av4'] or 0, r['exame'] or '', r['faltas_justificadas'] or 0, r['faltas_nao_justificadas'] or 0])

    output = io.BytesIO(si.getvalue().encode('utf-8-sig'))
    return send_file(output, as_attachment=True, download_name=f'Pauta_Turma_{turma_id}_Disc_{subject_id}.csv', mimetype='text/csv')

@secretary_bp.route('/pautas/importar-csv', methods=['POST'])
def importar_pauta_csv():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    turma_id = int(request.form.get('turma_id'))
    subject_id = int(request.form.get('subject_id'))
    ficheiro = request.files.get('ficheiro_csv')
    if not ficheiro:
        flash('Selecione um ficheiro CSV válido.', 'danger')
        return redirect(url_for('secretary.gerenciar_turmas'))

    stream = io.StringIO(ficheiro.stream.read().decode('utf-8-sig'))
    reader = csv.DictReader(stream, delimiter=';')
    db = get_db()
    cursor = db.cursor()
    importados = 0
    active_student = active_student_condition(cursor)

    for row in reader:
        code = row.get('Codigo_Aluno', '').strip()
        cursor.execute(
            f'SELECT id FROM students WHERE code = ? AND turma_id = ? AND {active_student}',
            (code, turma_id),
        )
        st = cursor.fetchone()
        if not st:
            continue
        av1 = float(row.get('AV1') or 0)
        av2 = float(row.get('AV2') or 0)
        av3 = float(row.get('AV3') or 0)
        av4 = float(row.get('AV4') or 0)
        media_trim = (av1 + av2 + av3 + av4) / 4.0
        exame_raw = row.get('Exame', '').strip()
        exame = float(exame_raw) if exame_raw else None
        media_final = (media_trim * 0.6 + exame * 0.4) if exame is not None else media_trim
        fj = int(row.get('Faltas_Justificadas') or 0)
        fnj = int(row.get('Faltas_Nao_Justificadas') or 0)

        cursor.execute('SELECT id FROM grades WHERE student_id = ? AND subject_id = ? AND turma_id = ?', (st['id'], subject_id, turma_id))
        existe = cursor.fetchone()
        if existe:
            cursor.execute('''
                UPDATE grades SET av1=?, av2=?, av3=?, av4=?, media_trimestral=?, exame=?, media_final=?,
                faltas_justificadas=?, faltas_nao_justificadas=?, total_faltas=?, status='Submetida'
                WHERE id=?
            ''', (av1, av2, av3, av4, media_trim, exame, media_final, fj, fnj, fj + fnj, existe['id']))
        else:
            cursor.execute('''
                INSERT INTO grades (student_id, subject_id, turma_id, trimestre, av1, av2, av3, av4, media_trimestral, exame, media_final, faltas_justificadas, faltas_nao_justificadas, total_faltas, status)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Submetida')
            ''', (st['id'], subject_id, turma_id, av1, av2, av3, av4, media_trim, exame, media_final, fj, fnj, fj + fnj))
        importados += 1

    db.commit()
    log_audit(session['user_id'], 'IMPORTAR_PAUTA_CSV', f"Importados {importados} registos na Turma {turma_id}, Disciplina {subject_id}.")
    flash(f'Pauta oficial importada com sucesso! {importados} registos atualizados.', 'success')
    return redirect(url_for('secretary.gerenciar_turmas'))

@secretary_bp.route('/reclamacoes', methods=['GET', 'POST'])
def gerenciar_reclamacoes():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    
    if request.method == 'POST':
        action = request.form.get('action', 'reply')
        actor_id = session.get('user_id')
        if action == 'notifications_read' and actor_id:
            cursor.execute('''
                UPDATE complaint_notifications SET read_at = CURRENT_TIMESTAMP
                WHERE recipient_user_id = ? AND read_at IS NULL
            ''', (actor_id,))
            db.commit()
            return redirect(url_for('secretary.gerenciar_reclamacoes'))
        rec_id = request.form.get('rec_id', type=int)
        cursor.execute('''
            SELECT id, student_id, guardian_id, estado, mensagem,
                   nome_encarregado, contacto,
                   forwarded_to_director
            FROM reclamacoes_encarregados WHERE id = ?
        ''', (rec_id,))
        complaint = cursor.fetchone() if rec_id else None
        if not complaint:
            flash('A reclamação seleccionada não existe.', 'danger')
            return redirect(url_for('secretary.gerenciar_reclamacoes'))

        if action in ('reply', 'forward', 'status') and not actor_id:
            flash('A sessão não permite efectuar esta operação.', 'danger')
            return redirect(url_for('secretary.gerenciar_reclamacoes'))

        if action == 'reply':
            body = request.form.get('resposta_escola', '').strip()
            message_key = request.form.get('message_key', '').strip()
            if not body or len(body) > 5000:
                flash('Escreva uma resposta válida (até 5000 caracteres).', 'danger')
                return redirect(url_for('secretary.gerenciar_reclamacoes'))
            if not message_key:
                message_key = f'complaint:{rec_id}:{secrets.token_urlsafe(24)}'
            if (
                len(message_key) > 128
                or not message_key.startswith(f'complaint:{rec_id}:')
            ):
                flash('A chave da mensagem é inválida. Actualize a página e tente novamente.', 'danger')
                return redirect(url_for('secretary.gerenciar_reclamacoes'))
            message_id = add_message(
                cursor,
                complaint_id=rec_id,
                sender_type='secretary',
                user_id=actor_id,
                body=body,
                message_key=message_key,
            )
            old_state = complaint['estado']
            new_state = 'Respondida'
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET resposta_escola = ?, status = 'Respondida', estado = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (body, new_state, rec_id))
            if old_state != new_state:
                record_state_change(
                    cursor, complaint_id=rec_id, old_state=old_state,
                    new_state=new_state, actor_type='secretary',
                    user_id=actor_id,
                )
                notify_state_change(
                    cursor, complaint,
                    f'complaint:{rec_id}:state:{new_state}:{message_id}',
                )
            notify_new_message(
                cursor, complaint, message_id, 'secretary',
                f'complaint:{rec_id}:staff-message:{message_id}',
            )
            flash('Resposta adicionada à conversa.', 'success')
        elif action == 'status':
            new_state = request.form.get('estado', '').strip()
            if new_state not in COMPLAINT_STATES:
                flash('Seleccione um estado válido.', 'danger')
                return redirect(url_for('secretary.gerenciar_reclamacoes'))
            old_state = complaint['estado']
            legacy_status = 'Respondida' if new_state in ('Respondida', 'Resolvida') else 'Pendente'
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET estado = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (new_state, legacy_status, rec_id))
            if old_state != new_state:
                record_state_change(
                    cursor, complaint_id=rec_id, old_state=old_state,
                    new_state=new_state, actor_type='secretary',
                    user_id=actor_id,
                )
                notify_state_change(
                    cursor, complaint,
                    f'complaint:{rec_id}:state:{new_state}:{time.time_ns()}',
                )
            flash('Estado actualizado.', 'success')
        elif action == 'forward':
            reason = request.form.get('forward_reason', '').strip()
            if len(reason) > 1000:
                flash('O motivo de encaminhamento excede 1000 caracteres.', 'danger')
                return redirect(url_for('secretary.gerenciar_reclamacoes'))
            if complaint['forwarded_to_director']:
                flash('Esta reclamação já foi encaminhada à Direcção.', 'info')
                return redirect(url_for('secretary.gerenciar_reclamacoes'))
            old_state = complaint['estado']
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET forwarded_to_director = 1, forwarded_by = ?,
                    forwarded_at = CURRENT_TIMESTAMP, forward_reason = ?,
                    estado = 'Em Tratamento', assigned_to = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (actor_id, reason or None, actor_id, rec_id))
            record_state_change(
                cursor, complaint_id=rec_id, old_state=old_state,
                new_state='Em Tratamento', actor_type='secretary',
                user_id=actor_id, note=reason or 'Encaminhada à Direcção.',
            )
            notify_directors(
                cursor, rec_id,
                f'complaint:{rec_id}:forwarded',
            )
            flash('Reclamação encaminhada à Direcção.', 'success')
        else:
            flash('Acção desconhecida.', 'danger')
            return redirect(url_for('secretary.gerenciar_reclamacoes'))
        db.commit()
        return redirect(url_for('secretary.gerenciar_reclamacoes'))

    state_filter = request.args.get('estado', '').strip()
    category_filter = request.args.get('categoria', '').strip()
    search = request.args.get('q', '').strip()
    conditions = ['1 = 1']
    params = []
    if state_filter in COMPLAINT_STATES:
        conditions.append('r.estado = ?')
        params.append(state_filter)
    if category_filter:
        conditions.append('r.categoria = ?')
        params.append(category_filter)
    if search:
        conditions.append('''
            (s.nome LIKE ? OR s.code LIKE ? OR r.assunto LIKE ?
             OR r.nome_encarregado LIKE ?)
        ''')
        params.extend([f'%{search}%'] * 4)
    cursor.execute(f'''
        SELECT r.*, s.nome AS aluno_nome, s.code AS aluno_code
        FROM reclamacoes_encarregados r
        JOIN students s ON r.student_id = s.id
        WHERE {' AND '.join(conditions)}
        ORDER BY CASE r.estado WHEN 'Nova' THEN 0 WHEN 'Reaberta' THEN 1 ELSE 2 END,
                 r.updated_at DESC, r.id DESC
    ''', tuple(params))
    reclamacoes = cursor.fetchall()
    cursor.execute('''
        SELECT COUNT(*) AS total FROM reclamacoes_encarregados
        WHERE estado IN ('Nova', 'Reaberta')
    ''')
    complaint_new_count = cursor.fetchone()['total']
    cursor.execute('''
        SELECT COUNT(*) AS total FROM complaint_notifications
        WHERE recipient_user_id = ? AND read_at IS NULL
    ''', (session.get('user_id'),))
    complaint_unread_count = cursor.fetchone()['total']
    cursor.execute('''
        SELECT id, complaint_id, kind, created_at
        FROM complaint_notifications
        WHERE recipient_user_id = ? AND read_at IS NULL
        ORDER BY created_at DESC, id DESC
        LIMIT 20
    ''', (session.get('user_id'),))
    complaint_notifications = cursor.fetchall()
    if reclamacoes:
        ids = [row['id'] for row in reclamacoes]
        placeholders = ', '.join('?' for _ in ids)
        cursor.execute(f'''
            SELECT complaint_id, sender_type, body, created_at
            FROM complaint_messages
            WHERE complaint_id IN ({placeholders})
            ORDER BY created_at, id
        ''', tuple(ids))
        messages_by_complaint = {}
        for message in cursor.fetchall():
            messages_by_complaint.setdefault(message['complaint_id'], []).append(message)
        cursor.execute(f'''
            SELECT complaint_id, old_state, new_state, note, created_at
            FROM complaint_status_history
            WHERE complaint_id IN ({placeholders})
            ORDER BY created_at, id
        ''', tuple(ids))
        history_by_complaint = {}
        for event in cursor.fetchall():
            history_by_complaint.setdefault(event['complaint_id'], []).append(event)
        reclamacoes = [
            {
                **dict(row),
                'messages': messages_by_complaint.get(row['id'], []),
                'history': history_by_complaint.get(row['id'], []),
            }
            for row in reclamacoes
        ]
    template_context = {
        'reclamacoes': reclamacoes,
        'complaint_states': COMPLAINT_STATES,
        'complaint_categories': COMPLAINT_CATEGORIES,
        'complaint_notifications': complaint_notifications,
        'complaint_unread_count': complaint_unread_count,
        'complaint_new_count': complaint_new_count,
        'latest_notification_id': (
            complaint_notifications[0]['id'] if complaint_notifications else 0
        ),
        'complaint_message_keys': {
            row['id']: f"complaint:{row['id']}:{secrets.token_urlsafe(24)}"
            for row in reclamacoes
        },
        'selected_state': state_filter,
        'selected_category': category_filter,
        'search': search,
    }
    if request.args.get('_partial') == '1':
        return render_template('secretary/_complaint_cards.html', **template_context)
    return render_template('secretary/reclamacoes.html', **template_context)

"""Reset and seed the SIGE Neon development database with fictitious school data.

This command is intentionally guarded: it accepts only the confirmed Neon
development target, requires an explicit confirmation, and verifies a private
backup before opening a write transaction.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from werkzeug.security import generate_password_hash

from services.complaints import (
    add_message,
    notify_directors,
    notify_new_complaint,
    notify_new_message,
    notify_state_change,
    record_state_change,
)
from services.db import (
    create_standalone_connection,
    database_target_key,
    ensure_same_database_target,
    get_database_url,
)


EXPECTED_DATABASE = 'neondb'
EXPECTED_SCHEMA = 'public'
EXPECTED_YEAR = 2026
EXPECTED_CONFIRMATION = 'I_CONFIRM_DEVELOPMENT'

RESET_TABLES = (
    'complaint_notifications',
    'complaint_status_history',
    'complaint_messages',
    'reclamacoes_encarregados',
    'guardian_password_resets',
    'guardian_students',
    'student_deletion_requests',
    'attendance',
    'student_behavior',
    'enrollment_history',
    'grades',
    'lesson_summaries',
    'payments',
    'teacher_assignments',
    'auth_rate_limits',
    'audit_logs',
    'code_counters',
    'students',
    'guardians',
    'turmas',
    'users',
)

FIRST_NAMES = (
    'Ernesto', 'Ana', 'Celso', 'Maria', 'Júlia', 'Fernando', 'Teresa',
    'Carlos', 'Lídia', 'Armando', 'Marta', 'João', 'Rosa', 'António',
    'Filomena', 'Mateus', 'Elisa', 'Domingos', 'Helena', 'Bento',
)
MIDDLE_NAMES = (
    'Manuel', 'Paula', 'Alberto', 'Isabel', 'Armando', 'Domingos', 'João',
    'António', 'Amélia', 'Francisco', 'Celina', 'Tomás', 'Mário', 'Luísa',
    'Daniel',
)
SURNAMES = (
    'Mucavele', 'Nhantumbo', 'Macuácua', 'Chissano', 'Matsinhe',
    'Machava', 'Mavota', 'Machel', 'Mondlane', 'Cossa', 'Mussa',
    'Sitoe', 'Bila', 'Nhampossa', 'Jone', 'Mabunda', 'Muianga',
    'Cuamba', 'Massango', 'Langa', 'Tembe', 'Ndlovu', 'Magaia',
    'Nhamirre', 'Mbanze',
)
NATURALITIES = ('Beira', 'Dondo', 'Búzi', 'Nhamatanda', 'Manga')
PROFESSIONS = (
    'Agricultor(a)', 'Comerciante', 'Professor(a)', 'Enfermeiro(a)',
    'Carpinteiro(a)', 'Motorista', 'Funcionário(a)', 'Pescador(a)',
)

STAFF_ACCOUNTS = (
    ('admin', 'admin', 'DEMO-ADM-0001', 'admin@sige-demo.test', 'Administrador'),
    ('director', 'director', 'DEMO-DIR-0001', 'director@sige-demo.test', 'Director'),
    ('secretary', 'secretary', 'DEMO-SEC-0001', 'secretaria@sige-demo.test', 'Secretaria'),
    (
        'chief_secretary',
        'chefe_secretaria',
        'DEMO-CHS-0001',
        'chefe.secretaria@sige-demo.test',
        'Chefe da Secretaria',
    ),
    (
        'pedagogical_1',
        'pedagogico_1',
        'DEMO-PED1-0001',
        'pedagogico1@sige-demo.test',
        'Direcção Pedagógica 1',
    ),
    (
        'pedagogical_2',
        'pedagogico_2',
        'DEMO-PED2-0001',
        'pedagogico2@sige-demo.test',
        'Direcção Pedagógica 2',
    ),
    ('teacher_1', 'teacher', 'DEMO-PROF-0001', 'professor1@sige-demo.test', 'Professor 1'),
    ('teacher_2', 'teacher', 'DEMO-PROF-0002', 'professor2@sige-demo.test', 'Professor 2'),
    ('teacher_3', 'teacher', 'DEMO-PROF-0003', 'professor3@sige-demo.test', 'Professor 3'),
)

SUBJECT_TEACHERS = {
    'POR': 'teacher_1',
    'ING': 'teacher_1',
    'HIS': 'teacher_1',
    'GEO': 'teacher_1',
    'MAT': 'teacher_2',
    'BIO': 'teacher_2',
    'FIS': 'teacher_2',
    'QUI': 'teacher_2',
    'AGP': 'teacher_2',
    'EDV': 'teacher_3',
    'EDF': 'teacher_3',
}

SUBJECTS_BY_CLASS = {
    'general': ('POR', 'ING', 'HIS', 'GEO', 'BIO', 'MAT', 'QUI', 'EDV', 'EDF'),
    'A': ('POR', 'ING', 'HIS', 'GEO', 'EDF'),
    'B': ('POR', 'ING', 'BIO', 'FIS', 'MAT', 'QUI', 'EDF'),
    'C': ('POR', 'ING', 'GEO', 'EDV', 'MAT', 'EDF'),
}

GRADE_SUBJECTS_BY_CLASS = {
    'general': ('POR', 'MAT', 'BIO'),
    'A': ('POR', 'HIS', 'GEO'),
    'B': ('POR', 'MAT', 'BIO'),
    'C': ('POR', 'EDV', 'MAT'),
}

COMPLAINT_STATES = (
    'Nova',
    'Em Tratamento',
    'Respondida',
    'Resolvida',
    'Reaberta',
)
COMPLAINT_CATEGORIES = (
    'Notas e Avaliações',
    'Matrícula',
    'Assiduidade',
    'Pagamentos',
    'Documentação',
    'Atendimento',
    'Outros Assuntos',
)
COMPLAINT_SUBJECTS = (
    'Demonstração: esclarecimento sobre notas',
    'Demonstração: dúvida sobre matrícula',
    'Demonstração: consulta de assiduidade',
    'Demonstração: pedido de documento escolar',
    'Demonstração: esclarecimento sobre pagamentos',
)


def expected_turma_specs():
    specs = []
    for class_number in range(8, 13):
        for section_index, section in enumerate(('A', 'B', 'C')):
            area = section if class_number >= 10 else None
            name = (
                f'{class_number}.ª {section}01'
                if class_number >= 10
                else f'{class_number}.ª {section}'
            )
            capacity = 35 + ((class_number + section_index) % 3)
            specs.append({
                'class_number': class_number,
                'name': name,
                'area': area,
                'turno': 'Manhã' if (class_number + section_index) % 2 == 0 else 'Tarde',
                'curso': 'Diurno',
                'capacity': capacity,
                'vacancies': capacity,
                'room': f'Sala {class_number:02d}-{section}',
            })
    return specs


def student_identity(index):
    offset = index - 1
    first = FIRST_NAMES[offset % len(FIRST_NAMES)]
    middle = MIDDLE_NAMES[(offset // len(FIRST_NAMES)) % len(MIDDLE_NAMES)]
    surname_1 = SURNAMES[(offset * 3) % len(SURNAMES)]
    surname_2 = SURNAMES[((offset // len(FIRST_NAMES)) * 7 + 4) % len(SURNAMES)]
    return {
        'code': f'DEMO-ALU-{index:04d}',
        'name': f'{first} {middle} {surname_1} {surname_2}',
        'bi': f'DEMO-BI-2026-{index:04d}',
        'guardian_email': f'encarregado.{index:04d}@sige-demo.test',
        'contact': f'DEMO-CONTACTO-{index:04d}',
    }


def synthetic_name(index, offset=0):
    adjusted = index - 1
    first = FIRST_NAMES[(adjusted + offset * 3) % len(FIRST_NAMES)]
    middle = MIDDLE_NAMES[
        (adjusted // len(FIRST_NAMES) + offset * 5) % len(MIDDLE_NAMES)
    ]
    surname_1 = SURNAMES[(adjusted * 7 + offset * 3) % len(SURNAMES)]
    surname_2 = SURNAMES[
        (adjusted // len(FIRST_NAMES) * 11 + offset * 9 + 1) % len(SURNAMES)
    ]
    return f'{first} {middle} {surname_1} {surname_2}'


def validate_target_values(database, schema, hostname, env_confirmation, cli_confirmation):
    if env_confirmation != EXPECTED_CONFIRMATION:
        raise RuntimeError(
            'Defina SIGE_DEMO_RESET_CONFIRM=I_CONFIRM_DEVELOPMENT para confirmar o ambiente.'
        )
    if cli_confirmation != EXPECTED_DATABASE:
        raise RuntimeError(
            f'Use --confirm-development-database {EXPECTED_DATABASE} para confirmar o destino.'
        )
    if (
        database != EXPECTED_DATABASE
        or schema != EXPECTED_SCHEMA
        or not hostname
        or not hostname.lower().endswith('.neon.tech')
    ):
        raise RuntimeError(
            'Destino recusado: o comando só pode operar em neondb/public no Neon confirmado.'
        )


def target_fingerprint(database_url):
    values = database_target_key(database_url)
    payload = '\0'.join(str(value) for value in values).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def deletion_order(tables, relationships):
    table_set = set(tables)
    children = {table: set() for table in table_set}
    indegree = {table: 0 for table in table_set}
    for child, parent in relationships:
        if child in table_set and parent in table_set and child not in children[parent]:
            children[parent].add(child)
            indegree[child] += 1

    ready = sorted(table for table, count in indegree.items() if count == 0)
    parent_first = []
    while ready:
        parent = ready.pop(0)
        parent_first.append(parent)
        for child in sorted(children[parent]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()

    if len(parent_first) != len(table_set):
        raise RuntimeError('Não foi possível ordenar as relações para uma limpeza segura.')
    return list(reversed(parent_first))


def _private_file(path, description):
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file() or not stat.S_ISREG(resolved.stat().st_mode):
        raise RuntimeError(f'{description} não é um ficheiro regular existente.')
    if resolved.stat().st_mode & 0o077:
        raise RuntimeError(f'{description} não tem permissões privadas.')
    return resolved


def verify_backup(backup_path, manifest_path, database_url, repository_root):
    backup = _private_file(backup_path, 'O backup')
    manifest = _private_file(manifest_path, 'O manifesto do backup')
    repository = Path(repository_root).resolve()
    if os.path.commonpath((str(backup), str(repository))) == str(repository):
        raise RuntimeError('O backup está dentro do repositório; limpeza recusada.')
    if backup.parent != manifest.parent:
        raise RuntimeError('Backup e manifesto devem estar no mesmo directório privado.')
    if backup.stat().st_size == 0:
        raise RuntimeError('O backup está vazio.')

    try:
        metadata = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError('O manifesto do backup é inválido.') from error
    if (
        metadata.get('backup_file') != backup.name
        or metadata.get('target_fingerprint') != target_fingerprint(database_url)
        or metadata.get('sha256') != hashlib.sha256(backup.read_bytes()).hexdigest()
    ):
        raise RuntimeError('O backup não corresponde ao destino actual ou ao manifesto.')

    listing = subprocess.run(
        ['pg_restore', '--list', str(backup)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if listing.returncode or not listing.stdout.strip():
        raise RuntimeError('pg_restore não conseguiu validar o backup.')
    return backup


def build_staff_credentials():
    credentials = []
    for key, role, code, email, label in STAFF_ACCOUNTS:
        credentials.append({
            'key': key,
            'type': 'staff',
            'profile': role,
            'code': code,
            'name': f'{label} de Demonstração',
            'email': email,
            'password': secrets.token_urlsafe(24),
        })
    return credentials


def build_guardian_credential(index):
    identity = student_identity(index)
    return {
        'key': f'guardian_{index:04d}',
        'type': 'guardian',
        'profile': 'guardian',
        'student_code': identity['code'],
        'name': synthetic_name(index, offset=1),
        'email': identity['guardian_email'],
        'password': secrets.token_urlsafe(24),
    }


def build_demo_credentials():
    credentials = build_staff_credentials()
    credentials.extend(build_guardian_credential(index) for index in range(1, 301))
    passwords = [account['password'] for account in credentials]
    if len(set(passwords)) != len(credentials):
        raise RuntimeError('Foram geradas palavras-passe repetidas; tente novamente.')
    return credentials


def _existing_tables(cursor):
    cursor.execute('''
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
    ''')
    return {row['table_name'] for row in cursor.fetchall()}


def _row_counts(cursor, tables):
    counts = {}
    for table in RESET_TABLES:
        if table not in tables:
            continue
        if not re.fullmatch(r'[a-z_]+', table):
            raise RuntimeError('Nome de tabela não permitido.')
        cursor.execute(f'SELECT count(*) AS total FROM "{EXPECTED_SCHEMA}"."{table}"')
        counts[table] = cursor.fetchone()['total']
    return counts


def _foreign_keys(cursor):
    cursor.execute('''
        SELECT tc.table_name AS child_table, kcu.column_name AS child_column,
               ccu.table_name AS parent_table, ccu.column_name AS parent_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.constraint_schema = kcu.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.constraint_schema = tc.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.constraint_schema = current_schema()
        ORDER BY tc.table_name, kcu.column_name
    ''')
    return [
        (row['child_table'], row['child_column'], row['parent_table'], row['parent_column'])
        for row in cursor.fetchall()
    ]


def _validate_unmanaged_references(cursor, existing_tables, deleting_tables, foreign_keys):
    for child, child_column, parent, parent_column in foreign_keys:
        if child in deleting_tables or parent not in deleting_tables:
            continue
        if child not in existing_tables or parent not in existing_tables:
            continue
        cursor.execute(f'''
            SELECT count(*) AS total
            FROM "{EXPECTED_SCHEMA}"."{child}" child
            JOIN "{EXPECTED_SCHEMA}"."{parent}" parent
              ON child."{child_column}" = parent."{parent_column}"
        ''')
        if cursor.fetchone()['total']:
            raise RuntimeError(
                f'A tabela fora da limpeza {child} ainda referencia {parent}; '
                'não serão removidos registos nessa condição.'
            )


def _curriculum_for(spec):
    return SUBJECTS_BY_CLASS[spec['area'] or 'general']


def _grade_subjects_for(spec):
    return GRADE_SUBJECTS_BY_CLASS[spec['area'] or 'general']


def _create_staff(cursor, credentials):
    user_ids = {}
    for account in credentials:
        cursor.execute('''
            INSERT INTO users (code, nome, email, password_hash, role, contacto, ativo)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        ''', (
            account['code'],
            account['name'],
            account['email'],
            generate_password_hash(account['password']),
            account['profile'],
            f"DEMO-CONTACTO-FUNCIONARIO-{account['key']}",
        ))
        user_ids[account['key']] = cursor.lastrowid
    return user_ids


def _create_guardian(cursor, credential, contact):
    cursor.execute('''
        INSERT INTO guardians (nome, email, password_hash, contacto, ativo)
        VALUES (?, ?, ?, ?, 1)
    ''', (
        credential['name'],
        credential['email'],
        generate_password_hash(credential['password']),
        contact,
    ))
    return cursor.lastrowid


def _insert_student(
    cursor, index, spec, turma_id, year_id, secretary_id, guardian_id, guardian_name
):
    identity = student_identity(index)
    father_name = synthetic_name(index, offset=2)
    mother_name = synthetic_name(index, offset=3)
    contact = identity['contact']
    birth_year = EXPECTED_YEAR - spec['class_number'] - 6
    birth_month = (index % 12) + 1
    birth_day = (index % 27) + 1
    birth_date = f'{birth_year:04d}-{birth_month:02d}-{birth_day:02d}'
    naturality = NATURALITIES[(index - 1) % len(NATURALITIES)]
    parentesco = 'Mãe' if index % 2 else 'Pai'

    cursor.execute('''
        INSERT INTO students (
            code, nome, nome_pai, nome_mae, bi_cedula, data_nascimento,
            sexo, morada, naturalidade, provincia_nascimento, nacionalidade,
            pai_naturalidade, pai_profissao, pai_contacto,
            mae_naturalidade, mae_profissao, mae_contacto,
            encarregado_nome, encarregado_parentesco, encarregado_naturalidade,
            encarregado_profissao, encarregado_contacto_principal,
            encarregado_contacto_alternativo, encarregado_morada, encarregado_email,
            encarregado_contacto_1, encarregado_contacto_2,
            classe_ingresso, turma_id, ano_lectivo_id, turno, curso,
            data_matricula, isento_taxa, classe_sugerida, ingresso_situacao,
            funcionario_id, ativo
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'Interno', ?, 1
        )
    ''', (
        identity['code'],
        student_identity(index)['name'],
        father_name,
        mother_name,
        identity['bi'],
        birth_date,
        'F' if index % 2 else 'M',
        f'Morada de demonstração {index:04d}, Manga, Beira, Sofala',
        naturality,
        'Sofala',
        'Moçambicana',
        naturality,
        PROFESSIONS[index % len(PROFESSIONS)],
        f'DEMO-PAI-{index:04d}',
        naturality,
        PROFESSIONS[(index + 3) % len(PROFESSIONS)],
        f'DEMO-MAE-{index:04d}',
        guardian_name,
        parentesco,
        naturality,
        PROFESSIONS[(index + 5) % len(PROFESSIONS)],
        contact,
        None,
        f'Morada de demonstração {index:04d}, Manga, Beira, Sofala',
        identity['guardian_email'],
        contact,
        None,
        spec['class_number'],
        turma_id,
        year_id,
        spec['turno'],
        spec['curso'],
        '2026-01-15',
        spec['class_number'],
        secretary_id,
    ))
    student_id = cursor.lastrowid
    cursor.execute('''
        INSERT INTO guardian_students (guardian_id, student_id, parentesco, principal)
        VALUES (?, ?, ?, 1)
    ''', (guardian_id, student_id, parentesco))
    return student_id, identity


def _create_academic_data(cursor, students, specs_by_turma, year_id, user_ids, subjects):
    subject_owner = SUBJECT_TEACHERS
    grade_count = 0
    attendance_count = 0
    summary_count = 0
    payment_count = 0
    payments_for = {}

    for spec in specs_by_turma.values():
        curriculum = _curriculum_for(spec)
        for subject_code in curriculum:
            owner_key = subject_owner[subject_code]
            cursor.execute('''
                INSERT INTO teacher_assignments (teacher_id, turma_id, subject_id)
                VALUES (?, ?, ?)
            ''', (
                user_ids[owner_key],
                spec['id'],
                subjects[subject_code],
            ))

        for subject_code in _grade_subjects_for(spec):
            subject_id = subjects[subject_code]
            teacher_id = user_ids[subject_owner[subject_code]]
            cursor.execute('''
                INSERT INTO lesson_summaries (
                    teacher_id, turma_id, subject_id, numero_aula,
                    data_aula, sumario, observacoes
                ) VALUES (?, ?, ?, 1, ?, ?, ?)
            ''', (
                teacher_id,
                spec['id'],
                subject_id,
                '2026-03-10',
                f'DEMONSTRAÇÃO: revisão de conteúdos de {subject_code}.',
                'Registo fictício; não corresponde a uma aula real.',
            ))
            summary_count += 1

    for student in students:
        index = student['number']
        spec = student['spec']
        attendance_type = 'Presença'
        if index % 7 == 0:
            attendance_type = 'Justificada' if index % 9 == 0 else 'Falta'
        cursor.execute('''
            INSERT INTO attendance (student_id, turma_id, subject_id, data_aula, tipo, motivo)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            student['id'],
            student['turma_id'],
            subjects[_grade_subjects_for(spec)[0]],
            '2026-03-10',
            attendance_type,
            'Demonstração: motivo fictício.' if index % 7 == 0 else None,
        ))
        attendance_count += 1

        for term in (1, 2, 3):
            for subject_offset, subject_code in enumerate(_grade_subjects_for(spec)):
                scores = [
                    float(8 + ((index * 3 + term * 5 + subject_offset * 2 + part * 3) % 13))
                    for part in range(4)
                ]
                average = sum(scores) / len(scores)
                exam = (
                    float(12 + ((index + term + subject_offset) % 9))
                    if (index + term + subject_offset) % 7 == 0
                    else None
                )
                final = (
                    (average * 0.6) + (exam * 0.4)
                    if exam is not None
                    else average
                )
                status = (
                    'Submetida'
                    if term < 3 and (index + subject_offset) % 2 == 0
                    else 'Rascunho'
                )
                cursor.execute('''
                    INSERT INTO grades (
                        student_id, subject_id, turma_id, trimestre,
                        av1, av2, av3, av4, media_trimestral, exame,
                        media_final, faltas_justificadas,
                        faltas_nao_justificadas, total_faltas,
                        comentario_professor, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    student['id'],
                    subjects[subject_code],
                    student['turma_id'],
                    term,
                    *scores,
                    average,
                    exam,
                    final,
                    0 if index % 4 else 1,
                    1 if index % 11 == 0 else 0,
                    1 if index % 11 == 0 else (0 if index % 4 else 1),
                    'Demonstração: avaliação fictícia.',
                    status,
                ))
                grade_count += 1

        if index % 5 == 0:
            payment_count += 1
            receipt = f'DEMO-REC-{payment_count:04d}'
            cursor.execute('''
                INSERT INTO payments (
                    receipt_number, student_id, mes_pago, ano_lectivo_id,
                    valor_pago, banco_plataforma, referencia_talao,
                    data_deposito, data_registo, funcionario_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                receipt,
                student['id'],
                ('Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho')[
                    (index // 5) % 6
                ],
                year_id,
                float(250 + (index % 6) * 100),
                'DEMONSTRAÇÃO — sem transacção',
                f'DEMO-PAG-{payment_count:04d}',
                '2026-03-10',
                '2026-03-10',
                user_ids['secretary'],
            ))
            payments_for[student['id']] = cursor.lastrowid

    for spec in specs_by_turma.values():
        cursor.execute(
            'UPDATE turmas SET vagas_disponiveis = capacidade_maxima - 20 WHERE id = ?',
            (spec['id'],),
        )

    return {
        'grades': grade_count,
        'attendance': attendance_count,
        'summaries': summary_count,
        'payments': payment_count,
        'payments_for': payments_for,
    }


def _change_complaint_state(cursor, complaint_id, complaint, new_state, user_id, note):
    old_state = complaint['estado']
    legacy_status = 'Respondida' if new_state in ('Respondida', 'Resolvida') else 'Pendente'
    cursor.execute('''
        UPDATE reclamacoes_encarregados
        SET estado = ?, status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (new_state, legacy_status, complaint_id))
    if old_state != new_state:
        record_state_change(
            cursor,
            complaint_id=complaint_id,
            old_state=old_state,
            new_state=new_state,
            actor_type='secretary',
            user_id=user_id,
            note=note,
        )
        notify_state_change(
            cursor,
            complaint,
            f'demo:complaint:{complaint_id}:state:{new_state}',
        )
    complaint['estado'] = new_state


def _seed_complaints(cursor, students, user_ids):
    created = []
    secretary_id = user_ids['secretary']
    director_id = user_ids['director']
    status_groups = {
        'Nova': (0, 1, 2),
        'Em Tratamento': (3, 4, 5),
        'Respondida': (6, 7, 8),
        'Resolvida': (9, 10, 11),
        'Reaberta': (12, 13, 14),
    }
    forwarded_ids = set(status_groups['Respondida'])

    for desired_state, student_indexes in status_groups.items():
        for student_index in student_indexes:
            student = students[student_index]
            number = student['number']
            category = COMPLAINT_CATEGORIES[(number - 1) % len(COMPLAINT_CATEGORIES)]
            subject = COMPLAINT_SUBJECTS[(number - 1) % len(COMPLAINT_SUBJECTS)]
            body = (
                f'DEMONSTRAÇÃO: solicito esclarecimento fictício relacionado com '
                f'{category.lower()}.'
            )
            cursor.execute('''
                INSERT INTO reclamacoes_encarregados (
                    student_id, guardian_id, nome_encarregado, contacto,
                    mensagem, status, resposta_escola, categoria, assunto,
                    estado, updated_at, assigned_to, forwarded_to_director,
                    submission_key
                ) VALUES (?, ?, ?, ?, ?, 'Pendente', '', ?, ?, 'Nova',
                          CURRENT_TIMESTAMP, ?, 0, ?)
            ''', (
                student['id'],
                student['guardian_id'],
                student['guardian_name'],
                student['contact'],
                body,
                category,
                subject,
                secretary_id,
                f'demo-submission-{number:04d}',
            ))
            complaint_id = cursor.lastrowid
            complaint = {
                'id': complaint_id,
                'student_id': student['id'],
                'guardian_id': student['guardian_id'],
                'estado': 'Nova',
                'nome_encarregado': student['guardian_name'],
                'contacto': student['contact'],
            }
            initial_message_id = add_message(
                cursor,
                complaint_id=complaint_id,
                sender_type='guardian',
                guardian_id=student['guardian_id'],
                body=body,
                message_key=f'demo:complaint:{complaint_id}:initial',
            )
            record_state_change(
                cursor,
                complaint_id=complaint_id,
                old_state=None,
                new_state='Nova',
                actor_type='guardian',
                guardian_id=student['guardian_id'],
                note='Mensagem inicial fictícia de demonstração.',
            )
            notify_new_complaint(cursor, complaint_id, initial_message_id)
            created.append((complaint_id, complaint, desired_state, student_index))

    for complaint_id, complaint, desired_state, student_index in created:
        student = students[student_index]
        if desired_state == 'Nova':
            continue
        if desired_state == 'Em Tratamento':
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET estado = 'Em Tratamento', assigned_to = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (secretary_id, complaint_id))
            record_state_change(
                cursor,
                complaint_id=complaint_id,
                old_state='Nova',
                new_state='Em Tratamento',
                actor_type='secretary',
                user_id=secretary_id,
                note='Demonstração: reclamação em acompanhamento.',
            )
            complaint['estado'] = 'Em Tratamento'
            continue

        secretary_message = (
            'Demonstração: a Secretaria analisou o pedido fictício.'
        )
        secretary_message_id = add_message(
            cursor,
            complaint_id=complaint_id,
            sender_type='secretary',
            user_id=secretary_id,
            body=secretary_message,
            message_key=f'demo:complaint:{complaint_id}:secretary',
        )
        cursor.execute('''
            UPDATE reclamacoes_encarregados
            SET resposta_escola = ?, status = 'Respondida', estado = 'Respondida',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (secretary_message, complaint_id))
        record_state_change(
            cursor,
            complaint_id=complaint_id,
            old_state='Nova',
            new_state='Respondida',
            actor_type='secretary',
            user_id=secretary_id,
            note='Resposta fictícia da Secretaria.',
        )
        notify_state_change(
            cursor,
            complaint,
            f'demo:complaint:{complaint_id}:secretary-state',
        )
        notify_new_message(
            cursor,
            complaint,
            secretary_message_id,
            'secretary',
            f'demo:complaint:{complaint_id}:secretary-message',
        )
        complaint['estado'] = 'Respondida'

        if desired_state == 'Resolvida':
            _change_complaint_state(
                cursor,
                complaint_id,
                complaint,
                'Resolvida',
                secretary_id,
                'Demonstração: pedido fictício resolvido.',
            )
        elif desired_state == 'Reaberta':
            followup = 'Demonstração: pedido fictício reaberto pelo encarregado.'
            followup_id = add_message(
                cursor,
                complaint_id=complaint_id,
                sender_type='guardian',
                guardian_id=student['guardian_id'],
                body=followup,
                message_key=f'demo:complaint:{complaint_id}:guardian-followup',
            )
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET estado = 'Reaberta', status = 'Pendente',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (complaint_id,))
            record_state_change(
                cursor,
                complaint_id=complaint_id,
                old_state='Respondida',
                new_state='Reaberta',
                actor_type='guardian',
                guardian_id=student['guardian_id'],
                note='Demonstração: nova mensagem fictícia do encarregado.',
            )
            notify_new_message(
                cursor,
                complaint,
                followup_id,
                'guardian',
                f'demo:complaint:{complaint_id}:guardian-message',
            )
            complaint['estado'] = 'Reaberta'

        if student_index in forwarded_ids:
            old_state = complaint['estado']
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET forwarded_to_director = 1, forwarded_by = ?,
                    forwarded_at = CURRENT_TIMESTAMP,
                    forward_reason = ?, estado = 'Em Tratamento',
                    assigned_to = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                secretary_id,
                'Demonstração: encaminhamento fictício à Direcção.',
                secretary_id,
                complaint_id,
            ))
            record_state_change(
                cursor,
                complaint_id=complaint_id,
                old_state=old_state,
                new_state='Em Tratamento',
                actor_type='secretary',
                user_id=secretary_id,
                note='Demonstração: encaminhada à Direcção.',
            )
            notify_directors(
                cursor,
                complaint_id,
                f'demo:complaint:{complaint_id}:forwarded',
            )
            complaint['estado'] = 'Em Tratamento'

            director_message = 'Demonstração: a Direcção analisou o pedido fictício.'
            director_message_id = add_message(
                cursor,
                complaint_id=complaint_id,
                sender_type='director',
                user_id=director_id,
                body=director_message,
                message_key=f'demo:complaint:{complaint_id}:director',
            )
            cursor.execute('''
                UPDATE reclamacoes_encarregados
                SET resposta_escola = ?, status = 'Respondida',
                    estado = 'Respondida', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (director_message, complaint_id))
            record_state_change(
                cursor,
                complaint_id=complaint_id,
                old_state='Em Tratamento',
                new_state='Respondida',
                actor_type='director',
                user_id=director_id,
                note='Demonstração: resposta fictícia da Direcção.',
            )
            notify_state_change(
                cursor,
                complaint,
                f'demo:complaint:{complaint_id}:director-state',
            )
            notify_new_message(
                cursor,
                complaint,
                director_message_id,
                'director',
                f'demo:complaint:{complaint_id}:director-message',
            )
            complaint['estado'] = 'Respondida'


def _write_credentials_temp(path, credentials):
    output = Path(path).expanduser().resolve()
    repository = Path.cwd().resolve()
    if os.path.commonpath((str(output), str(repository))) == str(repository):
        raise RuntimeError('O ficheiro de credenciais deve ficar fora do repositório.')
    parent = output.parent
    if not parent.is_dir() or parent.stat().st_mode & 0o077:
        raise RuntimeError('O directório de credenciais deve existir e ter permissões privadas.')
    if output.exists():
        raise RuntimeError('O ficheiro de credenciais já existe; não será sobrescrito.')

    fd, temp_name = tempfile.mkstemp(prefix=f'.{output.name}.', dir=parent)
    temp = Path(temp_name)
    os.chmod(temp, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({
                'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'warning': 'Contas temporárias fictícias para desenvolvimento.',
                'accounts': [
                    {
                        key: value
                        for key, value in account.items()
                        if key in ('type', 'profile', 'student_code', 'name', 'email', 'password')
                    }
                    for account in credentials
                ],
            }, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return temp, output
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _validate_core_configuration(cursor):
    cursor.execute('''
        SELECT id, numero FROM classes
        WHERE numero BETWEEN 8 AND 12
        ORDER BY numero, id
    ''')
    classes = cursor.fetchall()
    class_ids = {}
    for row in classes:
        if row['numero'] in class_ids:
            raise RuntimeError(f'Existe mais de uma configuração para a classe {row["numero"]}.')
        class_ids[row['numero']] = row['id']
    if set(class_ids) != set(range(8, 13)):
        raise RuntimeError('As classes 8.ª a 12.ª não estão configuradas.')

    cursor.execute('''
        SELECT id FROM academic_years WHERE ano = ? AND ativo = 1
    ''', (EXPECTED_YEAR,))
    years = cursor.fetchall()
    if len(years) != 1:
        raise RuntimeError('É necessário exactamente um ano lectivo activo de 2026.')

    cursor.execute('SELECT id, codigo FROM subjects')
    subject_rows = cursor.fetchall()
    subjects = {row['codigo']: row['id'] for row in subject_rows}
    required_subjects = set(SUBJECT_TEACHERS)
    if not required_subjects.issubset(subjects):
        missing = ', '.join(sorted(required_subjects - set(subjects)))
        raise RuntimeError(f'Disciplinas curriculares em falta: {missing}.')
    return class_ids, years[0]['id'], subjects


def _preflight(cursor):
    existing = _existing_tables(cursor)
    required = {
        'users', 'students', 'guardians', 'guardian_students', 'classes',
        'academic_years', 'subjects', 'turmas', 'teacher_assignments',
        'grades', 'lesson_summaries', 'attendance', 'payments',
        'reclamacoes_encarregados', 'complaint_messages',
        'complaint_status_history', 'complaint_notifications',
    }
    missing = required - existing
    if missing:
        raise RuntimeError(
            'Esquema incompleto; migração/initialização não será executada automaticamente: '
            + ', '.join(sorted(missing))
        )
    tables = set(RESET_TABLES) & existing
    foreign_keys = _foreign_keys(cursor)
    _validate_unmanaged_references(cursor, existing, tables, foreign_keys)
    counts = _row_counts(cursor, tables)
    class_ids, year_id, subjects = _validate_core_configuration(cursor)
    return existing, tables, foreign_keys, counts, class_ids, year_id, subjects


def _insert_school_data(cursor, class_ids, year_id, subjects, credentials):
    staff_credentials = [row for row in credentials if row['type'] == 'staff']
    guardian_credentials = {
        row['key']: row for row in credentials if row['type'] == 'guardian'
    }
    user_ids = _create_staff(cursor, staff_credentials)
    specs = expected_turma_specs()
    specs_by_turma = {}

    for spec in specs:
        cursor.execute('''
            INSERT INTO turmas (
                nome, sala, classe_id, ano_lectivo_id, turno, curso,
                capacidade_maxima, vagas_disponiveis, area_curricular
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            spec['name'],
            spec['room'],
            class_ids[spec['class_number']],
            year_id,
            spec['turno'],
            spec['curso'],
            spec['capacity'],
            spec['vacancies'],
            spec['area'],
        ))
        spec['id'] = cursor.lastrowid
        specs_by_turma[spec['id']] = spec

    all_students = []
    student_number = 0
    for spec in specs:
        for _student_in_class in range(20):
            student_number += 1
            credential = guardian_credentials[f'guardian_{student_number:04d}']
            identity = student_identity(student_number)
            guardian_id = _create_guardian(cursor, credential, identity['contact'])
            student_id, identity = _insert_student(
                cursor,
                student_number,
                spec,
                spec['id'],
                year_id,
                user_ids['secretary'],
                guardian_id,
                credential['name'],
            )
            student_record = {
                'id': student_id,
                'number': student_number,
                'code': identity['code'],
                'turma_id': spec['id'],
                'spec': spec,
                'guardian_id': guardian_id,
                'guardian_name': credential['name'],
                'contact': identity['contact'],
            }
            all_students.append(student_record)

    academic = _create_academic_data(
        cursor,
        all_students,
        specs_by_turma,
        year_id,
        user_ids,
        subjects,
    )
    _seed_complaints(cursor, all_students, user_ids)
    _verify_seed(cursor, specs, all_students, academic)
    cursor.execute('''
        INSERT INTO audit_logs (user_id, operacao, detalhes, ip_address)
        VALUES (?, ?, ?, ?)
    ''', (
        user_ids['admin'],
        'PREPARAR_DEMONSTRACAO',
        'Criados dados escolares fictícios para validação do SIGE Neon.',
        '127.0.0.1',
    ))
    return {
        'staff': len(staff_credentials),
        'guardians': len(guardian_credentials),
        'students': len(all_students),
        'classes': len(class_ids),
        'turmas': len(specs),
        'grades': academic['grades'],
        'attendance': academic['attendance'],
        'summaries': academic['summaries'],
        'payments': academic['payments'],
        'complaints': 15,
    }


def _verify_seed(cursor, specs, students, academic):
    expected = {
        'users': len(STAFF_ACCOUNTS),
        'guardians': 300,
        'students': 300,
        'turmas': 15,
        'teacher_assignments': 108,
        'grades': 2700,
        'attendance': 300,
        'lesson_summaries': 45,
        'payments': 60,
        'reclamacoes_encarregados': 15,
        'complaint_messages': 30,
    }
    for table, expected_count in expected.items():
        cursor.execute(f'SELECT count(*) AS total FROM "{EXPECTED_SCHEMA}"."{table}"')
        actual = cursor.fetchone()['total']
        if actual != expected_count:
            raise RuntimeError(
                f'Validação da demonstração falhou para {table}: '
                f'esperado {expected_count}, encontrado {actual}.'
            )

    cursor.execute('''
        SELECT t.id, t.nome, t.capacidade_maxima, t.vagas_disponiveis,
               count(s.id) FILTER (WHERE s.ativo = 1) AS active_students
        FROM turmas t
        LEFT JOIN students s ON s.turma_id = t.id
        GROUP BY t.id, t.nome, t.capacidade_maxima, t.vagas_disponiveis
        ORDER BY t.nome
    ''')
    turma_rows = cursor.fetchall()
    if len(turma_rows) != 15:
        raise RuntimeError('A validação encontrou turmas duplicadas ou em falta.')
    for row in turma_rows:
        if row['active_students'] != 20:
            raise RuntimeError(f'A turma {row["nome"]} não contém exactamente 20 alunos activos.')
        if row['active_students'] > row['capacidade_maxima']:
            raise RuntimeError(f'A capacidade da turma {row["nome"]} foi excedida.')
        if row['vagas_disponiveis'] != row['capacidade_maxima'] - 20:
            raise RuntimeError(f'As vagas da turma {row["nome"]} não correspondem à lotação.')

    cursor.execute('''
        SELECT count(*) AS total
        FROM guardian_students gs
        JOIN students s ON s.id = gs.student_id
        WHERE gs.principal = 1
    ''')
    if cursor.fetchone()['total'] != 300:
        raise RuntimeError('Nem todos os educandos têm um encarregado principal.')

    cursor.execute('''
        SELECT count(*) AS total
        FROM reclamacoes_encarregados r
        JOIN guardian_students gs
          ON gs.guardian_id = r.guardian_id AND gs.student_id = r.student_id
        WHERE r.guardian_id IS NOT NULL
    ''')
    if cursor.fetchone()['total'] != 15:
        raise RuntimeError('As reclamações de demonstração não têm associações válidas.')

    cursor.execute('''
        SELECT estado, count(*) AS total
        FROM reclamacoes_encarregados GROUP BY estado
    ''')
    states = {row['estado']: row['total'] for row in cursor.fetchall()}
    if any(states.get(state) != 3 for state in COMPLAINT_STATES):
        raise RuntimeError('As reclamações não cobrem os cinco estados de demonstração.')

    cursor.execute('''
        SELECT count(*) AS total FROM complaint_notifications
        WHERE recipient_guardian_id IS NOT NULL OR recipient_user_id IS NOT NULL
    ''')
    if cursor.fetchone()['total'] == 0:
        raise RuntimeError('As notificações da demonstração não foram criadas.')


def run(args):
    runtime_url = get_database_url()
    ddl_url = get_database_url(prefer_unpooled=True) or runtime_url
    ensure_same_database_target(runtime_url, ddl_url)
    hostname = urlparse(ddl_url).hostname or ''
    connection = create_standalone_connection(ddl_url)
    cursor = connection.cursor()
    temp_credentials = None
    final_credentials = None
    published_credentials = False
    committed = False
    try:
        cursor.execute('''
            SELECT current_database() AS database, current_schema() AS schema,
                   current_setting('server_version_num') AS version
        ''')
        target = cursor.fetchone()
        validate_target_values(
            target['database'],
            target['schema'],
            hostname,
            os.getenv('SIGE_DEMO_RESET_CONFIRM', ''),
            args.confirm_development_database,
        )
        if not ddl_url.startswith(('postgres://', 'postgresql://')):
            raise RuntimeError('O gerador de demonstração só opera sobre PostgreSQL.')

        existing, tables, foreign_keys, counts, class_ids, year_id, subjects = _preflight(cursor)
        deletion = deletion_order(tables, [
            (child, parent) for child, _child_column, parent, _parent_column in foreign_keys
        ])
        print(
            f'Destino validado: {target["database"]}/{target["schema"]}; '
            f'PostgreSQL {int(target["version"]) // 10000}.'
        )
        print('Plano de limpeza:', ', '.join(
            f'{table}={counts.get(table, 0)}'
            for table in deletion
        ))
        print(
            'Plano de demonstração: 9 contas de funcionários, 300 encarregados, '
            '15 turmas e 300 alunos; sem mensagens externas.'
        )
        if not args.apply:
            print('Pré-visualização apenas; nenhum dado foi alterado.')
            return
        if not args.backup or not args.backup_manifest or not args.credentials_output:
            raise RuntimeError(
                '--apply requer --backup, --backup-manifest e --credentials-output.'
            )
        verify_backup(
            args.backup,
            args.backup_manifest,
            ddl_url,
            Path.cwd(),
        )
        credentials = build_demo_credentials()
        temp_credentials, final_credentials = _write_credentials_temp(
            args.credentials_output,
            credentials,
        )

        cursor.execute("SET LOCAL lock_timeout = '15s'")
        cursor.execute("SET LOCAL statement_timeout = '5min'")
        for table in sorted(tables):
            cursor.execute(f'LOCK TABLE "{EXPECTED_SCHEMA}"."{table}" IN ACCESS EXCLUSIVE MODE')
        # Recheck the target after acquiring locks, then delete only the explicit allowlist.
        cursor.execute('SELECT current_database() AS database, current_schema() AS schema')
        locked_target = cursor.fetchone()
        if locked_target['database'] != EXPECTED_DATABASE or locked_target['schema'] != EXPECTED_SCHEMA:
            raise RuntimeError('O destino mudou durante a preparação; transacção recusada.')
        _validate_unmanaged_references(cursor, existing, tables, foreign_keys)
        for table in deletion:
            cursor.execute(f'DELETE FROM "{EXPECTED_SCHEMA}"."{table}"')
        for table in tables:
            cursor.execute(f'SELECT count(*) AS total FROM "{EXPECTED_SCHEMA}"."{table}"')
            if cursor.fetchone()['total'] != 0:
                raise RuntimeError(f'A limpeza transaccional não esvaziou {table}.')

        result = _insert_school_data(
            cursor,
            class_ids,
            year_id,
            subjects,
            credentials,
        )
        if len(credentials) != len(STAFF_ACCOUNTS) + 300:
            raise RuntimeError('A lista privada de credenciais está incompleta.')
        os.replace(temp_credentials, final_credentials)
        published_credentials = True
        connection.commit()
        committed = True
        temp_credentials = None
        print(
            'Demonstração criada e validada: '
            + ', '.join(f'{key}={value}' for key, value in result.items())
        )
        print(f'Credenciais temporárias guardadas em ficheiro privado: {final_credentials}')
    except Exception:
        connection.rollback()
        if temp_credentials is not None:
            temp_credentials.unlink(missing_ok=True)
        if published_credentials and not committed and final_credentials is not None:
            final_credentials.unlink(missing_ok=True)
        raise
    finally:
        cursor.close()
        connection.close()


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Preparação dos dados fictícios SIGE Neon. '
            'EXCLUSIVO para neondb/public de desenvolvimento; por omissão é apenas pré-visualização.'
        )
    )
    parser.add_argument('--apply', action='store_true', help='Executa a limpeza e a criação dentro de uma transacção.')
    parser.add_argument(
        '--confirm-development-database',
        default='',
        help=f'Confirma explicitamente o destino {EXPECTED_DATABASE}.',
    )
    parser.add_argument('--backup', help='Caminho do backup completo privado previamente validado.')
    parser.add_argument('--backup-manifest', help='Manifesto privado que vincula backup e destino.')
    parser.add_argument('--credentials-output', help='Caminho privado, fora do repositório, para as credenciais.')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()

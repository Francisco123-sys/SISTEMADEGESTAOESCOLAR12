COMPLAINT_CATEGORIES = (
    'Matrícula',
    'Notas e Avaliações',
    'Assiduidade',
    'Pagamentos',
    'Documentação',
    'Comportamento',
    'Atendimento',
    'Outros Assuntos',
)

COMPLAINT_STATES = (
    'Nova',
    'Em Tratamento',
    'Respondida',
    'Resolvida',
    'Reaberta',
)

STAFF_ROLES = ('secretary', 'chefe_secretaria', 'admin')
DIRECTOR_ROLES = ('director', 'admin')


def _insert_notification(
    cursor,
    *,
    event_key,
    complaint_id,
    message_id,
    kind,
    guardian_id=None,
    user_id=None,
):
    if (guardian_id is None) == (user_id is None):
        raise ValueError('A notificação deve ter exactamente um destinatário.')
    cursor.execute('''
        INSERT INTO complaint_notifications (
            event_key, complaint_id, message_id, recipient_guardian_id,
            recipient_user_id, kind
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_key) DO NOTHING
    ''', (
        event_key, complaint_id, message_id, guardian_id, user_id, kind,
    ))


def notify_users(cursor, complaint_id, message_id, kind, roles, event_prefix):
    placeholders = ', '.join('?' for _ in roles)
    cursor.execute(
        f'''SELECT id FROM users
            WHERE ativo = 1 AND role IN ({placeholders})''',
        tuple(roles),
    )
    for row in cursor.fetchall():
        user_id = row['id']
        _insert_notification(
            cursor,
            event_key=f'{event_prefix}:user:{user_id}',
            complaint_id=complaint_id,
            message_id=message_id,
            kind=kind,
            user_id=user_id,
        )


def notify_guardian(cursor, complaint, message_id, kind, event_prefix):
    guardian_ids = []
    if complaint['guardian_id'] is not None:
        guardian_ids.append(complaint['guardian_id'])
    else:
        complaint_keys = complaint.keys()
        guardian_name = (
            complaint['nome_encarregado']
            if 'nome_encarregado' in complaint_keys else None
        )
        guardian_contact = (
            complaint['contacto']
            if 'contacto' in complaint_keys else None
        )
        cursor.execute('''
            SELECT gs.guardian_id
            FROM guardian_students gs
            JOIN guardians g ON g.id = gs.guardian_id
            WHERE gs.student_id = ? AND g.ativo = 1
              AND LOWER(TRIM(g.nome)) = LOWER(TRIM(?))
              AND COALESCE(TRIM(g.contacto), '') = COALESCE(TRIM(?), '')
        ''', (
            complaint['student_id'],
            guardian_name,
            guardian_contact,
        ))
        guardian_ids.extend(row['guardian_id'] for row in cursor.fetchall())

    for guardian_id in dict.fromkeys(guardian_ids):
        _insert_notification(
            cursor,
            event_key=f'{event_prefix}:guardian:{guardian_id}',
            complaint_id=complaint['id'],
            message_id=message_id,
            kind=kind,
            guardian_id=guardian_id,
        )


def add_message(
    cursor,
    *,
    complaint_id,
    sender_type,
    body,
    guardian_id=None,
    user_id=None,
    message_key=None,
):
    if sender_type not in ('guardian', 'secretary', 'director'):
        raise ValueError('Tipo de remetente inválido.')
    is_guardian = sender_type == 'guardian'
    if is_guardian != (guardian_id is not None):
        raise ValueError('O remetente encarregado deve corresponder à sessão.')
    if is_guardian == (user_id is not None):
        raise ValueError('O remetente funcionário deve corresponder à sessão.')

    if message_key:
        cursor.execute('''
            INSERT INTO complaint_messages (
                complaint_id, sender_type, sender_guardian_id,
                sender_user_id, message_key, body
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_key) DO NOTHING
        ''', (
            complaint_id, sender_type, guardian_id, user_id, message_key, body,
        ))
        cursor.execute(
            'SELECT id, complaint_id FROM complaint_messages WHERE message_key = ?',
            (message_key,),
        )
        row = cursor.fetchone()
        if not row:
            raise RuntimeError('Não foi possível registar a mensagem da reclamação.')
        if row['complaint_id'] != complaint_id:
            raise ValueError('A chave da mensagem pertence a outra reclamação.')
        return row['id']

    cursor.execute('''
        INSERT INTO complaint_messages (
            complaint_id, sender_type, sender_guardian_id,
            sender_user_id, body
        ) VALUES (?, ?, ?, ?, ?)
    ''', (complaint_id, sender_type, guardian_id, user_id, body))
    return cursor.lastrowid


def record_state_change(
    cursor,
    *,
    complaint_id,
    old_state,
    new_state,
    actor_type,
    note=None,
    guardian_id=None,
    user_id=None,
):
    cursor.execute('''
        INSERT INTO complaint_status_history (
            complaint_id, old_state, new_state, actor_type,
            actor_guardian_id, actor_user_id, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        complaint_id, old_state, new_state, actor_type,
        guardian_id, user_id, note,
    ))


def notify_new_complaint(cursor, complaint_id, message_id):
    notify_users(
        cursor,
        complaint_id,
        message_id,
        'new_complaint',
        STAFF_ROLES,
        f'complaint:{complaint_id}:new',
    )


def notify_new_message(cursor, complaint, message_id, sender_type, event_key):
    if sender_type == 'guardian':
        notify_users(
            cursor,
            complaint['id'],
            message_id,
            'new_message',
            STAFF_ROLES,
            event_key,
        )
    else:
        notify_guardian(
            cursor,
            complaint,
            message_id,
            'new_message',
            event_key,
        )


def notify_state_change(cursor, complaint, event_key):
    notify_guardian(cursor, complaint, None, 'status_changed', event_key)


def notify_directors(cursor, complaint_id, event_key):
    notify_users(
        cursor,
        complaint_id,
        None,
        'forwarded',
        DIRECTOR_ROLES,
        event_key,
    )

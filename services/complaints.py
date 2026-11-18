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


def build_complaint_timeline(
    complaint,
    messages,
    history,
    *,
    include_private_details=True,
):
    complaint = dict(complaint)
    message_events = [dict(message) for message in messages]
    events = []
    initial_message_index = next((
        index for index, event in enumerate(message_events)
        if event.get('sender_type') == 'guardian'
        and event.get('body') == complaint.get('mensagem')
        and str(event.get('created_at')) == str(complaint.get('created_at'))
    ), None)

    if complaint.get('mensagem') and initial_message_index is None:
        events.append({
            'id': 0,
            'kind': 'submitted',
            'created_at': complaint.get('created_at'),
            'actor_type': 'guardian',
            'actor_name': (
                complaint.get('nome_encarregado')
                if include_private_details else 'Encarregado de Educação'
            ),
            'body': complaint['mensagem'],
            'old_state': None,
            'new_state': None,
            'note': None,
        })

    actor_labels = {
        'guardian': 'Encarregado de Educação',
        'secretary': 'Secretaria',
        'director': 'Direcção',
    }
    for index, event in enumerate(message_events):
        actor_type = event.get('sender_type')
        events.append({
            'id': event.get('id', 0),
            'kind': 'submitted' if index == initial_message_index else 'message',
            'created_at': event.get('created_at'),
            'actor_type': actor_type,
            'actor_name': (
                event.get('actor_name') if include_private_details
                else actor_labels.get(actor_type, 'Escola')
            ),
            'body': event.get('body'),
            'old_state': None,
            'new_state': None,
            'note': None,
        })

    legacy_response = complaint.get('resposta_escola')
    response_recorded = any(
        event.get('sender_type') in ('secretary', 'director')
        and event.get('body') == legacy_response
        for event in message_events
    )
    if legacy_response and not response_recorded:
        events.append({
            'id': 0,
            'kind': 'message',
            'created_at': complaint.get('updated_at') or complaint.get('created_at'),
            'actor_type': 'secretary',
            'actor_name': 'Secretaria',
            'body': legacy_response,
            'old_state': None,
            'new_state': None,
            'note': None,
        })

    for event in history:
        event = dict(event)
        actor_type = event.get('actor_type')
        events.append({
            'id': event.get('id', 0),
            'kind': 'status',
            'created_at': event.get('created_at'),
            'actor_type': actor_type,
            'actor_name': (
                event.get('actor_name') if include_private_details
                else actor_labels.get(actor_type, 'Escola')
            ),
            'body': None,
            'old_state': event.get('old_state'),
            'new_state': event.get('new_state'),
            'note': event.get('note') if include_private_details else None,
        })

    event_order = {'submitted': 0, 'message': 1, 'status': 2}
    events.sort(key=lambda event: (
        str(event.get('created_at') or ''),
        event_order[event['kind']],
        int(event.get('id') or 0),
    ))
    return events


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

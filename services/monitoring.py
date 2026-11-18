from datetime import datetime
import time


PRESENCE_TIMEOUT_SECONDS = 5 * 60
SESSION_TIMEOUT_SECONDS = 30 * 60

ROLE_SECTIONS = {
    'secretary': ('Secretaria', 'Secretaria'),
    'chefe_secretaria': ('Secretaria', 'Secretaria'),
    'director': ('Director', 'Director'),
    'pedagogico_1': ('Direcção Pedagógica I', 'Direcção Pedagógica I'),
    'pedagogico_2': ('Direcção Pedagógica II', 'Direcção Pedagógica II'),
    'teacher': ('Professor', 'Professor'),
    'admin': ('Administrador', 'Administrador'),
    'guardian': ('Encarregado de Educação', 'Encarregado de Educação'),
}

ACTIVE_SECTIONS = (
    'Secretaria',
    'Director',
    'Direcção Pedagógica I',
    'Direcção Pedagógica II',
    'Professor',
    'Administrador',
    'Encarregado de Educação',
)

DETAILS_ROLES = {'admin', 'director', 'chefe_secretaria'}


def section_for_role(role):
    return ROLE_SECTIONS.get(role, (None, None))[0]


def account_key(role, account_id):
    account_type = 'guardian' if role == 'guardian' else 'staff'
    return f'{account_type}:{account_id}'


def touch_active_session(cursor, key, role, name, now=None):
    now = time.time() if now is None else now
    section = section_for_role(role)
    if not section:
        raise ValueError('Perfil sem secção de presença definida.')
    cursor.execute('''
        INSERT INTO active_sessions (
            account_key, role, display_name, section, login_at, last_activity
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_key) DO UPDATE SET
            login_at = CASE
                WHEN active_sessions.last_activity < ?
                THEN excluded.login_at
                ELSE active_sessions.login_at
            END,
            role = excluded.role,
            display_name = excluded.display_name,
            section = excluded.section,
            last_activity = excluded.last_activity
    ''', (
        key, role, name, section, now, now,
        now - PRESENCE_TIMEOUT_SECONDS,
    ))


def remove_active_session(cursor, key):
    if key:
        cursor.execute('DELETE FROM active_sessions WHERE account_key = ?', (key,))


def current_counts(cursor, now=None):
    now = time.time() if now is None else now
    cursor.execute(
        'DELETE FROM active_sessions WHERE last_activity < ?',
        (now - SESSION_TIMEOUT_SECONDS,),
    )
    cursor.execute('''
        SELECT section, COUNT(*) AS total
        FROM active_sessions
        WHERE last_activity >= ?
        GROUP BY section
    ''', (now - PRESENCE_TIMEOUT_SECONDS,))
    totals = {row['section']: row['total'] for row in cursor.fetchall()}
    return [{'section': section, 'count': totals.get(section, 0)}
            for section in ACTIVE_SECTIONS]


def recent_session_details(cursor, now=None):
    now = time.time() if now is None else now
    current_counts(cursor, now)
    cursor.execute('''
        SELECT display_name, role, section, login_at, last_activity
        FROM active_sessions
        WHERE last_activity >= ?
        ORDER BY section, display_name
    ''', (now - SESSION_TIMEOUT_SECONDS,))
    sessions = []
    for row in cursor.fetchall():
        last_activity = row['last_activity']
        sessions.append({
            'name': row['display_name'],
            'role': ROLE_SECTIONS[row['role']][1],
            'section': row['section'],
            'login_at': _format_timestamp(row['login_at']),
            'last_activity': _format_timestamp(last_activity),
            'status': (
                'Activa'
                if last_activity >= now - PRESENCE_TIMEOUT_SECONDS
                else 'Inactiva'
            ),
        })
    return sessions


def _format_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp).strftime('%d/%m/%Y %H:%M:%S')

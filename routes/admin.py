from datetime import datetime, timedelta
from flask import Blueprint, jsonify, render_template, request, session, redirect, url_for, flash
from services.db import get_db

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/dashboard')
def dashboard():
    if session.get('user_role') != 'admin':
        flash('Acesso reservado ao Administrador Geral.', 'danger')
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    filters = {
        'module': request.args.get('module', '').strip()[:80],
        'operation': request.args.get('operation', '').strip()[:80],
        'user': request.args.get('user', '').strip()[:100],
        'date_from': request.args.get('date_from', '').strip(),
        'date_to': request.args.get('date_to', '').strip(),
    }
    conditions = []
    params = []
    invalid_date = False
    if filters['module']:
        conditions.append("COALESCE(a.module, 'Histórico anterior') = ?")
        params.append(filters['module'])
    if filters['operation']:
        conditions.append('a.operacao LIKE ?')
        params.append(f"%{filters['operation']}%")
    if filters['user']:
        conditions.append("(COALESCE(u.nome, '') LIKE ? OR CAST(a.user_id AS TEXT) = ?)")
        params.extend((f"%{filters['user']}%", filters['user']))
    for field, operator in (('date_from', '>='), ('date_to', '<=')):
        if not filters[field]:
            continue
        try:
            filter_date = datetime.strptime(filters[field], '%Y-%m-%d').date()
        except ValueError:
            filters[field] = ''
            invalid_date = True
            continue
        if field == 'date_to':
            filter_date += timedelta(days=1)
            operator = '<'
        conditions.append(f'CAST(a.timestamp AS TEXT) {operator} ?')
        params.append(filter_date.isoformat())
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ''
    cursor.execute(f'''
        SELECT a.id, a.user_id, COALESCE(u.nome, 'Conta removida') AS actor_name,
               COALESCE(a.module, 'Histórico anterior') AS module,
               a.operacao, COALESCE(a.affected_record, '—') AS affected_record,
               COALESCE(a.change_details, a.detalhes) AS change_details,
               COALESCE(a.result, 'Sucesso') AS result, a.timestamp
        FROM audit_logs a
        LEFT JOIN users u ON u.id = a.user_id
        {where_clause}
        ORDER BY a.timestamp DESC, a.id DESC
        LIMIT 100
    ''', params)
    logs = cursor.fetchall()
    cursor.execute('''
        SELECT id, code, nome, email, role, ativo
        FROM users
        ORDER BY role ASC, nome ASC
    ''')
    funcionarios = cursor.fetchall()
    if invalid_date:
        flash('Uma data do filtro é inválida e foi ignorada.', 'warning')
    return render_template(
        'admin/dashboard.html',
        logs=logs,
        filters=filters,
        funcionarios=funcionarios,
    )


@admin_bp.route('/audit/<int:log_id>')
def audit_details(log_id):
    if session.get('user_role') != 'admin':
        return jsonify(error='Acesso reservado ao Administrador Geral.'), 403
    cursor = get_db().cursor()
    cursor.execute('''
        SELECT a.id, COALESCE(u.nome, 'Conta removida') AS actor_name,
               COALESCE(a.module, 'Histórico anterior') AS module,
               a.operacao, COALESCE(a.affected_record, '—') AS affected_record,
               COALESCE(a.change_details, a.detalhes) AS change_details,
               COALESCE(a.result, 'Sucesso') AS result, a.timestamp
        FROM audit_logs a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.id = ?
    ''', (log_id,))
    log = cursor.fetchone()
    if not log:
        return jsonify(error='Registo de auditoria não encontrado.'), 404
    response = jsonify({
        key: str(value) if value is not None else '—'
        for key, value in dict(log).items()
    })
    response.headers['Cache-Control'] = 'no-store'
    return response
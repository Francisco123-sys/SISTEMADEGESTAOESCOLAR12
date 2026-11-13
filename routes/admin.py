from flask import Blueprint, render_template, session, redirect, url_for, flash
from services.db import get_db

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/dashboard')
def dashboard():
    if session.get('user_role') != 'admin':
        flash('Acesso reservado ao Administrador Geral.', 'danger')
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 20')
    logs = cursor.fetchall()
    return render_template('admin/dashboard.html', logs=logs)
import csv
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file
from services.db import get_db, log_audit, DB_INTEGRITY_ERRORS
from services.student_service import calcular_classe_sugerida, gerar_codigo_unico, atribuir_turma_automatica
from datetime import datetime
import io
import os
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

secretary_bp = Blueprint('secretary', __name__, url_prefix='/secretary')

BANCOS_PERMITIDOS = ['Banco BCI', 'Millennium BIM', 'Standard Bank', 'M-Pesa', 'e-Mola']

def check_secretary():
    return session.get('user_role') in ['secretary', 'chefe_secretaria', 'admin']

def validar_referencia_9_digitos(ref):
    return ref.isdigit() and len(ref) == 9

@secretary_bp.route('/dashboard')
def dashboard():
    if not check_secretary():
        flash('Acesso não autorizado.', 'danger')
        return redirect(url_for('auth.login_landing'))
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT COUNT(*) as total FROM students')
    total_alunos = cursor.fetchone()['total']
    
    cursor.execute('SELECT COUNT(*) as total FROM students WHERE isento_taxa = 1')
    total_isentos = cursor.fetchone()['total']
    
    cursor.execute('SELECT COUNT(*) as total FROM students WHERE nee_tipo != "Nenhuma"')
    total_nee = cursor.fetchone()['total']
    
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
    
    return render_template('secretary/dashboard.html', total_alunos=total_alunos, total_isentos=total_isentos, total_nee=total_nee, total_receita=total_receita, pagamentos=ultimos_pagamentos)

@secretary_bp.route('/students', methods=['GET'])
def students_list():
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    
    busca = request.args.get('q', '').strip()
    classe_filtro = request.args.get('classe', '')
    turma_filtro = request.args.get('turma_id', '')
    filtro_especial = request.args.get('especial', '')
    
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        SELECT t.id, t.nome, t.turno, t.curso, c.numero as classe_num 
        FROM turmas t 
        JOIN classes c ON t.classe_id = c.id 
        ORDER BY c.numero ASC, t.nome ASC
    ''')
    turmas_disponiveis = cursor.fetchall()

    params = []
    query = '''
        SELECT s.*, t.nome as turma_nome, t.sala as turma_sala, t.id as turma_db_id, c.numero as classe_num 
        FROM students s 
        LEFT JOIN turmas t ON s.turma_id = t.id 
        LEFT JOIN classes c ON t.classe_id = c.id
        WHERE 1=1
    '''
    if busca:
        query += ' AND (s.nome LIKE ? OR s.code LIKE ? OR s.bi_cedula LIKE ?)'
        params.extend([f'%{busca}%', f'%{busca}%', f'%{busca}%'])
        
    if classe_filtro:
        query += ' AND s.classe_ingresso = ?'
        params.append(int(classe_filtro))
        
    if turma_filtro:
        query += ' AND s.turma_id = ?'
        params.append(int(turma_filtro))

    if filtro_especial == 'isentos':
        query += ' AND s.isento_taxa = 1'
    elif filtro_especial == 'nee':
        query += ' AND s.nee_tipo != "Nenhuma"'

    query += ' ORDER BY s.classe_ingresso ASC, t.nome ASC, s.nome ASC'
    cursor.execute(query, params)
    todos_alunos = cursor.fetchall()
    
    estrutura_escola = {}
    for a in todos_alunos:
        c_num = a['classe_ingresso']
        t_nome = a['turma_nome'] or 'Sem Turma Atribuída'
        t_id = a['turma_db_id']
        
        if c_num not in estrutura_escola:
            nivel = 'Primário' if c_num <= 6 else 'Secundário'
            estrutura_escola[c_num] = {'numero': c_num, 'nivel': nivel, 'turmas': {}}
            
        if t_nome not in estrutura_escola[c_num]['turmas']:
            estrutura_escola[c_num]['turmas'][t_nome] = {'id': t_id, 'alunos': []}
            
        estrutura_escola[c_num]['turmas'][t_nome]['alunos'].append(a)

    return render_template('secretary/students.html', 
                           estrutura_escola=estrutura_escola, 
                           turmas_disponiveis=turmas_disponiveis,
                           busca=busca, classe_filtro=classe_filtro, turma_filtro=turma_filtro, filtro_especial=filtro_especial)

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
        classe_atribuida = int(request.form.get('classe'))
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
            'contacto_1': request.form.get('contacto_1', '').strip(),
            'contacto_2': request.form.get('contacto_2', '').strip(),
            'classe': classe_atribuida,
            'turno': request.form.get('turno', 'Manhã'),
            'curso': request.form.get('curso', 'Diurno'),
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
        
    return render_template('secretary/student_form.html')

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
            
        if not valor_pago or float(valor_pago) <= 0:
            flash('ERRO: O valor do pagamento deve ser maior que zero. Sem pagamento válido, o sistema não regista o aluno.', 'danger')
            return redirect(url_for('secretary.student_registration_payment'))
            
        if not data_deposito:
            flash('ERRO: Informe a data em que o depósito foi efectuado.', 'danger')
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
            'valor_pago': float(valor_pago),
            'data_deposito': data_deposito,
            'mes_pago': mes_pago
        }
        
        session.pop('pending_matricula', None)
        return _concluir_matricula_no_banco(db, dados_matricula, dados_pagamento)
        
    return render_template('secretary/student_registration_payment.html', aluno=dados_matricula, bancos=BANCOS_PERMITIDOS, hoje=datetime.now().strftime('%Y-%m-%d'))

def _concluir_matricula_no_banco(db, m, pag):
    cursor = db.cursor()
    codigo_aluno = gerar_codigo_unico("ALU", "students")
    ano_lectivo_id = 1
    data_hoje = datetime.now().strftime('%Y-%m-%d')
    
    turma_id = atribuir_turma_automatica(db, m['classe'], ano_lectivo_id, m['turno'], m['curso'])
    
    cursor.execute('''
        INSERT INTO students (
            code, nome, nome_pai, nome_mae, bi_cedula, data_nascimento, sexo, morada, naturalidade,
            encarregado_contacto_1, encarregado_contacto_2,
            classe_ingresso, turma_id, ano_lectivo_id, turno, curso, data_matricula, isento_taxa, justificacao_isencao, nee_tipo,
            classe_sugerida, justificacao_alteracao, funcionario_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        codigo_aluno, m['nome'], m['nome_pai'], m['nome_mae'],
        m['bi_cedula'], m['data_nascimento'], m['sexo'], m['morada'], m['naturalidade'],
        m['contacto_1'], m['contacto_2'],
        m['classe'], turma_id, ano_lectivo_id, m['turno'], m['curso'], data_hoje, m['isento'], m['justificacao_isencao'], m['nee_tipo'],
        m['classe_sugerida'], m['justificacao'], session['user_id']
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
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        bi_cedula = request.form.get('bi_cedula', '').strip()
        cursor.execute('SELECT id FROM students WHERE bi_cedula = ? AND id != ?', (bi_cedula, student_id))
        if cursor.fetchone():
            flash(f'ERRO: O BI {bi_cedula} já pertence a outro aluno.', 'danger')
            return redirect(url_for('secretary.student_edit', student_id=student_id))
            
        cursor.execute('''
            UPDATE students SET nome=?, nome_pai=?, nome_mae=?, bi_cedula=?, data_nascimento=?, sexo=?,
            morada=?, encarregado_contacto_1=?, encarregado_contacto_2=?, isento_taxa=?, justificacao_isencao=?, nee_tipo=?
            WHERE id=?
        ''', (
            nome, request.form.get('nome_pai'), request.form.get('nome_mae'), bi_cedula,
            request.form.get('data_nascimento'), request.form.get('sexo'), request.form.get('morada'),
            request.form.get('contacto_1'), request.form.get('contacto_2'),
            1 if request.form.get('isento_taxa') else 0, request.form.get('justificacao_isencao', ''),
            request.form.get('nee_tipo', 'Nenhuma'), student_id
        ))
        db.commit()
        flash('Dados do aluno atualizados com sucesso!', 'success')
        return redirect(url_for('secretary.students_list'))
        
    cursor.execute('SELECT * FROM students WHERE id = ?', (student_id,))
    aluno = cursor.fetchone()
    return render_template('secretary/student_edit.html', aluno=aluno)


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
    
    if request.method == 'POST':
        student_id = request.form.get('student_id')
        mes_pago = request.form.get('mes_pago')
        valor = float(request.form.get('valor_pago'))
        banco = request.form.get('banco_plataforma', '').strip()
        referencia = request.form.get('referencia_talao', '').strip()
        data_deposito = request.form.get('data_deposito', '').strip()
        
        if banco not in BANCOS_PERMITIDOS:
            flash('ERRO: Selecione um banco ou carteira móvel válida (Banco BCI, Millennium BIM, Standard Bank, M-Pesa ou e-Mola).', 'danger')
            return redirect(url_for('secretary.payment_form'))
            
        if not validar_referencia_9_digitos(referencia):
            flash('ERRO: A referência do talão deve conter EXATAMENTE 9 dígitos numéricos.', 'danger')
            return redirect(url_for('secretary.payment_form'))
            
        if not data_deposito:
            flash('ERRO: Preencha a data em que o depósito foi efectuado.', 'danger')
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
                INSERT INTO payments (receipt_number, student_id, mes_pago, ano_lectivo_id, valor_pago, banco_plataforma, referencia_talao, data_deposito, data_registo, funcionario_id)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            ''', (recibo_num, student_id, mes_pago, valor, banco, referencia, data_deposito, data_registo, session['user_id']))
            db.commit()
            
            payment_id = cursor.lastrowid
            log_audit(session['user_id'], 'PAGAMENTO_REGISTADO', f"Recibo {recibo_num} emitido ({banco}, Ref: {referencia}).")
            flash(f'Pagamento registado com sucesso! Recibo: {recibo_num}', 'success')
            return redirect(url_for('secretary.receipt_view', payment_id=payment_id))
        except DB_INTEGRITY_ERRORS:
            flash(f'ERRO: Referência {referencia} duplicada na base de dados!', 'danger')
            return redirect(url_for('secretary.payment_form'))

    cursor.execute('SELECT id, code, nome, classe_ingresso FROM students WHERE isento_taxa = 0 ORDER BY classe_ingresso ASC, nome ASC')
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
        mes_pago = request.form.get('mes_pago')
        valor_pago = float(request.form.get('valor_pago'))
        banco = request.form.get('banco_plataforma')
        referencia = request.form.get('referencia_talao').strip()
        data_deposito = request.form.get('data_deposito')
        
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
        
    cursor.execute('''
        SELECT code, nome, bi_cedula, sexo, data_nascimento, encarregado_contacto_1
        FROM students
        WHERE turma_id = ?
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

@secretary_bp.route('/colaboradores/toggle/<int:user_id>')
def toggle_colega(user_id):
    if session.get('user_role') not in ['chefe_secretaria', 'admin']:
        return redirect(url_for('secretary.dashboard'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('UPDATE users SET ativo = CASE WHEN ativo = 1 THEN 0 ELSE 1 END WHERE id = ? AND role = "secretary"', (user_id,))
    db.commit()
    flash('Estado do colega alterado!', 'info')
    return redirect(url_for('secretary.gerenciar_colaboradores'))

@secretary_bp.route('/colaboradores/delete/<int:user_id>')
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
    
    if request.method == 'POST':
        acao = request.form.get('acao')
        if acao == 'criar_turma':
            nome = request.form.get('nome', '').strip()
            sala = request.form.get('sala', '').strip()
            classe_id = int(request.form.get('classe_id'))
            turno = request.form.get('turno')
            curso = request.form.get('curso')
            capacidade = int(request.form.get('capacidade_maxima', 40))
            
            cursor.execute('''
                INSERT INTO turmas (nome, sala, classe_id, ano_lectivo_id, turno, curso, capacidade_maxima, vagas_disponiveis)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ''', (nome, sala, classe_id, turno, curso, capacidade, capacidade))
            db.commit()
            log_audit(session['user_id'], 'CRIAR_TURMA', f"Turma {nome} ({sala}) criada com {capacidade} vagas.")
            flash(f'Turma {nome} ({sala}) criada com {capacidade} vagas!', 'success')
            
        elif acao == 'transferir_aluno':
            student_id = int(request.form.get('student_id'))
            nova_turma_id = int(request.form.get('nova_turma_id'))
            
            cursor.execute('SELECT turma_id FROM students WHERE id = ?', (student_id,))
            atual = cursor.fetchone()
            
            cursor.execute('SELECT vagas_disponiveis, turno, curso, classe_id FROM turmas WHERE id = ?', (nova_turma_id,))
            destino = cursor.fetchone()
            
            if not destino or destino['vagas_disponiveis'] <= 0:
                flash('ERRO: A turma/sala selecionada já não possui vagas disponíveis!', 'danger')
            else:
                if atual and atual['turma_id']:
                    cursor.execute('UPDATE turmas SET vagas_disponiveis = vagas_disponiveis + 1 WHERE id = ?', (atual['turma_id'],))
                cursor.execute('UPDATE turmas SET vagas_disponiveis = vagas_disponiveis - 1 WHERE id = ?', (nova_turma_id,))
                cursor.execute('''
                    UPDATE students SET turma_id = ?, turno = ?, curso = ?, classe_ingresso = ? WHERE id = ?
                ''', (nova_turma_id, destino['turno'], destino['curso'], destino['classe_id'], student_id))
                db.commit()
                log_audit(session['user_id'], 'ATRIBUIR_TURMA', f"Aluno ID {student_id} atribuído à Turma ID {nova_turma_id}.")
                flash('Aluno atribuído à turma e controle de vagas atualizado!', 'success')
        return redirect(url_for('secretary.gerenciar_turmas'))

    cursor.execute('''
        SELECT t.*, c.numero as classe_num, ay.ano as ano_lectivo,
               (SELECT COUNT(*) FROM students s WHERE s.turma_id = t.id) as inscritos
        FROM turmas t
        JOIN classes c ON t.classe_id = c.id
        JOIN academic_years ay ON t.ano_lectivo_id = ay.id
        ORDER BY c.numero ASC, t.nome ASC
    ''')
    turmas = cursor.fetchall()
    
    cursor.execute('SELECT id, code, nome, classe_ingresso FROM students ORDER BY nome ASC')
    alunos = cursor.fetchall()
    
    cursor.execute('SELECT id, nome FROM subjects ORDER BY nome ASC')
    disciplinas = cursor.fetchall()
    
    return render_template('secretary/turmas.html', turmas=turmas, alunos=alunos, disciplinas=disciplinas)

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
        
    cursor.execute('SELECT id, code, nome FROM students WHERE turma_id = ? ORDER BY nome ASC', (turma_id,))
    alunos = cursor.fetchall()
    return render_template('secretary/lista_nominal.html', turma=turma, alunos=alunos)

@secretary_bp.route('/pautas/exportar-csv/<int:turma_id>/<int:subject_id>')
def exportar_pauta_csv(turma_id, subject_id):
    if not check_secretary():
        return redirect(url_for('auth.login_landing'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT s.code, s.nome, g.av1, g.av2, g.av3, g.av4, g.exame, g.faltas_justificadas, g.faltas_nao_justificadas
        FROM students s
        LEFT JOIN grades g ON s.id = g.student_id AND g.subject_id = ? AND g.turma_id = ?
        WHERE s.turma_id = ? ORDER BY s.nome ASC
    ''', (subject_id, turma_id, turma_id))
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

    for row in reader:
        code = row.get('Codigo_Aluno', '').strip()
        cursor.execute('SELECT id FROM students WHERE code = ? AND turma_id = ?', (code, turma_id))
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
        rec_id = int(request.form.get('rec_id'))
        resposta = request.form.get('resposta_escola', '').strip()
        if resposta:
            cursor.execute('''
                UPDATE reclamacoes_encarregados SET resposta_escola = ?, status = 'Respondida' WHERE id = ?
            ''', (resposta, rec_id))
            db.commit()
            flash('Resposta registada com sucesso!', 'success')
        return redirect(url_for('secretary.gerenciar_reclamacoes'))
        
    cursor.execute('''
        SELECT r.*, s.nome as aluno_nome, s.code as aluno_code
        FROM reclamacoes_encarregados r
        JOIN students s ON r.student_id = s.id
        ORDER BY r.status ASC, r.id DESC
    ''')
    reclamacoes = cursor.fetchall()
    return render_template('secretary/reclamacoes.html', reclamacoes=reclamacoes)

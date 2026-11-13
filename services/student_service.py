from datetime import datetime
from services.db import get_db

def calcular_classe_sugerida(data_nascimento):
    if not data_nascimento:
        return 1
    try:
        ano_nasc = int(data_nascimento.split('-')[0])
        ano_atual = datetime.now().year
        idade = ano_atual - ano_nasc
        classe = idade - 5
        if classe < 1:
            return 1
        elif classe > 12:
            return 12
        return classe
    except Exception:
        return 1

def gerar_codigo_unico(prefixo, tabela):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(f"SELECT COUNT(*) as total FROM {tabela}")
    total = cursor.fetchone()['total'] + 1
    return f"{prefixo}-{total:06d}"

def atribuir_turma_automatica(db, classe_num, ano_lectivo_id, turno, curso="Diurno"):
    cursor = db.cursor()
    cursor.execute('''
        SELECT t.id FROM turmas t
        JOIN classes c ON t.classe_id = c.id
        WHERE c.numero = ? AND t.ano_lectivo_id = ? AND t.turno = ? AND t.curso = ? AND t.vagas_disponiveis > 0
        ORDER BY t.id ASC LIMIT 1
    ''', (classe_num, ano_lectivo_id, turno, curso))
    turma = cursor.fetchone()
    
    if turma:
        turma_id = turma['id']
        cursor.execute('UPDATE turmas SET vagas_disponiveis = vagas_disponiveis - 1 WHERE id = ?', (turma_id,))
        return turma_id
    else:
        cursor.execute('SELECT id FROM classes WHERE numero = ?', (classe_num,))
        c_row = cursor.fetchone()
        classe_id = c_row['id'] if c_row else classe_num
        
        cursor.execute('SELECT COUNT(*) as total FROM turmas WHERE classe_id = ? AND ano_lectivo_id = ? AND curso = ?', (classe_id, ano_lectivo_id, curso))
        count = cursor.fetchone()['total']
        letra = chr(65 + count)
        
        cursor.execute('SELECT COUNT(*) as total_turmas FROM turmas')
        num_sala = cursor.fetchone()['total_turmas'] + 1
        sala_nome = f"Sala {num_sala:02d}"
        
        nome_turma = f"{classe_num}ª {letra} ({turno} - {curso})"
        cursor.execute('''
            INSERT INTO turmas (nome, sala, classe_id, ano_lectivo_id, turno, curso, capacidade_maxima, vagas_disponiveis)
            VALUES (?, ?, ?, ?, ?, ?, 40, 39)
        ''', (nome_turma, sala_nome, classe_id, ano_lectivo_id, turno, curso))
        return cursor.lastrowid

from datetime import datetime
import re
from services.db import get_db


AREAS_CURRICULARES = {
    'A': 'Letras e Ciências Sociais',
    'B': 'Ciências Naturais e Exactas',
    'C': 'Artes Visuais e Cénicas / Ciências com Desenho',
}


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
    code_columns = {
        'students': 'code',
        'payments': 'receipt_number',
        'users': 'code',
    }
    if tabela not in code_columns or not re.fullmatch(r'[A-Z0-9]+', prefixo):
        raise ValueError('Tabela ou prefixo de código inválido.')

    db = get_db()
    cursor = db.cursor()
    code_column = code_columns[tabela]
    cursor.execute(
        f'SELECT {code_column} AS code FROM {tabela} WHERE {code_column} LIKE ?',
        (f'{prefixo}-%',),
    )
    highest_existing = 0
    pattern = re.compile(rf'{re.escape(prefixo)}-(\d+)')
    for row in cursor.fetchall():
        match = pattern.fullmatch(row['code'])
        if match:
            highest_existing = max(highest_existing, int(match.group(1)))

    counter_key = f'{tabela}:{prefixo}'
    cursor.execute('''
        INSERT INTO code_counters (counter_key, value)
        VALUES (?, ?)
        ON CONFLICT(counter_key) DO UPDATE SET
            value = CASE
                WHEN code_counters.value + 1 > excluded.value
                THEN code_counters.value + 1
                ELSE excluded.value
            END
        RETURNING value
    ''', (counter_key, highest_existing + 1))
    return f'{prefixo}-{cursor.fetchone()["value"]:06d}'

def _codigo_sequencial(numero):
    codigo = ''
    while numero:
        numero, resto = divmod(numero - 1, 26)
        codigo = chr(65 + resto) + codigo
    return codigo


def _validar_contexto_turma(classe_num, turno, curso, area_curricular):
    if classe_num not in range(8, 13):
        raise ValueError('As novas matrículas devem ser para a 8.ª à 12.ª Classe.')
    if turno not in ('Manhã', 'Tarde'):
        raise ValueError('Seleccione o turno Manhã ou Tarde.')
    if curso not in ('Diurno', 'À Distância'):
        raise ValueError('Seleccione a modalidade Diurno ou À Distância.')

    if classe_num >= 10:
        if area_curricular not in AREAS_CURRICULARES:
            raise ValueError('Seleccione a área curricular da 10.ª à 12.ª Classe.')
    elif area_curricular:
        raise ValueError('A área curricular só se aplica da 10.ª à 12.ª Classe.')


def atribuir_turma_automatica(
    db,
    classe_num,
    ano_lectivo_id,
    turno,
    curso="Diurno",
    area_curricular=None,
    capacidade_padrao=40,
):
    _validar_contexto_turma(classe_num, turno, curso, area_curricular)
    if capacidade_padrao < 1:
        raise ValueError('A capacidade da turma deve ser maior que zero.')

    cursor = db.cursor()

    cursor.execute('SELECT id FROM classes WHERE numero = ? ORDER BY id ASC LIMIT 1', (classe_num,))
    classe = cursor.fetchone()
    if not classe:
        raise ValueError(f'A classe {classe_num}.ª não está configurada.')
    classe_id = classe['id']

    # Serializa a escolha/criação de turmas para esta classe em ambas as bases suportadas.
    cursor.execute('UPDATE classes SET numero = numero WHERE id = ?', (classe_id,))

    area_filter = 'AND t.area_curricular = ?' if area_curricular else 'AND t.area_curricular IS NULL'
    params = [classe_id, ano_lectivo_id, turno, curso]
    if area_curricular:
        params.append(area_curricular)

    cursor.execute('''
        SELECT t.id
        FROM turmas t
        WHERE t.classe_id = ? AND t.ano_lectivo_id = ? AND t.turno = ? AND t.curso = ?
          AND t.vagas_disponiveis > 0
          {area_filter}
        ORDER BY t.id ASC LIMIT 1
    '''.format(area_filter=area_filter), params)
    turma = cursor.fetchone()

    if turma:
        turma_id = turma['id']
        cursor.execute('''
            UPDATE turmas SET vagas_disponiveis = vagas_disponiveis - 1
            WHERE id = ? AND vagas_disponiveis > 0
        ''', (turma_id,))
        if cursor.rowcount == 1:
            return turma_id

    cursor.execute('''
        SELECT nome FROM turmas
        WHERE classe_id = ? AND ano_lectivo_id = ? AND turno = ? AND curso = ?
          AND ((area_curricular = ?) OR (area_curricular IS NULL AND ? IS NULL))
    ''', (classe_id, ano_lectivo_id, turno, curso, area_curricular, area_curricular))
    nomes_existentes = {row['nome'] for row in cursor.fetchall()}

    if classe_num <= 9:
        sequencia = 1
        while True:
            codigo_grupo = _codigo_sequencial(sequencia)
            nome_turma = f'{classe_num}ª {codigo_grupo}'
            if nome_turma not in nomes_existentes:
                break
            sequencia += 1
    else:
        sequencia = 1
        while True:
            nome_turma = f'{classe_num}ª {area_curricular}{sequencia:02d}'
            if nome_turma not in nomes_existentes:
                break
            sequencia += 1

    cursor.execute('SELECT COUNT(*) AS total FROM turmas WHERE ano_lectivo_id = ?', (ano_lectivo_id,))
    num_sala = cursor.fetchone()['total'] + 1
    sala_nome = f'Sala {num_sala:02d}'

    cursor.execute('''
        INSERT INTO turmas (
            nome, sala, classe_id, ano_lectivo_id, turno, curso,
            capacidade_maxima, vagas_disponiveis, area_curricular
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        nome_turma, sala_nome, classe_id, ano_lectivo_id, turno, curso,
        capacidade_padrao, capacidade_padrao - 1, area_curricular
    ))
    return cursor.lastrowid

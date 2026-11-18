-- SIGE PostgreSQL / Neon Schema (Escola Secundária da Manga - Beira, Sofala)

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    nome TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'director', 'pedagogico_1', 'pedagogico_2', 'teacher', 'secretary', 'chefe_secretaria')),
    contacto TEXT,
    foto_path TEXT DEFAULT 'default_avatar.png',
    ativo INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS academic_years (
    id SERIAL PRIMARY KEY,
    ano INTEGER UNIQUE NOT NULL,
    ativo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS code_counters (
    counter_key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS classes (
    id SERIAL PRIMARY KEY,
    numero INTEGER NOT NULL CHECK(numero BETWEEN 1 AND 12),
    nivel TEXT NOT NULL CHECK(nivel IN ('Primário', 'Secundário'))
);

CREATE TABLE IF NOT EXISTS turmas (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    sala TEXT DEFAULT 'Sala 01',
    classe_id INTEGER NOT NULL REFERENCES classes(id),
    ano_lectivo_id INTEGER NOT NULL REFERENCES academic_years(id),
    turno TEXT NOT NULL CHECK(turno IN ('Manhã', 'Tarde', 'Noite')),
    curso TEXT NOT NULL DEFAULT 'Diurno' CHECK(curso IN ('Diurno', 'Nocturno', 'À Distância')),
    capacidade_maxima INTEGER DEFAULT 40,
    vagas_disponiveis INTEGER DEFAULT 40,
    area_curricular TEXT CHECK(area_curricular IN ('A', 'B', 'C') OR area_curricular IS NULL),
    diretor_turma_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS subjects (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    codigo TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS teacher_assignments (
    id SERIAL PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES users(id),
    turma_id INTEGER NOT NULL REFERENCES turmas(id),
    subject_id INTEGER NOT NULL REFERENCES subjects(id)
);

CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    nome TEXT NOT NULL,
    nome_pai TEXT,
    nome_mae TEXT,
    bi_cedula TEXT UNIQUE NOT NULL,
    data_nascimento TEXT NOT NULL,
    sexo TEXT CHECK(sexo IN ('M', 'F')),
    morada TEXT,
    naturalidade TEXT,
    provincia_nascimento TEXT,
    distrito_nascimento TEXT,
    pais_nascimento TEXT,
    pais_residencia TEXT,
    provincia_residencia TEXT,
    distrito_residencia TEXT,
    nacionalidade TEXT DEFAULT 'Moçambicana',
    documento_identificacao_tipo TEXT,
    documento_identificacao_pais TEXT,
    documento_identificacao_numero TEXT,
    foto_path TEXT,
    pai_naturalidade TEXT,
    pai_profissao TEXT,
    pai_contacto TEXT,
    mae_naturalidade TEXT,
    mae_profissao TEXT,
    mae_contacto TEXT,
    encarregado_nome TEXT,
    encarregado_parentesco TEXT,
    encarregado_naturalidade TEXT,
    encarregado_profissao TEXT,
    encarregado_contacto_principal TEXT,
    encarregado_contacto_alternativo TEXT,
    encarregado_morada TEXT,
    encarregado_email TEXT,
    ingresso_situacao TEXT NOT NULL DEFAULT 'Interno' CHECK(ingresso_situacao IN ('Interno', 'Transferido')),
    escola_origem TEXT,
    provincia_origem TEXT,
    distrito_origem TEXT,
    classe_anterior INTEGER,
    ano_lectivo_anterior INTEGER,
    documento_transferencia_path TEXT,
    observacoes_transferencia TEXT,
    encarregado_contacto_1 TEXT NOT NULL,
    encarregado_contacto_2 TEXT,
    encarregado_contacto_3 TEXT,
    encarregado_contacto_4 TEXT,
    classe_ingresso INTEGER NOT NULL,
    turma_id INTEGER NOT NULL REFERENCES turmas(id),
    ano_lectivo_id INTEGER NOT NULL REFERENCES academic_years(id),
    turno TEXT NOT NULL DEFAULT 'Manhã',
    curso TEXT NOT NULL DEFAULT 'Diurno',
    data_matricula TEXT NOT NULL,
    isento_taxa INTEGER DEFAULT 0,
    justificacao_isencao TEXT,
    nee_tipo TEXT DEFAULT 'Nenhuma',
    classe_sugerida INTEGER,
    justificacao_alteracao TEXT,
    ativo INTEGER NOT NULL DEFAULT 1,
    eliminado_em TIMESTAMP,
    eliminado_por INTEGER REFERENCES users(id) ON DELETE SET NULL,
    funcionario_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guardians (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    contacto TEXT,
    ativo INTEGER NOT NULL DEFAULT 1,
    session_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guardian_password_resets (
    token_hash TEXT PRIMARY KEY,
    guardian_id INTEGER NOT NULL REFERENCES guardians(id) ON DELETE CASCADE,
    expires_at DOUBLE PRECISION NOT NULL,
    used_at DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_rate_limits (
    bucket_hash TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    window_started_at DOUBLE PRECISION NOT NULL,
    blocked_until DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS guardian_students (
    guardian_id INTEGER NOT NULL REFERENCES guardians(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    parentesco TEXT,
    principal INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guardian_id, student_id)
);

CREATE TABLE IF NOT EXISTS student_deletion_requests (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    turma_id INTEGER NOT NULL REFERENCES turmas(id),
    solicitado_por INTEGER NOT NULL REFERENCES users(id),
    solicitado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    motivo TEXT,
    estado TEXT NOT NULL DEFAULT 'Pendente'
        CHECK(estado IN ('Pendente', 'Aprovado', 'Rejeitado')),
    decidido_por INTEGER REFERENCES users(id),
    decidido_em TIMESTAMP,
    motivo_decisao TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_student_deletion_pending
    ON student_deletion_requests(student_id) WHERE estado = 'Pendente';

CREATE TABLE IF NOT EXISTS grades (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    turma_id INTEGER NOT NULL REFERENCES turmas(id),
    trimestre INTEGER CHECK(trimestre IN (1, 2, 3)),
    av1 DOUBLE PRECISION DEFAULT 0,
    av2 DOUBLE PRECISION DEFAULT 0,
    av3 DOUBLE PRECISION DEFAULT 0,
    av4 DOUBLE PRECISION DEFAULT 0,
    media_trimestral DOUBLE PRECISION DEFAULT 0,
    exame DOUBLE PRECISION DEFAULT NULL,
    media_final DOUBLE PRECISION DEFAULT 0,
    faltas_justificadas INTEGER DEFAULT 0,
    faltas_nao_justificadas INTEGER DEFAULT 0,
    total_faltas INTEGER DEFAULT 0,
    comentario_professor TEXT DEFAULT '',
    status TEXT DEFAULT 'Rascunho' CHECK(status IN ('Rascunho', 'Submetida', 'Fechada', 'Reaberta'))
);

CREATE TABLE IF NOT EXISTS lesson_summaries (
    id SERIAL PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES users(id),
    turma_id INTEGER NOT NULL REFERENCES turmas(id),
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    numero_aula INTEGER NOT NULL,
    data_aula TEXT NOT NULL,
    sumario TEXT NOT NULL,
    observacoes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    turma_id INTEGER NOT NULL,
    subject_id INTEGER NOT NULL,
    data_aula TEXT NOT NULL,
    tipo TEXT CHECK(tipo IN ('Presença', 'Falta', 'Justificada')),
    motivo TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    receipt_number TEXT UNIQUE NOT NULL,
    student_id INTEGER NOT NULL REFERENCES students(id),
    mes_pago TEXT NOT NULL,
    ano_lectivo_id INTEGER NOT NULL REFERENCES academic_years(id),
    valor_pago DOUBLE PRECISION NOT NULL,
    banco_plataforma TEXT NOT NULL,
    referencia_talao TEXT UNIQUE NOT NULL,
    data_deposito TEXT NOT NULL,
    data_registo TEXT NOT NULL,
    funcionario_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reclamacoes_encarregados (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    guardian_id INTEGER REFERENCES guardians(id),
    nome_encarregado TEXT NOT NULL,
    contacto TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    status TEXT DEFAULT 'Pendente' CHECK(status IN ('Pendente', 'Respondida')),
    resposta_escola TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    categoria TEXT,
    assunto TEXT,
    estado TEXT NOT NULL DEFAULT 'Nova',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    assigned_to INTEGER REFERENCES users(id),
    forwarded_to_director INTEGER NOT NULL DEFAULT 0,
    forwarded_by INTEGER REFERENCES users(id),
    forwarded_at TIMESTAMP,
    forward_reason TEXT,
    submission_key TEXT
);

CREATE TABLE IF NOT EXISTS complaint_messages (
    id SERIAL PRIMARY KEY,
    complaint_id INTEGER NOT NULL REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
    sender_type TEXT NOT NULL CHECK(sender_type IN ('guardian', 'secretary', 'director')),
    sender_guardian_id INTEGER REFERENCES guardians(id),
    sender_user_id INTEGER REFERENCES users(id),
    message_key TEXT,
    body TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS complaint_status_history (
    id SERIAL PRIMARY KEY,
    complaint_id INTEGER NOT NULL REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
    old_state TEXT,
    new_state TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('guardian', 'secretary', 'director')),
    actor_guardian_id INTEGER REFERENCES guardians(id),
    actor_user_id INTEGER REFERENCES users(id),
    note TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS complaint_notifications (
    id SERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    complaint_id INTEGER NOT NULL REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES complaint_messages(id) ON DELETE CASCADE,
    recipient_guardian_id INTEGER REFERENCES guardians(id) ON DELETE CASCADE,
    recipient_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('new_complaint', 'new_message', 'status_changed', 'forwarded')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP,
    CHECK ((recipient_guardian_id IS NULL) <> (recipient_user_id IS NULL))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    operacao TEXT NOT NULL,
    detalhes TEXT NOT NULL,
    ip_address TEXT,
    module TEXT,
    affected_record TEXT,
    change_details TEXT,
    result TEXT NOT NULL DEFAULT 'Sucesso',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS active_sessions (
    account_key TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    display_name TEXT NOT NULL,
    section TEXT NOT NULL,
    login_at DOUBLE PRECISION NOT NULL,
    last_activity DOUBLE PRECISION NOT NULL
);

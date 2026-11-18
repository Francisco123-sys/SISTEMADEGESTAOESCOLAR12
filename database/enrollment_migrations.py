def migrate_enrollment_schema(cursor, is_postgres):
    columns = (
        ('pais_nascimento', 'TEXT'),
        ('distrito_nascimento', 'TEXT'),
        ('pais_residencia', 'TEXT'),
        ('provincia_residencia', 'TEXT'),
        ('distrito_residencia', 'TEXT'),
        ('documento_identificacao_tipo', 'TEXT'),
        ('documento_identificacao_pais', 'TEXT'),
        ('documento_identificacao_numero', 'TEXT'),
    )
    if is_postgres:
        for column, definition in columns:
            cursor.execute(
                f'ALTER TABLE students ADD COLUMN IF NOT EXISTS {column} {definition}'
            )
    else:
        cursor.execute('PRAGMA table_info(students)')
        existing_columns = {row[1] for row in cursor.fetchall()}
        for column, definition in columns:
            if column not in existing_columns:
                cursor.execute(
                    f'ALTER TABLE students ADD COLUMN {column} {definition}'
                )

    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS ux_students_identity_document
        ON students (
            documento_identificacao_pais,
            documento_identificacao_tipo,
            documento_identificacao_numero
        )
        WHERE documento_identificacao_numero IS NOT NULL
          AND documento_identificacao_numero <> ''
    ''')


def migrate_enrollment_data():
    from services.db import (
        create_standalone_connection,
        ensure_same_database_target,
        get_database_url,
        is_postgres,
    )

    runtime_url = get_database_url()
    migration_url = get_database_url(prefer_unpooled=True)
    selected_url = migration_url or runtime_url
    if is_postgres(selected_url):
        ensure_same_database_target(runtime_url, selected_url)

    db = create_standalone_connection(selected_url)
    cursor = db.cursor()
    try:
        migrate_enrollment_schema(cursor, is_postgres(selected_url))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()

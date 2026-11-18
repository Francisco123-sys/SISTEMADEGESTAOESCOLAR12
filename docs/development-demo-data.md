# SIGE Neon demonstration data

`scripts/seed_demo_data.py` is a development-only reset and data generator. It
does not run `init-db`, alter the schema, send email, or contact external
payment services.

The command defaults to a read-only preview. Applying it requires all of the
following:

- The PostgreSQL target is the explicitly confirmed Neon `neondb/public`
  development database.
- `SIGE_DEMO_RESET_CONFIRM=I_CONFIRM_DEVELOPMENT` is set in the process
  environment and `--confirm-development-database neondb` is supplied.
- A private full backup and its matching private manifest are provided.
- The private credentials output path is outside the Git repository.

Run the preview from the repository root as a module so project imports resolve:

```sh
SIGE_DEMO_RESET_CONFIRM=I_CONFIRM_DEVELOPMENT \
  python -m scripts.seed_demo_data --confirm-development-database neondb
```

The preview reports only the configured database/schema and row counts. It does
not modify records.

The apply operation deletes only the allowlisted demonstration tables, in
foreign-key order, and inserts the replacement data in the same transaction.
If validation fails, the transaction is rolled back. School-year, class, and
subject configuration tables are preserved. User credentials are individually
generated, stored as password hashes in PostgreSQL, and written to a
mode-`0600` file outside the repository.

The generated records use `.test` email addresses and `DEMO-` contact,
document, payment, and record identifiers. They are fictitious and must not be
used for real communication or payments.

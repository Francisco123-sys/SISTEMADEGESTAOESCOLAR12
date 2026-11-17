# Relatório de testes

## Validações executadas
- `python -m unittest discover -s tests -v` — 70 testes totais; 62 aprovados e 8 testes PostgreSQL ignorados por não receberem a variável de teste.
- `SIGE_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/sige_test python -m unittest tests.test_postgresql_integration -v` — 8 testes aprovados num PostgreSQL 16 efémero, restrito a loopback.
- `python -m unittest tests.test_guardian_password_recovery -v` — 9 testes aprovados para tokens, SMTP simulado, enumeração e limites de tentativas.
- `python -m compileall -q app.py routes services database tests` — concluído sem erros.
- `git diff --check` — concluído sem erros.
- `SECRET_KEY=route-validation-only python -m flask --app app routes` — rotas carregadas.
- Os testes PostgreSQL também verificaram inicialização idempotente, recuperação e limites persistidos, pagamento/recibo, submissão de notas, associações/autorização de encarregados, contador de códigos, transferência de aluno/vagas e geração de PDF.

## Limites da validação
- O PostgreSQL foi local e efémero; não foi feita ligação à Neon nem a uma base de produção.
- O SMTP foi simulado nos testes; falta validar a entrega no ambiente de homologação com credenciais próprias desse ambiente.

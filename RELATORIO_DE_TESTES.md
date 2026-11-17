# Relatório de testes

## Validações executadas
- `python -m unittest discover -s tests -v` com PostgreSQL local isolado — 45 testes aprovados.
- `python -m compileall -q app.py routes services database tests` — concluído sem erros.
- `git diff --check` — concluído sem erros.
- `SECRET_KEY=route-validation-only python -m flask --app app routes` — rotas carregadas.
- Auditoria dos templates — 39 formulários POST verificados, nenhum sem token CSRF.
- PostgreSQL 16 efémero: inicialização idempotente, associações/autorização de encarregados, contador de códigos, transferência de aluno/vagas e geração de PDF aprovados.
- A configuração inicial de `init-db` priorizou as URLs não agrupadas presentes no `.env`; o utilizador confirmou que o destino era desenvolvimento. As execuções PostgreSQL de integração seguintes neutralizaram essas variáveis e usaram somente o servidor efémero em loopback.

## Limites da validação
- O PostgreSQL foi local e efémero; não foi feita ligação à Neon.
- Nenhuma ligação ou migração foi feita em produção; a integração automatizada usou apenas PostgreSQL local efémero.

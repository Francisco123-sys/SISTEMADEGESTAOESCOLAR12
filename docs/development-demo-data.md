# Dados escolares sintéticos para testes

`scripts/seed_demo_data.py` apresenta primeiro um plano apenas de leitura. Por
omissão prepara 1.000 alunos e permite seleccionar 2.000 ou 5.000. O plano
abrange classes 8.ª a 12.ª, 30 turmas presenciais e à distância, avaliações,
pagamentos internos e três professores adicionais. Não envia e-mail nem executa
transacções externas.

O script só aceita o PostgreSQL Neon `neondb/public` e exige confirmação dupla.
Mesmo assim, confirme no painel Neon que o endpoint corresponde a uma base
isolada de testes antes de qualquer `--apply`: uma ligação com o nome esperado
não prova, por si só, que o banco não é de produção. O reset apaga os dados das
tabelas escolares explicitamente permitidas, incluindo alunos, encarregados,
turmas, pagamentos, notas, avaliações e associações. Não remove nem altera
contas existentes na tabela `users`; verifica os registos das contas existentes
antes de confirmar a transacção. As configurações de classes, disciplinas e ano
lectivo são preservadas.

Pré-visualize a operação a partir da raiz do repositório:

```sh
SIGE_DEMO_RESET_CONFIRM=I_CONFIRM_DEVELOPMENT \
  python -m scripts.seed_demo_data --confirm-development-database neondb
```

Para aplicar, forneça também um caminho de credenciais fora do repositório e
com directório privado (permissões `0700`). Os dados são gravados numa única
transacção; qualquer falha provoca rollback.

```sh
SIGE_DEMO_RESET_CONFIRM=I_CONFIRM_DEVELOPMENT \
  python -m scripts.seed_demo_data \
  --confirm-development-database neondb \
  --students 1000 \
  --apply \
  --credentials-output /caminho/privado/sige-test-credentials.json
```

Não execute esse comando contra produção nem como parte do deployment. Os
e-mails usam o domínio reservado `.invalid`; contactos são valores sintéticos
não telefónicos. A senha temporária dos novos professores e encarregados fica
somente no ficheiro privado indicado.

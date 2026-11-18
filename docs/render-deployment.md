# Deployment no Render

O `render.yaml` configura um Web Service Python com Gunicorn, verificação HTTP
em `/`, cookies de sessão seguros e um disco persistente para anexos e rascunhos
de matrícula. A base de dados continua a ser PostgreSQL externo; o SQLite local
não é adequado para produção.

## Antes de publicar

1. Revogue a palavra-passe Neon que foi exposta e crie uma nova credencial.
2. No Render, ligue o repositório GitHub e use o Blueprint `render.yaml`.
3. Configure `DATABASE_URL` com a nova ligação PostgreSQL. Configure
   `DATABASE_URL_UNPOOLED` com a ligação directa ao mesmo banco, se as migrações
   precisarem de uma ligação sem pooler.
4. Configure `APP_BASE_URL` com o endereço HTTPS público da aplicação.
5. Configure as variáveis SMTP para activar recuperação por e-mail. Sem SMTP,
   o sistema não envia mensagens; a aplicação continua a iniciar.
6. Configure `SIGE_INITIAL_ADMIN_EMAIL` e `SIGE_INITIAL_ADMIN_PASSWORD` apenas
   se for necessário criar a primeira conta administrativa. Use uma senha forte
   e remova essas variáveis após a criação.

O `SECRET_KEY` é gerado pelo Render. Não substitua por um valor de exemplo e não
reutilize a credencial Neon comprometida. O disco persistente Render tem custo
associado e é montado em `/var/data`; não armazene uploads privados no disco
efémero do container.

## Migrações e arranque

As migrações não são executadas automaticamente no arranque ou em cada deploy.
Depois de confirmar o destino e fazer uma cópia de segurança, execute no
ambiente do serviço, uma única vez para a primeira configuração:

```sh
python -m flask --app app init-db
```

O comando inicializa ou actualiza o esquema de forma aditiva; não use
`migrate:fresh`, `DROP DATABASE` ou o gerador de dados como parte do deployment.
Operações de seed/reset são separadas e destinam-se exclusivamente a uma base
de teste isolada.

O Render inicia a aplicação com Gunicorn no endereço `0.0.0.0:$PORT`. A rota `/`
serve a página de login e é usada como health check. Confirme a página de login,
autenticação, acesso à base PostgreSQL e uploads após o primeiro deploy.

## Publicação via GitHub

Faça push da branch de trabalho para GitHub, conecte o repositório no dashboard
Render e crie o Blueprint a partir de `render.yaml`. Não envie `.env` nem
credenciais para GitHub. Só disponibilize o serviço depois de rotacionar a
credencial Neon exposta e configurar as variáveis obrigatórias no Render.

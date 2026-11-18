# Deployment no Render

O `render.yaml` configura um Web Service Python com Gunicorn, verificação HTTP
em `/`, cookies de sessão seguros e um disco persistente para anexos e rascunhos
de matrícula. A base de dados continua a ser PostgreSQL externo; o SQLite local
não é adequado para produção.

## Antes de publicar

O `.env` local é ignorado pelo Git, portanto não é enviado com o checkout que o
Render usa para executar Gunicorn. `python-dotenv` carrega o ficheiro local
durante o desenvolvimento; no Render, a aplicação recebe as mesmas
configurações como variáveis de ambiente do serviço.

Para reutilizar os valores já existentes sem os copiar para o repositório:

1. No Render, abra o Web Service e seleccione **Environment**.
2. Clique em **Add Environment Variable** e depois **Add from .env** (a
   [importação em lote do Render](https://render.com/docs/configure-environment-variables#adding-in-bulk-from-a-env-file)).
3. Seleccione o `.env` local existente e confirme a importação das variáveis.
   Isto importa os valores directamente para o ambiente privado do serviço; não
   publica o ficheiro nem cria uma nova `SECRET_KEY`.
4. Guarde com **Save, rebuild, and deploy** para reiniciar Gunicorn com os
   valores importados. A importação pode ser repetida quando o `.env` mudar.

O Blueprint declara `SECRET_KEY`, `DATABASE_URL`, `DATABASE_URL_UNPOOLED` e as
restantes configurações externas com `sync: false`. Assim, não gera uma chave
nova e não substitui os valores que foram importados do `.env` quando o
Blueprint é sincronizado. As definições de disco, caminhos e cookies continuam
a ser geridas pelo `render.yaml`.

Se o Web Service foi criado manualmente, a importação em **Environment** aplica
as variáveis directamente ao serviço existente; não é necessário recriar o
serviço. Se foi criado por Blueprint, sincronize a branch `deploy/render-setup`
e faça a mesma importação em **Environment**. O repositório não permite
determinar qual dos dois casos se aplica; confirme a origem do serviço no
dashboard Render. Em ambos os casos, a importação inicial do `.env` é a única
acção necessária no Render para disponibilizar os valores já existentes.

O Render fornece `PORT`; o comando Gunicorn liga-se a `0.0.0.0:$PORT`. Antes de
criar a aplicação Flask, o arranque verifica a presença de `SECRET_KEY` e,
quando detecta o Render, exige também uma URL PostgreSQL válida em
`DATABASE_URL` ou `DATABASE_URL_UNPOOLED`. As mensagens de erro não revelam
valores de configuração. O disco persistente Render tem custo associado e é
montado em `/var/data`; não armazene uploads privados no disco efémero.

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

Faça push da branch de deployment para GitHub e crie/sincronize o Blueprint
`render.yaml`. Importe o `.env` directamente na página **Environment** do
serviço Render. Nunca envie `.env` nem credenciais para GitHub. Se uma
credencial tiver sido exposta anteriormente, revogue-a antes de disponibilizar
o serviço.

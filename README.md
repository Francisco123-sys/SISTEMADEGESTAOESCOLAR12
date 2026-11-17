# Sistema Integrado de Gestão Escolar (SIGE) - Escola Secundária da Manga (Beira - Sofala)

## Persistência em Nuvem (Neon PostgreSQL)
O projecto está configurado para persistir todos os dados na nuvem através do **Neon PostgreSQL** (`DATABASE_URL` no ficheiro `.env`), garantindo que matrículas, pagamentos, pautas, sumários e utilizadores nunca sejam perdidos entre reinicializações ou deploys (Vercel, Render, Railway, VPS, etc.).

### Como Executar Localmente:
1. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
2. Copie `.env.example` para `.env` e configure `SECRET_KEY` e `DATABASE_URL`. Use uma chave aleatória forte e mantenha as credenciais fora do repositório.
3. Inicialize o esquema explicitamente no ambiente pretendido:
   ```bash
   python -m flask --app app init-db
   ```
   Este comando cria/actualiza as tabelas e os dados académicos iniciais. Para criar o primeiro administrador, configure `SIGE_INITIAL_ADMIN_EMAIL` e `SIGE_INITIAL_ADMIN_PASSWORD` (mínimo de 12 caracteres) no ambiente. Não são criadas contas com palavras-passe padrão. Não execute inicialização ou migrações em produção sem autorização e uma cópia de segurança verificada.
4. Inicie a aplicação:
   ```bash
   python app.py
   ```

## Funcionalidades Principais:
1. **Persistência PostgreSQL (Neon) + Fallback SQLite:** Suporte nativo para Neon Pooler (`DATABASE_URL`) e ligação directa (`DATABASE_URL_UNPOOLED`) para migrações/DDL.
2. **Bancos & Carteiras Móveis:** Seletor obrigatório com **Banco BCI, Millennium BIM, Standard Bank, M-Pesa e e-Mola**.
3. **Validação Estrita de Referência (9 Dígitos):** Aceita exclusivamente 9 dígitos numéricos (`\d{9}`), bloqueando duplicações e caracteres inválidos.
4. **Datas Distintas no Pagamento:** Regista tanto a **Data em que o Depósito foi Efectuado** quanto a **Data do Registo no Sistema**.
5. **Matrícula Condicionada ao Pagamento:** Exige o lançamento do pagamento/talão antes de gravar o aluno na base de dados (exceto alunos oficialmente isentos).
6. **Exportação de Listas Oficiais em `.DOC`:** Documento Word com o cabeçalho institucional da **Escola Secundária da Manga**.

## Matrículas e acesso dos encarregados
- A Secretaria pode registar e actualizar dados do aluno e do agregado familiar, associar turma e ano lectivo activo e anexar fotografia/documento de transferência.
- Fotografias são normalizadas e os documentos ficam em armazenamento privado, servidos apenas através de rotas autenticadas.
- As contas de encarregados são criadas/geridas pela Secretaria, podem ser associadas a vários educandos e não usam BI ou código de aluno como credencial. A redefinição assistida invalida sessões anteriores.
- O portal do encarregado requer autenticação, limita notas e reclamações aos educandos associados e permite alterar contacto e palavra-passe. Todos os formulários de alteração usam protecção CSRF; as acções de bloqueio, eliminação e logout são POST.
- A exportação PDF oferece lista de vitrine sem BI/contactos e lista oficial protegida por perfil.

## Testes
Execute os testes automatizados com:
```bash
python -m unittest discover -s tests -v
```

O acesso ao portal usa palavras-passe definidas pela Secretaria; ainda não existe recuperação autónoma por e-mail. Valide alterações ao esquema num banco PostgreSQL de teste isolado antes de qualquer migração em produção.

Para executar também os testes de integração PostgreSQL, use uma base efémera local chamada `sige_test` na porta `55432` e defina `SIGE_TEST_DATABASE_URL` e `DATABASE_URL` para essa base. Defina `DATABASE_URL_UNPOOLED` e `POSTGRES_URL_NON_POOLING` como vazias durante o teste: a inicialização do esquema dá prioridade a essas URLs não agrupadas quando estão configuradas no ambiente.

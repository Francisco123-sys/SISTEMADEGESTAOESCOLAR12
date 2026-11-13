# Sistema Integrado de Gestão Escolar (SIGE) - Escola Secundária da Manga (Beira - Sofala)

## Persistência em Nuvem (Neon PostgreSQL)
O projecto está configurado para persistir todos os dados na nuvem através do **Neon PostgreSQL** (`DATABASE_URL` no ficheiro `.env`), garantindo que matrículas, pagamentos, pautas, sumários e utilizadores nunca sejam perdidos entre reinicializações ou deploys (Vercel, Render, Railway, VPS, etc.).

### Como Executar Localmente:
1. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
2. Confirme que o ficheiro `.env` está na raiz do projecto com as credenciais do Neon PostgreSQL (já incluído no pacote).
3. Inicie a aplicação (as tabelas e perfis iniciais são criados automaticamente no PostgreSQL):
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

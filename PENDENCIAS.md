# Pendências

- Configurar e testar SMTP com as credenciais e certificados do ambiente de deploy; a recuperação por e-mail fica indisponível até essa configuração.
- Aplicar a migração de esquema PostgreSQL em janela autorizada para persistir os limites de autenticação; enquanto a tabela não existir, os limites são apenas por processo.
- Considerar CAPTCHA adaptativo e alertas de monitorização para tentativas suspeitas se o ambiente de produção justificar esse controlo adicional.

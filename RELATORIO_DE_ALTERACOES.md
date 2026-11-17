# Relatório de alterações

## Segurança e contas
- Removido o acesso ao portal do encarregado por BI/código; o acesso passou a exigir conta e palavra-passe.
- Implementadas associações entre encarregados e educandos, autorização por associação, protecção CSRF global e invalidação de sessões após redefinição de palavra-passe.
- Acrescentada alteração de palavra-passe pelo próprio encarregado, com validação da palavra-passe actual e mínimo de 12 caracteres.
- Criada gestão de contas e associações pela Secretaria.
- Acrescentados pedidos de recuperação por e-mail com resposta genérica, tokens guardados como hash, validade de 30 minutos, uso único e revogação de sessões.
- Acrescentados limites de tentativas para login e recuperação, usando buckets HMAC sem guardar IP/e-mail em claro.

## Matrículas e gestão escolar
- Ampliados os formulários de matrícula/edição com informação familiar, transferência e anexos.
- Acrescentados armazenamento privado de ficheiros, validação de PDF e normalização de fotografias.
- Actualizada a gestão de turmas/capacidade e a associação de aluno à turma, incluindo transferência manual.
- Acrescentadas exportações PDF de lista de vitrine e lista oficial protegida.
- Introduzidos contadores de códigos concorrentes para evitar colisões de identificadores.
- Corrigida a geração de números de recibo para ler `payments.receipt_number` (em vez de uma coluna `code` inexistente).
- Reforçada a validação de pagamentos/recibos e das notas submetidas; professores só podem consultar pautas das suas atribuições.
- Adicionados testes para áreas A/B/C nas classes 10.ª–12.ª e para filtros/ocupação.
- Convertidas as acções de bloqueio, eliminação e logout de utilizadores para POST protegido contra CSRF.
- Removido o bootstrap de contas padrão e tornada explícita a inicialização do esquema.

## Segurança operacional
- `.env` removido do índice do Git; `.env.example` contém apenas configuração de exemplo. A cópia local de `.env` não foi consultada nem apagada.
- Remover o ficheiro do estado actual do repositório não apaga versões anteriores do histórico. Se continha credenciais reais, estas devem ser substituídas.
- Nenhuma migração foi executada em Neon; a validação PostgreSQL usou apenas um container local efémero.

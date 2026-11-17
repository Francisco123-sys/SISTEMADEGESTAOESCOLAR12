# Relatório de alterações

## Segurança e contas
- Removido o acesso ao portal do encarregado por BI/código; o acesso passou a exigir conta e palavra-passe.
- Implementadas associações entre encarregados e educandos, autorização por associação, protecção CSRF global e invalidação de sessões após redefinição de palavra-passe.
- Acrescentada alteração de palavra-passe pelo próprio encarregado, com validação da palavra-passe actual e mínimo de 12 caracteres.
- Criada gestão de contas e associações pela Secretaria.

## Matrículas e gestão escolar
- Ampliados os formulários de matrícula/edição com informação familiar, transferência e anexos.
- Acrescentados armazenamento privado de ficheiros, validação de PDF e normalização de fotografias.
- Actualizada a gestão de turmas/capacidade e a associação de aluno à turma, incluindo transferência manual.
- Acrescentadas exportações PDF de lista de vitrine e lista oficial protegida.
- Introduzidos contadores de códigos concorrentes para evitar colisões de identificadores.
- Convertidas as acções de bloqueio, eliminação e logout de utilizadores para POST protegido contra CSRF.
- Removido o bootstrap de contas padrão e tornada explícita a inicialização do esquema.

## Segurança operacional
- `.env` removido do índice do Git; `.env.example` contém apenas configuração de exemplo. A cópia local de `.env` não foi consultada nem apagada.
- Remover o ficheiro do estado actual do repositório não apaga versões anteriores do histórico. Se continha credenciais reais, estas devem ser substituídas.
- Não foram executadas migrações em Neon, operações em dados persistentes, commits ou push.

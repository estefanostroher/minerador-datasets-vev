# Robô minerador de funções TypeScript

Projeto de pesquisa para construir, por mineração de repositórios públicos do GitHub, um dataset reproduzível de funções TypeScript com documentação comportamental original, testes unitários executáveis e implementação de referência.

## Estado do projeto

A especificação e o plano de implementação foram aprovados. A execução seguirá com subagentes e commits agrupados pelas seis etapas da mineração. A aplicação, a CLI e o ambiente Docker ainda não foram implementados.

- [Especificação aprovada](docs/superpowers/specs/2026-10-24-robo-minerador-design.md)
- [Plano de implementação](docs/superpowers/plans/2026-10-24-robo-minerador.md)

## Processo previsto

| Etapa | Responsabilidade |
| --- | --- |
| Descoberta | Pesquisar repositórios no GitHub e preservar consultas, resultados e commits |
| Triagem | Verificar acesso, licença, configurações e suporte de execução |
| Extração | Identificar implementações, documentação, chamadas diretas nos testes e contexto |
| Validação | Exigir três aprovações da suíte completa e pelo menos quatro casos válidos por função |
| Deduplicação | Consolidar implementações de código textualmente idêntico e preservar proveniências |
| Construção | Exportar JSONL, entradas para uso futuro, estatísticas e amostra para inspeção manual |

A aplicação será modular e escrita em Python. Um auxiliar Node.js/TypeScript com **ts-morph**, baseado na TypeScript Compiler API, fará a análise estrutural e a resolução de símbolos. Adaptadores atenderão Jest e Vitest. SQLite armazenará o estado das coletas; arquivos preservarão fontes e evidências.

## Reprodução prevista

Docker Compose disponibilizará as ferramentas do minerador. Cada repositório será instalado e validado em ambiente Node.js isolado, com versões, dependências, comandos e configurações registrados. A primeira falha de instalação, compilação ou execução da suíte excluirá o repositório, sem ajustes corretivos.

O plano contém a política proposta de seleção de ambientes, a estrutura de arquivos e os comandos de verificação que serão disponibilizados durante a implementação. Esses comandos ainda não constituem funcionalidades executáveis deste repositório.

## Escopo científico

Popularidade, README, cobertura e complexidade serão registrados sem novos limiares de exclusão. A inspeção manual será preparada por amostragem reproduzível e realizada pelo pesquisador.

Mutação, categorização comportamental dos testes, geração por LLMs e comparação entre geração direta e guiada ficam para trabalhos futuros. A existência de testes nos projetos não comprova adoção histórica de TDD.

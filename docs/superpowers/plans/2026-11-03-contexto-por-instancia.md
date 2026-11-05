# Contexto por instância — plano de implementação

> **Histórico:** este ajuste não liberou instâncias da coleta auditada. A política de contexto foi substituída pelo [contrato v3](../specs/2026-11-03-snapshot-reutilizavel-design.md); consulte as [evidências finais](2026-11-04-extracao-v3-evidencias.md). O registro abaixo permanece como evidência da etapa anterior.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar bloqueios globais de contexto comprovadamente independentes, preservando pendências reais e encaminhando somente instâncias aprovadas à validação.

**Architecture:** Um índice de impacto no auxiliar Node percorre as dependências das evidências de cada instância e distingue problemas locais de problemas globais ou desconhecidos. O validador Python confere a compatibilidade das aprovações com esse contexto, sem aceitar uma promoção por simples remoção da restrição global. O snapshot e a publicação v2 mantêm seus contratos.

**Tech Stack:** Python, pytest, Node >=22, TypeScript Compiler API 6.0.3, node:test, JSONL v2.

---

## Restrições

- Trabalhar no checkout atual, sem branches, worktrees, commits ou push automáticos.
- Não executar código/configurações/testes dos candidatos, instalar suas dependências ou acessar a rede para uma nova coleta.
- Ler `results/03_extracao.jsonl` apenas para comparação em memória; preservar bytes e SHA-256 do arquivo.
- Preservar IDs, textos, critérios, estados, valores de validação e ordenação. Não ampliar H-F06.
- Comentar/documentar todas as funções e callbacks novos em português; identificadores em inglês.
- Testes focados em riscos concretos de seleção, associação e integridade, sem meta de cobertura ou obrigação de TDD.
- Revisão de aderência antes da revisão de qualidade; corrigir e revisar novamente os problemas encontrados.

## Mapa de arquivos

- Novo módulo focado em `src/robo_minerador/typescript/`: índice de dependências e avaliação de impacto; evitar concentrar a política inteira em `analyze.mjs`.
- `src/robo_minerador/typescript/analyze.mjs`: integrar a avaliação por instância aos critérios, sem alterar seleção de unidades e artefatos.
- `src/robo_minerador/typescript/virtual-project.mjs`: expor informações já conhecidas de configuração/declarações se necessárias ao índice.
- `src/robo_minerador/typescript/test-associations.mjs`: localizar incertezas de teste somente quando seu alcance puder ser determinado sem enfraquecer mocks/identidade.
- `src/robo_minerador/extraction_analyzer.py`: validar aprovações por dependências relevantes, preservando o protocolo estrito.
- `src/robo_minerador/typescript/tests/context-impact.test.mjs` e `tests/test_extraction_analyzer.py`: regressões de impacto e protocolo.
- `README.md`, especificação aprovada e este plano: contrato final e evidências verificadas.

## Tarefa 1 — Implementação integrada Node/Python

**Responsável:** subagente implementador com propriedade dos módulos do analisador e seus testes. Coordenador não edita esses arquivos durante a delegação.

- [x] Reproduzir com fixture uma unidade aprovada que fica pendente apenas por um import ausente em um módulo externo independente:

```js
const files = {
  'src/a.ts': '/** Incrementa a entrada. */\nexport function increment(x:number) { return x + 1; }',
  'src/a.test.ts': "import { test } from 'vitest'; import { increment } from './a'; test('local', () => increment(1));",
  'other/b.ts': "import missing from './absent'; export const unrelated = missing;",
};
```

Esperado após a correção: `increment.decision === 'approved'`, com referência ausente e issue ainda registradas no repositório. Mudar o import ausente para o arquivo de implementação, teste associado ou dependência transitiva deve manter `preserved_local_context.status === 'not_evaluated'`.

- [x] Construir índice compartilhado por origem das dependências e calcular o fechamento transitivo das raízes da implementação, documentação e testes associados, com conjunto de visitados para ciclos. Incluir configuração efetiva, manifests e declarações ambientes que possam afetar resolução. Não confundir snapshot publicado com escopo de impacto.
- [x] Tratar issues relevantes e de alcance desconhecido como impeditivas; ignorar para a instância somente issues comprovadamente independentes. Caminho externo ao diretório ou extensão de recurso não são, isoladamente, prova de independência. Recursos referenciados e acesso dinâmico desconhecido continuam conservadores.
- [x] Separar completude de busca de presença de evidência. Ausência de documentação/testes só pode ser rejeitada se não houver omissões ou incertezas que possam esconder evidência pertinente. Preservar os bloqueios existentes para mocks, registro incerto e identidade ambígua.
- [x] Trocar no Python a proibição global de referências não resolvidas por uma conferência das dependências necessárias da instância. Se for necessário acrescentar evidência ao protocolo, validar campos, caminhos, completude e coerência nos dois lados; não confiar em uma lista arbitrária fornecida pelo auxiliar.
- [x] Acrescentar regressões para referências triple-slash, dependências transitivas de testes, arquivos globais/ambientes, configurações ancestrais/extends, omissões de documentos/testes, recursos dinâmicos e issues sem alcance conhecido. Proteger explicitamente a rejeição de protocolo adulterado que tente aprovar uma dependência necessária ausente.
- [x] Executar `npm test --prefix "src/robo_minerador/typescript"` e `.\.venv\Scripts\python.exe -m pytest tests/test_extraction_analyzer.py tests/test_extraction.py tests/test_extraction_format.py -q`; relatar comandos, resultados e decisões conservadoras ao coordenador.
- [x] Subagente independente revisa aderência à especificação. Corrigir problemas e repetir a revisão.
- [x] Outro subagente independente revisa qualidade, complexidade, falsos positivos, custo do índice e protocolo. Corrigir problemas e repetir a revisão.

## Tarefa 2 — Comparação dos snapshots existentes

**Responsável:** subagente de auditoria, após implementação aprovada, com escrita somente em arquivos temporários fora do projeto.

- [x] Registrar tamanho e SHA-256 do JSONL antes da auditoria; recuperar os dez grupos repository/instance e expandir as instâncias v2 com o catálogo de motivos.
- [x] Reconstruir as issues originais agrupadas, removendo somente os metadados da compactação e expandindo as ocorrências por caminho. Distinguir issues de coleta das geradas pelo próprio analisador, para não realimentar limitações antigas como falhas de coleta.
- [x] Reanalisar `repository.context.files` no auxiliar confiável, passando apenas as issues de coleta reconstruídas. Usar `Analyzer` para validar o novo protocolo, sem executar os arquivos candidatos.
- [x] Conferir mesma quantidade/ordem de unidades, IDs recalculados com os mesmos ordinais, código/documentação/testes preservados e validação não executada. Quando uma associação ou critério mudar legitimamente, registrar o motivo e inspecionar o exemplo em vez de forçar igualdade artificial.
- [x] Contar transições gerais e das 191 instâncias anteriormente pendentes apenas em contexto. Relatar aprovadas, rejeitadas, pendentes e motivos restantes, sem exigir aprovação mínima.
- [x] Conferir tamanho e SHA-256 finais idênticos aos iniciais. Não publicar uma nova coleta oficial nem criar comando de reanálise offline nesta entrega.

## Tarefa 3 — Documentação e verificação final

**Responsável:** coordenador, sem sobreposição com os arquivos delegados.

- [x] Atualizar README e especificação para explicar impacto por instância, limitações conservadoras e encaminhamento exclusivo de `approved`.
- [x] Registrar aqui as evidências reais da auditoria, distinguindo replay offline de nova coleta e critérios estáticos de execução da suíte.
- [x] Executar `.\.venv\Scripts\python.exe -m pytest -q`, `npm test --prefix "src/robo_minerador/typescript"` e `git diff --check` após as correções finais.
- [x] Revisar diff e estado do Git; apresentar arquivos alterados, verificações e efeito observado, sem commit ou push automático.

## Revisão do plano

Cobertura conferida: impacto local/global, configuração/ambiente, buscas negativas, mocks, protocolo Python, IDs/v2 e auditoria somente leitura têm tarefas explícitas. A implementação não assume que as 191 candidatas se tornarão aprovadas. Pendências não entram automaticamente na validação.

## Execução e revisões — 2026-11-03

Implementação delegada concluída nos módulos `context-impact.mjs`, `analyze.mjs`, `virtual-project.mjs` e `extraction_analyzer.py`, com regressões Node e Python. `test-associations.mjs` não precisou de alteração; a localização das incertezas usa as identidades já fornecidas pelo coletor.

A revisão independente de aderência encontrou dois riscos de aprovação indevida: dependências de configuração de testes e leitura/alteração de ambiente por `process`. Ambos foram reproduzidos, corrigidos e revisados novamente, com aprovação. A revisão de qualidade encontrou carregadores CommonJS indiretos (`module.require`, aliases e `createRequire`); a correção conservadora e as regressões também receberam aprovação na re-revisão. A revisão final de integração solicitou o fechamento deste registro documental, sem identificar outro bloqueador de código.

Não há campos novos no protocolo. Novas informações usam as listas existentes, com os códigos `UNRESOLVED_AMBIENT_REFERENCE`, `UNRESOLVED_CONFIG_TYPES`, `UNRESOLVED_PROJECT_REFERENCE` e `UNPROVEN_RESOURCE_SCOPE`. O Python repete a análise confiável quando existe contexto aprovado. A repetição confere também a omissão adulterada de arestas/diagnósticos e compartilha os controles de cancelamento/heap/timeout do analisador.

### Verificações executadas

| Executor | Comando | Resultado |
| --- | --- | --- |
| Implementador, após as correções | `.\.venv\Scripts\python.exe -m pytest tests/test_extraction_analyzer.py tests/test_extraction.py tests/test_extraction_format.py -q` | 94 passaram, 32,05 s |
| Revisor de qualidade, re-revisão | Testes Python de `test_extraction_analyzer.py` | 46 passaram |
| Coordenador, código final | `.\.venv\Scripts\python.exe -m pytest -q` | 351 passaram, 33,40 s |
| Coordenador, código final | `npm test --prefix "src/robo_minerador/typescript"` | 54 passaram, zero falhas |
| Coordenador e revisores | `git diff --check` | Sem erros de whitespace; avisos LF/CRLF do checkout Windows |

Esses testes executam o auxiliar confiável sobre fontes virtuais. Não executam os testes dos repositórios candidatos.

### Replay dos snapshots, sem nova coleta

Entrada: `results/03_extracao.jsonl`, execução iniciada em `2026-11-03T02:46:50Z`. Dez repositórios e 2.844 arquivos preservados. A auditoria refez a análise somente dos textos existentes, passando as issues originais de coleta reconstruídas; os diagnósticos antigos do Node foram recalculados, não realimentados como falhas de coleta.

| Decisão | Antes | Depois |
| --- | ---: | ---: |
| `approved` | 0 | 0 |
| `rejected` | 141 | 141 |
| `not_evaluated` | 7.028 | 7.028 |
| Total | 7.169 | 7.169 |

Das **191 instâncias pendentes apenas por contexto, todas continuaram pendentes**. Todas coexistem com omissões de coleta e `UNPROVEN_RESOURCE_SCOPE`. Atribuição múltipla de bloqueios globais, não somável: `UNINSPECTED_CONTEXT` afeta as 191; `UNRESOLVED_CONFIG_TYPES`, 174; `MULTIPLE_TSCONFIGS`, 151; `BINARY_CONTEXT`, 118; e `content_limit`, dez. Remover apenas um desses bloqueios não comprova contexto completo.

As 7.169 unidades mantiveram ordem, IDs, código, assinaturas, documentação, testes, valores de validação e caminhos de contexto. Os critérios e suas justificativas também permaneceram iguais. Houve novas arestas legítimas de configuração herdada e autorreferências locais antes tratadas como externas passaram a referências não resolvidas; essas diferenças ficaram registradas na auditoria.

A expansão v2 conferiu 35.845 referências a motivos, 12 definições, 7.169 referências de contexto e 53 referências diagnósticas de issues. O hash do conteúdo dos 2.844 arquivos foi verificado. A fixture de independência aprova corretamente o alvo, mas **a correção não liberou instâncias desta coleta**; seus bloqueios restantes exigem trabalho adicional de coleta/resolução ou comprovação de alcance, fora desta entrega.

Análises somadas: 255,07 s; auditoria completa: 264,05 s. Não houve `AnalyzerUnavailable` na execução completa. Nenhum contexto foi aprovado nesse corpus, portanto a segunda análise condicional de conferência não foi acionada nele. Uma primeira tentativa atingiu o timeout externo do terminal; a auditoria completa foi repetida com prazo externo ampliado, sem mudar os limites do produto.

Arquivo de resultados antes e depois:

```text
58.524.958 bytes
SHA-256: 0ada94ae6000a42c78652d432b2bb35d3075d2b44f62d17056feafc9f5c34187
```

Artefatos locais de auditoria, fora do projeto, em `C:\Users\Estefano\AppData\Local\Temp\opencode\`:

- `audit_context_20261103_summary.json`: resumo, tempos e transições; SHA-256 `698fb6355fb832a554819ca04bb28d9c5387ad32a8e3cbeacff467e573557fe6`.
- `audit_context_20261103.json`: detalhes por instância, incluindo as 191 candidatas.
- `audit_context_20261103.py` e `verify_audit_context_20261103.py`: replay e conferência independente.
- `audit_context_20261103_before.json` e `audit_context_20261103_after.json`: manifestos de integridade.

Esses arquivos temporários não são dependências do produto. Não houve commit/push, nova coleta ou alteração dos resultados do pesquisador.

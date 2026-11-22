# Resolução automática de receitas de validação

**Data:** 2026-11-16. **Estado:** implementação aprovada e realizada; evidências e histórico das revisões estão no plano. Execução real e substituição do 04 continuam no gate manual.

**Fonte:** requisitos da conversa e investigação do snapshot, incluindo pesquisas dos agentes, confrontados com os contratos do checkout. Nenhum candidato foi executado para produzir este documento.

**Plano:** [tarefas de implementação](../plans/2026-11-16-resolucao-automatica-receitas.md).

## 1. Problema e resultado esperado

O catálogo contém seis receitas indexadas por `(repository_id, commit_sha)`. `resolve_recipe`, em `validation_recipe.py`, não cria receitas: devolve `RECIPE_NOT_FOUND` quando não encontra a chave. A extração aprovada não substitui os pré-requisitos dinâmicos.

Na execução problemática informada, a única origem foi `1460677238`, `https://github.com/abdoulhamiddiallo/liquidity-watch-fabric`, SHA `42d2f51a9cc06f1c966a393b9dc44200b34a1e08`. As 13 funções aprovadas na extração ficaram `not_evaluated` na validação por receita ausente; o 04 terminou `failed`. Isso não demonstra falha científica das funções nem autoriza aprová-las.

A mudança deverá tentar resolver automaticamente apenas ausências legítimas, usando dados de um snapshot local verificado e uma política versionada de ambientes. Uma receita resolvida autoriza tentar a passagem original, não aprova repositório ou instância. Incertezas continuam `not_evaluated`; decisões e fechamento continuam derivados dos avaliadores existentes.

### Critérios de aceitação

- Receitas cadastradas prevalecem, inclusive receitas pendentes e pnpm. Seus hashes, conteúdo, evidências e fluxo permanecem compatíveis.
- O fallback só entra após `RECIPE_NOT_FOUND`, com ID/SHA/URL coerentes; não contorna conflito de identidade ou catálogo inválido.
- Entradas idênticas, catálogo idêntico e política idêntica produzem receita, hashes e proveniência idênticos, sem relógio, rede ou tags móveis na geração.
- Limites do `run_started` coincidem com a receita publicada; sem receita, são `null`.
- Não se retêm contextos completos de todos os repositórios na pré-resolução. O loader original continua obrigatório, uma vez por grupo na execução.
- O alvo não recebe garantia de aprovação. Seu trio só poderá ser habilitado após testes reais do instrumento, instalação frozen, censo, bootstrap e regressões comportamentais.
- O 04 v3, leitores portáteis, replay histórico e dataset preservam a proveniência, sem exigir snapshot, rede ou Node para validar os novos vínculos declarativos.
- Não há sétima saída, parâmetro manual novo, receita gravada no catálogo por execução, retry corretivo ou mudança do gate científico.

## 2. Escopo e alternativas

### A — Resolvedor híbrido por snapshot (escolhido)

Consultar o catálogo primeiro; na ausência legítima, analisar metadados limitados do snapshot, construir uma receita no contrato atual e persistir sua origem no 04. Custo médio: nova análise estática, política de imagens e extensão aditiva de proveniência. Evita manutenção por repositório e mantém exceções explícitas no catálogo. O risco é inferir escopo ou compatibilidade além das evidências; regras fechadas e fixtures negativas limitam esse risco.

### B — Expansão manual do catálogo

Adicionar uma receita revisada por ID/SHA para cada origem nova. Custo inicial menor e decisões explícitas por projeto, mas manutenção cresce com os candidatos e SHAs; não resolve automaticamente futuras ausências. Também exige validar o novo trio do instrumento: uma receita manual não elimina esse requisito.

| Aspecto | Híbrido | Catálogo manual |
| --- | --- | --- |
| Esforço inicial | Médio | Pequeno por origem |
| Experiência | Automática nos casos cobertos | Intervenção a cada ausência |
| Manutenção | Política e regras compartilhadas | Receitas por identidade |
| Flexibilidade | Limitada deliberadamente ao subconjunto comprovado | Exceções explícitas mais amplas |
| Risco | Inferência estática indevida | Receita manual indevida ou desatualizada |

Recomendação e decisão já aprovadas: A, conservando B como precedência e mecanismo de exceções. Não introduzir um executor alternativo nem expandir todos os gerenciadores nesta entrega.

## 3. Código existente e fronteiras

- `validation_recipe.py`: parsing JSON estrito do catálogo, `_validate_recipe`, hash canônico `_hash` e resolução exata. Reutilizar o validador; não manter dois formatos de receita.
- `validation.py:90–103`: hoje publica limites do catálogo antes de resolver e carregar o contexto. A resolução automática precisa anteceder essa abertura.
- `extraction_context.py:load_repository_context`: exige extração v3, reabre snapshot, refaz projeção e AST e compara contexto, identidade e evidências originais. Não substituir por uma projeção reduzida.
- `snapshot_store.py`: `open_verified`, `list_entries`, `open_reader` e `read_blobs` oferecem acesso offline com OIDs fixados. Verificações repetidas de integridade são aceitáveis nesta entrega; não acessar `_entries` ou armazenamento privado para otimizar.
- `validation_instrument.py:configure_instrument`: inventaria dependências efetivamente instaladas, verifica locks e exige trio em `MATRIX`. A análise do lock não substitui essa negociação.
- `typescript/validation-bootstrap.mjs`: hoje exige pool efetivo `forks`, browser desabilitado e ausência de runner customizado não suportado. Não força esses valores; uma ampliação exige prova própria.
- `validation_evaluation.py`: `PAIR_POLICY` usa `function_original_tests_behavior`, mínimo de quatro casos positivos, uma passagem, sem retries corretivos e recursos `diagnostic_only`. Preservar essa política.
- `validation_format.py`: verifica receita integral, hash, identidade, ambiente e igualdade dos limites da abertura. Validar a nova proveniência neste caminho, sem importar o analisador Node.
- `validation_replay.py`: reproduz evidências históricas v2 em v3, não gera receita nova. Preservar seu domínio atual.
- `dataset.py:56` e `dataset_format.py:106–107`: o ambiente já contém `repository['recipe']`, que é o envelope completo, e a evidência de repositório também está incorporada. Não é necessário criar um campo paralelo de proveniência.

## 4. Resolução e ordem de execução

### 4.1 Precedência e identidade

1. Validar/carregar o catálogo como hoje. Catálogo malformado continua erro de configuração, não ausência.
2. Chamar `resolve_recipe`. Se houver receita, devolver o envelope existente sem acrescentar proveniência automática, mesmo que `not_evaluated`.
3. Se houver erro de URL, não tentar fallback. Na coordenação, publicar diagnóstico operacional do grupo sem executar candidato; não reinterpretar o erro como `RECIPE_NOT_FOUND`.
4. Somente para `RECIPE_NOT_FOUND`, conferir a identidade da origem da extração e da referência do snapshot. Quando o catálogo já conhece o ID em outro SHA, exigir a mesma URL segundo a comparação atual sem distinção de caixa. URL já associada a outro ID é conflito. Preservar a URL original da publicação na receita.
5. Resolver pelo snapshot ou produzir pendência explicável. Não adquirir snapshot nem escolher outro SHA.

### 4.2 Pré-resolução compacta

Antes de emitir `run_started`, processar os grupos sequencialmente. Cadastrados não usam o novo analisador. Para ausentes, abrir a referência com `SnapshotStore.open_verified`, obter inventário verificado e usar `open_reader` e `read_blobs` em lotes limitados para manifests, locks, configurações, documentação e CI relevantes. Não chamar `load_repository_context` nessa fase; não executar nem importar módulos candidatos.

Guardar por grupo apenas envelope da resolução, diagnósticos compactos e referência/hashes do snapshot. Descartar bytes, inventário, AST e reader ao concluir o grupo. O inventário temporário continua sujeito aos limites existentes do store. O consumo de memória não cresce com a soma dos blobs de todas as origens.

Emitir a abertura com `resolution.recipe.limits`, ou `null` quando `recipe` for `null`. Durante a execução de cada grupo, chamar o loader original uma única vez, incluindo grupos pendentes, preservando a verificação de extração. Antes de `prepare_workspace`, exigir igualdade completa entre o snapshot verificado pelo loader e o usado pela receita automática. Divergência impede instalação e vira diagnóstico operacional.

### 4.3 Falha de contexto depois do planejamento

Contrato fechado, sem novo campo de schema: `provenance.snapshot` identifica o **snapshot da análise**, não comprova que o loader da extração concluiu. Guardar o retorno do loader em variável temporária; atribuir `execution.verified` somente depois da comparação completa. Se o loader lançar erro ou retornar referência diferente, descartar esse retorno e manter `execution.verified` ausente. Publicar `repository.snapshot=null`, conservando a receita planejada, seu status `resolved`, seu hash, a proveniência e os limites já anunciados. Não refazer a resolução nem alterar a abertura.

Nesses dois casos, não chamar `prepare_workspace`, `configure_instrument`, fases do executor ou qualquer comando candidato. Preflight global/limpeza da infraestrutura não são comandos candidatos. Exigir no 04:

- Todas as fases têm `status='not_started'`, `start=end=null`, `commands=[]`; `not_started_commands` enumera os comandos planejados e a causa. `required` continua derivado da receita.
- `environment={}`, `lockfiles=[]`, `unit_suite={}`; nenhuma contagem, testemunho, censo ou evidência dinâmica aprovada. Repositório e todas as instâncias ficam `not_evaluated`.
- `reproducible_environment`, `dependency_installation`, `required_preparation` e `complete_unit_suite` ficam `not_evaluated`, inclusive quando o preparo planejado é vazio. `unit_suite_scope` pode continuar aprovado **como classificação estática**, nunca como prova de execução. Todos os critérios das instâncias, incluindo `execution_prerequisites`, ficam `not_evaluated`.
- Um diagnóstico operacional obrigatório do mesmo repositório tem `phase='context'`, código `RECIPE_CONTEXT_UNAVAILABLE` ou `RECIPE_CONTEXT_MISMATCH`, mensagem não vazia e `evidence={analysis_snapshot, observed_snapshot, cause}`. `analysis_snapshot` é igual à proveniência; `observed_snapshot=null` no erro do loader e referência completa diferente no mismatch; `cause={code,message}` com strings não vazias. O snapshot divergente aparece só nesse diagnóstico, nunca como snapshot verificado de execução.

O validador local de repositório exige essas restrições para admitir receita automática presente e snapshot nulo. O leitor do 04 completo exige ainda o diagnóstico correspondente e sua coerência; removê-lo ou adulterá-lo invalida a publicação. Com snapshot de execução presente, exigir igualdade completa com o snapshot da análise. Assim, erro/mismatch gera 04 publicável sem permitir aprovação nem mascarar `failed`. Ajustar a derivação de preparo no avaliador para não emitir `PREPARATION_NOT_REQUIRED` aprovado quando a verificação de contexto automática falhou; não mudar os critérios ou a política científica.

Falhas de pré-resolução pertencem ao grupo e não abortam outros grupos. Capturar exceções operacionais esperadas com código e evidência; não ocultar falha de configuração global. Um envelope pendente sem receita tem `recipe_sha256=null`. Nunca construir receita incompleta que viole `_validate_recipe` apenas para preencher limites. Se todos continuarem pendentes, `run_result` poderá continuar `failed`; o pipeline não deverá avançar sobre `failed` por conveniência.

O fallback pertence ao caminho atual v3, `policy_version=2`. O caminho explícito histórico `policy_version=1` conserva resolução pelo catálogo e não ganha geração automática retroativa.

## 5. Análise determinística e limites

### 5.1 Subconjunto inicial

Suportar somente um pacote npm no repositório: na raiz ou aninhado, sem workspaces, sem fronteira de pacote ambígua e sem múltiplos gerenciadores. O inventário completo determina a quantidade de `package.json`, configurações e entradas de teste; não escolher o primeiro diretório encontrado. Os caminhos devem ser POSIX relativos canônicos e contidos no snapshot. Gitlinks, links em entradas necessárias, caminhos indecodificáveis relevantes ou limites excedidos produzem pendência.

Inventariar **todos** os locks do repositório, sem excluir fixtures, vendor ou diretórios sem manifesto. Reconhecer por basename com casefold: `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lock`, `bun.lockb`. Variações de caixa do nome canônico ou colisões de caixa são ambíguas. Qualquer lock fora do diretório exato do pacote — raiz/ancestral, irmão, descendente, fixture ou vendor — produz `AUTO_RECIPE_INPUT_UNSUPPORTED`, mesmo havendo exatamente um package.json. Um lock npm em filho sem manifesto também é ambíguo. Não ignorar locks externos ao pacote.

No diretório exato do pacote, aceitar somente locks npm canônicos; selecionar `npm-shrinkwrap.json` antes de `package-lock.json`. Se ambos existirem, registrar ambos nos inputs, mas congelar apenas o lock efetivo escolhido pelo npm. Validar os dois como JSON estrito lock v3, com `name` de topo e de `packages['']` como strings não vazias e `version` em ambos igual à do manifesto, além de gerenciador e resolução efetiva coerentes. Nomes divergentes não bloqueiam: reprodução sintética com Node 22.23.4/npm 10.9.9, imagem digest 539d, `npm ci --offline --no-audit --no-fund`, versões iguais e nomes divergentes concluiu sem alterar o hash do lock. Divergência de versão, versões efetivas de Vitest/Vite ou engines conflitantes produz pendência; não usar a precedência para esconder incoerência. Locks de outros gerenciadores no mesmo diretório também produzem pendência. v1/v2, lock ausente, JSON inválido, chaves duplicadas, constantes não JSON, resolução local/link/workspace ou versões efetivas ambíguas ficam pendentes. Não escolher versão pela range do `package.json` quando o lock não a determina.

Ordenar os caminhos de locks por bytes UTF-8. Ler e hashear todos os locks identificados que sejam blobs regulares dentro dos orçamentos, inclusive os que causam pendência; guardar esses hashes nos inputs da tentativa. Se um lock não puder ser lido por link, caminho ou orçamento, registrar caminho/código no diagnóstico, sem inventar hash nem tomar decisão positiva. A ordem fixa de leitura e os limites garantem diagnóstico determinístico.

Resolver Vitest e Vite efetivos pelo lock, respeitando o caminho da instalação utilizada pelo pacote. Instalações concorrentes sem resolução inequívoca não são suportadas. Validar `engines.node` de todos os pacotes com restrição no lock, inclusive Vite, Vitest e jsdom, além de `engines` e `packageManager` do manifesto. `.nvmrc` e `.node-version`, na raiz e no pacote, admitem somente uma versão exata, com prefixo `v` opcional e newline final opcional; todos devem concordar com o perfil. Pins conflitantes ou formatos não reconhecidos ficam pendentes. `packageManager` npm deve ter versão exata coerente com a imagem. CI segue o corte conservador da seção 5.2.2, sem extrair pins parcialmente.

Novos pnpm, yarn, bun, Jest, Mocha, `node:test`, múltiplos pacotes e workspaces estão fora da geração automática inicial. Receitas cadastradas para esses casos não são reclassificadas.

### 5.2 Configuração e classificação de escopo

O auxiliar confiável `typescript/validation-recipe-analysis.mjs` usará TypeScript 6.0.3 para reconhecer dados de configurações, sem `eval`, import dinâmico ou execução de config. Formas iniciais: imports conhecidos de `vitest/config` ou `vite`, `export default defineConfig` com objeto literal; `test.environment`, `setupFiles`, `globals`, `include`, `exclude`, `pool`, `isolate` e alias estáticos. Permitir o modelo específico `resolve(import.meta.dirname, 'src')` importado de `node:path` ou `path`, traduzindo-o apenas em caminho relativo verificado. Identificadores, spreads, funções, plugins ou resoluções fora dos modelos conhecidos não são avaliados: pendência com localização e razão.

Regra de escopo `single_npm_local_vitest_v1`:

1. Um pacote e lock inequívocos; script `test` exatamente `vitest run` ou `vitest run --config CAMINHO`, com CAMINHO relativo literal canônico. Sem filtros de testes, shell, delegação, projetos ou flags que mudem ambiente/pool/seleção.
2. Config explícita reconhecida prevalece; na descoberta padrão selecionar uma única `vitest.config.*`, ou, se ausente, uma única `vite.config.*`. Múltiplas extensões da classe efetiva ficam pendentes; todas as configs lidas mantêm hashes nos inputs, e imports não modelados da selecionada continuam pendentes. A precedência foi reproduzida com Vitest 4.1.11: config Vitest válida passou quatro testes enquanto config Vite lançava sentinel; sem a config Vitest o sentinel falhou. Sem browser habilitado, runner customizado, workspace/projetos, global setup dinâmico ou seleção incerta. Incluir a suíte original inteira, não apenas os testes associados às candidatas.
3. Inventariar todas as entradas compatíveis com a descoberta padrão do Vitest ou com os padrões literais reconhecidos. A primeira versão aceita defaults e listas de caminhos literais; globs customizados não modelados ficam pendentes. Não omitir entradas além de fronteiras de pacote.
4. Cruzar script, configuração, inventário e confirmação documental pela gramática fechada abaixo; CI existente impede resolução automática nesta primeira versão. Testes locais e de componentes headless são elegíveis; jsdom, setup e alias não constituem veto. Marcadores de suíte mista/browser (`playwright.config.*`, `cypress.config.*`, diretórios/seletores E2E, scripts de browser) exigem separação inequívoca; a primeira versão não resolve essa separação e fica pendente.
5. Registrar caminhos, regra, heading e linhas da confirmação documental. Não exigir a palavra literal “unitário”, nem concluir unidade só pelo nome Vitest. Ausência de confirmação ou ambiguidade usa `UNIT_SCOPE_UNRESOLVED`; ausência de marcador E2E não é prova universal de ausência de integração ou serviços.

Essa é uma classificação limitada da suíte local original, não uma análise completa de efeitos de todos os testes. O censo e os vínculos da passagem original continuam obrigatórios. Recursos são diagnóstico na política atual; não reintroduzir `environment=node`, auditoria histórica ou ausência absoluta de serviços como novo gate. Uma suíte mista conhecida ou fronteira desconhecida continua pendente, sem converter dúvida em rejeição científica.

### 5.2.1 Gramática documental normativa inicial

**Caminhos e seleção:** considerar somente `README.md` e `docs/testing.md` relativos à raiz do repositório e ao diretório do pacote. Deduplicar caminhos quando o pacote for a raiz. Comparar cada caminho relativo por Unicode NFC seguido de casefold; aceitar uma única correspondência por caminho lógico. Duas grafias concorrentes são `UNIT_SCOPE_UNRESOLVED`. Todos os documentos existentes dessa lista entram na análise e nos inputs; não escolher um documento favorável e ignorar outro. Ausência de todos ou ausência de confirmação no conjunto é pendência. Documentos fora da lista não são interpretados como instruções de teste.

**Headings:** normalizar newlines CRLF/CR para LF apenas no parser, preservando hash dos bytes originais. Reconhecer somente headings ATX em coluna zero, pela regex `^(#{1,6})[ \t]+(.+?)[ \t]*$`, fora de fences. Remover do título o sufixo que corresponda a `[ \t]+#+[ \t]*$`; normalizar o restante com Unicode NFC, casefold, trim e colapso de espaços/tabs para um espaço. Títulos aceitos por igualdade exata: `quality`, `qualité`, `qualidade`, `test`, `testing`, `tests`, `tests unitaires`, `unit tests`, `testes`. Para os três títulos de qualidade, admitir também um único prefixo `✅`, com U+FE0F opcional e espaço obrigatório, motivado pelo heading real `## ✅ Qualité`. Não retirar acentos nem interpretar outros sinônimos, prefixos, HTML, links ou ênfase no título. A seção vai até o próximo heading ATX de nível menor ou igual, ou EOF; inclui subseções. Seções aceitas sobrepostas são uma única união de intervalos, sem duplicar comandos. Setext/HTML não confirmam escopo.

Para localizar headings sem confundir exemplos de Markdown com seções, o scanner identifica fences em todo o documento: abertura após zero a três espaços com sequência de pelo menos três backticks ou tildes; fechamento só com o mesmo caractere, sequência de comprimento maior ou igual e whitespace restante. Não reconhece headings no conteúdo desses fences. Fence não fechado torna o documento ambíguo (`UNIT_SCOPE_UNRESOLVED`). Isso não amplia a gramática de **comandos**: dentro de seção aceita, só o fence de três backticks em coluna zero descrito a seguir pode confirmar comandos; as demais formas são ambíguas. Fora das seções, fences fechados são opacos, inclusive os de deploy.

**Unidades de comando:** dentro desses intervalos, reconhecer fences de três backticks em coluna zero, abertura com linguagem vazia ou uma entre `sh`, `bash`, `shell`, `console`, `text` (case-sensitive), fechamento com exatamente três backticks e espaços/tabs opcionais. Cada linha não vazia dentro do fence é uma unidade; remover trim e um único prefixo literal `$ ` opcional. Fora de fences, reconhecer spans de um único backtick que abrem e fecham na mesma linha; cada conteúdo, após trim, é uma unidade. Prosa e listas comuns não são comandos, nem mesmo se contiverem “npm test” sem backticks. Outros fences, fence aberto/nested ou sintaxe de code span não modelada dentro da seção tornam a confirmação ambígua. Não executar shell nem usar LLM para interpretar prosa.

**Vocabulário fechado:** unidades são case-sensitive e devem corresponder integralmente a `npm[ \t]+test`, `npm[ \t]+ci`, `npm[ \t]+run[ \t]+lint` ou `npm[ \t]+lint`. Não aceitar flags, prompt adicional, `npm run test`, encadeamento, variáveis, comentários de shell, continuação de linha ou prefixo `cd`. O launcher documentado escolhido é literalmente `npm test`; não normalizar outro comando para ele. `npm ci` é instalação; `npm run lint`/`npm lint` são apenas anotações documentais de lint, nunca comandos acrescentados à receita.

**Ordem e precedência:** ordenar documentos por caminho UTF-8 e unidades por posição. Pelo menos uma seção deve conter `npm test`. Em cada seção, antes do primeiro `npm test`, admitir somente `npm ci`; qualquer outra unidade de comando ou sintaxe de código não modelada nesse prefixo (inclusive `npm run build`, lint ou login) é `AUTO_RECIPE_PREPARATION_UNRESOLVED`. Após o primeiro `npm test`, admitir apenas o vocabulário fechado; unidade desconhecida é `UNIT_SCOPE_UNRESOLVED`. Seção sem `npm test` mas com unidades desconhecidas também é `UNIT_SCOPE_UNRESOLVED`; seção só com unidades conhecidas não confirma nem cancela outra confirmação. Não aceitar variantes entre documentos como override: qualquer impedimento prevalece sobre confirmação positiva. Registrar todos os motivos; código principal segue ordem fixa: entrada inválida/ambígua, CI fora do corte, preparo não resolvido, escopo não resolvido, ambiente não suportado. Desempatar motivos da mesma classe por caminho UTF-8, posição e código. Erros de identidade/snapshot anteriores à análise conservam sua precedência e código próprio.

**Interpretação limitada:** `## Qualidade` com fence contendo, em ordem, `npm test` e `npm run lint`, mais pacote/config/inventário headless elegíveis, confirma o contexto documental da classificação local/unitária e de componentes. Não exige a palavra “unitário” nem prova ausência de efeitos externos. `## Deploy` com `rayfin login`/`rayfin up` fora dos intervalos aceitos não é preparo de teste. Um comando fora do modelo **dentro** de uma seção de testes não é ignorado. Não inferir requisitos por frases livres, nem afirmar ausência absoluta de preparação; a decisão é limitada a essa gramática.

### 5.2.2 CI: corte conservador explícito

Esta entrega **não interpreta YAML de CI** e não adiciona PyYAML. Qualquer entrada em `.github/workflows/` cujo basename termine em `.yml` ou `.yaml`, comparado por NFC/casefold, produz `UNIT_SCOPE_UNRESOLVED`, com motivo `CI_WORKFLOW_UNSUPPORTED` no diagnóstico. Isso inclui workflows só de deploy, arquivos inválidos, links, nomes com caixa distinta e arquivos em subdiretórios: a primeira versão não tenta determinar relevância de jobs. Enumerar todos por caminho ordenado e hashear blobs legíveis sob os mesmos limites, sem parsing parcial. CI não fornece confirmação positiva nem override de documentação.

Também tratar como CI desconhecida, com o mesmo impedimento, a presença de `.gitlab-ci.yml`, `.circleci/config.yml`, `azure-pipelines.yml`, `.travis.yml` ou `Jenkinsfile` na raiz, por NFC/casefold. Ausência desses marcadores não prova ausência universal de automação; apenas define o corte detectável.

Não extrair `actions/setup-node`, `node-version`, matrizes, `run`, `working-directory`, `env`, `shell`, `needs`, expressões ou comandos de workflows por regex. Assim, jobs com `npm ci`/`npm test`, build antes de teste, setup-node com pin divergente e workflows dinâmicos são **todos pendentes** nesta entrega. Suportá-los exige futura gramática declarativa e validação de pins; não existe um subconjunto “reconhecido” implícito. A limitação pode manter o próprio alvo pendente se houver workflow no inventário; não prometer cobertura dele sem conferir esse inventário.

### 5.3 Preparação e comandos

Receita automática: instalação `argv=['npm','ci']`; suíte `argv=['npm','test']`; ambos com `cwd` igual ao diretório do pacote (`.` na raiz). Usar `DEFAULT_LIMITS`, `environment={}` e nenhuma opção que suprima lifecycle, dependências de desenvolvimento ou scripts originais. O npm executa seu lifecycle original, inclusive pretest/posttest, se presentes. A análise deve registrar esses scripts e tornar pendentes delegações que impeçam estabelecer o escopo original.

Somente com confirmação documental válida pela seção 5.2.1, sem impedimento de CI e sem comando de preparo fora do modelo, usar `preparation=[]`: tentar a passagem original sem preparo especulativo. Isso não afirma que a suíte passará. `npm run build` antes de `npm test` em seção aceita produz `AUTO_RECIPE_PREPARATION_UNRESOLVED`, não um comando de build na receita. Nenhuma confirmação documental produz `UNIT_SCOPE_UNRESOLVED`, não tentativa otimista. Nesta entrega, não inferir comandos adicionais de geração, login, deployment ou serviços; prosa fora da gramática não é interpretada como comando obrigatório.

Falhas efetivas de instalação, import, geração ausente ou suíte seguem a atribuição existente. Não fazer retry corretivo, gerar arquivos ausentes ou mudar dependências para encaixar `MATRIX`.

### 5.4 Orçamentos fixos

Política inicial: até 64 arquivos lidos pelo resolvedor; até 1 MiB por manifest/config/documento/CI; até 8 MiB por lock; até 16 MiB de bytes brutos acumulados por grupo. Contabilizar antes de ler e após receber os bytes; não truncar arquivos para decidir. Documentos/configs potencialmente relevantes além do limite tornam a decisão pendente, não são silenciosamente ignorados. Inventário e operações Git obedecem aos limites já existentes do store/transport.

O helper tem prazo de 30 segundos e saída máxima de 1 MiB; excesso ou saída inválida produz pendência. Os limites são constantes da política, não knobs novos da CLI. A instalação e a suíte mantêm os limites existentes da receita/executor.

## 6. Política de ambientes e instrumento

Criar `validation_recipe_policy.json` com versão inteira, perfis ordenados, digest imutável da imagem, plataforma, Node/npm exatos, trios elegíveis e regras/orçamentos. Hash da política: SHA-256 dos bytes do arquivo confiável. Selecionar o primeiro perfil compatível na ordem versionada, nunca a imagem mais recente da rede. Validação da política é global e recusa perfis duplicados, tags móveis, versões inexatas e combinações declaradas sem suporte em `MATRIX`.

Usar `semver` **7.7.2** como dependência exata do auxiliar confiável, com lock próprio atualizado. Ranges, prereleases e versões inválidas seguem essa biblioteca, com semântica documentada (`includePrerelease=false`); não escrever comparação parcial de SemVer em Python. O gerador não consulta registry, não baixa imagens e não executa `npm` do candidato. Verificar as ferramentas reais continua responsabilidade do executor.

Matriz original observada antes da implementação, mantida para histórico:

| Node | Vitest | Vite |
| --- | --- | --- |
| 22.23.4 | 4.1.10 | 7.3.6 |
| 22.23.4 | 4.1.11 | 8.3.0 |
| 24.22.2 | 5.0.5 | 8.3.4 |
| 26.13.0 | 5.0.5 | 8.3.5 |

O trio **22.23.4 / 4.1.11 / 8.0.16** integra agora a matriz e a política após a prova em fixtures sintéticas. O perfil é `linux/amd64`, Node `22.23.4`, npm `10.9.9`, imagem `node@sha256:539d4d5b9c0df96bb9d1229264d358dc816e8592cb8867828a580b527e7ad0fa`. Essa prova não executou o alvo.

Não basta o parser reconhecer versões. Uma combinação nova só entra na política e em `MATRIX` após fixtures reais com npm frozen, CLI original, bootstrap, protocolo/censo completo, quatro casos positivos associados e casos negativos de hooks, mocks, retries e chamadas fora do teste. Preservar pool, isolate, jsdom, setup e alias originais. Se o pool efetivo não for `forks`, manter pendência até haver integração demonstrada para esse pool; nunca acrescentar `--pool=forks` para salvar o alvo. O gate runtime de `configure_instrument` permanece autoritativo mesmo depois do planejamento estático.

## 7. Receita e proveniência persistidas

Reutilizar os campos exigidos por `_validate_recipe`: `repository_id`, `repository_url`, `commit_sha`, `recipe_version`, `image`, `platform`, `tools`, `lockfiles`, `environment`, `phases`, `unit_scope`, `limits`, `evidence`, `resolution_status`, `resolution`.

`tools` contém `node`, `vitest` e exatamente um entre `npm`/`pnpm`, com versões exatas para receita resolvida. Vite pertence ao planejamento/proveniência e à negociação runtime; não adicionar chave incompatível em `recipe.tools`. Locks têm `path/sha256/manager`. Comandos têm `argv/cwd`. `unit_scope` contém `status/code/packages/evidence/external_services`; cada pacote tem `path/name/scope`. Evidências têm `path/detail`, referenciadas por caminho nas resoluções e no escopo. Não usar evidência inventada para preencher referências.

O envelope atual permanece: `resolution_status`, `code`, `recipe`, `recipe_sha256`, `catalog_sha256`. Adicionar opcionalmente `provenance` **apenas ao envelope**, não dentro da receita. Campos exatos da proveniência automática:

| Campo | Contrato |
| --- | --- |
| `kind` | Literal `automatic` |
| `resolver_version` | Literal versionado `snapshot_npm_v1` |
| `policy_sha256` | SHA-256 dos bytes da política consultada |
| `snapshot` | Referência portátil completa de `SnapshotArtifact.reference()` usada na análise; não prova execução/verificação posterior do contexto |
| `inputs` | Lista ordenada por caminho, sem duplicatas, de `{path, sha256}` dos bytes brutos efetivamente lidos |
| `rule` | Literal `single_npm_local_vitest_v1` |
| `evidence` | Lista não vazia de `{path, detail}`; caminhos pertencem a `inputs` |
| `environment_profile` | `{image, platform, node, npm, vitest, vite}` exatos selecionados, ou `null` se nenhum perfil compatível |

Quando há snapshot e entradas verificadas, uma tentativa pendente também pode carregar proveniência válida. Falha anterior à verificação do snapshot não deve fabricar essa estrutura: envelope sem proveniência e diagnóstico com a causa. Receita nula exige status `not_evaluated`, hash de receita nulo e limits nulos na abertura. `catalog_sha256` continua sendo o hash real do catálogo consultado; não significa que a receita automática integra o catálogo. `recipe_sha256` continua o hash canônico da receita, sem a proveniência.

Leitor v3 deve validar campos/tipos exatos, hashes, caminhos, ordenação, identidades, coerência status/code, locks dentro de inputs e hashes correspondentes, perfil versus receita e snapshot de proveniência versus snapshot do repositório quando disponível. Receita automática presente exige snapshot de execução igual **ou** a publicação integral de falha de contexto sem execução definida na seção 4.3, com snapshot de execução nulo e diagnóstico obrigatório. Não basta omitir o snapshot para obter essa exceção. Uma pendência sem receita pode guardar snapshot de análise mesmo se o loader não concluiu, sem alegar verificação da extração. Sem proveniência, publicações legadas permanecem aceitas sob o contrato anterior. Rejeitar nova proveniência em publicação v2; não reinterpretar registros históricos.

O leitor portátil verifica **coerência declarada**, não rederiva análise estática a partir de hashes. Não precisa de Node, política instalada, snapshot local ou rede para esses vínculos novos. Não alegar que um hash de política prova aprovação externa da política. Auditorias históricas de recursos que já exigem ferramentas mantêm seu contrato separado; a extensão não adiciona essa dependência ao caminho v3 portátil.

Replay histórico não invoca o resolvedor nem converte `RECIPE_NOT_FOUND` antigo em receita nova. Dataset e deduplicação devem preservar o envelope integral e continuar admitindo apenas as populações que seus leitores atuais permitem. Não duplicar a proveniência em estruturas paralelas.

## 8. Caso orientador e limites da promessa

O snapshot informado tem exatamente um `package.json`, em `application/liquiditywatch-app/package.json`, sem workspace, e um package-lock v3 nesse diretório. Script original: `test='vitest run'`. Três testes TSX usam jsdom, Testing Library e mocks; não são por isso E2E de browser. Config: `defineConfig`, `environment='jsdom'`, setup `src/test/setup.ts`, globals e alias `@` para `src`. Pool/isolate não estão declarados: observar o efetivo, não assumir.

A seção de qualidade do README recomenda `npm test` e lint e descreve formatos/DAX/replay/atalhos/renderizações headless, mas não chama a suíte de unitária explicitamente. A confirmação só vale se caminho, heading e comandos corresponderem à gramática 5.2.1, com inventário/config elegíveis, sem locks externos nem CI bloqueante. O nome Vitest isolado não sustenta o escopo. Se algum desses requisitos falhar, o alvo continua pendente. Não criar exceção por ID para forçar resultado.

Restrições informadas: README Node >=20; Vite 8.0.16 `^20.19.0 || >=22.12.0`; jsdom 29.1.1 `^20.19.0 || ^22.13.0 || >=24`; Vitest 4.1.11 `^20 || ^22 || >=24`. Validar também as demais engines do lock, não apenas essa amostra.

O build gera `src/fabric.generated.ts`, ausente no snapshot e importado por `fabric-client.ts`. Seu script usa `npx fabric-app-data generate`, `tsc -b --noCheck` e Vite; `prebuild` usa `rayfin env --framework vite`. Instruções `rayfin login/up` são implantação, não requisito comprovado dos testes. Alguns testes mockam cliente/hook; isso não prova que todos evitam o arquivo gerado. Não inventar `npm run build`, login, serviço ou flag. Tentar instalação frozen e script original quando as regras os autorizarem; uma falha será registrada sem correção automática.

Fixtures devem ser exemplos sintéticos mínimos desses padrões, sem copiar código, resultados ou credenciais do candidato. Testes reais de compatibilidade usam dependências reais da fixture, não executam o repositório alvo.

## 9. Verificação, rollout e revisão

Cobrir precedência das seis receitas, determinismo, limites, JSON/links/locks, múltiplos pacotes/gerenciadores, configurações dinâmicas, SemVer, pins, identidade divergente, erros por grupo e liberação de memória. Testar abertura com limites corretos, loader original chamado uma vez, snapshot divergente impedindo executor e continuidade após pendência de outro grupo.

Provar roundtrip do 04 automático, legados sem proveniência, adulterações de todos os vínculos e preservação em dataset/replay sem resolver novamente. Regressões não podem mudar política local de instância nem maquiar fechamento `failed`.

Ordem de habilitação realizada: contratos estáticos e testes sintéticos; fixtures reais do instrumento; atualização da matriz/política após prova; integração. O histórico das revisões e verificações consta do plano; execução real permanece manual.

**Gate manual PENDENTE:** preservar backup de results e snapshots e obter autorização explícita antes de `--validate --overwrite`. Depois, deduplicação e dataset somente conforme o estado realmente publicado. Não repetir descoberta/triagem/extração ou `--all` sem necessidade. Não alterar a regra de continuação do pipeline.

**Revisões:** revisão documental inicial `APPROVED`; precedência/política e tarefas 3/4 tiveram revisões aprovadas; qualidade da tarefa 5 aprovada. Dois achados da tarefa 6 foram corrigidos; o usuário cancelou revisões posteriores. Verificações finais não são revisão independente nem aprovação geral de qualidade.

**Controle de mudanças:** implementação, lock confiável e fixtures foram alterados; a resolução não cadastra automaticamente o alvo no catálogo. Sem staging ou commit nesta etapa.

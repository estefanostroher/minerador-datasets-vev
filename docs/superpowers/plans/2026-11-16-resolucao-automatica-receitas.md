# Resolução automática de receitas — Implementation Plan

> **Registro histórico do plano:** as instruções e checkboxes nas seções 1–3 registram o plano original, não tarefas atuais pendentes. O estado efetivo e as evidências estão no resumo abaixo; o usuário cancelou revisões posteriores e não autorizou staging ou commit.

**Goal:** Resolver ausências legítimas de receita pelo snapshot verificado, sem enfraquecer critérios, alterar candidatos ou prometer aprovação.

**Architecture:** Catálogo primeiro, fallback determinístico apenas para `RECIPE_NOT_FOUND`. Uma pré-resolução limitada produz envelopes compactos antes de `run_started`; o loader original refaz a verificação completa uma vez por grupo durante a execução. Proveniência aditiva no envelope do 04 mantém leitores, replay e dataset auditáveis.

**Tech Stack:** Python >=3.11, pytest, Node >=22, TypeScript 6.0.3, semver 7.7.2 no auxiliar confiável, npm frozen, Docker Engine Linux e Compose v2.

**Spec:** [2026-11-16-resolucao-automatica-receitas-design.md](../specs/2026-11-16-resolucao-automatica-receitas-design.md).

**Emenda posterior:** as seções 5.1–5.2.1 da spec registram quatro correções limitadas fundamentadas em reprodução sintética npm/Vitest e no heading real: nomes do lock, precedência Vitest, prefixo de qualidade e import Node `path`. As demais restrições permanecem aplicáveis. A implementação desta emenda foi autorizada sem novas revisões ou commit; a resolução real solicitada é somente leitura, sem execução candidata ou escrita de resultados.

**Estado:** tarefas 1–10 concluídas, incluindo a verificação integrada pelo coordenador. A resolução do snapshot real foi somente leitura. O gate de execução e substituição do 04 segue manual; nenhum commit foi criado.

### Estado e evidências de implementação

- [x] Tarefas 1–6: precedência, política, helper, análise, resolvedor e proveniência implementados. Revisões iniciais de precedência/política e das tarefas 3/4 aprovadas; qualidade da tarefa 5 aprovada. Dois achados da tarefa 6 foram corrigidos; o usuário cancelou as revisões seguintes. Não se afirma aprovação geral de qualidade.
- [x] Tarefa 7: integração registrada com 341 testes aprovados; rodada anterior com 253.
- [x] Tarefa 8: oito testes Docker reais passaram sem skips em fixtures sintéticas, incluindo censo completo e quatro casos positivos, negativos de hook/mock/retry/threads; quatro testes de ponte das matrizes T1/T2 anteriores passaram. O trio Node 22.23.4/npm 10.9.9/Vitest 4.1.11/Vite 8.0.16/jsdom 29.1.1 foi habilitado na matriz/política após a prova. O carregamento runtime usa hook confiável `.mjs` servido pelo instrumento em caminhos fixos; não força pool.
- [x] Tarefa 9: 113 testes downstream passaram; replay e envelope foram verificados sem promover receita resolvida a aprovação.
- [x] Quatro correções estáticas posteriores: nomes divergentes de lock conforme `npm ci` sintético, precedência de config Vitest, prefixo ✅ em heading de qualidade e import builtin `path`. Rodadas informadas: 200 testes Python, 21 do helper e 135 Node com quatro skips.
- [x] Resolução somente leitura da origem real `1460677238`: `resolved`, receita presente e zero diagnósticos. Nenhum candidato foi executado e o 04 anterior não foi reescrito; ele continua `failed`. Isto não é prova de aprovação dinâmica das 13 funções.
- [x] Tarefa 10: documentação atualizada e verificações Docker e wheel abaixo. A verificação final não constitui revisão independente.
- [x] Verificação final do coordenador: `.\.venv\Scripts\python.exe -m pytest -q` terminou com **1355 passed, 25 skipped**, em **1354,47 s (22 min 34 s)**. Os skips incluem integrações opt-in; as oito fixtures reais do novo ambiente e os smoke tests Docker foram executados separadamente conforme registrado acima. `git diff --check` terminou sem erros, com avisos de conversão LF/CRLF; staging vazio e último commit ainda `bd8fbb6`.

### Verificação da imagem e embalagem

- `Test-Path -LiteralPath '.'`: `True`; `docker compose config --quiet`: sucesso; `docker compose build`: sucesso, imagem final `robo-minerador-minerador:latest`, ID `sha256:0c279a2bf8ee85562661340a97a02530239f92a8f5eede43eb928911ad1ef83d`, Linux/amd64. Node da imagem coordenadora `v22.23.5`, npm `10.9.9`; estes não são a versão Node do perfil isolado de validação.
- `docker compose run --rm minerador --help`: sucesso; `ROBO_MINERADOR_DOCKER_TESTS=1` com `pytest tests/test_pipeline_docker.py -q`: **2 passed**, sem skips.
- Wheel: `python -m pip wheel --no-deps --no-build-isolation` no Python global falhou por ausência de `setuptools.build_meta`; o ambiente virtual também não possui setuptools. ` .\.venv\Scripts\python.exe -m pip wheel --no-deps . --wheel-dir C:\Users\Estefano\AppData\Local\Temp\opencode` com isolamento de build passou. Inspeção ZIP confirmou política, helper, package/lock e `semver` 7.7.2 no manifest e lock. SHA-256 da wheel: `c23d569688879a2de90a545bd0fb445e83376e89a40b45ed1f0f58a782167603`.

---

## 1. Organização e contratos propostos

Todos os caminhos são relativos a `C:\Users\Estefano\Projects\robo-minerador`.

| Arquivo | Ação futura e responsabilidade |
| --- | --- |
| `src/robo_minerador/validation_recipe_policy.json` | Criar política versionada de perfis, regras e limites. |
| `src/robo_minerador/validation_recipe_analysis.py` | Criar ponte limitada para leitura/análise de metadados e helper confiável. |
| `src/robo_minerador/validation_recipe_resolver.py` | Criar precedência híbrida e montagem/validação da receita. |
| `src/robo_minerador/validation_recipe_provenance.py` | Criar validador Python puro dos vínculos portáteis. |
| `src/robo_minerador/typescript/validation-recipe-analysis.mjs` | Criar análise AST de dados e SemVer, sem executar candidato. |
| `src/robo_minerador/typescript/package.json`, `package-lock.json` | Adicionar semver exato ao ambiente confiável, nunca ao candidato. |
| `pyproject.toml` | Incluir a nova política em package-data. |
| `src/robo_minerador/validation.py` | Pré-resolução, limites da abertura, diagnóstico por grupo e confronto do snapshot. |
| `src/robo_minerador/validation_format.py` | Validar proveniência opcional em v3 e manter legados. |
| `src/robo_minerador/validation_evaluation.py` | Manter pré-requisitos pendentes se a verificação de contexto automático falhar, inclusive preparo planejado vazio; sem mudar política científica. |
| `src/robo_minerador/validation_instrument.py` | Atualizar MATRIX somente após prova real do trio novo. |
| `src/robo_minerador/typescript/validation-bootstrap.mjs` | Alterar apenas se fixtures demonstrarem necessidade e integração segura; nunca forçar pool. |
| `src/robo_minerador/validation_replay.py` | Preservar domínio v2→v3 e envelopes; mudança somente se testes revelarem perda. |
| `src/robo_minerador/dataset.py`, `dataset_format.py`, `deduplication_input.py` | Verificar preservação/validação, sem novo campo paralelo nem mudança de população. |
| `tests/test_validation_recipe_policy.py` | Criar testes da política e compatibilidade declarativa. |
| `tests/test_validation_recipe_analysis.py` | Criar testes de snapshot, limites e análise limitada. |
| `tests/test_validation_recipe_resolver.py` | Criar testes de precedência, receita e determinismo. |
| `tests/test_validation_recipe_provenance.py` | Criar testes de coerência e adulteração. |
| `tests/recipe_resolution_fixtures.py` | Criar fábricas sintéticas de snapshots/manifests/locks; sem código copiado do alvo. |
| `src/robo_minerador/typescript/tests/validation-recipe-analysis.test.mjs` | Criar testes AST/SemVer. |
| `tests/test_validation_recipe_runtime.py` | Criar integração opt-in com Docker e dependências reais de fixtures. |
| `tests/fixtures/recipe_runtime/` | Criar pacote sintético, config/setup/alias e lock frozen do trio alvo. |
| `tests/test_validation.py`, `test_validation_pair.py`, `test_validation_cli.py` | Acrescentar regressões de coordenação, política local e CLI. |
| `tests/test_dataset.py`, `test_dataset_format.py`, `test_deduplication_input.py` | Acrescentar preservação e adulteração downstream. |
| `README.md` | Explicar resolução, limites, proveniência e reexecução autorizada. |

Não modificar `validation_recipes.json` para cadastrar automaticamente o alvo. Não reestruturar o pipeline Docker não commitado.

### Interfaces novas a manter consistentes

- `load_recipe_policy(path=DEFAULT_POLICY) -> dict`: valida configuração confiável e devolve `version`, `sha256`, `profiles`, `rules`, `limits`. SHA sobre bytes originais; ordem de perfis significativa.
- `analyze_recipe_inputs(repository, store, policy) -> dict`: abre snapshot offline e devolve `snapshot`, `inputs`, `facts`, `issues`. `inputs` só contém path/hash; `facts` contém dados estruturados necessários, nunca AST, bytes ou caminhos de host. `issues` é lista de `{code, message, evidence}`.
- `resolve_effective_recipe(repository, catalog, store, policy) -> tuple[dict, list]`: devolve `(envelope, diagnostics)`; catálogo primeiro, análise só em ausência legítima. Diagnósticos internos são `{code, message, evidence}`, convertidos pelo coordenador no formato existente.
- `validate_recipe_provenance(resolution, repository, snapshot) -> None`: valida somente JSON/vínculos; lança `ValueError` em corrupção, não reabre arquivos, não importa Node nem resolvedor.
- Helper: stdin JSON com `files=[{path, content}]`, `profiles` e `rule`; stdout JSON com `facts` e `issues`. Protocolo só aceita dados, saída estrita e limitada. Hashes dos bytes originais são calculados em Python antes da decodificação. Versões comparadas pelo semver confiável, não por implementação Python paralela.

Receita final usa `_validate_recipe` e `_hash` existentes. Vite não entra em `recipe.tools`. Proveniência usa exatamente os campos da seção 7 da spec; não inventar um segundo envelope.

**Evolução incremental:** a tarefa 1 entrega a interface `resolve_effective_recipe` funcional com catálogo e verificação de identidade, mantendo ausências como `RECIPE_NOT_FOUND`. Não importa nem chama analisador ainda inexistente. As tarefas 2–4 acrescentam componentes isolados, cada uma com testes verdes. A tarefa 5 conecta o fallback e substitui as expectativas transitórias de ausência pelas expectativas finais de análise/geração. Esse estado intermediário é um incremento conservador testado, não um TODO a deixar no produto final. Cada tarefa encerra verde, passa por revisão de aderência e qualidade e só então permite avançar; não agrupar tarefas 1–5 em uma entrega única.

### Disciplina de execução futura

- [ ] Obter aprovação explícita destes documentos antes de qualquer implementação.
- [ ] Tratar aprovação da implementação como autorização para testes, fixtures sintéticas Docker, downloads de imagens/dependências das fixtures e instalação das dependências confiáveis previstas. Não pedir autorização intermediária para esses passos. Execução de candidato real e substituição de results continuam sujeitas ao gate manual separado; esta revisão documental não autoriza implementação.
- [ ] Registrar `git status` e diff inicial sem staging, protegendo o trabalho Docker já existente; verificar ferramentas com `Get-Command git, node, npm, docker` antes de usá-las no PowerShell.
- [ ] Usar o interpretador existente `.\.venv\Scripts\python.exe`; se ausente, pedir orientação, não instalar automaticamente.
- [ ] Em cada tarefa: escrever teste vermelho, executar e confirmar falha pelo contrato ausente; implementar a menor mudança; repetir o comando e confirmar o contrato verde. Não aceitar erro de import/configuração acidental como cobertura funcional.
- [ ] Não fazer commits intermediários, mesmo que uma skill os sugira.

## 2. Tarefas

### Tarefa 1 — Interface funcional mínima: catálogo e identidade

**Arquivos:** criar `src/robo_minerador/validation_recipe_resolver.py` e `tests/test_validation_recipe_resolver.py`; consultar `tests/test_validation_recipe.py` e suas seis origens reais do catálogo. Não criar ou importar o analisador nesta tarefa.

- [ ] Escrever testes da interface com as identidades das seis receitas, incluindo pendentes e pnpm. Passar `store` sentinela que lança em qualquer acesso e `policy=None`; este incremento não usa snapshot, política de ambientes nem analisador. Exigir os seguintes contratos:

```python
assert envelope == resolve_recipe(repository, catalog)
assert diagnostics == []
assert envelope['catalog_sha256'] == catalog['sha256']
assert store_calls == []
```

- [ ] Testar que conflito de URL no ID/SHA cadastrado, URL diferente para ID conhecido em outro SHA e URL associada a outro ID lançam `ValueError`, sem acesso ao store. Catálogo malformado continua recusado por `load_recipes`, sem ser convertido em ausência.
- [ ] Testar ausências legítimas, incluindo ID conhecido em SHA novo com URL igual: neste incremento, retornar exatamente `(resolve_recipe(repository, catalog), [])`, com `code='RECIPE_NOT_FOUND'`, recipe/hash nulos e nenhum acesso ao store. Não escrever ainda expectativas de chamada de análise ou receita automática.
- [ ] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_validation_recipe.py tests/test_validation_recipe_resolver.py -q` para confirmar o vermelho pela interface ausente; implementar a interface mínima no mesmo ciclo. Delegar receitas cadastradas a `resolve_recipe`, acrescentar as verificações de identidade descritas acima e preservar o envelope original para ausência legítima. Não inserir imports antecipados de componentes das tarefas 2–4.
- [ ] Repetir o mesmo comando e exigir todos os testes verdes **na tarefa 1**, com as seis receitas intactas. Encaminhar esse incremento funcional às revisões de aderência e qualidade antes da tarefa 2. Testes de ausência marcados como transitórios evoluem explicitamente na tarefa 5, sem skips ou falhas adiadas.

### Tarefa 2 — Política versionada e embalagem

**Arquivos:** criar `validation_recipe_policy.json` e `tests/test_validation_recipe_policy.py`; modificar `pyproject.toml`; colocar o loader em `validation_recipe_resolver.py`.

- [ ] Testar parsing estrito, SHA dos bytes, versões exatas, ordem estável, digest/plataforma, campos desconhecidos, perfis duplicados e tríos fora de `MATRIX`.
- [ ] Definir limites fixos: 64 arquivos, 1 MiB por não-lock, 8 MiB por lock, 16 MiB total, helper 30 segundos/1 MiB stdout. Testar limites exatos aceitos e limite+1 recusado; não criar opção de CLI.
- [ ] Implementar `load_recipe_policy` com perfis inicialmente restritos às combinações já comprovadas. Não habilitar o trio Vite 8.0.16 nesta tarefa. Qualquer perfil incluído precisa de digest/plataforma/Node/npm comprovados, não deduzidos do nome da tag.
- [ ] Acrescentar `validation_recipe_policy.json` à lista explícita de package-data, sem remover os JSON/MJS existentes.
- [ ] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_validation_recipe_policy.py -q`; exigir que a mudança de qualquer byte da política altere o hash, sem alterar receita cadastrada.

### Tarefa 3 — Helper confiável: SemVer e AST como dados

**Arquivos:** criar `typescript/validation-recipe-analysis.mjs` e `typescript/tests/validation-recipe-analysis.test.mjs`; modificar somente o package/lock do auxiliar confiável.

- [ ] Testar as restrições Node do caso orientador com `22.23.4`: Vite `^20.19.0 || >=22.12.0`, jsdom `^20.19.0 || ^22.13.0 || >=24`, Vitest `^20 || ^22 || >=24`. Rejeitar `22.12.0` para a restrição de jsdom, versão inválida e prerelease não admitida; testar limites `<`, `<=`, `>`, `>=`, caret, tilde, intervalos e OR pela biblioteca.
- [ ] Adicionar `semver: '7.7.2'` às dependencies confiáveis e atualizar o lock próprio, após autorização de implementação. Comando no diretório `src/robo_minerador/typescript`: `npm install --package-lock-only --ignore-scripts --save-exact semver@7.7.2`. Conferir diff: só manifests/lock confiáveis; nenhuma dependência do candidato.
- [ ] Instalar o auxiliar com `npm ci` nesse mesmo diretório, somente durante implementação autorizada. Não usar esse comando em qualquer snapshot de candidato.
- [ ] Implementar protocolo stdin/stdout e extração AST para `defineConfig` literal, jsdom, setup/globals, alias pelo modelo `resolve(import.meta.dirname, 'src')`, pool/isolate literais, config explícita e descoberta padrão.
- [ ] Testar spreads, chamadas arbitrárias, getter, função exportada, variável dinâmica, plugins, runner customizado, browser e múltiplas configs: geram issue localizada, sem execução. Inserir expressão maliciosa de escrita em fixture e comprovar que nenhum arquivo-sentinela aparece.
- [ ] Executar `node --test tests/validation-recipe-analysis.test.mjs` no diretório confiável. Exigir que o helper preserve pool/isolate declarados e represente ausência como ausência, sem inserir defaults runtime.

### Tarefa 4 — Leitura de snapshot e classificação limitada

**Arquivos:** criar `validation_recipe_analysis.py`, `tests/test_validation_recipe_analysis.py` e `tests/recipe_resolution_fixtures.py`.

- [ ] Criar fábrica `single_npm_snapshot` que monta, em `tmp_path`, pacote raiz ou `application/local-app`, lock v3, config literal e README de qualidade. Usar ID sintético `900000001` e URL `https://github.com/fixture/local-app`; SHA vem do commit Git da fixture, não de SHA fictício associado a snapshot real.
- [ ] Testar abertura via referência completa, enumeração do inventário, lotes `read_blobs` e descarte de readers. Usar spies para impedir `acquire`, rede, import de config e chamada a `load_repository_context` nessa fase.
- [ ] Implementar orçamento antes/depois da leitura, SHA dos bytes brutos, parsing JSON estrito e protocolo limitado do helper. Saída truncada, timeout, UTF-8 inválido ou excesso de stdout viram issue; não tomar decisão a partir de trecho parcial.
- [ ] Testar um único pacote raiz/aninhado e o caso aninhado sintético com três TSX headless, config jsdom/setup/alias e README de qualidade sem a palavra “unit”. Asserts centrais:

```python
assert facts['package_path'] == 'application/local-app'
assert facts['scope_rule'] == 'single_npm_local_vitest_v1'
assert facts['suite_argv'] == ['npm', 'test']
assert facts['preparation'] == []
assert all(set(item) == {'path', 'sha256'} for item in analysis['inputs'])
```

- [ ] Testar segundo package.json, workspace, pnpm/yarn/bun concorrentes, lock ausente, lock v1/v2, links/gitlinks, caminhos inseguros, dependência `link/file/workspace`, pacotes Vitest/Vite ambíguos, configuração dinâmica e engines/pins inconsistentes: nenhuma receita resolvida.
- [ ] Testar shrinkwrap e package-lock juntos: shrinkwrap selecionado, ambos nos inputs; hashes sobre bytes originais. Ambiguidade ou JSON inválido no material necessário não é ignorado.
- [ ] Inventariar locks de todo o repositório segundo a seção 5.1: testar lock na raiz com pacote aninhado, ancestral, irmão, descendente sem manifesto e fixture/vendor. Todos produzem `AUTO_RECIPE_INPUT_UNSUPPORTED`; hashes dos blobs legíveis entram nos inputs ordenados, sem hash inventado para link/limite. Testar colisões de caixa e os seis basenames fechados.
- [ ] Testar shrinkwrap+package-lock no mesmo diretório com name/version de topo/raiz divergentes ou Vitest/Vite efetivos conflitantes: pendência mesmo com shrinkwrap selecionável. Mesma identidade e resolução coerentes permitem a precedência.
- [ ] Testar inventário com script/config/diretório E2E e suíte mista: `UNIT_SCOPE_UNRESOLVED`, sem selecionar só os testes locais. Testar glob customizado desconhecido e fronteira de pacote ambígua com a mesma prudência.
- [ ] Implementar somente a gramática documental 5.2.1: quatro caminhos lógicos máximos, unicidade NFC/casefold, headings ATX com títulos fechados, intervalos por nível, fences/spans e comandos integrais. Não usar LLM nem inferir requisitos de prosa; registrar heading/linhas e todos os documentos existentes nos inputs.
- [ ] Criar fixture positiva reproduzível: pacote aninhado sem CI/locks externos, README com `## Qualidade`, fence `sh` com linhas `npm test` e `npm run lint`, e `## Deploy` separado com `rayfin login`. Exigir scope_rule aprovada e `preparation=[]`, sem comando rayfin ou build na receita. Repetir usando `docs/testing.md` e heading `Qualité` em NFC/NFD.
- [ ] Criar negativas independentes: `npm test` só em prosa; heading não listado; `npm run test`; documentos com colisão de caixa; fence não fechado; shell `npm test && npm run lint`; span/comando desconhecido. Exigir `UNIT_SCOPE_UNRESOLVED`, exceto unidade desconhecida antes do primeiro `npm test`, que exige `AUTO_RECIPE_PREPARATION_UNRESOLVED`.
- [ ] Testar `npm run build` antes de `npm test` na seção aceita: `AUTO_RECIPE_PREPARATION_UNRESOLVED`, nenhum preparo inferido. README positivo em um caminho e seção conflitante em outro: impedimento prevalece, independentemente da ordem do inventário. Ausência do arquivo gerado não vira aprovação nem motivo para inventar build.
- [ ] Implementar CI conservadora 5.2.2, sem YAML/PyYAML: cada workflow `.yml/.yaml` enumerado e marcador de outro CI produz `UNIT_SCOPE_UNRESOLVED` com motivo `CI_WORKFLOW_UNSUPPORTED`. Testar workflow só deploy, npm ci/test, setup-node com pin incompatível, matriz/expressão/env/shell/needs, arquivo malformado e link: todos pendentes, sem extrair pins nem interpretar jobs. Ausência de CI permite prosseguir pelos demais requisitos, não prova ausência universal de automação.
- [ ] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_validation_recipe_analysis.py -q`; exigir zero chamadas de loader completo e aquisição de rede.

### Tarefa 5 — Montagem da receita e resolução híbrida

**Arquivos:** modificar `validation_recipe_resolver.py`; ampliar `tests/test_validation_recipe_resolver.py`, usando `tests/recipe_resolution_fixtures.py`.

- [ ] Evoluir os testes transitórios de ausência da tarefa 1: para ID/SHA ausente legítimo e identidade coerente, esperar chamada a `analyze_recipe_inputs` e resolução conforme seus fatos/issues, não retorno incondicional de `RECIPE_NOT_FOUND`. Usar a fixture de snapshot da tarefa 4 e política da tarefa 2; confirmar o vermelho pelo fallback ainda não conectado, sem deixar expectativas intermediárias contraditórias na suíte final.
- [ ] Acrescentar spy de análise: cadastradas, inclusive pendentes, não a chamam; conflitos de identidade não a chamam; ID conhecido em SHA novo com a mesma URL e snapshot coerente pode chamá-la. Testar geração de receita e pendência resultante da análise, além da preservação das seis receitas.
- [ ] Conectar o fallback à interface já funcional da tarefa 1, usando os componentes existentes das tarefas 2–4. Manter catálogo primeiro e verificações de identidade antes da análise; preservar igualdade/cópia dos envelopes cadastrados e não alterar `resolve_recipe` legado. Não deixar o retorno transitório incondicional de ausência no caminho final elegível ao fallback.
- [ ] Escolher o primeiro perfil compatível na ordem da política e construir receita com `_validate_recipe`, `DEFAULT_LIMITS`, `environment={}`, `npm ci`, preparo vazio e `npm test` no cwd do pacote. `tools` tem exatamente Node/Vitest/npm; Vite fica no perfil de proveniência.
- [ ] Preencher `unit_scope.packages` com path/name/scope comprovados e referências de evidências existentes. `external_services` registra diagnóstico e incerteza quando cabível, sem inventar auditoria aprovada para satisfazer campo. O escopo só fica approved pela regra explícita; `resolved` não é decisão científica.
- [ ] Para incerteza sem receita representável, produzir envelope `not_evaluated`, recipe/hash nulos, catalog hash real; diagnósticos com códigos estáveis: `AUTO_RECIPE_INPUT_UNSUPPORTED`, `AUTO_RECIPE_ENVIRONMENT_UNSUPPORTED`, `UNIT_SCOPE_UNRESOLVED`, `AUTO_RECIPE_PREPARATION_UNRESOLVED`. Erros do store preservam seu código específico.
- [ ] Produzir proveniência de tentativa quando snapshot/inputs estiverem verificados; antes disso, omiti-la e publicar diagnóstico. Perfis incompatíveis usam `environment_profile=null`; não inventar versões.
- [ ] Testar estabilidade e imutabilidade das entradas com duas resoluções e ordem de inventário invertida:

```python
assert first == second
assert first[0]['recipe_sha256'] == _hash(first[0]['recipe'])
assert first[0]['catalog_sha256'] == catalog['sha256']
assert first[0]['recipe']['phases']['installation'] == [
    {'argv': ['npm', 'ci'], 'cwd': 'application/local-app'}]
assert catalog == catalog_before
```

- [ ] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_validation_recipe.py tests/test_validation_recipe_policy.py tests/test_validation_recipe_analysis.py tests/test_validation_recipe_resolver.py -q`. Exigir que o trio alvo fora de MATRIX permaneça pendente até a tarefa 8.

### Tarefa 6 — Proveniência e leitor portátil

**Arquivos:** criar `validation_recipe_provenance.py`, `tests/test_validation_recipe_provenance.py`; modificar `validation_format.py`; usar fixtures de publicação existentes.

- [ ] Escrever roundtrip v3 com envelope automático integral e testar legado sem proveniência. Rejeitar proveniência nova em v2.
- [ ] Implementar validação Python pura com chaves/tipos exatos, hashes hexadecimais, paths canônicos, inputs ordenados sem duplicatas, evidências ligadas a inputs, status/code e perfil coerentes com receita.
- [ ] Adulterar separadamente ID, URL, SHA, tree/manifest, lock path/hash, recipe hash, digest/plataforma/versões do perfil e snapshot. Cada caso deve lançar `ValueError`, não apenas alterar uma decisão.
- [ ] Testar receita nula com tentativa pendente e snapshot de análise válido. Testar também receita **presente e resolved** com snapshot de execução nulo pela exceção fechada de falha de contexto da seção 4.3: restrições locais de nenhuma execução e diagnóstico correspondente obrigatório no leitor completo.
- [ ] No leitor local validar fases not_started/start-end nulos/commands vazios, environment/observação vazios, locks vazios, critérios dinâmicos pendentes e contagens desconhecidas. No leitor completo vincular diagnóstico `phase=context`, code `RECIPE_CONTEXT_UNAVAILABLE`/`RECIPE_CONTEXT_MISMATCH` e evidence `{analysis_snapshot, observed_snapshot, cause}` à mesma origem/proveniência. Remover diagnóstico, aprovar um pré-requisito ou inserir comando iniciado deve invalidar o 04.
- [ ] Bloquear imports/processos Node e acesso ao store durante a validação dos vínculos novos. Com publicação v3 da política atual, o leitor deve aceitar a prova coerente sem invocar helper, baixar política ou reabrir snapshot.
- [ ] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_validation_recipe_provenance.py tests/test_validation_pair.py -q`. Confirmar que a validação não promete rederivar AST a partir de hashes.

### Tarefa 7 — Pré-resolução antes da abertura do 04

**Arquivos:** modificar `validation.py`, `validation_evaluation.py`, `validation_format.py`, `tests/test_validation.py`, `tests/test_validation_pair.py`, `tests/test_validation_cli.py`.

- [ ] Criar teste de grupos mistos: cadastrado, automático resolvido e ausência pendente. Comparar limits publicados com receita efetiva e exigir null sem receita:

```python
for item, repository in zip(rows[0]['limits'], repository_rows):
    recipe = repository['recipe']['recipe']
    assert item['limits'] == (None if recipe is None else recipe['limits'])
```

- [ ] Pré-resolver grupos sequencialmente e guardar somente envelopes/diagnósticos/referências. Emitir `run_started` depois de concluir a pré-resolução e derivar limits desses envelopes.
- [ ] Manter `load_repository_context` no laço original, exatamente uma vez por grupo, sem carregar/analisar contextos de todas as origens antecipadamente. Usar spies e objetos sentinela para comprovar descarte dos dados da análise reduzida.
- [ ] Receber loader em variável temporária e comparar snapshot completo antes de atribuir `execution.verified` ou chamar `prepare_workspace`. Em erro/mismatch, descartar retorno, manter snapshot de execução nulo, receita/proveniência/hash/limits inalterados e diagnóstico de contexto exato da seção 4.3. Assertar zero chamadas a workspace, configure_instrument e fases/comandos do executor.
- [ ] Ajustar a derivação no avaliador para contexto automático ausente não aprovar `required_preparation`, mesmo com lista de preparo vazia. Conservar scope estático quando comprovado; todos os critérios dinâmicos e critérios das instâncias ficam pendentes, contagem desconhecida. Não mudar PAIR_POLICY, run_result ou fluxos históricos cadastrados.
- [ ] Criar dois testes end-to-end de publicação: loader lança erro após receita resolvida; loader retorna referência completa diferente após receita resolvida. Em ambos chamar `validate_output_records(rows)` e exigir 04 válido, receita resolved conservada, limits iguais à abertura, snapshot nulo, diagnóstico vinculado e nenhum executor candidato iniciado. Um grupo seguinte válido deve continuar processado.

```python
assert repository['recipe'] == planned_envelope
assert repository['snapshot'] is None
assert rows[0]['limits'][0]['limits'] == planned_envelope['recipe']['limits']
assert repository['decision'] == 'not_evaluated'
assert all(p['status'] == 'not_started' and p['commands'] == []
           for p in repository['execution'].values())
assert candidate_calls == []
assert validate_output_records(rows) == rows[-1]
```

- [ ] Para cada publicação anterior, remover o diagnóstico de contexto, tornar observed_snapshot incoerente com o código (null no mismatch ou não nulo no unavailable), ou tornar cause inválido (code/message ausente ou vazio); exigir `ValueError`. Não alegar detectar mudança para outra mensagem livre válida só pelo hash. Inserir snapshot divergente em repository.snapshot, evidência dinâmica ou pré-requisito aprovado também deve invalidar o 04, sem alterar limits para fazê-lo passar.
- [ ] Simular erro de pré-resolução em uma origem seguido de outra resolvida; exigir fechamento de ambas, diagnóstico local e processamento da segunda. Preservar cleanup/cancelamento, inclusive ao fechar o gerador após abertura.
- [ ] Confirmar que grupos cadastrados não usam o novo helper e que policy_version=1 mantém comportamento histórico de resolução pelo catálogo; fallback automático pertence somente ao caminho atual v3/política 2.
- [ ] Testar todos pendentes: resultado continua `failed`. Testar caso local aprovado com falha não associada: política local continua igual, sem gate de environment=node ou recursos.
- [ ] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_validation.py tests/test_validation_pair.py tests/test_validation_cli.py tests/test_pipeline_status.py -q`. Não mudar `run_result` nem a continuação de pipeline sobre `failed`.

### Tarefa 8 — Prova real do trio alvo antes de habilitá-lo

**Arquivos:** criar `tests/test_validation_recipe_runtime.py`, `tests/fixtures/recipe_runtime/`; modificar `validation_instrument.py` e política somente após prova; tocar bootstrap apenas com necessidade demonstrada.

**Permissão:** a aprovação da implementação abrange esta tarefa, downloads e instalações das fixtures sintéticas e dependências confiáveis previstas; não solicitar autorização adicional. A tarefa não executa o candidato real nem substitui results. Nesta entrega documental, não executar nenhum desses passos.

- [ ] Criar pacote mínimo próprio com Vitest `4.1.11`, Vite `8.0.16`, jsdom `29.1.1`, config `defineConfig` jsdom/setup/globals/alias e testes TSX locais. Fixar dependências auxiliares da fixture no lock e preservar seus hashes; não copiar aplicação ou resultado do alvo.
- [ ] Preparar lock confiável da fixture por npm autorizado, e depois usar exclusivamente `npm ci` no container da imagem por digest. Registrar `node --version`, `npm --version`, plataforma, image ID e versões resolvidas. Não inferir os valores pela tag.
- [ ] Implementar teste opt-in, skip por padrão, habilitado por `ROBO_RECIPE_RUNTIME=1`; passar somente a fixture e a imagem aprovada pelo caminho real de workspace/executor/instrumento. Não relaxar MATRIX em produção para conseguir rodar o teste: a fixture pode injetar o trio candidato em uma matriz de teste isolada.
- [ ] Executar script original `npm test` uma vez em cada cenário independente. Comprovar `original_argv`, censo completo, bootstrap, identidades, quatro casos distintos associados à implementação original e ausência de retries corretivos:

```python
assert instance['test_cases']['valid_count'] == 4
assert instance['criteria']['original_target_execution']['status'] == 'approved'
assert instance['criteria']['associated_tests_execution']['status'] == 'approved'
assert instance['decision'] == 'approved'
```

- [ ] Criar cenários independentes negativos: hook associado falha após quatro casos; mock substitui alvo; só hook chama função; retry verde após falha; relatório/censo incompleto. Exigir respectivamente rejeição ou pendência conforme contrato existente, nunca aprovação por default.
- [ ] Omitir pool/isolate na fixture orientadora e registrar os valores efetivos. Comparar com execução controle da mesma fixture/config, sem instrumentação, para provar preservação, sem executar candidato. Testar também pool explícito não suportado: pendência/limitação, nunca alteração para forks. Uma execução de controle da fixture não autoriza segunda passagem corretiva da suíte candidata.
- [ ] Se pool efetivo incompatível impedir a prova, manter trio desabilitado e registrar a limitação, sem pedir decisão intermediária para simplesmente conservar a pendência. Consultar o usuário somente se for proposta ampliação de escopo para integrar outro pool. Não mudar a fixture para fingir equivalência com o alvo.
- [ ] Durante implementação aprovada, após verificar ferramentas, executar no PowerShell: `$env:ROBO_RECIPE_RUNTIME='1'; .\.venv\Scripts\python.exe -m pytest tests/test_validation_recipe_runtime.py -q; $env:ROBO_RECIPE_RUNTIME=$null`. Exigir **nenhum skip** nos cenários esperados; registrar imagem, ferramentas, censo e resultados.
- [ ] Somente se todos os cenários passarem, adicionar `(22.23.4, 4.1.11, 8.0.16)` a MATRIX e o perfil `node@sha256:539d4d5b9c0df96bb9d1229264d358dc816e8592cb8867828a580b527e7ad0fa`, `linux/amd64`, Node `22.23.4`, npm `10.9.9` à política. Se a prova contradisser esses valores, não habilitar: revisar perfil/spec com o usuário.
- [ ] Reexecutar testes das tarefas 2, 5 e 7 e os testes existentes do instrumento. Reconhecimento estático sozinho nunca conclui esta tarefa.

### Tarefa 9 — Replay, deduplicação e dataset

**Arquivos:** testes `test_validation_pair.py`, `test_deduplication_input.py`, `test_dataset.py`, `test_dataset_format.py`; código de replay/dataset somente se houver perda demonstrada.

- [ ] Publicar fixture v3 automática, carregar pelo leitor downstream e montar dataset. Exigir cópia integral do envelope nas duas posições já existentes:

```python
assert row['environment']['recipe'] == repository['recipe']
assert row['validation']['repository_evidence']['recipe'] == repository['recipe']
assert row['environment']['recipe']['provenance']['kind'] == 'automatic'
```

- [ ] Adulterar proveniência em só uma cópia do dataset; `dataset_format` deve recusar divergência. Adulterar o envelope do 04 consumido pelo 05; leitor deve recusar corrupção, não descartar proveniência.
- [ ] Reproduzir fixture histórica v2→v3 com `replay_pair_records`; assertar que não chama resolvedor/helper/store e que ausência histórica continua ausência. Não expandir API para replay v3→v3 nesta entrega.
- [ ] Confirmar que apenas instâncias já elegíveis pelos contratos atuais entram na deduplicação/dataset; receita automática resolvida sem evidência dinâmica não cria representante.
- [ ] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_validation_pair.py tests/test_deduplication_input.py tests/test_dataset.py tests/test_dataset_format.py -q`.

### Tarefa 10 — Documentação e regressão integrada

**Arquivos:** atualizar README e estes documentos com evidências futuras; não marcar tarefas concluídas por antecipação.

- [ ] Documentar catálogo primeiro, fallback limitado, combinações suportadas, novas pendências, origem automática no 04 e diferença entre resolver e aprovar. Explicar que jsdom/setup/alias não são vetos e que pool não é forçado.
- [ ] Documentar ausência de preparo especulativo, preservação de lifecycle e impossibilidade de garantir aprovação das 13 funções do alvo.
- [ ] Executar `.\.venv\Scripts\python.exe -m pytest -q` e `npm test` no auxiliar confiável, após implementação autorizada. Identificar testes runtime opt-in não executados; não reportar skips como prova dinâmica.
- [ ] Conferir diff limitado às responsabilidades previstas, integridade dos arquivos Docker preexistentes e ausência de mudança no catálogo por execução. Não instalar candidatos para satisfazer essa regressão.
- [ ] Registrar comandos, resultados, limitações e evidência da tarefa 8 no plano; distinguir fixture sintética de validação real de candidato.

## 3. Paralelismo e revisões

Histórico: revisão documental independente `APPROVED` após as correções de escopo, contexto, locks, autorizações e ordem incremental. As instruções originais abaixo foram posteriormente alteradas pela decisão do usuário de cancelar novas revisões; consulte o resumo para as revisões efetivamente feitas.

- [ ] Usar um agente implementador **novo por tarefa**, com contratos e arquivos delimitados. Ordem de escrita: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10. Aguardar integração e ambas as revisões de uma tarefa antes de iniciar a escrita da seguinte; esse corte evita conflitos em resolver, policy, format e testes compartilhados.
- [ ] Permitir pesquisa somente leitura em paralelo sobre gramática/fixtures/runtime/downstream; o coordenador avalia resultados e os fornece ao agente da tarefa correspondente. Não permitir agentes escrevendo simultaneamente nos mesmos arquivos, nem preparação paralela de fixtures que altere locks/política compartilhados.
- [ ] Depois de cada tarefa, despachar revisão de aderência; corrigir achados e reapreciar. Somente então despachar revisão de qualidade; corrigir e reapreciar antes de integrar/avançar. Revisor não implementa silenciosamente correções nem aprova o próprio trabalho.
- [ ] Revisor de aderência: confrontar precedência, loader uma vez, limite antes da abertura, subset npm, política atual, portabilidade e ausência de aprovação implícita com a spec. Devolver achados com arquivo/linha e requisito violado.
- [ ] Revisor de qualidade/segurança: examinar caminhos, limites, JSON estrito, subprocessos/helper sem execução de config, versões/lock, memory retention, cleanup e adulterações de proveniência. Revisão de qualidade só depois de resolver desvios de spec.
- [ ] Coordenador avalia os pareceres, reproduz os testes relevantes e registra como cada achado foi corrigido ou por que não procede. Se só houver autorrevisão, declarar a revisão independente pendente.

## 4. Gate manual — execução real ainda NÃO autorizada

Este gate não é parte da entrega documental nem deve ser executado automaticamente após os testes.

- [x] Usuário revisou spec/plano e autorizou implementação.
- [ ] Usuário realiza ou autoriza backup e substituição do 04. Confirmar preservação de `results/` e `.cache/robo-minerador/snapshots`, registrando caminhos de backup e hashes dos arquivos antes de sobrescrever; não inventar backup concluído.
- [ ] Só com autorização, executar `docker compose run --rm --build minerador --validate --overwrite`. O build refere-se à imagem confiável do minerador, não a `npm run build` do candidato. Não repetir `--all`, descoberta, triagem ou extração para testar a receita.
- [ ] Examinar o 04 com leitor atual: proveniência/locks/imagem, limits, diagnósticos por grupo, censo, contagens e `run_finished`. `failed` continua falha e impede avanço. `partial` não significa todas as candidatas aprovadas.
- [ ] Somente se o 04 for íntegro e elegível pelas regras atuais, e o usuário autorizar substituir as saídas dependentes, executar `docker compose run --rm minerador --deduplicate --overwrite`; conferir o 05 antes de `docker compose run --rm minerador --build-dataset --overwrite`. Não reutilizar 05/06 antigos como se viessem do 04 novo.
- [ ] Usuário roda/aprova o resultado final. Só depois de autorização explícita preparar o **único commit**, com inspeção de status/diff/histórico e seleção consciente dos arquivos; nunca incluir secrets ou resultados não autorizados. Este documento não autoriza staging, commit ou push.

## 5. Matriz de cobertura e encerramento documental

| Requisito da spec | Tarefa que demonstra |
| --- | --- |
| Precedência, identidade e seis receitas intactas | 1, 5, 7 |
| Política determinística, SemVer, pins e digest | 2, 3, 5, 8 |
| Snapshot offline, bounded reads e escopo local | 3, 4 |
| Gramática documental fechada, CI conservadora e locks fora do pacote | 4, 5 |
| Npm frozen, lifecycle e nenhum preparo inventado | 4, 5, 8 |
| Envelope/hash/proveniência portáteis | 5, 6, 9 |
| Abertura limits, loader original e snapshot igual | 7 |
| Loader falha/mismatch: receita conservada e 04 válido sem execução | 6, 7 |
| Trio alvo só após instrumento/censo/bootstrap reais | 8 |
| Política local, quatro casos, hooks/mocks e sem retry | 7, 8 |
| Replay legado e preservação em dataset/dedup | 6, 9 |
| Sem sétima saída, sem maquiagem failed | 7, 9, 10 |
| Backup, aprovação e commit único tardio | Gate manual |

O plano original e sua matriz ficam preservados como histórico. O resumo inicial registra a implementação e as provas efetivas; execução de candidato e aprovação de resultado real não decorrem dessas provas.

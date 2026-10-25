# T10 — relatórios Jest/Vitest

## Matriz comprovada

Execuções Linux/amd64 em Docker, Node **22.22.3**, npm **10.8.2**,
TypeScript **5.8.3**. Cada linha representa uma patch executada, sem extrapolação
para outras versões do mesmo major.

| Framework | Patch | Modo comprovado | Transformação fixada | CJS | ESM | Concorrência | Workspace |
|---|---|---|---|---|---|---|---|
| Jest | 29.7.0 | `cjs-ts-jest`, circus, `--runInBand` | ts-jest 29.4.5 | testado | não implementado | não avaliada | não avaliado |
| Jest | 30.0.0 | `cjs-ts-jest`, circus, `--runInBand` | ts-jest 29.4.5 | testado | não implementado | não avaliada | não avaliado |
| Vitest | 3.2.4 | `esm-vite`, pool forks, localização habilitada | Vite 6.1.0 | não implementado | testado | não avaliada | não avaliado |
| Vitest | 4.0.0 | `esm-vite`, pool forks, localização habilitada | Vite 6.1.0 | não implementado | testado | não avaliada | não avaliado |

Os quatro `package-lock.json` incluem integridades e dependências transitivas,
incluindo os peers compatíveis de ts-jest. As fixtures são material de engenharia,
não fontes do dataset. `scripts/lock_framework_fixtures.py` resolve locks dentro
da imagem npm congelada, sem executar scripts de instalação no host.

Cada baseline apresenta 10 ocorrências: quatro linhas parametrizadas de título
idêntico, skipped, todo, falha esperada, dois testes convencionais homônimos e um
teste convencional com assertion de exceção. São **7 casos elegíveis no runtime**;
o vínculo direto à função depende de T11. Uma execução adicional da fixture
`assertion.case.ts` apresenta 1 caso failed e nenhum passed, com exit code 1.

## APIs e contratos

1. Preparar o archive original uma vez com `DockerExecutor.prepare(plan, archive)`.
2. Chamar `run_fresh(prepared_id, argv, limits, supplements={nome: bytes})`.
   Os módulos observacionais entram por Docker API numa camada suplementar
   `/opt/robo-reporters/`, root-owned, modo 0444, sob root filesystem read-only.
   O archive do projeto conserva fontes e configuração originais. Cada suplemento
   recebe um digest em `ExecutionResult.supplements`. A imagem temporária é removida
   com `noprune=True`, preservando a preparação para novas execuções.
3. Jest: `node node_modules/jest/bin/jest.js --runInBand
   --testLocationInResults --reporters /opt/robo-reporters/jest.cjs`.
   Vitest: `node node_modules/vitest/vitest.mjs run
   --reporter /opt/robo-reporters/vitest.mjs`.
4. Recuperar `workspace/.robo-reports/raw.json` do workspace capturado. Em exit
   não zero, usar `ProjectExecutionFailure.result`, que conserva logs e workspace.
5. `frameworks/jest.ts` e `frameworks/vitest.ts` exportam
   `parseReport(raw, originalSources, rawEvidenceDigest, emitCaseResult?)`.
   O callback opcional recebe cada caso normalizado, inclusive exclusões.
   O CLI estático
   `dist/src/frameworks/normalize.js` recebe por stdin
   `{raw, sources: {relativePath: originalText}, evidence_id}` e devolve JSON.
   Ele não importa módulos nem executa configuração do projeto.
6. Python: `load_report(normalizedBytes, rawBytes, artifactStore)` valida o schema
   **1.0**, identidades entre linguagens, tipos estritos, versões de schema,
   caminhos e vínculo ao digest bruto. Devolve `FrameworkReport` frozen, com
   `cases: tuple[CaseResult, ...]` e `normalized_evidence_id`.

`CaseResult` conserva framework/patch, arquivo relativo, posição original UTF-16,
caminho de títulos, índice da ocorrência parametrizada, ID de runtime, status,
duração em ms, `expected_failure`, `mapping` e evidência. A identidade SHA-256 usa
um array JSON canônico desses componentes. O índice é atribuído às ocorrências
coletadas/reportadas pelo framework; o AST nunca determina o número de linhas.
Jest recebe um ordinal por declaração, independente da ordem de conclusão dos
arquivos. Vitest conserva o ID da task. Títulos repetidos não colapsam.

Jest usa `onRunComplete`; Vitest usa `onTestRunEnd` e snapshots das tasks de
`TestModule.task`, comprovados nas duas patches. Os snapshots preservam metadados
do framework e resultados, retirando referências circulares `file`/`suite`.
Jest não expõe `failing` em todos os resultados: a correlação AST registra
`test.failing` na declaração. Vitest conserva `task.fails`. Falha esperada nunca
vira aprovação normal. Skipped/pending/todo e resultados ausentes não contam.

Para T13, `SuiteResult(ordinal, outcome, exit_code, evidence_id)` é frozen,
serializável e separado da contagem por caso. `outcome` admite `passed`, `failed`
ou `interrupted`; não deriva da existência de casos passed. T10 não executa a
política de três repetições.

## Escopo e limitações

Os relatórios conservam argv, configuração, arquivos observados, versão do
compilador e transformação. `scope = unknown` e
`completeness = not_evaluated`: uma reprodução suplementar com exit zero não
aprova completude da suíte original. T13 deve cruzar comando/configuração,
coleção e resultados originais antes dessa decisão.

Os reporters registram hashes SHA-256 dos arquivos originais. A correlação exige
igualdade com os bytes UTF-8 fornecidos ao analyzer; fontes divergentes não contam.
Localizações de runtime são correlacionadas univocamente ao intervalo do callee
AST no texto original. Posições são UTF-16, linha base 1 e coluna base 0 no
protocolo estático; coordenadas de stack dos frameworks são convertidas durante
a correlação. Não há busca por título para reparar localização ausente. Sem
localização confiável, com declaração dinâmica não reconhecida, alias não
suportado, transformação diferente ou modo desconhecido, o mapeamento fica
`not_evaluated`. Isso é limitação do adaptador, não reprovação factual do projeto.
Arquivos com `.concurrent` não recebem mapeamento aprovado nesta versão.
Modos fora dos enums comprovados recebem `unimplemented_configuration`;
workspace e concorrência exigem novas provas antes de habilitação.

O workspace limitado usa `exec` explicitamente para dependências nativas
(esbuild/resolver do Jest 30); `/tmp` e o controle do supervisor conservam
`noexec`, e a raiz permanece read-only.

## Evidências e reprodução

Artefatos de execução são locais e ignorados pelo Git. O export T10 usa
`results/artifacts/frameworks/<framework>-<patch>/` por padrão, configurável por
`ROBO_FRAMEWORK_EVIDENCE`. O export T11 usa
`results/artifacts/direct-calls/<framework>-<patch>/`, configurável por
`ROBO_DIRECT_CALL_EVIDENCE`. Esses diretórios conservam relatórios, logs, ledgers
e objetos CAS; os antigos diretórios de exports em `tests/fixtures/` são ignorados
para impedir sua reentrada no versionamento.

O store completo permanece no volume `miner-evidence` ou no diretório temporário
do teste. O export pequeno conserva resolvers explícitos para objetos grandes no
volume; não promete recuperar `node_modules` a partir do export. IDs de imagens
preparadas são locais e efêmeros; a imagem base do catálogo permanece congelada.

Os testes offline constroem entradas controladas em
`tools/ts-analyzer/test/framework-fixtures.ts`, usando as fontes legíveis e locks
em `tests/fixtures/projects/`. As ocorrências são explícitas, independentes da
contagem AST. Python gera sua cadeia CAS em `tmp_path`, com archives, hashes,
overlays e ledgers verificados pelas APIs reais. Nenhum teste offline depende de
cache ou export prévio, e suas entradas não constituem prova de execução Docker.
Os testes Docker verificam os reporters, o isolamento e as quatro patches reais;
T11 verifica 28 casos, 24 elegíveis, 18 vínculos e gates negativos por patch.

Verificações PowerShell:

```powershell
npm.cmd test --prefix tools/ts-analyzer
npm.cmd run typecheck --prefix tools/ts-analyzer
python -m uv run pytest tests/unit tests/integration -m "not docker" -q
python -m uv run pytest tests/integration/test_framework_runtime.py -m docker -q
```

Para renovar o material exportado, definir `ROBO_REFRESH_FRAMEWORK_REPORTS=1`
antes do teste Docker, escolher um destino ignorado com `ROBO_FRAMEWORK_EVIDENCE`,
executar o build do analyzer e depois
`python -m uv run python scripts/export_framework_evidence.py`.
Cada preparação instala dependências uma vez; os testes seguintes reutilizam a
preparação em contêineres novos, offline, incluindo a prova de suplemento read-only.

Para T11, executar `tests/docker/test_direct_calls.py` na imagem miner congelada,
com fontes atuais montadas read-only e `ROBO_T11_EVIDENCE=/evidence/t11`.
`scripts/export_direct_call_evidence.py` copia os objetos pequenos desse volume,
verifica seus hashes e registra os resolvers dos objetos grandes. Os exports
continuam sendo evidências de engenharia, nunca itens do dataset.

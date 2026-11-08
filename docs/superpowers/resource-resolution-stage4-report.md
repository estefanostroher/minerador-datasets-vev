# Auditoria de recursos: correções e replay offline da etapa 4

## Resultado

As correções removem bloqueios espúrios de resolução, apagamento de tipos e manifests. As quatro instâncias continuam **pending** (`INSTANCE_RESOURCE_PROOF_UNAVAILABLE`) no replay post-hoc. O interpretador fechado ainda não demonstra os efeitos dos algoritmos completos; a configuração do anur também chama plugins sem modelo. Não há garantia prévia de aprovação nem nova execução dos candidatos.

O replay leu `results/04_validacao.jsonl` em streaming e usou somente fontes, manifesto, observação, receita e ambiente persistidos. Node/TypeScript do minerador fizeram análise AST; nenhum módulo candidato foi importado/executado. As associações, contextos e alvo reconstruídos conferem com os originais. Os demais critérios e contagens são identificados como resultados retidos da execução original, não recertificados por uma nova passagem.

Relatório detalhado, com método anterior/novo, critérios antes/depois, hashes, fechamento, localizações e inventário sintático diagnóstico:

`C:\Users\Estefano\AppData\Local\Temp\opencode\resource-replay-stage4-report.json`

Script de reprodução offline:

```powershell
.\.venv\Scripts\python.exe "C:\Users\Estefano\AppData\Local\Temp\opencode\resource_replay_stage4.py"
```

O inventário sintático anexo não é certificado de efeitos: ele documenta também operações posteriores ao primeiro ponto em que cada módulo perde a prova.

## Causas confirmadas e correções

1. **Extensões:** os dois analisadores concatenavam sufixos a `./file.js`, procurando `file.js.ts` em vez de `file.ts`. Compartilham agora `validation-resource-resolution.mjs`, que deriva candidatos no inventário original e recusa ambiguidades, declarações `.d.ts` como implementação, escapes, percent-encoding, queries e backslashes. Remapping `.js→.ts/.tsx`, `.jsx→.tsx`, `.mjs→.mts`, `.cjs→.cts` exige loader modelado. Na prova local, o binding Vite e o carregamento do destino por hash continuam obrigatórios. Não há escolha pelo primeiro glob encontrado.
2. **Tipos:** type aliases, interfaces e `import type`/`export type` eram tratados como execução desconhecida. São apagados; bindings `type` em imports/reexports mistos são separados dos bindings de valor. Um import misto continua fechando o módulo e seus efeitos. Classes, campos, inicializadores, assertions de tipo com expressão executável, namespaces e callbacks opacos continuam auditados/pendentes.
3. **Manifesto:** a presença de `exports`/`imports` bloqueava o pacote inteiro, embora os imports pertinentes fossem relativos e não consultassem esses campos. O manifesto continua no fechamento. Metadados de resolução não utilizados não são efeitos. **Uso de alias, package self-reference ou condição de exports/imports sem modelo permanece pending**; não há implementação geral da resolução de pacotes. `pretest`/`posttest` e scripts unitários fora do modelo continuam impedindo prova. A declaração de dependência não aprova seu pacote inteiro.
4. **TypeScript:** `paths` não utilizado deixa de bloquear todos os imports relativos. Usar um alias continua desconhecido. `extends` relativo continua fechado; plugins, baseUrl/rootDirs/moduleSuffixes não modelados continuam bloqueando. NodeNext/customConditions permanecem nos arquivos originais; o remapping implementado é o subconjunto relativo do loader, não uma substituição geral de todas as regras do compilador.
5. **APIs:** modelos nominais de `node:assert/strict` cobrem assertions específicas sobre dados e `match`/`doesNotMatch` com regexp literal. Não liberam callbacks, APIs arbitrárias ou argumentos opacos. Filesystem assíncrono cobre somente `mkdtemp/readFile/writeFile/rm` de proveniência temporária fechada. `open`, caminhos opacos e leituras de código continuam desconhecidos.
6. **Dados originais:** leitura UTF-8 de JSON válido via `new URL(caminhoLiteral, import.meta.url)` cria aresta `snapshot_data`; exige texto e bytes/modo vinculados ao snapshot. `JSON.parse` sem reviver só aceita string conhecida. Não libera escrita no snapshot, execução dos dados ou carregamento dinâmico. O JSON original dos vetores macrotracker passa a integrar o fechamento.
7. **Transitivos:** reexports estáticos e shorthand de dados são modelados; colisões de export, namespaces e capacidades dentro de objetos permanecem bloqueados. Falhas agora incluem binding, linha e expressão para distinguir causa primária de cascata.
8. **Configuração:** antes, falhar ao interpretar uma configuração removia seu registro AST; o hash ausente era então confundido com bytes divergentes do loader. Também se comparava setup efetivo com um conjunto vazio inventado. O hash passa a vir dos textos originais independentemente da compreensão de efeitos; configuração opaca produz diagnóstico de ausência de derivação. A aprovação continua impossível enquanto seus efeitos/setups não forem demonstrados.

## Replay individual e limites remanescentes

| Instância | Casos originais | Antes | Depois offline |
|---|---:|---|---|
| `0e60090700d343eb06ae2d7bc0d18531f3ccdd1add61cc44c8116a41cff213b3` | 11 | pending | pending |
| `7f322382e852b39a77c740d10988b6abe6b34dffbdd2b8a627e63628c9cbd5b3` | 12 | pending | pending |
| `43e49b48cebbc9c7321394312ed995d060e960cdcdc31f08e137f38be7064147` | 13 | pending | pending |
| `0719b126f7e343559fb5cdcc7e01c5ac21e263aa46d08e2b273caa3ca1bd41bf` | 12 | pending | pending |

### Anur

- `ledger/transcript.ts` deixa o fechamento executável: só era alcançado por `import type {Turn}`. Seu `node:fs/promises.open` **não foi declarado seguro**; o efeito não pertence a este fechamento após apagamento.
- O alias `@shared/*` não é usado neste grafo; não se inventou resolução para ele.
- `ledger/items.ts:38`: `WRITERS.indexOf` não possui modelo de método de array. Importar `isOpen` em `extract.ts:6` e depois `apply` em `extract.test.ts:8` perde prova em cascata porque a análise integral de `items.ts` foi interrompida, não porque esses bindings de valor sejam tipos.
- `ledger/mermaid.ts:48`: `SHAPES[t] ?? ["[", "]"]` exige acesso indexado e bifurcação de dados. O restante contém Map/Set, callbacks recursivos, mutação e métodos de string/regexp sem modelo integral. Nenhuma função foi declarada pura por identidade/hash.
- `vite.config.ts:1–6`: imports e chamadas de `@vitejs/plugin-react` e `@tailwindcss/vite`; há também configuração server/proxy/build/rolldown. O binding oficial Vite não modela os efeitos desses plugins. São necessários modelos de efeitos específicos vinculados às implementações efetivas; ignorar plugins ou apenas observar ausência de rede violaria o contrato.
- Os antigos avisos de loader hash, falta de cobertura e setups divergentes desaparecem. Permanecem corretamente a configuração sem derivação de efeitos/setups e as lacunas dos módulos acima.

### Três instâncias macrotracker

As três alcançam o mesmo arquivo associado inteiro, `packages/shared/test/engine.test.ts`; portanto compartilham estes bloqueios pertinentes:

- `src/engine/local-date.ts:12`: `LOCAL_DATE.exec`; o corpo também usa `Date.UTC`, `new Date`, conversões e formatação. Literal regexp não concede automaticamente essas capacidades.
- `src/engine/models.ts:58`: `s.trendWeightKg`; `src/engine/program.ts:31`: `a.weightKg`. Parâmetros abstraídos como dados opacos não têm modelo de leitura de propriedades/proveniência suficiente. Tipagem TS não é prova de ausência de getter/capacidade.
- `adaptive.ts:52–57`: `if`, spread, sort com callback, Math, indexação/ternário. `replay.ts:50–80`: Object.keys, reduce, estado mutável e loop `for`. O interpretador atual foi desenhado para dados imutáveis e callbacks limitados; certificar esses algoritmos exige semântica de efeitos/estado e consumidores fechados, não uma whitelist de nomes de função.
- `check-in.ts:2`, `adaptive.ts:8`, `replay.ts:2` e `engine.test.ts:3` perdem bindings por essas falhas transitivas. O barrel `index.ts` e os caminhos remapeados agora são atravessados; não há lacuna de fechamento/carregamento detectada pelo replay.
- `engine.test.ts:29–31`: a leitura do JSON de vetores agora tem destino e bytes no fechamento. O restante do teste usa recursão em `expectClose`, default parameter, throw, Set e métodos não compreendidos. Esses pontos constam no inventário AST; o primeiro bloqueio operacional do interpretador ainda ocorre no import transitivo.
- Não surgiu impedimento de launcher: a receita salva é `pnpm test:unit`, delegação raiz `pnpm -r test:unit` e script do pacote `vitest run`. No anur é `npm test`/`vitest run`. Não foram autorizadas flags desconhecidas por fallback.

**Limite demonstrado:** corrigir resolução e apagamento de tipos não torna este interpretador um analisador geral de JavaScript mutável. Os bloqueios acima são ausência de modelos de prova, não descoberta de serviço real proibido. Não foram classificados como `rejected`. Reexecutar agora não resolve essas lacunas. Não foi identificada falta de fonte/hash/carregamento pertinente que, sozinha, exija nova execução para este diagnóstico; para plugins, uma futura extensão de modelos pode exigir fingerprints de implementações que o método atual não coleta.

## Contrato de versões

- `closed_unit_effects`: versão **2**.
- `associated_resource_closure`: versão **2**.
- O discriminador `associated_closure_v1` continua identificando a política de escopo; a versão do algoritmo é dada pelos modelos e hashes do método, não pelo discriminador.
- O método global inclui `resolver_sha256`; o local inclui esse método, hash do fechamento e hash do produtor Python. O novo `.mjs` já é abrangido por `typescript/*.mjs` no package-data e `validation-*.mjs` na materialização do instrumento; não houve alteração no empacotamento preexistente.
- Hash do método local anterior em 04: `2cc373f27dcc4e75b7a9911c31f07904b89ab81a4509c5ccff027cad37f3163b`.
- Hash do método local deste replay: `92d3ba77f9938dadd39341dec5523270f7ce084fb87b442a355160ead29c0175`.
- Métodos antigos não são aceitos como assinatura do produtor novo. O avaliador retorna prova indisponível para método divergente; o leitor portátil pode recusar o registro histórico se seu critério publicado exigir uma prova que esse método não rederiva. Os formatos v1/v2 históricos não foram migrados nem reescritos. Replay criptográfico histórico exige o método/bytes antigos correspondentes; esta alteração **não promete** recertificá-los pelo método novo. O relatório post-hoc é artefato separado e não substitui 04.

## Verificação

As cinco regressões iniciais falharam antes das correções. Reexports/shorthand, assert.match, localização de diagnóstico, configuração opaca e leitura JSON/percent-encoding tiveram falhas red específicas antes do respectivo green. As reduções são próprias; nunca executam código candidato.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_validation_local.py tests/test_validation.py tests/test_validation_evidence.py tests/test_validation_cli.py tests/test_validation_input.py tests/test_validation_recipe.py tests/test_validation_resources.py tests/test_validation_docker.py tests/test_validation_resources_local.py -q
# 269 passed, 13 skipped in 58.45s

node --test src/robo_minerador/typescript/tests/validation-plan.test.mjs src/robo_minerador/typescript/tests/validation-transform.test.mjs src/robo_minerador/typescript/tests/validation-resources.test.mjs src/robo_minerador/typescript/tests/validation-vitest.test.mjs src/robo_minerador/typescript/tests/validation-resource-resolution.test.mjs
# 23 tests; 19 pass; 0 fail; 4 skipped
```

Incluem falta de cobertura do destino remapeado, setups opacos, serviço real com confirmação nativa, unknown, falsificação de fechamento/config/modelo/hash do resolver e adulteração de bytes JSON originais. Docker opt-in não foi executado nesta correção; os skips não são nova comprovação da matriz. `git diff --check` retornou 0 para arquivos rastreados, com avisos LF/CRLF preexistentes.

## Preservação

Hashes conferidos antes/depois do replay:

```text
04_validacao.jsonl        8583e67d25988767df885bc56de30e335a1ae74f92deaa8838bafb0f8004dcf0
03_extracao.jsonl         c9b95889dbf54af10826134b9ae033f6d087edf698220b64d837acbe3018890d
03_extracao-original.jsonl 0ada94ae6000a42c78652d432b2bb35d3075d2b44f62d17056feafc9f5c34187
```

A execução 04 preservada termina em `2026-11-08T16:06:12.770472Z`, com 190 instâncias, 0 approved, 3 rejected e 187 pending. Não houve escrita em results/snapshots, execução/instalação de candidatos, piloto, full gate, review posterior, agentes, branch, worktree, commit ou push.

Se futuramente for necessária e autorizada nova passagem após ampliar os modelos, guardar cópia de 04 antes do comando abaixo (a CLI não faz esse backup automaticamente):

```powershell
if (Test-Path -LiteralPath "results/04_validacao.pre-resource-v2.jsonl") { throw "Destino de backup já existe." }
Copy-Item -LiteralPath "results/04_validacao.jsonl" -Destination "results/04_validacao.pre-resource-v2.jsonl" -ErrorAction Stop
.\.venv\Scripts\python.exe -m robo_minerador validate --overwrite
```

Esses comandos de backup/execução são orientação para o usuário; não foram executados nesta tarefa. O backup deve usar destino ainda inexistente.

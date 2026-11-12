# Deduplicação exata — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. As instruções explícitas do pesquisador abaixo prevalecem sobre workflows genéricos de testes, worktrees e commits.

**Goal:** Implementar a deduplicação exata de todas e somente as instâncias aprovadas no 04, preservando origens e publicando o contrato auditável do 05.

**Architecture:** Python lê e confere integralmente 03/04, expande as instâncias selecionadas e constrói representações canônicas exatas dos artefatos/contexto. O agrupamento usa SHA-256 como índice e igualdade integral de bytes como confirmação, com representante mínimo por ID. A CLI prepara e valida todos os registros antes de abrir a única saída JSONL.

**Tech Stack:** Python >=3.11, biblioteca padrão (`json`, `hashlib`, `re`, `pathlib`, `collections`), leitores/publicador existentes e pytest apenas para riscos necessários. Sem dependências novas, rede, Docker ou execução de candidatos nesta etapa.

---

## 1. Autoridade, execução e limites

Especificação: `docs/superpowers/specs/2026-11-12-deduplicacao-design.md`. As decisões metodológicas já aprovadas não serão redesenhadas pela implementação.

- O coordenador elabora e revisa este plano. **Somente as tarefas de execução abaixo são delegadas.**
- Executor de cada tarefa: subagente `general`. Revisores: novos subagentes `general`, independentes do executor. O pesquisador indica o modelo `gpt-6.1-terra`; selecionar o tipo `general` na ferramenta e não afirmar ter confirmado/trocado o modelo, pois a API disponível não oferece esse controle.
- Usar este checkout e a branch `feature/minerator-pilot`. Não criar branch/worktree auxiliar. Não substituir trabalho alheio. Atribuições de escrita são exclusivas por tarefa.
- Executar T1 → T2 → T3 → T4 → T5. Não começar tarefa dependente antes das revisões da anterior. A execução do minerador continua sequencial; a delegação é organização do desenvolvimento.
- Ao terminar **cada tarefa**, executar review de aderência e depois review de qualidade, ambos por `general`. Corrigir achados com o executor e pedir nova revisão do trecho afetado. O coordenador confere diff e evidências antes de marcar a tarefa como concluída.
- Testes novos somente quando necessários para um risco concreto listado neste plano. Sem TDD obrigatório, teste por função ou meta de cobertura. Reutilizar fixtures e verificações existentes quando adequadas; não criar testes de constantes/getters/serialização trivial.
- Identificadores/código em inglês; comentários/docstrings em português; toda função/método novo, inclusive fixture e auxiliar de teste, tem docstring explicativa.
- Preservar `results/01*`–`04*` e cache. Não executar descoberta, triagem, extração ou validação reais. A única execução real prevista é `deduplicate`, após os gates de T5.
- **Um único commit ao final de tudo**, depois de T1–T5, correções, reviews e verificações; nenhum commit intermediário, nem de docs. Sem push.
- Dúvida metodológica ou contrato incompatível: parar somente o trecho afetado e perguntar ao pesquisador. Falha operacional/integridade do corpus não autoriza reparar dados, alterar critérios ou publicar sucesso.

### Bloco obrigatório de cada delegação

O coordenador envia ao executor/revisor o texto integral da tarefa, os contratos da seção 2 e estas regras: checkout/branch atuais; usar `general`; arquivos atribuídos; não alterar results/cache/arquivos alheios; código inglês/docstrings português; testes apenas justificados; nenhum commit/push intermediário; perguntar diante de ambiguidade. Revisor não escreve código: retorna achados com gravidade, caminho/linha, impacto e correção sugerida, ou `APPROVED` com escopo e verificações efetivamente realizadas.

## 2. Mapa de arquivos e APIs entre tarefas

| Arquivo | Responsabilidade / dono |
| --- | --- |
| `src/robo_minerador/deduplication_representation.py` | T1: projeção dos artefatos, trechos de linha, serialização canônica, política e hash. |
| `src/robo_minerador/deduplication_input.py` | T2: parsing estrito do 04, leitores existentes, vínculos entre entradas, seleção approved e proveniência. |
| `src/robo_minerador/typescript/deduplication-bindings.mjs` | T2, ajuste aprovado: Compiler API confiável para conferir offsets e expressões dos pontos creditados sem executar candidatos. |
| `src/robo_minerador/deduplication_format.py` | T3: validação portátil e leitura do contrato do 05. |
| `src/robo_minerador/deduplication.py` | T3: agrupamento, escolha de representante e registros completos em memória. |
| `src/robo_minerador/config.py` | T4: três caminhos da etapa. |
| `src/robo_minerador/cli.py` | T4: parser, despacho e fronteira de publicação. |
| `tests/test_deduplication_representation.py` | T1: riscos de falsa igualdade e alteração textual. |
| `tests/test_deduplication_input.py` | T2: riscos de seleção e integridade 03↔04. |
| `tests/test_deduplication.py` | T3: riscos de agrupamento, proveniência e corrupção do 05. |
| `tests/test_deduplication_cli.py` | T4: riscos de truncamento, seleção por CLI e ligação real dos componentes. |
| `README.md` | T5: instalação existente, comando, seleção, representação, saída e limites. |
| Este plano e a spec | Coordenador: decisões, checkboxes, reviews e evidências reais. T5 propõe atualizações factuais ao coordenador. |

Não modificar módulos de etapas anteriores para refatoração geral. Se houver necessidade concreta de ajuste em contrato compartilhado, reportar ao coordenador antes de ampliar atribuição.

### APIs fixadas

```python
# deduplication_representation.py
REPRESENTATION_VERSION = "exact_instance_context_v1"
# POLICY é o objeto completo definido em T1; consumidores conferem igualdade exata.
def canonical_bytes(value: object) -> bytes:
    """Serializa JSON canônico sem alterar os textos originais."""

def build_representation(unit: dict, files: dict[str, str]) -> bytes:
    """Projeta a unidade expandida e seu contexto integral em bytes canônicos."""

def representation_digest(raw: bytes) -> str:
    """Calcula SHA-256 da representação; não comprova igualdade isoladamente."""

# deduplication_input.py
def load_deduplication_inputs(validation_path, extraction_path) -> tuple[list[dict], dict]:
    """Confere ambas as publicações e seleciona apenas approved do 04."""

# deduplication.py
def deduplicate_instances(candidates: list[dict], source: dict) -> list[dict]:
    """Agrupa por igualdade integral e prepara toda a publicação antes da escrita."""

# deduplication_format.py
def validate_deduplication_records(records) -> dict:
    """Confere protocolo e vínculos do 05 e retorna seu fechamento."""

def load_deduplication(path) -> tuple[list[dict], dict]:
    """Lê o 05 estrito e devolve todas as instâncias auditáveis e sua abertura."""
```

As assinaturas acima são contratos, não funções vazias a adicionar à aplicação. Cada tarefa implementa corpos completos.

`candidates`: lista de `{"unit": expanded_unit, "files": {path: original_content}}`, um por aprovada do 04. `unit` é a expansão original do 03, não uma unidade sintetizada pelo 04. O mapa contém os arquivos do contexto publicado do repositório; `build_representation` seleciona exatamente os `unit.context_paths`, preservando seus conteúdos. Não anexar ambiente, testes novos ou testemunhas runtime ao objeto comparado.

`source`: objeto pronto para os campos da abertura:

```python
{
    "sources": {
        "extraction": {"path": str(extraction_path), "sha256": "<digest real>", "schema_version": 3, "status": "partial"},
        "validation": {"path": str(validation_path), "sha256": "<digest real>", "schema_version": 3, "status": "partial"},
    },
    "input_instance_count": 190,
    "eligible_count": 4,
    "input_decisions": {"approved": 4, "rejected": 3, "not_evaluated": 183},
}
```

Os valores desse exemplo são ilustrativos; calcular todos dos bytes/fechamentos realmente conferidos. `load_deduplication` devolve **também duplicatas**: a futura construção filtra `decision="approved"` explicitamente.

## 3. T1 — Representação exata dos artefatos e contexto

**Executor:** `general`. **Escrita:** `deduplication_representation.py` e seu teste necessário. **Dependência:** nenhuma.

- [x] Ler a seção 3 da spec, `extraction_format.expand_instance` e `extraction_analyzer._range/_snippet`. Conferir que `_range` faz join com LF e, portanto, não deve ser usado como extrator do trecho exato.
- [x] Implementar `canonical_bytes` com a receita única:

```python
def canonical_bytes(value):
    """Preserva strings e multiplicidade ao produzir bytes JSON determinísticos."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")
```

- [x] Definir `POLICY` exatamente como abaixo, reutilizado sem cópias divergentes por produtor e validador:

```python
POLICY = {
    "version": 1,
    "representation": "exact_instance_context_v1",
    "selection": "validation_instance_approved",
    "context_scope": "all_published_instance_files",
    "text_normalization": "none",
    "validation_environment_compared": False,
    "equality_confirmation": "canonical_bytes",
    "representative_selection": "lexicographic_min_instance_id",
}
```

- [x] Implementar auxiliar de trecho inclusivo de linhas. Reconhecer somente `\r\n|[\n\r\u2028\u2029]`, compatível com a extração/JavaScript; não usar `splitlines()` nem recompor por `"\n".join`. O limite final fica **antes do terminador da última linha selecionada**; terminadores internos permanecem intactos:

```python
breaks = list(re.finditer(r"\r\n|[\n\r\u2028\u2029]", content))
starts = [0, *(match.end() for match in breaks)]
ends = [*(match.start() for match in breaks), len(content)]
# Exigir type(start) is int, type(end) is int e 1 <= start <= end <= len(starts).
original_text = content[starts[start - 1]:ends[end - 1]]
```

- [x] Projetar `function` pelos seis campos da spec; documentos por `kind/path/original_text/association`; testes pelos campos originais listados na spec mais `original_text` do trecho e associação sem posições; arquivos por `path/content`. Na associação dos testes, incluir `kind`, `expression` e `resolved_target={path,name}`. Exigir arquivos/intervalos existentes, sem fallback para texto vazio. Preservar a presença/ausência de `skipped`, não inserir default.
- [x] Objeto canônico de topo: `{"representation_version": REPRESENTATION_VERSION, "function": ..., "documentation": ..., "tests": ..., "context_files": ...}`. Ordenar documentos/testes por `canonical_bytes(item)` e arquivos por caminho; não converter listas para conjuntos. Manter ordem original de `modifiers`. Omitir critérios/razões/IDs/horários/ambiente e posições, sem limpar valores semelhantes encontrados dentro dos textos originais.
- [x] Implementar `representation_digest` como `hashlib.sha256(raw).hexdigest()`, em função isolada para simular colisão no teste do agrupamento sem alterar os hashes de proveniência.
- [x] Criar apenas a matriz necessária: igualdade apesar de mudanças em proveniência/posições/ordem de conjuntos; desigualdade ao mudar código, documentação, teste, configuração, fixture, caminho ou fim de linha; multiplicidade de associações mantida; Unicode astral e surrogate isolado serializáveis; separadores JavaScript versus vertical tab. Justificativa: o corpus atual não contém contraprovas para normalização ou perda de multiplicidade; erro aqui exclui tarefas legítimas.
- [x] Rodar `.\.venv\Scripts\python.exe -m pytest tests/test_deduplication_representation.py -q`. Esperado: todos os cenários acima aprovados, sem rede ou execução de candidatos.
- [x] **Review de aderência (`general`):** conferir campos incluídos/excluídos contra a spec e ausência de normalização. **Review de qualidade (`general` novo):** inspecionar intervalos, multiplicidade, Unicode, erros e alterações não relacionadas. Corrigir e re-revisar antes de T2.

## 4. T2 — Integridade das entradas e seleção exclusiva do 04

**Executor:** `general`. **Escrita:** `deduplication_input.py` e seu teste necessário. **Dependência:** T1 aprovada.

- [x] Ler `validation_input.load_extraction`, `validation_format.validate_output_records`, `validation_evaluation._association/_count_instance` e os planos publicados por `typescript/validation-plan.mjs`. Ler fixtures `test_validation_input.extraction_rows` e `test_validation.published_fixture`: a segunda usa IDs/metadados controlados e **não é por si só um par 03↔04 íntegro**.
- [x] Ler bytes do 04 uma única vez para parsing e hash. Parsing JSONL estrito com `json.loads`, `strict_object`, `strict_float` e recusa de NaN/Infinity; separar por LF, permitir apenas o terminador final vazio, recusar linhas internas vazias. Usar o mesmo parser para o futuro leitor do 05 via auxiliar interno documentado `read_jsonl_bytes(path) -> (rows, raw)` neste módulo; ele não seleciona instâncias nem valida schemas.
- [x] Exigir publicação 04 v3 e política atual antes de chamar `validate_output_records`. Reutilizar o validador sem rederivar decisões sob política nova. Carregar 03 por `load_extraction`, que confere toda a publicação e retorna candidatas da extração mais metadados/hash dos bytes efetivamente consumidos.
- [x] Confrontar `04.run_started.source` com os metadados do 03 em `sha256`, `schema_version`, `status`, `input_count`, `repository_count`, `eligible_count`, `eligible_repository_count`, `decisions`. `path` é apenas proveniência, não condição de identidade do arquivo movido.
- [x] Expandir as candidatas do 03 usando `expand_instance(instance, repository, reasons)`. Criar índices de instância/repositório. Exigir igualdade de conjuntos entre IDs de **todas** as instâncias do 04 e IDs das aprovadas do 03; conferir repositório/URL/SHA de todos os grupos, sem depender de contagens iguais isoladamente.
- [x] Para instâncias aprovadas do 04, exigir a lista completa de `test_cases.associations[*].test` igual à lista projetada por `_association(test)` da unidade do 03, preservando ordem e multiplicidade publicadas. Para as demais, conferir a mesma igualdade quando associações estiverem publicadas; aceitar ausência legítima (lista vazia) quando o contexto não pôde ser carregado. `evaluate_repository` publica associações vazias quando `verified.instances` não contém a unidade: não transformar esse caso válido em corrupção. A integridade de estados/contagens continua sendo conferida pelo validador do 04, e identidades devem corresponder ao 03 em todos os estados. Não exigir alvo em planos de candidatas pendentes cujo ambiente não pôde iniciar.
- [x] Para instâncias aprovadas no 04, exigir alvo no plano com `id/path` corretos e intervalo inteiro UTF-16 positivo dentro da fonte. Conferir que o slice original desse intervalo é exatamente `reference_code`, com linhas correspondentes ao 03. Exigir vínculo das associações creditadas a points/declarations e fontes do contexto: os points preservam associação original e a declaração está no arquivo/intervalo do teste; conferir hashes das fontes em `plan.plans` quando referidas. Após ajuste autorizado pelo pesquisador, conferir a expressão exata do CallExpression no auxiliar confiável `deduplication-bindings.mjs`, sem executar fontes. Não exigir que toda associação sem testemunha tenha point quando a publicação válida registra limitações.

Conversão crítica dos offsets para texto, sem assumir índice Python igual ao índice JavaScript:

```python
raw_utf16 = content.encode("utf-16-le", errors="surrogatepass")
# Antes: exigir type(start/end) is int, 0 <= start < end <= len(raw_utf16) // 2.
snippet = raw_utf16[2 * start:2 * end].decode("utf-16-le", errors="surrogatepass")
```

O vínculo usa identidade, posições, hash e a conferência sintática autorizada: obter o CallExpression no intervalo exato do point e comparar `node.expression.getText(ast)` à expressão da associação; conferir também a declaração original do teste. Parsear somente textos publicados, nunca importar fontes ou executar candidatos. Agrupar a checagem por repositório e reutilizar ASTs por caminho no auxiliar. Exigir Node >=22/TypeScript 6.0.3 somente com aprovadas; limites de 512 MiB de heap e 180 s por invocação. Falha/timeout do auxiliar é erro de entrada/preflight antes da escrita, sem fallback lexical nem reprovação científica. Substituir a aproximação textual anterior e sua regex, preservando regressões `f<T>()` versus `f<T>.foo()`.

- [x] Selecionar somente `record_type == "instance" and decision == "approved"` do 04; construir `candidates` e `source` do contrato da seção 2. A decisão agregada do repositório não filtra. Validar ambas as entradas e o conjunto total mesmo quando a seleção é vazia; zero aprovadas não equivale a zero candidatas de extração.
- [x] Nos testes de unidade de seleção, doubles de leitores podem isolar riscos, mas não substituem a integração íntegra de T4. Reutilizar o produtor v3 de validação para decisões válidas e a fixture de extração para corrupção; não relaxar os leitores em produção para aceitar fixtures incompatíveis.
- [x] Cobrir apenas riscos necessários: aprovada sob repositório rejeitado/entrada partial; rejeitada/pendente textual idêntica fora da seleção; SHA divergente, conjunto de IDs divergente, associação adulterada, origem divergente, schema/política incompatível, JSON com chave repetida/não finito, fechamento ausente; pendente legítima sem plano; UTF-16 com caractere astral antes do alvo; corrupção mesmo com zero aprovadas. Justificativa: testes atuais validam 03 e 04 separadamente, não seu vínculo nem a seleção de deduplicação.
- [x] Rodar `.\.venv\Scripts\python.exe -m pytest tests/test_deduplication_input.py tests/test_validation_input.py tests/test_validation_pair.py -q`. Esperado: testes aprovados e estados de origem preservados.
- [x] **Review de aderência (`general`):** conferir filtro exclusivo, conjunto completo, hash e ausência de novos gates científicos. **Review de qualidade (`general` novo):** conferir parsing, UTF-16, fontes/associações e aceitação de pendências legítimas; registrar custo/uso de auxiliares realmente observado. Corrigir e re-revisar antes de T3.

## 5. T3 — Agrupamento, publicação e leitor portátil do 05

**Executor:** `general`. **Escrita:** `deduplication.py`, `deduplication_format.py` e `tests/test_deduplication.py`. **Dependência:** T1/T2 aprovadas.

- [x] Implementar agrupamento ordenando candidatas por `unit.instance_id`; impedir IDs repetidos. Para cada representação, usar bucket pelo digest e procurar igualdade de bytes completos dentro dele. A primeira ocorrência na ordem por ID é a representante; representações diferentes dentro de bucket colidido formam grupos diferentes. Não usar `digest -> representante` único.

```python
bucket = buckets.setdefault(representation_digest(raw), [])
match = next((entry for entry in bucket if entry["raw"] == raw), None)
if match is None:
    match = {"raw": raw, "representative_instance_id": unit["instance_id"], "count": 0}
    bucket.append(match)
match["count"] += 1
```

- [x] Reter bytes uma vez por representação distinta; não manter objeto JSON expandido duplicado depois da projeção se não for necessário. Contexto integral pode ser grande: não prometer memória limitada por streaming, pois a CLI precisa preparar/validar antes de abrir saída. Não introduzir banco, índices em disco ou limite científico de população.
- [x] Produzir `run_started`, instâncias ordenadas e `run_finished`, todos com `schema_version=1`, `stage="deduplication"` e horário UTC. Abertura: `source` expandido mais cópia de `POLICY`. Instância: todos os campos da seção 4 da spec. Critério: `approved/EXACT_REPRESENTATIVE` ou `rejected/EXACT_DUPLICATE`, com mensagens explícitas em português. Fechamento: `source_sha256={"extraction": ..., "validation": ...}`, `eligible_count`, `representative_count`, `duplicate_count`, `duplicate_group_count`, `instance_decisions={"approved": n, "rejected": m, "not_evaluated": 0}` e `status="completed"`.
- [x] Contar grupos duplicados somente quando seu `count >= 2`, inclusive se dois grupos distintos tiverem hash colidido. Para zero elegíveis, produzir somente abertura/fechamento. Origens `partial` não promovem pendências nem alteram automaticamente o estado desta execução.
- [x] Implementar `validate_deduplication_records`: chaves/tipos/versões/política UTC, origens/hash/estados, abertura/fechamento únicos, ordenação e unicidade dos IDs, campos positivos/zeros válidos sem aceitar bool como int, critério/decisão/disposição coerentes, URLs/SHAs de origem válidos, referências presentes e mínimas do grupo, mesmo hash/tamanho entre membros de um grupo e todas as somas. Refutar ciclos, referência a duplicata como representante, ID faltante, reclassificação sem código e contagem divergente.
- [x] Dois representantes com hashes iguais são permitidos: a confirmação integral pertence ao produtor. O validador portátil não tem os bytes da representação e **não deve afirmar** que verificou igualdade científica só porque hashes coincidem. A reprodução usa `load_deduplication_inputs` + `deduplicate_instances` e compara registros sem timestamps; os caminhos de proveniência podem ser diferentes em checkout movido, sem alterar IDs/decisões/representações.
- [x] Implementar `load_deduplication(path)` por parser estrito de T2 + validador portátil; devolver todas as instâncias e a abertura. Chamar o validador no produtor antes de retornar os registros para a CLI.
- [x] Testes necessários: iguais entre origens diferentes, ordem invertida sem trocar representante, diferentes com hash artificialmente igual e mesmo comprimento, duplicatas em cada um de dois grupos colididos, todas as origens preservadas, saída sem duplicatas, saída vazia, rejeição de corrupção de vínculos/política/totalizadores/estados. Justificativa: igualdade de digest isolada e perda de vínculos causam exclusão irreversível na construção; inspeção de quatro elegíveis reais não cobre essas contraprovas.
- [x] Rodar `.\.venv\Scripts\python.exe -m pytest tests/test_deduplication.py tests/test_deduplication_representation.py tests/test_deduplication_input.py -q`. Esperado: confirmação integral demonstrada, contagens corretas e releitura válida dos registros.
- [x] **Review de aderência (`general`):** comparar saída/casos vazios, agrupamento e origens com a spec. **Review de qualidade (`general` novo):** atacar colisão, integridade de referências, fronteiras do leitor e uso de memória. Corrigir e re-revisar antes de T4.

## 6. T4 — CLI e integração sem truncar saídas

**Executor:** `general`. **Escrita:** `config.py`, `cli.py`, `tests/test_deduplication_cli.py`; auxiliar de fixture apenas nesse teste se necessário. **Dependência:** T1–T3 aprovadas.

- [x] Adicionar somente os caminhos:

```python
DEDUPLICATION_INPUT_PATH = VALIDATION_OUTPUT_PATH
DEDUPLICATION_EXTRACTION_PATH = EXTRACTION_OUTPUT_PATH
DEDUPLICATION_OUTPUT_PATH = Path("results/05_deduplicacao.jsonl")
```

- [x] Registrar subcomando `deduplicate` com `--overwrite` e `--help`; atualizar descrição da CLI. Adicionar `_run_deduplication(args)` e despachar **antes** de `config.validate_workers(args.workers)`, tal como `validate`, pois deduplicate não tem workers. Não acessar token ou preflights de descoberta/extração/validação nesse caminho.
- [x] Conferir saída diferente de ambas as entradas por `resolve()` e, para caminhos existentes, `samefile()` para cobrir aliases/hardlinks. Recusar saída diretório e ancestral existente não diretório. Se saída existir sem overwrite, retornar 2 antes de ler/processar entradas.
- [x] Preparar `candidates, source`, construir lista completa e validar fechamento **antes** de `write_jsonl`. Erro de entrada/configuração retorna 2 com mensagem clara e preserva bytes anteriores mesmo com overwrite. Não imprimir trechos candidatos, ambiente ou payload de evidências em mensagens genéricas.
- [x] Publicar com `write_jsonl(output, records, ensure_ascii=True, compact=True)`. Escrita/cancelamento retorna 1 e informa possibilidade de arquivo incompleto; sucesso retorna 0 e imprime elegíveis, representantes, duplicatas, grupos duplicados e caminho. Não atribuir `partial` da origem ao resultado local.
- [x] Criar uma integração com **03/04 realmente coerentes**: obter publicação de extração v3 de fontes controladas com `make_local_snapshot`/analisador confiável já usados nos testes; fornecer observações simuladas compatíveis com essa unidade por produtor v3, sem executar testes de candidato. Vincular source ao hash real do 03 e metadados íntegros, sem monkeypatch dos leitores/representação/agrupamento/escritor. Se a fixture `published_fixture` for reaproveitada, completar seus metadados e IDs pelo contrato real em vez de aceitá-la incompleta.
- [x] Testes adicionais estritamente necessários na fronteira: saída existente sem autorização não chama leitor; entrada inválida com overwrite preserva bytes; alias saída/entrada não trunca 03/04; falha de escrita/cancelamento retorna 1; zero elegíveis publica validamente sem token/ferramentas externas; opções extras são recusadas; fluxo integrado relê o 05 real e confirma seleção/grupos. Justificativa: mocks de unidade não demonstram proteção antes da abertura nem a composição dos módulos novos.
- [x] Rodar `.\.venv\Scripts\python.exe -m pytest tests/test_deduplication_cli.py tests/test_validation_cli.py -q` e `.\.venv\Scripts\python.exe -m robo_minerador deduplicate --help`. Esperado: testes aprovados, help código 0 e somente opções autorizadas.
- [x] **Review de aderência (`general`):** CLI, códigos de saída, zero elegíveis e filtro exclusivamente do 04. **Review de qualidade (`general` novo):** ordem pré-escrita, alias de paths, cancelamento e integração sem bypass dos leitores. Corrigir e re-revisar antes de T5.

## 7. T5 — Evidências reais, regressões e documentação

**Executor:** `general`. **Escrita:** `README.md`; único resultado operacional novo permitido: `results/05_deduplicacao.jsonl`. **Dependência:** T1–T4 aprovadas. Coordenador mantém este plano e a spec.

- [x] Conferir `git status --short --branch` e executar a bateria proporcional consolidada:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_deduplication_representation.py tests/test_deduplication_input.py tests/test_deduplication.py tests/test_deduplication_cli.py tests/test_validation_input.py tests/test_validation_pair.py tests/test_validation_cli.py tests/test_validation.py -q
```

Esperado: todos aprovados. Se mudanças anteriores atingirem outro consumidor compartilhado, incluir sua suíte pertinente com justificativa. Não rodar automaticamente testes Docker nem toda a suíte lenta por meta de cobertura. Uma falha inicia investigação, não autoriza reduzir contrato ou esconder teste.

- [x] Obter SHA-256 de 03 e 04 antes da execução real e conservar valores em memória/registro desta tarefa. Usar `Get-FileHash -LiteralPath ... -Algorithm SHA256` apenas como cálculo de integridade no terminal, sem criar relatório extra nem ler conteúdos para manipulação por shell.
- [x] Verificar existência do 05 com ferramenta de leitura/metadados. Se não existir, rodar uma vez:

```powershell
.\.venv\Scripts\python.exe -m robo_minerador deduplicate
```

Se o 05 já existir e não tiver sido produzido nesta execução controlada, parar a publicação e pedir autorização antes de `--overwrite`; não assumir que o pedido de implementar autoriza apagar um resultado posterior do pesquisador. Verificar pais antes de comandos que criem arquivos. Saída existente recusada não é evidência de execução concluída.

- [x] Conferir a integridade de 03/04 reais e preservar ambos. Passaram; a alternativa de bloqueio por corrupção não foi necessária. Não houve reparo de dados ou nova validação.
- [x] Ler o 05 por `load_deduplication`; conferir quantidade exatamente igual às aprovadas observadas no 04, IDs aprovados/rejeitados, referência própria dos representantes e origem de cada membro. Para cada grupo duplicado, reconstruir bytes do 03 e verificar igualdade integral; sem duplicatas, conferir cada representante e declarar ausência de duplicatas **nesta população**, sem inventar grupos. Confirmar 03/04 com os mesmos hashes após a execução.
- [x] Reproduzir os resultados em memória com as mesmas entradas e comparar registros desconsiderando somente timestamps. Não repetir a gravação oficial para provar determinismo. Registrar contagens, estado, hashes de entrada e duração efetivamente observada, sem prometer desempenho.
- [x] Atualizar README com estado da etapa, links da spec/plano, comando e `--overwrite`, entradas 03/04 e saída 05, filtro approved por instância, representação exata/contexto integral, ambiente de validação excluído, hash mais igualdade de bytes, representante mínimo, campos/vínculos, casos sem duplicatas/sem elegíveis, códigos de saída e preservação dos artefatos anteriores. Informar que 05 é índice auditável e não o dataset final.
- [x] Enviar ao coordenador os resultados brutos/resumos factuais e a proposta de registro na seção 9 deste plano. Nenhum relatório/dataset sintético paralelo permanente.
- [x] **Review de aderência (`general`):** confrontar evidências com critérios da spec e texto do README. **Review de qualidade (`general` novo):** verificar reprodutibilidade, honestidade das contagens, hashes preservados e alterações não relacionadas. Coordenador resolve/re-revisa achados e confere os comandos/evidências antes de encerrar T5.

## 8. Gate final e único commit — coordenador

Este é o encerramento operacional após as tarefas, não um commit por tarefa. O coordenador usa `verification-before-completion`, confere todos os reviews e revisa o diff completo. Alterações finais de código reabrem a revisão e as verificações afetadas antes de commitar.

- [x] Marcar T1–T5 concluídas somente com seus resultados e reviews efetivos; conferir ausência de pendências metodológicas/integridade.
- [x] Inspecionar, em chamadas independentes, `git status --short --branch`, `git diff`, `git diff --check` e `git log --oneline -10`. Ler arquivos novos não rastreados, pois não aparecem em `git diff` simples. Confirmar que não há segredos, resultados ou cache no conjunto a versionar.
- [x] Preparar **somente arquivos pretendidos**, por caminhos explícitos: spec, plano, README, os quatro módulos novos, config/CLI, auxiliar sintático autorizado e testes novos necessários. Não usar `git add .`; `results/` e cache permanecem ignorados. O auxiliar é a única ampliação de arquivos prevista após a decisão AST registrada em T2.

Com os arquivos previstos criados e conferidos, o staging é:

```powershell
git add -- "docs/superpowers/specs/2026-11-12-deduplicacao-design.md" "docs/superpowers/plans/2026-11-12-deduplicacao.md" "README.md" "src/robo_minerador/deduplication_representation.py" "src/robo_minerador/deduplication_input.py" "src/robo_minerador/typescript/deduplication-bindings.mjs" "src/robo_minerador/deduplication_format.py" "src/robo_minerador/deduplication.py" "src/robo_minerador/config.py" "src/robo_minerador/cli.py" "tests/test_deduplication_representation.py" "tests/test_deduplication_input.py" "tests/test_deduplication.py" "tests/test_deduplication_cli.py"
```

Omitir da lista qualquer teste cuja necessidade tenha sido substituída por verificação existente equivalente, registrando a justificativa; não criar arquivo vazio apenas para satisfazer o comando.

- [x] Inspecionar `git diff --cached` e `git diff --cached --check`; confirmar que o conjunto staged contém toda a entrega e nenhum trabalho alheio. Confirmados 14 arquivos, somente código, testes e documentação, sem resultados/cache.
- [ ] Criar o único commit com mensagem alinhada ao histórico: `add exact instance deduplication`. Não amend, não pular hooks e não push. Se hook recusar, corrigir e revalidar antes de uma nova tentativa, sem criar commits intermediários.
- [ ] Conferir `git status --short --branch` e `git show --stat --oneline HEAD`. Informar hash do commit, verificações, contagens reais, caminhos de saída/documentação e qualquer limitação efetivamente observada.

## 9. Registro de execução e reviews

Estado da entrega: **T1–T5 concluídas e aprovadas; verificação global aprovada**. As contagens ilustrativas das seções de contrato não são resultados novos de deduplicação. Os resultados efetivos são registrados abaixo; nenhum commit intermediário foi criado. Este registro é preparado antes do commit único de encerramento; a identidade desse commit é consultável no histórico Git, sem autorreferência de hash neste arquivo.

| Tarefa | Execução | Review de aderência | Review de qualidade | Evidência |
| --- | --- | --- | --- | --- |
| T1 | Concluída — `ses_e2e45533dffeXZOie8jLF8EWuz` | APPROVED — `ses_e2e41ce81ffe6pmjiNeHPuS4op` | APPROVED — `ses_e2e4136f0ffesBEPJKy3XTVO1y` | 16 testes focados aprovados pelo executor (0,04 s) e revisor de aderência (0,03 s); coordenador inspecionou o módulo de 76 linhas. |
| T2 | Concluída — `ses_e2e401c23ffeY3LZi4Kz0G1MoD`; ajuste AST autorizado pelo pesquisador | APPROVED após correções — `ses_e2e3dadeaffe01LSl1Tldh82GF` | APPROVED após corrigir fronteira do callback — `ses_e2e34a863ffeIuHzRvlhMRJqZg` | 100 testes aprovados independentemente pelo revisor (21,34 s); regressões posteriores rejeitam chamadas em argumento do registro e função diferida: 103 aprovados pelo executor (23,47 s). O helper confere expressão/offsets no primeiro callback inline, excluindo funções aninhadas. Coordenador inspecionou o módulo Python e o helper. |
| T3 | Concluída — `ses_e2e31c0ffffe2UrEW0UWN6Cc5r` | APPROVED — `ses_e2e2f0a48ffeA683iWthK4yGVB` | APPROVED — `ses_e2e2e8ed0ffe2P0DufBpSgcGb2` | 67 testes de agrupamento/representação/entrada aprovados pelo executor (15,86 s). Revisores inspecionaram sem repetir suíte; coordenador leu produtor e validador completos. |
| T4 | Concluída — `ses_e2e2d7e8fffeO1wCq96s3Sj5aF` | APPROVED — `ses_e2e2854dbffe6HtG759X56JqM6` | APPROVED — `ses_e2e27c8aaffeg4PDf5RD00hvjv` | 38 testes CLI novos/existentes aprovados pelo executor; help código 0 também confirmado pelo revisor. Integração positiva com 03/04 coerentes e todas as fronteiras reais da deduplicação; caso vazio dispensa ferramentas. Coordenador inspecionou o despacho e a proteção pré-escrita. |
| T5 | Concluída — `ses_e2e26b6c8ffesZGGrAfkivR1Hw` | APPROVED — `ses_e2e173f68ffeP2VsCd4jtH77qA` | APPROVED — `ses_e2e16a68fffeV68T7cjrF5lynb` | 207 testes consolidados aprovados (50,34 s). CLI real código 0 em 177,7077305 s: 4 elegíveis, 4 representantes, 0 duplicatas/grupos; reprodução em memória idêntica exceto timestamp (186,834260 s). Ambos os revisores leram o 05; qualidade confirmou os hashes atuais. |

Ao receber cada retorno, o coordenador registra identificadores dos subagentes, comandos, resultados, achados/correções e nova revisão. A tabela é acompanhamento, não autorização para inferir aprovação.

### Evidência real de T5

O 04 continha 190 instâncias: 4 `approved`, 3 `rejected` e 183 `not_evaluated`. Somente as quatro aprovadas foram comparadas; todas permaneceram representantes. O 05 contém seis registros: abertura, quatro instâncias e fechamento `completed`. As fontes 03/04 permaneceram `partial` como proveniência. Nenhum grupo duplicado foi encontrado nessa população; as contraprovas de duplicação e colisão são testes controlados, não observações do corpus real.

| Artefato | SHA-256 |
| --- | --- |
| 03, antes e depois | `c9b95889dbf54af10826134b9ae033f6d087edf698220b64d837acbe3018890d` |
| 04, antes e depois | `202e83a294b8df901062453b23f90f1ff276a485a1cd2ff1520c54a6467526bb` |
| 05 publicado | `74615083095b5a981b0ebd522303ab9a4c30488c2d516e499b5c7393fdfa3145` |

A inspeção verificou IDs elegíveis, proveniência, autorreferências das representantes, tamanhos/digests das representações reconstruídas e igualdade de todos os registros na reprodução em memória, exceto `timestamp`. A CLI foi gravada uma vez, sem overwrite; não houve nova execução de validação, testes candidatos ou aquisição de snapshots. Os resultados e o cache não integram o commit.

### Verificação final do coordenador

Review global independente: **APPROVED**, `general` `ses_e2e140c6bffeb60a62jxfv2lXa`, após inspeção da entrega completa e do empacotamento do auxiliar `.mjs`. O coordenador repetiu a bateria consolidada da seção 7: **207 testes aprovados em 50,96 s**, e confirmou `deduplicate --help` com código 0. Também confirmou diretamente os três SHA-256 da tabela e leu os seis registros reais do 05. A revisão global não repetiu os testes nem a execução real. Alterações posteriores a esse review limitaram-se à atualização factual deste registro e dos checkboxes de execução.

## 10. Conferência de cobertura do plano

| Requisito da spec | Tarefa / gate |
| --- | --- |
| H-D01: igualdade exata de implementação/documentação/testes/contexto; sem near-duplicate | T1, T3 |
| Somente approved do 04; repositório agregado não filtra | T2, T4, T5 |
| Hash do 03, conjunto de IDs, origem e associação verificáveis | T2, integração T4 |
| Textos exatos, caminhos e multiplicidade, ambiente 04 fora da comparação | T1, T2 |
| SHA-256 não substitui igualdade integral; representante determinístico | T3 |
| Um registro por elegível, todas as origens e leitor portátil do 05 | T3, T5 |
| Partial de origem, zero duplicatas e zero elegíveis | T2, T3, T4 |
| Proteção de saída/overwrite, configuração e erros antes da escrita | T4 |
| Evidência real sem modificar 01–04/cache; documentação de limites | T5 |
| Subagentes general, reviews por tarefa, testes necessários, commit único | Seção 1, gates T1–T5 e seção 8 |

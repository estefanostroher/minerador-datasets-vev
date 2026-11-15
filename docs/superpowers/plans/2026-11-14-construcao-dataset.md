# Construção do dataset — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. O pesquisador autorizou subagentes de implementação e revisor independente após cada tarefa, com um único commit ao final de toda a construção aprovada.

**Goal:** Publicar um único `results/06_dataset.jsonl`, com uma linha completa por representante aprovado, preservando artefatos originais e calculando métricas estáticas.

**Architecture:** Validar a cadeia 01–05, reproduzir a representação exata do 05 e expandir os artefatos selecionados. Um auxiliar TypeScript confiável calcula tamanho e complexidade, e um montador puro produz registros determinísticos. A CLI só abre a saída após todas as verificações; um leitor próprio aceita o dataset sem arquivos intermediários.

**Tech Stack:** Python >=3.11, argparse, json, hashlib, pytest, Node.js >=22 e TypeScript 6.0.3 já utilizados pelo projeto. Sem dependência nova.

---

## 1. Contrato e condições de execução

Spec aprovada: `docs/superpowers/specs/2026-11-14-construcao-dataset-design.md`. Ler integralmente antes de implementar. As decisões da conversa e dessa spec prevalecem sobre sugestões antigas de mutação, rejeição global e esquema MBPP.

Este plano registra a implementação e suas evidências. Na implementação:

- Trabalhar no checkout atual, sem criar branch ou worktree auxiliar. Não fazer commits intermediários. O pesquisador autorizou um único commit ao concluir toda a construção, após aprovação dos revisores; não fazer push.
- Preservar 01–05 e cache. Não executar `discover`, `triage`, `extract`, `validate` ou `deduplicate` sobre dados reais.
- Usar nomes de código em inglês e comentários/docstrings em português, seguindo os módulos adjacentes.
- Criar somente testes necessários para riscos científicos e de integridade descritos abaixo. Reutilizar fixtures e cobertura existentes; a lista de casos é uma matriz de riscos, não obrigação de criar um teste novo por item. Não impor TDD, teste por função, testes que espelhem a implementação ou meta de cobertura.
- Executar os testes pertinentes existentes e os novos que forem justificados pelo risco da tarefa. Nenhum resultado esperado abaixo é evidência de execução já realizada.
- Uma execução real do novo construtor poderá criar o 06 quando ele não existir. Se existir, obter autorização antes de usar `--overwrite` no dado real.

### Subagentes, revisões e condição de liberação

Usar subagentes `general` com atribuições delimitadas. O coordenador mantém o contexto, confere o diff e integra os resultados; o subagente implementador não pode aprovar o próprio trabalho nem fazer commit.

Para cada uma das Tarefas 1–8:

1. Despachar um implementador com a tarefa integral, spec, interfaces, arquivos permitidos e verificações necessárias. Solicitar resumo, arquivos alterados, comandos/resultados e riscos remanescentes.
2. Ao terminar a tarefa, despachar um revisor independente `general`, com acesso ao diff, spec e requisitos da tarefa. A revisão deve cobrir aderência, qualidade, integridade científica, suficiência dos testes e preservação de dados; retornar `APPROVED` ou achados concretos com severidade e localização.
3. Havendo achados, o implementador corrige o trecho afetado e o revisor verifica novamente. Não liberar tarefa dependente com revisão pendente ou reprovada. Não criar testes extras por rotina: o revisor deve justificar o risco que qualquer teste adicional cobre.
4. O coordenador registra aprovação e evidências por tarefa neste plano, sem commits intermediários.

As tarefas têm dependências e devem seguir a ordem definida. Não executar edições simultâneas nos mesmos arquivos. Na Tarefa 1, o revisor pode aprovar as fixtures válidas e os testes de contrato ainda sem implementação, desde que a falha esperada esteja identificada e não seja apresentada como suíte passando.

Após a Tarefa 8, um revisor independente deve conferir o conjunto integrado e a construção real, além das aprovações individuais. O commit final depende de todas as tarefas concluídas, verificações pertinentes passando e revisões sem achados pendentes. A autorização de commit é para essa conclusão, não para a entrega do plano.

### Mapa de arquivos e responsabilidades

| Arquivo | Ação e responsabilidade |
| --- | --- |
| `src/robo_minerador/config.py` | Acrescentar seis caminhos da construção. |
| `src/robo_minerador/dataset_input.py` | Ler e vincular 01–05; selecionar representantes; recuperar metadados. |
| `src/robo_minerador/dataset_metrics.py` | Executar e verificar o protocolo do auxiliar de métricas. |
| `src/robo_minerador/typescript/dataset-metrics.mjs` | Analisar AST original e calcular métricas, sem importar candidatos. |
| `src/robo_minerador/dataset.py` | Montar registros finais, sem I/O ou alteração das decisões. |
| `src/robo_minerador/dataset_format.py` | Validar registros e ler JSONL final estrito. |
| `src/robo_minerador/dataset_cli.py` | Coordenar proteção de caminhos, preparação e publicação. |
| `src/robo_minerador/cli.py` | Registrar e despachar `build-dataset`. |
| `src/robo_minerador/jsonl.py` | Acrescentar `sort_keys` opcional, com padrão compatível. |
| `src/robo_minerador/deduplication_input.py` | Diferenciar falha operacional do auxiliar preservando compatibilidade com ValueError. |
| `tests/dataset_fixtures.py` | Construir cadeia controlada coerente, sem usar dados reais ou rede. |
| `tests/test_dataset_input.py` | Proteger vínculos, seleção e metadados. |
| `tests/test_dataset_metrics.py` | Proteger fronteira Python/Node e classificação de erros. |
| `src/robo_minerador/typescript/tests/dataset-metrics.test.mjs` | Verificar cálculo e localização AST. |
| `tests/test_dataset.py` | Verificar preservação e determinismo do montador. |
| `tests/test_dataset_format.py` | Verificar leitura isolada e corrupção de registros. |
| `tests/test_dataset_cli.py` | Verificar CLI, publicação e integração com leitores reais. |
| `README.md` | Documentar estado, comando, conteúdo e limites do 06. |

`pyproject.toml` já distribui `typescript/*.mjs`; não é necessário ampliar o padrão nem incluir testes Node no pacote. O script npm existente já executa `tests/*.test.mjs`.

### Interfaces fixas entre tarefas

```python
# dataset_input.py
def load_dataset_inputs(discovery_path, triage_path, extraction_path,
                        validation_path, deduplication_path) -> dict:
    """Devolve a cadeia conferida e os artefatos dos representantes."""

# dataset_metrics.py
class DatasetToolError(RuntimeError):
    """Representa impedimento operacional do auxiliar confiável."""

def measure_instances(candidates: list[dict]) -> dict:
    """Devolve size e cyclomatic_complexity indexados por instance_id."""

# dataset.py
def build_dataset(inputs: dict, measurements: dict, *, builder_version: str) -> list[dict]:
    """Monta registros ordenados sem I/O, decisões novas ou relógio."""

# dataset_format.py
def validate_dataset_records(records: list[dict]) -> int:
    """Valida registros completos e retorna a quantidade, inclusive zero."""

def load_dataset(path) -> list[dict]:
    """Lê apenas o dataset, sem Node, rede ou arquivos intermediários."""

# dataset_cli.py
def run_dataset(args) -> int:
    """Prepara e verifica tudo antes de publicar a saída configurada."""
```

As assinaturas acima são contratos, não funções vazias a copiar para o produto. Cada tarefa implementa sua responsabilidade antes de liberar a dependente.

`inputs` tem exatamente `candidates`, `sources` e `deduplication_policy`. Cada candidata contém `unit`, `files`, `extraction_repository`, `validation_instance`, `validation_repository`, `source`, `representative` e `duplicate_origins`. `unit` é a instância expandida; `files` mapeia caminho para texto de todos os arquivos publicados do repositório, sem reduzir `unit.context_paths`.

`sources` tem as cinco entradas descritas na spec: `path`, `sha256`, `status` e `schema_version`, com `null` para versões não declaradas. `measurements[id]` tem apenas `size` e `cyclomatic_complexity` nos envelopes da spec.

## Tarefa 1 — Preparar uma cadeia de entradas verificável

**Arquivos:** criar `tests/dataset_fixtures.py` e iniciar `tests/test_dataset_input.py`; consultar `tests/test_deduplication_cli.py:positive_inputs`, `tests/test_validation_input.py:extraction_rows`, `tests/test_triage.py` e `tests/test_extraction_input.py`.

- [x] **1.1 — Montar entradas positivas controladas.** Reutilizar `positive_inputs(monkeypatch, tmp_path)` para produzir 03/04 com plano e testemunhas reais do instrumento confiável. Criar registros 01/02 com a mesma identidade usando os formatos das fixtures de triagem; licença aprovada com `spdx_id="MIT"`, estrelas `0` e README ausente conhecido. Publicar em diretório temporário, com `write_jsonl`.
- [x] **1.2 — Vincular hashes na ordem correta.** Definir `02.run_started.input_sha256` a partir dos bytes do 01 e `03.run_started.input_sha256` a partir dos bytes do 02. Após alterar o início do 03, atualizar `04.run_started.source.sha256`; não alterar a observação científica. Só então produzir o 05 com `load_deduplication_inputs` e `deduplicate_instances`. Esse encadeamento evita uma fixture que passe apenas porque validadores foram mockados.

```python
raw = extraction_path.read_bytes()
validation_rows[0]["source"]["sha256"] = hashlib.sha256(raw).hexdigest()
write_jsonl(validation_path, validation_rows, ensure_ascii=True, compact=True)
candidates, source = load_deduplication_inputs(validation_path, extraction_path)
write_jsonl(deduplication_path, deduplicate_instances(candidates, source),
            ensure_ascii=True, compact=True)
```

- [x] **1.3 — Verificar a fixture com leitores reais.** Conferir 01 com `triage.load_discovery`, 02 com `extraction_input.load_triage`, 03 com `validation_input.load_extraction`, 04 com `validation_format.validate_output_records` e 05 com `deduplication_format.load_deduplication`. Retornar caminhos por nome e ID selecionado; não depender do diretório `results`.
- [x] **1.4 — Escrever os primeiros testes de seleção e cadeia.** A fixture positiva deve selecionar o ID aprovado mesmo com origem `partial`; uma alteração dos bytes do 01 sem atualizar o 02 deve impedir leitura antes de Node. O caso vazio deve usar publicações válidas sem instâncias, e não arquivos vazios como entradas 01–05.

```python
def test_changed_discovery_bytes_invalidate_chain(dataset_chain):
    """Impede ligar metadados de uma coleta diferente ao dataset."""
    paths, _ = dataset_chain
    paths["discovery"].write_bytes(paths["discovery"].read_bytes() + b" ")
    with pytest.raises(ValueError):
        load_dataset_inputs(*(paths[name] for name in (
            "discovery", "triage", "extraction", "validation", "deduplication")))
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_input.py -q
```

Se os testes forem escritos antes do módulo, a falha inicial esperada é a ausência de `dataset_input`; após a Tarefa 2, os casos de cadeia devem passar. Verificar separadamente a validade das fixtures com os leitores existentes.

## Tarefa 2 — Validar entradas, seleção e metadados

**Arquivos:** criar `src/robo_minerador/dataset_input.py` e iniciar `src/robo_minerador/dataset_metrics.py` com `DatasetToolError`; modificar pontualmente `src/robo_minerador/deduplication_input.py`; completar `tests/test_dataset_input.py` para erros do auxiliar existente.

- [x] **2.1 — Distinguir falha operacional existente sem quebrar consumidores.** Definir primeiro `DatasetToolError(RuntimeError)` em `dataset_metrics.py`, conforme o contrato de interfaces. Acrescentar `BindingVerificationUnavailable(ValueError)` em `deduplication_input.py`. Em `_verify_bindings`, manter a mensagem fixa atual, mas levantar essa subclasse em falhas de preflight, timeout, subprocesso ou protocolo. Erros de vínculos já comprovados em Python continuam `ValueError`. O dataset traduz somente a subclasse para `DatasetToolError`; a deduplicação existente continua capturando `ValueError`.

```python
class BindingVerificationUnavailable(ValueError):
    """Indica que o auxiliar não conseguiu concluir a conferência dos vínculos."""

# No adaptador do dataset, após as checagens baratas da cadeia:
try:
    candidates, linked = load_deduplication_inputs(validation_path, extraction_path)
except BindingVerificationUnavailable as error:
    raise DatasetToolError("Não foi possível conferir os vínculos TypeScript.") from error
```

- [x] **2.2 — Ler os cinco arquivos estritamente antes da seleção.** Usar `read_jsonl_bytes` para recusar duplicidade de chaves e não finitos, inclusive no 01 antes de chamar seu leitor legado. Validar estados, protocolos e conjuntos de população com os leitores existentes. Aceitar somente os eventos atuais de descoberta (`run_started`, `run_finished`, `incomplete_results`, `search_saturated`, `api_error`, `sha_unavailable`) e os tipos admitidos pelo contrato; conferir início e fim. Não reescrever leitores globais para necessidades exclusivas do construtor.
- [x] **2.3 — Implementar a cadeia 01→02→03→04→05.** Comparar hashes dos bytes lidos e metadados publicados, não caminhos. Exigir igualdade das populações correspondentes da seção 3 da spec; usar SHA canônico em minúsculas na comparação de identidades legadas sem editar evidências originais. Rejeitar conjuntos com IDs repetidos, URLs/SHAs conflitantes, origens trocadas ou registros extras/ausentes.
- [x] **2.4 — Reproduzir grupos do 05.** Depois de conferir 03/04, chamar `deduplicate_instances(candidates, linked)`. Comparar todos os registros de instância reproduzidos com os do 05, ignorando somente `timestamp`; conferir abertura e fechamento com seus validadores. Não comparar timestamps novos nem selecionar grupo só pelo hash.

```python
def without_timestamp(record):
    """Remove somente a informação volátil da reprodução do índice."""
    return {key: value for key, value in record.items() if key != "timestamp"}

expected = [without_timestamp(row) for row in reproduced
            if row["record_type"] == "instance"]
actual = [without_timestamp(row) for row in published_instances]
if expected != actual:
    raise ValueError("Deduplicação diverge dos artefatos de origem.")
```

- [x] **2.5 — Construir candidatas finais e fontes.** Indexar registros de 03/04 por repositório e instância; expandir `unit` pelo helper existente. Vincular representante aos registros originais 04 e 05; vincular todas as suas duplicatas como origem. Retornar candidatas ordenadas, sem incluir duplicatas como linhas finais.
- [x] **2.6 — Recuperar metadados históricos.** `source.license={"spdx_id": ..., "criterion": ...}` usa o critério aprovado integral do 02, com SPDX permitido e evidência identificável. Nome e estrelas vêm do 01; README do 02 deve preservar seu valor original dentro do envelope da spec. Ausência de valor opcional resulta em `unavailable`; `0` e `False` resultam em `available`. Não recorrer à licença preliminar do 01.
- [x] **2.7 — Cobrir corrupção com testes parametrizados.** Alterar um hash em cada elo, ID, URL, SHA, política do 05, representante, multiplicidade, licença e estado publicado. Testar entrada sem fechamento, chave JSON repetida e NaN. Para seleção, inserir aprovadas duplicadas, rejeitadas e pendentes sem plano; nenhuma pendência deve exigir plano inexistente. Reutilizar casos da deduplicação para colisão simulada de hash e igualdade integral.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_input.py tests/test_deduplication_input.py tests/test_deduplication.py -q
```

Esperado: seleção e cadeia corretas; testes anteriores continuam aceitando a subclasse de `ValueError`.

## Tarefa 3 — Calcular métricas sobre a AST original

**Arquivos:** criar `src/robo_minerador/typescript/dataset-metrics.mjs`, `src/robo_minerador/typescript/tests/dataset-metrics.test.mjs` e `tests/test_dataset_metrics.py`; completar `src/robo_minerador/dataset_metrics.py` iniciado na Tarefa 2.

- [x] **3.1 — Fixar o protocolo do auxiliar.** Entrada `{files: [{path, content}], targets: [{instance_id, path, start, end, kind, reference_code}]}`; offsets `start/end` são UTF-16 do alvo conferido no plano do 04. Saída `{policy: "typescript_function_metrics_v1", typescript: "6.0.3", measurements: [{instance_id, physical_lines, cyclomatic_complexity}]}`. Um alvo ausente, duplicado ou divergente invalida a chamada inteira.
- [x] **3.2 — Escrever testes AST independentes.** Importar a função exportada `measureTargets` no teste Node. Construir alvos a partir de nós TypeScript da fixture, usando `node.getStart(ast)` e `node.end`, não offsets contados por Python. Casos mínimos: função linear=1; `if`=2; `else if`=3; cada laço; dois `case`=3; `catch`=2; ternário=2; cada operador lógico/atribuição=2; opcional=1; decisões aninhadas excluídas; método; comentários e CRLF; emoji antes do alvo; sintaxe inválida e alvo trocado recusados.

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import ts from 'typescript';
import { measureTargets } from '../dataset-metrics.mjs';

test('não conta decisões do callback aninhado', () => {
  const content = 'function f(x) { const g = () => x ? 1 : 2; return g(); }';
  const ast = ts.createSourceFile('a.ts', content, ts.ScriptTarget.Latest, true);
  const node = ast.statements[0];
  const result = measureTargets({
    files: [{ path: 'a.ts', content }],
    targets: [{ instance_id: 'a'.repeat(64), path: 'a.ts',
      start: node.getStart(ast), end: node.end, kind: 'function',
      reference_code: node.getText(ast) }],
  });
  assert.equal(result.measurements[0].cyclomatic_complexity, 1);
});
```

- [x] **3.3 — Localizar a declaração por AST.** Criar `SourceFile` com `ts.createSourceFile`, rejeitar `parseDiagnostics`, indexar funções declaradas e métodos concretos por início/fim. Exigir nó do tipo correto e `getText(ast) === target.reference_code`. Não usar parser de snippet isolado, regex, import dinâmico ou execução do arquivo.
- [x] **3.4 — Implementar o visitante de decisões.** Aplicar a seguinte regra ao corpo do alvo localizado; não percorrer parâmetros ou decoradores.

```javascript
/** Conta decisões no corpo, excluindo unidades de fluxo aninhadas. */
function complexity(body) {
  let value = 1;
  const logical = new Set([
    ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken,
    ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.AmpersandAmpersandEqualsToken,
    ts.SyntaxKind.BarBarEqualsToken, ts.SyntaxKind.QuestionQuestionEqualsToken,
  ]);
  /** Visita somente os nós do fluxo da unidade selecionada. */
  function visit(node) {
    if (ts.isFunctionLike(node) || ts.isClassDeclaration(node) || ts.isClassExpression(node)) return;
    if (ts.isIfStatement(node) || ts.isForStatement(node) || ts.isForInStatement(node)
        || ts.isForOfStatement(node) || ts.isWhileStatement(node) || ts.isDoStatement(node)
        || ts.isCaseClause(node) || ts.isCatchClause(node) || ts.isConditionalExpression(node)
        || (ts.isBinaryExpression(node) && logical.has(node.operatorToken.kind))) value += 1;
    ts.forEachChild(node, visit);
  }
  visit(body);
  return value;
}
```

- [x] **3.5 — Contar tamanho sem documentação anterior.** Usar linha de `node.end - 1` menos linha de `node.getStart(ast)` mais 1. Assim, comentário anterior fica fora e terminador após `}` não adiciona linha. Não modificar o texto para calcular.
- [x] **3.6 — Implementar a fronteira Python.** Retornar `{}` sem preflight com lista vazia. Agrupar por repositório/SHA, enviar apenas os arquivos dos alvos e executar Node confiável com `--max-old-space-size=512`, timeout de 180 s por lote e pipes explícitos `stdin=PIPE, stdout=PIPE, stderr=PIPE` em `Popen`. Usar `Popen.communicate(input=payload, timeout=180)`; no timeout ou `KeyboardInterrupt`, terminar o processo e recolhê-lo antes de propagar. Não deixar auxiliar ativo após cancelamento. Parsear saída estrita e conferir política, versão, conjunto exato de IDs e inteiros reais positivos (não aceitar bool). Converter para envelopes de métricas da spec.
- [x] **3.7 — Classificar falhas.** O CLI do auxiliar deve retornar código 2 para fonte/alvo inválido e código 1 para falha interna; Python traduz o primeiro em `ValueError` e falhas de ferramenta/preflight, timeout, saída malformada ou código 1 em `DatasetToolError`, com mensagens fixas. Não publicar stdout/stderr arbitrários nem métricas parciais. Não há tentativa corretiva.

```powershell
node --test "src/robo_minerador/typescript/tests/dataset-metrics.test.mjs"
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_metrics.py -q
```

Esperado: regras numéricas acima comprovadas; ferramenta não chamada no vazio; processo recolhido no cancelamento.

## Tarefa 4 — Montar registros completos e determinísticos

**Arquivos:** criar `src/robo_minerador/dataset.py` e `tests/test_dataset.py`.

- [x] **4.1 — Testar preservação antes de montar.** A partir da cadeia verificada, comparar os valores originais da função/documentos com o resultado; comparar cada arquivo de `context_paths` com `context.files`. Usar um teste com duas associações da mesma declaração para impedir perda de multiplicidade; incluir parametrizado e ignorado em fixture controlada.
- [x] **4.2 — Implementar `build_dataset`.** Exigir igualdade entre IDs das candidatas e das métricas. Copiar objetos com `deepcopy`, ordenar candidatas por ID e arquivos por caminho. Usar `_snippet` para `tests[].original_text`; não converter testes em asserts independentes.

```python
def enriched_tests(unit, files):
    """Acrescenta texto original sem reduzir as associações estáticas."""
    return [{**deepcopy(test), "original_text": _snippet(test, files)}
            for test in unit["tests"]]

def context_files(unit, files):
    """Expande todos os textos publicados da instância e seus hashes."""
    return [{"path": path, "content": files[path],
             "sha256": hashlib.sha256(files[path].encode("utf-8")).hexdigest()}
            for path in sorted(unit["context_paths"])]
```

- [x] **4.3 — Incorporar ambiente e evidências integrais.** `environment` copia `recipe`, `environment`, `lockfiles` e `execution` do repositório 04. `validation.instance` recebe o registro integral 04; `validation.repository_evidence` recebe seu repositório integral; `validation.extraction_criteria` recebe os critérios expandidos do 03. Não usar `unit.validation` como validação dinâmica. Não remover provas globais necessárias para resolver os planos e referências locais.
- [x] **4.4 — Incorporar métricas e proveniência.** `metrics.size` e `cyclomatic_complexity` vêm de `measurements[id]`; `test_cases` e `coverage` são cópias integrais do 04. `source`, snapshot, requisitos e deduplicação seguem os blocos da spec. `provenance.builder={"name": "robo-minerador", "version": builder_version, "policy": "dataset_build_v1"}`; a CLI fornece `importlib.metadata.version("robo-minerador")`. `metric_policy` explicita nomes de método, TypeScript fixado e as regras de pontos de decisão, aninhamento e tamanho, sem timestamp novo.
- [x] **4.5 — Comprovar pureza e determinismo.** Antes/depois, comparar a estrutura de entrada com uma cópia profunda. Montar duas vezes com os mesmos valores e conferir igualdade e serialização canônica idêntica. Não sortear IDs, chamar relógio ou consultar Git/Node dentro do montador.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset.py -q
```

Esperado: todos os artefatos e scopes preservados; nenhuma mutação nas entradas; binários não incorporados.

## Tarefa 5 — Validar e ler o JSONL final isoladamente

**Arquivos:** criar `src/robo_minerador/dataset_format.py` e `tests/test_dataset_format.py`.

- [x] **5.1 — Fixar validação estrutural.** Exigir exatamente os campos de primeiro nível da seção 5 da spec, tipos reais, versão 1, TypeScript, IDs únicos/ordenados, caminhos únicos no contexto e coerência da origem. Recusar `text`, `prompt`, `specification`, linhas de execução e versões misturadas. Esquema vazio é `[]` e quantidade 0.
- [x] **5.2 — Validar conteúdo e referências internas.** Conferir SHA-256 de cada texto, intervalos/trechos de testes e documentos, arquivo e código da função, alvo/plano do 04, motivos expandidos e igualdade dos espelhos em `environment`, `metrics` e `validation`. Não exigir que binários referenciados estejam em `context.files`; não tratar referência ao snapshot como conteúdo disponível.
- [x] **5.3 — Reutilizar validação de evidências sem fabricar execução.** Chamar `validation_format._repository` e `_instance` sobre os registros integrais embutidos, com `PAIR_POLICY["resource_gate_scope"]`; validar schema/identidade antes dessas chamadas. Não construir uma execução artificial com contagens globais reduzidas, pois o repositório pode ter outras instâncias fora da linha. Se a importação privada exigir adaptador, mantê-lo neste módulo, sem duplicar o algoritmo científico. Não executar conferência Node no leitor portátil.
- [x] **5.4 — Validar métricas e deduplicação.** Conferir envelopes, método fixado, inteiros positivos, cobertura idêntica ao registro aprovado e `test_cases` sem coerções. Conferir representante autorreferente aprovado, duplicatas rejeitadas apontando para ele, IDs mínimos e representação digest/comprimento do representante recalculados com `build_representation`. O leitor isolado não comprova igualdade dos bytes das outras origens; essa prova pertence ao produtor da cadeia.
- [x] **5.5 — Implementar leitura estrita.** Arquivo zero bytes retorna `[]`; fora disso, usar `read_jsonl_bytes`, exigir um objeto JSON válido por linha e chamar `validate_dataset_records`. Aceitar JSON válido com espaços de apresentação para leitura sem exigir igualdade de bytes com o escritor; determinismo da escrita é outra verificação. Não criar arquivo, consultar entradas ou iniciar ferramentas.
- [x] **5.6 — Testar adulterações focadas.** Alterar referência de teste, hash de contexto, ID no plano, espelho de cobertura, critério de aprovação, origem e versão. Duplicar uma linha e trocar a ordem de duas linhas válidas. Executar roundtrip de um registro completo e do vazio. Verificar que os limites sobre truncamento de linhas completas permanecem documentados, em vez de exigir uma garantia que o formato não fornece.

```python
def test_reader_accepts_empty_dataset(tmp_path):
    """Representa corretamente uma população final vazia."""
    path = tmp_path / "dataset.jsonl"
    path.write_bytes(b"")
    assert load_dataset(path) == []
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_format.py tests/test_validation_input.py -q
```

Esperado: leitura isolada sem dependências operacionais; adulterações recusadas com ValueError.

## Tarefa 6 — Publicar pela CLI e preservar saídas existentes

**Arquivos:** modificar `config.py`, `jsonl.py`, `cli.py`; criar `dataset_cli.py` e `tests/test_dataset_cli.py`.

- [x] **6.1 — Acrescentar constantes de configuração.** Preservar todas as constantes existentes.

```python
DATASET_DISCOVERY_PATH = OUTPUT_PATH
DATASET_TRIAGE_PATH = TRIAGE_OUTPUT_PATH
DATASET_EXTRACTION_PATH = EXTRACTION_OUTPUT_PATH
DATASET_VALIDATION_PATH = VALIDATION_OUTPUT_PATH
DATASET_DEDUPLICATION_PATH = DEDUPLICATION_OUTPUT_PATH
DATASET_OUTPUT_PATH = Path("results/06_dataset.jsonl")
```

- [x] **6.2 — Acrescentar serialização ordenada opcional.** Em `write_jsonl`, adicionar argumento keyword-only `sort_keys: bool = False` e encaminhá-lo a `json.dumps`. O novo comando usa `ensure_ascii=True, compact=True, sort_keys=True`; consumidores anteriores mantêm seus bytes por padrão.

```python
def write_jsonl(path: Path, records: Iterable[dict], *, ensure_ascii: bool = False,
                compact: bool = False, sort_keys: bool = False) -> None:
    """Grava JSONL UTF-8 com opções explícitas de preservação e ordenação."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=ensure_ascii, allow_nan=False,
                                    sort_keys=sort_keys,
                                    separators=(",", ":") if compact else None) + "\n")
```

- [x] **6.3 — Registrar e despachar o comando.** Acrescentar `build-dataset` com `--overwrite` em `_parse_arguments`. Despachar antes de `config.validate_workers(args.workers)` em `main`; a nova etapa não possui esse argumento.

```python
build_dataset_command = commands.add_parser("build-dataset", help="Construir o dataset final.")
build_dataset_command.add_argument("--overwrite", action="store_true",
                                   help="Substituir intencionalmente a saída existente.")

# Em main, imediatamente após interpretar argumentos:
if args.command == "build-dataset":
    return run_dataset(args)
```

- [x] **6.4 — Coordenar `run_dataset`.** Verificar saída/diretórios e aliases contra as cinco entradas com `resolve` e `samefile` antes de abrir dados caros. Recusar existente sem autorização. Executar leitor → métricas → montador → validador → escritor; confrontar quantidade com representantes selecionados antes da escrita. Não requerer GITHUB_TOKEN. Sanitizar mensagens e manter estado `writing` para diferenciar preservação de saída de publicação incompleta.
- [x] **6.5 — Classificar retorno sem capturar tudo como código 2.** `ValueError` de configuração/integridade e OSError de leitura de entrada retornam 2; `DatasetToolError` retorna 1; escrita, cancelamento e erro de obtenção da versão instalada retornam 1. Não deixar `DatasetToolError` ser absorvido por capturas genéricas. Após sucesso imprimir `Instâncias: N; saída: CAMINHO` e retornar 0.
- [x] **6.6 — Proteger fronteiras com testes de CLI.** Testar saída anterior sem overwrite antes de qualquer leitor; entrada inválida com overwrite preservando bytes; hardlink/alias coincidente quando suportado; diretório como saída/pai arquivo; parâmetro não aceito; cancelamento antes e durante a escrita; ferramenta indisponível=1; registro inválido=2; vazio sem Node/Docker/Git; sucesso cria somente o 06. Fazer monkeypatch de `GitTransport`, `DockerExecutor` e cliente GitHub para levantar AssertionError caso chamados.
- [x] **6.7 — Verificar determinismo serializado e compatibilidade.** Publicar duas vezes em diretório temporário com overwrite e comparar bytes. Verificar chaves ordenadas e LF no arquivo externo, preservando CRLF dentro das strings. Executar a suíte anterior da deduplicação para detectar mudança involuntária no escritor/exceção.

```powershell
.\.venv\Scripts\python.exe -m robo_minerador build-dataset --help
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_cli.py tests/test_deduplication_cli.py tests/test_triage.py -q
```

Esperado: help apenas com `--help` e `--overwrite`; códigos 0/1/2 conforme contrato; saídas anteriores protegidas.

## Tarefa 7 — Integrar, documentar e verificar empacotamento

**Arquivos:** completar `tests/test_dataset_cli.py`; modificar `README.md`; registrar evidências no fim deste plano somente após execução.

- [x] **7.1 — Rodar integração positiva com leitores reais.** Usar a cadeia da Tarefa 1, invocar `cli.main(["build-dataset"])` e reler com `load_dataset`. Conferir IDs contra o 05, cada texto contra o 03, cobertura/casos contra o 04, licença contra o 02 e estrelas contra o 01. Não mockar o montador, o leitor final ou a métrica no teste de integração; apenas as fronteiras externas já controladas pela fixture.
- [x] **7.2 — Cobrir elegibilidade local e recursos não incorporados.** Verificar que aprovação local com suíte global negativa permanece incluída e identificada corretamente. Verificar uma referência de recurso binário já disponível nas evidências, sem ler/incorporar seu conteúdo. Não produzir um inventário artificial completo nem afirmar execução autônoma.
- [x] **7.3 — Documentar uso e contrato no README.** Atualizar “Estado do projeto” somente após a implementação verificada. Adicionar `build-dataset`, os seis caminhos, esquema dos blocos, seleção de representantes, custo de repetição de evidências, política de métricas, necessidade de Node/TypeScript com aprovadas, caso vazio, códigos de retorno e restrição de truncamento. Diferenciar CLI planejada de resultados efetivamente observados; não repetir como atuais as contagens históricas do 04/05.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_input.py tests/test_dataset_metrics.py tests/test_dataset.py tests/test_dataset_format.py tests/test_dataset_cli.py tests/test_deduplication.py tests/test_deduplication_input.py tests/test_deduplication_representation.py tests/test_deduplication_cli.py tests/test_extraction_input.py tests/test_validation_input.py -q
npm test --prefix "src/robo_minerador/typescript"
```

Esperado: verificações novas e regressões pertinentes passam. Se houver falha, registrar causa e corrigir antes de prosseguir; não ampliar indiscriminadamente a execução após passar sem motivo novo.

- [ ] **7.4 — Conferir arquivos distribuídos.** O projeto já possui pasta `build`; conferir com `Test-Path` antes de gerar artefatos temporários de pacote. Usar `pip wheel --no-deps --no-build-isolation` para a pasta temporária preaprovada abaixo; não instalar pacote novo. Inspecionar wheel com `zipfile` no terminal para exigir `dataset_*.py`, `dataset.py` e `typescript/dataset-metrics.mjs`, sem exigir `node_modules` ou dados reais. Não alterar `pyproject.toml` se o padrão atual já incluir o auxiliar.

```powershell
Test-Path -LiteralPath "C:\Users\Estefano\AppData\Local\Temp\opencode"
.\.venv\Scripts\python.exe -m pip wheel --no-deps --no-build-isolation --wheel-dir "C:\Users\Estefano\AppData\Local\Temp\opencode" .
```

Esperado: wheel contém os módulos e auxiliar; nenhuma mudança de dependências. Se ferramenta de build local estiver indisponível, reportar o impedimento em vez de instalar automaticamente.

## Tarefa 8 — Executar a construção real e encerrar com evidências

**Arquivos:** somente `results/06_dataset.jsonl` como saída do comando; acrescentar resultados observados à seção de evidências deste plano. Não modificar entradas reais.

- [x] **8.1 — Inspecionar a situação real.** Conferir existência de 01–05 e 06 com ferramenta dedicada de leitura. Os leitores devem aceitar o conjunto completo antes de gerar o dataset. Havendo incompatibilidade histórica, parar e explicar o elo divergente; não editar os hashes/arquivos reais para fazer a cadeia passar.
- [x] **8.2 — Registrar hashes das cinco entradas antes da execução.** Usar Python para imprimi-los no terminal, sem relatório auxiliar. Repetir depois e exigir igualdade.

```powershell
.\.venv\Scripts\python.exe -c "import hashlib; from pathlib import Path; from robo_minerador import config; names=('DATASET_DISCOVERY_PATH','DATASET_TRIAGE_PATH','DATASET_EXTRACTION_PATH','DATASET_VALIDATION_PATH','DATASET_DEDUPLICATION_PATH'); print({name:hashlib.sha256(Path(getattr(config,name)).read_bytes()).hexdigest() for name in names})"
```

- [x] **8.3 — Construir uma vez se a saída não existir.** Confirmar o pai `results` antes da execução que cria o arquivo. Se o 06 existir, pedir autorização específica para sobrescrevê-lo; não copiar/restaurar/substituir automaticamente arquivos do pesquisador.

```powershell
Test-Path -LiteralPath "results"
.\.venv\Scripts\python.exe -m robo_minerador build-dataset
```

Esperado: código 0 com contagem igual à população real de representantes, qualquer que seja ela. Não executar nova validação para aumentar a população.

- [x] **8.4 — Reler e confrontar a população.** A checagem final não imprime código ou evidências grandes, somente contagens, IDs e digest do 06.

```powershell
.\.venv\Scripts\python.exe -c "import hashlib; from robo_minerador import config; from robo_minerador.dataset_format import load_dataset; from robo_minerador.deduplication_format import load_deduplication; rows=load_dataset(config.DATASET_OUTPUT_PATH); prior,_=load_deduplication(config.DATASET_DEDUPLICATION_PATH); expected=sorted(r['instance_id'] for r in prior if r['disposition']=='representative'); actual=[r['instance_id'] for r in rows]; assert actual==expected; print({'instances':len(rows),'ids':actual,'sha256':hashlib.sha256(config.DATASET_OUTPUT_PATH.read_bytes()).hexdigest()})"
```

- [x] **8.5 — Revisar o diff e registrar evidências.** Executar `git diff --check` e `git status --short`. Registrar comandos, duração quando medida, códigos de retorno, contagem real, hash do 06, igualdade dos hashes 01–05 e limitações efetivamente encontradas. Não declarar teste ou build aprovado com base apenas neste plano.
- [x] **8.6 — Obter aprovação final integrada.** Acionar o revisor independente da Tarefa 8 para o resultado real e um revisor independente para o conjunto integrado. Conferir as aprovações das Tarefas 1–8, resolver achados e repetir somente as verificações afetadas por correções. Se a construção real estiver impedida, registrar o bloqueio e não antecipar o commit de conclusão.
- [ ] **8.7 — Fazer o único commit autorizado.** Somente após conclusão e aprovação, inspecionar status, diff e dez commits recentes; selecionar explicitamente os arquivos de código, testes e documentação pretendidos. Não incluir datasets reais, cache, segredos ou arquivos de trabalho alheios. Não usar `git add .`, não alterar configuração Git nem ignorar hooks. Redigir mensagem no estilo do histórico, por exemplo `add final dataset construction`. Não fazer push.

```powershell
git status --short
git diff
git log --oneline -10
```

Depois da inspeção, executar `git add --` com a lista explícita dos arquivos realmente alterados no escopo e conferir `git diff --cached --check` e `git diff --cached` antes de `git commit -m "add final dataset construction"`. Se hooks recusarem, corrigir e revisar antes de nova tentativa; não usar amend ou skip. Encerrar informando hash do commit, aprovação final e caminhos de spec/plano/dataset.

## Matriz de cobertura da spec

| Requisito | Tarefas |
| --- | --- |
| Único arquivo, sem enunciado, estatísticas ou binários | 4, 5, 6, 7 |
| Cadeia e populações 01–05; política atual; grupos exatos | 1, 2 |
| Representantes aprovados sem gate global | 2, 7, 8 |
| Código, documentação, associações e contexto integrais | 3, 4, 5 |
| Ambiente e provas resolvíveis na própria linha | 4, 5 |
| Licença verificada, metadados históricos e origens duplicadas | 2, 4, 5 |
| Métricas estáticas e cobertura/contagem sem reexecução | 3, 4, 5 |
| Determinismo, esquema, arquivo vazio e limites de truncamento | 4, 5, 6, 7 |
| CLI, overwrite, aliases, erros e preservação de entradas | 6, 8 |
| Leitor próprio, testes pertinentes e empacotamento | 5, 7 |

## Evidências da implementação

Este plano foi redigido antes da implementação e sua execução foi autorizada pelo pesquisador. As evidências abaixo registram resultados observados durante a implementação; não pressupõem conclusão das etapas ainda pendentes.

- Tarefa 7: `.\.venv\Scripts\python.exe -m pytest` nos arquivos dataset, deduplicação, extraction_input e validation_input relacionados no passo 7.3: **246 passaram**, em **370,52 s**.
- `npm test --prefix "src/robo_minerador/typescript"`: **114 passaram e 4 testes Docker foram ignorados**, em **2,16 s**. Nenhum candidato real foi executado.
- `git diff --check`: sem erros; Git informa sua política local de conversão LF/CRLF.
- Empacotamento: `pip wheel --no-deps --no-build-isolation` não produziu wheel porque `setuptools.build_meta` está ausente. Ausência de setuptools confirmada também no Python global; nenhuma dependência foi instalada. A inspeção do wheel permanece indisponível, sem alegação de pacote verificado.
- Referências binárias: a extração possui testes de referência a binário/dependência e de requisito `external_resource`; o montador preserva requisitos e política `references_only`. Não foi criada uma nova prova ponta a ponta de um recurso binário específico no 06.
- Ajuste técnico das Tarefas 5–6: o leitor isolado confere estruturalmente o diagnóstico global de recursos v3, sem replay AST/Node de diagnóstico não usado como gate; os critérios locais permanecem rederivados em Python. A validação aceita ordem distinta de chaves JSON sem perder multiplicidade de casos nem ordem das associações. Consumidores anteriores mantêm o comportamento padrão.

### Construção real — 2026-11-14

O subagente `ses_e23698c67ffewhymYrlZwDKudO` executou uma única vez `.\.venv\Scripts\python.exe -m robo_minerador build-dataset`, sem overwrite, após conferir a ausência do 06 e o diretório pai. Resultado **0**, duração **310,7899342 s**, **4 instâncias**. Nenhuma etapa candidata foi reexecutada.

Saída: `results/06_dataset.jsonl`, **68.000.071 bytes**, SHA-256 `e6485f67beedb245b22a066b144f6a9f2c3bac1096758a12caf94cce559307ca`.

| Instância | Linhas físicas | Complexidade | Mínimo comprovado de casos válidos |
| --- | ---: | ---: | ---: |
| `0719b126f7e343559fb5cdcc7e01c5ac21e263aa46d08e2b273caa3ca1bd41bf` | 19 | 2 | 12 |
| `0e60090700d343eb06ae2d7bc0d18531f3ccdd1add61cc44c8116a41cff213b3` | 184 | 43 | 11 |
| `43e49b48cebbc9c7321394312ed995d060e960cdcdc31f08e137f38be7064147` | 34 | 6 | 13 |
| `7f322382e852b39a77c740d10988b6abe6b34dffbdd2b8a627e63628c9cbd5b3` | 42 | 9 | 12 |

Os casos têm semântica `lower_bound`, não total exato. Cobertura indisponível em `associated_tests` e `full_unit_suite` nas quatro instâncias, sem conversão para zero.

| Entrada | SHA-256 antes e depois, sem alteração |
| --- | --- |
| 01 | `8ddf83a95a9a1e4d3046705ff74b8f947843bb27fa9e4ebb87f08e6b20542f48` |
| 02 | `52b9ef1e96e2b578c545890f9568fb8be2d67e66dfb7cfa7c28cd1e086e65bcd` |
| 03 | `c9b95889dbf54af10826134b9ae033f6d087edf698220b64d837acbe3018890d` |
| 04 | `202e83a294b8df901062453b23f90f1ff276a485a1cd2ff1520c54a6467526bb` |
| 05 | `74615083095b5a981b0ebd522303ab9a4c30488c2d516e499b5c7393fdfa3145` |

Leitura de verificação: `load_dataset(config.DATASET_OUTPUT_PATH)` e `load_deduplication(config.DATASET_DEDUPLICATION_PATH)`, confronto de IDs/quantidade conforme comando do passo 8.4. A revisão independente `ses_e236351dcffe5rm2xXgnRTDXAt` confirmou os quatro IDs, hashes, tamanho, serialização canônica e leitura sem subprocessos, bloqueando `subprocess.Popen`. Os hashes atuais de 01–05 coincidiram com a comparação antes/depois do implementador.

| Entrega | Condição de revisão | Estado nesta entrega |
| --- | --- | --- |
| Tarefa 1 | Aderência `ses_e23ea9d8bffeeCZWNmq4Wc1aRn` e qualidade `ses_e23e3b4ccffekfIk5lWgq8W7ba` | APPROVED; fixtures positiva/vazia verificadas, caso local corrigido |
| Tarefa 2 | Aderência `ses_e23dd1522ffeWlHe6Bxeew498G` e qualidade `ses_e23d46f15ffer4eKGskxWMnoM8` | APPROVED; 99 testes pertinentes passaram na última correção |
| Tarefa 3 | Aderência `ses_e23c9e92effeI7Z2d3XmCEOKRn` e qualidade `ses_e23c81a15ffeeUjutuHzTxPQAs` | APPROVED; 3 testes Node e 6 Python |
| Tarefa 4 | Aderência `ses_e23c49554ffe6vSm5jPT4n283C` e qualidade `ses_e23c2fa28ffeLHBIxtf80dPR4W` | APPROVED; 4 testes, cópias independentes por registro |
| Tarefa 5 | Aderência `ses_e239d405effewY7pIAZTCY4Jf3` e qualidade `ses_e2390d855ffexwCHASCPgkki6i` | APPROVED; última suíte pertinente: 121 passaram, 2 ignorados |
| Tarefa 6 | Aderência `ses_e237d7cddffeNp5kyzCD5DceyD` e qualidade `ses_e237ca347ffe07lDIW1iaM0UiM` | APPROVED; revisão independente executou 144 testes pertinentes |
| Tarefa 7 | Aderência `ses_e236ce9e0ffeYhFBbE8KWUR85F` e qualidade `ses_e236bcbf5ffekOTALJ1PY61AqX` | APPROVED; wheel indisponível por tooling |
| Tarefa 8 | Aderência `ses_e236351dcffe5rm2xXgnRTDXAt` e qualidade `ses_e2361b24cffe4q22tANxkYdQnW` | APPROVED |
| Conjunto integrado | Revisão `ses_e2360930dffeTABgY7BC6bszy6` | APPROVED |

- Verificação recente do coordenador: `.\.venv\Scripts\python.exe -m pytest tests/test_dataset_metrics.py -q` — **6 passaram em 1,03 s**. A releitura real confirmou **4 registros**, **68.000.071 bytes**, SHA-256 `e6485f67beedb245b22a066b144f6a9f2c3bac1096758a12caf94cce559307ca` e igualdade do conjunto de IDs com o 05.

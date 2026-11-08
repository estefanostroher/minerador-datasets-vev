# Validação dinâmica — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` para executar este plano tarefa por tarefa, adaptado à decisão expressa do pesquisador: um subagente revisor distinto do implementador faz o review conjunto de aderência à spec e qualidade ao fim de cada tarefa. Os passos usam caixas de seleção para acompanhamento.

**Goal:** Produzir `results/04_validacao.jsonl` a partir de candidatas approved v3, executando uma passagem da suíte unitária completa em Docker e comprovando pelo menos quatro casos válidos diretamente associados por função.

**Architecture:** Python verifica a entrada e os snapshots, prepara receitas imutáveis e coordena ambientes Linux sequencialmente. Um adaptador Vitest instrumenta chamadas e entradas em memória e associa eventos aos casos executados. Avaliação e publicação preservam proveniência, contexto e distinção entre rejeição científica e impedimento operacional.

**Tech Stack:** Python >=3.11, Git, Docker Engine Linux/Docker Desktop, Node.js compatível por receita, npm/pnpm fixados, TypeScript Compiler API confiável e Vitest das dependências originais.

---

## 1. Autoridade, escopo e regras obrigatórias

Spec aprovada: [`2026-11-07-validacao-design.md`](../specs/2026-11-07-validacao-design.md). Também ler as convenções e a política proporcional de testes da [spec geral](../specs/2026-10-29-minerador-incremental-design.md).

Este plano é uma entrega documental. Sua escrita não inicia implementação nem execução candidata. O pesquisador já escolheu implementação por subagentes; não voltar a oferecer execução inline como alternativa padrão.

### Testes somente quando necessários

- Não impor TDD obrigatório, testes por tarefa/função, meta de cobertura ou quantidade mínima de testes do minerador.
- Antes de adicionar um teste, registrar o risco concreto: seleção, associação, contagem, exclusão ou integridade. Explicar por que um teste existente ou inspeção direta não basta.
- Reutilizar e estender fixtures/cenários existentes. Criar novos arquivos somente quando houver responsabilidade distinta e risco não coberto.
- Os cenários listados abaixo são riscos a verificar, não uma obrigação de criar um teste separado para cada item. Combinar parametrizações e casos coerentes.
- Rodar verificações existentes pertinentes. A execução da suíte dos projetos candidatos é obrigação metodológica, diferente dos testes do próprio minerador.

### Desenvolvimento e autorizações

- Mesmo checkout e branch atuais; sem worktrees ou branches auxiliares. Nenhum commit intermediário: o commit só poderá ocorrer ao final de todas as tarefas e respectivos reviews aprovados, mediante autorização explícita. Push também depende de autorização explícita.
- Código e identificadores em inglês; comentários/docstrings em português. Toda função/método novo tem documentação conforme a spec geral.
- Preservar resultados, snapshots e trabalho existente. Nenhum subagente altera arquivos fora da atribuição sem comunicar ao coordenador.
- Dúvida metodológica ou ambiguidade real: suspender o trecho afetado e perguntar ao pesquisador. Não escolher silenciosamente uma flexibilização para obter aprovações.
- Sem implementação de deduplicação/construção, recuperação de pendentes, outros frameworks ou containerização das outras etapas.
- Não executar testes reais de candidatos enquanto o instrumento estiver em desenvolvimento. Validar o instrumento em fixtures controladas antes do piloto.

## 2. Subagentes e gates de revisão

Para **cada tarefa de implementação**, o coordenador deve:

1. Enviar a um **subagente implementador novo** o texto integral da tarefa, contexto necessário, contratos já aprovados, arquivos atribuídos e as regras da seção 1.
2. Receber arquivos alterados, decisões, comandos executados, resultados completos e preocupações. Não aceitar apenas “funcionou”.
3. Despachar **outro subagente, revisor conjunto de spec + quality**, somente após a implementação da tarefa. Ele compara código/diff com os itens aplicáveis da spec e verifica correção, clareza, tratamento de erros, cancelamento, recursos, integridade, semântica da instrumentação e proporcionalidade dos testes.
4. O revisor deve inspecionar o código e as evidências, não confiar apenas no relato do implementador. Seu relatório separa os achados de aderência e qualidade, mas pertence a um único review.
5. Devolver achados ao implementador e obter nova revisão conjunta até `APPROVED` em ambas as dimensões. Autorrevisão não substitui esse subagente independente.
6. O coordenador inspeciona o diff e as evidências, executa a integração pertinente e só então marca a tarefa concluída.

Não iniciar tarefa dependente com achados abertos. Trabalhar sequencialmente neste piloto para evitar conflitos nos módulos compartilhados. O revisor de uma tarefa não pode ter implementado o trecho que revisa.

As tarefas abaixo são unidades de contexto para delegação e revisão, não passos individuais. Cada uma recebe um review conjunto de spec + quality ao concluir seus passos internos. O review da tarefa 5 abrange também o conjunto integrado antes do piloto; o da tarefa 6 abrange o fechamento. Não criar ciclos adicionais de subagentes para cada subtítulo ou checkbox. Registrar tarefa, papel, identificador da sessão, arquivos/requisitos revisados, comandos, achados e conclusão no registro de execução deste plano. Não simular reviews ou copiar aprovações históricas.

### Conteúdo mínimo dos despachos

**Implementador:** objetivo, tarefa integral, spec aplicável, contratos, arquivos próprios, arquivos apenas consultáveis, política de testes necessários, proibição de candidatos reais/commits, critérios de conclusão e formato do relatório.

**Revisor spec + quality:** tarefa, spec, baseline/diff e evidências; proibir alterações e execução de candidatos; pedir uma análise conjunta com duas seções, aderência e qualidade. Cada achado informa severidade, arquivo/linha, requisito ou risco e verificação mínima. Responder `APPROVED` somente sem lacunas aplicáveis nas duas dimensões. Não exigir testes cosméticos ou refatorações alheias ao escopo.

## 3. Estado inicial a conferir

Baseline documental: seis origens com 189 approved no 03 atual: agbrte 115, Nemryz 23, armature 17, anur-living-map 23, dental-erp 1, macrotracker-server 10. Dez registros de repositório, 3.976 instâncias; origem `partial`. Não confundir com o replay histórico de cinco origens.

O cache v3 já contém referências de snapshots. O loader atual `load_instance_context` reanalisa todo o repositório para uma instância; precisa de consumo em lote preservando a conferência estrita. O escritor `write_jsonl` já aceita `ensure_ascii=True, compact=True`. O parser da CLI atual só contém discover/triage/extract.

O cliente Docker foi encontrado instalado, mas o mecanismo Linux estava indisponível na inspeção de planejamento. Isso é pré-requisito de ambiente a verificar na execução, não falha dos candidatos.

## 4. Mapa de arquivos e fronteiras

| Arquivo | Ação | Responsabilidade |
| --- | --- | --- |
| `src/robo_minerador/validation_input.py` | Criar | Leitura estrita e seleção da entrada v3 |
| `src/robo_minerador/extraction_context.py` | Alterar | Replay em lote com mesmo contrato estrito do loader atual |
| `src/robo_minerador/validation_recipe.py` | Criar | Receitas, hashes, escopo, ambiente e limites |
| `src/robo_minerador/validation_recipes.json` | Criar | Seis receitas vinculadas a ID/SHA e evidências originais |
| `src/robo_minerador/validation_workspace.py` | Criar | Materialização Linux fiel e workspace isolado |
| `src/robo_minerador/validation_docker.py` | Criar | Docker CLI, fases, processos, limites e cancelamento |
| `src/robo_minerador/validation_evidence.py` | Criar | Protocolo de eventos, contagem e decisões |
| `src/robo_minerador/validation_evaluation.py` | Criar | Avaliação científica separada do protocolo de eventos |
| `src/robo_minerador/validation_instrument.py` | Criar | Ponte entre contexto verificado, workspace e instrumento readonly |
| `src/robo_minerador/validation_format.py` | Criar | Contrato dos registros e verificações da saída |
| `src/robo_minerador/validation.py` | Criar | Orquestração sequencial e publicação de resultados |
| `src/robo_minerador/typescript/validation-transform.mjs` | Criar | Sondas de chamada/entrada e mapas de origem |
| `src/robo_minerador/typescript/validation-runtime.mjs` | Criar | Contextos e transporte de evidências de runtime |
| `src/robo_minerador/typescript/validation-vitest.mjs` | Criar | Integração Vitest, configuração aditiva e reporter |
| `src/robo_minerador/typescript/validation-runner.mjs` | Criar | Bootstrap confiável e negociação de versão/capacidades |
| `src/robo_minerador/typescript/validation-{plan,bootstrap,command,preload,channel,entry,reporter,reporter-entry}.mjs` | Criar | Plano AST, comandos originais, censo coordenador e transporte de evidências |
| `src/robo_minerador/config.py` | Alterar | Caminhos e limites validados da etapa |
| `src/robo_minerador/cli.py` | Alterar | Comando validate e códigos de saída |
| `pyproject.toml` | Alterar | Empacotar receitas e auxiliares confiáveis |
| `README.md` | Alterar | Instalação Docker, execução, contrato e limitações reais |

Os auxiliares `.mjs` ficam junto ao analisador para reutilizar a dependência confiável TypeScript 6.0.3 e o padrão de empacotamento existente. Não instalar dependências do instrumento dentro do manifesto/lockfile candidato. O workspace confiável e suas dependências ficam separados do projeto e sua identidade entra no resultado.

Arquivos de verificação potenciais, criados apenas para os riscos justificados: `tests/test_validation_input.py`, `tests/test_validation_recipe.py`, `tests/test_validation_docker.py`, `tests/test_validation_evidence.py`, `tests/test_validation.py`; estender `tests/test_extraction_context.py`; auxiliares Node em `src/robo_minerador/typescript/tests/validation-*.test.mjs`. Usar fixtures pequenas geradas em diretório temporário aprovado. Não versionar node_modules de fixtures nem resultados de candidatos.

## 5. Contratos entre tarefas

Utilizar dicionários JSON validados, coerentes com o código existente. Não introduzir hierarquia de classes para cada estado. Fixar estes pontos de integração:

| Função/módulo | Contrato |
| --- | --- |
| `load_extraction(path)` em validation_input | Retorna `(groups, source)`; grupo contém `repository`, `instances` approved e `reasons`; source identifica bytes/hash/versão/estado/contagens |
| `load_repository_context(instances, repository, reasons, store)` em extraction_context | Retorna artefato/contexto/requisitos e mapa de instâncias verificadas por ID; uma projeção/reanálise por origem |
| `load_recipes(path)` em validation_recipe | Valida catálogo, unicidade ID/SHA, argumentos, recursos, hashes e evidências declarativas |
| `resolve_recipe(repository, catalog)` em validation_recipe | Retorna receita aplicável ou resultado operacional de ausência/incompatibilidade; não faz ajuste corretivo |
| `prepare_workspace(store, verified, recipe, executor)` em validation_workspace | Materializa commit e configura volumes/áreas de trabalho; retorna handle de workspace próprio e identidade |
| `DockerExecutor` em validation_docker | `preflight()`, `run_phase(workspace, phase, recipe)`, `cancel()`, `close()`; identifica resultados e recolhe recursos próprios |
| `validate_events(events, expected)` em validation_evidence | Valida protocolo, sequências, identidades, término/completude e correspondências de cada evento |
| `evaluate_repository(group, recipe, execution)` em validation_evidence | Retorna resultado de repositório e instâncias, com critérios, razões e evidências |
| `validate_output_records(records)` em validation_format | Confere esquema, referências, critérios, contagens e fechamento sem executar candidatos |
| `validate_repositories(groups, source, catalog, executor, store)` em validation | Produz registros na ordem da spec e garante fechamento/cancelamento |

Criar novas funções auxiliares apenas quando reduzirem uma responsabilidade claramente delimitada. Ajuste de assinatura durante a implementação exige atualizar produtores, consumidores, testes pertinentes e este registro, sem deixar nomes divergentes.

### Agrupamento e dependências

| Tarefa | Contexto compartilhado | Entrega verificável | Dependência |
| --- | --- | --- | --- |
| 1 | Entrada, fontes, receitas e replay | Candidatas verificadas e receitas rastreáveis por ID/SHA | Nenhuma |
| 2 | Workspace, Docker e recursos | Executor controlado demonstrado em fixture Linux | 1 |
| 3 | Protocolo, sondas e Vitest | Casos de runtime ligados às chamadas originais em fixtures | 1 e 2 |
| 4 | Critérios, contagens, cobertura e JSONL | Avaliação e publicação integradas com evidências | 1–3 |
| 5 | CLI, distribuição e integração | Comando utilizável, pacote completo e gate pré-piloto | 1–4 |
| 6 | Piloto, resultados e fechamento | Saída real auditada e reviews finais | 5 aprovada e execução autorizada |

O mesmo implementador mantém o contexto de todos os passos de uma tarefa. Há transferência explícita dos arquivos compartilhados entre tarefas, sem edições concorrentes. As verificações internas continuam proporcionais ao risco; agrupar tarefas não elimina os critérios de aceite.

## Tarefa 1 — Entrada, proveniência e receitas verificadas

**Arquivos próprios:** `src/robo_minerador/validation_recipes.json`, `src/robo_minerador/validation_recipe.py`, `src/robo_minerador/validation_input.py`, `src/robo_minerador/extraction_context.py`; testes pertinentes de entrada, receita e contexto. O coordenador registra evidências neste plano.

### Fontes e receitas

- [ ] Conferir status/branch e registrar hashes dos seis resultados existentes e manifestos/marcadores dos snapshots, preservando o baseline.
- [ ] Inspecionar os seis snapshots offline, lendo manifests, lockfiles efetivos, CI, documentação, configurações, setups e scripts. Para cada origem, registrar quais comandos originais cobrem todos os pacotes unitários e quais passos são necessários. Não executar configurações candidatas nessa inspeção.
- [ ] Validar Docker Linux e sua capacidade de aplicar 4 CPUs/8 GiB. Se indisponível, pedir ao pesquisador a preparação do Docker Desktop; continuar somente o trabalho independente. Não iniciar testes reais para verificar disponibilidade.
- [ ] Resolver imagens Linux por digest e versões exatas de Node/npm/pnpm antes da tentativa candidata. Conferir requisitos do commit e matrizes de compatibilidade do Vitest; usar a versão do framework resolvida pelo lockfile, sem atualizar o candidato.
- [ ] Criar catálogo das seis origens com `repository_id`, `commit_sha`, `recipe_version`, `image`, `platform`, `tools`, `lockfiles`, `environment`, `phases`, `unit_scope`, `limits` e `evidence`. Cada fase contém lista ordenada de `{argv, cwd}`; comandos compostos originais podem ser invocados por seus scripts, sem reinterpretação por shell do Python.
- [ ] Representar impossibilidade de resolver escopo/ambiente por `resolution_status=not_evaluated`, código e evidência, em vez de comando fictício. Em armature, inspecionar a dependência de TerminusDB e o escopo antes de concluir rejeição ou pendência. Em Nemryz, conferir shrinkwrap; em macrotracker, workspaces e build do shared.
- [ ] Validar unicidade, paths relativos, argumentos, digest, ferramentas exatas e orçamentos. Erro sintático do catálogo é configuração inválida; receita ausente/inconclusiva é pendência daquela origem.
- [ ] Conferir internamente o catálogo contra as fontes, registrar seu hash e preservar sua identidade até o piloto. Nenhuma receita sem identidade efetiva pode iniciar comandos candidatos.

**Verificação necessária:** inspeção direta das seis receitas contra fontes originais; testes de parser só para risco de executar SHA/receita incorretos ou permitir comandos com diretório fora do workspace. Não testar cada getter/campo isoladamente.

**Comandos de ambiente, sem candidatos:**

```powershell
git status --short
git branch --show-current
docker version
docker info --format '{{.OSType}}'
```

Esperado: daemon acessível e `linux`. Falha Docker bloqueia tarefas de execução, não rejeita origens.

### Leitura estrita do 03 e replay em lote

- [ ] Implementar `load_extraction` com parsing estrito e hash dos bytes, reutilizando `strict_object`, `strict_float` e expansão v3 existentes. Rejeitar chaves repetidas, NaN/infinito, falta de início/fim, versões misturadas, IDs/SHA incoerentes, referências ausentes e contagens divergentes.
- [ ] Validar todos os registros, inclusive repositórios sem approved e diagnósticos; só então selecionar grupos. Não exigir 189 candidatas ou seis origens como regra de entrada.
- [ ] Extrair o núcleo estrito de `load_instance_context` para `load_repository_context`: verificar artefato uma vez, projetar uma vez, reanalisar uma vez, comparar contexto/analisador/requisitos e cada unidade/ordinal/ID completo. Manter o loader individual como consumidor compatível desse núcleo.
- [ ] Não aceitar unidade divergente só porque função/nome/código parecem iguais. Cache ausente/corrompido gera pendência sem fetch. Divergência individual identifica a instância afetada; divergência compartilhada não permite aprovar dependentes.
- [ ] Verificar parsing/publicação com fixture existente adaptada; estender testes de loader para duas approved do mesmo repositório e confirmar uma única reanálise. Comparar os resultados individuais e em lote.
- [ ] Submeter ao review conjunto de spec + quality e inspecionar ausência de alterações nos bytes dos resultados/snapshots.

**Risco que justifica regressão:** seleção de instâncias erradas e enfraquecimento da verificação ao otimizar o loader não são demonstrados por uma coleta real bem-sucedida.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extraction_context.py tests/test_validation_input.py -q
```

Esperado: cenários pertinentes aprovados e rede não utilizada. Criar o segundo arquivo apenas com as regressões justificadas.

## Tarefa 2 — Ambiente Docker e execução controlada

**Arquivos próprios:** `validation_workspace.py`, `validation_docker.py`, `config.py`; testes de executor quando necessários.

- [ ] Acrescentar constantes de entrada/saída e recursos em `config.py`, com validação de inteiros positivos reais, sem aceitar booleanos:

```python
VALIDATION_INPUT_PATH = EXTRACTION_OUTPUT_PATH
VALIDATION_OUTPUT_PATH = Path("results/04_validacao.jsonl")
VALIDATION_CPUS = 4
VALIDATION_MEMORY_MIB = 8 * 1024
VALIDATION_INSTALL_SECONDS = 20 * 60
VALIDATION_PREPARATION_SECONDS = 10 * 60
VALIDATION_SUITE_SECONDS = 30 * 60
```

- [ ] Usar subprocessos por lista de argumentos, Docker CLI e handles próprios; não acrescentar SDK Python sem necessidade. Preflight confere daemon Linux e suporte aos limites, sem comandos candidatos.
- [ ] Materializar blobs/modos/links do inventário verificado no filesystem Linux, sem export-ignore, filtros ou CRLF do host. Conferir identidade/conteúdo antes da execução; metadados Git necessários são cópia própria, sem alternates graváveis para o cache.
- [ ] Usar volume de workspace exclusivo por origem/execução e área confiável separada para o instrumento. Nunca montar cache original gravável, credenciais, socket Docker ou filesystem amplo do host no candidato.
- [ ] Executar instalação e preparação com rede; terminar seus processos/containers. Iniciar fase de testes em processo/container novo, mesmo workspace, `--network none`, sem portas publicadas ou herança de serviços anteriores.
- [ ] Aplicar limite de CPU/memória efetivo, impedir swap fora do orçamento anunciado e usar deadline monotônico por fase, somando todos os comandos. Registrar durações, returncode, eventos OOM, interrupção pelo orçamento e falha do daemon separadamente.
- [ ] Capturar saídas e eventos em arquivos temporários de trabalho, integrar evidências necessárias ao resultado e sinalizar perda de captura; não truncar silenciosamente casos. `cancel`/`close` encerram somente recursos próprios, inclusive em exceções.
- [ ] Verificar materialização com fixture Git que contenha binário, executável, link e export-ignore, incluindo fonte com LF. Verificar argumentos Docker, deadline agregado e limpeza com runner simulado; uma fixture Linux real confirma o mecanismo, sem projetos minerados.
- [ ] Obter aprovação do subagente de review conjunto de spec + quality.

**Riscos:** fonte transformada pelo Windows, cache alterado, teste conectado indevidamente a serviço, orçamento reiniciado por pacote, container sobrevivente e OOM confundido com reprovação.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_validation_docker.py tests/test_snapshot_store.py -q
```

Esperado: cenários de infraestrutura aprovados; falhas simuladas geram evidências operacionais. Inspeção de `docker inspect` da fixture real confirma rede, mounts e limites efetivos.

## Tarefa 3 — Instrumentação e integração Vitest

**Arquivos próprios:** `typescript/validation-transform.mjs`, `typescript/validation-runtime.mjs`, `typescript/validation-vitest.mjs`, `typescript/validation-runner.mjs` e a validação do protocolo em `validation_evidence.py`; testes Node/Python dos riscos atribuídos. A tarefa 4 assume depois a avaliação científica nesse último módulo.

### Protocolo e transformação das chamadas

- [ ] Definir versão 1 do protocolo interno com eventos `run_started`, `case_started`, `direct_call`, `target_entered`, `case_finished`, `run_finished`, identidade de sessão e sequência por emissor. `case_finished` inclui tentativas/resultados; `run_finished` informa processos encerrados e completude. Python rejeita evento desconhecido, identidade divergente, caso inexistente ou trace incompleto.
- [ ] Derivar IDs de declarações/pontos de chamada dos bytes e posições verificados. IDs de casos incluem projeto/arquivo/declaração/ocorrência do runner, não somente título ou índice global de publicação. Mapear source maps à origem antes de atribuir evidência.
- [ ] Implementar transformador com TypeScript confiável, restrito a pontos comprovados pela análise. Inserir sonda na chamada e entrada no corpo original; não substituir a função por spy ou wrapper como prova da implementação.
- [ ] Preservar ordem de avaliação, `this`, exceções, argumentos, retornos e short-circuit. Se uma forma não puder ser transformada com garantia, emitir limitação localizada e não atribuir por cobertura. Não ampliar seleção estática para chamadas indiretas.
- [ ] Usar contexto assíncrono por caso e correlação de chamada/alvo, com isolamento de worker/processo/projeto. Hooks, chamadas indiretas, processos não instrumentados e trabalho posterior ao encerramento do caso não recebem crédito presumido.
- [ ] Criar fixture mínima com o teste original abaixo e confirmar que uma execução chama a função duas vezes mas produz um único caso válido. Acrescentar no mesmo conjunto parametrização com títulos iguais e casos concorrentes, além do controle de mock/indireção quando necessários ao risco.

```typescript
import { expect, test } from 'vitest';
import { compute } from './target';

test('duas chamadas no mesmo caso', () => {
  expect(compute(1)).toBe(2);
  expect(compute(2)).toBe(3);
});
```

```typescript
/** Soma uma unidade ao valor recebido. */
export function compute(value: number): number {
  return value + 1;
}
```

- [ ] Comparar valores/efeitos das fixtures com e sem transformação em ambiente controlado; as execuções de controle pertencem ao teste do instrumento, não à validação de um candidato científico.
- [ ] Inspecionar internamente a transformação antes da integração com Vitest. Não declarar suporte a um tipo de chamada apenas por emitir JavaScript sintaticamente válido; o review independente conjunto ocorrerá ao concluir a integração desta tarefa.

**Riscos:** falso vínculo entre teste e alvo, alteração semântica pela sonda e contaminação entre casos. A aprovação de uma suíte real não detectaria esses falsos positivos.

```powershell
node --test src/robo_minerador/typescript/tests/validation-transform.test.mjs
.\.venv\Scripts\python.exe -m pytest tests/test_validation_evidence.py -q
```

Esperado: casos diretos atribuídos corretamente e indiretos/ambíguos sem crédito; protocolo incompleto não vira contagem zero completa.

### Adaptador Vitest e passagem única em fixtures

- [ ] Verificar APIs suportadas nas versões efetivas das receitas antes de programar o adaptador. Negociar capacidades explicitamente; versão desconhecida gera pendência, sem fallback por aproximação.
- [ ] Carregar a configuração original e acrescentar plugin/reporter confiáveis sem reescrever include/exclude, setup, projetos, aliases ou paralelismo. Não importar módulos Vitest de uma instalação diferente da candidata para executar a suíte.
- [ ] Instalar o instrumento em área confiável separada com dependências fixadas; registrar sua identidade. Preservar o comando original, argumentos, cwd e configurações, declarando no resultado a adição da instrumentação. Não modificar manifesto/lockfile para injetá-la.
- [ ] Relacionar ciclo de vida do runner aos eventos, inclusive testes `each`, skip/todo, falha esperada, retries e repetição. O mesmo título em parametrizações/projetos distintos não funde casos; retry verde não apaga a falha inicial.
- [ ] Integrar workspaces com vários processos/comandos: uma passagem por pacote, orçamento global da fase, sem invocar o comando do pacote duas vezes para obter dados faltantes.
- [ ] Comprovar que a evidência final inclui todos os processos necessários. Final de processo sem fechamento de eventos gera incompletude, não aprovação automática.
- [ ] Executar fixtures Docker com as versões efetivas suportadas: chamadas diretas, método com `this`, async/concorrência, hooks, mock do alvo e parametrização. Reutilizar as fixtures de transformação desta tarefa; não copiar o corpus inteiro de candidatos para testes do instrumento.
- [ ] Testar falha inicial seguida de retry aprovado em fixture: a decisão esperada é rejeição de suíte, nunca aprovação nem caso adicional.
- [ ] Obter review conjunto de spec + quality por subagente independente do implementador. Bloquear piloto real enquanto houver dúvida sobre preservação semântica ou atribuição de casos.

**Resultado esperado:** integração real com Vitest e Docker em fixtures; nenhuma suíte dos seis projetos executada. Comandos dos testes de integração devem ser registrados no relatório da tarefa, com imagens/versões exatas do catálogo congelado.

## Tarefa 4 — Avaliação, cobertura e publicação do JSONL

**Arquivos próprios:** `validation_evidence.py`, `validation_format.py`, `validation.py`; integração opcional em `validation-vitest.mjs` após transferência pelo coordenador; testes focados de evidência e publicação. Preservar o protocolo aprovado na tarefa 3.

### Recursos, cobertura e decisão científica

- [ ] Implementar os seis critérios de repositório e os quatro de instância da spec, mantendo razões/evidências por critério. `required=false` não é quarto estado de elegibilidade.
- [ ] Classificar dependência efetiva de recursos usando receita e observação dos caminhos executados. Rede bloqueada ou sucesso da suíte não bastam para aprovar ausência de serviço real; SDK importado também não basta para rejeitar.
- [ ] Registrar por requisito a prova de recurso permitido, proibido ou inconclusivo. Recursos de configuração/setup de toda a suíte têm alcance de repositório. Não iniciar TerminusDB/PostgreSQL/Redis para recuperar um resultado.
- [ ] Contar caso válido somente com resultado elegível e cadeia completa declaração → chamada direta → alvo original. Deduplicar por identidade de caso por função. Separar quantidade de chamadas, casos, tentativas, assertions e iterações de property-based testing.
- [ ] Implementar a decisão do mínimo com testemunhas comprovadas e completude:

```python
def minimum_cases_status(valid_cases: int, counting_complete: bool) -> str:
    """Decide o limiar sem tratar observação incompleta como ausência de casos."""
    if valid_cases >= 4:
        return "approved"
    return "rejected" if counting_complete else "not_evaluated"
```

- [ ] Aplicar separadamente os demais critérios; quatro testemunhas não anulam identidade/suíte/recursos pendentes. Rejeição do repositório bloqueia todas as candidatas com motivos explícitos; não fabricar execução individual.
- [ ] Classificar primeiro erro candidato, timeout do framework, limite do minerador, OOM, registry indisponível e defeito do instrumento conforme a spec. Exit code sozinho não comprova causa candidata.
- [ ] Coletar cobertura apenas na passagem existente e com suporte já disponível. Publicar `associated_tests` somente com atribuição por caso; cobertura global entra separadamente como `full_unit_suite`. Mapear linhas/ramificações ao código original; denominador zero é não aplicável.
- [ ] Se não houver atribuição confiável ou provedor, publicar indisponibilidade com `null` e motivo, sem instalar pacote ou repetir suíte. Falha da observação obrigatória mantém pendência, não é ocultada como cobertura opcional ausente.
- [ ] Verificar os limites 3/4 casos e completude, primeira falha, recurso proibido versus simulador e diferença cobertura global/associada, reutilizando a fixture de eventos. Integrar os resultados à publicação antes do review conjunto da tarefa.

**Riscos:** aprovação indevida por quatro chamadas, rejeição indevida por observação incompleta ou infraestrutura, cobertura atribuída ao escopo errado. Não criar testes para o simples literal de cada nome de critério.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_validation_evidence.py -q
```

### Contrato de publicação e orquestração

- [ ] Implementar os registros da seção 11 da spec, todos com `schema_version=1`. Publicar uma origem por grupo e todas as suas instâncias, inclusive não executadas por falha anterior. Não repetir texto de código/documentação/testes do 03.
- [ ] Guardar ambiente/receita/lockfiles/dependências efetivas no registro de repositório. Não depender apenas de hash de receita ou caminho temporário: incorporar os comandos, parâmetros e evidências necessários à reprodução.
- [ ] Guardar em cada instância critérios, contagem exata/limite inferior, completude, todas as testemunhas contabilizadas e razões das associações não creditadas. Vínculo com intervalo/ponto original deve permitir localizar o teste do 03 sem busca por nome.
- [ ] Implementar `validate_output_records`: rejeitar schema/referências/IDs incompatíveis, casos duplicados, soma divergente e decisão sem critérios suficientes. Critérios rejeitados prevalecem; ausência de cobertura opcional não gera partial.
- [ ] Orquestrar sequencialmente: verificar grupo, resolver receita, preparar workspace, executar fases autorizadas, consumir eventos, avaliar, publicar. Abortar passos posteriores após falha; continuar próximo grupo independente quando possível.
- [ ] Publicar ordem determinística, único escritor e fechamento mesmo quando há pendências recuperáveis. Se escrita/cancelamento impedir fechamento, não declarar arquivo completo.
- [ ] Confirmar `completed` com rejeições científicas, `partial` com avaliação útil e impedimentos operacionais, `failed` sem avaliação científica útil. Preservar estado parcial da origem sem copiá-lo automaticamente.
- [ ] Exercitar o escritor real com fixture que gere duas candidatas: uma aprovada e outra abaixo do limiar. Reabrir e validar os registros, localizar os testes originais pelos vínculos e verificar ausência de dados fabricados.
- [ ] Exercitar zero candidatas sem exigir Docker/Git/Node; falha de um grupo não apaga resultados válidos de outro. Obter review conjunto de spec + quality.

**Risco:** decisões, IDs e evidências podem se perder na compactação/publicação mesmo com contagem correta em memória.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_validation.py -q
```

Esperado: uma execução de fixture completa passa pelo publicador e retorna vínculos suficientes à construção, sem implementar a etapa 6.

## Tarefa 5 — CLI, distribuição e gate integrado

**Arquivos próprios:** `cli.py`, `pyproject.toml`, `README.md`; `config.py` apenas se integração exigir; testes existentes da CLI e integração. O coordenador conduz o gate integrado e seu review conjunto; correções nos módulos anteriores voltam ao implementador responsável.

### CLI, distribuição e documentação operacional

- [ ] Registrar `validate` com `--overwrite` e `--help`, sem workers/limit/caminhos customizados na versão inicial. Conferir entrada/configuração/preflight antes da saída; exceção de zero candidatas evita pré-requisitos desnecessários.
- [ ] Reutilizar `write_jsonl` com `ensure_ascii=True, compact=True`. Saída existente sem autorização retorna 2; entrada/preflight inválidos preservam saída anterior mesmo com overwrite.
- [ ] Implementar códigos 0/1/2 e mensagens em português conforme a spec; nenhum token GitHub é exigido. Não imprimir segredos nem capturar todo o ambiente Windows no JSONL.
- [ ] Incluir `validation_recipes.json` nos package-data e conferir inclusão de todos os auxiliares `.mjs`; o glob atual já inclui arquivos diretos de typescript. Não embutir node_modules ou volumes Docker na distribuição.
- [ ] Atualizar README: Docker só na validação; preparação do daemon Linux; receitas e versões; uma passagem; unidade de caso; recursos permitidos; limites; estados/saída; preservação de snapshots; comandos e limitações efetivamente demonstradas.
- [ ] Executar ajuda de todos os comandos e integração de overwrite/entrada inválida usando fixtures; reaproveitar padrão de CLI existente, sem rede/candidatos.
- [ ] Conferir wheel em TEMP e listar seu conteúdo; incluir as evidências no review integrado de spec + quality ao final desta tarefa.

```powershell
.\.venv\Scripts\python.exe -m robo_minerador validate --help
.\.venv\Scripts\python.exe -m robo_minerador extract --help
.\.venv\Scripts\python.exe -m robo_minerador triage --help
.\.venv\Scripts\python.exe -m robo_minerador discover --help
Test-Path -LiteralPath "C:\Users\Estefano\AppData\Local\Temp\opencode"
.\.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir "C:\Users\Estefano\AppData\Local\Temp\opencode"
```

Esperado: ajudas com saída 0 e wheel com receitas/auxiliares. O coordenador registra o nome efetivo da wheel e executa `python -m zipfile -l` nela para inspecionar conteúdo, sem inferir empacotamento apenas pelo sucesso do build.

### Gate integrado antes de executar origens reais

**Responsável:** coordenador; revisão por subagentes independentes. Não editar módulos em paralelo com implementadores.

- [ ] Ler o diff completo e reconciliar contratos de Python/Node/receitas, critérios, nomes de eventos e esquema. Verificar que nenhum fallback aprova identidade ambígua ou observação incompleta.
- [ ] Conferir um registro de repositório e cada tipo de instância da fixture pelo escritor real: testemunhas, cobertura, comandos/dependências e decisões.
- [ ] Executar a suíte Python existente com timeout do harness de pelo menos 1.200.000 ms; a suíte histórica completa levou cerca de 686 s. Não confundir timeout do harness com falha de teste.
- [ ] Executar a suíte Node existente e as integrações Docker controladas do adaptador nas versões de receitas suportadas. Não exigir Docker dos testes unitários independentes; uma suíte de integração não executada por daemon ausente continua bloqueio, não sucesso.
- [ ] Despachar um subagente para review integrado de spec + quality, com inspeção independente do código e evidências. Corrigir e revisar novamente. Registrar aprovação nas duas dimensões antes do piloto.
- [ ] Conferir hashes protegidos novamente, receitas congeladas, ausência de conflitos e acesso ao daemon. Somente então liberar a tarefa 6.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm test --prefix ".\src\robo_minerador\typescript"
git diff --check
git status --short
```

Esperado: verificações pertinentes aprovadas; nenhum resultado real modificado. Resultados históricos não substituem comandos atuais.

## Tarefa 6 — Piloto nas seis origens e fechamento

**Responsável:** coordenador, após aprovação da execução do plano e conclusão da tarefa 5. O plano aprovado deve autorizar explicitamente esse piloto; a aprovação da escrita documental não o executa. Um subagente revisor independente realiza o review conjunto de spec + quality do conjunto e das evidências ao final da tarefa.

**Arquivos próprios:** `README.md` e este plano para registrar resultados; `results/04_validacao.jsonl` é produzido exclusivamente pela CLI. Correções de código voltam ao implementador da tarefa correspondente.

### Piloto oficial

- [ ] Revalidar o 03 e registrar hash/contagens atuais. Se a população mudou, comunicar a diferença; não substituir a entrada para reproduzir artificialmente as 189 candidatas históricas.
- [ ] Conferir que o 04 não existe. Se existir, parar e pedir autorização de substituição; não passar overwrite automaticamente. Não modificar 01/02/03 nem suas cópias originais.
- [ ] Validar snapshots offline e receitas. Pendências/rejeições pré-execução comprovadas são resultados legítimos; não forçar execução de projeto com escopo inválido ou serviço proibido.
- [ ] Executar uma única passagem oficial de cada suíte elegível, sequencialmente e com limites congelados. Não repetir projeto após falha para instalar outra versão, alterar config, reparar código ou obter cobertura.

```powershell
.\.venv\Scripts\python.exe -m robo_minerador validate
```

- [ ] Registrar código de saída e ler todos os diagnósticos. Código 1 pode representar pendências legítimas; código 0 não significa que todas as funções foram aprovadas.
- [ ] Reabrir o 04 pelo validador de saída, conferir fechamento, totais, vínculo de cada candidata e todas as testemunhas creditadas. Comparar os hashes protegidos e inspecionar limpeza de recursos próprios.
- [ ] Registrar contagens reais por origem e causa de rejeição/pendência, incluindo falhas de instrumento. Não inferir que o problema foi resolvido se nenhuma instância tiver evidência dinâmica demonstrada; não impor quota artificial de aprovação.
- [ ] Se surgir defeito do minerador, preservar evidências da tentativa e reportar o bloqueio. Uma nova tentativa real exige decisão explícita e proveniência da execução anterior; nunca ocultá-la como simples correção de teste.

### Evidências e reviews finais

- [ ] Registrar comandos, versões, hashes, tempos de fases, decisões por origem, casos contabilizados, cobertura disponível e limitações efetivas. Evidências científicas ficam no 04; este plano apenas explica os resultados sem se tornar dependência para reconstruí-los.
- [ ] Atualizar status da entrega sem apresentar aprovação estática como dinâmica ou pendência operacional como reprovação. Documentar qualquer impedimento que permaneça aberto.
- [ ] Despachar um subagente revisor de spec + quality do conjunto final, distinto dos implementadores. Não marcar conclusão enquanto houver achado bloqueante em qualquer dimensão ou verificação obrigatória não executada.
- [ ] Rodar novamente somente as verificações afetadas por mudanças posteriores ao gate da tarefa 5, além de conferir saída e whitespace final. Evitar repetir a suíte longa sem alteração ou preocupação nova que o justifique.
- [ ] Apresentar resumo ao pesquisador com arquivos, evidências, decisões e pendências. Somente após todas as seis tarefas e seus reviews aprovados, aguardar autorização explícita para um commit final com as alterações pretendidas. Não fazer commits intermediários nem iniciar deduplicação automaticamente.

## 6. Matriz de cobertura da spec

| Seções da spec | Tarefas |
| --- | --- |
| 1–2: objetivo, fontes e seis origens | 1, 6 |
| 3: escopo unitário, preparação e recursos | 1, 3, 4 |
| 4–5: componentes, receitas, replay e materialização | 1–4 |
| 6: Docker, recursos, rede e ciclo de vida | 2, 5 |
| 7: identidade e casos executados | 3, 4 |
| 8: cobertura | 3, 4 |
| 9: critérios e classificação de falhas | 2, 4 |
| 10: entrada, CLI, zero candidatas e overwrite | 1, 4, 5 |
| 11: JSONL, proveniência, estados e códigos | 4–6 |
| 12: consumo futuro por deduplicação/construção | 4, 5 |
| 13: verificação proporcional e aceite real | 1–6 |
| 14: convenções, subagentes e limites de escopo | Seções 1–2 deste plano; todas as tarefas |

## 7. Registro da execução

Execução autorizada pelo pesquisador após a revisão do agrupamento e do fluxo conjunto de reviews. Não há autorização de commit intermediário ou push.

### Tarefa 1 — implementação e review aprovados

- Implementador: `ses_e48c2066effeJF77qZMfgOTQ5D`. Leitor integral v3, catálogo/validador de receitas e loader em lote implementados.
- Revisor spec + quality: `ses_e48a71419ffeBAdqFvBYH7vOVl`. Primeiro review pediu quatro correções: tipos/caminhos/diagnósticos da entrada, build desnecessário de macrotracker, opções de instalação e identificação de divergências individuais. Após as correções, **APPROVED** nas duas dimensões.
- Verificação independente do revisor: `python -B -m pytest tests/test_validation_input.py tests/test_validation_recipe.py tests/test_extraction_context.py tests/test_extraction_format.py -q -p no:cacheprovider` → **117 passed, 102,22 s**.
- Coordenador inspecionou o diff do loader e o contrato de receitas e reabriu entrada/catálogo: seis grupos, 189 approved, origem partial, hash do 03 `fcf3b6a01cbd309708762c5bff69bb27c50e12137b607603f58fcd59aed423c6`.
- Docker Linux passou a responder. Imagens oficiais foram resolvidas/inspecionadas pelo implementador; limites efetivos e isolamento ainda serão demonstrados na tarefa 2.
- Receitas explicitamente pendentes: armature `UNIT_SCOPE_UNRESOLVED`; dental-erp `ENVIRONMENT_REQUIREMENTS_CONFLICT`; macrotracker `TOOL_BOOTSTRAP_UNVERIFIED`. Não são aprovações de execução. Macrotracker não requer build para a suíte unitária conforme condição development/imports/CI originais.
- Nenhuma candidata executada ou dependência candidata instalada. Testes novos justificam riscos de seleção, adulteração e integridade do consumo em lote.

Baseline SHA-256 dos resultados protegidos, conferido pelo coordenador antes das tarefas de execução:

| Arquivo | SHA-256 |
| --- | --- |
| 01_descoberta-original.jsonl | `fc5b53545079a4a8c75a3cf805e4bd1b3508a104300a032ef2d09b52ea3247b7` |
| 01_descoberta.jsonl | `8ddf83a95a9a1e4d3046705ff74b8f947843bb27fa9e4ebb87f08e6b20542f48` |
| 02_triagem-original.jsonl | `ed88a852ac6ed3706e609722daea446d6f6ab85b2bd8d28798c61b50786669e4` |
| 02_triagem.jsonl | `52b9ef1e96e2b578c545890f9568fb8be2d67e66dfb7cfa7c28cd1e086e65bcd` |
| 03_extracao-original.jsonl | `0ada94ae6000a42c78652d432b2bb35d3075d2b44f62d17056feafc9f5c34187` |
| 03_extracao.jsonl | `fcf3b6a01cbd309708762c5bff69bb27c50e12137b607603f58fcd59aed423c6` |

### Tarefa 2 — implementação e review aprovados

- Implementador: `ses_e48964240ffeG7xsppt7aEySPh`; revisor conjunto: `ses_e486623b2ffeKLUgUyWQaepruh`, **APPROVED** em spec e quality.
- Executor, workspace Linux, recursos e cancelamento implementados; fixtures reais comprovaram bytes/modos/links/export-ignore, CPU 4, memória 8 GiB e swap adicional zero, rede none com loopback, instrumento readonly e limpeza.
- Revisor executou **73 passed em 47,34 s** com Docker opt-in para executor/receita/entrada e **62 passed em 285,05 s** para snapshot/transporte/contexto. Docker Engine 29.6.1, Linux/amd64, cgroup v2. Nenhum recurso próprio remanescente.
- Macrotracker receita v2 resolvida com bootstrap pnpm 10.34.7 em volume confiável separado, tarball SHA-512 e conferência offline. Preparação não altera manifesto/lockfile candidato.
- Limitações explícitas: instalação/preparação em bridge sem firewall de domínios; LFS/submódulos recusados conservadoramente como pendência operacional; outras arquiteturas/cgroup v1 não certificados pela evidência real.

### Tarefa 3 — implementação e review aprovados

- Implementador: `ses_e485df932ffec5qLjbUYuSSA1Z`; revisor conjunto: `ses_e47b75307ffejA04couLEAvmLh`.
- Ponte `configure_instrument` e `InstrumentSession.collect`, plano AST, transformação em memória, execução dos comandos originais npm/pnpm, reporter/censo e protocolo entregues. Arquivos adicionais de responsabilidade única registrados no mapa acima.
- Primeiro review identificou dois P1: receptor `undefined` sombreável e falsa completude em descendência síncrona opaca. Corrigidos com `void 0` e prova explícita de observabilidade/entrypoint, sem inferir árvore completa pela herança do preload. Re-review: **APPROVED** nas duas dimensões.
- Revisor confirmou **34 passed, zero skips, 311,16 s** em evidência com ponte Docker; **10 passed, zero skips** nas fixtures Node/Docker da matriz; **83 passed, 2 skipped** nas regressões T1/T2, sendo skips integrações T2 sem opt-in nessa rodada.
- Matriz real: Node22.23.4/Vitest4.1.10/Vite7.3.6; Node22.23.4/Vitest4.1.11/Vite8.3.0; Node24.22.2/Vitest5.0.5/Vite8.3.4; Node26.13.0/Vitest5.0.5/Vite8.3.5. Scripts npm/pnpm, lifecycle, argumentos, recursão, casos/skips/retries e falha beforeAll preservados nas fixtures.
- Cobertura global, quando já disponível, é separada da cobertura associada indisponível. Outros pools/runner customizado e formas sintáticas não comprovadas continuam pendentes; não são forçados a passar. Nenhuma suíte candidata foi executada.

### Tarefa 4 — implementação e review aprovados

- Implementador: `ses_e4798458ffferj137fgnZvMomC`; revisor conjunto: `ses_e4783b0dbffe4RFH1E93doaN5U`. Avaliação científica extraída em `validation_evaluation.py`, preservando protocolo T3.
- Coordenador/publicador e validador de saída implementados, com logs integrais incorporados, critérios por repositório/instância, testemunhas por caso e cobertura separada por escopo. Fixture pelo escritor real demonstra quatro casos aprovados e três rejeitados, com fechamento completed.
- Primeiro review pediu três correções: não ocultar falha operacional por rejeição científica, não usar cobertura de relatório bruto recusado e exigir metadados/evidências coerentes no validador de saída. Corrigidos; re-review **APPROVED** em spec e quality.
- Revisor confirmou **145 passed, 11 skipped, 16,53 s** nas verificações Python pertinentes e **6 passed, zero skips** Node. Skips são integrações Docker opt-in, já demonstradas nas tarefas anteriores. Coordenador executou adicionalmente o publicador: **25 passed, 0,81 s**.
- Sem auditoria suficiente de recursos, ausência de serviços permanece pendente; sucesso da suíte/rede none não a substitui. O executor real ainda não fornece autoauditoria estruturada de recursos. Essa limitação será reportada no piloto, sem aprovações fabricadas.
- Nenhuma candidata executada ou resultado real modificado.

### Tarefa 5 — CLI, distribuição e gate integrado aprovados

- Implementador: `ses_e4779d953ffe1QbqAbMEPcfddD`; revisor conjunto integrado: `ses_e47649f74ffejVLYN16suXR6rw`.
- CLI validate com help/overwrite, preflight antes de escrita, zero candidatas sem ferramentas externas, streaming e códigos 0/1/2 implementados. Catálogo e auxiliares incluídos na wheel; README atualizado com limitações reais.
- Gate do implementador com Docker opt-in: **676 passed, zero skips, 1.022,35 s** Python e **102 passed, zero skips, 28,158 s** Node. Não houve suítes candidatas. Wheel real inspecionada byte a byte, sem cache/resultados/testes/node_modules.
- O review integrado identificou três lacunas no validador T4: auditoria de recursos sem universo suficiente, contagem exata com observação incompleta e abertura sem metadados obrigatórios. Autor T4 corrigiu com universo portátil hash/linhas, reconstrução da contagem e validação estrita de política/limites/versões.
- Re-review integrado: **APPROVED** spec + quality. Revisor executou **176 passed, 11 skips opt-in, 20,44 s** Python e **6 passed, zero skips** Node e reproduziu as corrupções, agora recusadas. Somente essas partes afetadas foram repetidas após o gate longo.
- Seis resultados protegidos e receitas preservados. Gate libera o piloto T6 autorizado pelo pesquisador; 04 ainda não existia ao final do review.

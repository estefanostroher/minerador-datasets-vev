# Snapshot reutilizável e elegibilidade estática — plano de implementação

> **Estado mais recente, após o fix P1 de contexto JS:** consultar as [evidências finais](2026-11-04-extracao-v3-evidencias.md). Os três achados do [marco histórico de retomada](2026-11-03-retomada-v3.md), o falso bloqueio de `stubGlobal(fetch)` e a guarda de fonte JS `undefined` foram corrigidos e revisados. A revisão final encontrou ainda JS de contexto excluído por `allowJs=false`, que ocultava mocks: corrigido, aderência e qualidade **APPROVED**. Revisão documental final **APPROVED**, único P2 resolvido e nenhum achado. Entrega autorizada para commit pelo coordenador, sem push; o hash é consultável no histórico Git.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preservar o snapshot versionado no SHA da triagem e permitir encaminhamento estático fundamentado à validação, sem exigir prova de execução na extração.

**Architecture:** Git bare fornece objetos e inventário verificáveis em cache por repositório/SHA, sem checkout ou execução de candidatos. Python projeta os textos necessários para o auxiliar TypeScript e publica JSONL v3 referenciando o artefato completo. O analisador separa integridade/evidência estática de requisitos de execução, que permanecem não avaliados até a validação.

**Tech Stack:** Python >=3.11, Git, PyGithub existente, Node >=22, TypeScript 6.0.3, pytest e node:test. Nenhuma instalação de dependências candidatas.

---

## Estado, entradas e autorização

Plano aprovado em 2026-11-03 pelo pedido de prosseguir, incluindo implementação com subagentes e piloto das cinco origens da coorte. Contrato detalhado: `docs/superpowers/specs/2026-11-03-snapshot-reutilizavel-design.md`. Cache em disco, requisito Git, limites e publicação v3 aprovados. Resultados oficiais, execução de candidatos, coleta ampliada e commits permanecem fora da autorização.

O parágrafo acima registra a autorização inicial. Na retomada, o pesquisador **autorizou o commit pelo coordenador após correções e verificações, sem push**. Entrega autorizada para commit pelo coordenador, sem push; o hash é consultável no histórico Git. As verificações mais recentes do coordenador registram **501 testes Python aprovados (685,99 s), 92 Node aprovados (1,561 s)** e `extract --help` com saída 0. A rodada anterior de 481 Python/88 Node é histórica. Itens marcados abaixo têm comprovação na implementação/revisões e na auditoria consolidadas; itens abertos não significam necessariamente falha, mas exigem confirmação específica ou ação final do coordenador.

O checkout contém alterações anteriores de contexto por instância ainda não commitadas. Não apagá-las, revertê-las em bloco, misturá-las com resultados ou criar commit automaticamente. A nova implementação deverá substituir deliberadamente as políticas inadequadas de `context-impact.mjs` e atualizar seus testes correspondentes.

Trabalhar na branch atual, sem worktrees/branches auxiliares. Comentários/docstrings de todas as funções, métodos e callbacks em português; identificadores em inglês. Testes para riscos concretos, sem cobertura mínima ou imposição de TDD. A aprovação de uma tarefa exige revisão independente de aderência e depois qualidade; corrigir os apontamentos antes da tarefa seguinte.

## Decisões aprovadas e implementadas

| Tema | Decisão |
| --- | --- |
| Aquisição | Git bare no SHA exato; sem tarball como prova de completude, sem fallback para branch. |
| Persistência | `.cache/robo-minerador/snapshots/<id>/<sha>/`, manifesto canônico e marcador completo atômico. |
| API | Metadados na aquisição inicial; nenhum GET de blob por função/arquivo; cache hit sem rede da extração. |
| Limites de aquisição | 100 mil entradas; 512 MiB de blobs; 1 GiB lógico/300 s por aquisição; duas aquisições simultâneas. Disco amostrado por `st_size` durante fetch; inventário/bytes conferidos em streaming após fetch, antes da publicação. Não é quota física rígida. |
| Análise | 32 MiB de texto por invocação, 512 MiB de heap e 180 s Node; limite de análise não é corrupção do snapshot. |
| Contexto | Integridade e presença das evidências versionadas, sem prometer executabilidade. |
| Saída | Novas execuções v3; leitura v1/v2 preservada; cache necessário para reproduzir v3. |
| Encaminhamento | Apenas `approved`; pendentes continuam auditáveis e não acionam validação. |
| Piloto | Cinco repositórios da coorte de 191, no mesmo SHA, só após autorização e sem sobrescrever resultados oficiais. |

Limites são controles operacionais, não heurísticas científicas de elegibilidade. Excesso produz diagnóstico operacional, não ausência comprovada de requisito. Não há limite total do cache nem limpeza automática. Heap/timeout e replay estrito valem por invocação, não como orçamento agregado.

## Mapa de arquivos e responsabilidades

| Arquivo | Responsabilidade |
| --- | --- |
| Novo `src/robo_minerador/snapshot_store.py` | Cache, referências, manifesto, integridade e publicação atômica. |
| Novo `src/robo_minerador/git_transport.py` | Subprocessos Git confiáveis, aquisição cancelável e leitura de objetos. |
| `src/robo_minerador/extraction_snapshot.py` | Projeção textual do snapshot persistido e evidências de análise. |
| `src/robo_minerador/config.py` e `.gitignore` | Caminho do cache e limites exclusivos da extração. |
| `src/robo_minerador/extraction.py`, `cli.py` | Integração, preflight e vida útil compartilhada dos recursos. |
| `src/robo_minerador/typescript/context-impact.mjs` | Evidência estática e requisitos de execução separados. |
| `src/robo_minerador/typescript/virtual-project.mjs`, `test-associations.mjs`, `analyze.mjs` | Leitura de contexto/configurações, associação e critérios. |
| `src/robo_minerador/extraction_analyzer.py` | Protocolo estrito e verificação de evidência/snapshot, sem bloqueio dinâmico global. |
| `src/robo_minerador/extraction_format.py` | Publicação/expansão v3 e compatibilidade de leitura v1/v2. |
| Novo `src/robo_minerador/extraction_context.py` | Carregamento offline verificável para o futuro consumidor da validação. |
| Novos `tests/test_snapshot_store.py`, `test_git_transport.py`, `test_extraction_context.py` | Integridade, aquisição, recursos e loader. |
| `tests/test_extraction_snapshot.py`, `test_extraction_analyzer.py`, `test_extraction.py`, `test_extraction_format.py` | Integração e regressões do contrato. |
| `src/robo_minerador/typescript/tests/context-impact.test.mjs` | Fronteira estática/dinâmica e associações protegidas. |
| README e especificações de extração | Novo contrato, limites e evidências reais. |

## Tarefa 0 — Fixar baseline e contrato

**Responsável:** coordenador. **Dependência:** aprovação deste plano.

- [x] Registrar `git status --short`, diff e lista de arquivos novos da implementação anterior; não criar backup com tokens nem descartar trabalho.
- [x] Registrar hash, tamanho, cabeçalho e rodapé dos três resultados atuais; tratar os arquivos como somente leitura durante desenvolvimento.
- [x] Marcar a especificação nova como aprovada e os adendos anteriores como históricos onde houver conflito, sem apagar suas evidências de zero aprovações.
- [x] Executar as suítes atuais e registrar os resultados efetivos, sem assumir os números antigos de 351/54.

Comandos:

```powershell
git status --short
git diff --check
.\.venv\Scripts\python.exe -m pytest -q
npm test --prefix "src/robo_minerador/typescript"
```

Aceite: baseline reproduzido e contrato aprovado. Falhas preexistentes são investigadas antes da implementação, não mascaradas.

Baseline executado pelo coordenador: **351 testes Python passaram (33,08 s)**, **54 testes Node passaram**, Git **2.53.0.windows.3**. Estado/diff e hashes dos resultados registrados em `C:\Users\Estefano\AppData\Local\Temp\opencode\snapshot_v3_baseline.json`; resultados 01/02/03 têm respectivamente 53.379, 280.558 e 58.524.958 bytes. SHA-256 do 03: `0ada94ae6000a42c78652d432b2bb35d3075d2b44f62d17056feafc9f5c34187`.

## Tarefa 1 — Armazenamento verificável e fixtures Git locais

**Responsável:** subagente de persistência. **Arquivos:** criar `snapshot_store.py` e `tests/test_snapshot_store.py`; alterar `config.py` e `.gitignore` apenas para cache/limites aprovados.

- [x] Definir a interface `SnapshotArtifact` e erros operacionais `SnapshotUnavailable`, reutilizando a semântica existente sem converter falhas em rejeições científicas. O artefato mantém a referência pública separada do caminho privado de armazenamento.
- [x] Implementar manifesto canônico UTF-8 com separadores compactos, ordem determinística e SHA-256; inventariar todos os caminhos, incluindo binários e modos especiais. Não omitir caminhos por `is_relevant`, extensão ou `export-ignore` na aquisição.
- [x] Criar staging por aquisição; validar identidade e objetos antes do marcador completo; publicar por rename atômico no mesmo volume. Um diretório final existente válido é reutilizado, não sobrescrito.
- [ ] Evitar aquisição duplicada por chave dentro do processo. Em concorrência entre processos, não publicar staging incompleto nem apagar o cache do outro processo; publicação concorrente deve validar/reutilizar o vencedor ou retornar `SNAPSHOT_BUSY`.
- [ ] Acrescentar testes de manifesto adulterado, SHA/árvore divergentes, entrada ausente, binário, nomes com diferenças de caixa, caminho não UTF-8, staging interrompido e publicação concorrente.

Contrato a implementar em `snapshot_store.py`:

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SnapshotArtifact:
    """Identifica snapshot verificado sem confundir referência e caminho local."""

    repository_id: int
    commit_sha: str
    tree_oid: str
    manifest_sha256: str
    file_count: int
    total_blob_bytes: int
    storage_path: Path

    def reference(self) -> dict:
        """Produz referência portátil, sem credenciais ou caminho absoluto."""
        return {
            "format": "git-bare-v1",
            "cache_key": f"{self.repository_id}/{self.commit_sha}",
            "repository_id": self.repository_id,
            "commit_sha": self.commit_sha,
            "tree_oid": self.tree_oid,
            "manifest_sha256": self.manifest_sha256,
            "file_count": self.file_count,
            "total_blob_bytes": self.total_blob_bytes,
            "status": "complete",
        }
```

Interfaces de `SnapshotStore` a entregar: `acquire(candidate, client, *, cancel) -> SnapshotArtifact`, `open_verified(reference) -> SnapshotArtifact` e `read_blob(artifact, path) -> bytes`. A implementação recebe o transporte por injeção para permitir testes sem rede. `open_verified` jamais adquire arquivos automaticamente.

Comando: `.\.venv\Scripts\python.exe -m pytest tests/test_snapshot_store.py -q`.

Aceite: cache completo e corrompido são distinguíveis; arquivos binários versionados são preservados; referência não admite trocar SHA ou raiz do cache.

## Tarefa 2 — Transporte Git no commit exato

**Responsável:** subagente de transporte, após interfaces da tarefa 1. **Arquivos:** criar `git_transport.py`, `tests/test_git_transport.py`; integrar chamada ao store. Não alterar descoberta/triagem nem seus limites.

- [x] Preflight de Git em modo local antes de abrir a saída. Usar lista de argumentos, não shell; URL HTTPS pública construída a partir da identidade confirmada por metadados existentes.
- [x] Criar repositório bare vazio e buscar somente o SHA registrado, com `--depth=1`, `--no-tags` e `--no-recurse-submodules`. Não usar filtros parciais de blobs. Não executar checkout, filtros ou programas do candidato.
- [ ] Isolar configurações Git de usuário/sistema, helpers de credenciais, templates/hooks e prompts; não persistir token. Admitir transporte local somente na fixture injetada de testes, não em URL candidata de produção.
- [x] Conferir o OID do commit recebido e sua árvore; executar verificação de objetos e enumerar a árvore com separação NUL. Ler blobs com `cat-file`, sem textconv/filtros, respeitando tamanho e bytes originais.
- [ ] Monitorar cancelamento, tempo, disco e quantidade/tamanho de entradas; matar e recolher os processos em qualquer falha. Não baixar submódulos ou LFS automaticamente. Registrar modos especiais no inventário.
- [x] Limitar aquisição a duas operações simultâneas e uma tentativa automática por candidato. As consultas REST mantêm o gate PyGithub; não interpretar o transporte Git como quota REST ilimitada.
- [ ] Demonstrar com repositório local que um arquivo marcado `export-ignore` permanece no snapshot Git; Git hooks de fixture não são executados; segundo acesso à mesma chave usa cache sem fetch.

Sequência de comandos a encapsular em subprocessos com ambiente controlado:

```text
git init --bare <staging>/repository.git
git --git-dir=<staging>/repository.git fetch --depth=1 --no-tags --no-recurse-submodules <https-url> <sha>:refs/minerador/snapshot
git --git-dir=<staging>/repository.git rev-parse refs/minerador/snapshot^{commit}
git --git-dir=<staging>/repository.git rev-parse refs/minerador/snapshot^{tree}
git --git-dir=<staging>/repository.git fsck --full --strict --no-reflogs
git --git-dir=<staging>/repository.git ls-tree -r -z -l refs/minerador/snapshot
git --git-dir=<staging>/repository.git cat-file --batch
```

Os argumentos de hardening e o ambiente controlado devem ser fornecidos pelo runner em todas as invocações, não copiados parcialmente entre chamadas. Capturar e sanitizar falhas sem publicar stderr ou URLs autenticadas.

Comandos de teste:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_git_transport.py tests/test_snapshot_store.py -q
```

Aceite: troca de SHA recusada; aquisição cancelável; binários e `export-ignore` preservados; cache hit sem rede; estouro de limite nunca publica marcador completo.

## Tarefa 3 — Projeção estática e fronteira dos critérios

**Responsável:** subagente de análise, após tarefas 1–2. **Arquivos:** `extraction_snapshot.py`, módulos TypeScript de análise/associação e seus testes.

- [x] Substituir a aquisição textual por projeção do snapshot verificado. Enumerar o inventário inteiro; selecionar textos para TypeScript, documentação, manifests e configurações sem descartar seus bytes no snapshot. Separar `snapshot_complete` de `analysis_complete`.
- [x] Remover o limite de 512 leituras/8 MiB apenas do caminho de aquisição da extração. Não mudar `triage_snapshot.py`. Aplicar o orçamento de análise aprovado e registrar projetos/arquivos não analisados, sem afirmar buscas negativas completas nesses escopos.
- [ ] Ajustar o FS virtual para saber que um arquivo binário existe sem fornecê-lo como fonte TypeScript. Caminhos locais usados em links/imports não podem ser inventados nem resolvidos fora do snapshot.
- [x] Substituir a promoção automática de `process`, config e carregadores a bloqueios globais por registros de `execution_requirements`. Diferenciar ausência real de arquivo usado como evidência, resolução estática de identidade e dependências somente de execução.
- [ ] Inspecionar setups/presets declarativos e imports locais resolvíveis das configurações para mocks e substituições conhecidos, sem executar expressões de configuração. Mock comprovado sobre o alvo invalida aquela evidência; configuração dinâmica não é confirmação de execução nem, isoladamente, arquivo ausente.
- [x] Manter exigências de documentação, chamada direta, unidade identificável e H-F06. Não ampliar a seleção para arrows/construtores/accessors. Preservar `validation` com contagens nulas e estado não avaliado.
- [ ] Acrescentar pares positivos/negativos com a mesma base: configuração preservada, acesso a ambiente, builtin Node, recurso binário presente, alias/reexport, mock em setup, função com mesmo nome, snapshot incompleto, referência de identidade ausente e busca negativa truncada.

Caso positivo central a transformar em regressão, usando snapshot local com `fixtures/input.bin` versionado:

```typescript
// src/math.ts
/** Incrementa a entrada. */
export function increment(value: number) { return value + 1; }

// src/math.test.ts
import { test } from 'vitest';
import { increment } from './math';
test('incrementa', () => { increment(Number(process.env.INPUT ?? '1')); });

// vitest.config.ts
export default { test: { environment: 'node' } };
```

Esperado: elegibilidade estática `approved`, requisitos de ambiente/configuração registrados e nenhuma execução confirmada. Controle negativo: acrescentar mock comprovado da implementação no setup resolvido e retirar a única evidência não mockada; a associação não pode ficar aprovada.

Comandos:

```powershell
npm test --prefix "src/robo_minerador/typescript"
.\.venv\Scripts\python.exe -m pytest tests/test_extraction_snapshot.py -q
```

Aceite: não existe mais regra “há uma config/process no repositório, logo todas as funções ficam pendentes”; identidade, mocks conhecidos e omissões estáticas reais continuam protegidos.

**Fix P1 posterior — contexto JS:** programa auxiliar de AST/símbolos independente de `allowJs` inspeciona config/setup/preset e dependências transitivas. O programa original conserva seleção/resolução científica e testemunhas, sem criar evidência JS auxiliar. Fonte necessária sem AST gera `UNANALYZED_TEXT`; sintaxe inválida gera `INVALID_SOURCE`, com impacto localizado. Matrizes TS/JS cobrem mock negativo e controle sem mock positivo. Aderência **APPROVED** em `ses_e571a862cffeSBY3iwYgGqILq5`; qualidade **APPROVED** em `ses_e574fb170ffe3b3EYsUMVS4WBj`, com execução independente dos 92 testes Node pelo revisor.

## Tarefa 4 — Protocolo Python e consumidor offline

**Responsável:** subagente de protocolo. **Arquivos:** `extraction_analyzer.py`, novo `extraction_context.py`, `tests/test_extraction_analyzer.py`, novo `tests/test_extraction_context.py`.

- [ ] Acrescentar ao protocolo interno uma descrição verificada do snapshot/inventário e requisitos de execução. Definir campos obrigatórios/permitidos, enumerações e correspondência com o manifesto fornecido pelo Python, sem confiar em `complete=true` declarado pelo Node.
- [x] Conferir trechos, caminhos, linhas, ordinais e evidências contra bytes do snapshot. Não aceitar código alterado apenas porque SHA/manifesto nominal coincide. Configuração e requisito de execução não podem ser confundidos com estado da validação.
- [ ] Substituir a reanálise condicional introduzida no ajuste anterior somente se a nova checagem preservar a recusa de protocolos adulterados. Se a repetição continuar necessária, executá-la com os mesmos recursos/cancelamento e declarar seu custo; não duplicar processos simultâneos por candidato.
- [x] Implementar `load_instance_context(instance, repository, reasons, store) -> dict`: expandir metadados, verificar identidade e `context.snapshot`, recuperar evidências localmente e retornar referência ao snapshot para a futura validação. Não instalar, executar ou buscar contexto pela rede.
- [x] Definir loader exclusivamente local: JSONL v3 sem cache íntegro resulta em erro explícito, não em download ou promoção. Leitores legados continuam permitindo inspeção v1/v2, mas não inventam snapshot ausente.
- [ ] Testar substituição de SHA, cache corrompido, path fora do inventário, binário presente, snippet adulterado, referência de contexto incompatível e execução fictícia indevidamente marcada como aprovada.

Exemplo de referência esperada para fixture, produzido pelo objeto da tarefa 1:

```python
reference = artifact.reference()
assert reference["status"] == "complete"
assert reference["commit_sha"] == candidate["commit_sha"]
assert reference["cache_key"] == f"{candidate['repository_id']}/{candidate['commit_sha']}"
assert "storage_path" not in reference
```

Asserções são executadas nos testes com `artifact` e `candidate` construídos no mesmo caso, não com dados de produção ou variáveis de ambiente implícitas.

Comando: `.\.venv\Scripts\python.exe -m pytest tests/test_extraction_analyzer.py tests/test_extraction_context.py -q`.

Aceite: `approved` não contorna integridade; requisitos de ambiente continuam não avaliados; loader não tem fallback de rede nem aceita snapshot de outro commit.

## Tarefa 5 — Publicação v3 e integração da extração

**Responsável:** subagente de integração, após tarefas 1–4. **Arquivos:** `extraction.py`, `cli.py`, `extraction_format.py`, testes de extração/formato e README.

- [x] Preflight Git/Node e entrada antes de abrir saída ou adquirir candidatos. Manter recusa de saída existente sem `--overwrite`, workers e ordenação. Não acrescentar busca automática de pendentes.
- [ ] Criar um store/controle de aquisição por execução, com cancelamento integrado ao pool. `_extract_candidate` usa snapshot verificado, projeção e analisador; falha de aquisição registra problema operacional, sem declarar ausência científica.
- [x] Acrescentar `context.snapshot`, `context_policy` e `execution_requirements` no repository. Manter textos úteis em `context.files`; binários e inventário completo permanecem no artefato referenciado.
- [x] Parametrizar `compact_records(records, *, schema_version=2)`: manter o default para consumidores/testes v2; a extração nova solicita v3. Início/fim e registros necessários à identificação do contrato indicam a versão coerente. `expand_instance` suporta 1/2/3 sem buscar artefatos.
- [x] Preservar IDs para unidades com mesma origem/posição/ordinal; snapshot completo pode revelar novas unidades. Não exigir contagens iguais artificialmente. Atualizar a expansão/roundtrip sem repetir listas do inventário por função.
- [ ] Registrar limites, cache hit/miss e política de contexto no início/fim. Não afirmar que tempo por invocação é prazo total do repositório. Conservar `issue_count`/`error_count` sem tratar requisitos de execução informativos como falhas operacionais.
- [x] Documentar que o JSONL v3 precisa do cache correspondente para reprodução. Nunca apagar snapshots completos ao executar `--overwrite`; esse flag substitui apenas a saída explicitamente autorizada.
- [ ] Testar CLI completa com transporte Git local injetado: uma instância `approved`, outra pendente por evidência insuficiente e outra rejeitada por requisito comprovadamente descumprido; consumidor seleciona somente a primeira.

Contrato ilustrativo de repositório v3, sem dados fictícios confundidos com evidência real:

```json
{
  "schema_version": 3,
  "record_type": "repository",
  "context_policy": "versioned_snapshot_static_evidence_v1",
  "execution_requirements": [
    {"kind": "environment", "path": "src/math.test.ts", "status": "not_evaluated"}
  ]
}
```

O registro real também preserva os campos obrigatórios de origem, contexto, resumo e referência produzida por `SnapshotArtifact.reference()`. O exemplo é apenas do acréscimo contratual, não uma linha publicável completa.

Comandos:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extraction.py tests/test_extraction_format.py tests/test_extraction_context.py -q
.\.venv\Scripts\python.exe -m robo_minerador extract --help
```

Aceite: v1/v2 continuam legíveis; novas execuções v3 têm referências verificáveis; expansão não inventa arquivos; o consumidor offline demonstra encaminhamento de aprovados sem executar a validação.

## Tarefa 6 — Piloto real limitado e diagnóstico de aceite

**Responsável:** subagente de auditoria após revisões das tarefas anteriores. **Condição:** autorização de rede para este piloto. Escrita em diretório temporário aprovado e no novo cache; resultados oficiais somente leitura.

- [x] Ler os SHAs originais e selecionar as cinco origens da coorte: `YounghyeonPark/agbrte`, `Nemryz/opencode-sdd-integrity`, `mossglenn/armature`, `HiAnur/anur-living-map` e `saching13/macrotracker-server`. Não resolver branch atual nem substituir commit indisponível.
- [ ] Inspecionar previamente exemplos da coorte para selecionar um caso com evidências estáticas concretas; registrar por que deve ser elegível segundo o contrato revisado.
- [x] Adquirir cada snapshot uma vez, dentro dos limites aprovados, e publicar o replay v3 apenas no temporário. Registrar bytes, tempo, quantidade de aquisições, consultas REST e cache hits. Segunda passagem usa cache e deve fazer zero aquisições.
- [x] Comparar as 191 identidades antigas com as unidades novas por identidade exata; se novas unidades mudarem ordinais lexicais, inspecionar os casos, não remapear por nome. Contar adições, desaparecimentos e transições.
- [x] Demonstrar pelo menos um caso real elegível com snapshot íntegro, `approved` e aceito pelo loader offline. Se nenhum caso for demonstrado, tratar o aceite real como não atendido e apresentar bloqueios específicos antes de declarar solução.
- [x] Para pendentes, distinguir aquisição incompleta, limite/limitação do analisador, identidade/mocks, documentação e H-F06. Não usar um único motivo genérico de contexto para ocultar os bloqueios restantes.
- [x] Conferir hash/tamanho dos resultados originais antes/depois. Não executar configuração, instalação ou suíte candidata; manter `validation.status=not_evaluated` e contagens nulas.

O piloto não promete aprovar 191 instâncias e não cria uma quota científica. Sua finalidade é verificar que o sistema consegue encaminhar casos reais comprovadamente elegíveis, em vez de passar apenas em uma fixture pequena.

**Rodada anterior ao fix JS (histórico):** 2.760 unidades, 189 approved/76 rejected/2.495 pendentes; coorte 188 approved/3 pendentes, sem novas/removidas. Aquisição original: cinco metadados/cinco fetches. Replay daquela rodada: cinco hits/zero misses, offline. Segunda passagem comprovada para HiAnur e loader `nextTip` no mesmo store, não para os cinco novamente. As três pendências decorrem de `src/main/conformance.ts` UTF-8 com dois NULs, preservado mas excluído da projeção. Auditoria: 507 arquivos protegidos sem alterações, incluindo seis results; o 03 atual é trabalho recente do usuário. Detalhes, custos e timeout histórico no [relatório persistente](2026-11-04-extracao-v3-evidencias.md).

**Revalidação mais recente após o fix JS:** mesmos 2.760 registros de instância e cinco repositórios, integralmente iguais ao replay anterior exceto horário; contagens e coorte acima preservadas. Cinco hits/zero misses e zero metadados/fetch/aquisições/sockets; pipeline **400,231 s**, faseado e sem timeout. HiAnur: pipeline **11,979 s**, loader **11,373 s**, aceito no mesmo store após fechamento normal. **553 arquivos protegidos intactos**, incluindo os 507 anteriores e seis results, conforme baseline recapturado após mudanças autorizadas. As 47 fontes JS já tinham AST no programa original: não houve novas evidências nesse corpus. O timeout de 900 s pertence à rodada histórica anterior.

## Tarefa 7 — Revisão final, evidências e entrega

**Responsável:** coordenador e revisores independentes. **Arquivos:** README, especificações e este plano; corrigir código apenas em retorno ao implementador responsável.

- [x] Revisar aderência metodológica e qualidade em etapas separadas, incluindo os antigos falsos bloqueios e os controles negativos de mocks/integridade.
- [x] Registrar comandos/resultados efetivamente executados, integridade, custo de disco/rede, resultados do piloto e limitações não resolvidas. Não usar testes passando como substituto do aceite real.
- [x] Atualizar docs anteriores com referência ao contrato substituto, preservando a história e a constatação de que o ajuste anterior não liberou a coleta.
- [x] Executar verificações finais e apresentar diff/arquivos alterados, sem commit ou push automático.

Fechamento comprovado pelo coordenador: **501 Python (685,99 s), 92 Node (1,561 s), CLI `extract --help` com saída 0 e `git diff --check` com saída 0**; status, diff e log inspecionados. Revisões de aderência e qualidade aprovadas. A re-revisão documental `ses_e56d4796affeiNYYzHsAX8xJIJ` retornou **APPROVED**, único P2 resolvido, nenhum achado. Nenhuma suíte foi repetida nesta edição documental.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm test --prefix "src/robo_minerador/typescript"
.\.venv\Scripts\python.exe -m robo_minerador extract --help
git diff --check
git status --short
```

## Critérios de parada

- SHA/cache incorreto ou snapshot incompleto: parar aquele candidato e registrar falha operacional; nunca usar branch ou completar o contexto por suposição.
- Associação da implementação original não comprovada: manter pendência, mesmo com snapshot completo.
- Regressão de seleção, IDs, mocks ou integridade: corrigir antes de seguir.
- Ausência de exemplo real aprovado no piloto: não declarar o problema resolvido; revisar a classificação concreta antes de ampliar coleta ou repetir ajustes genéricos.
- Regra metodológica ambígua: parar somente o trecho afetado e perguntar ao pesquisador.

## Revisão de consistência do plano

As tarefas 1–2 atendem aquisição/integridade; a 3 separa análise de execução; a 4 protege o protocolo e o consumidor; a 5 integra e versiona; a 6 mede o resultado real; a 7 fecha as evidências. Cache, Git, limites e schema v3 são alterações de contrato aprovadas e implementadas. A validação dinâmica permanece fora do escopo e recebe somente `approved`. Revisão documental final **APPROVED**. Entrega autorizada para commit pelo coordenador, sem push; o hash é consultável no histórico Git. A regra histórica de não criar commit automático permanece válida.

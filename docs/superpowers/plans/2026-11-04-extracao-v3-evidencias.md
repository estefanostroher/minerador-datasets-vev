# Extração v3 — evidências finais de 2026-11-04

> **Evidência mais recente:** rodada `pilot_v3_final_js_20261104`, após o fix P1 de contexto JavaScript. Os timestamps dessa rodada alcançaram 2026-11-05 UTC, mantendo o prefixo solicitado. A seção abaixo registra seu resultado; as medições da rodada `pilot_v3_final_20261104` permanecem identificadas como históricas.

## Conclusão e escopo

O replay offline dos cinco snapshots originais produziu **2.760 unidades: 189 approved, 76 rejected e 2.495 not_evaluated**. Na coorte histórica de 191, **188 estão approved e três permanecem pendentes**, com ganho de 185 aprovações sobre o piloto inicial. Aprovação estática permite encaminhamento à futura validação; não significa dataset validado ou testes executados.

Referências: [contrato vigente](../specs/2026-11-03-snapshot-reutilizavel-design.md), [plano](2026-11-03-snapshot-reutilizavel-extracao.md) e [marco histórico de retomada](2026-11-03-retomada-v3.md). Este relatório consolida a auditoria persistida em TEMP e as verificações finais informadas pelo coordenador. A edição documental não repetiu o piloto nem as suítes.

Checkout: `C:\Users\Estefano\Projects\robo-minerador`, base Git da auditoria `c350773`. Não houve instalação de dependências candidatas nem execução de suas configurações, scripts ou testes. As alterações locais anteriores foram preservadas. Aderência e qualidade do fix JS e revisão documental final estão **APPROVED**. Entrega autorizada para commit pelo coordenador, sem push; o hash é consultável no histórico Git.

## Última rodada — após correção P1 de contexto JS

A revisão final detectou que JS excluído do programa por `allowJs=false` podia ocultar mocks. A correção usa **programa auxiliar para AST/símbolos de contexto JS**, independente de `allowJs`, abrangendo configuração, setup, preset e imports transitivos. O **programa original** continua determinando seleção, resolução científica e testemunhas de chamadas; JS disponível somente no auxiliar não gera testemunhas científicas inventadas. Fonte necessária sem AST produz `UNANALYZED_TEXT`; sintaxe inválida produz `INVALID_SOURCE`, com impacto localizado.

As matrizes de regressão TS/JS incluem mock que impede aprovação e controle sem mock que preserva a evidência válida, em config/setup/preset/transitivo. Aderência **APPROVED**, sessão `ses_e571a862cffeSBY3iwYgGqILq5`; qualidade **APPROVED**, sessão `ses_e574fb170ffe3b3EYsUMVS4WBj`, com execução independente dos **92 testes Node** pelo revisor de qualidade. Os resultados das suítes completas constam da seção seguinte, informados pelo coordenador.

### Replay e alcance da evidência

- Mesmos cinco SHAs e mesmos **2.760 registros de instância e cinco repositórios**, integralmente iguais ao replay anterior, retirando somente `extracted_at`. Zero novas/removidas/alteradas; critérios, razões, contexto, requisitos, issues, testes, validação e IDs approved preservados. Diagnóstico final igual semanticamente, exceto `observed_at`; hash do JSONL difere por metadados de execução/proveniência.
- Resultado confirmado: **189 approved, 76 rejected, 2.495 not_evaluated**; coorte **188 approved/3 pendentes**, 191/191 iguais à rodada anterior. As contagens por repositório na tabela abaixo continuam atuais. Os cinco terminaram `partial`, nenhum `failed`; 23 grupos/933 ocorrências de issues.
- **47 fontes JS projetadas já tinham AST no programa original**: agbrte 16, Nemryz quatro, Armature 12, HiAnur 12, Macrotracker três. Nenhuma dependia exclusivamente do auxiliar; não houve novas evidências nesse corpus. O replay comprova estabilidade após o fix, não uma nova prova real do cenário adversarial `allowJs=false`, coberto pelas regressões.
- Cinco cache hits, zero misses/erros; **zero metadados, fetch, aquisições e sockets**. `cache_acquire_calls=5` representa consultas ao store, não cinco aquisições novas. Store externo preservado após conclusão normal.
- **553 arquivos protegidos, zero alterações**, incluindo os 507 anteriores, relatórios históricos e seis results. Baseline recapturado após o fix JS e as edições documentais autorizadas; essas mudanças anteriores não foram tratadas como violação. Essa proteção antecede a presente atualização de docs.

### Loader e custo atuais

Novo pipeline público HiAnur, drenagem e `records.close()`, seguido do loader `nextTip` no **mesmo objeto SnapshotStore: PASSED**. Pipeline **11,979 s**, loader **11,373 s**, fase completa com integridade **23,929 s**. Mesma unidade `web/map/tips.ts:76–93` identificada na prova histórica abaixo; 343 unidades, 23 approved/320 pendentes, cache hit 1 e miss/error 0. Store não cancelado, cliente fechado e zero metadados/fetch/aquisições/sockets. O loader real cobre uma approved, não todas as 189.

Pipeline dos cinco: **400,231 s**; fase completa com integridade **401,265 s**. Dez invocações Node de análise/conferência somaram **378,358 s**, máximo **92,821 s** por invocação. Comparação separada: **2,985 s**. A execução foi faseada e **não teve timeout**. Traces estáticos e cobertura AST foram medidos separadamente, com comandos parcialmente paralelos; não somar essas medidas como tempo de parede total. Pico RSS não medido; os custos não constituem benchmark.

O timeout de **900 s** e a continuação de **54,214 s** pertencem exclusivamente à rodada anterior, preservada abaixo. As três pendências por NUL permanecem com a mesma projeção/issues e cache; o parsing específico daquele blob não foi repetido na rodada JS. Nenhum candidato foi executado e a validação continua não avaliada.

## Contrato entregue

- Git bare no SHA exato, sem fallback de branch ou checkout. Cache por ID/SHA com manifesto canônico, marcador completo e verificação de commit, árvore, objetos e blobs; binários e `export-ignore` preservados.
- JSONL v3 como índice canônico, com `context_policy="versioned_snapshot_static_evidence_v1"`, `context.snapshot` e `context.analysis_complete`. Os textos de `context.files` não são o inventário inteiro. Reprodução requer JSONL **e cache**.
- `execution_requirements` registra configuração, ambiente, dependências externas, carregamento dinâmico e recursos de execução com `not_evaluated`. Sua presença não veta automaticamente uma instância e seus contadores não entram em `issue_count`. Identidade, mocks/substituições, integridade e evidência estática continuam protegidos.
- `expand_instance` aceita v1/v2/v3. `load_instance_context(instance, repository, reasons, store)` exige `SnapshotStore` e approved v3: verifica evidências, reprojeta e reanalisa offline, sem fallback de rede. Encerramento normal preserva a vida útil do store externo; aborto cancela Git e Node.
- Workers de 1 a 16, padrão 4; até dois fetches/aquisições simultâneos. Cache hit íntegro faz zero consultas de metadados e zero fetch. `--overwrite` substitui somente a saída, não snapshots completos.

## Verificações e revisões do coordenador

| Verificação | Resultado informado |
| --- | --- |
| `python -m pytest -q` no ambiente do projeto, após fix JS | **501 passaram, 685,99 s** |
| `npm test --prefix "src/robo_minerador/typescript"`, após fix JS | **92 passaram, 1,561 s** |
| `python -m robo_minerador extract --help` no ambiente do projeto | **Saída 0** |
| Identidade entre tsconfigs alternativas | Corrigida; aderência e qualidade aprovadas |
| Exports concorrentes TS/JS, distinguindo declarações | Corrigidos; aderência e qualidade aprovadas |
| Escopo efetivamente aplicável de configurações | Corrigido; aderência e qualidade aprovadas |
| Falso bloqueio por `stubGlobal` de `fetch` e guarda de fonte JS `undefined` | Corrigidos; nova revisão aprovada |
| P1 JS de contexto excluído por `allowJs=false`, ocultando mock | Corrigido; aderência e qualidade APPROVED; 92 Node executados independentemente pelo revisor |

Store, transporte, protocolo, loader e CLI já haviam sido revisados nas etapas anteriores. Os números de suítes completas acima são evidências do coordenador, distintas da auditoria offline e da execução independente dos 92 Node pelo revisor de qualidade; nenhuma suíte foi repetida na edição documental. **481 Python (560,09 s) e 88 Node** foram o resultado histórico anterior ao fix JS. Aderência e qualidade do fix JS e revisão documental final estão **APPROVED**. O coordenador confirmou CLI e diff check com saída 0, além de status/diff/log inspecionados. Entrega autorizada para commit pelo coordenador, sem push; o hash é consultável no histórico Git.

A revisão documental `ses_e56d4796affeiNYYzHsAX8xJIJ` apontou um único P2: o status de qualidade do fix JS ainda constava como pendente/em curso. O revisor registrou os demais pontos sem inconsistências materiais. Após a correção nos cinco documentos, a re-revisão retornou **APPROVED**, com o único P2 resolvido e nenhum achado.

## Piloto inicial e replay anterior — comparação histórica

O piloto original adquiriu cinco snapshots, com **cinco consultas de metadados e cinco fetches**. Seu resultado foi 3 approved, 76 rejected e 2.681 pendentes. A auditoria `pilot_v3_final_20261104`, anterior ao fix JS, reutilizou os mesmos snapshots, sem nova aquisição: `cache_hits=5`, `cache_misses=0`, `cache_errors=0`, zero metadados/fetch. Todos os repositórios terminaram `partial`, nenhum `failed`. A rodada JS posterior confirmou integralmente os resultados desta tabela.

| Repositório | Unidades | Approved | Rejected | Not evaluated | Coorte approved/pendente |
| --- | ---: | ---: | ---: | ---: | ---: |
| YounghyeonPark/agbrte | 1.446 | 115 | 34 | 1.297 | 115/3 |
| Nemryz/opencode-sdd-integrity | 480 | 24 | 20 | 436 | 23/0 |
| mossglenn/armature | 398 | 17 | 21 | 360 | 17/0 |
| HiAnur/anur-living-map | 343 | 23 | 0 | 320 | 23/0 |
| saching13/macrotracker-server | 93 | 10 | 1 | 82 | 10/0 |
| **Total** | **2.760** | **189** | **76** | **2.495** | **188/3** |

Diagnóstico final: `error_count=23` grupos e `issue_count=933` ocorrências. Não são contagens de requisitos informativos ou de testes executados.

### Snapshots e custos lógicos

| Repositório / ID | SHA original | Entradas | Bytes de blobs | Bytes de cache |
| --- | --- | ---: | ---: | ---: |
| agbrte / 1317120711 | `0c5c31099ce74cf87b6b4dc34a38e6a1f6ecfe58` | 513 | 7.990.979 | 3.241.053 |
| opencode-sdd-integrity / 1281991062 | `c3d26596edf63adc444b1900790bbccd49795226` | 363 | 3.097.639 | 823.178 |
| armature / 1164258973 | `8acaa14e01102a9e4e7a5941765113539c43c613` | 322 | 5.080.632 | 1.827.427 |
| anur-living-map / 1421624693 | `6285658e0b03725860659a26de8a75415bf151db` | 199 | 2.197.054 | 788.981 |
| macrotracker-server / 1442582077 | `63d69dabed0f5ea58ae9784e40b930b5ca468702` | 101 | 1.806.793 | 272.700 |

Total: **1.498 entradas, 20.173.097 bytes de blobs e 6.953.339 bytes de cache (~6,63 MiB)**. Texto projetado: 18.006.028 bytes. São somas lógicas, não alocação física em clusters. Manifestos e objetos foram verificados; evidências de fonte, documentação e testes das 189 aprovadas foram relidas com conferência dos OIDs e bytes/hashes.

### Comparação científica

A referência histórica é `pilot_v3_output.jsonl`, não o resultado atual do usuário. Comparação por identidade exata, sem remapear por nome:

- **2.760 unidades compartilhadas, zero novas/removidas**. IDs, código, assinatura, função integral, documentação e validação iguais em todas.
- Transições: 186 pending → approved; três approved → approved; 76 rejected → rejected; 2.495 pending → pending.
- Testes iguais em 2.654 unidades. **106 unidades** receberam associações adicionais (102 Nemryz, quatro Armature): **2.057 registros distintos acrescentados**, sem remoção. São associações estáticas, não casos executados. A comparação confiável não equivale a inspeção manual de cada chamada.
- Na coorte: **185 pending → approved**, três pending → pending e três approved → approved. **191/191 preservam IDs, código, assinatura, documentação, testes e validação**; apenas o critério de contexto mudou, além da atualização de suas razões.
- A aprovação adicional fora da coorte é a unidade já existente `trimEvidenceEntries`, Nemryz, `tools/shared/types.ts:486–489`, com duas associações adicionais.

O replay histórico anterior de dez snapshots, com **0 approved, 141 rejected e 7.028 pendentes**, continua sendo evidência do ajuste antigo. A v3 substitui aquele contrato, sem reescrever sua história.

## Três pendências concretas da coorte

Todas pertencem a agbrte e mantêm os quatro outros critérios aprovados; somente `preserved_local_context` permanece pendente:

| Unidade | Local |
| --- | --- |
| `SessionHostServer.accept` | `src/host/sessionServer.ts:469–557` |
| `listenFailure` | `src/web/server.ts:84–107` |
| `admitsFrame` | `src/web/server.ts:195–201` |

As closures alcançam `src/main/ipc/api.ts:85`, cujo import `@main/conformance.js` resolve pelo alias herdado `@main/* → src/main/*`. O snapshot contém `src/main/conformance.ts`, **6.854 bytes**, OID `c0660a22eb4550d96d5d92e29c95a64e4aa8b028`, SHA-256 `2d07fd126d696eba653db3c3f92eb1549f29aaa64bab7580084bbd972fce6044`.

O blob é UTF-8 válido, com **dois NULs nos offsets 2362 e 2630**, dentro de templates (linhas 61 e 68). O parser TypeScript produziu **zero diagnósticos sintáticos**. A projeção textual exclui qualquer texto com NUL e registra `UNANALYZED_TEXT`; o host virtual não recebe essa fonte. É **limitação de análise**, não arquivo ausente, corrupção ou aquisição incompleta. Esta entrega documental preserva as três pendências; não força aprovação das 191.

As **2.495 pendências totais não compartilham necessariamente essa causa**. Contagens por critério, com sobreposição entre unidades:

| Critério | Approved | Rejected | Not evaluated |
| --- | ---: | ---: | ---: |
| identifiable_implementation | 2.760 | 0 | 0 |
| associated_documentation | 1.527 | 0 | 1.233 |
| direct_test_association | 803 | 0 | 1.957 |
| nontrivial_implementation | 1.031 | 76 | 1.653 |
| preserved_local_context | 2.313 | 0 | 447 |

Busca incompleta não comprova ausência de teste/documentação. As combinações detalhadas constam do artefato `verification.json` identificado abaixo.

## Loader e reutilização do mesmo store — prova histórica anterior ao fix JS

Prova real com `nextTip`, `web/map/tips.ts:76–93`, HiAnur, ID `4a81d684f1a7a062a93b341504e56e24b4a200a25a9ed4e60e2297acefb97e07`: pipeline público drenado e fechado normalmente, loader aprovado no **mesmo objeto store**, seguido de novo pipeline no mesmo store.

- Primeira passagem: 343 unidades, 23 approved e 320 pendentes, 12,729 s.
- Loader: aceito em 12,813 s, com snapshot/contexto/requisitos e instância verificados.
- Segunda passagem: mesmas 343 unidades, 12,463 s; instâncias expandidas idênticas, retirando apenas `extracted_at`, inclusive em relação ao replay dos cinco.
- Cada passagem teve cache hit 1, miss/error 0. `store.cancel=0`; dois clientes criados e fechados. Na continuação: **metadados=0, fetch=0, aquisições=0, sockets=0**.

O loader real foi exercitado para **uma approved de um repositório**, não para todas as 189. A conferência dos blobs de todas as aprovadas foi uma verificação separada. Toda validação continua `not_evaluated`, com `executed_test_cases=null`, `suite_passed=null` e `external_services="not_evaluated"`; zero execuções candidatas.

## Integridade e tempo observado — rodada histórica anterior ao fix JS

A auditoria comparou **507 arquivos protegidos**, incluindo os **seis resultados**, código, testes, docs, cache e relatórios históricos: **zero alterações**. Essa comparação antecede a presente edição documental e usa o estado atual capturado no início da auditoria. O `results/03_extracao.jsonl` recente é trabalho do usuário; não foi comparado ao baseline antigo como se devesse conservar aquele hash.

Na auditoria, o 03 atual tinha 114.825.617 bytes, SHA-256 `fcf3b6a01cbd309708762c5bff69bb27c50e12137b607603f58fcd59aed423c6`; o `03_extracao-original.jsonl` tinha 58.524.958 bytes, SHA-256 `0ada94ae6000a42c78652d432b2bb35d3075d2b44f62d17056feafc9f5c34187`. Ambos permaneceram intactos. Os hashes completos dos seis resultados estão no relatório temporário de origem.

O pipeline dos cinco durou **~467 s** pelos diagnósticos UTC (precisão de um segundo). O harness atingiu **900 s de timeout durante o pós-processamento**, depois de persistir `run_finished` e comparações. A continuação bem-sucedida levou **54,214 s**, com seis invocações Node e 28,163 s medidos nelas. O custo da tentativa interrompida não desaparece: não se deve apresentar só 467 s como custo total. Não há pico RSS medido nem benchmark comparável.

Limites vigentes: 100 mil entradas, 512 MiB de blobs, 1 GiB lógico/300 s por aquisição e gate de duas aquisições. Durante fetch, disco é monitorado por amostragem de `st_size`, **não quota física rígida**; inventário/bytes são conferidos em streaming após fetch e antes de publicar. Projeção de 32 MiB; heap Node de 512 MiB e timeout de 180 s **por invocação**, inclusive replay estrito, sem orçamento agregado. Não há teto total do cache nem limpeza automática. O replay final usou workers=1 e não permitiu aquisições.

## Proveniência dos artefatos

Diretório de origem: `C:\Users\Estefano\AppData\Local\Temp\opencode\`. Os arquivos históricos foram preservados; este documento torna suas conclusões principais persistentes no projeto.

**Rodada mais recente, após fix JS:**

- `pilot_v3_final_js_20261104_relatorio.md` e `_summary.json`: método e evidências consolidadas atuais.
- `pilot_v3_final_js_20261104_output.jsonl`: saída mais recente, 28.648.566 bytes, SHA-256 `beabcde011e3adfd0c517139f9289bee5ac870ca13814733de4429ee533d0877`.
- `pilot_v3_final_js_20261104_comparison.json`: igualdade integral das instâncias/repositórios e coorte, deltas zero em requisitos/issues.
- `pilot_v3_final_js_20261104_loader.json`: nova prova do loader no mesmo store após fechamento normal.
- `pilot_v3_final_js_20261104_coverage.json` e `_context_<repository_id>.json`: cobertura AST e rastreamento estático.
- `pilot_v3_final_js_20261104_before.json`: baseline atual dos 553 arquivos; checkpoints e `_summary.json` registram integridade posterior.

**Rodada anterior, preservada como histórico:**

- `pilot_v3_final_20261104_relatorio.md`: método, resultados, hashes e comandos da auditoria.
- `pilot_v3_final_20261104_f2_final_report.json`: snapshots, contagens, loader, integridade e custo.
- `pilot_v3_final_20261104_verification.json`: critérios e padrões de pendência.
- `pilot_v3_final_20261104_r3_output.jsonl`, `_r3_comparison_old.json`, `_r3_cohort.json` e `_r3_remaining_cohort.json`: saída e comparações.
- `pilot_v3_final_20261104_f2_approved_integrity.json`, `_f2_same_store_loader.json` e `_f2_same_store_pipeline.json`: blobs e prova do mesmo store.
- `pilot_v3_final_20261104_facts.json`, `_resolver.json` e `_blobcheck.json`: rastreamento dos três casos com NUL.

Os bloqueios anteriores do harness com NUL/pipes, uma tentativa de ler JSONC como JSON estrito e uma leitura com encoding Windows implícito foram corrigidos em novos scripts, preservando os relatórios anteriores. Não são apresentados como falhas do minerador. Entrega autorizada para commit pelo coordenador, sem push; o hash é consultável no histórico Git.

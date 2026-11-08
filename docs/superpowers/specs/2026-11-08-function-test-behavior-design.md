# Comportamento do par função–testes originais

## Decisão autorizada

Em 2026-11-08, o usuário aprovou explicitamente substituir o gate de recursos pela avaliação do **comportamento observado do par função–testes originais**. Quatro casos válidos são suficientes. A auditoria de recursos passa a diagnóstico opcional e não decide aprovação, rejeição ou pendência da instância. Este adendo substitui o requisito de recursos dos desenhos anteriores, inclusive o fechamento local.

## Contrato científico

Uma instância aprovada exige os quatro critérios:

1. `execution_prerequisites`: snapshot/ambiente reproduzível, escopo unitário original, instalação e preparação necessária comprovados.
2. `associated_tests_execution`: declarações associadas e hooks pertinentes concluídos, sem falhas inesperadas; falha inicial de retry continua sendo falha. Erro do instrumento não demonstra passagem.
3. `original_target_execution`: testemunhas verificáveis de declaração original, chamada direta e entrada na implementação original, sem substituir o alvo por mock.
4. `minimum_valid_cases`: pelo menos quatro identidades runtime distintas, executadas e aprovadas. Skip, todo, repetição de chamada e retry não fabricam casos.

Um limite inferior de quatro ou mais é suficiente. Observação global incompleta não apaga streams locais fechados. Contagem inferior a quatro só rejeita quando completa. Falhas alheias, inclusive de outra declaração no mesmo arquivo, não são herdadas; falhas associadas ou de hooks pertinentes mantêm o contrato local anterior.

O resultado afirma comportamento dos testes observados da função. Não afirma propriedades gerais do programa nem ausência de serviços reais.

## Formato e compatibilidade

Novas publicações usam `schema_version=3`, com política exata em `run_started`:

```json
{"version":2,"suite_passes":1,"minimum_valid_cases":4,"no_corrective_retries":true,"instance_execution_scope":"function_original_tests_behavior","resource_gate_scope":"diagnostic_only"}
```

O leitor confere esta política antes de interpretar as decisões. Exige versão uniforme e critérios coerentes com o formato. Os quatro critérios individuais continuam agregados por rejeição prevalente e aprovação somente quando todos aprovados. O repositório conserva os critérios de ambiente/escopo/instalação/preparação/suíte completa, sem `no_external_services` obrigatório.

As publicações v1/v2 mantêm seus critérios e políticas originais, incluindo `global_provisional` e `associated_closure_v1`. Não há migração implícita, troca de hash histórico ou interpretação da política nova em formato antigo. A API interna de avaliação mantém o padrão histórico `policy_version=1`; o coordenador/CLI atual seleciona explicitamente a política 2 por padrão. O protocolo de eventos permanece v1.

## Recursos e orquestração

O coordenador atual não chama `observe_resources`. Assim, helper ausente, exceção ou timeout da auditoria não entra no caminho de execução, avaliação ou publicação. Os produtores e modelos anteriores permanecem disponíveis para diagnóstico explícito e leitura histórica; nenhuma versão nova de modelos é exigida.

`resource_diagnostics` é informação compartilhada, fora de `criteria`. Diagnósticos herdados são rotulados `historical_not_recertified`. Seus estados approved/rejected/unknown não alteram a decisão científica. O leitor novo não chama o helper AST de recursos. Ele continua revalidando streams, identidades, metadados de contagem, chamadas, entrada original, transformações, retries, hooks, fases, ambiente e hashes das estruturas compartilhadas.

Receitas, comandos originais, configuração, serviços e escopo de execução permanecem como registrados; a mudança não amplia receitas para conseguir candidatas.

## Replay post-hoc

`validation_replay.replay_pair_records` reavalia **todas** as instâncias da publicação v2 salva. Usa fases, ambiente, associações originais, plano e eventos persistidos. Reconstrói critérios pelo avaliador e valida a publicação resultante pelo leitor portátil. Não altera status manualmente nem executa candidato, instalação ou Vitest.

A abertura nova registra hash/caminho/política/formato da origem, fechamento original e `candidate_execution=false`. O arquivo novo é separado do 04. Critérios de recursos e diagnósticos antigos ficam identificados como históricos, sem recertificação por modelos atuais. Impedimentos obrigatórios do replay são rederivados das evidências, em vez de herdar automaticamente erros antigos de auditoria.

Resultados medidos, hashes, artefatos, backup e comandos constam em [function-test-behavior-report.md](../function-test-behavior-report.md).

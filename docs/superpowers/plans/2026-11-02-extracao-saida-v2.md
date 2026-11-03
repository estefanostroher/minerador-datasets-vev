# Saída enxuta da extração — plano de implementação

> **For agentic workers:** usar superpowers:subagent-driven-development. Mesmo checkout e branch feature/minerator-pilot, sem worktrees, branches auxiliares ou commits automáticos. Subagente implementa; revisores independentes verificam aderência e depois qualidade.

**Goal:** Publicar a extração em JSONL v2 com contexto e metadados compartilhados, catálogo de motivos e diagnósticos agrupados, preservando unidades, decisões e evidências.

**Architecture:** Manter o protocolo interno do analisador e as regras científicas. Acrescentar um adaptador Python de publicação entre o gerador ordenado existente e o escritor JSONL. O coordenador mantém somente catálogo de motivos e estado do repositório corrente; o escritor aceita separadores compactos opcionais.

**Tech Stack:** Python >=3.11, geradores/biblioteca padrão, pytest existente e auxiliar TypeScript já instalado para integração estática.

## Base aprovada e escopo

Pesquisador aprovou a amostra de antes/depois de Camera.clearCinematic e autorizou implementação. Especificação geral de 2026-10-29 e desenho da extração continuam válidos, exceto pelo contrato de publicação substituído abaixo. Identificadores/código em inglês; docstrings e comentários em português em todas as funções. Testes novos somente para riscos concretos de perda de integridade, referências incorretas, decisões/contagens alteradas ou vazamento de recursos; não impor TDD/coverage. Parar trecho afetado em caso de ambiguidade metodológica e perguntar ao pesquisador.

Não alterar nem converter results/03_extracao.jsonl em uso, não iniciar coleta real, não mudar limites/análise/seleção/trivialidade/validação. Sem nova CLI de migração nesta tarefa. Formato antigo permanece histórico; novas execuções usam exclusivamente v2 e não anexam registros. Processos já em andamento usam módulos carregados anteriormente; não substituir o arquivo aberto.

## Contrato v2

### Versão e registros

`run_started.schema_version=2` identifica o formato de todo o arquivo; ausência desse campo identifica o legado v1. Manter UTF-8, um JSON por linha, ensure_ascii=True na extração e separadores compactos. Descoberta/triagem conservam a serialização atual. `run_finished.schema_version=2` também identifica o encerramento.

Tipos: diagnostic, repository, criterion_reason, instance. Uma origem por repository_id por execução, como já validado na entrada. Ordenação: início; repository; definições criterion_reason antes da primeira referência e instances na ordem anterior; diagnostics locais agrupados; próximo repository; fim. Catálogo é global à execução, com R001/R002/... atribuídos na primeira publicação determinística, independente da ordem de conclusão dos workers.

### Contexto e origem

Repository conserva repository_id/repository_url/commit_sha/extracted_at/analyzer/extraction_status/context/summary e recebe instance_defaults.validation com o mesmo objeto de validação não executada usado hoje. Não repetir URL/SHA/horário/validation nas instâncias quando idênticos ao repository/default; discrepâncias não devem ser silenciosamente descartadas.

Instance conserva repository_id, instance_id, decision, function (incluindo reference_code e assinatura), documentation e tests integrais. IDs são calculados antes da publicação e permanecem iguais. Quando context_paths corresponde exatamente aos caminhos ordenados de repository.context.files, substituí-lo por `context_ref="repository"`. Caminhos de contexto distintos ou com ordem diferente devem ser preservados explicitamente, sem declarar que um subconjunto equivale ao contexto completo. Não substituir contexto real por dependências presumidas.

### Motivos e expansão

Para cada texto distinto, publicar `{"record_type":"criterion_reason","reason_id":"R001","text":"..."}` antes da primeira referência. Criteria conserva cada chave/status e substitui reason por reason_ref. Preservar outros campos eventualmente existentes. Remover reasons somente quando for exatamente a lista ordenada de razões dos critérios não approved; preservar razões adicionais ou ordem distinta para não perder informação. Validation diferente do default deve ser preservada na instância.

Criar helper público de expansão de instância a partir do repository e catálogo: reconstruir origem/context_paths/criteria.reason/validation/reasons legados sem mudar valores, incluindo strings com surrogates. Aceitar instância legada sem modificá-la indevidamente. Referência ausente, duplicada/divergente ou repository_id incompatível deve gerar erro explícito, nunca completar com dados inventados. A expansão permite às próximas etapas consumirem a representação completa; não implementar a validação nesta tarefa.

### Issues e diagnósticos

Agrupar issues do mesmo repositório com mesmo payload exceto path: code/message e quaisquer detalhes operacionais diferentes (http_status/rate_limit_headers/outros campos) não podem ser mesclados. Distinguir issue global sem path de issue por arquivo. Cada grupo tem issue_id I001/... escopado ao repositório, affected_count e affected_paths completos quando aplicável; preservar ordem determinística. Contar ocorrências, não apenas quantidade de caminhos únicos quando houver repetição.

Em vez de copiar mensagem e caminhos novamente, publicar um diagnostic repository_error por grupo com repository_id/issue_ref/affected_count e observed_at. Se as ocorrências tiverem horários diferentes, registrar o intervalo com last_observed_at. Diagnósticos operacionais sem issue correspondente, especialmente erro tardio de fechamento de cliente, mantêm seus detalhes e devem continuar visíveis. Não descartar falhas nem reduzir o estado partial/failed por haver menos linhas.

No fim, `error_count` passa a contar diagnósticos repository_error publicados (grupos + falhas sem issue). Acrescentar `issue_count` com o número de ocorrências originais representadas, incluindo falhas de fechamento. Contagens de repositórios/instâncias/decisões e status permanecem iguais às antigas. Registrar a nova semântica na documentação. Catálogo e diagnósticos não contam como instâncias.

## Arquivos e tarefas

### 1. Adaptador e integração

**Criar:** src/robo_minerador/extraction_format.py, tests/test_extraction_format.py.
**Alterar:** src/robo_minerador/extraction.py, jsonl.py, cli.py, tests/test_extraction.py e somente os demais testes de extração afetados pelo contrato.

- [x] Implementar compact_records(records) como gerador que consome o fluxo legado interno e publica o contrato v2. Validar referências/origens relevantes sem reavaliar elegibilidade. Manter memória limitada ao repositório corrente e catálogo.
- [x] Implementar expand_instance(instance, repository, reasons) com reconstrução exata dos valores compartilhados e erros explícitos de vínculo inválido; não expor leitor que silenciosamente ignore schema desconhecido.
- [x] Agrupar issues com chave JSON canônica excluindo path, mas distinguindo a presença do campo. Usar issue_id local e mapear diagnósticos legados ao grupo correto. Fazer flush antes do próximo repository/fim; não fabricar run_finished quando a origem foi interrompida.
- [x] Envolver o gerador público extract_repositories com o adaptador, mantendo _extract_candidate/protocolo Node internos como v1. Fechamento explícito do adaptador fecha origem e pool mesmo em erro de escrita/KeyboardInterrupt, sem carregar toda a execução.
- [x] Acrescentar `compact=False` keyword-only ao escritor; quando True, usar separators=(',', ':'). Manter defaults atuais e chamar compact=True somente para extract. Mocks de escrita atualizados.
- [x] Testes críticos: comparação roundtrip de instâncias v1/v2 com IDs/código/docs/testes/estados iguais; contexto completo vs subconjunto/ordem; catálogo entre repositórios e motivos repetidos; defaults/razões extras/surrogate; agrupamento sem perda de caminhos/detalhes e fechamento tardio; flush em truncamento sem falso fim; cancelamento/erro do consumidor; compatibilidade com descoberta/triagem.
- [x] Executar os cinco arquivos de testes de extração: 105 casos aprovados pelo implementador; suíte completa verificada também pelo coordenador, conforme evidências abaixo. Sem candidatos executados ou rede.

### 2. Revisões e evidências

**Alterar pelo coordenador:** README.md, desenho da extração (adendo v2) e este plano.

- [x] Revisão de aderência independente aprovada após correção do flush em exceção ordinária do produtor; revisão de qualidade independente aprovada.
- [x] Verificação do coordenador: suíte Python completa, ajuda de extract e git diff --check. Node não modificado; integração real com Node coberta pelos testes existentes.
- [x] Inspecionar fixture escrita pela CLI/analisador reais em diretório temporário, com contexto antes da instância, motivos previamente definidos e expansão fiel.
- Cancelado por mudança de contexto do pesquisador: reprocessamento do prefixo real. O pesquisador informou ter interrompido a execução e excluído 03_extracao.jsonl antes desta verificação. Substituído pela medição sintética abaixo, sem recriar dados do pesquisador ou fazer nova coleta.
- [x] Documentar versão, referências, contadores e evidências no README e adendo ao desenho. Arquivo legado não alterado pelo implementador; exclusão feita pelo pesquisador. Sem commit automático.

## Evidência inicial

Antes da mudança, análise somente leitura dos primeiros 421404552 bytes encontrou 31985 registros: 17 repository, 13671 instance e 18297 diagnostic. context_paths ocupava 316182477 bytes; todas as listas eram idênticas aos caminhos ordenados dos arquivos do repository correspondente. As 13671 listas reasons também repetiam exatamente os critérios. Havia 12 textos distintos em 68355 ocorrências de razões. 18296 diagnósticos repository_error repetiam issues do repository, incluindo 11509 content_budget. Prefixo sem run_finished, portanto execução parcial em andamento. Medições finais serão adicionadas após a implementação.

## Verificações executadas

- Coordenador: `.\.venv\Scripts\python.exe -m pytest -q` → **321 aprovados**, sem skips, 5,27 s. Ajuda de extract retornou 0; `git diff --check` sem erros (somente avisos de finais de linha do Git).
- `.\.venv\Scripts\python.exe -m pytest tests/test_extraction.py::test_cli_real_jsonl_and_origin --basetemp C:/Users/Estefano/AppData/Local/Temp/opencode/extracao-v2-inspecao -q` → **2 aprovados**. Inspeção direta do JSONL: schema_version=2, repository com arquivos intactos e defaults de validação, cinco definições de motivos antes da instância, context_ref e reason_ref corretos, fim completed com error_count=issue_count=0. Helper Node real analisou fontes de fixture, sem executar candidatos; uma variante preservou surrogate isolado.
- Medição sintética com publicador e escritor reais: **512 arquivos, 512 instâncias e 1000 issues por caminho**. V1 **7123499 bytes / 1515 registros**; v2 **593485 bytes / 521 registros**; redução **91,6686%**. As 512 instâncias expandidas foram integralmente iguais às originais; SHA-256 canônico dos valores: `e60c6e80ed91a2d87548b43da12cbc649e442c6fd78f4e83d558bd61e6874bf3`. Todos os mil caminhos foram preservados; resumo error_count=1 e issue_count=1000. Somente arquivos temporários fora do workspace foram escritos.
- Essa medição é fixture de repetição estrutural, não dado minerado ou projeção da coleta. A simulação inicial no prefixo real apontava cerca de 89,3 MB antes de agrupar diagnósticos, mas não foi possível conferir o publicador final contra esse prefixo após a exclusão pelo pesquisador.
- Revisão de aderência corrigiu perda de diagnósticos pendentes quando next(source) lança exceção ordinária. O adaptador publica esses diagnósticos e relança a mesma exceção; não tenta publicar durante close/GeneratorExit/KeyboardInterrupt ou throw do consumidor. Re-revisão focada aprovada; revisão de qualidade verificou referências, contagens, memória e limpeza, sem achados remanescentes.

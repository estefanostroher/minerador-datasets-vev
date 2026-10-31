# Concorrência na descoberta e na triagem

**Objetivo:** reduzir a espera de rede com threads limitadas, preservando seleção, ordem, SHA, decisões e uma única saída JSONL por etapa.

**Aprovação:** o pesquisador solicitou implementar threads/workers nas duas etapas após discutir a recuperação paralela de SHAs e a inspeção paralela de candidatos. Solicitou antes um commit exclusivo da triagem; realizado em `f7dd5d1`. Após a implementação e as verificações, autorizou explicitamente um commit separado da paralelização.

## Decisões e limites

- Checkout atual, branch `feature/minerator-pilot`, sem worktrees/branches auxiliares. Código inglês; comentários/docstrings em português em todas as funções e métodos. Subagentes com arquivos atribuídos, revisão de aderência e qualidade. Ambiguidade metodológica exige consulta ao pesquisador.
- `--workers` nas duas CLIs, padrão 4, inteiro entre 1 e 16. `--workers 1` mantém caminho sequencial. Validar antes de qualquer consulta ou substituição de saída.
- Manter transporte API e conteúdos em memória. Não introduzir download compactado, cache persistente ou execução de código dos candidatos.
- Descoberta: busca/paginação/particionamento permanecem sequenciais e ordenados. Selecionar IDs únicos no coordenador antes de submeter SHAs, no máximo os que faltam para o limite. Resolver uma página em paralelo e consolidar na ordem da resposta. Duplicatas preservam a primeira resolução e consultas de origem. SHAs observados em momentos diferentes continuam explicitamente observações de uma coleta, não snapshot global.
- Triagem: um candidato inteiro por tarefa; chamadas daquele candidato usam o mesmo cliente. Consolidar na ordem de entrada; somente o coordenador emite registros, contadores, progresso e escrita. Pendências, falhas esperadas e diagnósticos mantêm a semântica atual. SHA ausente não consulta a API.
- Usar `ThreadPoolExecutor` e janela limitada de futures; não submeter todos os candidatos. Clientes PyGithub separados por thread, com conexões reutilizadas e fechamento garantido. Nunca compartilhar requester/sessão entre threads.
- Um controle de pausa por execução é compartilhado entre busca e workers. O retry do PyGithub continua limitado e identifica rate limits; a pausa reconhecida em um worker bloqueia novas chamadas nos demais. Respeitar `Retry-After` e reset primário; HTTP 403 sem sinais de rate limit não ativa pausa global. Não rotacionar tokens nem prometer 1000 candidatos em dez minutos.
- `run_started` registra `workers`. Uma tarefa mais lenta pode atrasar a publicação das seguintes para preservar a ordem; memória e trabalho em voo permanecem limitados.

## Tarefas e arquivos

### 1. Infraestrutura de workers e limite de API

Criar `src/robo_minerador/concurrency.py`; alterar `github_api.py`; criar `tests/test_concurrency.py`.

- [x] Implementar mapa ordenado com janela limitada, cancelamento de pendentes, propagação de erros internos e encerramento das threads/clientes.
- [x] Implementar clientes por thread e pausa compartilhada, integrada às chamadas e ao retry da biblioteca sem logs de credenciais.
- [x] Testes estritamente necessários: tarefas realmente sobrepostas por eventos, ordem independente de conclusão, janela limitada, fechamento/cancelamento e rate limit compartilhado reconhecido. Não depender de rede nem de comparação de tempo frágil.

### 2. Descoberta concorrente

Alterar `discovery.py`; criar `tests/test_discovery_concurrency.py`.

- [x] Injetar executor/resolvedor opcional mantendo compatibilidade das funções existentes e testes sequenciais.
- [x] Separar seleção/deduplicação da resolução e consolidar SHAs/diagnósticos na ordem original.
- [x] Verificar limite, primeira observação, fallback/incompletude preservados, falha de um SHA sem perda dos demais e propagação de defeito interno.

### 3. Triagem e CLI

Alterar `triage.py`, `cli.py`, `config.py`; criar `tests/test_triage_concurrency.py`.

- [x] Integrar tarefas por candidato com janela limitada, cliente por worker e ordem estável. Sem duplicar início/fim em falhas de pool.
- [x] Adicionar `--workers` às duas etapas, default 4/max16 e fallback sequencial 1. Registrar nos metadados e preservar validação antes da escrita.
- [x] Verificar resultados/contagens idênticos em execução fora de ordem, SHA ausente, falhas esperadas, fechamento após erro e argumentos inválidos preservando saídas.

### 4. Revisão, documentação e verificação

Atualizar `README.md`, especificação geral e este plano.

- [x] Revisão por subagentes: aderência e depois qualidade; coordenador inspeciona diffs.
- [x] Rodar `.\.venv\Scripts\python.exe -m pytest -q`, ajuda de ambos subcomandos e `git diff --check`.
- [x] Informar comandos com `--workers`, limites e alcance da verificação. Preservar resultados locais existentes; testes simulados demonstram concorrência/integridade, não desempenho real nem coleta final dos 1000 candidatos.

## Evidências e encerramento

- Implementação por subagentes com arquivos atribuídos; revisões de aderência e qualidade aprovadas após correções de propagação de defeitos em futures não consumidos, publicação da pausa na última tentativa de retry e aviso de pausa iniciada após resposta bem-sucedida com cota zero.
- Verificação final do coordenador: **214 testes aprovados em 0,81 s**, incluindo os 154 testes anteriores. Relógios simulados, eventos e barreiras demonstram sobreposição, ordem e pausas sem chamadas reais à API nem testes baseados em comparação frágil de duração.
- Ajuda de `discover` e `triage` confirma `--workers`, padrão 4 e faixa 1–16. `git diff --check` sem erros de whitespace; somente avisos locais de conversão LF/CRLF.
- A integração do retry foi inspecionada na versão instalada PyGithub 2.10.0. Os retries continuam limitados; concorrência não aumenta a cota nem garante prazo de execução.
- README e especificação geral atualizados. Resultados locais preservados; nenhuma nova coleta real ou benchmark de rede executado nesta mudança. O commit de concorrência foi autorizado separadamente do commit de triagem `f7dd5d1`; nova verificação antes do commit confirmou 214 testes aprovados e diff sem erros de whitespace.

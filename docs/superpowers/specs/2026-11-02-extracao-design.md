# Extração estática concorrente

## Decisões aprovadas

Python coordena a recuperação de snapshots e um auxiliar Node.js com TypeScript Compiler API identifica funções declaradas e métodos de classe. A extração não instala dependências, compila nem executa código dos candidatos. A execução da suíte completa uma vez e a exigência de quatro casos válidos pertencem à validação.

O comando `extract --workers N --overwrite` usa os mesmos limites das etapas anteriores: padrão 4, intervalo 1–16. Cada tarefa processa um repositório inteiro; somente o coordenador publica registros, na ordem de entrada. Reutilizar WorkerPool e RateLimitGate, sem paralelismo interno de requisições. No máximo um processo Node por tarefa em voo.

## Entrada e proveniência

Ler `results/02_triagem.jsonl`, validar UTF-8/JSON estrito, início/fim, contagens, IDs únicos, URLs, SHAs e coerência das decisões com os critérios antes de rede/escrita. Processar somente aprovados. Preservar hash SHA-256 da entrada e seu estado parcial, sem transformar pendências anteriores em candidatos aprovados. Os metadados integrais da descoberta e triagem não são repetidos.

## Análise e limites

Recuperar árvore e blobs no SHA registrado, reutilizando as verificações de integridade e limites do Snapshot. Recuperar arquivos-fonte, testes, documentação textual, manifests, configurações JSON e contexto local disponível; não presumir completude ao atingir limites. Preservar textos originais e hashes. Links, arquivos não textuais necessários e referências não resolvidas exigem pendência explícita, não reprovação por ausência. Sem executar tsconfig, configurações de framework ou scripts. Conteúdos candidatos ficam em memória.

O auxiliar utiliza um sistema de arquivos virtual; resolver imports locais, aliases e reexports com o compilador. Identificar testes por APIs de frameworks reconhecidos e evidência de imports/manifests, sem aceitar qualquer função chamada test/it como teste. Associar apenas chamadas resolvidas dentro do callback de um teste ao símbolo original da função/método. Chamadas em hooks, helpers, callbacks diferidos e testes ignorados não são evidência de caso executado; preservar o contexto sem contar execuções. Tipos any, alvos ambíguos, mocks e despacho polimórfico não comprovam chamada à implementação original. Funções expressão, arrows, construtores e accessors ficam fora da unidade aprovada; não ampliar o escopo silenciosamente.

Documentação exige descrição original do comportamento (inclusive tags descritivas); assinatura repetida, nomes e tags apenas tipadas não bastam. Aceitar comentários ligados à declaração e documentação externa explicitamente associável por caminho/símbolo/link, preservando o trecho original. Ambiguidade de associação fica pendente. Não impor completude ou qualidade mínima.

H-F06 rejeita apenas formas comprovadas: retorno constante independente de entradas, acessor simples e delegação direta sem lógica adicional. Não usar um limiar numérico de complexidade. Casos não comprováveis permanecem pendentes. A resolução estática de contexto local não aprova ausência de serviços externos nem executabilidade.

## Contrato de saída v1 (histórico)

Única saída: `results/03_extracao.jsonl`, UTF-8, um objeto por linha. Recusar arquivo existente sem `--overwrite`; entrada/configuração inválida preserva saída mesmo com essa opção. Sem retomada/anexação.

- `diagnostic`: `stage=extraction`, `event`, mensagem em português e instante UTC. Início contém input_path/input_sha256/input_status/input_count/eligible_count/workers/strategy. Falhas operacionais sanitizadas ficam no mesmo arquivo. Fim registra contagens de repositórios e instâncias, erros e estado.
- `repository`: IDs, URL, SHA, extraction_status (completed/partial/failed), versão do analisador, context.files (path/content/sha256), dependências locais e referências não resolvidas, issues e summary (instance_count/approved/rejected/not_evaluated). Sempre emitir um registro por candidato processado, inclusive sem instâncias.
- `instance`: IDs e SHA, instance_id estável calculado por hash da identidade/origem/posição, decision, function (kind/name/class_name/path/start_line/end_line/signature/reference_code), documentation, tests (posição, framework, nome, parametrização e evidência direct_call/resolved_target), context_paths, criteria, validation e reasons. Critérios: identifiable_implementation, associated_documentation, direct_test_association, nontrivial_implementation, preserved_local_context. Estados approved/rejected/not_evaluated; qualquer rejeição determina rejected, todos aprovados permitem approved, demais ficam not_evaluated. validation permanece status=not_evaluated, executed_test_cases=null, suite_passed=null, external_services=not_evaluated.

O código de referência e a documentação ficam na instância; arquivos completos compartilhados ficam no registro repository, vinculados por ID/SHA/caminho. Ordem por repositório: contexto primeiro, instâncias em ordem de caminho/posição, diagnósticos locais. A construção final consolidará instâncias integrais. Aprovação aqui significa encaminhamento à validação, não instância final.

Estado completed significa processamento da entrada elegível sem limitações operacionais/análises pendentes; rejeições científicas não causam partial. Partial sinaliza pendência/erro com processamento útil; failed sinaliza impossibilidade total de análise. Entrada partial permanece registrada, não implica cobertura dos candidatos ausentes. Nenhum elegível é uma execução completed vazia. Códigos de saída: 0 completed, 1 partial/failed/escrita, 2 configuração/entrada inválida/saída existente.

## Adendo aprovado — publicação v2

Novas execuções seguem o [contrato enxuto v2](../plans/2026-11-02-extracao-saida-v2.md), que substitui apenas a publicação v1 descrita acima. Não altera protocolo interno Node, critérios científicos, instâncias selecionadas ou IDs.

- Início e fim declaram schema_version=2. Arquivo sem versão é legado v1; não anexar nem misturar versões.
- URL/SHA/horário e instance_defaults.validation ficam no repository. Instance conserva repository_id/instance_id/decision/function/documentation/tests; context_ref=repository substitui somente listas idênticas aos caminhos completos ordenados do contexto. Subconjuntos/ordens diferentes e valores de validação distintos permanecem explícitos.
- criterion_reason define reason_id/text antes da primeira referência; criteria conserva status e usa reason_ref. reasons é omitido somente se for reconstruível exatamente dos critérios.
- Issues são agrupadas por payload integral exceto path, distinguindo globais de arquivos e preservando todas as ocorrências/caminhos/detalhes. Diagnostics locais usam issue_ref e contagem, com intervalo de observação quando necessário. Falhas sem issue correspondente mantêm seus detalhes.
- error_count conta diagnósticos publicados; issue_count conta ocorrências originais representadas. Estados e contagens científicas permanecem iguais.
- expand_instance recompõe os valores completos a partir do repository e catálogo, sem inventar referências. Escrita v2 tem separadores compactos; descoberta e triagem mantêm seus defaults.

O pesquisador interrompeu a coleta v1 e excluiu results/03_extracao.jsonl durante a implementação deste adendo. Não foi feita conversão ou nova coleta; a próxima execução produzirá v2 do início ao fim.

## Desenvolvimento e verificação

Trabalhar no checkout atual, branch feature/minerator-pilot, sem branches, worktrees ou commits automáticos. Subagentes recebem escopo de arquivos; coordenador revisa aderência e qualidade. Código e identificadores em inglês; comentários/docstrings de todas as funções em português. Parar o trecho afetado diante de ambiguidade metodológica e perguntar ao pesquisador.

Testes somente para riscos concretos de seleção, associação ou integridade: homônimos/shadowing, aliases/reexports, métodos, indireção, mocks, documentação, trivialidade, parametrizações sem contagem executada, entrada inconsistente e ordem/limite de concorrência. Não impor TDD ou meta de cobertura. Executar suítes existentes e inspecionar JSONL real ou de fixture; preservar resultados anteriores do pesquisador.

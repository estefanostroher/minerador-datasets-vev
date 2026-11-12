# Deduplicação exata de instâncias aprovadas

## 1. Decisões e fontes

Desenho aprovado pelo pesquisador em 2026-11-12. Fontes: `Heurísticas_V6.md` (H-D01–H-D05), rascunho do artigo em Downloads e especificação incremental de 2026-10-29. A etapa implementa apenas deduplicação exata; não calcula similaridade estrutural/semântica nem produz o dataset final.

Decisões expressas:

- Comparar exclusivamente registros `record_type="instance"` com `decision="approved"` de `results/04_validacao.jsonl`. A decisão agregada do repositório não substitui esse filtro.
- Comparar implementação, documentação, testes e contexto integral publicado para a instância na extração, incluindo caminhos relativos e conteúdos exatos.
- Não comparar o ambiente registrado na validação. Conservar esse ambiente como proveniência por origem, sem combinar ambientes ou provas. Arquivos de configuração presentes no contexto continuam sendo comparados como quaisquer outros arquivos.
- Não normalizar textos, nomes, espaços, comentários ou quebras de linha. Não remover semelhanças apenas estruturais ou semânticas.
- Representante: menor `instance_id` lexicográfico de cada grupo de igualdade integral. A ordem de entrada não altera a escolha.
- Um registro de saída para cada instância elegível, incluindo duplicatas; rejeitar uma duplicata no 05 não modifica sua aprovação histórica no 04.

## 2. Entrada e integridade

Entrada principal: `config.DEDUPLICATION_INPUT_PATH`, inicialmente `results/04_validacao.jsonl`. Fonte de artefatos: `config.DEDUPLICATION_EXTRACTION_PATH`, inicialmente `results/03_extracao.jsonl`. Saída: `config.DEDUPLICATION_OUTPUT_PATH`, inicialmente `results/05_deduplicacao.jsonl`.

Primeira versão operacional: extração v3 e validação v3 sob a política atual de comportamento função–testes. Formatos históricos de validação não são reinterpretados automaticamente.

Ler JSON estrito: recusar chaves repetidas, constantes não finitas, registros desconhecidos, execuções sem abertura/fechamento, versões misturadas e contagens/referências incoerentes. Reutilizar `validation_format.validate_output_records` para conferir a publicação da validação e `validation_input.load_extraction` para a extração v3.

Conferir o SHA-256 dos bytes integrais do 03 contra `run_started.source.sha256` do 04. Confrontar também os metadados da população de origem publicados no 04 com os obtidos pelo leitor do 03. Não exigir igualdade textual dos caminhos de entrada: os bytes e os vínculos verificam a identidade, permitindo mover um checkout.

Todas as instâncias presentes na validação devem corresponder às candidatas aprovadas da extração: IDs, repositório, URL e commit. Conferir também as associações publicadas quando presentes no 04. Pendências legítimas da validação podem não ter contexto carregado, associações ou plano de execução; não exigir que esses artefatos existam para instâncias não aprovadas. Antes de comparar uma instância aprovada, exigir e conferir alvo e associações do 04 contra os artefatos expandidos do 03. Ausência obrigatória/divergência é erro de integridade, nunca reprovação científica ou descarte silencioso.

Execuções de origem encerradas como `partial` são aceitas, com estado registrado. Selecionar somente as aprovadas observadas, sem reavaliar ou recuperar pendentes/rejeitadas. Zero elegíveis é uma execução válida. Validar ambas as entradas mesmo nesse caso.

Sem rede, recuperação de snapshots, Docker, instalação ou execução de candidatos. A etapa verifica publicações e suas provas portáteis; não afirma nova verificação dos objetos Git. Após revisão da T2, o pesquisador autorizou usar a TypeScript Compiler API confiável para conferir exclusivamente os vínculos sintáticos dos pontos creditados com as expressões originais. Havendo instâncias aprovadas, exigir Node >=22 e TypeScript 6.0.3 antes da publicação; zero aprovadas dispensa esse auxiliar. Não importar módulos candidatos, recalcular elegibilidade ou usar regex para aproximar a sintaxe. A representação comparada permanece a mesma.

## 3. Representação comparada: `exact_instance_context_v1`

Construir objeto JSON com versão de representação e os seguintes componentes:

1. `function`: `kind`, `name`, `class_name`, `path`, `signature`, `reference_code`.
2. `documentation`: cada item com `kind`, `path`, `original_text` e `association` original. Excluir somente `start_line` e `end_line`.
3. `tests`: registros de associações diretas da extração, incluindo `path`, `framework`, `name`, `parameterized`, `modifiers`, `skipped` quando presente, trecho original das linhas `start_line` a `end_line` e associação (`kind`, `expression`, alvo com `path` e `name`). Não incluir `call_line`, `start_line` ou `end_line` em nenhum vínculo. Não selecionar apenas quatro casos nem somente testemunhas creditadas: preservar todos os testes associados publicados para a instância.
4. `context_files`: todos os arquivos referenciados pelo `context_paths` expandido, cada um com `path` e `content` integral. `context_ref="repository"` é expandido pelo helper existente, sem presumir que seu conjunto seja mínimo.

Os trechos de testes são extraídos preservando os terminadores originais, com a mesma semântica de intervalo inclusivo por linhas usada pela extração. Se vários vínculos diretos apontarem para a mesma declaração, preservar cada associação publicada; não perder multiplicidade.

Arquivos são ordenados por caminho. Documentação e associações de testes são ordenadas pelos bytes da serialização canônica de cada item, preservando multiplicidade. Modificadores são preservados na ordem publicada. Objetos JSON usam chaves ordenadas, separadores compactos, `ensure_ascii=True` e `allow_nan=False`; codificar em UTF-8. Isso preserva inclusive strings com surrogates isolados sem inventar normalização Unicode.

Excluir da representação IDs de instância/repositório, URL, commit, horários, posições de localização, critérios, razões, métricas, provas de casos, logs e ambiente de validação. Os arquivos de contexto podem conter esses valores como texto original: não editar seu conteúdo para removê-los.

Calcular SHA-256 e quantidade de bytes dessa representação. O hash é um índice: dentro de cada bucket, exigir igualdade dos bytes completos. Uma colisão de hash não autoriza agrupar representações diferentes. O vínculo do grupo é `representative_instance_id`, não o hash isolado.

## 4. Formato da saída v1

Todos os registros contêm `schema_version=1`, `stage="deduplication"` e `timestamp` UTC. JSONL UTF-8 compacto com escapes ASCII, consistente com as publicações recentes. Ordem: abertura, instâncias em ordem lexicográfica de `instance_id`, fechamento.

### `record_type="run_started"`

- `sources.extraction` e `sources.validation`: caminho efetivamente lido, SHA-256, versão e estado de cada entrada.
- `policy`: versão/nome da representação, seleção `validation_instance_approved`, ambiente fora da comparação, textos exatos, contexto integral, confirmação integral após hash e regra de representante.
- `input_instance_count`: quantidade de instâncias no 04, em todos os estados.
- `eligible_count`: quantidade de aprovadas selecionadas no 04.
- `input_decisions`: contagens de approved/rejected/not_evaluated no 04.

### `record_type="instance"`

- `instance_id`, `repository_id`, `repository_url`, `commit_sha`: identidade da origem; preservados do vínculo conferido entre 03 e 04.
- `decision`: `approved` para representante; `rejected` para duplicata exata.
- `disposition`: `representative` ou `duplicate`.
- `representative_instance_id`: próprio ID para representante, ID da representante para duplicata.
- `representation_sha256`: digest hexadecimal SHA-256.
- `representation_byte_count`: comprimento da representação em bytes.
- `criteria.exact_uniqueness`: `status`, `code` e `message` em português.
  - Representante: `approved` / `EXACT_REPRESENTATIVE`.
  - Duplicata: `rejected` / `EXACT_DUPLICATE`.

Cada origem fica em um registro próprio. O conjunto de registros com o mesmo `representative_instance_id` preserva todas as origens do grupo. Os IDs e os hashes das entradas permitem recuperar código, documentação, testes, caminhos/linhas, licença e ambiente nas etapas anteriores. Não copiar todos esses artefatos para o 05 nem sintetizar união de testes/ambientes.

### `record_type="run_finished"`

- Hashes de ambas as entradas, `eligible_count`, `representative_count`, `duplicate_count`, `duplicate_group_count`, contagens de decisões e `status="completed"`.
- `duplicate_group_count` conta grupos com pelo menos dois membros, não todos os representantes.
- `eligible_count = representative_count + duplicate_count`; cada elegível aparece exatamente uma vez; todos os representantes existem e apontam para si.

Não encontrar duplicatas: todas as elegíveis são representantes aprovadas; `duplicate_count=duplicate_group_count=0`. Não encontrar elegíveis: somente abertura/fechamento, contagens zeradas e estado `completed`. Origens `partial` permanecem declaradas na proveniência, sem tornar automaticamente esta execução parcial.

## 5. CLI e erros

`python -m robo_minerador deduplicate` aceita somente `--overwrite` e `--help`, sem limite, workers ou seleção adicional. A saída existente exige autorização explícita por `--overwrite`.

Validar caminhos, entradas e construir/verificar resultados antes de abrir a saída. Recusar sobreposição da saída com qualquer entrada, diretório como arquivo ou pais incompatíveis. Configuração/entrada inválida: código 2 e preservar saída existente, mesmo com overwrite. Conclusão válida: código 0. Falha de escrita ou cancelamento: código 1; conferir fechamento, pois o escritor existente não garante publicação atômica. Não publicar arquivo alternativo.

Fornecer leitor/validador do 05 para a futura construção, verificando protocolo, política, contagens, origem e vínculos/representante mínimo. Não alegar que apenas o hash de saída comprova igualdade sem acesso aos artefatos de origem: o produtor confirma os bytes, e o consumidor pode reproduzir a representação com as entradas identificadas.

## 6. Implementação e revisão

Trabalhar no checkout atual e branch `feature/minerator-pilot`, sem worktree ou branch auxiliar. Por instrução posterior do pesquisador, usar exclusivamente subagentes do tipo `general` para implementação e revisão, com atribuições delimitadas e revisão do coordenador. O pesquisador identifica esse agente como configurado com `gpt-6.1-terra`; a ferramenta de delegação permite selecionar o tipo, mas não confirmar ou substituir seu modelo. Ao final de cada tarefa, um `general` independente revisa aderência e qualidade antes de liberar a próxima tarefa dependente. Correções exigem nova revisão do trecho afetado. Código e identificadores em inglês; comentários e docstrings em português; toda função/método novo deve ter docstring explicativa.

Criar testes somente quando necessários para riscos concretos de seleção incorreta, falsa deduplicação ou perda de integridade; justificar no plano. Não impor TDD obrigatório, testes por função nem meta de cobertura. Executar testes pertinentes existentes e verificar a saída real. Preservar arquivos 01–04 e cache. Não executar novas validações. O pesquisador autorizou um único commit ao final de toda a implementação, depois de concluir tarefas, reviews, correções e verificações; não fazer commits intermediários de documentação ou código, nem push. Esta autorização não antecipa um commit na entrega apenas do plano. Interromper o trecho afetado e perguntar ao pesquisador se surgir ambiguidade metodológica.

## 7. Critérios de conclusão

- CLI operacional; entradas e saída com contratos verificáveis; documentação de uso e campos.
- Provas de que pendentes/rejeitadas do 04 não participam da comparação, mesmo quando seus artefatos coincidem com aprovadas.
- Provas de igualdade exata, contexto/caminho/texto divergentes, multiplicidade de associações, independência da ordem, colisão simulada de hash e preservação de origens.
- Provas de recusa de entrada corrompida, fonte 03 divergente, referências/contagens inválidas e proteção de overwrite.
- Execução real sobre 03/04 atuais, quando íntegros, com inspeção de elegíveis, grupos e fechamento; registrar somente resultados efetivamente observados.

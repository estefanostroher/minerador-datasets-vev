# Construção do dataset de funções TypeScript

## 1. Objetivo e decisões aprovadas

Desenho validado em conversa com o pesquisador em 2026-11-14. Esta entrega especifica a etapa final; sua implementação será objeto de um plano posterior à revisão deste documento.

Produzir `results/06_dataset.jsonl`, com uma linha completa por instância aprovada e mantida como representante na deduplicação. Cada linha deve permitir consumir os artefatos e interpretar as evidências da instância sem resolver referências a outras linhas ou aos arquivos intermediários.

Decisões expressas:

- Preservar função ou método TypeScript original, testes diretamente associados, documentação original, contexto textual, métricas, origem e ambiente.
- Não gerar enunciado, prompt ou paráfrase da documentação. Não incluir campos `text`, `prompt` ou `specification`.
- Seguir a elegibilidade atual por instância, sem impor aprovação global do repositório ou repetir validações.
- Incluir todos os representantes elegíveis, sem limite por repositório.
- Calcular tamanho e complexidade ciclomática por análise estática; reutilizar contagens e cobertura da validação.
- Não calcular Mutation Score nem classificar categorias comportamentais dos testes.
- Não incorporar binários, inclusive em Base64. Preservar as referências disponíveis aos recursos não incorporados.
- Gerar somente o dataset, sem relatório, estatísticas persistidas ou arquivo de erros separado. Contagens e erros operacionais aparecem no terminal.
- Repetir contexto compartilhado em cada instância. O formato normalizado por repositório e o índice dependente de arquivos intermediários foram considerados e descartados em favor do registro completo.

“Completo” significa completo quanto aos artefatos textuais e evidências especificados aqui. Não significa projeto executável sem recuperar dependências npm, binários ou outros recursos externos identificados. A implementação de referência estará presente no dataset; cabe ao experimento futuro controlar sua exposição à LLM.

## 2. Fontes e precedência metodológica

Fontes consultadas: `Heurísticas_V6.md`, artigo `Tema_4_VeV___Building_a_Dataset_for_Test_Driven_Development_Oriented_Code_Generation_with_Large_Language_Models (1).pdf` e `mbpp_primeiros_10_sem_resposta.json`, em Downloads; contratos e código atuais das etapas anteriores.

As decisões expressas nesta conversa prevalecem nos pontos divergentes: sem enunciado, mutação ou categorias comportamentais; aprovação local da instância em vez de exclusão integral por falha global. A tabela operacional antiga da V6 ainda menciona mutação; isso não a inclui nesta etapa.

Mapeamento da Tabela 2 do artigo:

| Artigo | Dataset |
| --- | --- |
| ID / Language | `instance_id` / `language` |
| Function / Reference Code | `function`, incluindo `reference_code` |
| Specification | Sem enunciado separado; documentação original em `documentation` |
| Tests / Test Framework | `tests`, incluindo framework por associação |
| Coverage / Complexity | `metrics` |
| Mutation Score | Ausente |
| Source / Version / License | `source`, incluindo commit e licença |

`context`, `environment`, `validation`, `deduplication` e `provenance` detalham reprodução e rastreabilidade. `schema_version` identifica o formato, não a versão do código minerado.

## 3. Entradas e cadeia de integridade

Adicionar constantes `DATASET_DISCOVERY_PATH`, `DATASET_TRIAGE_PATH`, `DATASET_EXTRACTION_PATH`, `DATASET_VALIDATION_PATH`, `DATASET_DEDUPLICATION_PATH` e `DATASET_OUTPUT_PATH` em `config.py`, inicialmente apontando para `results/01_descoberta.jsonl` até `results/06_dataset.jsonl`.

Primeira versão: descoberta e triagem sob seus contratos atuais, extração v3, validação v3 sob a política função–testes atual, deduplicação v1. Não reinterpretar automaticamente versões históricas incompatíveis.

Conferir integralmente as publicações antes de abrir a saída:

1. Ler JSON estrito: recusar chaves repetidas, valores não finitos, linhas vazias, registros desconhecidos e protocolos incompletos.
2. Validar descoberta e triagem com os leitores existentes, acrescentando verificações estritas onde o leitor legado não as fizer. Validar extração, validação e deduplicação com os respectivos leitores atuais.
3. Conferir os hashes dos bytes originais: o início do 02 identifica o 01; o início do 03 identifica o 02; o início do 04 identifica o 03; o 05 identifica 03 e 04. Confrontar também estados e contagens de origem publicados pelos contratos correspondentes.
4. Conferir IDs, URLs e SHAs ao longo da cadeia. A seleção integral de repositórios do 02 deve corresponder à população descoberta; os repositórios processados no 03 devem corresponder aos aprovados do 02; as candidatas do 04 devem corresponder às aprovadas do 03.
5. Reutilizar a conferência de 03/04 de `deduplication_input.load_deduplication_inputs`, incluindo alvo, associações e vínculos sintáticos comprovados. Ela não executa candidatos.
6. Exigir que o conjunto de IDs do 05 corresponda exatamente às aprovadas do 04. Reproduzir `exact_instance_context_v1` para todas elas e conferir hashes, comprimentos, igualdade dos bytes, grupos e representante mínimo contra o 05. Hash isolado não comprova igualdade; colisões não autorizam fundir representações distintas.

Os caminhos registrados são informativos; sua grafia não precisa coincidir após mover o checkout. A identidade decorre dos bytes e dos vínculos, não do caminho absoluto.

Origens encerradas como `partial` são permitidas e permanecem explícitas. Pendentes e rejeitadas não são recuperadas nem promovidas. Entrada obrigatória ausente, sem fechamento ou incompatível é erro de integridade, inclusive quando não há representantes.

## 4. Seleção e ordenação

Selecionar somente registros do 05 com `decision="approved"`, `disposition="representative"` e `representative_instance_id == instance_id`, confirmados como aprovados no 04. Não filtrar pela decisão agregada do repositório.

Preservar `instance_id` original; não criar numeração sequencial `task_id`. Ordenar a saída lexicograficamente pelo ID. A quantidade de linhas deve ser exatamente a quantidade de representantes do 05.

Zero representantes produz arquivo vazio e conclusão válida, após verificar todas as entradas. Não acrescentar linha de resumo para representar o conjunto vazio.

## 5. Contrato do registro v1

Cada linha é um objeto com os campos abaixo. Objetos expandidos mantêm os campos originais de suas evidências; campos adicionais do construtor são os explicitados nesta spec. Não substituir os critérios efetivos do 04 pelo bloco de validação estática herdado do 03.

| Campo | Conteúdo obrigatório |
| --- | --- |
| `schema_version` | Inteiro `1`. |
| `instance_id` | SHA-256 identificador original. |
| `language` | String `TypeScript`. |
| `function` | Objeto original expandido do 03: tipo, nome, classe quando aplicável, assinatura, caminho, linhas e `reference_code` integral. |
| `tests` | Lista de associações originais do 03, com localização, framework, nome, parametrização, modificadores, marcação de ignorado quando presente e associação direta; acrescentar `original_text` recuperado das linhas originais do contexto. |
| `documentation` | Lista original do 03, com tipo, texto original, caminho, linhas e associação comprovada. |
| `context` | `files`, `snapshot`, `execution_requirements` e `binary_policy="references_only"`. |
| `metrics` | `size`, `cyclomatic_complexity`, `test_cases` e `coverage`, conforme seção 7. |
| `source` | `repository_id`, `repository`, `repository_url`, `commit_sha`, `license`, `stars` e `readme`. |
| `environment` | Objetos originais `recipe`, `environment`, `lockfiles` e `execution` do repositório no 04, sem resolver ferramentas ou dependências novas. |
| `validation` | `instance` com registro integral da instância no 04, `extraction_criteria` expandidos do 03 e `repository_evidence` com registro integral do repositório no 04. |
| `deduplication` | `policy`, registro original `representative` do 05 e lista `duplicate_origins`. |
| `provenance` | `sources`, `builder` e `metric_policy`. |

### 5.1. Função, testes e documentação

Preservar os valores originais, sem reformatar TypeScript, corrigir fontes, trocar framework, gerar asserts ou converter testes para outro formato. Métodos permanecem métodos; o arquivo de contexto preserva classe, imports e helpers necessários.

`tests` contém todas as associações diretas publicadas para a instância, não apenas quatro testemunhas nem somente declarações que receberam crédito dinâmico. Preservar ordem e multiplicidade das associações, inclusive várias chamadas da mesma declaração. A evidência de execução no bloco `validation` distingue associação estática, execução positiva, ignorados e contagens incompletas. Não afirmar que cada associação publicada foi executada com sucesso.

Recuperar `original_text` com a semântica inclusiva de linhas já usada em `deduplication_representation._snippet`, preservando os terminadores internos. Testes parametrizados permanecem como declarações originais; não sintetizar um trecho por execução.

Não procurar documentação nova. Incluir README completo somente quando ele já estiver no contexto textual da instância; registrar sua presença no repositório separadamente.

### 5.2. Contexto e evidências compartilhadas

`context.files` contém todos os arquivos de `context_paths` após expansão, ordenados por caminho, com `path`, `content` integral e `sha256` calculado conforme a convenção do 03. Não reduzir esse conjunto a imports aparentes ou a quatro testemunhas. Referências `context_ref` e `reason_ref` são resolvidas antes da publicação.

`context.snapshot` preserva a referência original do snapshot, não seu conteúdo Git. `execution_requirements` preserva os requisitos originais e sua condição de evidência estática; não reinterpretar `not_evaluated` como falha nova da instância.

Os textos e as provas publicados no 04 permanecem incorporados em `validation.repository_evidence`, inclusive tabelas de fontes e planos usados na validação. Referências internas dessas evidências devem poder ser resolvidas dentro da própria linha. Não eliminar evidências compartilhadas só por se referirem também a outros testes; o resultado global permanece identificado como global.

Não ler binários nem recuperar snapshots para completar recursos. Preservar referências disponíveis em requisitos, manifestos, receita ou evidência; não afirmar inventário completo de binários necessários quando as fontes não o demonstrarem. Não criar binários, Base64 ou placeholders de arquivo.

### 5.3. Origem, licença e duplicatas

Obter nome e estrelas no 01, presença de README no 02 e licença verificada em `criteria.license` do 02. `source.license` deve conter `spdx_id` e o critério integral com sua evidência original; não substituir pela licença preliminar do GitHub no 01. Ausência ou inconsistência de licença verificada impede a publicação por erro de integridade.

`stars` e `readme` usam envelope `{status, value, reason}`: `available` com valor e motivo `null`, ou `unavailable` com valor `null` e motivo. Zero estrelas e README ausente são valores disponíveis, não indisponibilidade. Não inventar metadados nem consultá-los novamente na rede.

`duplicate_origins` preserva os registros integrais do 05 com `disposition="duplicate"` que apontam para o representante; ordenação por ID e lista vazia quando não houver duplicatas. Não unir código, documentação, testes ou ambientes dessas origens com os do representante.

### 5.4. Proveniência

`provenance.sources` contém `discovery`, `triage`, `extraction`, `validation` e `deduplication`, cada uma com caminho efetivamente lido, SHA-256 dos bytes originais, estado e versão de esquema quando declarada. Formatos sem versão explícita usam `schema_version=null`, sem inventar número de versão retroativo.

`builder` contém nome `robo-minerador`, versão do pacote e política `dataset_build_v1`. `metric_policy` identifica `typescript_function_metrics_v1`, versão do TypeScript e regras da seção 7. Não adicionar horário de construção volátil: mesmas entradas e mesmas versões devem produzir os mesmos bytes.

## 6. Reprodução e limites de autonomia

O construtor trabalha offline. Não exige token, Docker ou fetch Git, não instala dependências e não executa funções, scripts, configurações ou testes candidatos. Havendo candidatas aprovadas, reutiliza Node >=22 e TypeScript 6.0.3 confiáveis para conferência sintática e cálculo de métricas. Zero aprovadas dispensa o auxiliar, mas não a verificação das entradas.

O dataset fornece fontes textuais e as instruções/identidades de ambiente já observadas, sem garantir disponibilidade futura de registros npm, imagens ou recursos não incorporados. O construtor não cria executor de experimentos nem novo mecanismo de reprodução.

## 7. Métricas: `typescript_function_metrics_v1`

### 7.1. Tamanho

`metrics.size` contém `status="available"`, `physical_lines`, `method="original_declaration_physical_lines_v1"` e `reason=null`.

Contar linhas físicas do início da declaração até o fechamento, incluindo linhas vazias e comentários internos, excluindo documentação anterior à declaração. Reconhecer CRLF como um terminador e também LF, CR, U+2028 e U+2029. Uma declaração em uma linha tem tamanho 1. O terminador após o fechamento não adiciona linha à função. Conferir os limites na AST do arquivo original; não aproximar a unidade por busca de nome ou regex.

### 7.2. Complexidade ciclomática

`metrics.cyclomatic_complexity` contém `status="available"`, `value`, `method="typescript_ast_decisions_v1"` e `reason=null`.

Iniciar em 1; acrescentar 1 por ocorrência de:

- `if`;
- `for`, `for…in`, `for…of`, `while` ou `do…while`;
- cláusula `case`, sem contar `default`;
- `catch`;
- expressão ternária;
- operador `&&`, `||` ou `??`, inclusive `&&=`, `||=` e `??=`.

Contar cada nó uma vez. Não contar `else`, `switch` por si só, encadeamento opcional ou operadores de tipos. Percorrer o corpo da unidade selecionada; não atravessar funções aninhadas, arrows, métodos ou classes aninhadas, nem contar expressões de parâmetros/decoradores como fluxo do corpo. Um `else if` contém novo `if` e acrescenta 1.

O auxiliar usa a AST do arquivo completo e os limites conferidos da unidade, preservando contexto de métodos. Erro de parser, alvo divergente ou cálculo impossível é impedimento de construção, não zero, descarte silencioso ou nova rejeição científica.

### 7.3. Testes e cobertura

`metrics.test_cases` copia integralmente `test_cases` do 04, preservando a semântica publicada de contagem exata ou limite inferior. `metrics.coverage` copia integralmente `coverage`, mantendo separados `associated_tests` e `full_unit_suite`, denominadores, ferramentas, provas e motivos disponíveis.

Não converter declarações em número de casos executados, nem somar associações como casos distintos. Valor indisponível permanece `null` com estado/motivo original. Cobertura global não deve ser apresentada como cobertura dos testes associados. Nenhuma métrica introduz limiar de exclusão.

## 8. Componentes e interfaces

- **Leitor e vinculador de entradas:** recebe os cinco caminhos; devolve fontes verificadas, representantes, artefatos expandidos e evidências. Depende dos validadores existentes e da representação exata; não publica dados.
- **Auxiliar estático de métricas:** recebe arquivos originais e identidade/limites dos alvos por repositório; devolve métricas por `instance_id`. Depende do TypeScript confiável, sem carregar módulos candidatos.
- **Montador do dataset:** recebe entradas verificadas e métricas; produz os objetos finais determinísticos. Expande contexto e copia evidências sem mudar decisões.
- **Validador/leitor do dataset:** lê o 06 isoladamente e confere esquema, IDs únicos e ordenados, hashes textuais, referências internas, coerência de métricas e decisões. Não afirma conferir os bytes dos arquivos intermediários sem recebê-los.
- **CLI/publicador:** resolve configuração, protege a saída, coordena preflight, preparação, verificação e escrita; informa resultado no terminal.

O validador deve aceitar o conjunto vazio. Em arquivo não vazio, exigir o mesmo conjunto de fontes e políticas em todas as linhas. A quantidade esperada de representantes pode ser conferida pelo produtor contra o 05; o leitor isolado não consegue detectar a perda de linhas completas do JSONL, nem distinguir arquivo vazio legítimo de truncamento total.

## 9. CLI, serialização e erros

Comando: `python -m robo_minerador build-dataset`, somente com `--help` e `--overwrite`. Sem limite, workers, seleção de funções ou caminhos na CLI. Configuração por constantes, conforme as etapas recentes.

Serializar cada objeto com chaves ordenadas, separadores compactos, `ensure_ascii=True` e `allow_nan=False`, em UTF-8, seguido de LF. Textos decodificados permanecem idênticos aos originais, inclusive surrogates isolados preservados por escapes. Não normalizar espaços, finais de linha ou Unicode.

Validar configuração, integridade, vínculos, métricas e registros antes de abrir a saída. Recusar sobreposição da saída com qualquer entrada, inclusive aliases resolvíveis do mesmo arquivo; recusar diretório como arquivo e pai inválido. Saída existente exige `--overwrite`. Erros de configuração/entrada ou recusa de sobrescrita retornam 2 e preservam a saída anterior.

Conclusão válida retorna 0. Impedimento operacional do auxiliar, falha de escrita ou cancelamento retorna 1. Falha antes da escrita preserva a saída anterior; durante escrita pode deixar arquivo incompleto, pois o escritor atual não garante substituição atômica. Não publicar arquivo alternativo nem prometer fechamento no JSONL de instâncias. O terminal informa a falha e, no sucesso, a quantidade de instâncias e o caminho final.

A preparação pode usar memória proporcional às entradas e às linhas completas; não prometer streaming de memória limitada. Não duplicar estruturas intermediárias desnecessariamente, mas não alterar o contrato em busca de compactação.

## 10. Verificação e critérios de aceitação

Testes devem cobrir riscos concretos, sem exigir teste por função ou meta de cobertura:

1. Seleção exata de representantes, inclusive aprovação local com repositório globalmente reprovado; exclusão de pendentes, rejeitadas e duplicatas.
2. Hashes, vínculos e populações incompatíveis em qualquer etapa; grupo adulterado no 05; colisão simulada de hash sem igualdade de bytes.
3. Preservação de código, documentos, contexto, multiplicidade de associações, ignorados e parametrizados, CRLF e caracteres preservados por escapes.
4. Métodos com contexto de classe, funções aninhadas e cada categoria de decisão da métrica, sem contaminar o alvo com decisões de outra unidade.
5. Cobertura por escopo, indisponibilidade versus zero e contagem exata versus mínimo comprovado.
6. Licença verificada versus preliminar, README ausente versus não avaliado, estrelas zero, origens duplicadas e ausência de binários incorporados.
7. Proteção de overwrite e sobreposição, conjunto vazio, erro operacional do auxiliar, falha de escrita e roundtrip pelo leitor final.

Executar os testes existentes pertinentes às interfaces reutilizadas. A implementação deverá verificar o 06 produzido com o leitor próprio e confrontar IDs/quantidade com o 05. Uma execução real de construção só usa as entradas existentes, sem novas validações nem alteração de 01–05 ou cache. Registrar no plano as verificações efetivamente realizadas, sem tratar contagens históricas como população fixa.

## 11. Limites desta entrega

Sem implementação nesta fase. Após revisão e aprovação desta spec escrita, elaborar o plano de implementação com caminhos, tarefas, dependências e verificações concretas.

Instrução posterior do pesquisador: implementar com subagentes e revisor independente após cada tarefa. Criar somente testes necessários. Fazer um único commit quando toda a construção do dataset estiver concluída e aprovada pelos revisores; não fazer commits intermediários nem push. A autorização não antecipa o commit nesta entrega de planejamento.

Ficam fora desta etapa experimentos com LLM, geração de enunciado, divisão train/validation/test, amostragem manual, novas análises de documentação, near-duplicates, testes de mutação, categorias comportamentais, reexecução de testes e relatórios separados.

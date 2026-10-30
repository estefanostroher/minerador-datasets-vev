# Minerador incremental de funções TypeScript

## Objetivo e decisões aprovadas

Construir um minerador simples, coordenado por Python, para produzir um dataset de funções e métodos TypeScript existentes, com documentação comportamental original, implementação de referência e testes unitários diretamente associados.

A fonte metodológica principal é `Heurísticas_V6.md`, originalmente em Downloads. Suas regras detalhadas prevalecem sobre divergências da tabela de etapas e do rascunho do artigo. Por decisão expressa do pesquisador, H-R05 passa a exigir uma execução aprovada da suíte completa, em vez de três execuções consecutivas. Mantém-se a exclusão integral na primeira falha de instalação, compilação ou execução, sem ajustes corretivos. Mutação, categorização comportamental de testes e experimentos com LLMs ficam fora desta implementação.

O desenvolvimento será incremental, com um commit de implementação por etapa: descoberta, triagem, extração, validação, deduplicação e construção. A documentação não autoriza executar commits automaticamente. Testes do minerador serão criados apenas quando estritamente necessários, conforme a política de verificação abaixo.

## Execução do plano e transição entre etapas

- Todo o desenvolvimento deve ocorrer no checkout atual, na branch `feature/minerator-pilot`. Não criar worktrees separadas nem branches auxiliares. Os subagentes devem trabalhar nesse mesmo checkout, respeitando a atribuição de arquivos e evitando edições simultâneas no mesmo arquivo.
- A execução do plano deve utilizar subagentes, com tarefas delimitadas e revisão dos resultados pelo agente coordenador. Trabalho independente pode ser delegado em paralelo; tarefas dependentes devem respeitar a ordem das etapas. A execução sequencial do minerador descrita na arquitetura não restringe o uso de subagentes durante o desenvolvimento.
- Ao concluir uma etapa e antes de iniciar a seguinte, revisar a saída produzida, os requisitos e o plano da próxima etapa. Comunicar ao pesquisador o resultado da etapa e eventuais pendências.
- Caso seja encontrada qualquer ambiguidade, relacionada a requisitos, metodologia, implementação, formatos, ferramentas ou qualquer outro aspecto, parar o trabalho afetado, perguntar ao pesquisador e aguardar sua resposta antes de decidir ou implementar. Essa regra vale na transição entre etapas e também durante sua execução; não resolver ambiguidades silenciosamente por suposição.
- Atualizar e ampliar o plano da etapa quando os resultados anteriores, novas informações ou esclarecimentos exigirem mudanças. Registrar as decisões e os critérios de conclusão antes de executar o trecho afetado. Mudanças de escopo ou de metodologia dependem de confirmação do pesquisador.
- Todos os planos e instruções de trabalho enviados aos subagentes devem reproduzir essas regras e a política de testes estritamente necessários.

## Convenções de código e documentação

- Escrever o código em inglês, incluindo nomes de módulos, classes, funções, métodos, parâmetros, variáveis e constantes.
- Escrever comentários e docstrings em português.
- Toda função ou método criado deve ter uma docstring em português explicando o que faz. Documentar parâmetros, retorno e efeitos colaterais quando necessários para entender seu uso. Em auxiliares TypeScript, aplicar a mesma regra com comentários de documentação.
- Manter o código modular, com módulos de responsabilidades claras e cada função ou método realizando uma única tarefa. Separar, por exemplo, consulta à API, transformação de registros e persistência, sem introduzir abstrações desnecessárias.
- Incluir essas convenções nos planos de implementação, nas instruções aos subagentes e na revisão de cada etapa.

## Arquitetura

Python coordena as etapas, com PyGithub para acesso à REST API do GitHub. A execução inicial será sequencial, com funções e módulos pequenos, sem banco de dados ou infraestrutura de filas. A análise estrutural de TypeScript poderá usar um auxiliar Node.js baseado na Compiler API ou ts-morph, preservando a resolução de referências diretas exigida por H-F02. Essa escolha será detalhada quando a extração for planejada.

Cada etapa lê os arquivos anteriores necessários e produz exatamente um JSONL. Não haverá exportação JSON, arquivo de resumo, arquivo separado de exclusões ou logs persistidos como artefatos secundários de saída. Evidências, resultados e diagnósticos necessários pertencem aos registros do JSONL da etapa. Fontes recuperadas e ambientes temporários de execução são arquivos de trabalho, não saídas adicionais do processo.

Os arquivos intermediários não repetem integralmente os anteriores. `repository_id` e `repository_url` ligam os registros por repositório. A partir da extração, `instance_id` distingue funções do mesmo projeto. A consolidação final recupera os atributos necessários dos arquivos anteriores.

## Descoberta

- Buscar repositórios públicos com TypeScript e data de criação igual ou posterior a `2025-01-01`, usando `created:>=2025-01-01`.
- Expor constantes simples para a data mínima e a quantidade máxima de repositórios, inicialmente `MIN_REPOSITORY_DATE = "2025-01-01"` e `MAX_REPOSITORIES = 1000`.
- A quantidade limita repositórios únicos encontrados antes da triagem; não determina a quantidade de aprovados ou de instâncias finais. Deve aceitar valores como 1000 e 10000.
- Começar pela busca direta de todo o intervalo de criação, com `sort=updated` e `order=desc`. Aproveitar imediatamente os itens da primeira página antes de considerar qualquer divisão. O corte por criação desde 2025 permanece obrigatório; a ordenação é por atualização, não por criação, e não constitui amostragem aleatória.
- Não usar estrelas ou presença de README como filtro. Não acrescentar exclusões por arquivamento ou outros atributos sem decisão metodológica.
- Autenticar por token em variável de ambiente, sem persistir credenciais.
- Ao atingir um limite temporário de requisições do GitHub, aguardar a liberação indicada pela API e continuar a coleta, informando a espera no terminal. Não encerrar a coleta somente por um rate limit temporário; erros não recuperáveis continuam sendo registrados no JSONL.
- Paginar cada consulta com `per_page=100`, no máximo dez páginas acessíveis, parando assim que atingir o limite de IDs únicos. `total_count>1000` sozinho não autoriza divisão nem diagnóstico de saturação: se os itens acessíveis bastarem, encerrar imediatamente.
- Dividir o intervalo somente se ainda faltarem candidatos após as dez páginas acessíveis e `total_count>1000`, ou se uma página informar `incomplete_results=true` enquanto ainda faltar atingir o limite. No fallback, visitar primeiro a metade de criação mais recente; cada consulta continua ordenada por atualização decrescente. Não subdividir proativamente.
- Preservar os itens de toda página incompleta e emitir diagnóstico, mantendo estado parcial mesmo se esses itens completarem o limite; nesse caso, parar sem dividir. Se ainda faltar e o intervalo for divisível, subdividir para tentar recuperar mais dados. Em intervalo indivisível, preservar a parcela acessível; emitir `search_saturated` somente se `total_count>1000` e ainda faltarem candidatos após o teto acessível. Não declarar coleta exaustiva.
- Eliminar repetições de repositórios entre consultas pelo ID do GitHub, preservando as consultas de origem.
- Preservar o SHA da primeira observação de cada ID, inclusive quando reaparecer nas consultas de fallback.
- Registrar ID, URL, licença informada pela API, estrelas, branch padrão, commit SHA selecionado, data da coleta e consultas de origem. As próximas etapas usarão esse SHA, mesmo se a branch mudar.
- A licença informada pela API é preliminar. Ausência ou indeterminação será preservada para a triagem.
- Falhas e limitações da coleta serão registradas no mesmo JSONL como registros de diagnóstico, distinguíveis por `record_type`. Esses registros não contam para a quantidade de repositórios.

Registrar em `run_started` a estratégia `direct_search_then_partition`, a ordenação `updated_desc` e o limite superior de criação congelado no início da execução. Esse instante limita a criação; não congela atualizações nem representa snapshot da busca. Além do teto acessível, a combinação de consultas particionadas não garante ordenação global por atualização.

O README da implementação da descoberta explicará configuração, execução, campos, limite configurável, corte temporal, busca direta antes do fallback, limitações da ordenação e distinção entre candidato e repositório aprovado. Sua atualização ocorrerá após a revisão da implementação. A medição histórica de aproximadamente 50,2 segundos precede esta otimização; o novo desempenho depende de nova execução real.

## Contrato de saídas

Cada arquivo será UTF-8, com um objeto JSON por linha. O discriminador `record_type` permite distinguir dados e diagnósticos no mesmo arquivo. Etapas posteriores processam somente os tipos e estados pertinentes.

| Etapa | Arquivo | Unidade e conteúdo novo |
| --- | --- | --- |
| Descoberta | `results/01_descoberta.jsonl` | Repositório candidato e metadados de coleta; diagnósticos da busca. |
| Triagem | `results/02_triagem.jsonl` | IDs de origem, decisão, critérios avaliados, evidências e motivos. |
| Extração | `results/03_extracao.jsonl` | IDs de origem e instância, função, implementação original, documentação, testes e contexto; decisões de extração. |
| Validação | `results/04_validacao.jsonl` | IDs, resultados da execução, casos válidos por função, cobertura, ambiente, comandos e diagnósticos. |
| Deduplicação | `results/05_deduplicacao.jsonl` | IDs, representação/hash comparados, decisão e vínculos entre duplicatas e origens. |
| Construção | `results/06_dataset.jsonl` | Somente instâncias finais completas, com os atributos exigidos pelas heurísticas. |

Os motivos de exclusão ficam no arquivo da etapa que tomou a decisão. Somente aprovados seguem para processamento. Critérios usam `approved`, `rejected` ou `not_evaluated`; ausência de avaliação não equivale a aprovação. Falha de infraestrutura de coleta não deve ser apresentada como reprovação científica de um repositório.

## Responsabilidades posteriores

### Triagem

Recuperar os artefatos no SHA selecionado; verificar acesso, licença permitida, TypeScript, indícios de testes e configuração suficiente para execução. Registrar presença de README sem usá-la como requisito. A confirmação executável fica na validação.

### Extração

Identificar funções declaradas e métodos de classe, recuperar implementação e documentação comportamental original associável, resolver chamadas diretas nos testes e preservar imports, mocks, fixtures e dependências locais. Excluir implementações triviais conforme H-F06. Não inventar especificações nem usar apenas semelhança de nomes para comprovar associação.

### Validação

Preparar ambiente controlado, instalar dependências, compilar quando necessário e executar a suíte completa uma vez. Qualquer falha exclui o repositório. Exigir ao menos quatro casos válidos diretamente associados por função, contando os casos parametrizados efetivamente executados. Excluir dependência de serviços externos reais. Registrar ambiente reproduzível e cobertura disponível; cobertura indisponível é diferente de zero e não é filtro de inclusão.

### Deduplicação

Remover somente duplicações exatas e preservar todas as origens. A representação comparada será explicitamente definida antes de implementar esta etapa. Não excluir automaticamente semelhanças estruturais ou semânticas.

### Construção

Consolidar cada instância aceita com identificador, função/assinatura, especificação original, implementação de referência, testes e contexto executável, origem, licença, SHA, caminhos, ambiente e métricas. Incluir todas as elegíveis sem limite por projeto. Caracterizar documentação, tamanho, complexidade e distribuição por projeto; preparar amostra manual reproduzível com parâmetros e semente registrados no próprio JSONL, junto aos metadados das instâncias pertinentes.

O arquivo `mbpp_primeiros_10_sem_resposta.json` é referência de legibilidade e organização por tarefa, não um esquema a copiar. A implementação de referência deve estar no dataset completo, mas não na entrada fornecida futuramente à LLM.

## Verificação proporcional

A criação de testes automatizados do minerador é permitida apenas quando estritamente necessária para verificar um risco concreto de seleção incorreta, associação incorreta, contagem incorreta ou perda de integridade dos dados. Cada teste proposto no plano deve justificar qual risco verifica e por que as verificações existentes ou uma inspeção direta da saída não são suficientes. Não impor TDD obrigatório, meta de cobertura ou criação de testes por tarefa, módulo ou função.

Na descoberta, verificar principalmente paginação, particionamento de consultas, limite de repositórios únicos, preservação do SHA e tratamento explícito de coleta incompleta. Quando um teste automatizado for estritamente necessário, usar respostas simuladas para evitar dependência da API real. Uma coleta pequena pode verificar a integração real quando houver credencial disponível.

Nas etapas seguintes, avaliar a necessidade de testes para decisões de elegibilidade, resolução função–teste, contagem de casos executados, exclusão por falha e deduplicação exata, sem transformar essa lista em obrigação automática de criar testes. Não criar testes que apenas espelhem operações triviais. Executar as verificações existentes pertinentes e inspecionar as saídas da etapa antes de declarar sua conclusão.

Essa política trata dos testes do próprio minerador. A execução da suíte dos repositórios minerados permanece uma exigência metodológica da etapa de validação.

## Referências consultadas

- Methods2Test: https://github.com/microsoft/methods2test — associação entre métodos e testes; as heurísticas de nomes não substituem H-F02.
- SWE-bench: https://github.com/SWE-bench/SWE-bench — separação entre coleta, construção e execução; coleta Python com JSONL intermediário.
- PyDriller: https://github.com/ishepard/pydriller — mineração de histórico Git; não necessário para a descoberta de snapshots.
- PyGithub: https://github.com/PyGithub/PyGithub — cliente Python da REST API.
- GitHub Search API: https://docs.github.com/en/rest/search/search#search-repositories — paginação, teto por consulta e resultados incompletos.

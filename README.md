# Robô minerador de funções TypeScript

Projeto de pesquisa para construir um dataset de funções e métodos TypeScript de repositórios públicos do GitHub, com documentação comportamental original, testes unitários executáveis e implementação de referência.

## Estado do projeto

A **descoberta**, a **triagem estática** e a **extração estática concorrente** estão implementadas: pacote Python, CLI e uma saída JSONL por etapa. A descoberta encontra candidatos; a triagem inspeciona seus artefatos no SHA selecionado; a extração identifica funções/métodos, documentação original e chamadas diretas em testes. Aprovação na extração permite avançar para validação, mas ainda não representa uma instância final do dataset. Essas três etapas não instalam dependências dos candidatos nem executam suas suítes.

A **validação dinâmica** está implementada com CLI, receitas, executor Docker, instrumento Vitest e publicação por instância. A execução real mais recente avaliou 190 candidatas de seis origens: **4 aprovadas, 3 rejeitadas e 183 pendentes**, com estado `partial`. A aprovação comprova pelo menos quatro casos positivos associados à implementação original; a auditoria de recursos é exclusivamente diagnóstica.

- [Especificação aprovada — 2026-10-29](docs/superpowers/specs/2026-10-29-minerador-incremental-design.md)
- [Plano da descoberta — 2026-10-29](docs/superpowers/plans/2026-10-29-descoberta.md)
- [Plano da triagem — 2026-10-31](docs/superpowers/plans/2026-10-31-triagem.md)
- [Concorrência na descoberta e triagem — 2026-10-31](docs/superpowers/plans/2026-10-31-concorrencia.md)
- [Especificação da extração — 2026-11-02](docs/superpowers/specs/2026-11-02-extracao-design.md)
- [Plano e evidências da extração — 2026-11-02](docs/superpowers/plans/2026-11-02-extracao.md)
- [Saída enxuta da extração v2 — contrato e evidências](docs/superpowers/plans/2026-11-02-extracao-saida-v2.md)
- [Contexto por instância — contrato](docs/superpowers/specs/2026-11-03-contexto-por-instancia-design.md) e [implementação e evidências](docs/superpowers/plans/2026-11-03-contexto-por-instancia.md)
- [Extração v3 — contrato](docs/superpowers/specs/2026-11-03-snapshot-reutilizavel-design.md), [plano](docs/superpowers/plans/2026-11-03-snapshot-reutilizavel-extracao.md) e [evidências finais — 2026-11-04](docs/superpowers/plans/2026-11-04-extracao-v3-evidencias.md)
- [Validação dinâmica — contrato](docs/superpowers/specs/2026-11-07-validacao-design.md) e [plano e registro de execução](docs/superpowers/plans/2026-11-07-validacao.md)

A integração com a API real foi **verificada em uma coleta pequena**, com cinco candidatos e cinco SHAs resolvidos, incluindo a conferência de um SHA e da data de criação no GitHub. O arquivo real já existente do pesquisador foi inspecionado e preservado; a reprodução adicional ocorreu em memória, sem nova escrita pela CLI. As evidências estão no [plano da descoberta](docs/superpowers/plans/2026-10-29-descoberta.md).

Após a otimização aprovada, uma única coleta real em memória de cinco candidatos levou **5,934444 s**: uma busca (**2,936184 s**) e cinco consultas de SHA (**2,997519 s**), sem esperas por rate limit. Terminou com `completed` / `limit_reached`, sem resultados incompletos. Antes da otimização, a reprodução histórica levou **50,197490 s**, com 15 buscas e 14 subdivisões. O tempo observado foi **88,18% menor (8,46× mais rápido)**; são amostras únicas com candidatos diferentes, não um benchmark ou garantia de desempenho.

O arquivo `results/01_descoberta.jsonl` do pesquisador permanece intacto, com os metadados antigos válidos como registro histórico. A medição nova não o substituiu; uma nova gravação depende de opção explícita do usuário por `--overwrite`. A alteração passou pelas revisões de aderência e qualidade e pela verificação local do coordenador.

## Instalação e execução

Requer Python **3.11 ou superior**. Execute os comandos PowerShell na raiz do projeto. O uso direto do executável da `.venv` dispensa ativação do ambiente.

```powershell
python --version
Test-Path -LiteralPath "."
python -m venv ".venv"
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

Configure um token do GitHub na variável de ambiente `GITHUB_TOKEN`, disponível para o processo PowerShell, antes da coleta. A aplicação lê essa variável; não carrega um arquivo `.env`. Mantenha o valor fora dos arquivos versionados e da conversa, sem imprimi-lo no terminal.

Para coletar até cinco repositórios únicos:

```powershell
.\.venv\Scripts\python.exe -m robo_minerador discover --limit 5
```

Sem `--limit`, vale o máximo configurado de 1000. A instalação também disponibiliza o comando de console equivalente:

```powershell
.\.venv\Scripts\robo-minerador.exe discover --limit 5
```

A saída tem caminho fixo: `results/01_descoberta.jsonl`, relativo à raiz de execução. Se o arquivo já existir, o comando recusa a coleta antes de consultar a API. Para autorizar sua substituição intencional:

```powershell
.\.venv\Scripts\python.exe -m robo_minerador discover --limit 5 --overwrite
```

`--overwrite` é opcional e deve ser usado somente quando a saída anterior puder ser descartada. Não há anexação nem retomada. A configuração é validada antes da coleta e da substituição: token ausente, limite inválido ou data inválida/futura preservam o arquivo existente, mesmo com `--overwrite`. A escrita não oferece substituição atômica; uma falha de disco pode deixar o arquivo incompleto.

### Configuração

As constantes ficam em `src/robo_minerador/config.py`:

| Constante | Valor inicial | Significado |
| --- | --- | --- |
| `MIN_REPOSITORY_DATE` | `"2025-01-01"` | Data mínima inclusiva de criação do repositório, à meia-noite UTC. |
| `MAX_REPOSITORIES` | `1000` | Máximo de IDs únicos antes da triagem; `--limit` o substitui apenas nessa execução. |
| `OUTPUT_PATH` | `Path("results/01_descoberta.jsonl")` | Único arquivo de saída da descoberta; não há opção de caminho na CLI. |
| `DEFAULT_WORKERS` | `4` | Concorrência padrão das duas etapas; `--workers` substitui nessa execução. |
| `MAX_WORKERS` | `16` | Maior valor aceito para `--workers`. |

O limite deve ser um inteiro positivo e pode superar 1000, por exemplo 10000, sem alterar o algoritmo. Ele não garante que existam candidatos suficientes nem define o tamanho final do dataset. Diagnósticos não consomem esse limite.

### Concorrência nas duas etapas

Ambos os comandos aceitam `--workers` de **1 a 16**, com padrão **4**. Use `1` para execução sequencial. Valores inválidos são recusados antes de consultar a API ou substituir a saída.

```powershell
.\.venv\Scripts\python.exe -m robo_minerador discover --limit 1000 --workers 4
.\.venv\Scripts\python.exe -m robo_minerador triage --workers 4
```

Se a saída da etapa já existir e puder ser substituída, acrescente `--overwrite`. Isso reinicia o processamento; não retoma uma execução anterior.

- **Descoberta:** busca, paginação e particionamento continuam sequenciais para preservar a seleção. Somente a recuperação dos SHAs dos novos candidatos de cada página ocorre em paralelo; a próxima página aguarda essa resolução. Não são submetidos IDs além do limite solicitado.
- **Triagem:** até `workers` candidatos são inspecionados simultaneamente, cada um inteiro em sua thread. Os resultados são publicados na ordem de entrada; um candidato lento pode atrasar a publicação dos seguintes.
- Cada thread reutiliza seu próprio cliente e suas conexões. A janela de tarefas é limitada; não há submissão antecipada de todos os candidatos. Somente o coordenador escreve o JSONL e atualiza contadores/progresso. Os clientes são fechados no encerramento.
- Na execução concorrente, uma pausa de API reconhecida é compartilhada entre os clientes da etapa, incluindo a busca. `Retry-After` e reset de cota são respeitados; o terminal informa a espera. Cancelamento interrompe esperas coordenadas e tarefas pendentes; chamadas HTTP já em andamento podem aguardar seu término/timeout.

Threads reduzem a espera por rede, mas não aumentam a cota da API nem reduzem, por si só, a quantidade de requisições. Não há garantia de 1000 candidatos em dez minutos. Na descoberta e triagem, o transporte continua pela API, com conteúdos em memória, sem downloads compactados ou cache em disco. A extração v3 usa o cache Git descrito abaixo. `run_started` registra o número configurado em `workers`.

## Como a descoberta seleciona candidatos

- A consulta usa `language:TypeScript is:public created:INICIO..FIM`, mantendo o corte obrigatório de criação **desde `2025-01-01`, inclusive**. O instante inicial UTC da execução, com precisão de segundos, congela somente o limite superior inclusivo de criação, não as atualizações ou um snapshot da busca.
- O corte considera a **criação do repositório**, não a idade do código. Um projeto recente pode conter código antigo importado.
- Toda consulta solicita `sort=updated`, `order=desc` e até 100 itens por página: prioridade às atualizações mais recentes, não às criações mais recentes. A estratégia `direct_search_then_partition` começa pelo intervalo inteiro desde 2025, coleta imediatamente os itens da primeira página e continua por até dez páginas acessíveis, parando assim que atingir o alvo de IDs únicos. Não há janela inicial de uma hora nem subdivisão preventiva por contagem alta.
- O particionamento por criação é fallback somente se ainda faltarem candidatos após dez páginas com `total_count>1000`, ou se uma página recebida estiver incompleta e o alvo continuar não atingido. Nesse fallback, visita primeiro a metade de criação mais recente; cada consulta continua solicitando atualização decrescente.
- A ordenação é solicitada à API, não uma verificação de sequência global estrita de `updated_at`. Os resultados podem mudar durante a coleta. Combinar partições de criação não garante selecionar os **10000 mais recentemente atualizados** em ordem global estrita. A seleção não é aleatória nem ordenada por estrelas.
- O teto de 1000 vale por consulta, não para toda a coleta. Em intervalo indivisível de um segundo, `search_saturated` só é emitido se ainda faltarem candidatos após as dez páginas acessíveis com `total_count>1000`. Contagem alta com limite satisfeito não implica saturação nem estado parcial. Toda página incompleta gera diagnóstico e mantém estado parcial, mesmo se seus itens completarem o limite; nesse caso, a coleta para sem dividir. A coleta não é apresentada como exaustiva.
- IDs repetidos são consolidados em uma linha, preservando as consultas de origem observadas e o SHA recuperado na primeira observação. A ordem dos registros de repositório segue essa primeira observação.
- A busca mantém o comportamento padrão do GitHub para forks, sem `fork:true`; não promete incluir todos os forks. A indexação, as respostas incompletas e a prioridade temporal limitam a cobertura e introduzem vieses.
- O filtro `language:TypeScript` não garante TypeScript como linguagem principal nem funções elegíveis. O campo `language` preserva o metadado efetivamente retornado pela API.
- Estrelas e presença de README não são filtros. Estrelas são registradas; o README não é consultado agora e terá sua presença registrada na triagem, no SHA selecionado. Não há filtro adicional por licença, arquivamento ou atividade recente.

### Limites de requisições e falhas

A espera por limites temporários da API foi aprovada no protocolo. O cliente usa timeout de 30 segundos e o retry padrão do PyGithub, com tentativas limitadas. Na versão local inspecionada, **PyGithub 2.10.0**, o padrão permite até 10 novas tentativas: usa `Retry-After` quando informado, aguarda até o reset do limite primário com margem de um segundo ou usa 60 segundos para limite secundário, sujeito ao backoff da biblioteca. Não há teto adicional configurado para a duração da espera. A dependência aceita `PyGithub>=2.5,<3`; esses detalhes descrevem a versão inspecionada.

Nem todo HTTP 403 é tratado como rate limit: a biblioteca verifica os sinais da resposta. As esperas reconhecidas são informadas no terminal em português, por exemplo no formato `Limite temporário da API: aguardando N segundos para tentar novamente.` Não há retry infinito nem rotação de tokens. Se as tentativas se esgotarem, a falha operacional é registrada no JSONL com status HTTP e cabeçalhos numéricos de limite disponíveis, sem credenciais. Não são habilitados logs HTTP detalhados nem arquivos de log.

## Saída e interpretação

A etapa produz **exatamente um arquivo**, `results/01_descoberta.jsonl`, em UTF-8, com um objeto JSON por linha. Não usa banco de dados e não produz resumo, exportação JSON, arquivo de erros ou logs persistidos separados. Mensagens de andamento e resultado aparecem no terminal.

Há dois tipos de registro, distinguidos por `record_type`:

- `repository`: um repositório candidato por ID do GitHub.
- `diagnostic`: início, fim e limitações da coleta. Não representa um candidato.

Por isso, a contagem de linhas não equivale à quantidade de repositórios. Para processar candidatos, selecione somente `record_type == "repository"` e examine também os diagnósticos.

### Campos de repositório

| Campo | Conteúdo |
| --- | --- |
| `record_type` | Sempre `repository` nesse tipo de registro. |
| `repository_id` | ID numérico do GitHub, usado para unicidade. |
| `repository_url` | URL pública canônica retornada pelo GitHub. |
| `repository` | Nome completo retornado pela API, como `organizacao/projeto`. |
| `language` | Linguagem informada pela API, possivelmente `null`; não é inferida da consulta nem precisa ser TypeScript. |
| `created_at` | Data de criação informada pela API, para inspecionar o corte temporal. |
| `license_reported` | Identificador SPDX informado pela API ou `null`. É preliminar e não aprova a licença. |
| `stars` | Contagem de estrelas observada, sem limiar de inclusão. |
| `default_branch` | Nome da branch padrão informado pela API. |
| `commit_sha` | SHA do commit recuperado da branch padrão, ou `null` se indisponível. Não é substituído por nome de branch, tag ou data de push. |
| `sha_status` | `resolved` ou `unavailable`. A indisponibilidade preserva o candidato e gera diagnóstico; não é reprovação científica. |
| `discovered_at` | Instante UTC de observação do candidato. |
| `search_queries` | Consultas que encontraram o ID durante a coleta, sem repetição. |

As próximas etapas usam o SHA selecionado, mesmo se a branch mudar. Na triagem, candidatos sem SHA ficam `not_evaluated`, sem selecionar uma versão substituta.

Exemplo **fictício e apenas ilustrativo** de uma linha de repositório, não de uma coleta executada nem de uma instância aprovada do dataset:

```jsonl
{"record_type":"repository","repository_id":123,"repository_url":"https://github.com/organizacao-ficticia/projeto-ficticio","repository":"organizacao-ficticia/projeto-ficticio","language":"JavaScript","created_at":"2026-10-01T10:00:00Z","license_reported":"MIT","stars":12,"default_branch":"main","commit_sha":"0123456789abcdef0123456789abcdef01234567","sha_status":"resolved","discovered_at":"2026-10-29T14:00:00Z","search_queries":["language:TypeScript is:public created:2025-01-01T00:00:00Z..2026-10-29T13:59:59Z"]}
```

### Diagnósticos e estado da execução

Os diagnósticos contêm `record_type="diagnostic"`, `stage="discovery"`, `event`, `query`, `repository_id`, `message` e `observed_at`. A consulta e o ID podem ser `null` quando não se aplicam; as mensagens são em português e os instantes em UTC. Os eventos acrescentam seus próprios campos:

| Evento | Interpretação |
| --- | --- |
| `run_started` | Primeiro registro. Informa `min_repository_date`, `max_repositories`, `upper_timestamp` de criação congelado, `strategy="direct_search_then_partition"` e `ordering="updated_desc"`. Não congela as modificações. |
| `run_finished` | Último registro. Informa `repository_count`, `status` e `stop_reason`. |
| `incomplete_results` | Uma página informou resultados incompletos. Mantém a execução parcial mesmo se houver subdivisão posterior ou se o limite for atingido. |
| `search_saturated` | Um intervalo indivisível de um segundo excedeu os 1000 resultados acessíveis e o alvo continuou não atingido após dez páginas. |
| `api_error` | Falha esperada da integração. Preserva candidatos já recuperados. |
| `sha_unavailable` | Falha ao recuperar o SHA do candidato indicado por `repository_id`; ele permanece com `commit_sha=null` e `sha_status="unavailable"`. |

Os eventos de falha incluem `http_status` e `rate_limit_headers`, quando disponíveis. O estado em `run_finished` deve ser interpretado assim:

| Estado | Significado | Código de saída |
| --- | --- | --- |
| `completed` | Terminou dentro da estratégia, por limite atingido ou busca esgotada, sem os diagnósticos de limitação acima. Não significa inventário completo do GitHub. | `0` |
| `partial` | Houve incompletude, saturação, falha de SHA ou erro de API com dados preservados. | `1` |
| `failed` | Erro de API sem nenhum candidato recuperado. | `1` |

`stop_reason` distingue `limit_reached`, `search_exhausted` e `api_error`. O motivo não substitui o estado: atingir o limite ainda pode resultar em `partial`.

Erros de argumentos/configuração ou recusa de saída existente retornam `2`, antes da coleta. Falhas ao verificar ou gravar a saída retornam `1`, com mensagem no terminal, sem criar um arquivo alternativo. Nesses casos pode não haver um JSONL novo e completo; confira também o código de saída do processo.

### Inspeção sem arquivo secundário

Após uma coleta, este comando mostra contagens e o último registro no terminal:

```powershell
.\.venv\Scripts\python.exe -c "import json; from pathlib import Path; rows=[json.loads(line) for line in Path('results/01_descoberta.jsonl').read_text(encoding='utf-8').splitlines()]; repos=[r for r in rows if r['record_type']=='repository']; print('Repositorios:', len(repos)); print('IDs unicos:', len({r['repository_id'] for r in repos})); print('Diagnosticos:', len(rows)-len(repos)); print(json.dumps(rows[-1], ensure_ascii=False))"
```

As contagens de candidatos e IDs únicos devem coincidir. Confira `run_finished` e todos os diagnósticos; o comando não substitui essa análise. Na verificação real registrada no plano, o arquivo existente continha sete linhas: cinco candidatos com SHA resolvido e os diagnósticos de início e fim, com estado `completed`. Também foram conferidos o SHA e a data de criação de um candidato contra o GitHub.

## Triagem estática

A triagem lê todos os candidatos de `results/01_descoberta.jsonl`, valida a integridade da entrada e consulta o GitHub no SHA registrado. A execução usa quatro workers por padrão, controlados por `--workers`. Os conteúdos ficam em memória durante a inspeção de cada candidato; não há clone, cache em disco, instalação ou execução de código dos projetos.

Configure `GITHUB_TOKEN` no processo PowerShell. Se o token já estiver nas variáveis do usuário Windows, disponibilize-o ao processo sem imprimir seu valor:

```powershell
$env:GITHUB_TOKEN = [Environment]::GetEnvironmentVariable('GITHUB_TOKEN', 'User')
.\.venv\Scripts\python.exe -m robo_minerador triage
```

A saída única é `results/02_triagem.jsonl`. Se existir, o comando exige `--overwrite`, que reinicia a etapa e substitui a saída; não há retomada:

```powershell
.\.venv\Scripts\python.exe -m robo_minerador triage --overwrite
.\.venv\Scripts\python.exe -m robo_minerador triage --help
```

Nesta versão, a triagem aceita `--workers`, `--overwrite` e `--help`; não aceita `--limit` ou caminhos personalizados. Os caminhos ficam em `config.TRIAGE_INPUT_PATH` e `config.TRIAGE_OUTPUT_PATH`. Configuração ou entrada inválida preserva a saída existente mesmo com `--overwrite`.

Os cinco critérios são acesso público e artefatos recuperáveis, licença permitida, indício de implementação TypeScript, indícios de testes unitários e documentação/configuração de execução. Licenças aceitas: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause e ISC. A verificação usa o endpoint de licença no SHA e confere caminho, blob e conteúdo; não é uma auditoria das licenças de todos os subpacotes. README, lockfile e popularidade não são requisitos.

Cada registro `repository` contém `repository_id`, `repository_url`, `commit_sha`, `decision`, `criteria`, `readme`, `reasons` e `screened_at`. Critérios e decisão usam `approved`, `rejected` ou `not_evaluated`. Qualquer reprovação determina `rejected`; somente todos os critérios aprovados permitem `approved`. Interrupção antecipada após licença reprovada deixa os demais critérios não avaliados. Falhas operacionais e limites do analisador geram pendência, sem reprovação científica.

A análise é conservadora e lexical: comandos personalizados, seletores e delegações de workspaces não resolvidos permanecem pendentes. Evidências distinguem caminhos candidatos, arquivos inspecionados e inspeção completa. A leitura de testes para ao encontrar evidência suficiente do mesmo pacote/framework; documentação é consultada como alternativa quando necessário. Limites por snapshot: 20 mil entradas, 512 leituras, 1 MiB por arquivo e 8 MiB acumulados. Árvores truncadas e links relevantes não inspecionados não são tratados como ausência de artefatos.

Os diagnósticos `run_started`, `repository_error` e `run_finished` ficam no mesmo arquivo. O início registra o hash SHA-256 e o estado da descoberta. O fim informa as contagens `approved`, `rejected`, `not_evaluated`, `error_count` e o estado: `completed` sem pendências/erros, `partial` com pendências/erros e alguma avaliação, ou `failed` quando nenhum critério de nenhum candidato pôde ser avaliado. Rejeições científicas, por si só, não tornam a execução parcial. Os códigos de saída são 0 para `completed`, 1 para parcial/falha/escrita e 2 para entrada/configuração inválida ou saída existente não autorizada.

A execução real sequencial, anterior à concorrência, foi interrompida depois de informar 625/1000 candidatos, com uma espera de aproximadamente 734 segundos pelo limite da API. A saída dessa execução não deve ser apresentada como coleta completa: confira a existência de `run_finished`. O progresso no terminal pode estar à frente das linhas persistidas devido ao buffer de escrita. Essa observação não é um benchmark de desempenho. A implementação concorrente foi verificada com respostas simuladas e sincronização entre threads; não foi executada uma nova coleta real nem substituídos os resultados locais para medir seu desempenho.

## Extração estática concorrente

A extração lê `results/02_triagem.jsonl` e processa somente repositórios `approved`, sempre no SHA original. O arquivo de entrada é validado integralmente antes da rede e da abertura da saída: estrutura, critérios/decisões, contagens, IDs únicos, URLs, SHAs e diagnósticos. O hash dos bytes originais e o estado da triagem ficam no diagnóstico inicial. A entrada local foi aceita com **1000 repositórios, 89 aprovados e estado `partial`**; pendências da triagem não são reclassificadas nem processadas como aprovadas.

### Preparação e comandos

Além da instalação Python editável descrita acima, a extração requer **Git instalado**, **Node.js >=22** e a dependência confiável **TypeScript 6.0.3**. Na raiz deste checkout, instale somente o auxiliar do minerador com o lockfile versionado:

```powershell
node --version
git --version
Test-Path -LiteralPath ".\src\robo_minerador\typescript"
npm ci --prefix ".\src\robo_minerador\typescript"
```

Essa instalação pertence ao próprio minerador. Dependências dos repositórios candidatos não são instaladas. A CLI confere Git, Node, auxiliar, versão de TypeScript e entrada antes de substituir qualquer saída.

```powershell
$env:GITHUB_TOKEN = [Environment]::GetEnvironmentVariable('GITHUB_TOKEN', 'User')
.\.venv\Scripts\python.exe -m robo_minerador extract --workers 4
.\.venv\Scripts\python.exe -m robo_minerador extract --help
```

A saída JSONL é `results/03_extracao.jsonl`, índice canônico da etapa. A reprodução v3 exige também o cache correspondente em `.cache/robo-minerador/snapshots/<repository_id>/<commit_sha>/`. Se a saída já existir e puder ser substituída intencionalmente, acrescente `--overwrite`: ele substitui a saída, não o cache. Não há retomada, anexação, `--limit` ou caminhos personalizados na CLI. Configuração/entrada inválida preserva a saída mesmo com `--overwrite`.

`--workers` aceita **1 a 16**, padrão **4**. Cada tarefa recupera e analisa um repositório inteiro, com no máximo um processo Node ativo por tarefa e um gate de **duas aquisições Git simultâneas**. A janela de tarefas é limitada; consultas REST conservam o gate de API. O coordenador publica na ordem de entrada. Cancelamento aborta Git e Node; chamadas HTTP já em voo podem aguardar seu timeout. Um `SnapshotStore` externo injetado mantém sua vida útil após encerramento normal do pipeline e pode ser reutilizado pelo loader; abortos continuam cancelando ambos os tipos de subprocesso.

Na primeira aquisição, a identidade pública é conferida por metadados; o transporte recupera o **SHA exato** em Git bare, sem fallback para branch, checkout ou execução de candidatos. `repository.git/`, `manifest.json` canônico e `complete.json` preservam objetos, inventário e hash verificáveis; só há publicação completa após conferência de commit, árvore e objetos. Cache hit íntegro é local: **zero consultas de metadados e zero fetch**. Binários e arquivos `export-ignore` permanecem no snapshot; links, gitlinks e ponteiros LFS são inventariados, sem baixar seus alvos automaticamente.

### O que é extraído

O auxiliar usa a **TypeScript Compiler API** sobre fontes em memória. Não importa módulos candidatos nem executa scripts, configurações ou testes. Identifica funções declaradas e métodos concretos de classes, inclusive classes expressão, preservando implementação, assinatura, caminhos e linhas. Funções expressão, arrows, construtores e accessors não são unidades desta versão.

- Resolve imports locais, aliases e reexports; analisa configurações TypeScript JSON/JSONC sem executá-las.
- Inspeciona AST e símbolos de contexto JS em programa auxiliar mesmo com `allowJs=false`, incluindo configurações, setups, presets e imports transitivos. O programa original continua determinando seleção e chamadas científicas; o auxiliar não inventa testemunhas JS de teste. Fonte necessária sem AST gera `UNANALYZED_TEXT`; sintaxe inválida gera `INVALID_SOURCE`, com impacto localizado.
- Avalia o contexto a partir da implementação, documentação e testes associados, percorrendo dependências transitivas e raízes ambientes. Uma referência ausente em módulo comprovadamente independente permanece diagnosticada, mas não bloqueia automaticamente a instância. Referências triple-slash, configurações herdadas e autorreferências de pacotes também participam da análise.
- Reconhece APIs de Vitest, Jest, Mocha e `node:test` com evidência de imports ou configuração/manifests. Uma função local chamada `test` ou `it` não comprova um teste.
- Associa chamadas diretas resolvidas no callback do teste. Helpers, hooks, callbacks diferidos, receptores ambíguos, mocks/spies e registros não comprovados não aprovam a associação original. Testes ignorados preservam seus metadados e, isoladamente, não aprovam o critério.
- Preserva documentação anexada descritiva e trechos externos explicitamente vinculados por caminho/símbolo/link. Não gera descrições nem usa apenas semelhança de nomes. Descrições curtas são aceitas; metadados/tipos sem descrição não bastam. Vínculos ambíguos ficam pendentes.
- Rejeita apenas formas de trivialidade comprováveis de H-F06. Outras formas cuja lógica não pôde ser comprovada ficam pendentes, sem limiar de complexidade.

A extração **não confirma testes aprovados, quatro casos executados, ausência de serviços externos ou ambiente reproduzível em execução**. No formato v3, os valores compartilhados ficam em `repository.instance_defaults.validation`: `status="not_evaluated"`, `executed_test_cases=null`, `suite_passed=null` e `external_services="not_evaluated"`. Uma instância pode manter um bloco próprio se seus valores divergirem do padrão; a expansão preserva esses valores. Tabelas parametrizadas não são convertidas em contagem de execuções.

### Registros do JSONL

| `record_type` | Conteúdo |
| --- | --- |
| `diagnostic` | `stage="extraction"`, início/fim, falhas e limitações. Início e fim declaram `schema_version=3`; registram proveniência, workers, limites/política, contagens, estado e uso do cache nos campos correspondentes. |
| `repository` | ID, URL, SHA, horário, `extraction_status`, analisador/versão, contexto compartilhado, issues, resumo e `instance_defaults.validation`. Declara `context_policy="versioned_snapshot_static_evidence_v1"`, `context.snapshot` verificável, `context.analysis_complete` e `execution_requirements`. `context.files` contém textos projetados com caminho, conteúdo e SHA-256, não o inventário inteiro. Sempre existe um registro por aprovado processado, inclusive sem instâncias. |
| `criterion_reason` | `reason_id` e `text`. Define uma explicação uma única vez por execução, antes da primeira referência. IDs R001/R002/... seguem a primeira publicação ordenada, não a conclusão das threads. |
| `instance` | `repository_id`, `instance_id`, decisão, função/implementação, documentação, testes e critérios. Cada critério conserva `status` e referencia sua explicação por `reason_ref`. `context_ref="repository"` substitui a lista completa de caminhos; subconjuntos ou ordens distintas permanecem explícitos em `context_paths`. |

Cada objeto ocupa uma linha, com separadores compactos, sem espaços de apresentação. A saída é UTF-8 com escapes JSON para caracteres não ASCII, preservando inclusive nomes JavaScript com surrogates isolados; os valores originais são recuperados ao decodificar o JSON. Código de referência, documentação e testes continuam na instância. Arquivos completos, origem e valores comuns ficam no repositório, evitando repetição por função; a construção final os consolidará nas instâncias pertinentes.

Há um único snapshot por `repository_id` em cada execução, garantido pela validação da entrada. As instâncias herdam URL, SHA e horário desse registro. O bloco `reasons` é omitido somente quando reproduz exatamente os motivos dos critérios não aprovados; razões adicionais ou ordem diferente permanecem explícitas. O protocolo v3 verifica a evidência estática contra o snapshot; a compactação da publicação preserva os artefatos e IDs da análise.

Para expandir metadados, use `robo_minerador.extraction_format.expand_instance(instance, repository, reasons)`, com `{reason_id: text}` dos registros `criterion_reason`. Aceita **v1/v2/v3**, restaura origem, textos de contexto, explicações e validação, sem buscar artefatos. Referências ausentes/incompatíveis geram erro. Ausência de `schema_version` identifica v1; novas execuções produzem v3, sem migração automática ou mistura de versões. `context_ref="repository"` refere-se aos textos publicados, não ao inventário completo do manifesto.

Para preparar uma **approved v3** para a futura validação, use `robo_minerador.extraction_context.load_instance_context(instance, repository, reasons, store)`. O loader exige um `SnapshotStore`, verifica identidade, manifesto, objetos e evidências, reprojeta os textos e repete a análise confiável, comparando a unidade completa. É exclusivamente offline: cache ausente/corrompido gera erro, sem fallback de rede. Expandir legado não o promove a snapshot v3. Preserve **JSONL e cache** para reprodução.

Issues com o mesmo payload, exceto o caminho, ficam agrupadas no repositório: `issue_id`, `affected_count` e `affected_paths` preservam as ocorrências e todos os caminhos, inclusive repetições. Mensagens, status HTTP, cabeçalhos ou outros detalhes diferentes não são fundidos; issues globais ficam separadas das issues por arquivo. O diagnóstico correspondente contém `repository_id`, `issue_ref`, `affected_count` e `observed_at`, além de `last_observed_at` quando os horários divergem. Sua mensagem e seus caminhos são obtidos na issue referenciada. Erros sem issue correspondente, como falha tardia ao fechar o cliente, continuam com seus detalhes no diagnóstico.

O `instance_id` é SHA-256 da identidade do repositório, SHA do commit, caminho, intervalo de linhas, ordinal lexical entre declarações na mesma linha, tipo e nome da unidade. Não depende do horário ou da ordem de conclusão das threads. Os critérios são `identifiable_implementation`, `associated_documentation`, `direct_test_association`, `nontrivial_implementation` e `preserved_local_context`. Qualquer rejeição determina `rejected`; todos aprovados permitem `approved`; os demais casos ficam `not_evaluated`.

Somente instâncias `approved` serão encaminhadas à futura validação. As `not_evaluated` permanecem auditáveis, sem disparar recuperação automática. Um repositório `partial` pode conter instâncias `approved` quando suas pendências forem comprovadamente independentes delas. A ausência de documentação ou chamadas diretas só permite rejeição com busca suficientemente completa; evidência positiva não equivale a completude de contexto.

`completed` indica processamento sem limitações/pendências; reprovações científicas isoladas não tornam a execução parcial. `partial` indica issues ou critérios pendentes com análise útil; `failed` indica que nenhum repositório pôde ser analisado. Zero aprovados na entrada produz uma execução vazia `completed`. No v3, como no v2, `error_count` conta diagnósticos `repository_error` publicados (grupos e falhas sem issue), enquanto `issue_count` conta as ocorrências originais representadas, incluindo erros de fechamento. Por exemplo, mil avisos idênticos por caminhos distintos podem resultar em `error_count=1` e `issue_count=1000`. Agrupar mensagens não aprova pendências nem melhora o estado da execução. Códigos de saída: **0** completed, **1** partial/failed/escrita, **2** entrada/configuração inválida ou saída existente sem autorização. O estado anterior `partial` permanece explícito como proveniência, sem prometer cobertura dos candidatos que não avançaram.

### Limites e evidências

Aquisição v3: **100 mil entradas, 512 MiB de blobs, 1 GiB lógico por aquisição e 300 s por aquisição**, até duas simultâneas. Durante fetch, o disco é monitorado por amostragem da soma de `st_size`, não por quota física rígida. Entradas e bytes do inventário são conferidos em streaming após fetch e antes da publicação. Não há limite total do cache nem limpeza automática. Esses limites são exclusivos da extração; os limites antigos de 512 leituras/8 MiB continuam na triagem.

Projeção/análise: **32 MiB de texto**, Node com **512 MiB de heap e 180 s por invocação**, inclusive no replay estrito. Não é um orçamento agregado do repositório, do pipeline ou de Python+Node; workers elevam o consumo possível. Snapshot completo e `analysis_complete` são distintos: bytes preservados não garantem análise completa.

Configurações, ambiente (`process`), dependências npm, carregamento dinâmico e recursos de filesystem são registrados em `execution_requirements`, sempre `not_evaluated`, sem veto automático. Esses registros e seus contadores são informativos, não ocorrências de `issue_count`. Identidade ambígua, mocks/substituições comprovados, evidências ausentes/adulteradas e buscas incompletas continuam protegidos. A análise considera configurações aplicáveis ao escopo; não exige prova dinâmica na extração.

O Python confere o protocolo e, quando existe contexto aprovado, repete a análise no auxiliar confiável para verificar grafo, referências e evidências. Essa conferência acrescenta custo local, sem chamadas ao GitHub ou execução de candidatos; cada invocação mantém heap, timeout e cancelamento. O prazo de 180 segundos vale por invocação, não pela soma das duas análises.

### Evidências históricas anteriores à v3

Uma inspeção real em memória de `yanfengliu/3d-maker`, SHA `3cc524da249b7fc3c4c0b0ec42612056ee8d68f5`, recuperou **67 arquivos** de uma árvore com 69 blobs e identificou **111 unidades: 0 aprovadas, 2 rejeitadas e 109 não avaliadas**, com estado `partial`. Foram registrados dois arquivos de contexto não inspecionados, seis vínculos documentais ambíguos, uma issue de testes incertos e 17 de implementação não comprovada. Isso verifica a integração real e suas limitações, sem validar testes nem representar uma coleta completa. A saída oficial `03_extracao.jsonl` não foi gerada por essa inspeção; descoberta e triagem foram preservadas.

A revisão do formato mediu, ainda no v1, um prefixo de **421,4 MB e 31.985 registros**, dos quais **316,2 MB** eram listas de contexto repetidas. O pesquisador interrompeu a execução e excluiu esse arquivo antes da verificação final do v2; portanto, não foi feita conversão nem medição final nesse dado real. Com o publicador e o escritor efetivos, uma **fixture sintética** de 512 arquivos, 512 instâncias e mil ocorrências de limitação passou de **7.123.499 para 593.485 bytes** (**91,7% menor**) e de **1.515 para 521 registros**. Todas as instâncias expandidas foram iguais às originais e os mil caminhos foram preservados; não é uma projeção do tamanho final da coleta. A integração CLI + analisador Node + escritor também foi verificada com fontes simuladas, sem rede ou execução de testes candidatos.

A revisão de contexto reanalisou **dez snapshots existentes, com 7.169 instâncias**, sem rede ou alteração dos resultados. Os estados permaneceram **0 approved, 141 rejected e 7.028 not_evaluated**; as **191 instâncias antes pendentes apenas por contexto continuaram pendentes** por omissões e alcance não comprovado. IDs, código, documentação, testes e valores de validação foram preservados. Portanto, o ajuste corrige a independência demonstrada nas regressões, mas **não liberou instâncias dessa coleta**. O arquivo de 58.524.958 bytes manteve SHA-256 `0ada94ae6000a42c78652d432b2bb35d3075d2b44f62d17056feafc9f5c34187` antes e depois da auditoria.

As inspeções e o replay de dez snapshots acima são **históricos, anteriores ao contrato v3**. A constatação de zero aprovações permanece válida para aquela análise; o hash antigo não é o esperado para o `03_extracao.jsonl` atual do usuário.

### Evidência final v3

O [relatório final v3](docs/superpowers/plans/2026-11-04-extracao-v3-evidencias.md) registra o replay offline dos cinco snapshots: **189 approved, 76 rejected e 2.495 not_evaluated**, em 2.760 unidades. Na coorte de 191: **188 approved e 3 pendentes**, ganho de 185 aprovações. As três pendências decorrem de projeção textual com NUL, não de aquisição ausente/corrompida. Houve prova do loader e segunda passagem HiAnur no mesmo store, sem rede. Nada disso executou candidatos ou substituiu resultados do usuário.

A **rodada mais recente, após o fix P1 de contexto JS**, confirmou igualdade integral das 2.760 instâncias e cinco repositórios, exceto horário: cinco cache hits, zero misses/metadados/fetch/aquisições/sockets, pipeline de **400,231 s**, sem timeout. O loader `nextTip` passou novamente no mesmo store após fechamento normal. As 47 fontes JS desse corpus já tinham AST no programa original; a rodada comprova estabilidade, enquanto as regressões TS/JS cobrem o cenário adversarial `allowJs=false`. O coordenador informou **501 testes Python aprovados (685,99 s), 92 Node aprovados (1,561 s)** e `extract --help` com saída 0. As revisões de aderência e qualidade do fix JS estão **APPROVED**; o revisor de qualidade também executou os 92 testes Node independentemente. A revisão documental final está **APPROVED**, sem achados após resolver o único P2 de status. Entrega autorizada para commit pelo coordenador, sem push; o hash é consultável no histórico Git.

## Validação dinâmica em Docker

### Preparação e comando

Python continua no host; **somente a validação usa Docker**. Havendo candidatas, requer Git, Node.js >=22 e o auxiliar confiável com TypeScript **6.0.3**, instalado pelo `npm ci --prefix ".\src\robo_minerador\typescript"` descrito acima. Essa instalação é do instrumento, separada dos manifestos/lockfiles candidatos. Em uma instalação por wheel, execute `npm ci --prefix` no diretório `robo_minerador/typescript` do pacote instalado. A wheel inclui receitas, auxiliares e lockfile confiável, sem `node_modules`, datasets ou cache.

Prepare manualmente o Docker Desktop com mecanismo **Linux** (ou Docker Engine Linux acessível pela CLI). A aplicação não inicia o Desktop. Confira o daemon antes do piloto autorizado:

```powershell
docker version
docker info --format '{{.OSType}} {{.Architecture}} {{.ServerVersion}} {{.CgroupVersion}}'
.\.venv\Scripts\python.exe -m robo_minerador validate --help
```

A infraestrutura demonstrada em fixtures é **Linux/amd64, Docker Engine 29.6.1, cgroup v2**, com **4 CPUs, 8 GiB e zero swap adicional** por ambiente. O daemon deve ter capacidade para aplicar esses recursos. Instalação tem orçamento de **20 minutos**, preparação necessária **10 minutos**, e a soma dos comandos unitários **30 minutos**; os prazos não reiniciam por pacote/subprocesso. Receitas fixam os limites efetivos; as constantes `VALIDATION_*` em `config.py` também são validadas antes da escrita. Não há aumento automático após falha.

O comando operacional é:

```powershell
.\.venv\Scripts\python.exe -m robo_minerador validate
```

**Não exige `GITHUB_TOKEN`**. Não herda credenciais/variáveis do Windows nos containers; transmite somente ambiente explícito da receita e do executor. A CLI inicial aceita apenas `--help` e `--overwrite`, sem workers, limite, seleção de funções ou caminhos personalizados. Entrada: `config.VALIDATION_INPUT_PATH` (`results/03_extracao.jsonl`); única saída canônica: `config.VALIDATION_OUTPUT_PATH` (`results/04_validacao.jsonl`).

Saída existente é recusada com código **2**, antes do preflight caro. Para substituição intencional, use `validate --overwrite`. Toda a entrada v3, configuração e catálogo são validados antes da execução; havendo candidatas, Git, Node/TypeScript e Docker passam por preflight antes de abrir a saída. Erro global preserva os bytes anteriores mesmo com overwrite. Não há anexação, retomada ou repetição corretiva automática. Falha de escrita/cancelamento pode deixar um arquivo sem fechamento; a escrita não é atômica.

Para preservar um 04 anterior antes de uma nova execução autorizada, execute o bloco abaixo na raiz do checkout. A cópia recebe timestamp e identificador único; falha no backup interrompe o bloco antes da validação. `validate` não aceita `--workers`: o paralelismo da extração não se aplica a esta etapa.

```powershell
& {
    $previous = ".\results\04_validacao.jsonl"
    if (Test-Path -LiteralPath $previous) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $id = [guid]::NewGuid().ToString("N")
        Copy-Item -LiteralPath $previous -Destination ".\results\04_validacao.$stamp.$id.backup.jsonl" -ErrorAction Stop
    }
    .\.venv\Scripts\python.exe -m robo_minerador validate --overwrite
}
```

Zero candidatas gera `completed` sem Git, Node, Docker, imagens, instalação ou receitas aplicáveis de projetos ausentes. A entrada, a configuração de publicação, o formato global do catálogo e a proteção da saída continuam sendo conferidos. Origem `partial` é aceita e registrada, sem reprocessar suas instâncias pendentes nem copiar automaticamente esse estado para a validação. Os arquivos 01–03 e seus snapshots são preservados; o cache é lido offline, sem fetch corretivo.

### Receitas e população atual

O baseline inicial do 03 tinha **10 repositórios, 3.976 instâncias e estado `partial`**: 189 approved, 318 rejected e 3.469 not_evaluated. A tabela abaixo documenta suas 189 candidatas de seis origens; a execução mais recente recebeu 190 candidatas. Essas contagens são observações históricas, não regras fixas. As receitas em `src/robo_minerador/validation_recipes.json` vinculam ID/SHA, hashes de lockfiles, evidências originais e imagem imutável por digest:

| Origem | Candidatas | Node / gerenciador / Vitest | Escopo e situação da receita |
| --- | ---: | --- | --- |
| YounghyeonPark/agbrte | 115 | 22.23.4 / npm 10.9.9 / 4.1.10 | `npm ci`, build original necessário, `npm test`; testes unitários completos da configuração original. |
| Nemryz/opencode-sdd-integrity | 23 | 22.23.4 / npm 10.9.9 / 4.1.11 | `npm-shrinkwrap.json` efetivo; `npm test -- tools/test/unit`, categoria Unit integral documentada originalmente. |
| mossglenn/armature | 17 | 22.23.4 / npm 10.9.9 / 5.0.3 | `UNIT_SCOPE_UNRESOLVED`: suíte mista com setup TerminusDB, sem separação unitária comprovada; nenhum comando candidato autorizado pela receita. |
| HiAnur/anur-living-map | 23 | 24.22.2 / npm 11.19.1 / 5.0.5 | `npm ci`, `npm test`; inclui pacote aninhado descoberto pela configuração original. |
| abinauv/dental-erp | 1 | Node/npm não resolvidos / Vitest 5.0.5 | `ENVIRONMENT_REQUIREMENTS_CONFLICT`: Node20 original incompatível com engines do lock; não trocar runtime para salvar candidatura. |
| saching13/macrotracker-server | 10 | 26.13.0 / pnpm 10.34.7 / 5.0.5 | Receita v2: bootstrap pnpm confiável separado, integridade SHA-512; instalação frozen e `pnpm test:unit` recursivo de API/shared, sem build desnecessário. |

### Uma passagem, evidências e limites reais

Executa-se **uma passagem** de toda a suíte unitária original de **todos os pacotes**, preservando configuração, scripts, setups, paralelismo e escopo original. Integração/E2E originalmente separados ficam fora. A decisão aprovada de uma passagem prevalece sobre a referência histórica a três no rascunho do artigo. Não se altera fonte/teste para corrigir falhas nem se repete a suíte para cobertura.

Um caso válido exige identidade do runner e prova verificável **declaração original → chamada direta → entrada na implementação original → resultado aprovado**. Duas chamadas, múltiplas assertions, iterações e retries não multiplicam casos. Parametrizações podem ser casos distintos mesmo com títulos iguais. Hooks, chamadas indiretas e mocks do alvo não recebem crédito presumido; falha inicial inesperada não é apagada por retry verde. O mínimo é **quatro casos válidos por função**; menos de quatro só reprova quando a contagem é completa. Lacunas permanecem pendentes.

**Decisão por instância — comportamento do par função–testes:** ambiente reproduzível, escopo original, instalação e preparação necessária continuam pré-requisitos. Cada função exige passagem das declarações associadas e hooks pertinentes, entrada original e pelo menos quatro casos válidos. Quatro testemunhas verificadas podem ser limite inferior quando falta censo global exato. Falha de teste alheio, inclusive no mesmo arquivo, não é herdada. Falha associada ou de hook pertinente mantém o critério local; erro de finalização do instrumento não fabrica passagem. A aprovação afirma o comportamento dos testes observados da função, sem alegar propriedades gerais do programa ou ausência de serviços.

Matriz demonstrada **em fixtures**, sem candidatos reais:

| Node | Vitest | Vite |
| --- | --- | --- |
| 22.23.4 | 4.1.10 | 7.3.6 |
| 22.23.4 | 4.1.11 | 8.3.0 |
| 24.22.2 | 5.0.5 | 8.3.4 |
| 26.13.0 | 5.0.5 | 8.3.5 |

As fixtures comprovam transformação de chamadas/métodos com `this`, assincronia/concorrência, parametrização, hooks/mocks sem crédito, skips, retries e falha de setup; a ponte Python/Docker exercita comandos npm/pnpm, lifecycle e censo. O adaptador suporta o pool **`forks`** comprovado. Outros pools, runners personalizados, versões não demonstradas, sintaxe sem transformação segura e descendentes sem observabilidade suficiente permanecem pendentes, sem fallback por aproximação. Herdar preload, isoladamente, não comprova observação completa de descendentes. Suporte em fixture não comprova compatibilidade de toda configuração real.

**Recursos são exclusivamente diagnósticos na política atual.** Estados approved/rejected/unknown da auditoria não aprovam, rejeitam nem deixam a instância pendente. `validate` não chama a auditoria automaticamente: helper Node ausente, erro ou timeout de análise de recursos não bloqueiam a publicação de testemunhas válidas. O ambiente, as receitas, a rede e o comando original continuam preservados. A mudança não autoriza ampliar serviços ou alterar escopo para conseguir candidatas.

**Auditoria histórica/diagnóstico explícito:** `DockerExecutor.observe_resources` e os modelos em `validation_resources_local.py`, `validation-resource-closure.mjs`, `validation_resource_local_models.json` e `validation_resource_models.json` continuam disponíveis. As regras abaixo descrevem esse método anterior, obrigatório apenas nos formatos/políticas históricos; não são gates do comportamento função–testes atual. A análise não executa fontes. Node, Vitest e Vite exigem versões/bindings efetivos; bibliotecas candidatas não são liberadas por nome.

A prova parte do alvo e de **todos os arquivos associados**, incluindo configuração original efetiva, hooks, setups, imports transitivos, manifests e configurações de resolução aplicáveis. O reporter preserva projeto/arquivo, isolamento, pool, configuração, setups e dependências do loader; as fontes são conferidas contra o manifesto original. `.npmrc` omitido pela projeção v3 é incorporado com conferência dos bytes; somente `legacy-peer-deps=true` é modelado. As fontes ficam uma vez em `resource_evidence.sources`; `resource_local_method` e `instance_resource_evidence` publicam modelos, hashes, fechamento, derivações e referências à observação. Não se repetem fontes inteiras por instância.

O subconjunto compreende dados/expressões imutáveis, funções locais simples, APIs limitadas de registro/assertion/hooks/`each` do Vitest, `vi.fn`, filesystem em diretório novo criado por `mkdtempSync` sob `tmpdir`, SQLite `:memory:` sem SQL/extensões e criação de HTTP de simulação com handler analisado e porta efêmera no loopback explícito. A origem do recurso determina o modelo: porta local isolada não comprova simulação. A rejeição dinâmica modelada inicialmente cobre `node:http(s).get` para a API real `api.github.com`, com evento nativo ligado a API, URL, hash, arquivo e linha originais; carregar o módulo não comprova alcançar a chamada. Outros serviços continuam proibidos, mas só recebem rejeição com prova suficiente, inclusive a prova curada já prevista no catálogo.

**Fronteira conservadora do método histórico:** sintaxe, operação, import, configuração, ambiente ou lifecycle desconhecidos **possivelmente relevantes** mantêm `no_external_services=not_evaluated`. O modelo local exige `forks`, `isolate=true` e ambiente Node efetivos, além de configuração literal compreendida. O reporter também observa a configuração raiz: raiz e projeto selecionado devem concordar nas fontes, setups e isolamento; uma raiz ausente ou diferente não é presumida independente. Plugins, aliases, projetos/configurações compostos, carregamento dinâmico, mutação, bibliotecas não modeladas, SQL e filesystem arbitrário permanecem desconhecidos nesse diagnóstico. Falta de texto ou cobertura de carregamento impede sua prova de recursos. Arquivos alheios são excluídos por fechamento estrutural de imports/configuração e isolamento comprovado. O método histórico analisa todos os corpos e efeitos compartilhados do módulo associado, sem fatiamento intrarquivo. Suíte verde e `--network none` não substituem essa prova. Essas lacunas pertencem ao diagnóstico de recursos e não bloqueiam a política função–testes atual.

Na leitura histórica, `validate_output_records` rederiva os recursos modelados com o auxiliar **Node/TypeScript confiável do minerador** e textos incorporados. Na política atual, o leitor portátil rederiva provas locais, critérios e metadados **sem chamar o helper de recursos**, sem Docker, snapshots ou caminhos temporários. Auditorias incorporadas ficam como diagnóstico histórico não recertificado. O protocolo de testemunhas e as verificações de origem, transformações, chamadas, entrada original, retries e hooks permanecem obrigatórios. Fixtures não são candidatas do dataset.

Instalação/preparação têm rede bridge, sem firewall por domínio; a suíte usa processo/container novo com `--network none` e loopback, sem serviços sobreviventes da preparação. Não se montam socket Docker, credenciais ou cache gravável. Workspace Linux preserva bytes/modos/links do snapshot; instrumento e ferramentas ficam separados. LFS/submódulos não materializados ficam pendentes. Outras arquiteturas e cgroup v1 não estão certificados pelas fixtures reais.

### Resultado e interpretação

Novas execuções do 04 usam UTF-8/JSONL compacto com escapes ASCII e **`schema_version=3`**, com política explícita `version=2`, `instance_execution_scope=function_original_tests_behavior` e `resource_gate_scope=diagnostic_only`. Os quatro critérios da instância são `execution_prerequisites`, `associated_tests_execution`, `original_target_execution` e `minimum_valid_cases`. O leitor valida a política exata na abertura e conserva a semântica histórica de v1 e de v2, incluindo `global_provisional` e `associated_closure_v1`; não migra nem reinterpreta arquivos anteriores e exige versão uniforme por arquivo.

**Replay offline já executado:** as 190 instâncias do 04 preservado foram reavaliadas das evidências salvas, sem nova execução de Vitest: **4 approved, 3 rejected e 183 not_evaluated**, contra 0/3/187 antes. As quatro aprovações retêm 11/12/13/12 testemunhas originais como limites inferiores. O JSONL v3 separado foi publicado em TEMP e aceito pelo leitor portátil após reabertura. Backup, hashes, metodologia, relatório por instância e comandos estão em [function-test-behavior-report.md](docs/superpowers/function-test-behavior-report.md). O 04 original permanece intacto. A população histórica da tabela de receitas acima é um baseline anterior; este replay usa o 03 vinculado ao 04, com 190 elegíveis.

**Execução real posterior ao replay:** o `results/04_validacao.jsonl` atual terminou em **2026-11-08T18:25:27.950251+00:00**, também com **4 approved, 3 rejected e 183 not_evaluated**, seis diagnósticos operacionais e estado `partial`. Uma aprovação pertence a `HiAnur/anur-living-map` e três a `saching13/macrotracker-server`. Os JSONL de resultados são artefatos locais ignorados pelo Git.

Publicam-se `run_started`, um `repository` por origem elegível, suas `instance`, diagnósticos e `run_finished`. Permanecem hash/estado/contagens do 03, receita, ambiente, comandos, critérios, witnesses e limitações. Fontes técnicas necessárias ao replay ficam compartilhadas no repositório, não repetidas por função. Evidências e logs são incorporados antes da limpeza. O diagnóstico global não encerra a avaliação individual; somente instâncias com todos os critérios pertinentes aprovados avançam.

Cobertura `associated_tests` é separada de `full_unit_suite`. A primeira requer atribuição por caso; a global não a substitui. Ausência de provedor/atribuição confiável é `null` com motivo, nunca zero; não se instala provedor nem se repete a execução para obtê-la. Cobertura opcional ausente não reprova.

| Estado / condição | Código | Interpretação |
| --- | ---: | --- |
| `completed` | 0 | Sem pendências obrigatórias/erros operacionais; pode rejeitar todas as candidatas ou ter zero candidatas. |
| `partial` | 1 | Avaliação científica útil com pendências/erros operacionais. |
| `failed` | 1 | Nenhuma avaliação científica útil por impedimentos operacionais. |
| Cancelamento / falha de escrita | 1 | Pode não haver fechamento; não aceitar como execução completa. |
| Entrada/configuração/preflight inválidos ou saída existente sem autorização | 2 | Recusa antes de abrir/substituir a saída. |

Exit code sozinho não comprova culpa do projeto: OOM, orçamento do minerador, registry/daemon/instrumento indisponíveis são operacionais. Falha candidata identificada rejeita o critério pertinente; falha associada ou de hook pertinente rejeita a instância. Serviços observados pertencem ao diagnóstico de recursos e não alteram a decisão na política atual. Uma execução operacionalmente parcial pode conter instâncias aprovadas com todos os gates locais demonstrados. Somente instâncias `approved` poderão seguir à futura deduplicação.

## Verificações locais

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_triage.py tests/test_triage_checks.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m robo_minerador --help
.\.venv\Scripts\python.exe -m robo_minerador discover --help
.\.venv\Scripts\python.exe -m robo_minerador triage --help
.\.venv\Scripts\python.exe -m robo_minerador extract --help
.\.venv\Scripts\python.exe -m robo_minerador validate --help
npm test --prefix ".\src\robo_minerador\typescript"
git diff --check
```

Para incluir as integrações Docker controladas no gate (não executa projetos minerados), com daemon Linux preparado:

```powershell
$env:ROBO_TEST_DOCKER = '1'
.\.venv\Scripts\python.exe -m pytest -q
npm test --prefix ".\src\robo_minerador\typescript"
```

Sem opt-in, as integrações Docker são puladas; skips não demonstram integração aprovada. O gate Python completo exige timeout do harness de pelo menos 1.200.000 ms.

`tests/test_discovery.py` contém **16 casos**, contando as parametrizações, aprovados também na execução final do coordenador. As regressões necessárias cobrem: limite 5 atendido na primeira página mesmo com contagem alta; limite 1000 atendido nas dez páginas sem dividir; limite 1001 com fallback somente após essas páginas; e parâmetros reais `sort=updated` / `order=desc` enviados pelo cliente. Permanecem as verificações de integridade: IDs únicos, fronteiras das partições, consultas de origem e SHA inicial preservados; incompletude na primeira página ou em página posterior, inclusive com limite atingido; saturação indivisível; falhas de SHA e de busca com dados preservados; e propagação de erros de programação. As operações de rede são simuladas. A integração real foi verificada separadamente pela coleta pequena acima.

## Etapas seguintes

Descoberta, triagem, extração e validação por instância estão implementadas. A validação foi exercitada em fixtures e na execução real descrita acima. Deduplicação e construção serão desenvolvidas depois. Cada etapa tem uma única saída JSONL:

| Etapa | Responsabilidade | Arquivo |
| --- | --- | --- |
| Descoberta | Buscar candidatos e preservar metadados, consultas, SHA e diagnósticos. | `results/01_descoberta.jsonl` |
| Triagem | Verificar acesso, licença, TypeScript, indícios de testes e configuração; registrar presença de README. | `results/02_triagem.jsonl` |
| Extração | Identificar funções e métodos, implementação e documentação originais, chamadas diretas nos testes e contexto. | `results/03_extracao.jsonl` |
| Validação | Executar a suíte completa uma vez e exigir ao menos quatro casos válidos diretamente associados por função. | `results/04_validacao.jsonl` |
| Deduplicação | Remover duplicações exatas e preservar suas origens. | `results/05_deduplicacao.jsonl` |
| Construção | Consolidar instâncias finais, métricas e amostra para inspeção manual. | `results/06_dataset.jsonl` |

A extração associa testes às funções por chamadas diretas e preserva seu contexto; ela não comprova que esses testes passam. A validação recebe somente instâncias aprovadas, instala dependências, prepara o necessário e executa a suíte unitária completa **uma vez**, contando os casos parametrizados efetivamente executados. Cada função precisa de pelo menos quatro casos válidos diretamente associados. Falha candidata identificada rejeita o critério pertinente sem ajustes corretivos; falhas alheias não são herdadas pela função e impedimento operacional permanece pendente.

Os arquivos intermediários são ligados por `repository_id` e `repository_url`; a partir da extração, `instance_id` distingue funções do mesmo projeto. Somente a construção final consolidará todos os atributos. A extração usa TypeScript Compiler API; o 04 acrescenta evidências dinâmicas conforme o contrato próprio. As etapas posteriores ainda dependem de planejamento.

### Desenvolvimento e revisão

O trabalho ocorre no checkout atual, na branch `feature/minerator-pilot`, sem worktrees ou branches auxiliares. O plano prevê subagentes com arquivos atribuídos e revisão pelo coordenador. Antes de avançar de etapa, revisar a saída e detalhar o próximo plano. Qualquer ambiguidade exige parar o trecho afetado, perguntar ao pesquisador e aguardar sua resposta. Commits dependem de autorização explícita.

## Escopo científico

Popularidade e README não definem elegibilidade. Nas etapas futuras, cobertura disponível e complexidade serão registradas sem novos limiares de exclusão; cobertura indisponível é diferente de zero. A inspeção manual será preparada por amostragem reproduzível e realizada pelo pesquisador.

Mutação, categorização comportamental dos testes, geração por LLMs e comparação entre geração direta e guiada ficam para trabalhos futuros. A existência de testes nos projetos não comprova adoção histórica de TDD.

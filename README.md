# Robô minerador de funções TypeScript

Projeto de pesquisa para construir um dataset de funções e métodos TypeScript de repositórios públicos do GitHub, com documentação comportamental original, testes unitários executáveis e implementação de referência.

## Estado do projeto

Somente a **descoberta de repositórios** está implementada: pacote Python, CLI, busca pela API do GitHub e saída JSONL com candidatos e diagnósticos. Um candidato ainda não é um repositório aprovado nem uma instância do dataset. A descoberta não clona projetos, analisa funções ou testes, instala dependências dos candidatos nem executa suas suítes.

- [Especificação aprovada — 2026-10-29](docs/superpowers/specs/2026-10-29-minerador-incremental-design.md)
- [Plano da descoberta — 2026-10-29](docs/superpowers/plans/2026-10-29-descoberta.md)

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

O limite deve ser um inteiro positivo e pode superar 1000, por exemplo 10000, sem alterar o algoritmo. Ele não garante que existam candidatos suficientes nem define o tamanho final do dataset. Diagnósticos não consomem esse limite.

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

As próximas etapas deverão usar o SHA selecionado, mesmo se a branch mudar. O tratamento dos candidatos sem SHA será definido antes da triagem.

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

## Verificações locais

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py -q
.\.venv\Scripts\python.exe -m robo_minerador --help
.\.venv\Scripts\python.exe -m robo_minerador discover --help
git diff --check
```

`tests/test_discovery.py` contém **16 casos**, contando as parametrizações, aprovados também na execução final do coordenador. As regressões necessárias cobrem: limite 5 atendido na primeira página mesmo com contagem alta; limite 1000 atendido nas dez páginas sem dividir; limite 1001 com fallback somente após essas páginas; e parâmetros reais `sort=updated` / `order=desc` enviados pelo cliente. Permanecem as verificações de integridade: IDs únicos, fronteiras das partições, consultas de origem e SHA inicial preservados; incompletude na primeira página ou em página posterior, inclusive com limite atingido; saturação indivisível; falhas de SHA e de busca com dados preservados; e propagação de erros de programação. As operações de rede são simuladas. A integração real foi verificada separadamente pela coleta pequena acima.

## Etapas seguintes

Somente a primeira etapa está implementada. Cada etapa terá uma única saída JSONL:

| Etapa | Responsabilidade | Arquivo |
| --- | --- | --- |
| Descoberta | Buscar candidatos e preservar metadados, consultas, SHA e diagnósticos. | `results/01_descoberta.jsonl` |
| Triagem | Verificar acesso, licença, TypeScript, indícios de testes e configuração; registrar presença de README. | `results/02_triagem.jsonl` |
| Extração | Identificar funções e métodos, implementação e documentação originais, chamadas diretas nos testes e contexto. | `results/03_extracao.jsonl` |
| Validação | Executar a suíte completa uma vez e exigir ao menos quatro casos válidos diretamente associados por função. | `results/04_validacao.jsonl` |
| Deduplicação | Remover duplicações exatas e preservar suas origens. | `results/05_deduplicacao.jsonl` |
| Construção | Consolidar instâncias finais, métricas e amostra para inspeção manual. | `results/06_dataset.jsonl` |

A extração associa testes às funções por chamadas diretas e preserva seu contexto; ela não comprova que esses testes passam. A futura validação instalará dependências, compilará quando necessário e executará a suíte completa **uma vez**, contando os casos parametrizados efetivamente executados. Cada função deverá ter pelo menos quatro casos válidos diretamente associados. A primeira falha de instalação, compilação ou execução excluirá o repositório, sem ajustes corretivos.

Os arquivos intermediários serão enxutos, ligados por `repository_id` e `repository_url`; a partir da extração, `instance_id` distinguirá funções do mesmo projeto. Somente a construção final consolidará todos os atributos. A escolha da ferramenta de análise TypeScript e os detalhes de execução das etapas futuras ainda dependem de planejamento.

### Desenvolvimento e revisão

O trabalho ocorre no checkout atual, na branch `feature/minerator-pilot`, sem worktrees ou branches auxiliares. O plano prevê subagentes com arquivos atribuídos e revisão pelo coordenador. Antes de avançar de etapa, revisar a saída e detalhar o próximo plano. Qualquer ambiguidade exige parar o trecho afetado, perguntar ao pesquisador e aguardar sua resposta. Commits dependem de autorização explícita.

## Escopo científico

Popularidade e README não definem elegibilidade. Nas etapas futuras, cobertura disponível e complexidade serão registradas sem novos limiares de exclusão; cobertura indisponível é diferente de zero. A inspeção manual será preparada por amostragem reproduzível e realizada pelo pesquisador.

Mutação, categorização comportamental dos testes, geração por LLMs e comparação entre geração direta e guiada ficam para trabalhos futuros. A existência de testes nos projetos não comprova adoção histórica de TDD.

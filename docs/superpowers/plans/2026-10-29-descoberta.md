# Descoberta de repositórios — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. A execução deve utilizar subagentes, conforme decisão do pesquisador.

**Goal:** Implementar somente a descoberta de repositórios públicos com TypeScript, criados desde 2025, produzindo um único JSONL inspecionável com candidatos e diagnósticos.

**Architecture:** Python coordena uma coleta sequencial com PyGithub. Módulos pequenos separam configuração, planejamento das consultas, integração com GitHub, montagem de registros e escrita JSONL. A busca direta usa atualização decrescente e aproveita as páginas acessíveis antes de particionar por criação; apenas no fallback visita a metade de criação mais recente primeiro.

**Tech Stack:** Python 3.11 ou superior, PyGithub, biblioteca padrão; pytest somente para os cenários críticos justificados neste plano.

---

## Progresso da execução — 2026-10-29

- Branch conferida: `feature/minerator-pilot`, no checkout atual. Nenhum worktree ou branch auxiliar criado; nenhum commit autorizado ou executado.
- A implementação das tarefas 1–5 foi concluída e passou pelas revisões de aderência à especificação e qualidade. O README da tarefa 6 foi atualizado e revisado. A inspeção do código confirma identificadores em inglês e docstrings em português em todas as funções e métodos, inclusive auxiliares de teste.
- Antes da otimização, o coordenador verificou **9 casos históricos aprovados**, ajuda da CLI, `pip check` e `git diff --check`, além de serialização e recusa de sobrescrita em memória. Após a revisão da implementação, o coordenador repetiu `python -m pytest tests/test_discovery.py -q`: **16 casos aprovados em 0,43 s**, com respostas simuladas e sem API real. `git diff --check` não apontou erros de whitespace.
- **Decisão 1 — espera por rate limit:** aguardar a liberação indicada pelo GitHub e avisar no terminal, usando o retry limitado do PyGithub. A implementação mantém essa decisão, sem tratar todo HTTP 403 como limite temporário.
- A implementação não usa SQLite ou outro banco de dados. O contrato prevê somente `results/01_descoberta.jsonl`, reunindo candidatos, metadados e diagnósticos, sem saídas secundárias. O arquivo real já existente do pesquisador foi inspecionado e preservado na verificação abaixo.
- A integração real foi verificada pelo agente da tarefa `ses_e75cfc76affeFhWTsDioEH74UP`, usando `GITHUB_TOKEN` do usuário Windows (escopo User). A reprodução adicional ocorreu em memória, sem nova escrita pela CLI, sem arquivos persistentes adicionais nem saída simulada. A atualização documental usa o relato dessa tarefa, sem novo acesso à credencial ou à API.
- A descoberta **não está encerrada**. O fechamento, a autorização de commit e a revisão da saída e do plano antes da triagem continuam pendentes. O pesquisador aprovou a busca direta com `sort=updated`, `order=desc` e particionamento somente como fallback, detalhada na seção 5. A alternativa de janela inicial de uma hora foi rejeitada; não há nova aprovação pendente para esta mudança.
- A revisão da API, do coletor e dos testes foi implementada e passou pelas revisões de aderência e qualidade. A nova medição real em memória e a atualização do README estão concluídas, conforme a seção 6. Esta atualização altera somente `README.md` e este plano, com base na especificação, no código e nos testes atuais e no relato de execução; não acessa credenciais nem grava resultados.

### Evidências históricas da integração real — 2026-10-30 UTC, antes da otimização

- **Arquivo do pesquisador:** `results/01_descoberta.jsonl` foi lido e parseado sem alteração. Contém sete linhas: início em `2026-10-30T00:19:19Z`, cinco repositórios com cinco SHAs `resolved` e fim em `2026-10-30T00:20:09Z`, com estado `completed`.
- **Reprodução em memória:** uma única coleta real com limite 5, início do intervalo em `2025-01-01T00:00:00Z` e fim exclusivo em `2026-10-30T00:26:06Z` levou **50,197490 s**. Foram 15 GETs de busca (**47,165187 s**) e cinco consultas de SHA da branch padrão (**3,023196 s**). A primeira consulta de SHA começou cerca de **47,173 s** após o início.
- **Particionamento:** a consulta raiz informou `total_count=12.225.986`. Após 14 subdivisões, a consulta final informou 513 itens em um intervalo de aproximadamente uma hora. Todas as respostas indicaram `incomplete_results=false`. Não houve retry nem espera por rate limit.
- **Espaçamento do requester:** ocorreram 14 pausas padrão de aproximadamente 0,246 s, totalizando **3,451733 s**, já incluídos no tempo total. Esse espaçamento não foi a causa principal da duração observada; predominou a sequência de buscas e subdivisões antes dos SHAs.
- **Conferência de candidato:** o GET adicional `/repos/erikgdl/Html-Typescript/commits/c40d7021700b612ee69937a7a95168c7080d8c78` retornou o mesmo SHA em **0,406435 s**, fora do tempo da coleta. A criação registrada no candidato coincidiu com a API: `2026-10-29T23:31:34Z`.
- **Limites da evidência:** os aproximadamente **50,2 s** medem o algoritmo anterior, com subdivisão proativa, não a estratégia atual. Trata-se de uma observação única, não de benchmark ou garantia de desempenho. O arquivo do pesquisador permaneceu intacto; a reprodução em memória não comprova uma nova escrita da CLI. Seus metadados antigos continuam válidos como registro histórico, sem representar a estratégia nova.

### Nova evidência da integração real — 2026-10-30 UTC, após a otimização

- **Origem:** uma única coleta real em memória pelo agente `ses_e75cfc76affeFhWTsDioEH74UP`, com limite 5, sem chamar o escritor ou a CLI de persistência. Esta documentação utiliza seu relato, sem novo acesso ao token ou à API.
- **Consulta raiz:** `language:TypeScript is:public created:2025-01-01T00:00:00Z..2026-10-30T00:52:23Z`, com `sort=updated`, `order=desc` explícitos no código. A resposta informou `total_count=12.226.213`, 100 itens e `incomplete_results=false`.
- **Duração:** **5,934444 s** no total; um GET de busca em **2,936184 s** e cinco consultas de SHA em **2,997519 s**; zero esperas por rate limit. Os primeiros cinco candidatos da resposta foram selecionados, todos com criação desde 2025 e SHA `resolved`. O fim registrou cinco candidatos, `status=completed` e `stop_reason=limit_reached`, sem incompletude.
- **Comparação observada:** frente aos **50,197490 s** e 15 buscas anteriores à otimização, o tempo foi **88,18% menor**, equivalente a **8,46× mais rápido**. São duas amostras únicas, com candidatos diferentes, não um benchmark ou garantia de ganho futuro.
- **Ordenação e alcance:** toda consulta solicita atualização decrescente à API. Isso não verifica uma sequência global estrita de `updated_at` nem garante valores específicos desse campo. Os resultados podem mudar durante a coleta; o limite superior congela a criação, não um snapshot de modificações. O fallback por criação também não garante os 10000 mais recentemente atualizados em ordem global estrita.
- **Persistência:** `results/01_descoberta.jsonl` permanece intocado, com seus metadados históricos anteriores. Não houve nova escrita da CLI nem artefato secundário. Uma nova coleta persistida só substituirá esse arquivo mediante opção explícita do usuário por `--overwrite`. A implementação revisada passou pela checagem final local do coordenador.

## 1. Base e limites desta entrega

Especificação aprovada: `docs/superpowers/specs/2026-10-29-minerador-incremental-design.md`.

Estado inicial, antes da execução deste plano: o repositório continha README e a especificação, sem aplicação Python. O README apontava para documentos antigos ausentes e mencionava SQLite e três execuções de suíte. A implementação e a correção documental estão registradas no progresso acima.

Esta entrega torna executável apenas a descoberta. Não clonar, instalar, compilar ou executar projetos candidatos. Não implementar triagem, extração, validação, deduplicação de funções ou consolidação final neste commit. Um candidato não é uma instância aprovada do dataset.

O desenvolvimento é organizado em seis commits de implementação, um por etapa. Este plano detalha o primeiro. Os planos seguintes serão detalhados na transição, após examinar a saída anterior e esclarecer ambiguidades. Não criar agora implementações vazias das outras cinco etapas.

## 2. Regras obrigatórias para todos os subagentes

**Workspace obrigatório:** usar o checkout atual e a branch `feature/minerator-pilot` durante toda a implementação. Não criar worktrees separadas nem branches auxiliares, inclusive para revisão ou testes. Antes de editar, conferir a branch atual; se for diferente da indicada, informar o coordenador e aguardar orientação do pesquisador, sem mudar de branch automaticamente.

Copiar estas instruções para cada delegação:

1. Leia a especificação e este plano antes de editar. Trabalhe somente nos arquivos atribuídos, no checkout compartilhado da branch `feature/minerator-pilot`; não crie worktrees ou branches e não reverta trabalho de outros agentes.
2. Código e identificadores em inglês. Comentários e docstrings em português. Toda função ou método criado, inclusive auxiliares de teste, deve ter docstring explicando sua única responsabilidade.
3. Prefira funções pequenas, composição simples e módulos focados. Não criar hierarquias, interfaces ou camadas genéricas sem necessidade concreta.
4. Ao encontrar qualquer ambiguidade, interrompa o trecho afetado e informe o coordenador. O coordenador deve perguntar ao pesquisador e aguardar a resposta antes de decidir ou implementar esse trecho.
5. Atualize o plano quando um esclarecimento ou uma descoberta exigir mudança. Mudanças de escopo ou metodologia precisam de confirmação do pesquisador.
6. Crie testes apenas quando estritamente necessários para os riscos justificados na seção 8. Não impor TDD, meta de cobertura ou teste por função.
7. Produza exatamente um arquivo de saída da etapa: `results/01_descoberta.jsonl`. Não criar resumo, exportação JSON, arquivo de erros ou logs persistidos separados. Mensagens no terminal são permitidas.
8. Não criar commits por tarefa interna ou por subagente. O agrupamento é um commit da descoberta completa; a autorização para efetivamente executar o commit deve ser confirmada pelo coordenador.
9. Ao concluir, informe arquivos alterados, verificações executadas, evidências, limitações e dúvidas. Não declarar sucesso de verificações não executadas.

O coordenador revisará os resultados antes de integrar tarefas dependentes. Revisões devem conferir tanto aderência à especificação quanto clareza e correção do código.

## 3. Estrutura mínima de arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `pyproject.toml` | Instalação do pacote, dependência PyGithub, extra de desenvolvimento com pytest e comando `robo-minerador`. |
| `.gitignore` | Ignorar ambiente virtual, caches, credenciais locais e resultados de coleta. |
| `src/robo_minerador/__init__.py` | Identificar o pacote, sem efeitos colaterais. |
| `src/robo_minerador/__main__.py` | Delegar a execução por `python -m robo_minerador` à CLI. |
| `src/robo_minerador/config.py` | Constantes de coleta e validação dos valores de entrada. |
| `src/robo_minerador/search.py` | Formatar consultas e dividir intervalos de criação, sem acesso à rede. |
| `src/robo_minerador/github_api.py` | Autenticação, consulta paginada e recuperação do SHA via PyGithub. |
| `src/robo_minerador/discovery.py` | Percorrer intervalos, consolidar IDs únicos e produzir registros de dados/diagnóstico. |
| `src/robo_minerador/jsonl.py` | Escrever um objeto JSON por linha em UTF-8. |
| `src/robo_minerador/cli.py` | Interpretar o comando `discover`, ligar os componentes e definir o código de saída. |
| `tests/test_discovery.py` | Apenas os cenários críticos da seção 8, sem rede real. |
| `README.md` | Instruções executáveis, campos, limitações e distinção das seis etapas. |

Não adicionar banco de dados, Docker, pandas, ferramenta de análise TypeScript ou frameworks de CLI nesta etapa. Usar `argparse`, `datetime`, `pathlib`, `json` e `os` da biblioteca padrão.

## 4. Contrato da descoberta

### Configuração

Manter as constantes em `config.py`:

```python
from pathlib import Path

MIN_REPOSITORY_DATE = "2025-01-01"
MAX_REPOSITORIES = 1000
OUTPUT_PATH = Path("results/01_descoberta.jsonl")
```

A data é um limite inclusivo de **criação do repositório**. O máximo conta somente IDs únicos de candidatos, não linhas do arquivo, páginas ou diagnósticos. Alterar para 10000 deve funcionar sem alterar o algoritmo. Não prometer que haverá essa quantidade de candidatos disponíveis.

Para a verificação pequena, a CLI aceitará `--limit` como substituição temporária do máximo, sem editar o valor padrão. Não adicionar opções para todas as constantes.

Ler `GITHUB_TOKEN` do ambiente. Validar token presente, limite inteiro positivo e data válida antes de iniciar chamadas. Uma data mínima posterior ao início da coleta deve gerar erro de configuração. Configuração inválida não deve substituir uma saída existente.

### Registros de repositório

Exemplo ilustrativo de uma única linha:

```jsonl
{"record_type":"repository","repository_id":123,"repository_url":"https://github.com/org/project","repository":"org/project","language":"TypeScript","created_at":"2026-10-01T10:00:00Z","license_reported":"MIT","stars":12,"default_branch":"main","commit_sha":"0123456789abcdef0123456789abcdef01234567","sha_status":"resolved","discovered_at":"2026-10-29T14:00:00Z","search_queries":["language:TypeScript is:public created:2026-10-01T00:00:00Z..2026-10-29T13:59:59Z"]}
```

Regras dos campos:

- `repository_id`: ID numérico retornado pelo GitHub; chave de unicidade.
- `repository_url`: URL pública canônica retornada pelo GitHub.
- `repository`: nome completo retornado pelo GitHub, sem derivar identidade somente do nome.
- `language`: valor informado pela API; não interpretar o filtro de busca como prova de função elegível ou exigir TypeScript como linguagem principal.
- `created_at`: criação informada pela API, para inspeção do corte temporal.
- `license_reported`: identificador SPDX informado pela API ou `null`; não é aprovação de licença.
- `stars`: contagem observada, nunca um filtro ou ordenação.
- `default_branch`: branch informada pela API.
- `commit_sha`: SHA recuperado ao consultar a branch padrão. Não usar nome da branch, `pushed_at`, SHA de árvore ou tag como substituto.
- `sha_status`: `resolved` ou `unavailable`. Uma falha de recuperação gera SHA `null` e diagnóstico associado; não atribuir uma reprovação científica automaticamente.
- `discovered_at`: instante UTC de observação do candidato.
- `search_queries`: consultas efetivamente responsáveis por encontrar o ID, sem repetição.

Não consultar README nesta etapa. Sua presença será registrada na triagem, no SHA escolhido.

### Diagnósticos no mesmo arquivo

```jsonl
{"record_type":"diagnostic","stage":"discovery","event":"incomplete_results","query":"language:TypeScript is:public created:2026-10-01T00:00:00Z..2026-10-29T13:59:59Z","repository_id":null,"message":"A API informou resultados incompletos; a coleta deste intervalo não pode ser considerada exaustiva.","observed_at":"2026-10-29T14:00:00Z"}
```

Usar mensagens em português e eventos em inglês. Eventos necessários: `run_started`, `run_finished`, `incomplete_results`, `search_saturated`, `api_error` e `sha_unavailable`. Campos específicos pertencem ao próprio registro; não criar esquema genérico complexo.

`run_started` registra data mínima, máximo solicitado, instante superior de criação congelado, estratégia `direct_search_then_partition` e ordenação `updated_desc`. Esse instante não congela atualizações nem cria um snapshot dos resultados. `run_finished` registra quantidade de candidatos, estado e motivo da parada. São metadados de execução dentro do JSONL, não arquivos secundários.

Estados de execução: `completed`, `partial` ou `failed`. Motivos de parada: `limit_reached`, `search_exhausted` ou `api_error`. Resultados incompletos tornam o estado `partial`, mesmo que os itens dessa página completem o limite. Saturação indivisível torna o estado parcial somente quando ainda faltam candidatos após o teto acessível; contagem acima de 1000, por si só, não é diagnóstico. `completed` significa execução concluída dentro da estratégia documentada; não significa inventário completo do GitHub.

Não persistir tokens, cabeçalhos de autenticação ou representações de requisições que possam conter credenciais. Diagnósticos de exceção devem usar código HTTP e contexto sanitizado.

## 5. Busca, paginação e limite por consulta

1. Congelar o instante inicial UTC, com precisão de segundos, como limite superior da criação. Isso limita novas criações, mas não congela atualizações nem constitui snapshot da busca.
2. Representar intervalos internamente como `[start, end)`, com `end` exclusivo. O último segundo incluído na consulta é `end - timedelta(seconds=1)`. Para incluir o segundo inicial da execução, usar esse instante mais um segundo como `end` inicial.
3. Usar `language:TypeScript is:public created:INICIO..FIM`. Não acrescentar estrelas mínimas, licença, README, atividade recente ou exclusão por arquivamento. Registrar o comportamento padrão da busca quanto a forks, sem alegar cobertura de todos os forks.
4. Começar pelo intervalo inteiro desde 2025. Em toda consulta, inclusive nas metades de fallback, enviar `sort=updated`, `order=desc`, `per_page=100` e `page`. A ordenação é por atualização decrescente, nunca por criação ou estrelas.
5. Buscar a página 1, ler `total_count`, `incomplete_results` e `items` da resposta original e coletar imediatamente seus candidatos, antes de considerar divisão. Consolidar pelo ID, acrescentar consultas de origem e recuperar SHA apenas na primeira observação; consultas posteriores preservam esse SHA e a ordem inicial dos registros.
6. Verificar incompletude em toda página recebida, inclusive quando seus itens completam o limite: preservar os dados e emitir `incomplete_results`, mantendo estado parcial. Se o limite foi atingido, encerrar sem novas buscas, subdivisões ou recuperações de SHA desnecessárias.
7. Sem incompletude e ainda abaixo do limite global, continuar pelas páginas acessíveis, até dez páginas por consulta ou esgotar os resultados. Parar assim que houver IDs únicos suficientes, mesmo com `total_count>1000`. Não repetir a primeira página.
8. Subdividir somente quando ainda faltarem candidatos e ocorrer uma destas condições: as dez páginas acessíveis foram consumidas e `total_count>1000`; ou uma página recebida informou incompletude. Contagem alta isolada nunca aciona divisão proativa. Com até 1000 resultados completos, esgotar a consulta sem divisão.
9. Quando necessário e possível, dividir o intervalo ao meio, em segundos inteiros, e visitar primeiro a metade de criação mais recente. Cada metade repete o mesmo fluxo de busca direta antes de considerar nova divisão. A deduplicação permite aproveitar dados do intervalo pai sem recuperar novamente seus SHAs.
10. Se o intervalo for indivisível, preservar e consumir as páginas acessíveis enquanto faltarem candidatos. Incompletude continua diagnosticada. Emitir `search_saturated` somente se, após o teto de dez páginas, `total_count>1000` e o alvo continuar não atingido. Não emitir esse diagnóstico apenas por contagem alta se, por exemplo, cinco candidatos da primeira página bastarem.
11. A escrita final contém uma linha por ID. Além do teto acessível, combinar consultas por criação não garante ordem global por atualização entre os candidatos; a prioridade da metade de criação recente é apenas fallback, não a estratégia inicial nem uma ordenação global por criação.

Exemplo de funções puras para o contrato de intervalos:

```python
from datetime import datetime, timedelta


def build_query(start: datetime, end: datetime) -> str:
    """Formata a busca para um intervalo UTC com limite final exclusivo."""
    last_second = end - timedelta(seconds=1)
    lower = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    upper = last_second.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"language:TypeScript is:public created:{lower}..{upper}"


def split_interval(start: datetime, end: datetime) -> tuple:
    """Divide um intervalo em metades, retornando primeiro a mais recente."""
    seconds = int((end - start).total_seconds())
    if seconds < 2:
        raise ValueError("O intervalo não pode ser dividido em segundos inteiros.")
    middle = start + timedelta(seconds=seconds // 2)
    return (middle, end), (start, middle)
```

O particionamento é exclusivamente um fallback para alcançar o alvo após o teto acessível ou uma resposta incompleta. Não criar janela inicial de uma hora nem consultas preventivas por dia.

## 6. Tarefas de implementação e delegação

### Tarefa 1 — Base instalável e configuração

**Responsável:** subagente de implementação da base.

**Arquivos:** criar `pyproject.toml`, `.gitignore`, `src/robo_minerador/__init__.py` e `src/robo_minerador/config.py`.

- [x] Confirmar novamente os arquivos existentes e preservar alterações do pesquisador.
- [x] Criar pacote com `setuptools`, layout `src`, Python `>=3.11`, dependência `PyGithub>=2.5,<3` e extra `dev` com `pytest>=8,<10`. A dependência será verificada contra a API efetivamente instalada na tarefa 2.
- [x] Declarar o entry point `robo-minerador = "robo_minerador.cli:main"`.
- [x] Adicionar as constantes da seção 4 e uma função focada em validar data/limite. Usar `date.fromisoformat` e rejeitar valores de limite não positivos.
- [x] Ignorar `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `.env` e `results/`. Não criar um arquivo `.env` contendo token.
- [x] Instalar o pacote em ambiente virtual do projeto usando os comandos da seção 9. Não executar ainda o entry point, pois a CLI depende da tarefa 4.
- [x] Retornar alterações e resultado da instalação para revisão do coordenador. Não criar testes triviais para constantes ou importações.

### Tarefa 2 — Consultas e cliente GitHub

**Responsável:** subagente da integração, após a revisão da tarefa 1.

**Arquivos:** criar `src/robo_minerador/search.py` e `src/robo_minerador/github_api.py`.

- [x] Implementar `build_query` e `split_interval` conforme a seção 5, mantendo o módulo sem rede ou acesso a disco.
- [x] Criar cliente autenticado com `Auth.Token`, timeout explícito de 30 segundos e fechamento garantido da conexão.
- [x] Implementar uma função `fetch_search_page(client, query, page)` que retorne o dicionário original da busca, incluindo `incomplete_results`. Usar o requester do PyGithub para `GET /search/repositories` caso o objeto paginado de alto nível não exponha esse campo. Conferir a assinatura na versão instalada antes de codificar.
- [x] Enviar `q`, `per_page=100`, `page`, `sort=updated` e `order=desc`, conforme a revisão implementada. Uma página é uma operação de rede separada; planejamento e deduplicação pertencem a `discovery.py`.
- [x] Implementar `resolve_commit_sha(client, full_name, default_branch)` consultando o commit atual da branch padrão e retornando seu SHA.
- [x] Usar o tratamento de rate limit do PyGithub: ao atingir um limite temporário identificado pela biblioteca, aguardar a liberação indicada pelo GitHub e continuar, informando a espera no terminal. Essa espera foi explicitamente aprovada pelo pesquisador. Manter as tentativas limitadas de transporte, sem repetir indefinidamente falhas não recuperáveis; não classificar automaticamente todo HTTP 403 como rate limit.
- [x] Habilitar mensagens de espera de forma restrita ao logger de retry, sem ativar logs HTTP detalhados ou criar arquivo de log. Se as tentativas forem esgotadas, informar no diagnóstico os cabeçalhos de limite/reset disponíveis, sem autenticação. Outros erros podem terminar a execução parcialmente; não criar mecanismo de rotação de tokens.
- [x] Conferir nomes e docstrings em revisão, sem teste isolado de cada wrapper de API.

### Tarefa 3 — Escrita JSONL simples

**Responsável:** subagente de persistência; pode trabalhar em paralelo à tarefa 2 após a tarefa 1.

**Arquivo:** criar `src/robo_minerador/jsonl.py`.

- [x] Implementar `write_jsonl(path, records)` com criação da pasta pai e escrita UTF-8 de `json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"` para cada registro.
- [x] Não escrever indentação multilinha, array externo, JSON secundário ou resumo separado.
- [x] Manter a política de substituição de saída na CLI, não dentro do serializador. Esta primeira versão não implementará retomada nem anexação de execuções.
- [x] Verificar a serialização com registros dos dois tipos da seção 4 e leitura com `json.loads`, sem criar um teste automatizado dedicado a esse wrapper trivial. Não salvar o exemplo como resultado real de mineração.

### Tarefa 4 — Orquestração da descoberta e CLI

**Responsável:** subagente da descoberta, após revisão das tarefas 2 e 3.

**Arquivos:** criar `src/robo_minerador/discovery.py`, `src/robo_minerador/cli.py` e `src/robo_minerador/__main__.py`.

- [x] Implementar `collect_repositories(client, start, end, limit)`, sem escrita de arquivos. Injetar o cliente para permitir respostas simuladas sem autenticação real.
- [x] Separar funções para percorrer intervalos/páginas, montar o registro do candidato, consolidar um ID já visto e criar diagnóstico. Não juntar acesso à rede, persistência e interpretação de argumentos em um único método.
- [x] Usar pilha ou recursão com intervalos em segundos; visitar o mais recente primeiro. Aplicar o limite global após deduplicar o ID, conforme a seção 5.
- [x] Recuperar SHA apenas na primeira observação do ID. Se falhar, preservar o candidato com `sha_status="unavailable"`, SHA `null` e diagnóstico `sha_unavailable` associado.
- [x] Preservar os registros já coletados em caso de falha da busca; terminar com `run_finished` parcial ou falho. Não converter exceções de programação em uma falsa conclusão bem-sucedida: somente falhas esperadas da integração podem ser registradas como falhas operacionais.
- [x] Criar CLI com subcomando `discover` e opção `--limit`. Não adicionar subcomandos vazios das próximas etapas.
- [x] Antes de qualquer busca, se a saída já existir, interromper com mensagem em português e sem alterá-la. Disponibilizar `--overwrite` para substituição intencional após validar a configuração; não anexar execuções.
- [x] Ler configuração, coletar, escrever o único JSONL e mostrar no terminal o caminho e o resultado. Retornar código 0 para `completed`, 1 para execução parcial/falha de API ou persistência e 2 para erro de argumentos/configuração ou saída existente.
- [x] Se houver erro de escrita, relatar no terminal e retornar 1; não tentar criar outro arquivo para registrar a falha do próprio disco.
- [x] Criar `__main__.py` somente delegando a `main()` sob a guarda `if __name__ == "__main__"`.
- [x] Não imprimir token ou despejar a requisição autenticada em mensagens de erro.

### Tarefa 5 — Verificações estritamente necessárias

**Responsável:** subagente de verificação, após a tarefa 4, com revisão independente do fluxo de coleta.

**Arquivo:** criar `tests/test_discovery.py` somente com os cenários da seção 8.

- [x] Implementar cliente/respostas simuladas locais ao teste, usando `monkeypatch` nas duas operações de rede do módulo da descoberta. Não adicionar dependência de mocking ou chamadas reais ao GitHub.
- [x] Executar cada cenário da seção 8 e corrigir os defeitos demonstrados, sem ampliar a suíte por meta de cobertura.
- [x] Usar datas com timezone UTC e IDs/SHAs fictícios claramente identificados como dados de teste.
- [x] Executar `python -m pytest tests/test_discovery.py -q` no ambiente virtual. Resultado histórico do coordenador: 9 casos aprovados; após a revisão, 16 casos aprovados também na checagem final do coordenador, sem rede.
- [x] Fazer revisão funcional da ligação entre CLI, coletor e escritor; verificar manualmente configuração inválida e recusa de sobrescrita.
- [x] Reportar separadamente o que foi verificado com simulação e o que foi verificado com API real.

### Tarefa 6 — README, inspeção real e fechamento da descoberta

**Responsável:** subagente de documentação, com revisão e fechamento pelo coordenador.

**Arquivo:** atualizar `README.md`. Saída real, quando executada: `results/01_descoberta.jsonl`.

- [x] Corrigir os links para a especificação atual e este plano. Descrever o estado real da implementação, sem declarar futuras etapas prontas.
- [x] Documentar instalação, `GITHUB_TOKEN`, execução, `--limit`, `--overwrite`, constantes e caminho fixo da saída.
- [x] Explicar no README o corte obrigatório por criação desde 2025, atualização decrescente solicitada à API, busca direta antes do fallback, teto por consulta, resultados incompletos e vieses. Repositório recente pode conter código antigo importado.
- [x] Explicar cada campo, os dois tipos de registro, os estados de execução e como distinguir candidatos de diagnósticos. Documentar que contagem de linhas não equivale à de repositórios.
- [x] Mostrar um exemplo ilustrativo de linha e um comando de inspeção que não produza outro arquivo.
- [x] Remover da descrição vigente a dependência de SQLite e a exigência de três execuções. Explicar que o protocolo aprovado exige uma execução da suíte na futura validação; a descoberta não executa testes de candidatos.
- [x] Explicar que os próximos arquivos serão enxutos e ligados por IDs; somente a construção final consolidará os atributos. Manter as seis etapas na visão geral.
- [x] Verificar a coleta real pequena com limite 5, inspecionar o JSONL real do pesquisador e conferir pelo menos um SHA e a data de criação contra o GitHub. Evidências acima: arquivo existente preservado e reprodução adicional em memória, sem nova escrita pela CLI.
- [x] Registrar a indisponibilidade inicial de credencial e orientar a configuração local sem solicitar o token na conversa. Essa pendência foi posteriormente resolvida pela verificação real relatada acima.
- [x] Revisar todos os arquivos alterados: convenções de idioma, docstrings, responsabilidade única, exclusões não autorizadas, vazamento de credenciais e ausência de saídas secundárias.
- [ ] Apresentar resultado e limitações ao pesquisador. Antes da triagem, revisar a saída e atualizar o plano dessa etapa. Se houver ambiguidade, parar e perguntar.
- [x] Registrar a aprovação da busca direta por atualização decrescente antes do fallback por criação; janela inicial de uma hora rejeitada. Executar a revisão abaixo, sem novo gate de aprovação desta decisão.
- [ ] Quando houver autorização para commit, conferir `git status`, diff e histórico recente; incluir somente arquivos intencionais. Criar um único commit de implementação, com mensagem sugerida `add GitHub repository discovery`. Não versionar token nem coleta local automaticamente.

### Revisão aprovada — busca direta antes do particionamento

Implementação e revisões de aderência e qualidade concluídas no mesmo checkout, seguindo as regras da seção 2. A medição real em memória e o README foram atualizados sem sobrescrever `results/01_descoberta.jsonl` do pesquisador. Os itens abaixo registram a revisão atual, separadamente da implementação histórica; a checagem final local do coordenador foi concluída.

- [x] **API — `src/robo_minerador/github_api.py`:** acrescentados `sort="updated"` e `order="desc"` a toda busca, preservando os demais parâmetros, a resposta original e a política de rate limit; implementação revisada.
- [x] **Coletor — `src/robo_minerador/discovery.py`:** implementada e revisada a sequência da seção 5: aproveitamento imediato, até dez páginas, parada ao atingir o alvo e fallback somente por necessidade. Preservados diagnósticos de incompletude mesmo com limite atingido, SHAs iniciais e fronteiras de `search.py`; `run_started` registra `direct_search_then_partition` / `updated_desc`.
- [x] **Testes mínimos — `tests/test_discovery.py`:** cenários adaptados e revisados; **16 casos aprovados relatados**. As regressões cobrem limite 5 na primeira página com contagem alta, 1000 sem fallback, 1001 com fallback após dez páginas e parâmetros reais da API. Permanecem paginação, IDs únicos, fronteiras, consultas de origem, SHA inicial, incompletude na primeira página e em páginas posteriores, saturação indivisível, falhas de SHA/busca e propagação de erros de programação. Sem rede real; não reexecutados nesta atualização documental.
- [x] **Revisões de aderência e qualidade:** concluídas para a implementação e os testes da otimização, conforme relato de execução.
- [x] **Medição real em memória:** uma coleta real com limite 5 concluída em **5,934444 s**, com uma busca e cinco consultas de SHA, conforme evidência acima. Arquivo do pesquisador preservado, sem artefatos secundários.
- [x] **README final — `README.md`:** atualizado com estratégia, corte obrigatório desde 2025, limites da ordenação, regressões atuais, medição nova e comparação cautelosa com o histórico. Preservadas as políticas de saída única, ausência de banco e substituição somente por opção explícita.
- [x] **Checagem final do coordenador:** conjunto revisado, 16 casos aprovados e diff sem erros de whitespace; coleta real medida e arquivo anterior preservado.
- [ ] **Próxima etapa:** revisar saída e plano antes da triagem; commit somente com autorização explícita.

## 7. Dependências entre subagentes

```text
Base e configuração
  -> Consultas/API ────────┐
  -> Escrita JSONL ────────┤
                          -> Orquestração/CLI
                              -> Verificação necessária
                                  -> README/inspeção/fechamento
```

A redação preliminar do README pode ocorrer em paralelo após os contratos estarem definidos. Ajustes finais dependem do comportamento realmente verificado. Não permitir que dois subagentes editem o mesmo arquivo simultaneamente.

## 8. Justificativa e cenários mínimos de testes

Os cenários iniciais originaram nove casos históricos. A suíte revisada contém **16 casos**, contando parametrizações, aprovados nas revisões e na checagem final do coordenador. Os cenários críticos abaixo justificam essa suíte, sem meta de quantidade ou cobertura: fronteiras de paginação, deduplicação, saturação e exceções não são reproduzidas de forma confiável por uma coleta real pequena. Foram reutilizados testes e fixtures existentes, sem testes separados para constantes, getters ou cada campo serializado.

### Cenário A — Paginação, IDs únicos e SHA preservado

Simular primeira página com IDs 1 e 2, segunda com ID 2 repetido e ID 3, e terceira com ID 4. Configurar limite 3, contagem de busca que exija paginação e SHAs fixos por ID.

Verificar:

- somente IDs 1, 2 e 3 aparecem como candidatos;
- ID 2 aparece uma vez e seu SHA é recuperado uma vez;
- a terceira página não é consultada depois de atingir o limite;
- cada SHA retornado corresponde ao valor fixo simulado;
- diagnósticos de execução não consomem o limite.

**Risco:** limite por linhas ou por páginas produz quantidade errada e duplicatas; recuperar SHA novamente pode alterar a versão silenciosamente. Inspeção de uma coleta pequena sem repetição não demonstra essas propriedades.

### Cenário B — Busca direta e fallback somente após o teto

Adaptar os testes existentes para três casos de contagem:

1. `total_count>1000`, primeira página com pelo menos cinco IDs únicos e limite 5: uma única chamada de busca, cinco candidatos, sem divisão nem `search_saturated`.
2. `total_count<=1000`, alvo não atingido na primeira página: paginar até esgotar ou atingir o alvo, sem divisão.
3. `total_count>1000`, limite 1001: fornecer dez páginas completas de 100 IDs únicos antes de permitir a divisão. As metades incluem IDs já coletados e o ID adicional necessário, resultando em 1001 IDs únicos, sem nova recuperação dos SHAs iniciais.

No caso de fallback, reutilizar a fixture de intervalo de quatro segundos com candidatos no segundo imediatamente anterior à divisão e exatamente na divisão. Incluir essas fronteiras nas respostas consumidas, sem criar um teste separado.

Verificar:

- nenhuma subdivisão ocorre antes de aproveitar as dez páginas completas, salvo incompletude;
- somente no fallback, a metade de criação recente é visitada primeiro;
- nenhum segundo é perdido ou incluído nas duas metades;
- candidatos das duas fronteiras são preservados;
- a consulta original fornece até dez páginas e nunca uma página 11;
- o limite 1001 é alcançado sem duplicatas e preservando consultas de origem e SHAs iniciais.

**Risco:** uma execução pode aparentar sucesso enquanto omite candidatos de fronteira ou nunca supera 1000. Conferir algumas linhas de saída não identifica os repositórios silenciosamente perdidos.

### Cenário C — Coleta incompleta e falhas observáveis

Reutilizar os casos de incompletude na primeira página e em página posterior: preservar seus itens e o diagnóstico; se o limite for atingido, não dividir e manter `partial`; se faltar, recuperar candidatos pelas metades sem duplicatas nem alteração dos SHAs iniciais. Manter falha de SHA e falha de busca após página bem-sucedida. Para saturação indivisível, fornecer dez páginas completas e alvo ainda não atingido; contagem alta com limite 5 já satisfeito pertence ao caso direto, sem diagnóstico de saturação.

Verificar:

- os diagnósticos específicos aparecem no mesmo conjunto de registros;
- dados já recuperados são preservados;
- nenhuma execução limitada por esses eventos é marcada como `completed`;
- falha de SHA preserva candidato com `null` e `unavailable`;
- o resultado não declara licença aprovada nem aplica exclusão científica por erro de API.

**Risco:** erro operacional transformado em exclusão científica ou coleta incompleta apresentada como completa. A API real não oferece forma confiável de provocar essas condições em uma verificação pequena.

### Regressão mínima dos parâmetros da API

Implementado um único teste pequeno em memória do requester de `fetch_search_page`: confere `sort="updated"`, `order="desc"`, `per_page=100`, `page`, consulta com intervalo obrigatório de criação desde 2025 e preservação do corpo original. Não acessa rede nem credenciais. **Risco:** testes do coletor substituem a operação de rede e, sozinhos, não detectam retorno acidental à ordenação padrão ou alteração dos parâmetros reais de seleção. Não ampliar para uma suíte por wrapper.

## 9. Comandos de execução e inspeção

Os comandos abaixo são para PowerShell, executados na raiz do projeto. Usar o executável do ambiente virtual diretamente, sem exigir ativação.

### Preparação

```powershell
python --version
Test-Path -LiteralPath "."
python -m venv ".venv"
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Esperado: Python 3.11 ou superior e instalação concluída. O token deve ser configurado no ambiente local e estar disponível como `GITHUB_TOKEN`; não incluí-lo em exemplos persistidos nem na saída do terminal.

### Verificações locais

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py -q
.\.venv\Scripts\python.exe -m robo_minerador --help
.\.venv\Scripts\python.exe -m robo_minerador discover --help
git diff --check
```

Esperado: cenários aprovados e ajuda mostrando `discover`, `--limit` e `--overwrite`; diff sem erros de whitespace.

### Coleta pequena

```powershell
.\.venv\Scripts\python.exe -m robo_minerador discover --limit 5
```

Se houver saída anterior que o pesquisador autorize substituir:

```powershell
.\.venv\Scripts\python.exe -m robo_minerador discover --limit 5 --overwrite
```

Esperado: um único `results/01_descoberta.jsonl`, com até cinco registros `repository`, metadados de execução e eventuais diagnósticos. Sem limitações de API, havendo candidatos suficientes, exatamente cinco candidatos e código 0.

### Inspeção sem arquivo secundário

```powershell
.\.venv\Scripts\python.exe -c "import json; from pathlib import Path; rows=[json.loads(line) for line in Path('results/01_descoberta.jsonl').read_text(encoding='utf-8').splitlines()]; repos=[r for r in rows if r['record_type']=='repository']; print('Repositories:', len(repos)); print('Unique IDs:', len({r['repository_id'] for r in repos})); print('Diagnostics:', len(rows)-len(repos)); print(json.dumps(rows[-1], ensure_ascii=False))"
```

Esperado: contagens de candidatos e IDs únicos iguais; registro final de execução com estado coerente. Conferir também licença preliminar, criação desde 2025 e SHA de um candidato com as ferramentas de leitura disponíveis. Não usar o comando de inspeção como substituto da análise dos diagnósticos.

## 10. Critérios de conclusão

- [x] Pacote instalado e ajuda do comando da descoberta verificada localmente; integração real verificada separadamente conforme evidências acima.
- [x] Data mínima e máximo permanecem configuráveis; busca direta aproveita até dez páginas e só divide com alvo não atingido após o teto ou incompletude, conforme código e testes revisados.
- [x] Toda busca usa `sort=updated`, `order=desc`; fallback visita a metade de criação recente primeiro, sem filtro por estrelas nem promessa de ordenação global além do teto.
- [x] SHA é preservado ou sua indisponibilidade é explícita nos cenários simulados.
- [x] Há exatamente um JSONL de saída: o arquivo real do pesquisador foi inspecionado, com candidatos e metadados distinguíveis. Falhas são distinguíveis pelos diagnósticos verificados nos testes simulados; a coleta real observada terminou sem falhas.
- [x] O limite conta IDs únicos e não é afetado por diagnósticos.
- [x] Falhas e resultados incompletos não são apresentados como coleta plenamente concluída.
- [x] Somente os testes necessários foram criados, com justificativas desta seção e da seção 8.
- [x] Código em inglês, comentários/docstrings em português e funções com responsabilidade única.
- [x] README atualizado após a revisão descreve a estratégia aprovada, suas limitações e a execução única futura da suíte.
- [x] Pendência inicial da integração real comunicada por ausência de credencial e posteriormente resolvida, conforme relato da tarefa de verificação.
- [x] Integração real verificada com coleta pequena, inspeção do arquivo real existente e conferência de SHA e data; reprodução adicional em memória, sem sobrescrever o arquivo do pesquisador.
- [x] Nova medição real em memória após a otimização registrada separadamente da evidência histórica, preservando o arquivo do pesquisador.
- [x] Subagentes utilizados e resultados revisados pelo coordenador, conforme relato de execução.
- [x] Checagem final do coordenador após a revisão executada e registrada: 16 casos aprovados em 0,43 s e `git diff --check` sem erros de whitespace.
- [ ] Antes da triagem, saída revisada, ambiguidades esclarecidas e plano da próxima etapa atualizado.

## 11. Roteiro das etapas seguintes

| Etapa | Entrada | Saída única | Decisões que deverão ser esclarecidas antes de implementar |
| --- | --- | --- | --- |
| Triagem | Descoberta, filtrando `record_type=repository` | `results/02_triagem.jsonl` | Como tratar SHA indisponível, evidências de licença e formatos de configuração suportados. |
| Extração | Aprovados na triagem e metadados de origem | `results/03_extracao.jsonl` | Auxiliar estrutural, limites de sintaxe, associação documental e resolução direta de chamadas. |
| Validação | Instâncias candidatas e contexto | `results/04_validacao.jsonl` | Frameworks suportados, escolha do ambiente e comandos originais; uma execução completa, mínimo de quatro casos válidos. |
| Deduplicação | Instâncias validadas e referências anteriores | `results/05_deduplicacao.jsonl` | Representação exata comparada e forma de consolidar proveniências. |
| Construção | Arquivos anteriores ligados por IDs | `results/06_dataset.jsonl` | Esquema final completo, caracterização e parâmetros da amostra manual. |

Esses itens delimitam o roteiro, sem autorizar decisões antecipadas sobre ambiguidades das próximas etapas. Cada transição exige revisão do material produzido e detalhamento do plano correspondente.

# Guia operacional: descoberta, triagem e extração

Os quatro comandos manuais usam PowerShell 5.1 e o Python 3.12 da `.venv` do
projeto. Podem ser chamados de outro diretório pelo caminho completo do script.
Eles encadeiam os serviços Python existentes e o analyzer HTTP; o CLI integrado
de mineração (T19) ainda não está disponível.

## Comandos manuais principais

Após `uv sync --locked`, execute na raiz do projeto:

```powershell
# 1. Descoberta: intervalo UTC semiaberto de cinco minutos; seleção máxima de 10.
.\scripts\try_discovery.ps1 -CreatedFrom '2024-01-01T00:00:00Z' -CreatedUntil '2024-01-01T00:05:00Z' -MaxRepos 10

# 2. Triagem: consome o último discovery.json válido deste projeto/output.
.\scripts\try_screening.ps1

# 3. Extração: consome o último screening.json válido; funciona sem token.
.\scripts\try_extraction.ps1

# 4. Conjunto: nova descoberta, triagem e extração, com as mesmas funções.
.\scripts\try_first_stages.ps1 -MaxRepos 10
```

Cada script aceita `-Help`. Todos aceitam `-OutputDirectory` (padrão:
`results/manual-stages`, relativo à raiz do projeto). Triagem e extração aceitam
`-InputJson`; para evitar escolher implicitamente outra execução, use os caminhos
impressos pelo comando anterior:

```powershell
.\scripts\try_screening.ps1 -InputJson 'C:\caminho\results\manual-stages\runs\<execution_id>\discovery.json' -MaxRepos 3
.\scripts\try_extraction.ps1 -InputJson 'C:\caminho\results\manual-stages\runs\<execution_id>\screening.json'
```

Se o output foi personalizado, informe o mesmo `-OutputDirectory` nas etapas
dependentes. `-MaxRepos` pode redefinir a seleção antes da primeira triagem; depois
dela, a seleção fica congelada. O limite restringe a **demonstração processada**,
não as consultas de descoberta nem seus indicadores `exhaustive`/`status`.
São escolhidos repositórios completos com SHA, em ordem de `repo_id`.

Se a triagem sofrer interrupção de infraestrutura, repita com o `-InputJson` da
descoberta válida. O runner verifica a proveniência do checkpoint parcial interno,
mantém sua seleção e reconstrói o progresso pelo replay dos serviços: relatórios
concluídos não repetem descoberta nem download. Um JSON `interrupted` não é aceito
como entrada explícita concluída; corrupção ou falha de integridade não é ignorada.

Descoberta/triagem procuram `GITHUB_TOKEN` nesta ordem: processo, `.env`,
`.env.example` (fallback autorizado para estes comandos manuais). Leem somente
a chave, sem executar o arquivo. Uma declaração vazia/malformada interrompe a
busca. Sem token, há prompt oculto apenas em terminal interativo; execução
não interativa falha antes de criar estado ou fazer rede. Nada é gravado de
volta nos arquivos de credenciais.
Sem checkpoint compatível, a CLI informa a etapa faltante e o comando anterior
antes de pedir credenciais. As mensagens usam orientações fixas, sem mostrar
caminhos, conteúdo de arquivos ou texto de exceções.

Cada execução possui diretório único `runs/<execution_id>/`, contendo:

- `discovery.json`: manifesto completo, repositórios/SHA/licença, consultas,
  contagens e referência CAS; `processing_scope` descreve a seleção parcial.
- `screening.json`: configuração/run originais, seleção e relatórios completos,
  avaliações, planos de comandos e referências dos snapshots.
- `extraction.json`: candidatos, código/assinatura e offsets UTF-16, documentação
  original, associações, contexto dos testes e métricas; ou razões explícitas
  de `skipped`/`not_evaluated`.
- `result.json`: checkpoint da última etapa executada, inclusive erro parcial
  classificado por fase/tipo, sem texto da exceção.
- `owner.json`, `state.sqlite3` e `artifacts/`: identidade e evidências locais.

Os JSONs têm `schema_version: "1.0"`, `purpose: "manual"` e
`not_final_dataset: true`. São publicados individualmente por arquivo temporário
e substituição atômica. A publicação dos vários arquivos não é uma transação
global; não há ponteiro global `latest`. A busca automática considera somente
checkpoints compatíveis e íntegros dentro do output/projeto informado. Preserve
todo o diretório junto; referências CAS e SQLite são relativas ao checkpoint.
Checkpoints antigos de `manual-smoke` têm outro formato e não são importados:
inicie uma descoberta com estes comandos.

O gate exige H-R01–H-R04 positivos e plano estático resolvido. Rejeições geram
`skipped`; pendências geram `not_evaluated`. A produção atualmente mantém
H-R03/H-R04 pendentes de execução: ter `commands.status: planned` não é aprovação.
Logo, uma execução real pode legitimamente não produzir candidato algum. Todos
os resultados mantêm `accepted: false`, H-R05 pendente, `runtime_confirmed: false`
e `coverage: null`. Não representam instâncias aceitas de dataset.

Docker só é consultado se houver entrada aprovada com contexto/configuração
estática suportados. Prepare o analyzer deste projeto quando necessário:

```powershell
docker compose --profile analyzer up -d --wait analyzer
# Se os fontes/versões mudaram, build explícito:
docker compose --profile analyzer build analyzer
docker compose --profile analyzer up -d --wait analyzer
# Alternativa explícita pelo runner, apenas se houver entrada apta:
.\scripts\try_extraction.ps1 -BuildAnalyzer
```

O runner recusa analyzer ambíguo, não saudável ou com fontes/versões divergentes.
Após uma extração concluída, repetir com o JSON de triagem ou de extração verifica
offline o CAS completo, snapshots, plano, requisição, contexto, projeção e metadados
congelados antes de retornar o mesmo JSON. Esse replay não exige token, Docker,
health check ou nova transferência. Os metadados são evidência histórica da análise,
não uma afirmação sobre o estado atual do daemon; corrupção continua sendo recusada.
O HTTP fica privado, via Docker exec/Node fetch. Um helper temporário sem rede e
sem token transfere/verifica os bytes do CAS no volume compartilhado, sob
`/workspace/snapshots/manual-stages/<run_id>/<tree_hash>`; o analyzer lê esse volume
read-only. O prefixo `/workspace/snapshots` respeita a restrição do serviço atual.
Nenhum install/build/script/suíte de repositório é executado. Workspace ambíguo,
framework indefinido, tsconfig ausente/com extends/references/JSONC e fontes de
teste ausentes resultam em `not_evaluated`, sem inventar configuração.

Verificação controlada (sem GitHub):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_manual_stages.py -m 'not docker' -q
.\.venv\Scripts\python.exe -m pytest tests/integration/test_manual_stages.py -m docker -q
```

O teste Docker usa fonte da fixture `vitest-basic`, sem a matriz `versions/`, com
escopo tsconfig explícito para quatro alvos; registra aprovações de engenharia
como `source_kind: fixture`. Passa pelos mesmos gate, plano, transferência,
`AnalysisClient`, `ExtractionService` e preservação de contexto, sem promover
avaliações de um repositório GitHub real. O código de saída é 0 para checkpoint
completo, 2 para argumentos/token ausentes ou nenhum checkpoint compatível,
3 para parcial/infraestrutura e 4 para falha de integridade (inclusive checkpoint
explícito corrompido). Confira também as razões por repositório.

## 1. O que cada etapa entrega

| Etapa | Entrada | Saída e interpretação |
|---|---|---|
| Descoberta | Recorte de criação e API GitHub | Manifesto, consultas, respostas e SHA observado de cada repositório. Confira `status`, `exhaustive` e `warnings`. |
| Triagem | Repositório descoberto no SHA fixado | Snapshot, licença, README e plano estático de comandos. `eligible = false` e execução ainda `not_evaluated`. |
| Extração | Snapshot, triagem, plano congelado e caminho visível ao analyzer | Funções/métodos, associações estáticas a testes, documentação, métricas e referência de contexto. A elegibilidade permanece `not_evaluated`. |

Uma coleta visa um dataset, que poderá conter muitas instâncias elegíveis.
`per_page = 100` é tamanho de página, não limite de repositórios nem de
instâncias. Não existe parâmetro `max_instances`. Contagem de candidatos extraídos
não é estatística de instâncias aceitas: essa decisão depende da Validação,
incluindo T13, T15 e os gates pendentes de T14.

## 2. Preparar o ambiente

- Docker Desktop em modo **Linux containers**, daemon ativo e imagens
  `linux/amd64`.
- Python do host pode ser 3.14; o projeto usa **Python 3.12.9 gerenciado pelo uv**.
- Para testes Node no host: **Node 22.22.3** e npm **10.8.2**. TypeScript 5.8.3 e
  ts-morph 26.0.0 são instalados pelo lock do analyzer.
- Acesso à rede para instalar dependências e construir imagens. Os testes com
  entradas controladas não consultam o GitHub nem consomem sua quota.

```powershell
python --version
python -m uv --version
python -m uv python install 3.12.9
python -m uv sync --frozen --all-groups
python -m uv run python --version
node --version
npm.cmd --version
Test-Path -LiteralPath "tools/ts-analyzer"
npm.cmd ci --prefix tools/ts-analyzer
docker info --format '{{.OSType}}/{{.Architecture}}'
docker compose --profile analyzer config --quiet
```

Se `python -m uv` não existir, instale a versão usada pelo ambiente de pesquisa:
`python -m pip install uv==0.13.5`. O resultado de `uv run python --version`
deve indicar 3.12.9; não use o Python 3.14 diretamente para os testes do projeto.
O Docker pode exibir `x86_64` como arquitetura do daemon; as imagens e o Compose
devem selecionar `linux/amd64`.

## 3. Testar as três etapas sem token

Primeiro compile e teste o analyzer. Os testes Python de protocolo, relatórios e
contexto também usam seus arquivos compilados.

```powershell
npm.cmd test --prefix tools/ts-analyzer
npm.cmd run typecheck --prefix tools/ts-analyzer
```

Depois execute por etapa:

```powershell
# Descoberta: partições, autenticação sanitizada, paginação, retries e replay.
python -m uv run pytest tests/unit/test_partitions.py tests/integration/test_github.py tests/integration/test_discovery.py -q

# Triagem: comandos, licenças, snapshots e decisões persistidas.
python -m uv run pytest tests/unit/test_screening.py tests/integration/test_snapshots.py -q

# Extração: protocolo, instrumentação, documentação/métricas e contexto em bytes.
python -m uv run pytest tests/integration/test_analysis_protocol.py tests/integration/test_instrumentation_protocol.py tests/integration/test_context_roundtrip.py tests/integration/test_framework_reports.py -m "not docker" -q
```

Esses testes constroem respostas HTTP e relatórios controlados, usando as APIs
reais de persistência e verificação. Não comprovam uma coleta GitHub nem uma
execução real das suítes Jest/Vitest. CAS e archives temporários ficam no
`tmp_path` do pytest. Alguns testes de symlinks podem ser ignorados no Windows
quando a conta não tem permissão para criá-los; confira o resumo de skips.

## 4. Preparar e conferir a API GitHub

No GitHub, abra **Settings → Developer settings → Personal access tokens →
Fine-grained tokens** e crie um token com expiração e acesso de leitura a
repositórios públicos. O acesso público de leitura não exige permissões de
escrita nem o escopo amplo `repo` de um PAT clássico. Respeite as políticas do
proprietário/organização quando aplicáveis. Este guia usa somente dados públicos.

Informe o token no próprio terminal, ocultando a digitação e evitando gravá-lo
no histórico do comando. Ele ficará em `GITHUB_TOKEN` apenas nesta sessão:

```powershell
$secret = Read-Host "GitHub PAT" -AsSecureString
$credential = New-Object System.Net.NetworkCredential('', $secret)
$env:GITHUB_TOKEN = $credential.Password
$credential = $null
$secret = $null
$headers = @{
    Authorization = "Bearer $env:GITHUB_TOKEN"
    Accept = "application/vnd.github+json"
    'User-Agent' = "robo-minerador-manual"
    'X-GitHub-Api-Version' = "2026-03-10"
}
$rate = Invoke-RestMethod -Uri "https://api.github.com/rate_limit" -Headers $headers
$rate.resources.core
$rate.resources.search
```

Confira `remaining` e `reset` (Unix timestamp UTC). Para confirmar a identidade
autenticada, opcionalmente use
`(Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $headers).login`.
Não mostre `$headers`, não registre o token em logs/arquivos e não o envie ao chat.

Uma consulta opcional, de **uma única página**, ajuda a estimar o recorte:

```powershell
$q = [uri]::EscapeDataString('language:TypeScript created:2024-01-01T00:00:00Z..2024-01-01T00:59:59Z fork:true')
$search = Invoke-RestMethod -Uri "https://api.github.com/search/repositories?q=$q&per_page=1&page=1" -Headers $headers
$search | Select-Object total_count, incomplete_results
```

Isso é um diagnóstico de contagem, não a descoberta metodológica completa.
Busca e API core têm quotas diferentes. `401` normalmente indica token inválido;
`403` pode indicar bloqueio/permissão ou rate limit; `429` indica limitação.
Confira `Retry-After`, `X-RateLimit-Remaining` e `X-RateLimit-Reset` antes de repetir.
O cliente do projeto diferencia esses casos, limita tentativas a 3 e espera
acumulada a 900 segundos na configuração piloto; uma resposta permanente não
recebe retries indefinidos. Os comandos `Invoke-RestMethod` acima são chamadas
manuais, sem essa política automática.

## 5. Descoberta e triagem reais pelas APIs Python

O piloto aprovado em `config/pilot.toml` cobre **[2024-01-01, 2024-01-02) UTC**.
O exemplo abaixo cria uma **nova coleta de demonstração**, restrita explicitamente
à primeira hora desse dia, sem alterar o TOML aprovado. Mesmo esse recorte pode
gerar múltiplas consultas e downloads. A busca pode subdividir partições; o GitHub
limita cada busca a 1.000 resultados, e o manifesto registra incompletudes.

O exemplo executa descoberta e triagem estática no host, sem instalar dependências
ou executar código dos projetos coletados. Os caminhos `/workspace` e `/app` do
TOML são referências de contêiner; `load_config` permite lê-los no host, enquanto
`validate_local_references` exige que existam no ambiente em que for chamado.

```powershell
@'
import json
import os
from pathlib import Path
import httpx
from robo_minerador.config import DiscoveryConfig, load_config
from robo_minerador.discovery.github import GitHubClient
from robo_minerador.discovery.service import DiscoveryService, load_manifest
from robo_minerador.screening.service import ScreeningService, load_report
from robo_minerador.state.artifacts import ArtifactStore
from robo_minerador.state.store import StateStore

token = os.environ["GITHUB_TOKEN"]
config = load_config("config/pilot.toml")
scope = config.discovery.model_dump(mode="json")
scope.update(created_from="2024-01-01T00:00:00Z", created_until="2024-01-01T01:00:00Z")
config = config.model_copy(update={"discovery": DiscoveryConfig.model_validate(scope)})
root = Path("results/first-stages")
root.mkdir(parents=True, exist_ok=True)
artifacts = ArtifactStore(root / "artifacts")
with StateStore(root / "state.sqlite", artifacts=artifacts) as state:
    with httpx.Client(base_url="https://api.github.com", timeout=60, trust_env=False) as http:
        github = GitHubClient(config.discovery, http=http, token=token)
        discovery = DiscoveryService(github, state, artifacts).discover(None, config)
        manifest = load_manifest(artifacts, discovery.digest)
        print("run_id:", discovery.run_id)
        print("discovery:", discovery.digest, manifest["status"], manifest["warnings"])
        reports = []
        for repo in manifest["repositories"]:
            if repo["status"] != "complete" or not repo.get("commit_sha"):
                continue
            result = ScreeningService(github, state, artifacts).screen(
                discovery.run_id, repo, config.execution
            )
            report = load_report(artifacts, result.digest)
            reports.append(result.digest)
            print(repo["full_name"], result.digest, report["assessments"])
        (root / "references.json").write_text(json.dumps({
            "run_id": discovery.run_id,
            "discovery": discovery.digest,
            "screening": reports,
        }, indent=2), encoding="utf-8")
'@ | python -m uv run python -
```

Guarde `references.json`, SQLite e todo o CAS juntos. Para retomar a mesma coleta,
use o `run_id` salvo no lugar de `None`, mantendo exatamente a configuração.
`None` cria outra coleta. Alterar entradas congeladas exige um novo run.
`load_manifest` e `load_report` verificam evidências salvas sem acesso ao GitHub;
retomar uma etapa ainda incompleta pode exigir rede. Um manifesto terminal
`partial` não passa a `complete` apenas por ser relido.

## 6. Extração HTTP real com fixture conhecida

Construa a imagem atual e inicie apenas o analyzer. O Compose não publica sua
porta na internet e monta `miner-workspace` como somente leitura no serviço.

```powershell
docker compose --profile analyzer build analyzer
docker compose --profile analyzer up -d --wait analyzer
docker compose --profile analyzer ps
docker compose --profile analyzer exec -T analyzer node -e "fetch('http://127.0.0.1:3000/health').then(r=>r.json()).then(console.log)"
python -m uv run pytest tests/docker/test_analysis_service.py -m docker -q -s
```

O teste copia `tests/fixtures/projects/static-symbols` por Docker archive API para
um diretório de snapshot no volume compartilhado. Um helper temporário, sem rede,
faz a escrita; o analyzer continua com montagem read-only. O teste faz um POST
real a `/v1/analyze` dentro do contêiner, com `root` apontando para esse diretório,
`framework = vitest` e `tsconfig_paths = ["tsconfig.json"]`. Confere saúde/versões,
4 alvos, 5 associações diretas estáticas, 4 documentos e 4 métricas.
`runtime_confirmed` permanece `false`. Nenhuma configuração do projeto é executada
pela análise estática. Para parar o serviço, use
`docker compose --profile analyzer stop analyzer`.

### Integrar um snapshot real à extração

O checkpoint Python é `ExtractionService.extract(...)`, em
`src/robo_minerador/extraction/service.py`. O chamador precisa fornecer:

1. `run_id` e `Snapshot` da triagem (`load_snapshot(artifacts, snapshot_digest)`).
2. `screening_digest` do mesmo repositório/SHA.
3. `EnvironmentPlan` e seu `plan_artifact`, gerados por `plan_environment` e
   `persist_plan`, com os bytes originais e o mesmo plano de comandos da triagem.
4. `analyzer_root`: caminho **dentro do analyzer**, contendo aquele snapshot
   imutável no volume compartilhado; um caminho Windows do host não serve.
5. `framework` (`jest` ou `vitest`) e `tsconfig_paths` relativos ao snapshot.

`AnalysisClient(base_url)` chama `/v1/analyze`; a extração verifica os bytes dos
alvos contra o CAS e preserva o contexto. `framework_version` é obrigatório quando
há `InstrumentationClient`. `prepared` e `external_dependency_roots` permitem
informar a preparação verificada quando disponível. Sem ela, a referência original
continua preservada, mas reprodução e elegibilidade ficam pendentes. O teste
`test_context_roundtrip.py` mostra entradas congeladas, persistência e replay
executáveis; seu transporte HTTP controlado não é uma prova de coleta real.

Os comandos manuais acima compõem essas entradas de forma conservadora, sem
implementar o pipeline T19. Não há comando `miner extract` ou export final de dataset.

## 7. Catálogo Docker e provas de runtime opcionais

Os IDs SHA-256 de `config/environments.lock.json` identificam imagens locais
efetivamente construídas; não são tags de registry para baixar ou retaggear.
Confira a receita disponível:

```powershell
python -m uv run python scripts/freeze_environments.py --help
```

Em um daemon novo, ou quando for necessário reconstruir os runtimes, a receita é
`python -m uv run python scripts/freeze_environments.py --root .`. Ela baixa bases,
constrói miner/runners, verifica versões offline e só então publica o lock.
Esse comando **reescreve o catálogo gerado** e pode produzir novos IDs reais.
Use as versões aprovadas em `config/environments.json`, sem substituí-las.
O analyzer é construído separadamente pelo Compose e não integra os runtimes
desse lock. Um rebuild somente dele não exige congelar novamente o miner.

Para T10/T11, use fontes atuais sobre a imagem miner congelada. Assim, uma imagem
miner antiga não faz o teste passar usando código antigo. Após preparar o catálogo,
construir o analyzer e executar `npm.cmd test` no host:

```powershell
$source = (Get-Location).Path
$miner = (python -m uv run python -c "import json; print(json.load(open('config/environments.lock.json'))['runtimes']['miner']['image_id'])").Trim()
docker image inspect $miner --format '{{.Os}}/{{.Architecture}}'
docker run --rm --platform linux/amd64 --mount "type=bind,source=$source,target=/source,readonly" --mount "type=volume,source=miner-evidence,target=/evidence" --mount "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock" --workdir /source -e PYTHONPATH=/source/src -e ROBO_T11_EVIDENCE=/evidence/t11 $miner python -m pytest -p no:cacheprovider tests/integration/test_framework_runtime.py tests/docker/test_direct_calls.py -m docker -q
```

Esses testes exigem Docker e podem instalar dependências em contêineres. Não
recebem `GITHUB_TOKEN`. A matriz comprovada, os modos e as limitações estão em
[framework-support.md](framework-support.md). São provas de engenharia com
fixtures, distintas da Validação de um repositório coletado.

## 8. Resultados e encerramento

- Coletas reais: `results/first-stages/` no exemplo; `workspace/`, `results/` e
  artefatos gerados são ignorados pelo Git.
- Objetos CAS recebem nomes SHA-256 em runtime. Eles pertencem aos resultados
  locais/volumes, não às fontes versionadas. Preserve-os para replay.
- Fontes legíveis, locks e JSON controlados de teste podem ser versionados;
  exports históricos e artefatos hash de execução não devem entrar nas fixtures.
- Ao interpretar resultados, confira decisões e motivos, não apenas exit code
  ou quantidade de alvos. `not_evaluated` expressa uma avaliação pendente.

Remova a credencial ao terminar:

```powershell
Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
$headers = $null
```

As chamadas reais ao GitHub das seções 4 e 5 exigem seu token e sua execução.
Os testes controlados e o smoke Docker não substituem essa verificação.

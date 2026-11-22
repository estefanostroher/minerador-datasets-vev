# Docker Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Executar com subagentes neste checkout, conforme autorização do usuário, com revisão de spec seguida de qualidade. Nenhum subagente pode fazer commit. Haverá um único commit somente após execução manual, aprovação do resultado e autorização do usuário.

**Goal:** Executar etapas individuais ou o pipeline completo pelo serviço Compose `minerador`, com quantidade opcional de repositórios e continuação verificável após resultados parciais.

**Architecture:** Um coordenador Python traduz seletores em chamadas sequenciais à CLI existente, em subprocessos. Uma imagem contém as ferramentas do minerador e acessa o daemon Linux do host pelo socket. O instrumento dinâmico é transferido para volume Docker exclusivo, montado readonly nos containers da suíte.

**Tech Stack:** Python/argparse/subprocess, pytest, Docker Engine Linux, Docker Compose v2, Git, Node.js >=22, TypeScript 6.0.3.

**Spec:** `docs/superpowers/specs/2026-11-16-docker-pipeline-design.md`.

---

## Mapa de arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `src/robo_minerador/pipeline.py` | Parsing, ordenação, execução e resumo do pipeline. |
| `src/robo_minerador/pipeline_status.py` | Conferência das publicações parciais com leitores existentes. |
| `tests/test_pipeline.py` | Contratos do coordenador, com execução de etapas simulada. |
| `tests/test_pipeline_status.py` | Publicações válidas/inválidas, usando fixtures dos formatos existentes. |
| `src/robo_minerador/validation_docker.py` | Transferência e montagem do instrumento em volume próprio. |
| `src/robo_minerador/validation_instrument.py` | Publicação do instrumento pelo executor após geração de settings. |
| `src/robo_minerador/validation_workspace.py` | Handle do volume do instrumento. |
| `tests/test_validation_docker.py` | Regressões de montagem, integridade e ownership. |
| `tests/test_validation_instrument.py` | Atualização dos dublês da ponte, se referenciam o bind anterior. |
| `tests/test_pipeline_docker.py` | Smoke test opt-in da execução em container e do instrumento irmão. |
| `Dockerfile`, `compose.yaml`, `.dockerignore` | Ambiente de execução e persistência. |
| `README.md` | Preparação, exemplos e interpretação dos resultados. |

## Task 1: Contrato dos argumentos e seleção

- [x] Criar testes de parsing antes da implementação; executar `.\.venv\Scripts\python.exe -m pytest tests/test_pipeline.py -q` e confirmar falha por módulo ausente.
- [x] Usar `argparse`, com `nargs="?"`, `const=config.MAX_REPOSITORIES` e `default=None` para `--discover` e `--all`. Converter N com tipo que recusa valores não positivos por `ArgumentTypeError`.

```python
@pytest.mark.parametrize("argv,limit", [
    (["--discover", "100"], 100),
    (["--discover"], config.MAX_REPOSITORIES),
    (["--all", "100"], 100),
    (["--all"], config.MAX_REPOSITORIES),
])
def test_discovery_limit(argv, limit):
    args = parse_arguments(argv)
    assert (args.discover if args.discover is not None else args.all) == limit
```

- [x] Acrescentar testes de `0`, negativos, texto, ausência de seletor, `--all --triage`, `--workers` e `--limit` recusados com 2; `--help` retorna 0.
- [x] Definir ordem fixa `discover`, `triage`, `extract`, `validate`, `deduplicate`, `build-dataset`. Seletores combinados retornam apenas os selecionados nessa ordem. Construir argv de descoberta como `['discover', '--limit', str(N)]`; demais etapas recebem apenas seu nome e eventualmente `--overwrite`.
- [x] Executar novamente os testes do arquivo e confirmar aprovação. A CLI original mantém seus argumentos atuais.

## Task 2: Executor sequencial e proteção das saídas

- [x] Testar recusa de uma saída selecionada existente antes de qualquer chamada de etapa, inclusive se for a última etapa do conjunto. Usar `tmp_path` e constantes monkeypatched, nunca `results/` real.
- [x] Implementar `main(argv=None)` com código inteiro de saída e guarda `raise SystemExit(main())`. Separar parsing, seleção e execução em funções pequenas. Não importar `pipeline` na CLI original.
- [x] Implementar `run_stage(argv)` com subprocesso e streams herdados; usar o seguinte formato de comando:

```python
command = [sys.executable, "-m", "robo_minerador", *argv]
```

- [x] Manter handle do processo para cancelamento: na primeira interrupção encaminhar SIGINT onde suportado e aguardar sem prazo; uma segunda interrupção força terminate/kill. No Windows, encaminhar SIGBREAK. Retornar 1 e não iniciar a etapa seguinte; cobrir as esperas e o fechamento, inclusive com processo real no Windows.
- [x] Testar execução de todas as etapas, seleção combinada e interrupção em código 2 ou término anormal. Registrar no terminal as etapas e o código final.
- [x] Rodar `.\.venv\Scripts\python.exe -m pytest tests/test_pipeline.py -q` e `.\.venv\Scripts\python.exe -m robo_minerador.pipeline --help`.

## Task 3: Continuação após parcialidade íntegra

- [x] Inspecionar as fixtures e APIs dos leitores: `load_discovery`, `load_triage`, `load_extraction` e `validate_output_records`. Reutilizar builders de testes já existentes para construir publicações mínimas válidas.
- [x] Criar `tests/test_pipeline_status.py` cobrindo fechamento parcial válido, `failed`, JSON truncado, fechamento ausente, contagens incoerentes e estado incompatível com a etapa. Confirmar falha antes de adicionar o módulo.
- [x] Implementar `pipeline_status.accept_partial(stage, output)` como validação fechada: somente as quatro primeiras etapas podem continuar; chamar o leitor correspondente e verificar o estado validado `partial`. Para validação, ler JSONL integral e chamar `validate_output_records`. Erros de leitura/validação retornam falso ao coordenador, com mensagem de interrupção.
- [x] Calcular SHA-256 dos bytes em streaming antes e depois do filho; retorno 1 só permite continuação se a saída antes não existia ou o hash mudou, e o leitor conferir toda a publicação parcial. Revisão: metadados podem mudar sem novos bytes; recusar também uma republicação legítima com bytes idênticos.
- [x] Testar a matriz de transições no coordenador: `partial` válido seguido de sucesso termina com 1; parcial seguido de recusa termina com 2; parcial inválido interrompe com 1; deduplicação/construção com 1 sempre interrompem.
- [x] Rodar `.\.venv\Scripts\python.exe -m pytest tests/test_pipeline.py tests/test_pipeline_status.py -q`.

## Task 4: Transporte do instrumento pelo daemon

- [x] Acrescentar regressões em `tests/test_validation_docker.py`: fonte temporária interna nunca aparece em bind; volume é do executor; destino `/instrument` é readonly na suíte; upload ou conferência malsucedidos impedem execução; `close()` recolhe volume em sucesso e falha. Cobrir locks em create/start e cancelamento, além de corrupção real dos bytes.
- [x] Introduzir `instrument_volume: str | None = None` em `ValidationWorkspace`. Atualizar as referências ao handle do instrumento, inclusive dublês de testes. Manter `register_instrument_directory` para ownership do temporário Python.
- [x] Criar método `publish_instrument(workspace, root)` no executor. Exigir workspace próprio, inventariar arquivos regulares do instrumento confiável, construir tar com caminhos relativos POSIX e reservar volume por `_volume()`. Criar container administrativo com a receita original, volume do instrumento gravável em `/instrument`, copiar tar por `docker cp -`, executar verificação Node dos hashes esperados e remover o container administrativo em `finally`. O volume permanece registrado para limpeza mesmo se upload/verificação falhar.
- [x] Estender `_create` com `instrument_volume` e opção administrativa `writable_instrument=False`; recusar volume alheio. Construir montagem conforme o contrato:

```python
mount = "type=volume,src=" + instrument_volume + ",dst=/instrument,volume-nocopy"
if not writable_instrument:
    mount += ",readonly"
args += ["--mount", mount]
```

- [x] Em `configure_instrument`, manter a geração e os hashes atuais; após gravar `settings.json`, publicar e armazenar o nome do volume no workspace antes de liberar `bootstrap_argv`. Na suíte, `_create` recebe apenas esse volume readonly, não um bind do temporário.
- [x] Conferir os hashes de todos os arquivos transferidos, incluindo settings, antes de liberar a suíte. Preservar o cálculo anterior de `instrument_id`, que exclui settings por definição, e o hash próprio de settings.
- [x] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_validation_docker.py tests/test_validation_instrument.py -q`; localizar com Glob o arquivo da ponte se seu nome diferir. Executar também os testes que referenciam `instrument_mount`, localizados por Grep, e atualizar o contrato de forma consistente.

## Task 5: Imagem, Compose e exclusões

- [x] Criar Dockerfile multi-stage usando bases oficiais Debian bookworm: Node 22, Docker CLI e Python 3.12 slim. Copiar Node/npm e o binário Docker para o estágio Python e instalar Git e certificados. Instalar o pacote com pip e executar `npm ci --omit=dev --prefix /app/src/robo_minerador/typescript`. Manter o pacote e seu auxiliar no mesmo local por instalação editável na imagem, ou instalar wheel e executar npm no diretório do pacote instalado; escolher uma única forma e comprovar o caminho pelo preflight.
- [x] Configurar `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `WORKDIR /app` e entrada `python -m robo_minerador.pipeline`.
- [x] Criar `compose.yaml` com a configuração abaixo:

```yaml
services:
  minerador:
    build: .
    init: true
    environment:
      GITHUB_TOKEN: ${GITHUB_TOKEN:-}
    volumes:
      - ./results:/app/results
      - ./.cache/robo-minerador/snapshots:/app/.cache/robo-minerador/snapshots
      - /var/run/docker.sock:/var/run/docker.sock
```

- [x] Criar `.dockerignore` excluindo `.git`, `.venv`, `**/__pycache__`, `.pytest_cache`, `.cache`, `results`, `build`, `dist`, `**/*.egg-info`, `**/node_modules`, `.env` e `.env.*`. O lockfile do auxiliar permanece incluído.
- [x] Executar `docker compose config --quiet`. Antes do build, conferir `Test-Path -LiteralPath "."` e disponibilidade do daemon com `docker info`. Com daemon disponível, executar `docker compose build` e `docker compose run --rm minerador --help`.
- [x] Conferir versões dentro da imagem por entrypoint alternativo Python, usando subprocess para `git --version`, `node --version`, `npm --version`, `docker --version` e preflight do auxiliar. Não executar coleta real para testar build.

## Task 6: Smoke test Docker e documentação

- [x] Criar smoke test opt-in, ativado por `ROBO_MINERADOR_DOCKER_TESTS=1`, que inicia a imagem contra daemon Linux com diretórios temporários para resultados/cache. Usar imagem por digest da receita fixture existente para o container irmão, sem candidato GitHub.
- [x] Pelo coordenador containerizado, publicar instrumento fixture com arquivo de conteúdo conhecido em volume próprio. Pelo container irmão, ler esse arquivo e conferir hash; verificar no inspect que a montagem é readonly e que não há socket nem token. Conferir limpeza após execução e após corrupção real na conferência.
- [x] Verificar escrita em `/app/results` e leitura pelo diretório temporário do host. Não montar `results/` de produção neste teste.
- [x] Atualizar README: requisitos Docker Desktop Linux/Compose, token via ambiente, primeiro comando com `--build`, comandos de todas as etapas, seletores combinados, quantidade padrão, overwrite e códigos 0/1/2. Ajustar a afirmação antiga de que Python necessariamente roda no host, mantendo a execução direta documentada.
- [x] Executar testes Python afetados, testes Node do auxiliar e `git diff --check`. Se o contrato do executor atingir outras suítes de validação, executá-las também. Ampliar para a suíte Python completa se surgirem regressões ou dependências não cobertas.
- [x] Registrar no plano os comandos executados e resultados observados. O daemon estava disponível para build e smoke.

## Revisão do plano

### Revisões finais e verificações do coordenador

- As tasks 1–6 foram implementadas por subagentes no checkout autorizado. A revisão final da spec (`ses_e19598e77ffep4ug7sNRhxv6Zc`) e a revisão de qualidade (`ses_e1956b970ffeOi3mHhFDYegxIH`) foram **APPROVED** após correções de novidade por SHA/touch, SIGBREAK e cleanup, locks em create/start e cancelamento, corrupção real, token nos exemplos extract/all do README e exclusão de `dist`.
- Verificação reportada para tasks 1–4: **182 passed, 15 skipped**, mais três fixtures Docker reais aprovadas. Testes sintéticos cobrem parsing e status; cancelamento inclui processo real Windows com SIGBREAK, primeira espera sem prazo e segunda interrupção forçada. A transferência cobre upload, conferência, locks e cancelamento.
- Verificação do coordenador: `$env:ROBO_MINERADOR_DOCKER_TESTS='1'; .\.venv\Scripts\python.exe -m pytest tests/test_pipeline.py tests/test_pipeline_status.py tests/test_pipeline_docker.py tests/test_validation_docker.py -q`: **60 passed, 4 skipped, 28.16 s**.
- Verificação do coordenador: `npm test` no diretório do auxiliar: **114 passed, 4 skipped, 1612 ms**; `docker compose config --quiet`: sucesso.
- A suíte Python completa anterior excedeu 20 minutos acima de 87%, sem falhas reportadas até o timeout; não foi concluída e não está aprovada.
- [ ] Executar a validação manual e obter aprovação do usuário; somente depois, com autorização, criar um único commit futuro. Nenhum commit ou staging foi feito.

### Evidências das tasks 5–6 — 2026-11-16

- `docker info`: Docker Desktop Linux x86_64, servidor 29.6.1, cgroup v2; `Test-Path -LiteralPath "."`: `True`.
- `docker compose config --quiet`: sucesso; `docker compose build`: sucesso, imagem `robo-minerador-minerador:latest`, manifesto final `sha256:a6f53ec98cb693be4cc08de029e2e7789327f2c0265469d7528fe30b11cca5b7`. `docker compose run --rm minerador --help`: sucesso, sem coleta.
- Preflight dentro da imagem: Python 3.12.16, Git 2.39.5, Node 22.23.5, npm 10.9.9, Docker CLI 29.10.0; auxiliar em `/app/src/robo_minerador/typescript`, TypeScript 6.0.3.
- `ROBO_MINERADOR_DOCKER_TESTS=1` com `pytest tests/test_pipeline_docker.py -q`: **2 passed, 6.81 s** após o build final. O smoke usa diretórios temporários, token fictício, imagem da receita original por digest e containers/volumes próprios; verifica persistência, preflight, bytes, montagem readonly, ausência de token/socket no irmão e limpeza após sucesso e falha de conferência.
- `pytest tests/test_pipeline.py tests/test_pipeline_status.py -q`: **36 passed, 8.17 s**; `pytest tests/test_validation_docker.py -q`: **22 passed, 4 skipped, 16.15 s** (fixtures reais anteriores exigem outro opt-in).
- `npm test --prefix .\src\robo_minerador\typescript`: **114 passed, 4 skipped**, 1.635 s. `git diff --check`: sucesso, com avisos Git de conversão LF/CRLF.
- Baseline anterior informado: suíte completa ultrapassou 20 minutos e alcançou 87% sem falhas; validação com 182 passed, 15 skipped e três fixtures Docker reais aprovadas. Não houve coleta GitHub nem execução sobre população real nesta verificação. Validação manual e aprovação explícita continuam pendentes; nenhum commit ou staging foi feito.

- Interface, defaults, overwrite e ordem: tasks 1–2.
- Continuação após parcialidade e códigos finais: task 3.
- Instrumento entre containers e limpeza: task 4.
- Ferramentas, persistência, token e socket: task 5.
- Integração real Docker e instruções de uso: task 6.

Nenhuma tarefa requer sobrescrever os resultados reais ou alterar critérios científicos. O daemon estava disponível nas verificações de build e smoke registradas acima.

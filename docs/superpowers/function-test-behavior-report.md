# Política função–testes: implementação e replay verificado

## Resultado medido

O replay offline de **todas as 190 instâncias** do 04 preservado produziu uma publicação separada, aceita por `validate_output_records` após escrita e reabertura:

| Decisão individual | 04 histórico | Replay política 2 |
|---|---:|---:|
| approved | 0 | **4** |
| rejected | 3 | **3** |
| not_evaluated | 187 | **183** |

O fechamento novo registra seis repositórios: zero approved, dois rejected e quatro not_evaluated. A etapa continua `partial`, com seis diagnósticos operacionais rederivados de impedimentos/pendências obrigatórias salvos. A aprovação individual não é confundida com aprovação global da suíte.

**Não houve nova passagem de Vitest, instalação, execução de candidatos ou piloto.** O replay rederivou os critérios das fases, ambiente, plano, associações e streams originais. A afirmação nova é sobre o comportamento observado do par função–testes; não certifica ausência de serviços nem propriedades amplas do programa.

## Quatro aprovações

| Origem / instância | Antes | Depois | Casos válidos |
|---|---|---|---:|
| Anur `0e60090700d343eb06ae2d7bc0d18531f3ccdd1add61cc44c8116a41cff213b3` | pending | approved | 11 |
| macrotracker `7f322382e852b39a77c740d10988b6abe6b34dffbdd2b8a627e63628c9cbd5b3` | pending | approved | 12 |
| macrotracker `43e49b48cebbc9c7321394312ed995d060e960cdcdc31f08e137f38be7064147` | pending | approved | 13 |
| macrotracker `0719b126f7e343559fb5cdcc7e01c5ac21e263aa46d08e2b273caa3ca1bd41bf` | pending | approved | 12 |

As quatro contagens são `lower_bound`, não contagens totais exatas. Todos os quatro critérios individuais foram rederivados como approved. Os respectivos `case_evidence` permaneceram **idênticos** aos originais, incluindo declaração, identidade runtime, chamada direta, entrada original, resultado positivo e vínculo ao instrumento. O algoritmo percorre todas as instâncias; não contém estes quatro IDs como exceções.

As outras 186 instâncias mantiveram suas decisões. O relatório JSON contém a transição, os quatro critérios, a contagem e a comparação das testemunhas de cada uma das 190 instâncias.

## Código e contrato

- `validation_evaluation.py`: política explícita 2 e critérios função–testes, fora dos modelos de recursos. O caminho histórico permanece disponível sem mudar seus critérios.
- `validation.py`: novas execuções usam formato v3/política 2. A auditoria automática foi removida desse caminho, inclusive o acesso ao hook opcional. Falha/indisponibilidade/timeout do helper não pode cancelar execução ou publicação porque ele não é chamado.
- `validation_format.py`: valida a política na abertura, exige critérios coerentes e rederiva provas locais/metadados. O leitor v3 não executa o helper de recursos. A leitura v1/v2 conserva os gates históricos, sem migração automática.
- `validation_evidence.py`: encaminha a seleção explícita de política, conservando o protocolo de eventos v1.
- `validation_replay.py`: produz publicação separada pelas mesmas regras reais de avaliação e leitura; não troca status manualmente. Registra política/hash/caminho/fechamento de origem e `candidate_execution=false`.
- `tests/test_validation_pair.py`: regressões específicas da política nova; `tests/test_validation.py` permite selecionar explicitamente a política histórica nas fixtures existentes.

Diagnósticos de recursos antigos são rotulados `historical_not_recertified`, fora de `criteria`; sua decisão não muda aprovação/rejeição/pendência individual. Não há exigência de modelos v3 nem de análise de programa/plugins/configurações completos. Diagnósticos operacionais históricos ficam identificados separadamente; os diagnósticos obrigatórios novos vêm das evidências reavaliadas.

Ambiente reproduzível, locks, instalação, preparação e comando original continuam obrigatórios. Falhas associadas, retries com falha e hooks pertinentes continuam locais; falhas alheias, inclusive no mesmo arquivo, não são herdadas. Erro do instrumento não fabrica aprovação. Receitas e escopo não foram ampliados.

Desenho autorizado: [specs/2026-11-08-function-test-behavior-design.md](specs/2026-11-08-function-test-behavior-design.md). Plano executado: [plans/2026-11-08-function-test-behavior.md](plans/2026-11-08-function-test-behavior.md).

## Artefatos reais e reprodução

Diretório: `C:\Users\Estefano\AppData\Local\Temp\opencode`.

| Artefato | Nome |
|---|---|
| Backup byte a byte do 04, já criado e conferido | `04_validacao.before-function-test-pair.backup.jsonl` |
| Publicação v3 separada, já reaberta/validada | `04_validacao_pair_replay.jsonl` |
| Relatório das 190 transições | `function-test-pair-replay-report.json` |
| Script offline usado | `function_test_pair_replay.py` |

SHA-256 do replay publicado:

```text
9328bb779904e2811cb17c5bca2b0ff4018b36bfe9a338e6938512f92adfe70d
```

A reavaliação final foi registrada em `2026-11-08T17:30:55.262465+00:00`. Os timestamps de comandos/eventos salvos continuam originais; esse horário identifica o replay, não uma execução de teste. A origem preservada termina em `2026-11-08T16:06:12.770472Z`.

Comando **já executado**, na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe "C:\Users\Estefano\AppData\Local\Temp\opencode\function_test_pair_replay.py"
```

O script calcula hashes em streaming, cria o backup se ausente, lê JSON estrito, chama `replay_pair_records`, publica o arquivo separado, reabre com parsing estrito e executa o leitor portátil. Não importa nem executa módulos candidatos. Uma repetição atualiza apenas os artefatos de replay em TEMP, com novo horário/hash; o backup existente precisa conferir com a origem.

## Preservação comprovada

Hashes conferidos antes e depois do replay final:

```text
results/04_validacao.jsonl
8583e67d25988767df885bc56de30e335a1ae74f92deaa8838bafb0f8004dcf0

results/03_extracao.jsonl
c9b95889dbf54af10826134b9ae033f6d087edf698220b64d837acbe3018890d

results/03_extracao-original.jsonl
0ada94ae6000a42c78652d432b2bb35d3075d2b44f62d17056feafc9f5c34187
```

O backup confere com o hash do 04. Não houve escrita nos results/snapshots originais, commits, push, branch, worktree, subagentes ou review posterior.

## Verificação

As regressões novas foram observadas falhando antes da implementação: seleção de política ausente, módulo de replay ausente e aceitação indevida de metadados/proveniência adulterados. O conjunto focal final passou:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_validation_pair.py tests/test_validation.py tests/test_validation_local.py tests/test_validation_evidence.py tests/test_validation_cli.py tests/test_validation_input.py tests/test_validation_recipe.py tests/test_validation_resources.py tests/test_validation_resources_local.py -q --tb=short
# 284 passed, 11 skipped in 49.46s
```

Inclui 27 casos da política nova, publicação/reabertura, recursos unknown/rejected/desabilitados, helper com exceção/timeout que não é chamado, quatro/três casos, mock sem entrada, ausência de execução, skips/todos, retries com falha, hooks, erro do instrumento, falha alheia no mesmo arquivo, limite inferior e adulteração de política/provas/origem/metadados/proveniência. As fixtures históricas mantêm seus gates de recursos. Os skips não representam comprovação nova da matriz. Não houve alteração TypeScript, nova execução Node ou Docker nesta tarefa.

`git diff --check` retornou 0 para os arquivos rastreados, com avisos LF/CRLF do checkout. `python -m robo_minerador validate --help` retornou 0 e confirmou a opção `--overwrite`, sem `--workers`.

## Próxima execução real, se desejada

O replay já verifica as quatro aprovações acima com as evidências existentes. Uma execução futura pode produzir resultados diferentes e deve preservar o 04 anterior. A CLI mantém:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $previous = '.\results\04_validacao.jsonl'
    if (Test-Path -LiteralPath $previous) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $id = [guid]::NewGuid().ToString('N')
        Copy-Item -LiteralPath $previous -Destination ".\results\04_validacao.$stamp.$id.backup.jsonl" -ErrorAction Stop
    }
    .\.venv\Scripts\python.exe -m robo_minerador validate --overwrite
}
```

Esse bloco de execução real **não foi executado** nesta tarefa. `validate` não aceita `--workers`.

# Instance Validation Implementation Plan

**Objetivo:** avaliar chamadas e passagem dos testes associados por instância, mantendo reprodução e mínimo de quatro casos.

**Arquitetura:** esquema 2 para decisões locais; esquema 1 conserva semântica histórica. Repositório mantém diagnóstico global. A etapa 1 publicou gate global provisório; a etapa 2 o substitui por fechamento local versionado, preservando leitura da política histórica.

**Tecnologias:** Python/pytest e adaptador Node/Vitest existente.

- [x] Separar pré-requisitos compartilhados de resultado global em `validation_evaluation.py`; rederivar fechamento de emissores e passagem de declarações associadas/hooks dos módulos.
- [x] Separar testemunha existencial de contagem exata; não creditar streams corrompidos, colisões, emissores abertos ou associações limitadas.
- [x] Identificar erro confiável de finalização em `validation-runner.mjs`; preservar pendência operacional sem fabricar término de caso.
- [x] Versionar publicação em `validation.py` e rederivação portátil em `validation_format.py`, preservando leitura histórica v1.
- [x] Exercitar falha global alheia, falhas locais/hooks/retry, limite inferior, interrupção e adulteração com fixtures controladas; executar testes focais Python e Node sem Docker/candidatos.
- [x] Documentar interface sequencial de auditoria local para implementador 2 no adendo da spec.

Execução pelo IMPLEMENTADOR1 no checkout atual, sem agentes adicionais, commits ou review, conforme autorização expressa.

## Verificação da primeira etapa

```text
.\.venv\Scripts\python.exe -m pytest tests/test_validation_local.py tests/test_validation.py tests/test_validation_evidence.py tests/test_validation_cli.py tests/test_validation_input.py tests/test_validation_recipe.py tests/test_validation_resources.py tests/test_validation_docker.py -q
237 passed, 12 skipped in 34.97s
```

```text
node --test src/robo_minerador/typescript/tests/validation-plan.test.mjs src/robo_minerador/typescript/tests/validation-transform.test.mjs src/robo_minerador/typescript/tests/validation-resources.test.mjs src/robo_minerador/typescript/tests/validation-vitest.test.mjs
# tests 12
# suites 0
# pass 8
# fail 0
# cancelled 0
# skipped 4
# todo 0
# duration_ms 470.2086
```

`node --check src/robo_minerador/typescript/validation-runner.mjs`: código 0, sem saída. Integrações Docker opt-in permaneceram skipped; não constituem comprovação nova da matriz Vitest. Fixtures Python exercitam coordenador/escritor/leitor reais com fronteiras de execução controladas. `git diff --check`: código 0, somente avisos de LF/CRLF em arquivos de trabalho preexistentes. O Python global não possui pytest; os testes usam a `.venv` existente.

Pendências sequenciais: auditoria local de recursos e atualização final do README pelo implementador 2. Erro de finalização do runner classificado como operacional, sem reparo especulativo do ciclo de vida. Nenhum resultado/snapshot foi reescrito e nenhum candidato foi executado.

## Etapa 2 — auditoria local autorizada

Implementação inline pelo IMPLEMENTADOR2, sem agentes adicionais ou review. O fechamento parte do alvo e dos módulos associados, inclui manifesto/configuração efetiva, setups, hooks, imports locais e configurações de compilação aplicáveis. Modelos Node/Vitest existentes permanecem intactos para leitura histórica; modelo local separado exige isolamento forks, configuração efetiva e implementação Vite hasheada. Não há divisão intrarquivo: todos os efeitos compartilhados e corpos do módulo associado são analisados conservadoramente.

- [x] Acrescentar testes do produtor local, independência de arquivo alheio, hooks/config/imports e adulteração portátil.
- [x] Produzir metadados efetivos de configuração/isolamento e binding da implementação Vite sem alterar a seleção original.
- [x] Implementar fechamento AST e replay local com fontes compartilhadas; integrar executor, coordenador, avaliador e leitor.
- [x] Preservar leitura v1 e v2 provisório pela política publicada; novas execuções usam `associated_closure_v1`.
- [x] Demonstrar integração em fixture própria e executar testes focais ampliados; atualizar documentação.

### Verificação final da etapa 2

```text
.\.venv\Scripts\python.exe -m pytest tests/test_validation_local.py tests/test_validation.py tests/test_validation_evidence.py tests/test_validation_cli.py tests/test_validation_input.py tests/test_validation_recipe.py tests/test_validation_resources.py tests/test_validation_docker.py tests/test_validation_resources_local.py -q
263 passed, 13 skipped in 52.52s
```

```text
node --test src/robo_minerador/typescript/tests/validation-plan.test.mjs src/robo_minerador/typescript/tests/validation-transform.test.mjs src/robo_minerador/typescript/tests/validation-resources.test.mjs src/robo_minerador/typescript/tests/validation-vitest.test.mjs
# tests 12
# pass 8
# fail 0
# skipped 4
# duration_ms 495.4868
```

```text
$env:ROBO_TEST_DOCKER = '1'; .\.venv\Scripts\python.exe -m pytest tests/test_validation_resources.py::test_real_resource_producer_with_original_snapshot_and_vitest -q -s
2 passed in 75.01s (0:01:15)
```

As duas variantes Docker usaram Node 22.23.4 / Vitest 4.1.10 / Vite 7.3.6 e produtor, materialização, instrumento, avaliador, escritor e leitor reais. Ambas publicaram instância `approved`, quatro casos válidos e `portable_roundtrip=true`. Na segunda, a suíte original terminou com exit 1 por teste alheio; o fechamento local incluiu `.npmrc`, `package.json`, configuração, setup, alvo e teste associado. O código de serviço real alheio não foi executado. Os outros testes de serviço usam evidência nativa controlada, sem acessar API remota.

Testes adicionais recusam binding Vite sem versão/hash, raiz global não observada ou com setup diferente, fontes/imports/setups/efeitos adulterados e redução de fechamento mesmo após atualizar as referências por hash. v1 e v2 provisório permanecem legíveis com suas políticas históricas. Os skips são integrações opt-in fora da bateria padrão; não certificam a matriz inteira nessa execução.

`git diff --check`: código 0, somente avisos LF/CRLF preexistentes. `validate --help`: apenas `--help` e `--overwrite`, sem workers. Nenhum full gate, candidato, piloto, resultado/snapshot protegido, branch/worktree/commit/push ou novo review nesta etapa.

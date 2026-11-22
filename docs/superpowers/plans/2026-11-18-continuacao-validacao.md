# Continuação após validação sem aprovadas

> **For agentic workers:** Execute inline com executing-plans e test-driven-development, preservando as alterações existentes.

**Goal:** Chegar à deduplicação e ao dataset vazio quando a validação publicar um resultado íntegro `failed`.

**Architecture:** Alterar somente a política de continuação do coordenador. Conferir o JSONL inteiro com o leitor existente, exigir bytes novos e manter código geral 1 para sinalizar pendências. As decisões científicas e a resolução automática permanecem preservadas.

**Tech Stack:** Python, pytest, Docker Compose.

## Tarefas

- [x] Adicionar regressão em `tests/test_pipeline_status.py` com `published_fixture(fail_all=True, policy_version=2)`: aceitar o fechamento válido `failed`, recusar fechamento ausente e contagens adulteradas.
- [x] Adicionar fixture de cadeia com receita ausente em `tests/dataset_fixtures.py` e integração em `tests/test_pipeline.py`: simular apenas a validação, executar as CLIs reais de deduplicação e construção, verificar dataset vazio e retorno geral 1. Cobrir saída antiga e inválida.
- [x] Executar `.\.venv\Scripts\python.exe -m pytest tests/test_pipeline_status.py tests/test_pipeline.py -q` antes da implementação e conferir as falhas esperadas.
- [x] Em `pipeline_status.py`, aceitar `partial` nas quatro primeiras etapas e também `failed` somente na validação, sempre após conferência integral. Ajustar nome do helper e mensagem em `pipeline.py` para descrever continuação com pendências.
- [x] Reexecutar os testes de pipeline e as CLIs de dataset/deduplicação; atualizar README e contratos antigos com a exceção aprovada, preservando falhas fatais, hashes e estados de origem.
- [x] Conferir Compose, reconstruir a imagem e executar smoke Docker pertinente. Revisar os arquivos tocados sem commit e sem substituir resultados reais.

## Critérios

O estado `failed` continua registrado no 04 e nas origens do 05; nenhuma pendência é promovida. Arquivo antigo, inválido, ausente, sem fechamento ou retorno anormal não autoriza continuação. O comando pode concluir todas as etapas com código 1, indicando pendências, e `06_dataset.jsonl` vazio.

## Evidências

- Antes da implementação: **2 failed, 40 passed** nos testes de pipeline/status. As duas falhas foram a recusa da validação `failed` íntegra e a ausência de execução das etapas finais.
- Após o ajuste: **78 passed** com `pytest tests/test_pipeline_status.py tests/test_pipeline.py tests/test_dataset_cli.py tests/test_deduplication_cli.py -q`.
- `docker compose config --quiet` e `docker compose build`: saída 0.
- `ROBO_MINERADOR_DOCKER_TESTS=1` com `pytest tests/test_pipeline_docker.py -q`: **3 passed**. Inclui cenário novo com publicação sintética `failed` na imagem reconstruída, subprocessos reais de deduplicação/construção e persistência de dataset vazio. A validação desse cenário é simulada; nenhum candidato real foi executado.
- Revisão local: a exceção está restrita à validação após leitura integral; o gate de hash e o resultado geral 1 foram preservados. A documentação anterior foi atualizada para explicitar a exceção aprovada.

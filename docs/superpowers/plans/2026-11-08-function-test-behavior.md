# Implementação da política de comportamento função–testes

Desenho autorizado: [2026-11-08-function-test-behavior-design.md](../specs/2026-11-08-function-test-behavior-design.md).

## Etapas executadas

- [x] Preservar o trabalho existente e localizar os contratos de avaliação, publicação e leitura portátil.
- [x] Escrever regressões para quatro testemunhas com recursos opcionais, limiar três/quatro, falhas locais, integridade e política explícita; observar as falhas antes da implementação.
- [x] Publicar formato v3/política 2 com quatro critérios individuais e recursos exclusivamente diagnósticos; conservar leitura v1/v2.
- [x] Remover auditoria automática do coordenador atual e a dependência do helper de recursos do leitor novo.
- [x] Implementar replay post-hoc separado, usando o avaliador e leitor reais; testar publicação/reabertura e adulteração.
- [x] Guardar backup do 04 em TEMP e reavaliar as 190 instâncias, sem executar candidatos; conferir hashes originais.
- [x] Documentar resultados medidos, limitações e comando de execução futura com backup.

## Verificação proporcional

As fixtures próprias cobrem recursos desconhecidos/rejeitados/indisponíveis, auditoria desabilitada, helper com exceção/timeout, quatro testemunhas, três insuficientes, mocks sem entrada, ausência de execução, skip/todo, retries com falha, hooks, erro do instrumento, falha alheia no mesmo arquivo, limite inferior, origem/metadados/provas adulterados, políticas antigas e proveniência do replay.

Os testes históricos de recursos continuam verificando seu contrato anterior. Não há alteração TypeScript nem execução de matriz Docker/candidatos nesta tarefa. Comandos e contagens finais ficam no [relatório](../function-test-behavior-report.md).

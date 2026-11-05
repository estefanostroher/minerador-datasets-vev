# Snapshot reutilizável e elegibilidade estática

## Estado e aprovação

Contrato aprovado pelo pesquisador em 2026-11-03, após esclarecimentos sobre disco, compartilhamento dos cinco snapshots e consumo pela validação. Implementação com subagentes e piloto limitado às cinco origens da coorte autorizados pelo pedido de prosseguir. Não autoriza execução de candidatos, alteração dos resultados oficiais, coleta ampliada ou commits. Substitui a política conservadora do adendo de contexto por instância sem alterar retrospectivamente coletas existentes.

**Fechamento atualizado após o fix P1 de contexto JS:** contrato implementado, com testes e replay offline mais recentes no [relatório final](../plans/2026-11-04-extracao-v3-evidencias.md). Aderência e qualidade do fix JS estão **APPROVED**, com execução independente dos 92 testes Node pelo revisor de qualidade. A revisão documental final está **APPROVED**, sem achados após resolver o único P2 de status. Entrega autorizada para commit pelo coordenador, sem push; o hash é consultável no histórico Git. A restrição a commits acima registra a autorização inicial.

## Problema confirmado

A aquisição anterior à v3 usava leituras de blobs limitadas a 512 requisições e 8 MiB acumulados, com preservação apenas textual. Binários e extensões desconhecidas geravam pendências. Aquele ajuste deixou o contexto por instância mais preciso para alguns imports, mas também tornou configurações de testes, `process`, globais e carregadores dinâmicos bloqueios de alcance global.

O replay dos dez repositórios manteve 0 aprovações, 141 rejeições e 7.028 pendências. Nenhuma das 191 instâncias antes pendentes somente por contexto foi liberada. Portanto, o problema não se resolve apenas refinando a classificação dos mesmos arquivos incompletos.

Há duas perguntas diferentes:

1. Os arquivos versionados do commit e as evidências estáticas estão preservados?
2. O projeto instala, compila e executa seus testes corretamente, com ambiente e recursos suficientes?

A extração responde à primeira e aos critérios estáticos de seleção. A validação responde à segunda e confirma a execução real da implementação original. `approved` na extração significa candidata à validação, nunca entrada final no dataset.

## Aquisição v3

Usar Git como ferramenta confiável adicional. Recuperar o commit exato em um repositório bare local, sem checkout, filtros, hooks, scripts, submódulos recursivos ou instalação de dependências. Não usar branch como fallback nem aceitar um SHA diferente. Uma operação por repositório/SHA substitui as leituras por blob da extração; descoberta e triagem conservam seus contratos atuais.

Alternativas consideradas:

- Aumentar as leituras da API: mantém custo proporcional ao número de arquivos e o problema de truncamento operacional.
- Baixar um tarball: compacto e simples, mas arquivos produzidos por exportação podem respeitar `export-ignore`; não comprovar completude da árvore apenas porque o download terminou.
- Recuperar objetos Git, opção recomendada: permite conferir commit, árvore, modos e objetos sem materializar um diretório executável. Usa cache em disco e exige Git instalado.

Cache: `.cache/robo-minerador/snapshots/<repository_id>/<commit_sha>/`, contendo `repository.git/`, `manifest.json` e `complete.json`. Publicar somente após verificar commit, conectividade/integridade dos objetos e inventário integral da árvore. O manifesto canônico registra identidade, árvore e entradas ordenadas com caminho, modo, tipo, OID e tamanho. Caminhos Git não representáveis em UTF-8 são preservados em base64 e explicitados como limitação de análise quando pertinentes.

`complete.json` registra o SHA-256 dos bytes canônicos do manifesto. Referências ao cache usam chave lógica, SHA do commit, OID da árvore, versão do formato e hash do manifesto; não dependem de caminho absoluto da máquina. Cache reutilizado deve ser verificado localmente antes do uso. Não anunciar completude para staging interrompido ou objetos ausentes/corrompidos.

O snapshot contém o que está versionado no repositório principal. Ponteiros LFS, links e gitlinks/submódulos são inventariados explicitamente; não representam aquisição dos objetos LFS, conteúdo de submódulos, dependências npm ou arquivos não versionados. Binários regulares são preservados como bytes. Uma dependência dessas estruturas que impeça comprovar a evidência estática continua pendente. Sua mera presença no inventário não reprova todas as unidades.

## Recursos e API

Limites operacionais vigentes: 100 mil entradas; 512 MiB somados nos blobs versionados; 1 GiB lógico por aquisição; 300 segundos por aquisição Git; duas aquisições simultâneas, mesmo com mais workers de análise. Durante fetch, o disco é monitorado por amostragem da soma de `st_size`, não por quota física rígida. Entradas e bytes são conferidos no inventário em streaming após fetch e antes da publicação. Excesso aborta o candidato sem publicar snapshot completo parcial. Não elevar limites silenciosamente. Não há teto total do cache nem limpeza automática; `--overwrite` substitui a saída, não o cache.

A análise mantém heap de 512 MiB e 180 segundos por invocação Node, inclusive no replay estrito, com 32 MiB de texto por projeção. Não é orçamento agregado de repositório, pipeline ou memória Python+Node. Exceder o orçamento não altera a integridade do snapshot, mas impede afirmar completude da análise afetada; registrar a limitação sem aprovar por omissão. Os limites antigos de 512 leituras/8 MiB permanecem na triagem, não na aquisição da extração v3.

Não prometer eliminação de rate limits. PyGithub continua responsável por metadados e seu gate atual; o transporte Git tem limites próprios, cancelamento e no máximo uma tentativa automática de aquisição nesta entrega. Um cache hit íntegro dispensa aquisição e consulta de metadados na extração. O token não é colocado em argumentos, URLs Git, configuração persistente ou manifesto; candidatos públicos são recuperados por HTTPS público. Metadados de identidade/acesso são conferidos na primeira aquisição.

## Fronteira de análise

Manter `preserved_local_context`, com significado explícito: snapshot íntegro do commit e evidências usadas na seleção presentes e verificadas. Não significa que todas as dependências de execução foram instaladas ou que efeitos de configuração foram executados e comprovados.

| Situação | Tratamento v3 |
| --- | --- |
| Fixture ou imagem versionada preservada como bytes | Não gera pendência por não ser texto. |
| Configuração de testes preservada | Analisar referências declarativas possíveis; não bloquear somente pela existência do arquivo. |
| `process.env`, builtin Node ou pacote npm externo | Registrar requisito de execução; não equivale automaticamente a arquivo local ausente. |
| Carregamento dinâmico com comportamento não executado | Registrar requisito/limitação de execução; não exigir prova universal de comportamento. |
| Referência estática necessária para identificar função, teste ou documento não resolvida | Manter o critério afetado pendente. |
| Mock ou substituição comprovada do alvo usado como evidência | Não aprovar essa associação como chamada à implementação original. |
| Snapshot corrompido/incompleto, evidência adulterada ou arquivo usado ausente | Não aprovar contexto. |
| Busca de testes/documentação incompleta por limite do analisador | Não rejeitar por ausência; registrar pendência de análise. |

Configurações/setup preservados devem participar da análise estática de mocks e registros quando suas referências forem resolvíveis. Não confundir associação sintática ao símbolo com comprovação de execução: configurações dinâmicas não executadas e possíveis efeitos de ambiente são requisitos de validação. A futura validação deverá confirmar a chamada efetiva ao alvo original; sucesso da suíte isoladamente não satisfaz esse requisito.

Contexto JavaScript tem AST e símbolos em programa auxiliar independente de `allowJs`, para inspecionar configurações, setups, presets e dependências transitivas mesmo quando o programa original exclui JS. Seleção, resolução científica e testemunhas de chamadas continuam vinculadas ao programa original; o auxiliar não cria evidência científica de testes JS. Fonte necessária sem AST gera `UNANALYZED_TEXT`; sintaxe inválida gera `INVALID_SOURCE`, com impacto localizado ao escopo afetado. As regressões combinam TS/JS, config/setup/preset/transitivo, mock que impede aprovação e controle sem mock que preserva a evidência válida.

Preservar H-F06 e os demais critérios científicos. Não liberar unidades sem documentação, associação direta confiável ou identificação suficiente. A agregação continua `approved`/`rejected`/`not_evaluated`. Somente `approved` segue à validação, sem recuperação automática dos pendentes.

## Publicação — v3

Adotar `schema_version=3` para novas execuções porque a saída passa a depender de um artefato persistente e o critério de contexto muda de significado. Acrescentar `context_policy="versioned_snapshot_static_evidence_v1"` e um `context.snapshot` no repositório com a referência verificável ao cache. Não misturar versões nem converter arquivos existentes automaticamente.

Conservar compactação de motivos/issues, origem compartilhada, snippets, IDs e valores de validação. `context.files` pode continuar contendo textos analisados/evidências para inspeção e expansão; não significa inventário integral. O inventário integral pertence ao manifesto do snapshot. `context_ref="repository"` continua referindo-se à lista de textos publicada, não a todos os binários. Distinguir esse escopo na documentação e no loader.

Adicionar `repository.execution_requirements`, com tipos e caminhos de origem para requisitos observados estaticamente, sempre `status="not_evaluated"`. Tipos permitidos: `test_configuration`, `environment`, `external_dependency`, `dynamic_loading`, `external_resource`, `gitlink` e `lfs_pointer`. Não repetir uma lista global por função, nem transformá-la em confirmação de execução. Presença de um requisito não é, isoladamente, um critério de rejeição. Requisitos e seus contadores são informativos e não entram em `issue_count`. `context.analysis_complete` distingue completude da análise da integridade do snapshot.

Leitura/expansão legada v1/v2 continua disponível, sem tratar antigos aprovados como se possuíssem snapshot v3. O loader v3 verifica snapshot, commit e evidências e falha explicitamente se o artefato não estiver disponível. O cache é um artefato necessário à reprodução, não uma saída científica independente: o JSONL permanece o índice canônico da etapa, mas já não é um pacote autossuficiente. Copiar apenas o JSONL não transporta o snapshot.

`expand_instance` aceita v1/v2/v3 sem buscar artefatos. `load_instance_context(instance, repository, reasons, store)` exige approved v3 e um `SnapshotStore`: verifica evidências, reprojeta e repete a análise confiável, comparando a unidade completa. Não há fallback de rede. A vida útil de um store externo é preservada no encerramento normal do pipeline; abortos cancelam Git e Node. A CLI exige preflight de Git/Node/TypeScript e entrada antes de abrir a saída; workers 1–16, padrão 4. `npm ci` instala somente o auxiliar confiável do minerador.

## Aceite antes de declarar solução

1. Uma fixture controlada completa com configuração, binário e acesso a ambiente chega a `approved`, mantendo execução não avaliada. Uma fixture com associação ou snapshot realmente inválido não chega a `approved`.
2. O mesmo repositório/SHA é adquirido uma vez e reutilizado sem novas requisições de arquivos; SHA divergente ou cache adulterado é recusado.
3. Um piloto autorizado nas cinco origens da coorte de 191 permite inspecionar casos reais de ponta a ponta, sem sobrescrever resultados atuais. O piloto requer aquisição completa; o replay textual antigo não substitui essa evidência.
4. Pelo menos um caso real previamente inspecionado como elegível deve demonstrar a passagem à validação, se seus requisitos forem confirmados. Se nenhum puder ser demonstrado, relatar causas concretas e não declarar o problema de contexto resolvido apenas porque as suítes passaram. Não inventar quota de aprovados nem forçar as 191 aprovações.
5. Comprovar preservação de identidade para unidades compartilhadas entre coletas; novas unidades recuperadas pelo snapshot completo são adições legítimas e devem ser relatadas.

## Fora desta entrega

Implementação da validação dinâmica, instalação de dependências candidatas, execução de suítes, ampliação de H-F06, mudanças na descoberta/triagem, busca automática dos pendentes e coleta de todos os candidatos. O piloto limitado foi autorizado e realizado; uma nova execução oficial depende de autorização e não é iniciada pela documentação.

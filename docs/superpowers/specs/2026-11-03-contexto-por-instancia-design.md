# Pendências de extração por instância

> Histórico: a política conservadora deste adendo foi substituída pelo [contrato de snapshot reutilizável v3](2026-11-03-snapshot-reutilizavel-design.md), implementado e verificado nas [evidências finais de 2026-11-04](../plans/2026-11-04-extracao-v3-evidencias.md). As evidências de zero aprovações abaixo descrevem a implementação anterior e permanecem preservadas.

## Estado

Desenho aprovado pelo pesquisador em 2026-11-03, após esclarecimento dos critérios e do encaminhamento. Implementação autorizada com subagentes. Não autoriza nova coleta, sobrescrita dos resultados ou commit automático.

## Objetivo e evidências

Eliminar bloqueios globais indevidos sem converter incerteza em aprovação. A extração continua estática e usa o SHA imutável da triagem.

A execução iniciada em 2026-11-03T02:46:50Z terminou com dez repositórios, 7.169 instâncias, zero aprovadas, 141 rejeitadas e 7.028 pendentes. Entre as instâncias com testes associados, 191 tinham os outros quatro critérios aprovados e apenas o contexto pendente. Essas contagens descrevem a execução inspecionada, não uma previsão de aprovações após o ajuste.

Há dois bloqueios globais confirmados:

- `typescript/analyze.mjs` usa qualquer issue, fonte inválida ou referência não resolvida do repositório para marcar o contexto de todas as instâncias como incompleto. A mesma condição interfere na conclusão de ausência de documentação e testes.
- `extraction_analyzer.py` recusa qualquer instância com contexto aprovado quando há uma referência não resolvida no resultado do repositório, mesmo que ela pertença a outro módulo.

## Abordagens consideradas

1. **Impacto por instância, com conservação da incerteza — recomendada.** Usa dependências e evidências preservadas para distinguir pendências relevantes, irrelevantes e de alcance desconhecido. Exige corrigir Node e validador Python em conjunto.
2. **Aumentar limites de coleta.** Pode recuperar arquivos necessários, mas mantém o bloqueio por problemas independentes e aumenta o custo da API. Não resolve a causa principal.
3. **Aprovar contexto sempre que houver função e teste.** Reduz pendências, mas ignora dependências ausentes, configurações e recursos necessários. Incompatível com o contrato científico.

## Contexto e relevância

Construir um índice compartilhado de dependências por repositório. Para cada instância, iniciar pelos arquivos da implementação, dos testes diretamente associados e da documentação preservada; percorrer dependências locais transitivas, incluindo reexports e referências triple-slash. Ciclos não devem provocar recursão ilimitada.

Considerar também as configurações TypeScript e manifests efetivos, suas dependências e declarações ambientes que possam afetar a identidade dos símbolos. Pertencer a outro diretório, isoladamente, não comprova independência: arquivos globais, configurações ancestrais e módulos ambientes podem atingir várias unidades.

Classificar cada pendência por seu impacto:

| Impacto | Consequência |
| --- | --- |
| Relevante para a implementação ou evidência usada | Contexto pendente. |
| Comprovadamente independente | Mantém o diagnóstico do repositório; não bloqueia a instância. |
| Alcance desconhecido | Mantém pendência nos critérios que podem ser afetados. |

Não descartar issues apenas pelo código ou pela extensão do caminho. Uma imagem, fixture ou configuração pode ser necessária aos testes. Imports dinâmicos, acesso a recursos e configurações não resolvidos impedem concluir independência quando puderem atingir a instância. Issues sem caminho ou sem classificação segura continuam conservadoras.

O ajuste não precisa reduzir `context_paths`: o snapshot completo pode continuar compartilhado no repository. Distinguir arquivos publicados de arquivos usados na avaliação evita alterar a compactação v2 sem necessidade.

## Critérios e decisão

Separar evidência positiva de completude da busca:

- Implementação identificada e H-F06 mantêm as regras atuais; não ampliar as formas de não trivialidade neste ajuste.
- Documentação explicitamente associada permanece evidência positiva. Ausência só permite rejeição se a busca documental pertinente estiver completa; um documento omitido pode conter um vínculo externo à função.
- Associação direta continua exigindo identidade resolvida, registro comprovado e tratamento dos mocks e modificadores. Uma incerteza em outro teste não bloqueia automaticamente uma associação comprovada; efeitos sobre o alvo ou de alcance desconhecido permanecem impeditivos.
- Não encontrar chamadas não comprova ausência se testes, dependências de resolução ou registros relevantes ficaram fora da análise.
- Contexto aprovado exige que as evidências usadas e suas dependências necessárias estejam preservadas e sem pendências relevantes ou desconhecidas.

A agregação continua: qualquer critério rejeitado resulta em `rejected`; todos aprovados resultam em `approved`; os demais casos resultam em `not_evaluated`. Aprovação autoriza avançar à validação, sem afirmar que a suíte passou ou que existem quatro casos executados.

Somente instâncias `approved` serão encaminhadas à futura validação. Instâncias `not_evaluated` permanecem auditáveis na extração e não provocam recuperação automática pela validação. Uma eventual tentativa de recuperar análises incompletas exige uma operação separada, solicitada pelo pesquisador. Não substituir pendência operacional por rejeição científica apenas para produzir decisões binárias.

## Integração e protocolo

O Node calcula o impacto por instância. O Python deve conferir a coerência usando os caminhos preservados e as dependências publicadas, em vez de simplesmente remover a restrição global atual. Uma referência não resolvida em dependência necessária continua incompatível com contexto aprovado.

Manter o protocolo existente se as informações atuais forem suficientes. Caso seja indispensável publicar evidência adicional de impacto, definir e validar seus campos nos dois lados antes de aceitá-los; o validador não deve aceitar campos arbitrários. Não alterar `instance_id`, critérios, valores de validação, compactação, expansão ou ordenação da publicação.

Todas as issues continuam no repositório, inclusive as que não bloqueiam uma instância. Um repositório ou execução `partial` pode conter instâncias `approved` sem ocultar suas limitações.

## Escopo desta entrega

Alterar o analisador e sua validação Python, acrescentar regressões de seleção/integridade e atualizar a documentação. A coleta mantém limites e comportamento; lacunas reais identificadas na comparação serão relatadas separadamente.

Os arquivos em `results/` serão apenas lidos. A comparação pode reanalisar em memória os conteúdos preservados no JSONL atual, sem GitHub, alteração de SHA, execução de código candidato ou modificação do arquivo. Esse replay não recupera arquivos omitidos e não substitui uma futura execução oficial.

## Verificação

Regressões obrigatórias para os riscos concretos:

1. Função com documentação, lógica e teste comprovados não fica pendente por import ausente de um módulo independente.
2. Dependência direta ou transitiva ausente na implementação ou teste mantém a pendência.
3. Referência triple-slash, declaração ambiente ou configuração relevante não é ignorada por estar fora da cadeia de imports comuns.
4. Omissão de teste ou documento que possa conter evidência impede rejeição por ausência.
5. Mock ou registro incerto relacionado ao alvo continua sem comprovar associação à implementação original.
6. Issue global ou desconhecida mantém a conservação da incerteza.
7. O validador aceita a independência comprovada e recusa contexto aprovado incompatível com dependência necessária ausente.
8. IDs, trechos de código, documentação, associações, referências v2 e expansão permanecem íntegros.

Executar as suítes Node e Python existentes. Na comparação em memória, relatar transições de decisão e bloqueios restantes, com exemplos auditáveis. Não usar quantidade mínima de aprovações como critério de sucesso.

## Limites da implementação verificada

O índice compartilha arestas e fechamentos por raiz. Localiza fontes inválidas, imports/referências de caminho não resolvidos, ambiguidades de resolução e incertezas de teste quando há prova de alcance. Scripts globais, declarações ambientes, configurações e efeitos desconhecidos permanecem conservadores. As buscas negativas continuam exigindo ausência de issues/referências que possam esconder evidência no snapshot.

Configurações reconhecidas de Vitest/Vite, Jest e Mocha, inclusive JSON e campos embutidos em `package.json`, geram `UNPROVEN_RESOURCE_SCOPE` mesmo quando aparentemente completas. A implementação não comprova seus setups, plugins e efeitos. Acesso a arquivos, ambiente `process`, referências globais e carregadores `require`, `module` e `createRequire` também conserva alcance desconhecido. Isso inclui referências não chamadas e pode incluir homônimos locais. Não afirmar que esses acessos foram executados.

Não foram acrescentados campos ao protocolo. As listas existentes incluem dependências de configurações e diagnósticos de ambiente/projetos/recursos. O Python repete o auxiliar confiável quando há contexto aprovado, conferindo grafo, referências, issues e evidências; não confia apenas no grafo declarado pela primeira saída. Cada invocação conserva 512 MiB, timeout de 180 segundos e cancelamento; a conferência aumenta o custo local e não realiza requisições de API.

O replay offline dos dez snapshots atuais preservou as 7.169 unidades e decisões. Nenhuma das 191 candidatas pendentes somente em contexto foi aprovada: todas ainda têm omissões de coleta e recursos/configurações de alcance não comprovado. Isso delimita o resultado desta entrega e não autoriza nova coleta automática. Evidências detalhadas estão no plano de implementação.

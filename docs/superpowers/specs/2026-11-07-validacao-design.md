# Validação dinâmica de instâncias TypeScript

## 1. Estado e objetivo

Desenho e spec escrita aprovados pelo pesquisador na conversa de planejamento de 2026-11-07. O pesquisador autorizou a elaboração do [plano de implementação](../plans/2026-11-07-validacao.md), reforçando testes somente quando necessários e implementação com subagentes. Na revisão do plano, definiu um único subagente independente para o review conjunto de spec + quality ao final de cada tarefa e commit somente após todas as tarefas, mediante autorização explícita. Essa aprovação documental não inicia implementação, execução dos candidatos, substituição dos resultados existentes ou commits.

Implementar a quarta etapa do minerador: validar as candidatas aprovadas na extração por execução da suíte unitária completa em Docker, comprovar chamadas diretas à implementação original e exigir pelo menos quatro casos válidos por função. Produzir `results/04_validacao.jsonl`, com evidências auditáveis e ambiente reproduzível.

O minerador Python continua no Windows. Docker é a infraestrutura da validação; descoberta, triagem, extração, deduplicação e construção não são containerizadas nesta entrega.

### Fontes e precedência metodológica

- [Especificação geral](2026-10-29-minerador-incremental-design.md).
- [Extração](2026-11-02-extracao-design.md), [contexto](2026-11-03-contexto-por-instancia-design.md) e [snapshots v3](2026-11-03-snapshot-reutilizavel-design.md).
- [Evidências finais do piloto v3](../plans/2026-11-04-extracao-v3-evidencias.md).
- `Downloads/Heurísticas_V6.md` e o artigo `Tema_4_VeV___Building_a_Dataset_for_Test_Driven_Development_Oriented_Code_Generation_with_Large_Language_Models (1).pdf`.
- `Downloads/mbpp_primeiros_10_sem_resposta.json`, referência de apresentação por tarefa, não contrato desta etapa.

As decisões expressas anteriores e desta conversa prevalecem nas divergências: uma execução da suíte, não três; somente fontes existentes; sem mutação, categorização comportamental ou experimentos com LLMs. O rascunho do artigo ainda contém atividades fora desse escopo. Não alterar os documentos em Downloads nesta entrega.

## 2. População do piloto

A inspeção do `03_extracao.jsonl` atual encontrou dez registros de repositório e 3.976 instâncias: 189 `approved`, 318 `rejected` e 3.469 `not_evaluated`; a execução de origem é `partial`. As 189 candidatas aprovadas pertencem a seis origens, confirmadas pelo pesquisador:

| Repositório | ID | SHA | Candidatas approved |
| --- | ---: | --- | ---: |
| YounghyeonPark/agbrte | 1317120711 | `0c5c31099ce74cf87b6b4dc34a38e6a1f6ecfe58` | 115 |
| Nemryz/opencode-sdd-integrity | 1281991062 | `c3d26596edf63adc444b1900790bbccd49795226` | 23 |
| mossglenn/armature | 1164258973 | `8acaa14e01102a9e4e7a5941765113539c43c613` | 17 |
| HiAnur/anur-living-map | 1421624693 | `6285658e0b03725860659a26de8a75415bf151db` | 23 |
| abinauv/dental-erp | 1150946629 | `8d0135085c4ab77c270d531ece05b76d4139e44c` | 1 |
| saching13/macrotracker-server | 1442582077 | `63d69dabed0f5ea58ae9784e40b930b5ca468702` | 10 |
| **Total** | | | **189** |

Esse é o baseline observado, não uma contagem fixa imposta ao comando. Cada execução valida sua entrada real e registra seu hash e suas contagens. Os outros quatro repositórios não têm instâncias aprovadas e não são executados.

O relatório histórico de cinco snapshots também tinha 189 aprovações, mas com 24 em Nemryz e sem dental-erp. Não substituir a população atual por aquela coorte nem atualizar resultados para forçar igualdade. Se o replay estrito detectar divergência entre evidências atuais e o analisador confiável, registrar a pendência afetada sem promover ou reescrever a extração.

## 3. Escopo da suíte e elegibilidade

### 3.1 Suíte unitária completa

Executar todos os testes do escopo unitário original de todos os pacotes do repositório, não somente os arquivos associados às candidatas. Integração e E2E separados ficam fora. Suíte completa não significa build de produção nem todos os comandos de verificação do projeto.

A receita deve documentar quais comandos e configurações originais cobrem esse escopo. Um comando recursivo pode iniciar vários processos Vitest; sua composição continua sendo uma passagem completa, sem executar novamente um pacote para obter evidência adicional.

Não filtrar testes por nome ou função candidata, editar padrões de inclusão ou remover setups para obter aprovação. Se não houver separação original comprovável entre categorias, registrar `UNIT_SCOPE_UNRESOLVED` e manter a execução afetada `not_evaluated`. Uma dependência proibida comprovada na suíte unitária pode determinar rejeição mesmo sem executar a suíte.

### 3.2 Preparação necessária

Preservar os passos originais necessários para instalar e executar os testes, inclusive geração de código, scripts de instalação e builds de pacotes locais. Não acrescentar lint, typecheck separado ou build de produção quando não forem pré-requisitos. Se já integrarem o comando original necessário, não removê-los.

Usar o lockfile original com instalação congelada: `npm ci`, `pnpm install --frozen-lockfile` ou equivalente documentado. Registrar lockfile efetivo, inclusive precedência de `npm-shrinkwrap.json`, workspaces e dependências resolvidas. Não gerar lockfile, atualizar dependências, trocar gerenciador ou instalar automaticamente provedor de cobertura ausente para contornar problemas. Lockfile ausente/inutilizável ou ambiente não determinável gera pendência de reprodução, sem inventar uma receita.

### 3.3 Recursos permitidos e proibidos

Permitidos: mocks, fixtures, arquivos temporários, dependências locais reproduzíveis, bancos embarcados e servidores temporários criados pelos próprios testes para simular comunicação.

Proibidos como dependência da suíte unitária: APIs e serviços remotos reais, PostgreSQL, Redis, TerminusDB ou outros servidores reais separados, ainda que locais ou em containers. A função de um recurso, não apenas o processo ou a porta, distingue um simulador de teste de um serviço real. Um processo filho de teste não é proibido só por existir.

A mera presença de SDK, variável de ambiente ou configuração de banco não reprova. É necessária evidência de dependência efetiva do escopo unitário. Os `execution_requirements` da extração orientam a avaliação, mas não são confirmações dinâmicas.

Não iniciar infraestrutura real para fazer uma suíte passar. Dependência proibida comprovada rejeita integralmente o repositório; alcance ou natureza não comprovados deixam o critério pertinente pendente. Suíte aprovada e rede bloqueada, isoladamente, não comprovam ausência de serviços reais locais.

## 4. Arquitetura

### Alternativas consideradas

1. **Motor comum e receitas declarativas por repositório — aprovada.** Compartilha integridade, Docker, adaptação ao framework e publicação; explicita particularidades sem duplicar os critérios.
2. Descoberta completamente automática: comandos ambíguos e workspaces podem produzir seleção incorreta.
3. Scripts específicos por projeto: flexíveis, mas duplicam lógica e dificultam uniformidade e ampliação.

### Componentes e contratos

| Componente | Responsabilidade | Entrada e saída |
| --- | --- | --- |
| Leitor da validação | Conferir JSONL e selecionar approved v3 | Bytes do 03 → origem, repositórios, catálogo de motivos e candidatas |
| Verificador de contexto | Conferir snapshot, replay e identidade de todas as candidatas do grupo | Repositório/SHA e store → contexto verificado ou falhas identificadas |
| Resolvedor de receita | Validar comandos, versões, escopo unitário e requisitos | Snapshot e receita versionada → especificação de execução imutável |
| Preparador de workspace | Materializar arquivos originais em área isolada do cache | Artefato verificado → cópia de trabalho identificada |
| Executor Docker | Instalar, preparar, executar, limitar e cancelar | Receita e workspace → eventos de processo/ambiente e resultados brutos |
| Adaptador Vitest | Instrumentar e relacionar declarações, casos, chamadas e alvo original | Evidências estáticas e execução → casos e atribuições verificáveis |
| Avaliador | Aplicar critérios sem converter incerteza em aprovação/reprovação | Evidências → decisões de repositório e instância |
| Publicador | Validar e escrever registros ordenados | Resultados → único JSONL da etapa |

Implementar inicialmente Vitest, utilizado pelas seis origens. Validar compatibilidade por versão efetivamente instalada; compartilhar o nome do framework não comprova compatibilidade entre versões. Outro framework ou versão sem suporte comprovado fica `not_evaluated`.

Reutilizar `SnapshotStore`, projeção, `Analyzer`, cálculo de `instance_id` e expansão v3 existentes. O loader atual reanalisa um repositório por chamada a `load_instance_context`; introduzir consumo em lote com a mesma força de verificação, sem chamar esse replay 189 vezes. A otimização não pode remover conferência de unidade completa, contexto, requisitos, analisador ou ordinal lexical.

## 5. Receitas e materialização

Uma receita versionada do minerador fica vinculada a `repository_id` e `commit_sha`. Define comandos em listas de argumentos, diretórios de trabalho, escopo de pacotes/configurações, imagem por digest, plataforma, Node.js e gerenciador exatos, variáveis explícitas, preparação necessária e limites. Cada escolha deve apontar a evidência original que a fundamenta. Registrar versão e hash da receita no resultado.

As seis receitas são artefatos a preparar na implementação. As versões exatas das imagens e ferramentas precisam ser resolvidas e verificadas antes da primeira tentativa candidata, conforme os requisitos do commit e a capacidade do adaptador. Não usar tags mutáveis ou intervalos de versão como identidade final do ambiente. Não trocar versões ou comandos após falha para salvar a candidatura.

Revisão inicial por origem:

| Origem | Particularidade já observada, a confirmar na receita |
| --- | --- |
| agbrte | npm, Node >=22, Vitest 4 declarado; testes iniciam processos e sockets locais |
| Nemryz | npm, Node >=22, Vitest 4 declarado; conferir shrinkwrap e separação dos escopos |
| armature | npm workspaces; comando principal delega a app; setup exige TerminusDB real |
| anur-living-map | npm, Node >=23.6, Vitest 5 declarado; scripts de instalação e requisitos de execução |
| dental-erp | npm, Vitest 5 declarado, jsdom; postinstall Prisma e setup a inspecionar |
| macrotracker-server | pnpm 10.34.7, Node >=26; workspaces, builds locais e comandos unit/int separados |

Não antecipar aprovação ou rejeição dessas origens somente com essa tabela. Em armature, separar a prova do escopo unitário da prova de dependência do serviço antes de escolher entre rejeição e pendência.

Materializar o commit original a partir do Git bare já verificado, sem fetch ou fallback para branch. A validação não recupera automaticamente cache ausente/corrompido. Preservar bytes, modos e links pertinentes; não depender de tarball que omita `export-ignore`, conversão de fim de linha do Windows ou filtros Git. Submódulos e ponteiros LFS não equivalem a conteúdo adquirido: requisitos não satisfeitos são explicitados, sem download corretivo silencioso.

O cache não é área de execução nem montagem gravável dos candidatos. Usar cópia de trabalho própria com identidade verificável; preservar metadados Git se forem necessários aos comandos originais. Instalação e geração originais podem produzir arquivos nessa cópia; não modificar fontes/testes para corrigir falhas.

## 6. Ambiente e ciclo de vida Docker

Ambientes Linux, um repositório por vez no piloto. Preservar paralelismo interno original. Não acrescentar `--workers` para paralelismo entre repositórios nesta versão.

| Recurso | Valor inicial aprovado |
| --- | --- |
| CPU por ambiente | 4 CPUs |
| Memória por ambiente | 8 GiB |
| Instalação | 20 minutos |
| Preparação necessária, soma dos passos | 10 minutos |
| Suíte unitária completa, soma dos comandos | 30 minutos |

Orçamentos de fases não reiniciam por subprocesso ou pacote. Recursos e limites são configuráveis, validados e registrados antes da execução; não aumentá-los automaticamente após falha. Registrar limites efetivos e não anunciar limite de memória se o mecanismo permitir ultrapassá-lo por swap não contabilizado.

Rede disponível na instalação/preparação apenas para dependências e ferramentas declaradas. Testes executados sem rede externa, preservando loopback no próprio ambiente. Não montar socket Docker nem serviços do host no ambiente candidato. Simulações internas permitidas não precisam de publicação de portas no Windows.

Transmitir somente variáveis explícitas da receita e as próprias do ambiente de execução; não herdar credenciais/variáveis do Windows. Registrar configuração reproduzível sem segredos. A validação não exige `GITHUB_TOKEN`.

O executor deve preservar o workspace instalado entre fases e executar testes em processo novo com rede externa desabilitada, sem manter processos da preparação como serviços ocultos. Registrar arquitetura, distribuição/base da imagem, Docker Engine, versões reais das ferramentas, imagem efetiva e dependências resolvidas, não somente valores declarados.

Distinguir preparação do ambiente do minerador, inclusive aquisição da imagem e instalação do instrumento confiável, de comandos candidatos. Falha nessa infraestrutura não rejeita o projeto. O preflight verifica mecanismo Linux acessível e suporte aos limites antes de substituir a saída; não inicia Docker Desktop automaticamente.

Cancelamento encerra containers e subprocessos próprios, recolhe recursos temporários e preserva snapshots e resultados anteriores. Limpeza não executa comandos candidatos adicionais. Sobras após interrupção abrupta devem ser identificáveis como recursos de trabalho, não execuções concluídas.

## 7. Instrumentação e evidência por caso

### 7.1 Prova exigida

Executar uma passagem completa já instrumentada. Não executar novamente para coletar cobertura, desambiguar um caso ou tentar passar após falha.

A cadeia de evidência deve vincular:

1. arquivo e declaração original do teste, incluindo posição/ordinal quando necessários;
2. identidade do caso de runtime, com projeto Vitest, arquivo, declaração e ocorrência parametrizada;
3. ponto de chamada direta resolvido estaticamente para a candidata;
4. entrada na implementação original da função no contexto dessa chamada e desse caso;
5. resultado do caso, incluindo tentativas e condições especiais.

Nomes de testes, linha da função, sucesso da suíte ou cobertura isolada não substituem essa cadeia. Duas chamadas do mesmo caso não são dois casos. Iterações de property-based testing, assertions e retries também não são casos adicionais; conta-se o caso reconhecido pelo runner. Uma parametrização registrada pelo runner pode gerar vários casos distintos mesmo com títulos repetidos.

Aplicar sondas temporárias durante transformação/carregamento em memória e integrar ciclo de vida dos testes e reporter. Registrar versão/configuração/hash do instrumento. Não persistir transformações no snapshot ou usar spies/mocks que substituam a implementação de referência como prova do alvo original.

Instrumentar chamada e entrada sem alterar ordem de avaliação, receptor/`this`, argumentos, exceções, identidade relevante, retorno síncrono/assíncrono ou comportamento de optional chaining e sobrecargas. O plano deve validar as formas efetivamente suportadas antes de creditar casos. Forma não suportada é limitação explícita, não associação presumida.

Isolar identidade por processo/worker/projeto e por caso. Código assíncrono, testes concorrentes, hooks, processos filhos e tarefas que sobrevivem ao término do teste não podem ser atribuídos por uma variável global de teste ativo. Chamadas exclusivamente em hooks/helpers não satisfazem associação direta. Instrumentação incompleta de processos/imports dinâmicos não autoriza concluir que houve zero chamadas.

### 7.2 Contagem e completude

Caso válido: reconhecido e efetivamente executado pelo runner, aprovado, não ignorado/todo/fracasso esperado, com chamada direta e implementação original comprovadas. Falha esperada não conta como teste positivo da implementação. Um caso pode contar para mais de uma função se satisfizer a cadeia para cada uma.

Registrar tentativas; não usar retry/repeat como casos adicionais. Uma falha inesperada na primeira tentativa observada da suíte exclui o repositório mesmo se o runner terminar verde após retry. Não habilitar retries extras. Resultados esperados especiais seguem a configuração original para o resultado da suíte, mas não entram no mínimo de quatro.

Para H-F03, quatro ou mais casos comprovados satisfazem o limiar. Menos de quatro permite rejeição somente se a observação completa demonstrar que não há casos adicionais elegíveis; se uma lacuna puder alterar essa decisão, fica `not_evaluated`. A contagem publicada deve indicar se é exata ou limite inferior comprovado. Mesmo quando já há quatro testemunhas, lacunas que comprometam identidade, suíte ou outros critérios impedem aprovação.

Os testes associados estáticos que não executaram devem ter classificação explicável: skipped, todo, não descoberto, chamada não alcançada ou observação indisponível. Caso adicional só conta se também houver vínculo direto estático verificado; não promover chamadas indiretas apenas porque apareceram dinamicamente.

## 8. Cobertura

Coletar na mesma passagem quando houver suporte compatível. Não instalar automaticamente provedor ausente no projeto nem repetir testes. Registrar ferramenta, versão, configuração de instrumentação, mapas de fonte e escopo da métrica.

Distinguir cobertura de linhas e ramificações dos testes associados da cobertura da suíte completa sobre a função. A segunda pode ser informação complementar, mas não preencher a primeira. Atribuir aos testes associados exige dados por caso confiáveis; cobertura global agregada não basta.

Representar cada medição com `status`, `scope`, numeradores/denominadores quando disponíveis e motivo de indisponibilidade. `null` não é zero. Denominador zero é explicitado como não aplicável, sem inventar percentual. Ausência de cobertura não exclui a função. Falha isolada da coleta opcional permite cobertura indisponível se não comprometer execução/evidência obrigatória; falha que contamine essas evidências gera pendência operacional, sem rerun.

## 9. Critérios e decisões

Usar `approved`, `rejected`, `not_evaluated` nos critérios de elegibilidade. Etapa não necessária é representada por `required=false`, não como quarto estado de critério científico.

| Condição | Decisão |
| --- | --- |
| Instalação, preparação necessária ou suíte falha por causa candidata identificada, em receita válida | Repositório rejeitado; nenhuma instância avança |
| Timeout de teste/hook informado pelo próprio framework | Falha de suíte; rejeição integral |
| Serviço real proibido comprovadamente necessário à suíte unitária | Rejeição integral |
| Fonte/snapshot/receita/versão do adaptador não podem ser verificados | Critérios afetados não avaliados |
| Orçamento do minerador excedido, OOM do container, daemon indisponível ou falha do instrumento | Pendência operacional, não reprovação científica |
| Suíte passa, função com contagem completa abaixo de quatro | Instância rejeitada |
| Suíte passa, mas lacuna impede determinar elegibilidade | Instância não avaliada |
| Suíte e recursos aprovados, identidade original comprovada e mínimo de casos satisfeito | Instância aprovada |

Um exit code não zero não identifica sozinho a causa. Distinguir indisponibilidade de registry/rede/host e falha do runner confiável de inconsistência comprovada do projeto. Se a causa não puder ser determinada, manter pendência operacional. Não adicionar tentativas corretivas automáticas de comandos candidatos.

Critérios de repositório: `reproducible_environment`, `unit_suite_scope`, `dependency_installation`, `required_preparation`, `complete_unit_suite`, `no_external_services`. Quando preparação não é necessária, seu critério pode ser aprovado com evidência de não necessidade, sem alegar comando executado.

Critérios de instância: `repository_execution`, `original_target_execution`, `minimum_valid_cases`, `no_external_services`. Instâncias herdam restrições do repositório por vínculo explícito. Uma rejeição comprovada determina rejeição; todos os critérios obrigatórios aprovados permitem aprovação; os demais casos ficam não avaliados. Critérios não executados após interrupção permanecem pendentes, não recebem resultados fabricados.

## 10. Entrada e comando

Comando previsto: `python -m robo_minerador validate`, com `--overwrite` para substituição intencional e `--help`. Caminhos iniciais fixados por constantes, conforme as etapas existentes: entrada `results/03_extracao.jsonl`, saída `results/04_validacao.jsonl`. Não há anexação, retomada, `--limit` ou seleção de funções na CLI inicial.

Validar integralmente antes da execução: UTF-8/JSON estrito, versão uniforme v3, tipos, início/fim, IDs únicos, identidades/SHA, catálogo e referências de motivos, decisões/critérios, ordenação necessária, contagens e contexto. Fingerprint SHA-256 dos bytes originais. Aceitar origem `partial`, preservando-a como proveniência, sem processar pendentes.

Validar configuração e formato das receitas e, havendo candidatas, fazer preflight de Git, Node/auxiliar confiável e Docker antes de abrir/substituir a saída. Problemas globais de entrada/configuração preservam saída anterior mesmo com `--overwrite`. Ausência de receita aplicável, impossibilidade de resolver o ambiente daquela origem e verificações individuais de snapshots geram pendências por repositório, sem impedir avaliação independente dos demais. Receita malformada é erro de configuração, não falha candidata.

Zero candidatas produz execução vazia `completed`, sem iniciar containers ou exigir Git, auxiliar de análise, Docker, imagens ou receitas de projetos ausentes. Permanecem a validação da entrada, configuração de publicação e proteção da saída existente. Não transformar ausência de candidatas em falha científica.

## 11. Contrato de `04_validacao.jsonl`

### 11.1 Convenções

UTF-8, um objeto JSON por linha, serialização compacta com escapes que preservem os valores aceitos pela extração. Esquema próprio da etapa com `schema_version=1`, distinto do v3 da entrada. Todos os registros declaram a versão. Instantes UTC. Mensagens e razões em português; chaves e identificadores de código em inglês.

Única saída canônica da etapa. Relatórios nativos, traces e logs usados internamente são arquivos de trabalho; suas evidências necessárias devem ser incorporadas ao JSONL antes da limpeza, sem depender de caminhos temporários para justificar decisões. Não truncar silenciosamente evidências de casos. Se houver perda ou limite de captura, publicar a limitação e seu impacto.

Ordem: `run_started`; para cada repositório na ordem de entrada, registro `repository`, suas instâncias na ordem original e diagnósticos locais; `run_finished`. Somente o coordenador publica. O registro de repositório contém o resultado consolidado, não uma aprovação preliminar antes da suíte.

### 11.2 Tipos de registro

| Tipo | Campos e significado obrigatórios |
| --- | --- |
| `diagnostic` / `run_started` | `stage=validation`, instante, hash/caminho/versão/estado da entrada, contagens elegíveis, política, limites e versão do minerador/instrumento |
| `repository` | ID, URL, SHA, referência verificável de snapshot, decisão/critérios/motivos, receita e hash, ambiente efetivo, fases de execução, resumo de casos da suíte e contagens de decisões das instâncias |
| `instance` | `repository_id`, `instance_id`, decisão/critérios/motivos, vínculo ao repositório, contagens, completude, evidências dos casos e cobertura |
| `diagnostic` local | Identidade do repositório/instância quando pertinente, evento/código estável, causa, fase, mensagem e evidências operacionais disponíveis |
| `diagnostic` / `run_finished` | Contagens de repositórios/instâncias, decisões, erros operacionais, estado e instante final |

Definir um registro de repositório para cada origem elegível e um registro de instância para cada candidata, mesmo quando falha anterior impeça sua execução. Não republicar instâncias rejected/not_evaluated da extração como se tivessem sido validadas.

### 11.3 Evidências compartilhadas do repositório

- `environment`: plataforma/arquitetura, digest e identificação da imagem, base Linux, Docker Engine, Node real, gerenciador real e dependências resolvidas com versões e integridade/origem disponíveis.
- `recipe`: identificação, versão/hash e especificação efetiva necessária à reprodução; comandos e configurações não podem depender apenas de um arquivo local mutável.
- `lockfiles`: caminho, hash e gerenciador correspondente; preservar identidade do lockfile efetivamente utilizado, não de todo arquivo com nome semelhante.
- `execution`: por fase, necessidade, comandos/argumentos, diretório, variáveis explícitas não secretas, início/fim, duração, resultado e exit code quando existente. Registrar passos não iniciados e causa.
- `unit_suite`: frameworks/versões/configurações e pacotes abrangidos, uma passagem, casos e tentativas com estados distintos, completude e primeiro erro identificado.
- Evidências de recursos permitidos/proibidos e limitações da observação.

Dependências resolvidas podem ser registradas de forma compacta no repositório, sem repetir por função. Lockfile sozinho não substitui a conferência do ambiente efetivamente instalado. Não repetir código/documentação/testes do 03 em todos os resultados.

### 11.4 Evidências da instância

`test_cases` registra casos válidos comprovados, skipped/todo e casos cuja associação não pôde ser concluída, distinguindo desconhecido de zero e contagem exata de limite inferior. Contagens são por caso, não por quantidade de registros de associação estática.

`case_evidence` deve justificar todos os casos contabilizados. Cada entrada contém identidade inequívoca de runtime; projeto/arquivo e localização da declaração original; parametrização/ocorrência; resultado/tentativas; ponto(s) de chamada original; vínculo ao alvo por `instance_id` e localização; confirmação da execução original e identificação do instrumento. Títulos são auxiliares, nunca a chave única.

Preservar a associação exata aos registros do 03: caminho, intervalo do teste, framework, ponto de chamada e alvo resolvido, incluindo informação adicional de coluna/ordinal quando necessária. Se o mapeamento for ambíguo, não atribuir por semelhança de nomes. Evidências negativas e lacunas devem explicar por que uma associação estática não contribuiu para a contagem.

`coverage` separa os escopos `associated_tests` e `full_unit_suite`; métricas ausentes têm valores `null` e motivo. Sua indisponibilidade não altera os critérios obrigatórios.

### 11.5 Estados e códigos de saída

- `completed`: processamento de todas as candidatas concluído sem pendências obrigatórias ou falhas operacionais. Rejeições científicas, inclusive rejeitar todas, não tornam a etapa parcial. Cobertura opcional indisponível, justificada, também não.
- `partial`: há avaliação útil e pendências obrigatórias ou falhas operacionais.
- `failed`: nenhuma avaliação científica útil pôde ser realizada por impedimentos operacionais. Critérios de mérito rejeitados constituem avaliação, não falha do minerador.

Códigos: 0 para `completed`; 1 para `partial`, `failed`, cancelamento ou falha de escrita; 2 para entrada/configuração/preflight global inválidos ou saída existente sem autorização. Falha de escrita/cancelamento pode impedir `run_finished`; arquivo sem fechamento não deve ser aceito como execução completa. Registrar o estado parcial da origem sem automaticamente copiá-lo como estado desta etapa.

## 12. Relação com deduplicação e construção

Somente instâncias `approved` seguem para deduplicação. Não remover duplicatas nesta etapa. Manter IDs originais para consolidar, na construção:

- documentação comportamental, assinatura, implementação e testes originais do 03;
- casos executados, decisões, ambiente e comandos do 04;
- seleção e vínculos de origens do 05;
- licença e demais metadados das etapas pertinentes.

Isso permite uma instância final semelhante em organização ao exemplo MBPP, com descrição, identificador, preparação e lista de testes, enriquecida com contexto e reprodução. A construção não deve confundir cinco casos parametrizados com cinco trechos independentes nem converter testes complexos em assertions artificiais.

O dataset completo preservará a implementação de referência; sua exclusão da entrada da LLM e o controle de vazamento em arquivos de contexto pertencem à projeção experimental futura. O 04 não gera prompts nem altera documentação. A saída final prevista continua JSONL; não criar exportação MBPP nesta entrega.

## 13. Verificação e critérios de aceite

Aplicar a política de testes proporcionais da especificação geral: testes novos somente para risco concreto de seleção, associação, contagem ou integridade, com justificativa no plano. Sem TDD obrigatório, meta de cobertura ou teste por módulo. Verificações com fixtures controladas não são instâncias sintéticas do dataset científico.

O plano deve cobrir:

1. Leitura estrita, referências e contagens, preservação da saída em preflight inválido e vínculo de proveniência.
2. Snapshot/receita/instância adulterados recusados, sem rede de recuperação ou alteração dos resultados anteriores.
3. Parametrizações e títulos duplicados identificados por caso; múltiplas chamadas, assertions e retries sem inflação.
4. Chamada indireta, mock/substituição, hook e chamada não alcançada sem crédito indevido; entrada original comprovada no controle positivo.
5. Métodos e semântica de chamadas, assincronia, concorrência, processos e tarefas tardias sem atribuições cruzadas.
6. Primeira falha preservada mesmo após retry verde; exclusão integral sem tentativa corretiva.
7. Serviço real separado distinguido de mock, banco embarcado e servidor temporário de simulação.
8. Limites do minerador, falhas Docker/rede/instrumento e coleta incompleta classificados como operacionais.
9. Cobertura de suíte não apresentada como cobertura dos associados; indisponível diferente de zero, sem nova execução.
10. Publicação pelo escritor real com evidência suficiente para recuperar teste e função originais e preparar a construção.
11. Ausência de candidatas concluída sem containers, sem falha artificial por dependências de execução desnecessárias.
12. Piloto nas seis origens, com receitas verificadas antes das tentativas, sem quota de aprovados. Registrar causas concretas de rejeições/pendências e observar todos os casos creditados, não apenas sucesso do Vitest.

Antes do piloto oficial, verificar a infraestrutura e o adaptador em fixtures controladas. O desenvolvimento do instrumento não autoriza repetir suítes reais até passarem nem sobrescrever `03_extracao.jsonl`. A execução do piloto será prevista como tarefa explícita do plano após sua aprovação, não como parte da escrita desta spec.

Executar as verificações existentes pertinentes ao minerador e ao auxiliar e inspecionar a saída produzida antes de declarar implementação concluída. Preservar hashes dos resultados e snapshots protegidos no piloto; persistir as evidências científicas no JSONL, não depender exclusivamente de relatórios em TEMP.

## 14. Convenções de implementação e limites desta entrega

Trabalhar no checkout/branch atuais, sem worktrees ou branches auxiliares. Identificadores de código em inglês; comentários e docstrings em português; toda função/método novo documentado conforme a especificação geral. Módulos com responsabilidades claras, sem infraestrutura de filas ou banco de dados.

O plano deve respeitar a execução com subagentes, revisão pelo coordenador, tarefas delimitadas e arquivos atribuídos. Por decisão posterior expressa do pesquisador, cada tarefa termina com um review conjunto de spec + quality por um único subagente distinto do implementador; achados exigem correção e nova revisão. Não haverá commits intermediários: somente um commit ao final de todas as tarefas e reviews aprovados, mediante autorização explícita. A presente escrita da spec não inicia essa execução.

Fora do escopo: containerizar todo o minerador; outros adaptadores de framework; recuperar pendências da extração; reparar candidatos; flexibilizar H-F06; mutação; classificação comportamental; geração por LLM; deduplicação; esquema definitivo e implementação da construção; amostragem manual da etapa final; novas coletas de descoberta/triagem/extração.

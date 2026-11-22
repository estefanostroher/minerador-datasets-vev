# Execução do pipeline pelo Docker Compose

## Objetivo e decisões aprovadas

Executar qualquer etapa do minerador, ou as seis em sequência, por uma única linha com Docker Compose. O usuário seleciona as etapas e, quando há descoberta, pode informar a quantidade máxima de repositórios. As configurações de workers e recursos continuam em `config.py` e nas receitas existentes.

O serviço será `minerador`. A solução reutiliza a CLI Python existente e o Docker Engine Linux do host. A execução completa continua após uma etapa `partial` somente quando sua publicação estiver íntegra e encerrada. O resultado geral conserva a indicação de execução parcial.

## Interface

```powershell
docker compose run --rm --build minerador --discover 100
docker compose run --rm minerador --triage
docker compose run --rm minerador --extract
docker compose run --rm minerador --validate
docker compose run --rm minerador --deduplicate
docker compose run --rm minerador --build-dataset
docker compose run --rm minerador --all 100
docker compose run --rm minerador --all
docker compose run --rm minerador --all 100 --overwrite
docker compose run --rm minerador --help
```

- `--discover [N]` e `--all [N]` aceitam inteiro positivo; ausência de N usa `config.MAX_REPOSITORIES`, atualmente 1000.
- `--all` seleciona descoberta, triagem, extração, validação, deduplicação e construção, nessa ordem.
- Seletores individuais podem ser combinados, por exemplo `--triage --extract`; a execução sempre segue a ordem do pipeline, independentemente da ordem dos argumentos.
- `--all` combinado com seletores individuais é erro de argumentos.
- Sem seletor, a entrada apresenta ajuda e retorna 2. `--help` retorna 0.
- Não expor `--limit`, `--workers`, caminhos ou recursos nesta entrada. A CLI original continua disponível para uso direto em Python.
- `--overwrite` autoriza a substituição das saídas das etapas selecionadas; nunca é implícito. Não representa retomada.

## Componentes

### Imagem e Compose

Um `Dockerfile` fornece Python 3.11 ou superior, Git, Node.js >=22, npm, TypeScript 6.0.3 instalado com o lockfile do auxiliar e a CLI Docker. Preferir bases oficiais com a mesma distribuição Debian, usando estágios para obter Node e o binário Docker. Instalar o pacote Python sem dependências de desenvolvimento na imagem de execução.

`compose.yaml` define um único serviço de execução pontual com `init: true`, diretório de trabalho `/app`, entrada Python do coordenador e variável `GITHUB_TOKEN` recebida do ambiente. O build instala dependências; o usuário precisa apenas de Docker com Compose para executar o minerador.

Montagens persistentes:

| Origem no host | Destino | Uso |
| --- | --- | --- |
| `./results` | `/app/results` | Publicações 01 a 06 e artefatos existentes do pesquisador. |
| `./.cache/robo-minerador/snapshots` | `/app/.cache/robo-minerador/snapshots` | Aquisição e reprodução dos snapshots. |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Acesso administrativo ao Docker Engine Linux pelo coordenador. |

No Docker Desktop, usar containers Linux. O socket é montado somente no coordenador; containers candidatos mantêm as montagens e o ambiente explícitos do executor atual. O token não é incluído na imagem nem enviado aos candidatos. A primeira versão usa o socket local, sem configuração adicional de daemon remoto.

`.dockerignore` exclui `.git`, ambientes virtuais, caches locais, resultados, builds, arquivos `.env` e `node_modules`. As dependências do auxiliar são instaladas dentro da imagem. Nenhum dado local do pesquisador integra o contexto útil da imagem.

### Coordenador Python

Criar um módulo separado, `robo_minerador.pipeline`, executável por `python -m robo_minerador.pipeline`. Interpretar os seletores com `argparse` e reutilizar `robo_minerador.cli.main(argv)` em processos filhos Python, executando a CLI original com `sys.executable`. Isso separa o estado de cada etapa e preserva a interface atual.

Antes da primeira etapa, verificar conflitos de saídas de todas as etapas selecionadas. Se alguma saída existir sem `--overwrite`, recusar o conjunto com código 2 antes de iniciar processamento. Entradas e pré-requisitos próprios de cada etapa continuam sob responsabilidade da CLI existente.

Apresentar no terminal início, nome, código de saída e resultado de cada etapa, além do resumo final. Não criar um sétimo arquivo de resultados ou log persistente.

### Instrumento de validação entre containers

Hoje o instrumento é gerado em um diretório temporário e montado por bind no container candidato. Um caminho temporário interno ao coordenador não existe no host do daemon; essa montagem deve ser substituída por um volume Docker exclusivo do executor.

Após gerar o instrumento confiável e `settings.json`, transferir seus arquivos via arquivo tar e `docker cp` para um volume nomeado. Conferir os hashes dos arquivos transferidos com programa Node confiável antes da suíte. Montar o volume em `/instrument` como somente leitura nos containers da suíte. Preservar identidade, conteúdo do instrumento, settings, comandos, critérios, limites e evidências existentes.

O executor registra ownership e recolhe esse volume com os demais recursos próprios, inclusive em falha e cancelamento. O diretório temporário Python permanece sob limpeza explícita. Usar o mesmo transporte por volume também na execução Python direta para evitar dois modos de validação.

## Continuação e erros

| Condição | Ação |
| --- | --- |
| Etapa retorna 0 | Avançar; o pipeline conserva eventual parcialidade anterior. |
| Etapa retorna 1 e a saída atual é uma publicação válida com fechamento `partial` | Avançar e marcar o resultado geral como parcial. |
| Etapa retorna 1 com `failed`, saída ausente, inválida ou sem fechamento | Interromper com 1. |
| Etapa retorna 2 | Interromper com 2. |
| Cancelamento ou término anormal do filho | Interromper, encerrar/recolher o filho e retornar 1. |

Validar a publicação com os leitores existentes do formato correspondente, não apenas buscando o estado na última linha. O fechamento deve pertencer à etapa executada. Não aceitar uma saída antiga como evidência de sucesso: calcular SHA-256 dos bytes em streaming antes e depois da etapa e exigir saída anteriormente ausente ou hash diferente, além da validação integral, antes de continuar com pendências. Revisão: metadados como mtime e ctime podem mudar sem alteração de conteúdo; bytes idênticos são recusados mesmo em republicação legítima idêntica.

Mapeamento dos leitores: descoberta → `triage.load_discovery`; triagem → `extraction_input.load_triage`; extração → `validation_input.load_extraction`; validação → `validation_format.validate_output_records`. Usar seus estados e validações, sem promover pendências. Deduplicação e construção não publicam sucesso parcial: retorno 1 nessas etapas interrompe o pipeline.

O código final é 0 se todas as etapas selecionadas concluírem sem parcialidade, 1 se houver parcialidade ou falha operacional e 2 em recusa de argumentos/configuração. Um dataset produzido a partir de etapas parciais não transforma o resultado geral em sucesso completo.

## Preservação dos artefatos

Os caminhos canônicos continuam sendo os configurados no projeto. Arquivos como `results/01_descoberta-original.jsonl` não são entradas implícitas e não são alterados pelo coordenador. Os critérios científicos permanecem os implementados atualmente, incluindo a política de validação função–testes documentada no README, que difere de trechos históricos do PDF e das heurísticas.

## Verificação e critérios de aceite

1. Testes do coordenador cobrem parsing, seleção combinada, ordem, limites, passagem de overwrite, recusa antecipada de saídas e códigos finais.
2. Testes de continuação distinguem publicação parcial válida, arquivo incompleto, JSON inválido, `failed`, saída antiga e cancelamento. Nenhum teste usa as publicações reais do pesquisador.
3. Testes do executor comprovam transferência do instrumento, verificação dos bytes, montagem readonly e limpeza em sucesso/falha, sem bind de caminho temporário do coordenador.
4. `docker compose config --quiet`, build da imagem e ajuda pelo serviço passam com Docker Engine Linux disponível.
5. Smoke test em ambiente temporário comprova Python/Git/Node/TypeScript/Docker na imagem, persistência e instrumento acessível em um container irmão, sem token no candidato.
6. A integração usa fixtures locais. Coleta GitHub e reexecução dos dados reais não fazem parte da verificação automática desta entrega.

## Estado da revisão

Interface e política de continuação aprovadas na conversa. As tasks 1–6 foram implementadas no checkout autorizado. Após as correções, a revisão final da spec (`ses_e19598e77ffep4ug7sNRhxv6Zc`) e a revisão de qualidade (`ses_e1956b970ffeOi3mHhFDYegxIH`) foram aprovadas. A execução manual e a aprovação do usuário permanecem pendentes antes do único commit futuro.

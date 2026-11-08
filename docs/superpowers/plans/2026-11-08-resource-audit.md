# Produtor de auditoria de recursos

Desenho autorizado pelo pesquisador em 2026-11-08: modelos explícitos e versionados de efeitos, ligados a implementações verificadas. Todo import, operação ou configuração não compreendidos mantém pendência. Execução inline, sem agentes, reviews, commits ou candidatos.

## Contrato e fronteira

O produtor interpreta AST TypeScript em linguagem fechada, sem executar fontes. Publica fontes originais, derivações e modelos para replay offline pelo mesmo auxiliar confiável. O validador portátil passa a exigir Node/TypeScript do minerador para rederivar a análise; não precisa de Docker, snapshots ou caminhos temporários. Hashes, linhas, escopo, bindings efetivos e módulos carregados são conferidos novamente. Não aceita classificações livres como prova de aprovação/rejeição.

Modelos iniciais: expressões/dados imutáveis; APIs limitadas de Vitest; operações temporárias Node; SQLite em memória; HTTP de simulação de origem estrutural conhecida. Operações desconhecidas, plugins/configs não modelados, import dinâmico, mutação, caminhos de filesystem sem proveniência temporária e bibliotecas não modeladas ficam pendentes. Serviço remoto real só rejeita com chamada incondicional em módulo original observado e análise integral sem efeitos opacos que possam substituir essa chamada.

## Passos

- [x] Testes focais do produtor/replay: permitido, proibido, desconhecido, imports transitivos e adulteração de modelo/fonte/caminhos.
- [x] Catálogo versionado, interpretador AST e ponte Python; fingerprints oficiais Vitest e runtime Node fixado por imagem/versão.
- [x] Captura aditiva de módulos originais e integração `DockerExecutor.observe_resources`.
- [x] Validador/publicador rederivam a prova; fixtures antigas usam produtor real.
- [x] Testes focais de integração e documentação da CLI/limitações; nenhum piloto.

Verificação: testes Python focais, regressão do loader de contexto, oito testes Node e fixture Docker própria (quatro casos, mocks e temporários; auditoria aprovada). A fixture tem `.npmrc` original `legacy-peer-deps=true`, como as fixtures existentes, para evitar falha de resolução de peers do npm 10.9.9 durante sua preparação. Não altera receita candidata. Declarações do teste e do mock ocupam intervalos distintos, respeitando a limitação preexistente de posições T3.

A fronteira inclui exclusão explícita de documentos/assets enumerados só com grafo fechado e inventário original vinculado. Rejeição por `get` exige evento nativo correspondente; carregar módulo não comprova alcançar a operação. Binding/versionamento adulterados são recusados por comparação JSON canônica, distinguindo booleanos e números.

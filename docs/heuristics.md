# Heurísticas estáticas de T09

## Escopo e estados

T09 caracteriza H-F04, H-F06, M-F01 e M-F05 das Heurísticas V6. A resposta
HTTP 1.0 contém um registro em `documents` e outro em `metrics` por target,
ligados por `symbol_target_id`. Os targets, seu código e os links de chamadas
permanecem disponíveis mesmo quando uma avaliação é reprovada ou inconclusiva.
T14 decide a elegibilidade final. T09 não executa projetos e mantém
`runtime_confirmed=false`.

`approved` indica evidência estática favorável ao critério específico;
`rejected` indica evidência contrária; `not_evaluated` indica inconclusão.
Cada avaliação registra `reason` e, quando pertinente, `evidence`. Ausência de
documentação e formas não suportadas são resultados de análise, não falhas HTTP.

## Documentação original

O extrator preserva o texto exato, o caminho relativo, o símbolo qualificado,
a associação e o intervalo `[start_offset, end_offset)` em unidades UTF-16.
Não reescreve, traduz, completa nem gera documentação. CRLF e caracteres fora
do BMP permanecem no texto original. As normalizações internas servem apenas
à classificação e nunca substituem a evidência armazenada.

São suportados:

- JSDoc que o parser TypeScript associa à declaração de implementação ou aos
  seus overloads, inclusive métodos de classes nomeadas. As associações são
  `adjacent_jsdoc` e `overload_jsdoc`. Comentários comuns e JSDoc que o parser
  não associa (por exemplo, certas posições inline) não são procurados por nome.
- Arquivos `.md` e `.markdown` do snapshot, analisados com
  `mdast-util-from-markdown`. Uma seção começa num heading com link inline
  `caminho-relativo.ts#símbolo-qualificado` e termina antes do próximo heading,
  de qualquer nível, ou no fim do arquivo. Exemplo, em `docs/api.md`:
  `## [increment](../src/counter.ts#Counter.increment)`.
  O fragmento precisa coincidir exatamente com `qualified_name`.
  Links de referência, âncoras inferidas pelo renderizador, nomes homônimos sem
  módulo e links somente ao arquivo não estabelecem associação.

Os caminhos Markdown são resolvidos lexicalmente em relação ao arquivo Markdown,
limitados aos caminhos do snapshot e confrontados com o caminho do target.
URLs, caminhos absolutos, escapes, consultas e referências externas não são
lidos. Os arquivos documentais passam pelos mesmos limites de arquivos/bytes e
verificações de symlink do snapshot; o parser recebe somente textos em memória.

Uma descrição livre deve conter evidência comportamental. O reconhecedor
conservador identifica ações explícitas em português/inglês: retornar, calcular,
converter, validar, rejeitar, lançar, normalizar, remover, adicionar, incrementar,
ordenar e filtrar (com variantes registradas em `documentation.ts`). Exige um
complemento além de um tipo ou termo genérico isolado, como `number`, `value`
ou `result`. Isso é uma heurística lexical, não compreensão semântica geral.
Em Markdown, headings, código, HTML e imagens não fornecem prosa comportamental.

| Situação | Estado | Motivo |
| --- | --- | --- |
| Nenhuma fonte associada | rejected | documentation_absent |
| Fonte sem descrição livre, como apenas `@param {number} x` | rejected | documentation_signature_only |
| Descrição livre sem evidência reconhecível, como `Helper function` | not_evaluated | documentation_behavior_unresolved |
| Descrição comportamental reconhecida em alguma fonte | approved | behavioral_description |

Marcadores de entradas, saídas, restrições e erros são apenas indicativos.
São registrados com o trecho original e seus offsets UTF-16; nenhuma quantidade
ou combinação desses marcadores é exigida. Documentação pouco detalhada não
é reprovada por falta de completude. O reconhecimento lexical pode deixar
descrições válidas inconclusivas; essas fontes continuam disponíveis à inspeção.

## Complexidade e tamanho

`cyclomatic-ts-v1` atribui base 1 por corpo e acrescenta 1 por `if`, laço
(`for`, `for-in`, `for-of`, `while`, `do-while`), `catch`, `case` não default,
ternário e operador binário `&&`, `||` ou `??`. Registra cada decisão com tipo
AST, texto e intervalo original. Ignora comentários, tipos, parâmetros e
funções/classes aninhadas, incluindo seus corpos e inicializadores.

Por exemplo, `function f(x:number){const g=()=>x?1:0;return x>0?2:3;}` tem
complexidade 2. O ternário de `g` pertence a outra unidade. Corpos de expressão
também contam a decisão do nó raiz quando medidos diretamente pelo helper.

`physical_lines` conta as linhas físicas do intervalo original
`getStart()`/`getEnd()`, com CRLF contado uma vez. Conta linhas vazias e
comentários dentro desse intervalo. As métricas são caracterização sem limiar;
complexidade 1 não reprova uma função. Métrica indisponível usa `null` e
`not_evaluated`, distinguindo indisponibilidade de zero.

## Trivialidade por formas AST

O estado de `triviality` avalia o critério de **não trivialidade** H-F06:

| Forma comprovada | Estado | Motivo |
| --- | --- | --- |
| Único retorno de literal primitivo independente das entradas | rejected | constant_literal_return |
| Getter com único retorno `this.campo` | rejected | simple_getter |
| Setter com única atribuição `this.campo = parâmetro` | rejected | simple_setter |
| Único retorno de chamada simples com parâmetros encaminhados na ordem | rejected | direct_delegation |
| Evidência de lógica adicional, como validação, decisão ou cálculo | approved | additional_logic |
| Outras formas não comprovadas | not_evaluated | triviality_shape_unresolved |

A delegação suportada usa um identificador ou `this.método`, sem encadeamento
opcional, com argumentos identificadores exatamente iguais aos parâmetros.
`f(x + 1)` e chamadas com efeitos nos argumentos permanecem inconclusivas.
Recursão nominal direta também permanece inconclusiva.

Retorno da entrada não é retorno literal constante. Objetos e arrays novos têm
identidade fresca e não são constantes primitivas. Getter com cálculo não é
getter simples. Defaults, destructuring, rest e decorators em parâmetros
resultam em `parameter_effects_unresolved`; async/generators resultam em
`execution_effects_unresolved`. Comentários e tipos não contam como lógica.

Os helpers reconhecem accessors, mas a descoberta de targets continua com o
escopo de T08: funções declaradas e métodos de classes nomeadas. Arrows,
expressões de função, construtores e accessors permanecem limitações explícitas
da descoberta. Isso evita ampliar o resolver de chamadas nesta tarefa.

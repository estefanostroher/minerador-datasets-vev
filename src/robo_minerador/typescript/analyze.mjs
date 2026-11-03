import ts from 'typescript';
import { pathToFileURL } from 'node:url';
import { createProject, walk, declarationName, className } from './virtual-project.mjs';
import { collectTests, identity } from './test-associations.mjs';
import { documentationFor } from './documentation.mjs';

/** Constrói resultado de critério com justificativa metodológica em português. */
function criterion(status, reason) { return { status, reason }; }

/** Reconhece constantes puras sem inferir comportamento de chamadas externas. */
function constant(expression) {
  if (!expression) return true;
  if (ts.isLiteralExpression(expression) || [ts.SyntaxKind.TrueKeyword, ts.SyntaxKind.FalseKeyword, ts.SyntaxKind.NullKeyword].includes(expression.kind)) return true;
  if (ts.isParenthesizedExpression(expression)) return constant(expression.expression);
  return constantPrimitive(expression);
}

/** Prova independência apenas de operações primitivas literais que não invocam código candidato. */
function constantPrimitive(expression) {
  if (ts.isNumericLiteral(expression) || ts.isStringLiteralLike(expression) || [ts.SyntaxKind.TrueKeyword, ts.SyntaxKind.FalseKeyword, ts.SyntaxKind.NullKeyword].includes(expression.kind)) return true;
  if (ts.isParenthesizedExpression(expression)) return constantPrimitive(expression.expression);
  if (ts.isPrefixUnaryExpression(expression)) return [ts.SyntaxKind.PlusToken, ts.SyntaxKind.MinusToken, ts.SyntaxKind.TildeToken, ts.SyntaxKind.ExclamationToken].includes(expression.operator) && constantPrimitive(expression.operand);
  if (!ts.isBinaryExpression(expression)) return false;
  const operators = [ts.SyntaxKind.PlusToken, ts.SyntaxKind.MinusToken, ts.SyntaxKind.AsteriskToken, ts.SyntaxKind.SlashToken, ts.SyntaxKind.PercentToken, ts.SyntaxKind.AsteriskAsteriskToken, ts.SyntaxKind.LessThanLessThanToken, ts.SyntaxKind.GreaterThanGreaterThanToken, ts.SyntaxKind.GreaterThanGreaterThanGreaterThanToken, ts.SyntaxKind.AmpersandToken, ts.SyntaxKind.BarToken, ts.SyntaxKind.CaretToken, ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken, ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken, ts.SyntaxKind.LessThanToken, ts.SyntaxKind.LessThanEqualsToken, ts.SyntaxKind.GreaterThanToken, ts.SyntaxKind.GreaterThanEqualsToken, ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken];
  return operators.includes(expression.operatorToken.kind) && constantPrimitive(expression.left) && constantPrimitive(expression.right);
}

/** Reconhece dependência explícita de parâmetros ou estado da instância, sem inferir retornos de chamadas. */
function inputDependent(expression, checker, parameters) {
  if (!expression) return false;
  if (expression.kind === ts.SyntaxKind.ThisKeyword) return true;
  if (ts.isIdentifier(expression)) return parameters.has(checker.getSymbolAtLocation(expression));
  if (ts.isParenthesizedExpression(expression) || ts.isPropertyAccessExpression(expression)) return inputDependent(expression.expression, checker, parameters);
  if (ts.isBinaryExpression(expression)) return inputDependent(expression.left, checker, parameters) || inputDependent(expression.right, checker, parameters);
  if (ts.isPrefixUnaryExpression(expression) || ts.isPostfixUnaryExpression(expression)) return inputDependent(expression.operand, checker, parameters);
  return false;
}

/** Identifica argumentos sem lógica adicional para delegação direta comprovada. */
function plainArgument(expression) {
  return ts.isIdentifier(expression) || constant(expression) || (ts.isPropertyAccessExpression(expression) && plainArgument(expression.expression)) || expression.kind === ts.SyntaxKind.ThisKeyword;
}

/** Detecta avaliação de parâmetros, inclusive defaults e chaves computadas em desestruturações. */
function hasParameterEvaluation(node) {
  for (const parameter of node.parameters) {
    if (parameter.initializer) return true;
    const names = [parameter.name];
    while (names.length) {
      const name = names.pop();
      if (!ts.isObjectBindingPattern(name) && !ts.isArrayBindingPattern(name)) continue;
      for (const element of name.elements) {
        if (!ts.isBindingElement(element)) continue;
        if (element.initializer || element.propertyName && ts.isComputedPropertyName(element.propertyName)) return true;
        names.push(element.name);
      }
    }
  }
  return false;
}

/** Avalia formas triviais comprováveis, sem limiar numérico de complexidade. */
function implementationCriterion(node, checker) {
  if (hasParameterEvaluation(node)) return criterion('not_evaluated', 'Inicializadores ou chaves computadas de parâmetros exigem análise adicional antes de concluir H-F06.');
  const statements = node.body.statements.filter(/** Ignora apenas sentenças vazias sem comportamento. */ statement => !ts.isEmptyStatement(statement));
  if (statements.length === 1) {
    const statement = statements[0];
    const expression = ts.isReturnStatement(statement) ? statement.expression : ts.isExpressionStatement(statement) ? statement.expression : null;
    if (ts.isReturnStatement(statement) && constant(expression)) return criterion('rejected', 'H-F06: retorno constante comprovado, independente das entradas.');
    if (expression && ts.isCallExpression(expression) && expression.arguments.every(plainArgument) && plainArgument(expression.expression)) return criterion('rejected', 'H-F06: delegação de uma única chamada sem lógica adicional.');
    if (ts.isReturnStatement(statement) && expression && ts.isPropertyAccessExpression(expression) && expression.expression.kind === ts.SyntaxKind.ThisKeyword) return criterion('rejected', 'H-F06: acesso simples a uma propriedade da instância.');
  }
  const parameters = new Set();
  for (const parameter of node.parameters) walk(parameter.name, /** Registra identidade dos vínculos de entrada, inclusive desestruturação. */ child => {
    if (ts.isIdentifier(child)) {
      const symbol = checker.getSymbolAtLocation(child);
      if (symbol) parameters.add(symbol);
    }
  });
  let logic = false;
  /** Procura lógica da própria unidade sem atribuir corpos de funções internas. */
  function visit(child) {
    if (ts.isFunctionLike(child)) return;
    if ((ts.isBinaryExpression(child) || ts.isPrefixUnaryExpression(child) || ts.isPostfixUnaryExpression(child)) && !constant(child) && inputDependent(child, checker, parameters)) logic = true;
    if ((ts.isIfStatement(child) || ts.isConditionalExpression(child) || ts.isWhileStatement(child)) && inputDependent(child.condition ?? child.expression, checker, parameters)) logic = true;
    if ((ts.isForOfStatement(child) || ts.isSwitchStatement(child)) && inputDependent(child.expression, checker, parameters)) logic = true;
    ts.forEachChild(child, visit);
  }
  visit(node.body);
  return logic ? criterion('approved', 'Implementação contém lógica explícita além das formas triviais comprovadas.') : criterion('not_evaluated', 'Não foi possível comprovar trivialidade nem lógica adicional nesta forma de implementação.');
}

/** Remove duplicatas de objetos mantendo ordenação determinística. */
function uniqueSorted(values) {
  return [...new Map(values.map(/** Usa representação integral como chave de deduplicação. */ value => [JSON.stringify(value), value])).values()].sort(/** Ordena por representação estável. */ (left, right) => compare(JSON.stringify(left), JSON.stringify(right)));
}

/** Compara textos sem depender da localidade do processo. */
function compare(left, right) { return left < right ? -1 : left > right ? 1 : 0; }

/** Analisa fontes em memória e devolve evidências estáticas sem executar candidatos. */
export function analyze(input) {
  const project = createProject(input);
  const tests = collectTests(project);
  const units = [];
  const invalidSources = new Set();
  for (const [filePath] of project.files) {
    const source = project.programFor(filePath)?.getSourceFile('/candidate/' + filePath);
    if (!source) continue;
    if (source.parseDiagnostics.length) {
      invalidSources.add(filePath);
      project.issues.push({ code: 'INVALID_SOURCE', message: 'Fonte contém sintaxe não interpretada integralmente.', path: filePath });
    }
    if (!/\.(?:[cm]?ts|tsx)$/.test(filePath) || /\.d\.[cm]?ts$/.test(filePath) || /(^|\/)(__tests__|tests?|specs?)(\/|$)|\.(test|spec)\.[^.]+$/.test(filePath) || tests.testPaths.has(filePath)) continue;
    walk(source, /** Seleciona exclusivamente declarações e métodos concretos. */ node => {
      if (!node.body) return;
      if (ts.isFunctionDeclaration(node) || (ts.isMethodDeclaration(node) && (ts.isClassDeclaration(node.parent) || ts.isClassExpression(node.parent)))) units.push(node);
    });
  }
  units.sort(/** Ordena unidades por caminho e posição lexical. */ (left, right) => compare(project.relative(left.getSourceFile().fileName), project.relative(right.getSourceFile().fileName)) || left.getStart() - right.getStart());
  for (const reference of project.unresolved) project.issues.push({ code: 'UNRESOLVED_LOCAL_IMPORT', message: 'Importação local ou alias não resolvido no snapshot.', path: reference.from });
  if (tests.pendingPaths.size || tests.pendingTargets.size || tests.mockedPaths.size || tests.mockedTargets.size) project.issues.push({ code: 'UNCERTAIN_TEST_EXECUTION', message: 'Receptores, callbacks, APIs, modificadores ou mocks contêm análise pendente sobre a implementação original.' });
  const contextPaths = [...project.files.keys()].sort(compare);
  const analysisIssues = [];
  const instances = units.map(/** Consolida critérios e preserva os textos originais de cada unidade. */ node => {
    const source = node.getSourceFile();
    const filePath = project.relative(source.fileName);
    const unitId = identity(node, project);
    const start = node.getStart(source);
    const end = node.getEnd();
    const documentation = documentationFor(node, project, units);
    if (documentation.ambiguous) analysisIssues.push({ code: 'AMBIGUOUS_DOCUMENTATION', message: 'Vínculo documental ambíguo ou documento vinculado indisponível.', path: filePath });
    const associatedTests = [];
    let hasUsableTest = false;
    let uncertainTest = tests.pendingTargets.has(unitId) || tests.pendingPaths.size > 0;
    for (const record of tests.records) {
      if (record.targetId !== unitId) continue;
      if (tests.mockedPaths.has(filePath) || tests.mockedTargets.has(unitId)) { uncertainTest = true; continue; }
      const testSource = record.node.getSourceFile();
      const modifiers = record.registration.modifiers;
      if (record.registration.skipped === false && !record.registration.uncertainModifiers && !record.registration.uncertainRegistration) hasUsableTest = true;
      else uncertainTest = true;
      associatedTests.push({
        path: record.filePath, framework: record.registration.framework, name: record.registration.name,
        start_line: testSource.getLineAndCharacterOfPosition(record.node.getStart()).line + 1,
        end_line: testSource.getLineAndCharacterOfPosition(record.node.getEnd()).line + 1,
        parameterized: record.registration.parameterized,
        ...(record.registration.skipped === null ? {} : { skipped: record.registration.skipped }), modifiers,
        association: { kind: 'direct_call', call_line: testSource.getLineAndCharacterOfPosition(record.call.getStart()).line + 1, expression: record.call.expression.getText(testSource), resolved_target: { path: filePath, start_line: source.getLineAndCharacterOfPosition(start).line + 1, name: declarationName(node) } },
      });
    }
    const incomplete = project.issues.length > 0 || invalidSources.size > 0 || project.unresolved.length > 0;
    const criteria = {
      identifiable_implementation: invalidSources.has(filePath) ? criterion('not_evaluated', 'Sintaxe incompleta impede identificar integralmente a implementação.') : criterion('approved', 'Declaração concreta identificada pela árvore sintática TypeScript.'),
      associated_documentation: documentation.documentation.length ? criterion('approved', 'Descrição original explicitamente associada à declaração.') : documentation.ambiguous || incomplete ? criterion('not_evaluated', 'Busca de documentação incompleta ou vínculo externo ambíguo.') : criterion('rejected', 'Nenhuma descrição associada encontrada na busca completa dos arquivos fornecidos.'),
      direct_test_association: hasUsableTest ? criterion('approved', 'Chamada direta resolvida em teste com registro comprovado e sem indicação de ignorado; execução não avaliada.') : uncertainTest || incomplete ? criterion('not_evaluated', 'Evidência de teste ignorada, incerta ou limitada por resolução e mocks não permite aprovar a associação.') : criterion('rejected', 'Busca completa não encontrou chamada direta em callback de teste comprovado.'),
      nontrivial_implementation: invalidSources.has(filePath) ? criterion('not_evaluated', 'Sintaxe incompleta impede comprovar a forma de implementação.') : implementationCriterion(node, project.programFor(filePath).getTypeChecker()),
      preserved_local_context: incomplete || documentation.ambiguous ? criterion('not_evaluated', 'Contexto contém referências ou análises pendentes.') : criterion('approved', 'Arquivos locais fornecidos e dependências resolvidas preservados por caminho.'),
    };
    if (associatedTests.length && !hasUsableTest) analysisIssues.push({ code: 'UNUSABLE_TEST_EVIDENCE', message: 'Associações preservadas apenas em testes ignorados ou com registro incerto.', path: filePath });
    if (criteria.nontrivial_implementation.status === 'not_evaluated') analysisIssues.push({ code: 'UNPROVEN_IMPLEMENTATION', message: 'Forma de implementação não permite concluir o critério H-F06.', path: filePath });
    const statuses = Object.values(criteria).map(/** Extrai estados para a decisão agregada. */ value => value.status);
    const decision = statuses.includes('rejected') ? 'rejected' : statuses.every(/** Exige aprovação de todos os critérios. */ status => status === 'approved') ? 'approved' : 'not_evaluated';
    return {
      decision,
      function: { kind: ts.isFunctionDeclaration(node) ? 'function_declaration' : 'class_method', name: declarationName(node), class_name: ts.isMethodDeclaration(node) ? className(node.parent) : null, path: filePath, start_line: source.getLineAndCharacterOfPosition(start).line + 1, end_line: source.getLineAndCharacterOfPosition(end).line + 1, signature: source.text.slice(start, node.body.getStart(source)).trimEnd(), reference_code: source.text.slice(start, end) },
      documentation: documentation.documentation, tests: associatedTests, context_paths: contextPaths,
      criteria,
      validation: { status: 'not_evaluated', executed_test_cases: null, suite_passed: null, external_services: 'not_evaluated' },
      reasons: Object.values(criteria).filter(/** Mantém razões de rejeição e pendência para auditoria. */ value => value.status !== 'approved').map(/** Preserva justificativas originais em português. */ value => value.reason),
    };
  });
  return { analyzer: { name: 'typescript_compiler_api', version: ts.version }, instances, local_dependencies: uniqueSorted(project.dependencies), unresolved_references: uniqueSorted(project.unresolved), issues: uniqueSorted([...project.issues, ...analysisIssues]) };
}

/** Executa exclusivamente o protocolo JSON da invocação direta. */
async function main() {
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    const result = analyze(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch {
    process.stderr.write('Falha ao interpretar ou analisar a entrada JSON.\n');
    process.stdout.write(JSON.stringify({ analyzer: { name: 'typescript_compiler_api', version: ts.version }, instances: [], local_dependencies: [], unresolved_references: [], issues: [{ code: 'ANALYZER_FAILURE', message: 'Entrada inválida ou análise indisponível.' }] }) + '\n');
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();

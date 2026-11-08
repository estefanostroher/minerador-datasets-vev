import ts from 'typescript';
import { createHash } from 'node:crypto';
import { transformSource, sourceHash } from './validation-transform.mjs';

/** Deriva ID estável de posição UTF-16 sem usar nome/título como chave runtime. */
function positionId(kind, path, start, end) {
  return createHash('sha256').update(JSON.stringify([kind, path, start, end])).digest('hex');
}

/** Gera metadados somente de textos e associações já verificados pela T1.
 * Todo CallExpression preservado pode ser localização de registro runtime. Isso
 * permite censar testes não associados sem pressupor que um nome seja API de teste.
 * Somente associações T1 acrescentam sondas de chamada direta.
 */
export function buildPlan(input) {
  if (ts.version !== '6.0.3') throw new Error('Compilador confiável divergente.');
  const files = new Map(), declarations = {}, points = {}, targets = {}, limitations = [];
  for (const file of input.files) {
    if (!/\.[cm]?[jt]sx?$/.test(file.path)) continue;
    if (files.has(file.path)) throw new Error('Fonte duplicada no plano.');
    const ast = ts.createSourceFile(file.path, file.content, ts.ScriptTarget.Latest, true);
    const calls = [], functions = [];
    /** Indexa fonte inteira, incluindo declarações não associadas às candidatas. */
    function visit(node) {
      if (ts.isCallExpression(node)) {
        const start = node.getStart(ast), end = node.end;
        const registrationOffset = ts.isCallExpression(node.expression) ? node.expression.end
          : ts.isPropertyAccessExpression(node.expression) ? node.expression.name.getStart(ast) : node.expression.getStart(ast);
        const position = ast.getLineAndCharacterOfPosition(registrationOffset);
        const last = ast.getLineAndCharacterOfPosition(ts.isCallExpression(node.expression) ? node.expression.end - 1 : registrationOffset);
        const id = positionId('declaration', file.path, start, end);
        const declaration = { id, file: file.path, start, end, modifiers: [],
          registration_line: position.line + 1, registration_column: position.character + 1,
          registration_kind: ts.isCallExpression(node.expression) ? 'application' : 'callee',
          application_last_line: last.line + 1, application_last_column: last.character + 1 };
        declarations[id] = declaration;
        calls.push({ node, declaration });
      }
      if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)
          && (ts.isClassDeclaration(node.parent) || ts.isClassExpression(node.parent))) && node.body) functions.push(node);
      ts.forEachChild(node, visit);
    }
    visit(ast);
    files.set(file.path, { path: file.path, ast, calls, functions, source: file.content,
      plan: { path: file.path, sha256: sourceHash(file.content), targets: [], points: [] } });
  }
  for (const unit of input.instances) {
    const original = unit.function, file = files.get(original.path);
    const matches = file?.functions.filter(/** Exige texto, assinatura e linhas originais, sem escolher homônimo. */
      function same(node) {
        const startLine = file.ast.getLineAndCharacterOfPosition(node.getStart(file.ast)).line + 1;
        const ordinal = file.functions.filter(/** Reproduz ordinal lexical da T1 entre unidades na mesma linha. */
          function preceding(value) {
            return value.getStart(file.ast) < node.getStart(file.ast)
              && file.ast.getLineAndCharacterOfPosition(value.getStart(file.ast)).line + 1 === startLine;
          }).length;
        const identity = input.identity ? createHash('sha256').update(JSON.stringify([
          input.identity.repository_id, input.identity.commit_sha, original.path,
          [original.start_line, original.end_line, ordinal], original.kind, original.name,
        ])).digest('hex') : unit.instance_id;
        return identity === unit.instance_id && node.getText(file.ast) === original.reference_code
          && file.ast.getLineAndCharacterOfPosition(node.getStart(file.ast)).line + 1 === original.start_line
          && file.ast.getLineAndCharacterOfPosition(node.end).line + 1 === original.end_line
          && file.source.slice(node.getStart(file.ast), node.body.getStart(file.ast)).trimEnd() === original.signature;
      }) ?? [];
    if (matches.length !== 1) {
      limitations.push({ instance_id: unit.instance_id, reason: 'Intervalo original ambíguo ou ausente; ordinal não recuperável somente deste texto.' });
      continue;
    }
    const target = { id: unit.instance_id, path: original.path, start: matches[0].getStart(file.ast), end: matches[0].end };
    file.plan.targets.push(target);
    targets[target.id] = target;
    // Agrupa registros T1 idênticos: duas chamadas na mesma linha exigem cardinalidade AST igual.
    const associations = new Map();
    for (const test of unit.tests) {
      const key = JSON.stringify(test);
      const group = associations.get(key) ?? { test, count: 0 };
      group.count++; associations.set(key, group);
    }
    for (const { test, count } of associations.values()) {
      const testFile = files.get(test.path);
      const registrations = testFile?.calls.filter(/** Liga intervalo estático a callback inline, não ao título. */
        function same(record) {
          const node = record.node;
          return testFile.ast.getLineAndCharacterOfPosition(node.getStart(testFile.ast)).line + 1 === test.start_line
            && testFile.ast.getLineAndCharacterOfPosition(node.end).line + 1 === test.end_line
            && node.arguments.some(/** Exige corpo inline para excluir helper/registro indireto. */
              function callback(argument) { return ts.isFunctionExpression(argument) || ts.isArrowFunction(argument); });
        }) ?? [];
      if (registrations.length !== 1) {
        limitations.push({ instance_id: target.id, association: test, reason: 'Declaração do teste ambígua pelos intervalos publicados.' });
        continue;
      }
      const registration = registrations[0];
      const callback = registration.node.arguments.find(/** Recupera o callback inline comprovado. */
        function inline(argument) { return ts.isFunctionExpression(argument) || ts.isArrowFunction(argument); });
      const direct = [];
      /** Exclui corpos diferidos e mantém chamadas imediatas do callback original. */
      function visitDirect(node) {
        if (ts.isFunctionLike(node) && node !== callback) return;
        if (ts.isCallExpression(node)
            && testFile.ast.getLineAndCharacterOfPosition(node.getStart(testFile.ast)).line + 1 === test.association.call_line
            && node.expression.getText(testFile.ast) === test.association.expression) direct.push(node);
        ts.forEachChild(node, visitDirect);
      }
      visitDirect(callback);
      if (direct.length !== count || test.association.resolved_target.path !== target.path
          || test.association.resolved_target.start_line !== original.start_line) {
        limitations.push({ instance_id: target.id, association: test, reason: 'Callsites AST não correspondem à cardinalidade/alvo da evidência T1.' });
        continue;
      }
      registration.declaration.modifiers = test.modifiers;
      registration.declaration.associated = true;
      for (const node of direct) {
        const id = positionId('point', test.path, node.getStart(testFile.ast), node.end);
        if (points[id] && points[id].instance_id !== target.id) throw new Error('Callsite associado a alvos divergentes.');
        const point = { id, instance_id: target.id, declaration_id: registration.declaration.id,
          path: test.path, start: node.getStart(testFile.ast), end: node.end, association: test.association };
        if (!points[id]) testFile.plan.points.push(point);
        points[id] = point;
      }
    }
  }
  const plans = [];
  for (const file of files.values()) {
    if (!file.plan.targets.length && !file.plan.points.length) continue;
    const result = transformSource(file.source, file.plan, '__robo_validation_probe');
    limitations.push(...result.limitations);
    plans.push(file.plan);
  }
  return { protocol_version: 1, declarations, points, targets, plans, limitations };
}

/** Executa geração de metadados confiável, sem importar qualquer módulo candidato. */
async function main() {
  const buffers = [];
  for await (const chunk of process.stdin) buffers.push(chunk);
  console.log(JSON.stringify(buildPlan(JSON.parse(Buffer.concat(buffers).toString('utf8')))));
}
if (process.argv[1] && new URL(import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\')
    === process.argv[1].replaceAll('/', '\\')) await main();

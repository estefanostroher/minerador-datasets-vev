import ts from 'typescript';
import { pathToFileURL } from 'node:url';

class InvalidInput extends Error {}

/** Mede exclusivamente declarações identificadas na fonte original. */
export function measureTargets(input) {
  if (ts.version !== '6.0.3') throw new Error('Compilador incompatível.');
  if (!input || !Array.isArray(input.files) || !Array.isArray(input.targets)) throw new InvalidInput();
  const files = new Map(), ids = new Set();
  for (const file of input.files) {
    if (!file || typeof file.path !== 'string' || typeof file.content !== 'string' || files.has(file.path)) throw new InvalidInput();
    const ast = ts.createSourceFile(file.path, file.content, ts.ScriptTarget.Latest, true);
    if (ast.parseDiagnostics.length) throw new InvalidInput();
    const nodes = new Map();
    function index(node) {
      if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.body)
        nodes.set(`${node.getStart(ast)}:${node.end}`, node);
      ts.forEachChild(node, index);
    }
    index(ast);
    files.set(file.path, { ast, nodes });
  }
  const measurements = input.targets.map(target => {
    if (!target || typeof target.instance_id !== 'string' || !target.instance_id || ids.has(target.instance_id)
        || !Number.isSafeInteger(target.start) || !Number.isSafeInteger(target.end)
        || typeof target.reference_code !== 'string') throw new InvalidInput();
    ids.add(target.instance_id);
    const file = files.get(target.path), node = file?.nodes.get(`${target.start}:${target.end}`);
    if (!node || (target.kind === 'function_declaration' ? !ts.isFunctionDeclaration(node)
      : target.kind === 'class_method' ? !(ts.isMethodDeclaration(node) && (ts.isClassDeclaration(node.parent) || ts.isClassExpression(node.parent))) : true)
      || file.ast.text.slice(target.start, target.end) !== target.reference_code) throw new InvalidInput();
    let complexity = 1;
    function visit(child) {
      if (ts.isFunctionLike(child) || ts.isClassLike(child)) return;
      if (ts.isIfStatement(child) || ts.isForStatement(child) || ts.isForInStatement(child)
          || ts.isForOfStatement(child) || ts.isWhileStatement(child) || ts.isDoStatement(child)
          || ts.isCaseClause(child) || ts.isCatchClause(child) || ts.isConditionalExpression(child)) complexity++;
      if (ts.isBinaryExpression(child) && [ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken,
        ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.AmpersandAmpersandEqualsToken,
        ts.SyntaxKind.BarBarEqualsToken, ts.SyntaxKind.QuestionQuestionEqualsToken].includes(child.operatorToken.kind)) complexity++;
      ts.forEachChild(child, visit);
    }
    visit(node.body);
    const physical_lines = file.ast.getLineAndCharacterOfPosition(node.end - 1).line
      - file.ast.getLineAndCharacterOfPosition(node.getStart(file.ast)).line + 1;
    return { instance_id: target.instance_id, physical_lines, cyclomatic_complexity: complexity };
  });
  return { policy: 'typescript_function_metrics_v1', typescript: ts.version, measurements };
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  let input;
  try { input = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw new InvalidInput(); }
  process.stdout.write(JSON.stringify(measureTargets(input)));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => { process.exitCode = error instanceof InvalidInput ? 2 : 1; });
}

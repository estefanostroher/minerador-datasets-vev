import ts from 'typescript';
import { pathToFileURL } from 'node:url';

/** Confere declarações e chamadas creditadas somente sobre fontes originais em memória. */
export function validateBindings(input) {
  if (ts.version !== '6.0.3') throw new Error('Compilador incompatível.');
  const files = new Map();
  for (const file of input.files) {
    if (files.has(file.path)) throw new Error('Fonte duplicada.');
    const ast = ts.createSourceFile(file.path, file.content, ts.ScriptTarget.Latest, true);
    if (ast.parseDiagnostics.length) throw new Error('Fonte sintaticamente inválida.');
    const calls = new Map();
    /** Indexa cada chamada pelos offsets UTF-16 originais. */
    function visit(node) {
      if (ts.isCallExpression(node)) calls.set(`${node.getStart(ast)}:${node.end}`, node);
      ts.forEachChild(node, visit);
    }
    visit(ast);
    files.set(file.path, { ast, calls });
  }
  for (const binding of input.bindings) {
    const file = files.get(binding.path);
    if (!file) throw new Error('Fonte ausente.');
    const { ast, calls } = file;
    const declaration = calls.get(`${binding.declaration_start}:${binding.declaration_end}`);
    const point = calls.get(`${binding.point_start}:${binding.point_end}`);
    const callback = declaration?.arguments.find(arg => ts.isArrowFunction(arg) || ts.isFunctionExpression(arg));
    let direct = false;
    /** Percorre o primeiro callback sem entrar em corpos de funções aninhadas. */
    function visitDirect(node) {
      if (ts.isFunctionLike(node) && node !== callback) return;
      if (node === point) direct = true;
      ts.forEachChild(node, visitDirect);
    }
    if (callback) visitDirect(callback);
    if (!declaration || !point || point.getStart(ast) < declaration.getStart(ast) || point.end > declaration.end
        || !callback || !direct
        || ast.getLineAndCharacterOfPosition(declaration.getStart(ast)).line + 1 !== binding.start_line
        || ast.getLineAndCharacterOfPosition(declaration.end).line + 1 !== binding.end_line
        || ast.getLineAndCharacterOfPosition(point.getStart(ast)).line + 1 !== binding.call_line
        || point.expression.getText(ast) !== binding.expression) throw new Error('Vínculo sintático divergente.');
  }
  return { ok: true };
}

/** Lê protocolo interno sem importar ou executar módulos do projeto candidato. */
async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  process.stdout.write(JSON.stringify(validateBindings(JSON.parse(Buffer.concat(chunks).toString('utf8')))));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => { process.exitCode = 1; });
}

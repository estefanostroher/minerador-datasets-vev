import ts from 'typescript';
import { createHash } from 'node:crypto';

/** Calcula hash dos bytes textuais verificados antes de qualquer edição em memória. */
export function sourceHash(source) { return createHash('sha256').update(source).digest('hex'); }

/** Instrumenta somente offsets AST únicos de um plano previamente verificado.
 *
 * O resultado mantém cada byte original fora das inserções. Não transpila nem
 * grava fontes. Optional chaining, super, private/computed, eval, generators e
 * parâmetros com inicialização/desestruturação ficam explicitamente pendentes.
 * O consumidor deve tratar qualquer limitação como observação incompleta.
 */
export function transformSource(source, plan, runtimeExpression, prelude = '') {
  if (ts.version !== '6.0.3') throw new Error('TypeScript confiável deve ser 6.0.3.');
  if (sourceHash(source) !== plan.sha256) throw new Error('Fonte diverge do plano verificado.');
  if (source.startsWith('#!')) throw new Error('Shebang não suportado nesta transformação.');
  if (/\r(?!\n)|[\u2028\u2029]/.test(source)) throw new Error('Separador de linha não suportado pelo source map.');
  if (!/^\w+$/.test(runtimeExpression) || source.includes(runtimeExpression)) {
    throw new Error('Identificador do instrumento colide com fonte original.');
  }
  const ast = ts.createSourceFile(plan.path, source, ts.ScriptTarget.Latest, true);
  if (ast.parseDiagnostics.length) throw new Error('Fonte não pôde ser analisada sem erros.');
  const nodes = new Map();
  const edits = [];
  const limitations = [];
  const supportedTargets = new Set();
  let targetOrdinal = 0;
  let preludeOffset = 0;
  for (const statement of ast.statements) {
    if (!ts.isExpressionStatement(statement) || !ts.isStringLiteral(statement.expression)) break;
    preludeOffset = statement.end;
  }
  if (prelude) edits.push({ start: preludeOffset, end: preludeOffset, text: prelude });

  /** Indexa intervalos exatos, sem associação por nome/linha aproximada. */
  function visit(node) {
    nodes.set(`${node.getStart(ast)}:${node.end}`, node);
    ts.forEachChild(node, visit);
  }
  visit(ast);

  /** Registra limitação localizada sem transformar a forma incerta. */
  function limit(item, reason) { limitations.push({ id: item.id, path: plan.path, start: item.start, end: item.end, reason }); }

  for (const target of plan.targets) {
    const node = nodes.get(`${target.start}:${target.end}`);
    if (!node || !(ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) || !node.body
        || node.asteriskToken || node.parameters.some(/** Recusa efeitos anteriores à entrada no corpo. */
          function hasParameterEffects(parameter) { return parameter.initializer || !ts.isIdentifier(parameter.name); })) {
      limit(target, 'Corpo original ou parâmetros não suportados.');
      continue;
    }
    const original = runtimeExpression + '_original_' + targetOrdinal++;
    if (ts.isFunctionDeclaration(node) && node.name && ts.isSourceFile(node.parent)) {
      // Captura binding hoisted antes de qualquer substituição feita pelo candidato.
      let captureOffset = 0;
      for (const statement of ast.statements) {
        if (!ts.isExpressionStatement(statement) || !ts.isStringLiteral(statement.expression)) break;
        captureOffset = statement.end;
      }
      edits.push({ start: captureOffset, end: captureOffset, text: `;const ${original}=${node.name.text};` });
    } else if (ts.isMethodDeclaration(node) && ts.isIdentifier(node.name)
        && ts.isClassDeclaration(node.parent) && node.parent.name
        && ts.isSourceFile(node.parent.parent) && !node.parent.heritageClauses?.length
        && !ts.getDecorators(node.parent)?.length
        && !node.parent.members.some(/** Recusa execução durante definição ou decoradores. */
          function hasClassEffects(member) {
            return ts.isClassStaticBlockDeclaration(member) || ts.isPropertyDeclaration(member)
              || !!member.name && ts.isComputedPropertyName(member.name) || !!ts.getDecorators(member)?.length;
          })) {
      const isStatic = node.modifiers?.some(/** Identifica método estático. */
        function isStaticModifier(modifier) { return modifier.kind === ts.SyntaxKind.StaticKeyword; });
      const receiver = node.parent.name.text + (isStatic ? '' : '.prototype');
      edits.push({ start: node.parent.end, end: node.parent.end,
        text: `;const ${original}=${runtimeExpression}.originalMethod(${receiver},${JSON.stringify(node.name.text)});` });
    } else {
      limit(target, 'Identidade imutável do callable original não pôde ser capturada.');
      continue;
    }
    // Preserva prólogo de diretivas, incluindo strict de funções não módulo.
    let offset = node.body.getStart(ast) + 1;
    for (const statement of node.body.statements) {
      if (!ts.isExpressionStatement(statement) || !ts.isStringLiteral(statement.expression)) break;
      offset = statement.end;
    }
    edits.push({ start: offset, end: offset, text: `;${runtimeExpression}.enter(${JSON.stringify(target.id)},${original});` });
    supportedTargets.add(target.id);
  }
  for (const point of plan.points) {
    const node = nodes.get(`${point.start}:${point.end}`);
    if (!node || !ts.isCallExpression(node) || node.questionDotToken
        || node.flags & ts.NodeFlags.OptionalChain || node.typeArguments?.length) {
      limit(point, 'Chamada opcional, genérica ou intervalo ambíguo não suportado.');
      continue;
    }
    const expression = node.expression;
    const prefix = `${JSON.stringify(point.id)},${JSON.stringify(point.instance_id)},`;
    if (ts.isIdentifier(expression) && expression.text !== 'eval') {
      // VoidExpression não consulta um binding undefined potencialmente sombreado.
      const receiver = ts.createPrinter().printNode(ts.EmitHint.Expression,
        ts.factory.createVoidExpression(ts.factory.createNumericLiteral(0)), ast);
      edits.push({ start: expression.getStart(ast), end: expression.end,
        text: `${runtimeExpression}.prepare(${prefix}${expression.getText(ast)},${receiver})` });
    } else if (ts.isPropertyAccessExpression(expression) && !expression.questionDotToken
        && !ts.isPrivateIdentifier(expression.name) && expression.expression.kind !== ts.SyntaxKind.SuperKeyword) {
      edits.push({ start: expression.getStart(ast), end: expression.end,
        text: `${runtimeExpression}.member(${prefix}${expression.expression.getText(ast)},${JSON.stringify(expression.name.text)})` });
    } else limit(point, 'Callee computed, super, private ou expressão indireta não suportada.');
  }
  edits.sort(/** Ordena edições para detectar sobreposição antes da aplicação inversa. */
    function compare(left, right) { return left.start - right.start || left.end - right.end; });
  for (let index = 1; index < edits.length; index++) {
    if (edits[index].start < edits[index - 1].end) throw new Error('Transformações sobrepostas não suportadas.');
  }
  let code = '', cursor = 0;
  const origins = [];
  /** Acrescenta segmento mantendo offset UTF-16 original para cada coluna gerada. */
  function append(text, offset, inserted) {
    code += text;
    for (let index = 0; index < text.length; index++) origins.push(offset + (inserted ? 0 : index));
  }
  for (const edit of edits) {
    append(source.slice(cursor, edit.start), cursor, false);
    append(edit.text, edit.start, true);
    cursor = edit.end;
  }
  append(source.slice(cursor), cursor, false);
  const map = sourceMap(code, source, origins, plan.path);
  return { code, map, edits, limitations, supported_targets: [...supportedTargets], source_sha256: plan.sha256 };
}

/** Codifica inteiro com sinal conforme Base64-VLQ de source maps v3. */
function vlq(value) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let encoded = '', remaining = value < 0 ? -value * 2 + 1 : value * 2;
  do {
    let digit = remaining % 32;
    remaining = Math.floor(remaining / 32);
    if (remaining) digit |= 32;
    encoded += alphabet[digit];
  } while (remaining);
  return encoded;
}

/** Relaciona cada coluna gerada à origem, inclusive inserções sem linhas artificiais. */
function sourceMap(code, source, origins, path) {
  const original = ts.createSourceFile(path, source, ts.ScriptTarget.Latest);
  let previousLine = 0, previousColumn = 0, generatedColumn = 0, previousGenerated = 0;
  const lines = [[]];
  for (let index = 0; index < code.length; index++) {
    if (code[index] === '\n') {
      lines.push([]); generatedColumn = 0; previousGenerated = 0;
      continue;
    }
    const position = original.getLineAndCharacterOfPosition(origins[index]);
    lines.at(-1).push(vlq(generatedColumn - previousGenerated) + 'A'
      + vlq(position.line - previousLine) + vlq(position.character - previousColumn));
    previousLine = position.line; previousColumn = position.character;
    previousGenerated = generatedColumn++;
  }
  return { version: 3, sources: [path], sourcesContent: [source], names: [],
    mappings: lines.map(/** Separa segmentos por coluna e linhas por ponto e vírgula. */
      function serialize(segments) { return segments.join(','); }).join(';') };
}

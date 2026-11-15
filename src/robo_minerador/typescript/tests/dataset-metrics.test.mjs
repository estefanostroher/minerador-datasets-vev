import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import { measureTargets } from '../dataset-metrics.mjs';

function input(content, reference_code, kind = 'function_declaration') {
  const ast = ts.createSourceFile('file.ts', content, ts.ScriptTarget.Latest, true);
  let target;
  function visit(node) {
    if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.getText(ast) === reference_code) target = node;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return { files: [{ path: 'file.ts', content }], targets: [{ instance_id: 'a', path: 'file.ts', start: target?.getStart(ast), end: target?.end, kind, reference_code }] };
}

test('conta cada constructo e ignora fluxo fora do corpo alvo', () => {
  const cases = [
    ['', 1], ['x?.y;', 1], ['if (x) {}', 2], ['if (x) {} else if (y) {}', 3],
    ['for (;;) {}', 2], ['for (const a in b) {}', 2], ['for (const a of b) {}', 2],
    ['while (x) {}', 2], ['do {} while (x);', 2],
    ['switch (x) { case 1: break; case 2: break; default: break; }', 3],
    ['try {} catch (e) {}', 2], ['x ? a : b;', 2],
    ...['&&', '||', '??', '&&=', '||=', '??='].map(op => [`a ${op} b;`, 2]),
    ['function nested() { if (x) {} } const g = () => x && y; class C { m() { if (x) {} } }', 1],
  ];
  for (const [body, expected] of cases) {
    const code = `function f(x = a && b) { ${body} }`;
    assert.equal(measureTargets(input(code, code)).measurements[0].cyclomatic_complexity, expected, body);
  }
});

test('método com CRLF e offsets UTF-16 após emoji exclui documentação', () => {
  const code = 'm(x: number) {\r\n return x ? 1 : 0;\r\n}';
  const source = '/* 😀 */\r\nclass C {\r\n/** doc */\r\n' + code + '\r\n}';
  assert.deepEqual(measureTargets(input(source, code, 'class_method')).measurements[0], { instance_id: 'a', physical_lines: 3, cyclomatic_complexity: 2 });
});

test('recusa alvo divergente e erro sintático', () => {
  const code = 'function f() {}';
  const bad = input(code, code); bad.targets[0].end--;
  assert.throws(() => measureTargets(bad));
  const syntax = input(code, code); syntax.files[0].content += '\nfunction broken( {';
  assert.throws(() => measureTargets(syntax));
});

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Project } from 'ts-morph';
import { cyclomaticComplexity, measureTarget } from '../src/metrics.js';

test('cyclomatic-ts-v1 skips nested function and class units', () => {
  const file = new Project({ useInMemoryFileSystem: true }).createSourceFile('a.ts', 'function f(x:number){const g=()=>x?1:0;return x>0?2:3;}');
  assert.equal(cyclomaticComplexity(file.getFunctionOrThrow('f')), 2);
});
test('counts expression-body decisions but skips nested parameter defaults', () => {
  const file = new Project({ useInMemoryFileSystem: true }).createSourceFile('a.ts', 'const f = (x:number) => x ? 1 : 0; function g(x = true ? 1 : 0) { return x; }');
  assert.equal(cyclomaticComplexity(file.getVariableDeclarationOrThrow('f').getInitializerOrThrow()), 2);
  assert.equal(cyclomaticComplexity(file.getFunctionOrThrow('g')), 1);
});
test('records actual decisions and physical CRLF lines of the original interval', () => {
  const code = 'function f(x:number) {\r\n // 😀 if fake\r\n class C { m(){if(x)return 1;} }\r\n if(x && x || x ?? 0) {}\r\n for(;;) {break;} while(x) {break;} do {} while(x);\r\n try {} catch(e) {}\r\n switch(x){case 1:break;default:break;}\r\n return x ? 1 : 0;\r\n}';
  const node = new Project({ useInMemoryFileSystem: true }).createSourceFile('a.ts', code).getFunctionOrThrow('f');
  const result = measureTarget(node, 'sha256:' + 'a'.repeat(64));
  assert.equal(result.cyclomatic_complexity, 11);
  assert.equal(result.decisions.length, 10);
  assert.equal(result.physical_lines, 9);
  assert.equal(result.method, 'cyclomatic-ts-v1');
  for (const decision of result.decisions) assert.equal(code.slice(decision.start_offset, decision.end_offset), decision.text);
});

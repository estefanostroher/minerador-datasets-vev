import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Project } from 'ts-morph';
import { assessTriviality } from '../src/triviality.js';

function assess(code: string) {
  const file = new Project({ useInMemoryFileSystem: true }).createSourceFile('a.ts', code);
  const node = file.getFunctions()[0] ?? file.getClasses()[0]?.getMembers()[0];
  assert.ok(node);
  return assessTriviality(node);
}
for (const [code, reason] of [
  ['function f(x:number){return 42;}', 'constant_literal_return'],
  ['class C { get value(){return this.value_;} }', 'simple_getter'],
  ['class C { set value(x:number){this.value_=x;} }', 'simple_setter'],
  ['function f(x:number){return other(x);}', 'direct_delegation'],
]) test(reason, () => {
  const result = assess(code);
  assert.equal(result.status, 'rejected');
  assert.equal(result.reason, reason);
  assert.ok(result.evidence.length);
});
for (const code of [
  'function f(x:number){if(x<0)throw Error();return other(x);}',
  'function f(x:number){return x ? 1 : 2;}',
  'function f(x:number){return x+1;}',
]) test('logic is not trivial: ' + code, () => assert.equal(assess(code).status, 'approved'));
for (const code of [
  'function f(x:number){return x;}',
  'function f(){return {};}',
  'function f(){return [];}',
  'function f(x:number){return other(x+1);}',
  'function f(x=effect()){return 1;}',
  'function f({x}:T){return 1;}',
  'class C { get value(){return this.value_+1;} }',
  'function f(x:number){return other(effect());}',
  'async function f(){return 1;}',
  'function* f(){return 1;}',
  'function f(x:number){return f(x);}',
]) test('unproven shape stays inconclusive: ' + code, () => assert.equal(assess(code).status, 'not_evaluated'));

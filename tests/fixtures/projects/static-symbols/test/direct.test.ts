import { test, it, describe, expect, beforeEach } from 'vitest';
import * as suite from 'vitest';
import { renamed as alias, overloaded, Counter } from '@lib/barrel';
import { same } from '../src/second';
function helper() { alias(90); }
beforeEach(() => alias(80));
describe('group', () => {
  test('first', () => { alias(1); expect(() => alias(2)).toThrow(); helper(); });
  it.each([1, 2])('second %s', value => { same(value); overloaded(value); });
  suite.test('method', () => { new Counter().increment(2); });
  test('ambiguous', () => { let receiver: any; receiver.increment(1); });
  test('property', () => { const receiver = { increment: (n: number) => n }; receiver.increment(1); });
  test('nested helper', () => { function local() { alias(100); } local(); });
});
function shadow(test: (title: string, callback: () => void) => void) {
  test('not a case', () => alias(200));
}

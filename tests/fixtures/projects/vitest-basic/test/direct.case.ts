import { test, expect, beforeEach, afterAll, vi } from 'vitest';
import * as valueModule from '../value';
import * as dependency from '../dependency';
import { magnitude as alias } from '../value';
import { magnitude as reexported } from '../barrel';
import { Counter, Other, delayed } from '../service';
import { writeFileSync } from 'node:fs';

const observables: unknown[] = [];
beforeEach(() => { valueModule.magnitude(5); });
function helper() { return valueModule.magnitude(-2); }
function argumentHelper() { observables.push('argument'); valueModule.magnitude(6); return -3; }
test('alias', () => { observables.push(alias(-1)); expect(alias(-1)).toBe(1); });
test('reexport', () => { observables.push(reexported(-2)); expect(reexported(-2)).toBe(2); });
test('resolved method', () => { const c = new Counter(3); observables.push(c.add(2)); expect(c.add(1)).toBe(6); });
test('other class', () => { const c = new Other(); observables.push(c.add(2)); expect(c.add(2)).toBe(12); });
test('false branch', () => { if (false) valueModule.magnitude(9); expect(true).toBe(true); });
test('indirect helper', () => { expect(helper()).toBe(2); });
test('named local helper', () => { function nested() { return valueModule.magnitude(-4); } expect(nested()).toBe(4); });
test('hook only', () => { expect(1).toBe(1); });
test('mocked target', () => {
  const spy = vi.spyOn(valueModule, 'magnitude').mockReturnValue(9);
  expect(valueModule.magnitude(1)).toBe(9); spy.mockRestore();
});
test('mock wrapper calls original', () => {
  const original = valueModule.magnitude;
  const spy = vi.spyOn(valueModule, 'magnitude').mockImplementation(n => original(n));
  expect(valueModule.magnitude(1)).toBe(1); spy.mockRestore();
});
test('dependency mocked', () => {
  const spy = vi.spyOn(dependency, 'adjust').mockReturnValue(7);
  const c = new Counter(1); expect(c.add(2)).toBe(8); observables.push(c.value); spy.mockRestore();
});
test('argument evaluation', () => { observables.push(valueModule.magnitude(argumentHelper())); });
test('expected assertion exception', () => { expect(() => valueModule.magnitude(Infinity)).toThrow('finite'); });
test('async return', async () => { observables.push(await delayed(8)); expect(await delayed(2)).toBe(2); });
test.each([[1, 1], [-1, 1], [1, 1]])('duplicate tuple', (n, expected) => { expect(valueModule.magnitude(n)).toBe(expected); });
test.skip('skipped direct', () => { valueModule.magnitude(0); });
afterAll(() => { writeFileSync('/workspace/.robo-observables.json', JSON.stringify(observables)); });

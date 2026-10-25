import { describe, test, expect } from 'vitest';
import { magnitude } from './value';
// Astral prefix ensures positions are UTF-16: 🧪
describe('magnitude', () => {
  test.each([-2, -1, 0, 1])('same', value => { expect(magnitude(value)).toBe(Math.abs(value)); });
  test.skip('skipped', () => { expect(magnitude(2)).toBe(2); });
  test.todo('todo');
  test.fails('expected failure', () => { expect(magnitude(1)).toBe(99); });
  test('same', () => { expect(magnitude(3)).toBe(3); });
  test('same', () => { expect(magnitude(4)).toBe(4); });
  test('conventional', () => { expect(() => magnitude(Infinity)).toThrow('finite'); });
});

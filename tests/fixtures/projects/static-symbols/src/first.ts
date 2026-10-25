// astral: 😀
/** Documentation stays outside the target. */
export function same(value: number): number { return value + 1; }
export function overloaded(value: string): string;
export function overloaded(value: number): number;
export function overloaded(value: string | number) { return value; }
export class Counter {
  constructor(public value = 0) {}
  increment(step: number) { return this.value += step; }
  get current() { return this.value; }
}
export const arrow = () => 42;

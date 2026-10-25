import { adjust } from './dependency';
export class Counter {
  constructor(public value: number) {}
  add(n: number) { 'use strict'; this.value += adjust(n); return this.value; }
}
export class Other { add(n: number) { return 10 + n; } }
export async function delayed(n: number) { return await Promise.resolve(n); }

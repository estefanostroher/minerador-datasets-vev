/** Observational supplement: one ALS store per runtime, never a global current case. */
import { AsyncLocalStorage } from 'node:async_hooks';
import { appendFileSync, mkdirSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

export interface TraceEvent { kind: string; [key: string]: unknown }
interface CaseContext { file: string; declaration_position: number; runtime_id: string; parameter_index: number | null; active: boolean }
interface Invocation { id: string; site: string; target: string; fn: Function; active: boolean; consumed: boolean }
interface Context { test?: CaseContext; invocation?: Invocation }

export function createRuntime(emit: (event: TraceEvent) => void) {
  const storage = new AsyncLocalStorage<Context>();
  const stream = randomUUID();
  let sequence = 0, invocationSequence = 0;
  function event(kind: string, detail: Record<string, unknown> = {}): void {
    const test = storage.getStore()?.test;
    emit({kind, stream_id: stream, sequence: sequence++, file: test?.file ?? null,
      declaration_position: test?.declaration_position ?? null,
      runtime_id: test?.runtime_id ?? null, parameter_index: test?.parameter_index ?? null, ...detail});
  }
  function bind(fn: Function, receiver: unknown, site: string, target: string) {
    // Callee/receiver/getter evaluation happened already. Arguments evaluate before
    // this function runs, so argument helper calls cannot borrow this invocation.
    return (...args: any[]) => {
      const invocation: Invocation = {id: String(invocationSequence++), site, target, fn, active: true, consumed: false};
      return storage.run({test: storage.getStore()?.test, invocation}, () => {
        event('direct_callsite', {invocation_id: invocation.id, callsite_id: site, target_id: target});
        try { return Reflect.apply(fn, receiver, args); }
        finally { invocation.active = false; }
      });
    };
  }
  return {
    collected(file: string, position: number, runtimeId: string, parameter: number | null): void {
      event('case_collected', {file, declaration_position: position, runtime_id: runtimeId, parameter_index: parameter});
    },
    entry(id: string, original: Function): void {
      const context = storage.getStore();
      const frame = context?.invocation;
      const attributable = Boolean(context?.test?.active && frame?.active && !frame.consumed && frame.target === id && frame.fn === original);
      if (frame) frame.consumed = true;
      event('original_target_entry', {target_id: id, invocation_id: frame?.active ? frame.id : null,
        callsite_id: frame?.active ? frame.site : null, attributable});
    },
    fn(fn: Function, site: string, target: string) { return bind(fn, undefined, site, target); },
    method(receiver: any, key: string, site: string, target: string) { return bind(receiver[key], receiver, site, target); },
    runCase<T>(file: string, position: number, runtimeId: string, parameter: number | null, callback: () => T): T {
      const test: CaseContext = {file, declaration_position: position, runtime_id: runtimeId, parameter_index: parameter, active: true};
      return storage.run({test}, () => {
        event('test_entry');
        const end = (outcome: string) => { test.active = false; event('case_end', {outcome}); };
        let result: T;
        try { result = callback(); }
        catch (error) { end('threw'); throw error; }
        if (result instanceof Promise) {
          return result.then(value => { end('returned'); return value; }, error => { end('threw'); throw error; }) as T;
        }
        end('returned'); return result;
      });
    },
  };
}

// The singleton is shared by overlay modules and the framework's callback wrapper.
// It stores no current case; case state lives exclusively in AsyncLocalStorage.
export const runtime = createRuntime(event => {
  mkdirSync('/workspace/.robo-reports', {recursive: true});
  appendFileSync('/workspace/.robo-reports/trace.jsonl', JSON.stringify({...event, pid: process.pid}) + '\n');
});

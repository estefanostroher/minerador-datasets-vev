import { array, object, relativeFile, emitCaseResults, RuntimeCase, Status, CaseResultSink } from './common.js';

export function parseReport(value: unknown, sources: Record<string, string>, evidenceId: string, emitCaseResult?: CaseResultSink) {
  const raw = object(value);
  if (raw.framework !== 'vitest' || !['3.2.4', '4.0.0'].includes(raw.version)) throw Error('unsupported Vitest version');
  const occurrences: RuntimeCase[] = [];
  function visit(tasks: any[], file: string, titles: string[]): void {
    for (const task of tasks) {
      if (task.type === 'suite') visit(array(task.tasks), file, [...titles, task.name]);
      else if (task.type === 'test') {
        const states: Record<string, Status> = {pass: 'passed', fail: 'failed', skip: 'skipped', todo: 'todo', run: 'not_executed', queued: 'pending', only: 'not_executed'};
        const state = task.result?.state ?? task.mode;
        if (!states[state]) throw Error('unknown Vitest case status');
        occurrences.push({file, title_path: [...titles, task.name], location: task.location,
          status: states[state], duration_ms: task.result?.duration ?? null,
          runtime_id: task.id, expected_failure: task.fails === true});
      }
    }
  }
  for (const file of array(raw.files)) visit(array(file.tasks), relativeFile(file.filepath), []);
  return emitCaseResults('vitest', raw, occurrences, sources, evidenceId, emitCaseResult);
}

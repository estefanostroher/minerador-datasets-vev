import { array, object, relativeFile, emitCaseResults, RuntimeCase, Status, CaseResultSink } from './common.js';

/** Only exact patches exercised with the companion reporter are admitted. */
export function parseReport(value: unknown, sources: Record<string, string>, evidenceId: string, emitCaseResult?: CaseResultSink) {
  const raw = object(value);
  if (raw.framework !== 'jest' || !['29.7.0', '30.0.0'].includes(raw.version)) throw Error('unsupported Jest version');
  const occurrences: RuntimeCase[] = [];
  for (const suite of array(object(raw.results).testResults)) {
    const file = relativeFile(suite.testFilePath);
    for (const item of array(suite.testResults)) {
      const statuses: Record<string, Status> = {passed: 'passed', failed: 'failed', pending: 'pending', skipped: 'skipped', todo: 'todo', disabled: 'not_executed'};
      if (!statuses[item.status]) throw Error('unknown Jest case status');
      occurrences.push({file, title_path: [...array(item.ancestorTitles), item.title],
        location: item.location, status: statuses[item.status], duration_ms: item.duration ?? null,
        runtime_id: '', expected_failure: item.failing === true});
    }
  }
  return emitCaseResults('jest', raw, occurrences, sources, evidenceId, emitCaseResult);
}

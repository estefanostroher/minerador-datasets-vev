import ts from 'typescript';
import { createHash } from 'node:crypto';

export type Framework = 'jest' | 'vitest';
export type Status = 'passed' | 'failed' | 'skipped' | 'pending' | 'todo' | 'not_executed';
export interface RuntimeCase {
  file: string; title_path: string[]; location?: {line: number; column: number};
  status: Status; duration_ms: number | null; runtime_id: string; expected_failure: boolean;
}
export interface CaseResult extends Omit<RuntimeCase, 'location'> {
  schema_version: '1.0'; framework: Framework; framework_version: string;
  declaration_position: number | null; parameter_index: number | null;
  mapping: 'trusted' | 'not_evaluated'; evidence_id: string; identity: string;
}
export type CaseResultSink = (result: CaseResult) => void;
export interface FrameworkReport {
  schema_version: '1.0'; framework: Framework; framework_version: string;
  compiler_version: string; transform: {name: string; version: string};
  cases: CaseResult[]; evidence_id: string; completeness: 'not_evaluated';
  scope: 'unknown'; limitations: string[];
  support: {status: 'tested' | 'not_evaluated'; mode: string; reason: string};
  trace_support: {status: 'tested' | 'not_evaluated'; mode: string; reason: string};
  scope_evidence: {argv: string[]; observed_files: string[]; configuration_evidence_id: string};
}
export function object(value: unknown): Record<string, any> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('expected report object');
  return value as Record<string, any>;
}
export function array(value: unknown): any[] {
  if (!Array.isArray(value)) throw Error('expected report array');
  return value;
}
export function relativeFile(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/workspace/')) throw Error('file outside workspace');
  const file = value.slice('/workspace/'.length);
  if (!file || file.split('/').some(p => !p || p === '.' || p === '..') || /[\\:\0]/.test(file)) throw Error('unsafe report path');
  return file;
}
interface Declaration {position: number; parameterized: boolean; expected: boolean; concurrent: boolean}
function testedCircus(raw: Record<string, any>): boolean {
  const projects = raw.configuration?.projects;
  if (!Array.isArray(projects) || projects.length !== 1) return false;
  const project = projects[0];
  const runner = project?.runner;
  const manifest = runner?.package_json_path;
  if (typeof manifest !== 'string' || !manifest.startsWith('/workspace/') ||
      !manifest.endsWith('/node_modules/jest-circus/package.json') ||
      manifest.split('/').some(part => part === '.' || part === '..') || /[\\:\0]/.test(manifest)) return false;
  const entry = manifest.slice(0, -'package.json'.length) + (raw.version === '29.7.0' ? 'runner.js' : 'build/runner.js');
  return ['29.7.0', '30.0.0'].includes(raw.version) && runner.namespace === 'jest-circus/runner' &&
    runner.package_name === 'jest-circus' && runner.package_version === raw.version &&
    runner.resolved_path === entry && runner.configured_path === entry &&
    runner.configured_realpath === entry && project.testRunner === entry;
}
function declaration(source: string | undefined, location: RuntimeCase['location']): Declaration | undefined {
  if (source === undefined || !location || !Number.isInteger(location.line) || !Number.isInteger(location.column)) return;
  const file = ts.createSourceFile('test.ts', source, ts.ScriptTarget.Latest, true);
  const starts = file.getLineStarts();
  if (location.line < 1 || location.line > starts.length || location.column < 1) return;
  // Both tested APIs expose stack coordinates (line/column base 1). We match
  // the callee interval, not a guessed string index or the test callback body.
  const offset = starts[location.line - 1] + location.column - 1;
  const found: Declaration[] = [];
  let hasConcurrency = false;
  function visit(node: ts.Node): void {
    if (ts.isPropertyAccessExpression(node) && node.name.text === 'concurrent') hasConcurrency = true;
    if (ts.isCallExpression(node) && node.arguments.length > 0 &&
        (ts.isStringLiteral(node.arguments[0]) || ts.isNoSubstitutionTemplateLiteral(node.arguments[0]))) {
      let expression: ts.Expression = node.expression;
      const modifiers: string[] = [];
      let parameterized = false;
      while (ts.isCallExpression(expression) || ts.isTaggedTemplateExpression(expression) || ts.isPropertyAccessExpression(expression)) {
        if (ts.isPropertyAccessExpression(expression)) {
          modifiers.push(expression.name.text);
          expression = expression.expression;
        } else if (ts.isCallExpression(expression)) {
          parameterized = true; expression = expression.expression;
        } else { parameterized = true; expression = expression.tag; }
      }
      if (ts.isIdentifier(expression) && ['test', 'it'].includes(expression.text) &&
          offset >= node.getStart(file) && offset <= node.expression.end) {
        found.push({position: node.getStart(file), parameterized,
          expected: modifiers.includes('failing') || modifiers.includes('fails'), concurrent: modifiers.includes('concurrent')});
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  return found.length === 1 ? {...found[0], concurrent: found[0].concurrent || hasConcurrency} : undefined;
}
export function emitCaseResults(
  framework: Framework, raw: Record<string, any>, occurrences: RuntimeCase[],
  sources: Record<string, string>, evidence_id: string,
  emitCaseResult?: CaseResultSink,
): FrameworkReport {
  if (!/^[a-f0-9]{64}$/.test(evidence_id)) throw Error('invalid evidence digest');
  const mode = raw.configuration?.mode ?? 'missing_configuration';
  const supported = raw.compiler_version === '5.8.3' && (
    (framework === 'jest' && testedCircus(raw) && mode === 'cjs-ts-jest' && raw.transform?.name === 'ts-jest' && raw.transform?.version === '29.4.5' &&
      raw.configuration?.argv?.includes('--runInBand') && !(raw.configuration?.global?.projects?.length > 0)) ||
    (framework === 'vitest' && mode === 'esm-vite' && raw.transform?.name === 'vite' && raw.transform?.version === '6.1.0' &&
      raw.configuration?.projectCount === 1 && raw.configuration?.pool === 'forks' && raw.configuration?.sequence?.concurrent !== true));
  // T10 result parsing is broader than the exact serial mode proved for T11.
  // Missing resolved runtime options are unknown, never implicit serial defaults.
  const traceSupported = supported && (framework === 'jest' || (
    raw.configuration?.fileParallelism === false && raw.configuration?.maxWorkers === 1 &&
    raw.configuration?.sequence?.shuffle !== true));
  const counts = new Map<string, number>();
  const cases: CaseResult[] = occurrences.map(item => {
    const source = sources[item.file];
    const sourceMatches = source !== undefined &&
      createHash('sha256').update(source, 'utf8').digest('hex') === raw.source_hashes?.[item.file];
    const candidate = sourceMatches ? declaration(source, item.location) : undefined;
    const mapped = supported && !candidate?.concurrent ? candidate : undefined;
    const declaration_position = mapped?.position ?? null;
    const key = JSON.stringify([item.file, declaration_position]);
    const occurrence = counts.get(key) ?? 0;
    counts.set(key, occurrence + 1);
    const parameter_index = mapped?.parameterized ? occurrence : null;
    // Runtime IDs disambiguate loop-generated non-each cases as well as equal titles.
    const runtime_id = item.runtime_id || String(occurrence);
    const identity = createHash('sha256').update(JSON.stringify([
      framework, item.file, declaration_position, item.title_path, parameter_index, runtime_id,
    ])).digest('hex');
    const {location: _location, ...rest} = item;
    return {...rest, runtime_id, schema_version: '1.0', framework, framework_version: raw.version,
      declaration_position, parameter_index, identity, evidence_id,
      expected_failure: item.expected_failure || Boolean(candidate?.expected),
      mapping: mapped ? 'trusted' : 'not_evaluated'};
  });
  if (emitCaseResult) cases.forEach(emitCaseResult);
  return {schema_version: '1.0', framework, framework_version: raw.version,
    compiler_version: raw.compiler_version, transform: raw.transform, cases, evidence_id,
    scope: 'unknown', completeness: 'not_evaluated',
    support: {status: supported ? 'tested' : 'not_evaluated', mode,
      reason: supported ? 'exact_patch_baseline_mode' : 'unimplemented_configuration'},
    trace_support: {status: traceSupported ? 'tested' : 'not_evaluated',
      mode: framework === 'vitest' ? 'esm-vite-forks-serial-workers1' : mode,
      reason: traceSupported ? 'exact_patch_trace_mode' : 'unimplemented_trace_configuration'},
    scope_evidence: {argv: raw.configuration?.argv ?? [], observed_files: [...new Set(occurrences.map(c => c.file))],
      configuration_evidence_id: evidence_id},
    limitations: ['Full-suite completeness requires command/config/collection evidence; exit zero is insufficient.',
      ...(cases.some(c => c.mapping === 'not_evaluated') ? ['untrusted_declaration_mapping'] : [])]};
}

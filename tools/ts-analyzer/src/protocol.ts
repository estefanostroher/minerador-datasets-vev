import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';
import { Ajv } from 'ajv';

export interface AnalysisRequest {
  protocol_version: '1.0'; request_id: string; operation: 'analyze_snapshot';
  snapshot_id: string; root: string; tsconfig_paths: string[]; framework: 'jest' | 'vitest';
}
export interface Issue { code: string; message: string; path?: string }
export interface Target {
  id: string; kind: 'function' | 'method'; qualified_name: string; path: string;
  start_offset: number; end_offset: number; start_line: number; start_column: number;
  end_line: number; end_column: number; code: string; signature: string;
}
export interface TestCall {
  symbol_target_id: string | null; callsite_path: string; start_offset: number; end_offset: number;
  case_declaration_id: string; case_title: string; case_start_offset: number; parameterized: boolean;
  link_kind: 'direct' | 'unresolved'; reason: 'resolved_symbol' | 'ambiguous_receiver' | 'unsupported_callee' | 'target_not_found';
  path_resolution: string[]; runtime_confirmed: false; mock_potential: boolean;
}
export interface Evaluation {
  status: 'approved' | 'rejected' | 'not_evaluated'; reason: string; evidence: string[];
}
export interface DocumentMarker {
  kind: 'inputs' | 'outputs' | 'constraints' | 'errors'; text: string; start_offset: number; end_offset: number;
}
export interface DocumentSource {
  path: string; start_offset: number; end_offset: number; text: string;
  association: 'adjacent_jsdoc' | 'overload_jsdoc' | 'markdown_path_symbol';
  symbol: string; markers: DocumentMarker[];
}
export interface Documentation extends Evaluation { symbol_target_id: string; sources: DocumentSource[] }
export interface Decision { kind: string; start_offset: number; end_offset: number; text: string }
export interface Metrics {
  symbol_target_id: string; status: 'approved' | 'not_evaluated'; reason: string;
  method: 'cyclomatic-ts-v1'; cyclomatic_complexity: number | null; physical_lines: number;
  decisions: Decision[]; triviality: Evaluation;
}
export interface AnalysisResponse {
  protocol_version: '1.0'; request_id: string; status: 'ok' | 'error'; targets: Target[];
  test_calls: TestCall[]; documents: Documentation[]; metrics: Metrics[]; limitations: Issue[]; errors: Issue[];
}
const ajv = new Ajv({ strict: true, allErrors: true });
export function schema(name: string): object {
  const candidates = [new URL(`../../schemas/${name}.schema.json`, import.meta.url), new URL(`../../../../schemas/${name}.schema.json`, import.meta.url)];
  const path = candidates.find(p => existsSync(fileURLToPath(p)));
  if (!path) throw new Error('Missing protocol schema');
  return JSON.parse(readFileSync(path, 'utf8')) as object;
}
export const validateRequest = ajv.compile<AnalysisRequest>(schema('analysis-request'));
export const validateResponse = ajv.compile<AnalysisResponse>(schema('analysis-response'));
export function errorResponse(requestId: string, code: string, message: string): AnalysisResponse {
  return { protocol_version: '1.0', request_id: requestId, status: 'error', targets: [], test_calls: [], documents: [], metrics: [], limitations: [], errors: [{ code, message }] };
}

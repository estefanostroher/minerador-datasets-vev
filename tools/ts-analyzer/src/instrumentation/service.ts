import { Ajv } from 'ajv';
import { readFileSync } from 'node:fs';
import { AnalysisRequest, Issue, schema } from '../protocol.js';
import { openSnapshot } from '../project.js';
import { instrument, Instrumentation, runtimeSupplement } from './transform.js';
import type { ServerOptions } from '../server.js';

export interface InstrumentationRequest extends Omit<AnalysisRequest, 'operation'> {
  operation: 'instrument_snapshot'; framework_version: string;
}
export interface InstrumentationResponse {
  protocol_version: '1.0'; request_id: string; snapshot_id: string; status: 'ok' | 'error';
  manifest: Instrumentation | null; supplements: Record<string, string>; errors: Issue[];
}
export const validateInstrumentationRequest = new Ajv({strict: true, allErrors: true}).compile<InstrumentationRequest>(schema('instrumentation-request'));
export async function instrumentSnapshot(request: InstrumentationRequest, options: ServerOptions): Promise<InstrumentationResponse> {
  const programs = await openSnapshot(request.root, request.tsconfig_paths, options.snapshotRoot, {maxFiles: options.maxFiles ?? 10000, maxBytes: options.maxBytes ?? 33554432});
  const manifest = instrument(programs, request.framework, request.framework_version);
  const supplements: Record<string, string> = {};
  if (manifest.status === 'ready') {
    supplements['runtime.cjs'] = runtimeSupplement();
    supplements['manifest.cjs'] = 'module.exports = ' + JSON.stringify(manifest) + ';\n';
    for (const name of ['adapter.cjs', 'jest-environment.cjs', 'jest-setup.cjs', 'vitest-setup.mjs']) {
      supplements[name] = readFileSync(new URL('../../../src/instrumentation/supplements/' + name, import.meta.url), 'utf8');
    }
  }
  return {protocol_version: '1.0', request_id: request.request_id, snapshot_id: request.snapshot_id,
    status: 'ok', manifest, supplements, errors: []};
}

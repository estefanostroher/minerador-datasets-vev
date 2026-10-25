import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { validateRequest, validateResponse, errorResponse, AnalysisResponse } from '../src/protocol.js';
import { createAnalyzerServer } from '../src/server.js';
import { validateInstrumentationRequest } from '../src/instrumentation/service.js';

const request = { protocol_version: '1.0', request_id: 'fixture', operation: 'analyze_snapshot', snapshot_id: 'sha256:' + createHash('sha256').update('static-symbols protocol fixture').digest('hex'), root: '/workspace/snapshots/fixture', tsconfig_paths: ['tsconfig.json'], framework: 'vitest' };
test('strict schemas reject unknown versions, properties and unsafe relative paths', () => {
  assert.equal(validateRequest(request), true);
  for (const change of [{ protocol_version: '2' }, { token: 'secret' }, { tsconfig_paths: ['../x'] }, { tsconfig_paths: ['C:/x'] }, { tsconfig_paths: ['/x'] }, { tsconfig_paths: ['a\\b'] }, { tsconfig_paths: ['a\0b'] }]) {
    assert.equal(validateRequest({ ...request, ...change }), false);
  }
  assert.equal(validateRequest({ ...request, root: '/../etc' }), false);
  assert.equal(validateResponse(errorResponse('fixture', 'invalid_request', 'Invalid request')), true);
  assert.equal(validateResponse({ ...errorResponse('fixture', 'internal_error', 'Failed'), secret: 'x' }), false);
});
test('instrumentation request preserves the strict T08 path envelope', () => {
  const body = {...request, operation: 'instrument_snapshot', framework_version: '4.0.0'};
  assert.equal(validateInstrumentationRequest(body), true);
  for (const root of ['/workspace/../etc', '/workspace/./x', '/workspace\\x', '/workspace/\0']) {
    assert.equal(validateInstrumentationRequest({...body, root}), false, root);
  }
});
test('HTTP health versions, valid analysis, bounded bodies and sanitized failures', async () => {
  const root = resolve('../../tests/fixtures/projects');
  const server = createAnalyzerServer({ snapshotRoot: root, maxBodyBytes: 1024 });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  try {
    const address = server.address();
    assert.ok(address && typeof address !== 'string');
    const url = `http://127.0.0.1:${address.port}`;
    const health = await (await fetch(url + '/health')).json() as Record<string, unknown>;
    assert.equal(health.protocol_version, '1.0');
    assert.equal(health.typescript_version, '5.8.3');
    assert.equal(health.ts_morph_version, '26.0.0');
    const post = (body: unknown) => fetch(url + '/v1/analyze', { method: 'POST', body: JSON.stringify(body) });
    const response = await post({ ...request, root: resolve(root, 'static-symbols').replaceAll('\\', '/') });
    assert.equal(response.status, 200);
    const data = await response.json() as AnalysisResponse;
    assert.equal(validateResponse(data), true, JSON.stringify(validateResponse.errors));
    assert.equal(data.request_id, request.request_id);
    assert.equal(data.documents.length, 4);
    assert.equal(data.metrics.length, 4);
    assert.ok(data.metrics.every(metric => metric.method === 'cyclomatic-ts-v1'));
    assert.equal((await post({ ...request, protocol_version: '9' })).status, 400);
    const escape = await post({ ...request, root: '/etc' });
    assert.equal(escape.status, 400);
    assert.ok(!(await escape.text()).includes('/etc'));
    assert.equal((await post({ value: 'x'.repeat(2048) })).status, 413);
  } finally { await new Promise<void>(r => server.close(() => r())); }
});
test('analysis worker deadline returns a sanitized structured 500', async () => {
  const root = resolve('../../tests/fixtures/projects');
  const server = createAnalyzerServer({ snapshotRoot: root, maxBodyBytes: 4096, timeoutMs: 1 });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  try {
    const address = server.address(); assert.ok(address && typeof address !== 'string');
    const response = await fetch(`http://127.0.0.1:${address.port}/v1/analyze`, { method: 'POST', body: JSON.stringify({ ...request, root: resolve(root, 'static-symbols').replaceAll('\\', '/') }) });
    assert.equal(response.status, 500);
    const body = await response.json() as Record<string, unknown>;
    assert.equal(validateResponse(body), true);
    assert.equal(body.request_id, 'fixture');
    assert.ok(!JSON.stringify(body).includes(root));
    assert.deepEqual(body.errors, [{ code: 'internal_error', message: 'Analysis failed' }]);
  } finally { await new Promise<void>(r => server.close(() => r())); }
});

test('instrumentation is a separate strict HTTP operation returning a usable overlay and supplements', async () => {
  const root = resolve('../../tests/fixtures/projects');
  const server = createAnalyzerServer({snapshotRoot: root, maxBodyBytes: 4096});
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  try {
    const address = server.address(); assert.ok(address && typeof address !== 'string');
    const url = `http://127.0.0.1:${address.port}`;
    const body = {...request, operation: 'instrument_snapshot', framework: 'jest', framework_version: '29.7.0',
      root: resolve(root, 'jest-basic').replaceAll('\\', '/')};
    const post = (path: string, value: unknown) => fetch(url + path, {method: 'POST', body: JSON.stringify(value)});
    assert.equal((await post('/v1/analyze', body)).status, 400);
    assert.equal((await post('/v1/instrument', {...body, extra: true})).status, 400);
    const response = await post('/v1/instrument', body);
    assert.equal(response.status, 200);
    const data = await response.json() as any;
    assert.equal(data.request_id, request.request_id);
    assert.equal(data.snapshot_id, request.snapshot_id);
    assert.equal(data.manifest.status, 'ready', JSON.stringify(data.manifest.limitations));
    assert.ok(data.manifest.callsites.some((c: any) => c.case_title === 'expected assertion exception'));
    assert.ok(!data.manifest.callsites.some((c: any) => c.case_title === 'named local helper'));
    assert.ok(data.supplements['runtime.cjs'].includes('AsyncLocalStorage'));
    assert.ok(data.supplements['jest-environment.cjs']);
  } finally { await new Promise<void>(r => server.close(() => r())); }
});

import { createServer, IncomingMessage, ServerResponse } from 'node:http';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { ts } from 'ts-morph';
import { AnalysisRequest, AnalysisResponse, errorResponse, validateRequest, validateResponse } from './protocol.js';
import { openSnapshot, SnapshotError } from './project.js';
import { discoverTargets } from './targets.js';
import { discoverCalls } from './calls.js';
import { discoverDocumentation } from './documentation.js';
import { discoverMetrics } from './metrics.js';
import { InstrumentationRequest, InstrumentationResponse, instrumentSnapshot, validateInstrumentationRequest } from './instrumentation/service.js';

const require = createRequire(import.meta.url);
export interface ServerOptions { snapshotRoot: string; maxBodyBytes: number; maxFiles?: number; maxBytes?: number; timeoutMs?: number; maxMemoryMb?: number }
export async function analyze(request: AnalysisRequest, options: ServerOptions): Promise<AnalysisResponse> {
  const programs = await openSnapshot(request.root, request.tsconfig_paths, options.snapshotRoot, { maxFiles: options.maxFiles ?? 10000, maxBytes: options.maxBytes ?? 33554432 });
  const targets = discoverTargets(programs);
  const calls = discoverCalls(programs, targets.targets, request.framework);
  return {
    protocol_version: '1.0', request_id: request.request_id, status: 'ok',
    targets: targets.targets, test_calls: calls.test_calls,
    documents: discoverDocumentation(programs, targets.targets), metrics: discoverMetrics(programs, targets.targets),
    limitations: [
      ...targets.limitations, ...calls.limitations,
      ...request.tsconfig_paths.map(path => ({
        code: 'source_scope', path,
        message: 'Targets use tsconfig root files, excluding test/fixture/config paths; imported dependencies are context. Installed packages are not loaded from the snapshot.',
      })),
    ],
    errors: [],
  };
}
if (!isMainThread) {
  const { request, options } = workerData as { request: AnalysisRequest | InstrumentationRequest; options: ServerOptions };
  const action = request.operation === 'instrument_snapshot' ? instrumentSnapshot(request, options) : analyze(request, options);
  action.then(response => parentPort!.postMessage({ response })).catch(error => parentPort!.postMessage({ invalid: error instanceof SnapshotError, failed: true }));
}
function isolatedAnalysis(request: AnalysisRequest | InstrumentationRequest, options: ServerOptions): Promise<AnalysisResponse | InstrumentationResponse> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL(import.meta.url), { workerData: { request, options }, resourceLimits: { maxOldGenerationSizeMb: options.maxMemoryMb ?? 384 } });
    const timer = setTimeout(() => { void worker.terminate(); reject(new Error('analysis_timeout')); }, options.timeoutMs ?? 30000);
    worker.once('message', (message: { response?: AnalysisResponse | InstrumentationResponse; invalid?: boolean }) => {
      clearTimeout(timer); void worker.terminate();
      if (message.response) resolve(message.response);
      else reject(message.invalid ? new SnapshotError('invalid_snapshot') : new Error('analysis_failed'));
    });
    worker.once('error', error => { clearTimeout(timer); reject(error); });
    worker.once('exit', code => { clearTimeout(timer); if (code !== 0) reject(new Error('analysis_failed')); });
  });
}
async function readBody(request: IncomingMessage, limit: number): Promise<unknown> {
  const chunks: Buffer[] = []; let bytes = 0;
  for await (const chunk of request) {
    const buffer = Buffer.from(chunk as Uint8Array);
    bytes += buffer.length;
    if (bytes > limit) throw new SnapshotError('body_too_large');
    chunks.push(buffer);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown; }
  catch { throw new SnapshotError('invalid_json'); }
}
function send(response: ServerResponse, status: number, data: object): void {
  response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); response.end(JSON.stringify(data));
}
export function createAnalyzerServer(options: ServerOptions) {
  let busy = false;
  const server = createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/health') {
      send(response, 200, { protocol_version: '1.0', library_version: (require('../../package.json') as { version: string }).version, node_version: process.versions.node, typescript_version: ts.version, ts_morph_version: (require('ts-morph/package.json') as { version: string }).version }); return;
    }
    if (request.method !== 'POST' || !['/v1/analyze', '/v1/instrument'].includes(request.url ?? '')) { send(response, 404, errorResponse('', 'not_found', 'Unknown endpoint')); return; }
    const instrumentation = request.url === '/v1/instrument';
    let id = '';
    try {
      const body = await readBody(request, options.maxBodyBytes);
      if (body && typeof body === 'object' && 'request_id' in body && typeof body.request_id === 'string' && /^[A-Za-z0-9._:-]{1,128}$/.test(body.request_id)) id = body.request_id;
      const valid = instrumentation ? validateInstrumentationRequest(body) : validateRequest(body);
      if (!valid) { send(response, 400, errorResponse(id, 'invalid_request', 'Invalid analysis request')); return; }
      if (busy) { send(response, 503, errorResponse(id, 'analyzer_busy', 'Analyzer is busy')); return; }
      busy = true;
      try {
        const result = await isolatedAnalysis(body as AnalysisRequest | InstrumentationRequest, options);
        if (!instrumentation && !validateResponse(result)) throw new Error('invalid_internal_response');
        send(response, 200, result);
      } finally { busy = false; }
    } catch (error) {
      const tooLarge = error instanceof SnapshotError && error.message === 'body_too_large';
      const invalid = error instanceof SnapshotError;
      send(response, tooLarge ? 413 : invalid ? 400 : 500, errorResponse(id, tooLarge ? 'body_too_large' : invalid ? 'invalid_request' : 'internal_error', tooLarge ? 'Request body exceeds limit' : invalid ? 'Invalid snapshot or request' : 'Analysis failed'));
    }
  });
  server.requestTimeout = 15000; server.headersTimeout = 10000; server.maxConnections = 16;
  return server;
}
function envLimit(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error('Invalid analyzer limit');
  return value;
}
if (isMainThread && process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  createAnalyzerServer({ snapshotRoot: process.env.SNAPSHOT_ROOT ?? '/workspace/snapshots', maxBodyBytes: envLimit('ANALYZER_MAX_BODY_BYTES', 65536), maxFiles: envLimit('ANALYZER_MAX_FILES', 10000), maxBytes: envLimit('ANALYZER_MAX_BYTES', 33554432), timeoutMs: envLimit('ANALYZER_TIMEOUT_MS', 30000), maxMemoryMb: envLimit('ANALYZER_MEMORY_MB', 384) }).listen(3000, '0.0.0.0');
}

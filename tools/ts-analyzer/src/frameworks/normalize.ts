/** Static-only stdin CLI. Never imports project configuration or project modules. */
import { parseReport as jest } from './jest.js';
import { parseReport as vitest } from './vitest.js';
import { restoreReport } from '../instrumentation/reports.js';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const parse = request.raw.framework === 'jest' ? jest : vitest;
const raw = request.instrumentation ? restoreReport(request.raw, request.sources, request.instrumentation) : request.raw;
process.stdout.write(JSON.stringify(parse(raw, request.sources, request.evidence_id)));

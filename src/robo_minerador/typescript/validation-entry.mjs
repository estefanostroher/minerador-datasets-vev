import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { settings, writeEvent, writeRecord } from './validation-channel.mjs';
import { createValidationRunner } from './validation-runner.mjs';

const config = settings();
const installation = config.installations.find(/** Confere instalação herdada do processo Vitest original. */
  function matches(value) { return value.root === process.env.ROBO_VALIDATION_VITEST_ROOT; });
if (!installation) throw new Error('Worker sem instalação Vitest negociada.');
const { TestRunner } = await import(pathToFileURL(path.join(installation.root, 'dist/index.js')).href);
/** Converte caminho nativo do worker para a mesma identidade da fonte verificada. */
function normalizeFile(file) { return path.relative(config.workspace_root, file).replaceAll('\\', '/'); }
/** Publica metadados auxiliares, sem usá-los como censo independente do reporter. */
function writeWorkerRecord(id, value) { writeRecord('worker', id, value); }
export default createValidationRunner(TestRunner, {
  identity: { ...config.identity }, tools: { ...installation.tools, suite_id: process.env.ROBO_VALIDATION_SUITE_ID },
  declarations: Object.values(config.plan.declarations), writeEvent, normalizeFile,
  allow_unassociated: true,
  writeWorkerRecord,
});

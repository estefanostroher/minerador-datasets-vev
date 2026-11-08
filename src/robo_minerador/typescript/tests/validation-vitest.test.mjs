import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const instrument = fileURLToPath(new URL('..', import.meta.url));
const matrix = [
  ['4.1.10', '7.3.6', 'node@sha256:539d4d5b9c0df96bb9d1229264d358dc816e8592cb8867828a580b527e7ad0fa'],
  ['4.1.11', '8.3.0', 'node@sha256:539d4d5b9c0df96bb9d1229264d358dc816e8592cb8867828a580b527e7ad0fa'],
  ['5.0.5', '8.3.4', 'node@sha256:6b9d4ea31e614e29f364bf75758458731eb1e105b80c99667438dd6b3fe262e0'],
  ['5.0.5', '8.3.5', 'node@sha256:1d9e3c29bf8eae6becc330e2546d461b23c1a86ac9207d015b5ea8ba26cbe210'],
];

for (const [version, vite, image] of matrix) {
  test(`fixture Docker Vitest ${version} Vite ${vite} ${image}`, { timeout: 180000,
    skip: process.env.ROBO_TEST_DOCKER !== '1' ? 'Integração Docker requer ROBO_TEST_DOCKER=1; skip não comprova suporte.' : false },
    /** Não usa skip: Docker/rede ausentes falham sem anunciar compatibilidade. */
    function fixture() {
      const result = spawnSync('docker', ['run', '--rm', '--label', 'org.robo-minerador.validation.fixture=t3',
        '--cpus', '4', '--memory', '8g', '--memory-swap', '8g', '--workdir', '/fixture',
        '--mount', `type=bind,source=${instrument},target=/instrument,readonly`, '--entrypoint', 'node', image,
        '/instrument/tests/validation-vitest-fixture.mjs', version, vite], { encoding: 'utf8', timeout: 170000, maxBuffer: 16 * 1024 * 1024 });
      assert.equal(result.error, undefined, 'Docker indisponível: ' + result.error?.message);
      assert.equal(result.status, 0, result.stdout + result.stderr);
      const summary = result.stdout.trim().split('\n').at(-1);
      const report = JSON.parse(summary);
      assert.equal(report.tools.vitest, version);
      assert.equal(report.tools.vite, vite);
      assert.equal(report.cases, 19);
      assert.equal(report.process_census_proven, false);
      assert.equal(report.original_script_bootstrap_proven, false);
      console.log(summary);
    });
}

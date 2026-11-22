import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

test('rejects duplicate local bindings across imports with a localized issue', async () => {
  const { analyzeConfig } = await import('../validation-recipe-analysis.mjs');
  const content = "import { defineConfig as x } from 'vite';\nimport { resolve as x } from 'node:path';\nexport default x({ test: { globals: true } });";
  const result = analyzeConfig({ path: 'vite.config.ts', content });
  assert.equal(result.config, null);
  assert.equal(result.issues[0].code, 'UNIT_SCOPE_UNRESOLVED');
  assert.deepEqual(result.issues[0].evidence, { path: 'vite.config.ts', line: 2, column: 10 });
});

test('rejects optional defineConfig call with source location', async () => {
  const { analyzeConfig } = await import('../validation-recipe-analysis.mjs');
  const content = "import { defineConfig } from 'vite';\nexport default defineConfig?.({test:{globals:true}});";
  const result = analyzeConfig({ path: 'vite.config.ts', content });
  assert.equal(result.config, null);
  assert.equal(result.issues[0].code, 'UNIT_SCOPE_UNRESOLVED');
  assert.deepEqual(result.issues[0].evidence, { path: 'vite.config.ts', line: 2, column: 16 });
});

test('does not unwrap assertions, satisfies or other unmodeled call forms', async () => {
  const { analyzeConfig } = await import('../validation-recipe-analysis.mjs');
  const prefix = "import { defineConfig } from 'vite'; import { resolve } from 'node:path'; ";
  const expressions = [
    'defineConfig({}) as object', 'defineConfig({}) satisfies object',
    '(defineConfig)({})', 'defineConfig<object>({})', 'defineConfig({} as object)',
    "defineConfig({resolve:{alias:{'@':resolve(import.meta.dirname,'src') as string}}})",
    "defineConfig({resolve:{alias:{'@':(resolve)(import.meta.dirname,'src')}}})",
  ];
  for (const expression of expressions) {
    const result = analyzeConfig({ path: 'vite.config.ts', content: `${prefix}export default ${expression};` });
    assert.equal(result.config, null, expression);
    assert.ok(result.issues.some(item => item.code === 'UNIT_SCOPE_UNRESOLVED' && item.evidence.path === 'vite.config.ts'), expression);
  }
});

for (const call of ["resolve<string>(import.meta.dirname, 'src')", "resolve?.(import.meta.dirname, 'src')"]) {
  test(`rejects unsupported alias call ${call} with location`, async () => {
    const { analyzeConfig } = await import('../validation-recipe-analysis.mjs');
    const content = `import { defineConfig } from 'vite';\nimport { resolve } from 'node:path';\nexport default defineConfig({resolve:{alias:{'@':${call}}}});`;
    const result = analyzeConfig({ path: 'vite.config.ts', content });
    assert.equal(result.config, null);
    assert.equal(result.issues[0].code, 'UNIT_SCOPE_UNRESOLVED');
    assert.deepEqual(result.issues[0].evidence, { path: 'vite.config.ts', line: 3, column: content.split('\n')[2].indexOf(call) + 1 });
  });
}

for (const field of ['node', 'npm', 'vitest', 'vite']) {
  test(`rejects prerelease ${field} even without engine constraints`, async () => {
    const { selectCompatibleProfile } = await import('../validation-recipe-analysis.mjs');
    const profile = { node: '22.23.4', npm: '10.9.9', vitest: '4.1.11', vite: '8.3.0' };
    profile[field] += '-beta.1';
    const inputs = { vitest: profile.vitest, vite: profile.vite, npm: profile.npm, nodePins: [], engines: [] };
    const result = selectCompatibleProfile(inputs, [profile]);
    assert.equal(result.profile, null);
    assert.equal(result.issues[0].code, 'AUTO_RECIPE_ENVIRONMENT_UNSUPPORTED');
  });
}

test('exports the recipe analysis interface', async () => {
  const helper = await import('../validation-recipe-analysis.mjs').catch(() => ({}));
  assert.equal(typeof helper.analyzeRecipeInputs, 'function');
  assert.equal(typeof helper.analyzeConfig, 'function');
  assert.equal(typeof helper.selectCompatibleProfile, 'function');
});

test('rejects unsupported configuration constructs with locations without executing them', async () => {
  const { analyzeConfig } = await import('../validation-recipe-analysis.mjs');
  const prefix = "import { defineConfig } from 'vitest/config';\n";
  const samples = [
    'export default defineConfig({ ...other });',
    'export default defineConfig({ test: { get globals() { return true } } });',
    'export default defineConfig(() => ({}));',
    'export default defineConfig({ plugins: [] });',
    'export default defineConfig({ test: { runner: "custom" } });',
    'export default defineConfig({ test: { browser: { enabled: true } } });',
    'export default defineConfig({ test: { globals: dynamic } });',
    'import { evil } from "x"; export default defineConfig({});',
    'export default defineConfig({ test: { pool: "forks", pool: "threads" } });',
    'export default defineConfig({ test: { globals: Boolean(1) } });',
  ];
  const directory = mkdtempSync(path.join(os.tmpdir(), 'recipe-ast-'));
  const sentinel = path.join(directory, 'sentinel');
  samples.push(`export default defineConfig({ test: { globals: require('node:fs').writeFileSync(${JSON.stringify(sentinel)}, 'bad') } });`);
  try {
    for (const content of samples) {
      const result = analyzeConfig({ path: 'vitest.config.ts', content: prefix + content });
      assert.equal(result.config, null, content);
      assert.ok(result.issues.length, content);
      assert.ok(result.issues.every(item => item.evidence.path === 'vitest.config.ts' && item.evidence.line >= 1 && item.evidence.column >= 1), content);
    }
    assert.equal(existsSync(sentinel), false);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('preserves explicit settings and rejects escaping aliases', async () => {
  const { analyzeConfig } = await import('../validation-recipe-analysis.mjs');
  const prefix = "import { defineConfig } from 'vite'; import { resolve } from 'node:path'; ";
  const settings = analyzeConfig({ path: 'app/vite.config.ts', content: prefix + "export default defineConfig({test:{pool:'threads',isolate:false,include:['src/a.test.ts'],exclude:['src/b.test.ts']}})" }, { packagePath: 'app' });
  assert.deepEqual({ ...settings.config.test }, { pool: 'threads', isolate: false, include: ['src/a.test.ts'], exclude: ['src/b.test.ts'] });
  assert.equal(analyzeConfig({ path: 'app/vite.config.ts', content: prefix + "export default defineConfig({resolve:{alias:{'@':resolve(import.meta.dirname,'../secret')}}})" }, { packagePath: 'app' }).config, null);
});

test('semver handles comparison operators, OR, caret, tilde, invalid versions and prereleases', async () => {
  const { selectCompatibleProfile } = await import('../validation-recipe-analysis.mjs');
  const profile = { node: '22.23.4', npm: '10.9.9', vitest: '4.1.11', vite: '8.3.0' };
  const input = { vitest: '4.1.11', vite: '8.3.0', npm: '10.9.9', nodePins: ['v22.23.4\n'], engines: [] };
  for (const range of ['>=22.23.4', '<=22.23.4', '>22.23.3', '<22.23.5', '^22.0.0', '~22.23.0', '22.23.0 - 22.23.9', '<20 || >=22.23.4']) {
    assert.deepEqual(selectCompatibleProfile({ ...input, engines: [range] }, [profile]).profile, profile, range);
  }
  for (const range of ['>22.23.4', '<22.23.4', '~22.22.0', 'not-a-range']) assert.equal(selectCompatibleProfile({ ...input, engines: [range] }, [profile]).profile, null, range);
  assert.equal(selectCompatibleProfile({ ...input, vitest: 'garbage' }, [profile]).profile, null);
  assert.equal(selectCompatibleProfile({ ...input, npm: '10.9.8' }, [profile]).profile, null);
});

test('JSON protocol selects explicit config and exposes profile and issues', () => {
  const script = fileURLToPath(new URL('../validation-recipe-analysis.mjs', import.meta.url));
  const input = { files: [{ path: 'app/custom.ts', content: "import { defineConfig } from 'vite'; export default defineConfig({test:{environment:'jsdom'}})" }], rule: 'single_npm_local_vitest_v1', inventory: { packagePath: 'app', configPath: 'app/custom.ts' }, profiles: [{ node: '22.23.4', npm: '10.9.9', vitest: '4.1.10', vite: '7.3.6' }], compatibility: { vitest: '4.1.10', vite: '7.3.6', nodePins: [], engines: ['>=22'] } };
  const proc = spawnSync(process.execPath, [script], { input: JSON.stringify(input), encoding: 'utf8' });
  assert.equal(proc.status, 0);
  assert.equal(proc.stderr, '');
  const result = JSON.parse(proc.stdout);
  assert.equal(result.facts.package_path, 'app');
  assert.equal(result.facts.config.path, 'app/custom.ts');
  assert.equal(result.facts.config.test.environment, 'jsdom');
  assert.deepEqual(result.facts.environment_profile, input.profiles[0]);
  assert.deepEqual(result.issues, []);
});

test('ambiguous default configurations and invalid explicit paths are pending', async () => {
  const { analyzeRecipeInputs } = await import('../validation-recipe-analysis.mjs');
  const request = { rule: 'single_npm_local_vitest_v1', files: ['vitest.config.ts', 'vitest.config.js'].map(path => ({ path, content: '' })) };
  assert.equal(analyzeRecipeInputs(request).issues[0].code, 'UNIT_SCOPE_UNRESOLVED');
  assert.equal(analyzeRecipeInputs({ ...request, inventory: { configPath: '../other' } }).issues[0].code, 'UNIT_SCOPE_UNRESOLVED');
});

test('default Vitest config takes precedence without executing or parsing Vite config', async () => {
  const { analyzeRecipeInputs } = await import('../validation-recipe-analysis.mjs');
  const directory = mkdtempSync(path.join(os.tmpdir(), 'recipe-precedence-'));
  const sentinel = path.join(directory, 'sentinel');
  const files = [
    { path: 'vitest.config.ts', content: "import { defineConfig } from 'vitest/config'; export default defineConfig({});" },
    { path: 'vite.config.ts', content: `require('node:fs').writeFileSync(${JSON.stringify(sentinel)}, 'bad'); !!!` },
  ];
  try {
    const request = { rule: 'single_npm_local_vitest_v1', files };
    const result = analyzeRecipeInputs(request);
    assert.deepEqual(result.issues, []);
    assert.equal(result.facts.config.path, 'vitest.config.ts');
    assert.deepEqual(result.facts.configs, ['vite.config.ts', 'vitest.config.ts']);
    assert.ok(analyzeRecipeInputs({ ...request, inventory: { configPath: 'vite.config.ts' } }).issues.length);
    assert.equal(existsSync(sentinel), false);
    const imported = files.map(file => file.path === 'vitest.config.ts' ? { ...file, content: "import base from './vite.config'; export default base;" } : file);
    assert.ok(analyzeRecipeInputs({ ...request, files: imported }).issues.length);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('path and node:path support only the modeled resolve import', async () => {
  const { analyzeConfig } = await import('../validation-recipe-analysis.mjs');
  for (const module of ['path', 'node:path']) {
    const content = `import { defineConfig } from 'vite'; import { resolve } from '${module}'; export default defineConfig({resolve:{alias:{'@':resolve(import.meta.dirname,'src')}}});`;
    assert.equal(analyzeConfig({ path: 'vitest.config.ts', content }).config?.resolve.alias['@'], 'src');
  }
  for (const statement of ["import { resolve } from 'fake-path'", "const { resolve } = require('path')", "import { join as resolve } from 'path'"]) {
    const content = `import { defineConfig } from 'vite'; ${statement}; export default defineConfig({resolve:{alias:{'@':resolve(import.meta.dirname,'src')}}});`;
    assert.equal(analyzeConfig({ path: 'vitest.config.ts', content }).config, null);
  }
});

test('npm engine constraints and package-manager pins participate in profile selection', async () => {
  const { selectCompatibleProfile } = await import('../validation-recipe-analysis.mjs');
  const profile = { node: '22.23.4', npm: '10.9.9', vitest: '4.1.10', vite: '7.3.6' };
  const input = { vitest: '4.1.10', vite: '7.3.6', npm: '10.9.9', nodePins: [], engines: [], npmEngines: ['>=10 <11'] };
  assert.deepEqual(selectCompatibleProfile(input, [profile]).profile, profile);
  assert.equal(selectCompatibleProfile({ ...input, npmEngines: ['>=11'] }, [profile]).profile, null);
  assert.equal(selectCompatibleProfile({ ...input, npmEngines: ['invalid'] }, [profile]).profile, null);
});

test('the policy selects the proven Vite 8.0.16 trio', async () => {
  const { readFileSync } = await import('node:fs');
  const { selectCompatibleProfile } = await import('../validation-recipe-analysis.mjs');
  const policy = JSON.parse(readFileSync(new URL('../../validation_recipe_policy.json', import.meta.url), 'utf8'));
  assert.equal(selectCompatibleProfile({ vitest: '4.1.11', vite: '8.0.16', nodePins: [], engines: [] }, policy.profiles).profile?.vite, '8.0.16');
});

test('selects the first profile satisfying every real engine, pin and locked version', async () => {
  const { selectCompatibleProfile } = await import('../validation-recipe-analysis.mjs');
  const profiles = [
    { node: '22.12.0', npm: '10.9.9', vitest: '4.1.11', vite: '8.0.16' },
    { node: '22.23.4', npm: '10.9.9', vitest: '4.1.11', vite: '8.0.16' },
  ];
  const inputs = { vitest: '4.1.11', vite: '8.0.16', npm: '10.9.9', nodePins: [], engines: [
    '^20.19.0 || >=22.12.0', '^20.19.0 || ^22.13.0 || >=24', '^20 || ^22 || >=24',
  ] };
  assert.deepEqual(selectCompatibleProfile(inputs, profiles).profile, profiles[1]);
  assert.equal(selectCompatibleProfile({ ...inputs, engines: ['invalid range'] }, profiles).profile, null);
  assert.equal(selectCompatibleProfile({ ...inputs, nodePins: ['v22.12.0'] }, profiles).profile, null);
  assert.equal(selectCompatibleProfile({ ...inputs, engines: ['>=22.0.0'] }, [{ ...profiles[1], node: '22.23.4-beta.1' }]).profile, null);
});

test('reads supported configuration as data without introducing runtime defaults', async () => {
  const { analyzeConfig } = await import('../validation-recipe-analysis.mjs');
  const file = { path: 'app/vitest.config.ts', content: `import { defineConfig } from 'vitest/config';
import { resolve } from 'node:path';
export default defineConfig({ test: { environment: 'jsdom', setupFiles: ['src/test/setup.ts'], globals: true }, resolve: { alias: { '@': resolve(import.meta.dirname, 'src') } } });` };
  const result = analyzeConfig(file, { packagePath: 'app' });
  assert.deepEqual(result.issues, []);
  assert.equal(result.config.test.environment, 'jsdom');
  assert.equal(result.config.resolve.alias['@'], 'app/src');
  assert.equal('pool' in result.config.test, false);
});

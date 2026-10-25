// Supplement loaded by the project runtime; no source/config rewrite.
const fs = require('node:fs');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');
const path = require('node:path').posix;
const req = createRequire('/workspace/package.json');
function runnerEvidence(project) {
  const configured_path = project.testRunner;
  try {
    const projectRequire = createRequire(path.join(project.rootDir, 'package.json'));
    const manifest = projectRequire('jest-circus/package.json');
    return {
      namespace: 'jest-circus/runner', package_name: manifest.name, package_version: manifest.version,
      package_json_path: fs.realpathSync(projectRequire.resolve('jest-circus/package.json')),
      resolved_path: fs.realpathSync(projectRequire.resolve('jest-circus/runner')),
      configured_path, configured_realpath: fs.realpathSync(configured_path),
    };
  } catch {
    return {configured_path, resolution_error: 'circus_resolution_unavailable'};
  }
}
function isTestedCircus(runner, version) {
  return ['29.7.0', '30.0.0'].includes(version) && runner.namespace === 'jest-circus/runner' &&
    runner.package_name === 'jest-circus' && runner.package_version === version &&
    runner.package_json_path.endsWith('/node_modules/jest-circus/package.json') &&
    runner.resolved_path === path.join(path.dirname(runner.package_json_path),
      version === '29.7.0' ? 'runner.js' : 'build/runner.js') &&
    runner.configured_path === runner.resolved_path && runner.configured_realpath === runner.resolved_path;
}
module.exports = class Reporter {
  constructor(globalConfig) { this.globalConfig = globalConfig; }
  onRunComplete(_contexts, results) {
    const version = req('jest/package.json').version;
    const projects = [..._contexts].map(context => ({...context.config, runner: runnerEvidence(context.config)}));
    const tsTransform = projects[0]?.transform?.find(entry => entry[0] === '^.+\\.tsx?$');
    const mode = projects.length === 1 && isTestedCircus(projects[0].runner, version) &&
      projects[0].extensionsToTreatAsEsm.length === 0 &&
      tsTransform?.[1].includes('/ts-jest/') && tsTransform?.[2]?.tsconfig?.module === 'CommonJS'
      ? 'cjs-ts-jest' : 'unimplemented';
    fs.mkdirSync('/workspace/.robo-reports', {recursive: true});
    fs.writeFileSync('/workspace/.robo-reports/raw.json', JSON.stringify({
      framework: 'jest', version,
      compiler_version: req('typescript/package.json').version,
      transform: {name: 'ts-jest', version: req('ts-jest/package.json').version},
      source_hashes: Object.fromEntries(results.testResults.map(file => [
        file.testFilePath.slice('/workspace/'.length),
        createHash('sha256').update(fs.readFileSync(file.testFilePath)).digest('hex'),
      ])),
      configuration: {mode, argv: process.argv.slice(2), global: this.globalConfig,
        projects: projects.map(p => ({rootDir: p.rootDir, transform: p.transform,
          testMatch: p.testMatch, extensionsToTreatAsEsm: p.extensionsToTreatAsEsm,
          testRunner: p.testRunner, runner: p.runner}))},
      results,
    }));
  }
};

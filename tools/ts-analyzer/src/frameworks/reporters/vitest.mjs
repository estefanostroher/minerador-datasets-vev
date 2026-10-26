import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
const req = createRequire('/workspace/package.json');
export default class Reporter {
  onInit(context) { this.context = context; }
  onTestRunEnd(modules, errors, reason) {
    const files = modules.map(module => module.task);
    const config = this.context.config;
    const mode = req('./package.json').type === 'module' && config.includeTaskLocation &&
      this.context.projects.length === 1 && !config.browser?.enabled ? 'esm-vite' : 'unimplemented';
    const seen = new WeakSet();
    const raw = JSON.stringify({
      framework: 'vitest', version: req('vitest/package.json').version,
      compiler_version: req('typescript/package.json').version,
      transform: {name: 'vite', version: req('vite/package.json').version},
      source_hashes: Object.fromEntries(files.map(file => [
        file.filepath.slice('/workspace/'.length),
        createHash('sha256').update(fs.readFileSync(file.filepath)).digest('hex'),
      ])),
       configuration: {mode, argv: process.argv.slice(2), include: config.include,
         coverage: config.coverage,
         exclude: config.exclude, testNamePattern: String(config.testNamePattern ?? ''),
         shard: config.shard ?? null, changed: config.changed ?? null,
         related: config.related ?? null, project: config.project ?? null,
         filters: config.filters ?? null, cliExclude: config.cliExclude ?? null,
         standalone: config.standalone ?? null, mergeReports: config.mergeReports ?? null,
        projectCount: this.context.projects.length, pool: config.pool,
        fileParallelism: config.fileParallelism ?? null,
        maxWorkers: config.maxWorkers ?? null,
        includeTaskLocation: config.includeTaskLocation, sequence: config.sequence},
      files, errors, reason,
    }, (key, value) => {
      if (key === 'file' || key === 'suite') return undefined;
      if (value && typeof value === 'object') {
        if (seen.has(value)) return undefined;
        seen.add(value);
      }
      return value;
    });
    fs.mkdirSync('/workspace/.robo-reports', {recursive: true});
    fs.writeFileSync('/workspace/.robo-reports/raw.json', raw);
  }
}

// Observational supplement: runs only in a fresh derived Docker workspace.
// Never installs a tool, rewrites a test body, or replaces the original config.
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {spawnSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const req = createRequire('/workspace/package.json');
const request = require('/opt/robo-reporters/coverage-request.cjs');
const output = '/workspace/.robo-reports/coverage-envelope.json';
fs.mkdirSync(path.dirname(output), {recursive: true});
function unavailable(reason) {
  fs.writeFileSync(output, JSON.stringify({availability: 'unavailable', reason}));
}
let tool;
try {
  if (req('typescript/package.json').version !== request.instrumentation.compiler_version) throw Error('compiler');
  if (req(request.framework + '/package.json').version !== request.version) throw Error('framework');
  if (request.framework === 'vitest') {
    const provider = req('@vitest/coverage-istanbul/package.json');
    if (provider.version !== request.instrumentation.tool.version) throw Error('provider patch mismatch');
    tool = {name: '@vitest/coverage-istanbul', version: provider.version};
  } else {
    const version = req('babel-plugin-istanbul/package.json').version;
    if (version !== request.instrumentation.tool.version) throw Error('untested instrumenter');
    tool = {name: 'babel-plugin-istanbul', version};
  }
} catch {
  unavailable('coverage_tool_unavailable');
  process.exit(0);
}
const coverage = {
  enabled: true, provider: 'istanbul', reporter: ['json'],
  reportsDirectory: '/workspace/.robo-reports/coverage',
};
const configPath = '/workspace/' + request.config_path;
const reporter = '/opt/robo-reporters/' + request.framework + (request.framework === 'jest' ? '.cjs' : '.mjs');
let config;
if (request.framework === 'jest') {
  const original = req(configPath);
  if (typeof original !== 'object' || original.projects ||
      (original.coverageProvider && original.coverageProvider !== 'babel')) {
    unavailable('coverage_configuration_unsupported');
    process.exit(0);
  }
  config = '/workspace/.robo-coverage.config.cjs';
  fs.writeFileSync(config, `const original=require(${JSON.stringify(configPath)});
module.exports={...original,rootDir:original.rootDir??'/workspace',collectCoverage:true,
coverageProvider:'babel',coverageReporters:['json'],coverageDirectory:${JSON.stringify(coverage.reportsDirectory)},
testLocationInResults:true,testNamePattern:${JSON.stringify(request.pattern)},reporters:[${JSON.stringify(reporter)}]};\n`);
} else {
  config = '/workspace/.robo-coverage.config.mjs';
  fs.writeFileSync(config, `import original from ${JSON.stringify(configPath)};
import fs from 'node:fs';
export default async env=>{
 const c=typeof original==='function'?await original(env):await original;
 if(c.test?.coverage?.provider && c.test.coverage.provider!=='istanbul') {
   fs.writeFileSync(${JSON.stringify(output)},JSON.stringify({availability:'unavailable',reason:'coverage_configuration_unsupported'}));
   throw Error('coverage_configuration_unsupported');
 }
 return {...c,root:c.root??'/workspace',test:{...c.test,
 coverage:{...c.test?.coverage,...${JSON.stringify(coverage)}},
 testNamePattern:${JSON.stringify(request.pattern)},reporters:[${JSON.stringify(reporter)}]}};
};\n`);
}
const args = [...request.argv.slice(1), '--config', config];
const run = spawnSync(request.argv[0], args, {cwd: '/workspace', stdio: 'inherit'});
if (fs.existsSync(output)) process.exit(run.status ?? 1);
if (run.error || (run.status !== 0 && !fs.existsSync(coverage.reportsDirectory + '/coverage-final.json'))) {
  unavailable('coverage_execution_failed');
  process.exit(run.status ?? 1);
}
const ts = req('typescript');
function originalFunctions(source, filename) {
  const ast = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true);
  const location = node => {
    const start = ast.getLineAndCharacterOfPosition(node.getStart(ast));
    const end = ast.getLineAndCharacterOfPosition(node.end);
    return {start: {line: start.line + 1, column: start.character},
      end: {line: end.line + 1, column: end.character}};
  };
  const functions = [];
  function visit(node) {
    if (ts.isFunctionLike(node) && node.body) {
      const nested = [];
      function children(child) {
        if (ts.isFunctionLike(child) && child.body) nested.push(location(child.body));
        ts.forEachChild(child, children);
      }
      ts.forEachChild(node.body, children);
      functions.push({start: node.getStart(ast), end: node.end,
        declaration: node.name ? location(node.name) : null, body: location(node.body), nested});
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return functions;
}
try {
  const map = JSON.parse(fs.readFileSync(coverage.reportsDirectory + '/coverage-final.json', 'utf8'));
  const files = {};
  for (const [filename, counters] of Object.entries(map)) {
    if (!filename.startsWith('/workspace/')) continue;
    const source = fs.readFileSync(filename);
    files[filename.slice('/workspace/'.length)] = {
      source_sha256: createHash('sha256').update(source).digest('hex'),
      source_maps: request.instrumentation.source_maps,
      functions: originalFunctions(source.toString('utf8'), filename), map: counters,
    };
  }
  fs.writeFileSync(output, JSON.stringify({availability: 'available', tool,
    compiler: {name: 'typescript', version: ts.version}, files}));
} catch {
  unavailable('coverage_unattributable');
}
process.exit(run.status ?? 1);

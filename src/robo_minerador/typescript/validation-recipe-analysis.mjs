import ts from 'typescript';
import semver from 'semver';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// The caller supplies already verified paths and dependency facts. No candidate code is loaded.
const RULE = 'single_npm_local_vitest_v1';
const configNames = new Set(['vitest.config.ts', 'vitest.config.mts', 'vitest.config.js', 'vitest.config.mjs', 'vitest.config.cts', 'vitest.config.cjs', 'vite.config.ts', 'vite.config.mts', 'vite.config.js', 'vite.config.mjs', 'vite.config.cts', 'vite.config.cjs']);
const canonical = value => typeof value === 'string' && value !== '' && !value.includes('\\') && !value.startsWith('/') && !/^[A-Za-z]:/.test(value) && (value === '.' || value.split('/').every(part => part !== '' && part !== '.' && part !== '..'));
const contained = (root, value) => root === '.' || value === root || value.startsWith(`${root}/`);
const exact = value => typeof value === 'string' && semver.valid(value) === value && semver.prerelease(value) === null;
const issue = (code, message, evidence = {}) => ({ code, message, evidence });

export function selectCompatibleProfile(inputs, profiles) {
  const issues = [];
  if (!inputs || !Array.isArray(profiles) || !Array.isArray(inputs.engines) || !Array.isArray(inputs.nodePins) || (inputs.npmEngines !== undefined && !Array.isArray(inputs.npmEngines)) || !exact(inputs.vitest) || !exact(inputs.vite) || (inputs.npm != null && !exact(inputs.npm))) {
    return { profile: null, issues: [issue('AUTO_RECIPE_ENVIRONMENT_UNSUPPORTED', 'Invalid compatibility inputs')] };
  }
  const pins = inputs.nodePins.map(pin => typeof pin === 'string' && /^v?\d+\.\d+\.\d+(?:\r?\n)?$/.test(pin) ? pin.replace(/\r?\n$/, '').replace(/^v/, '') : null);
  const npmEngines = inputs.npmEngines ?? [];
  if (pins.some(pin => !exact(pin)) || [...inputs.engines, ...npmEngines].some(range => typeof range !== 'string' || semver.validRange(range) === null)) {
    return { profile: null, issues: [issue('AUTO_RECIPE_ENVIRONMENT_UNSUPPORTED', 'Invalid Node pin or engine range')] };
  }
  const profile = profiles.find(p => p && exact(p.node) && exact(p.npm) && p.vitest === inputs.vitest && p.vite === inputs.vite && (!inputs.npm || p.npm === inputs.npm) && pins.every(pin => pin === p.node) && inputs.engines.every(range => semver.satisfies(p.node, range, { includePrerelease: false })) && npmEngines.every(range => semver.satisfies(p.npm, range, { includePrerelease: false }))) ?? null;
  if (!profile) issues.push(issue('AUTO_RECIPE_ENVIRONMENT_UNSUPPORTED', 'No compatible environment profile'));
  return { profile, issues };
}

export function analyzeConfig(file, inventory = {}) {
  const issues = [];
  const packagePath = inventory.packagePath ?? '.';
  if (!file || !canonical(file.path) || typeof file.content !== 'string' || !canonical(packagePath) || !contained(packagePath, file.path)) {
    return { config: null, issues: [issue('UNIT_SCOPE_UNRESOLVED', 'Invalid configuration path')] };
  }
  const source = ts.createSourceFile(file.path, file.content, ts.ScriptTarget.Latest, true, file.path.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const report = (node, message) => {
    const pos = source.getLineAndCharacterOfPosition(node.getStart(source));
    issues.push(issue('UNIT_SCOPE_UNRESOLVED', message, { path: file.path, line: pos.line + 1, column: pos.character + 1 }));
  };
  for (const diagnostic of source.parseDiagnostics) {
    const pos = source.getLineAndCharacterOfPosition(diagnostic.start ?? 0);
    issues.push(issue('UNIT_SCOPE_UNRESOLVED', 'Invalid configuration syntax', { path: file.path, line: pos.line + 1, column: pos.character + 1 }));
  }
  let defineName = null;
  let resolveName = null;
  const importNames = new Set();
  let exported = null;
  for (const statement of source.statements) {
    if (ts.isImportDeclaration(statement)) {
      const module = ts.isStringLiteral(statement.moduleSpecifier) ? statement.moduleSpecifier.text : '';
      const clause = statement.importClause;
      const bindings = clause?.namedBindings;
      if (!clause || clause.isTypeOnly || clause.name || !bindings || !ts.isNamedImports(bindings) || !['vitest/config', 'vite', 'node:path', 'path'].includes(module)) { report(statement, 'Unsupported import'); continue; }
      for (const binding of bindings.elements) {
        if (importNames.has(binding.name.text)) { report(binding, 'Duplicate local import binding'); continue; }
        importNames.add(binding.name.text);
        const original = (binding.propertyName ?? binding.name).text;
        if (!binding.isTypeOnly && original === 'defineConfig' && ['vite', 'vitest/config'].includes(module) && !defineName) defineName = binding.name.text;
        else if (!binding.isTypeOnly && original === 'resolve' && ['node:path', 'path'].includes(module) && !resolveName) resolveName = binding.name.text;
        else report(binding, 'Unsupported or duplicate import');
      }
    } else if (ts.isExportAssignment(statement) && !statement.isExportEquals && !exported) exported = statement.expression;
    else report(statement, 'Unsupported statement or ambiguous export');
  }
  const propertyName = prop => prop.name && (ts.isIdentifier(prop.name) || ts.isStringLiteral(prop.name)) ? prop.name.text : null;
  function object(node, allowed, parseValue) {
    if (!ts.isObjectLiteralExpression(node)) { report(node, 'Expected object literal'); return null; }
    const result = Object.create(null);
    for (const prop of node.properties) {
      const name = propertyName(prop);
      if (!ts.isPropertyAssignment(prop) || name === null || !allowed.has(name) || Object.hasOwn(result, name)) { report(prop, 'Unsupported or duplicate property'); continue; }
      result[name] = parseValue(name, prop.initializer);
    }
    return result;
  }
  function literal(node, type) {
    if (type === 'string' && ts.isStringLiteral(node)) return node.text;
    if (type === 'boolean' && (node.kind === ts.SyntaxKind.TrueKeyword || node.kind === ts.SyntaxKind.FalseKeyword)) return node.kind === ts.SyntaxKind.TrueKeyword;
    if (type === 'strings' && ts.isArrayLiteralExpression(node) && node.elements.every(ts.isStringLiteral)) return node.elements.map(item => item.text);
    report(node, `Expected literal ${type}`); return null;
  }
  function alias(node) {
    if (!ts.isObjectLiteralExpression(node)) { report(node, 'Expected alias object'); return null; }
    const names = new Set(node.properties.filter(ts.isPropertyAssignment).map(propertyName).filter(name => name !== null));
    return object(node, names, (_name, value) => {
      if (ts.isCallExpression(value) && !value.questionDotToken && !value.typeArguments && ts.isIdentifier(value.expression) && value.expression.text === resolveName && value.arguments.length === 2 && value.arguments[0].getText(source) === 'import.meta.dirname' && ts.isStringLiteral(value.arguments[1])) {
        const target = value.arguments[1].text;
        if (canonical(target) && target !== '.') {
          const result = path.posix.join(path.posix.dirname(file.path), target);
          if (canonical(result) && contained(packagePath, result)) return result;
        }
      }
      report(value, 'Unsupported alias target'); return null;
    });
  }
  let config = null;
  if (!exported) report(source, 'Missing default export');
  else if (!ts.isCallExpression(exported) || exported.questionDotToken || !ts.isIdentifier(exported.expression) || !defineName || exported.expression.text !== defineName || exported.arguments.length !== 1 || exported.typeArguments?.length) report(exported, 'Expected defineConfig with one object literal');
  else config = object(exported.arguments[0], new Set(['test', 'resolve']), (name, value) => {
    if (name === 'resolve') return object(value, new Set(['alias']), (_key, entry) => alias(entry));
    return object(value, new Set(['environment', 'setupFiles', 'globals', 'include', 'exclude', 'pool', 'isolate']), (key, entry) => {
      if (key === 'globals' || key === 'isolate') return literal(entry, 'boolean');
      if (key === 'include' || key === 'exclude') return literal(entry, 'strings');
      if (key === 'setupFiles' && ts.isArrayLiteralExpression(entry)) return literal(entry, 'strings');
      return literal(entry, 'string');
    });
  });
  return { config: issues.length ? null : config, issues };
}

// JSON stdin: {files:[{path,content}], profiles:[profile], rule: RULE,
//   inventory?:{packagePath?:string,configPath?:string},
//   compatibility?:{vitest:string,vite:string,npm?:string,nodePins:string[],
//                   engines:string[],npmEngines?:string[]}}.
// Paths are canonical POSIX paths relative to the repository; root is ".".
// The wrapper must supply the full relevant config inventory and collect every
// Node/npm engine range, exact effective lock versions and pins before calling.
// npm is the exact packageManager version when declared, not a range.
// Omitting compatibility skips profile selection; it does not imply success.
// stdout: {facts:{package_path,config,configs,environment_profile},issues}.
// config is null or {path,test?,resolve?}; absence of a setting stays absent.
// issues contain {code,message,evidence}; AST evidence includes path/line/column.
// The wrapper owns JSON strictness, file bounds, snapshot and lock verification.
export function analyzeRecipeInputs({ files = [], profiles = [], rule, inventory = {}, compatibility = null }) {
  const issues = [];
  const packagePath = inventory.packagePath ?? '.';
  const facts = { package_path: packagePath, config: null, configs: [], environment_profile: null };
  if (rule !== RULE || !canonical(packagePath) || !Array.isArray(files) || files.some(file => !file || !canonical(file.path) || typeof file.content !== 'string') || new Set(files.map(file => file.path)).size !== files.length) {
    return { facts, issues: [issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Invalid helper input')] };
  }
  const configs = files.filter(file => path.posix.dirname(file.path) === packagePath && configNames.has(path.posix.basename(file.path)));
  facts.configs = configs.map(file => file.path).sort();
  let selected;
  if (inventory.configPath != null) {
    if (!canonical(inventory.configPath) || !contained(packagePath, inventory.configPath)) issues.push(issue('UNIT_SCOPE_UNRESOLVED', 'Invalid explicit configuration path'));
    else selected = files.find(file => file.path === inventory.configPath);
    if (!selected && !issues.length) issues.push(issue('UNIT_SCOPE_UNRESOLVED', 'Explicit configuration not provided', { path: inventory.configPath }));
  } else {
    const vitestConfigs = configs.filter(file => path.posix.basename(file.path).startsWith('vitest.config.'));
    const effective = vitestConfigs.length ? vitestConfigs : configs;
    if (effective.length === 1) selected = effective[0];
    else if (effective.length > 1) issues.push(issue('UNIT_SCOPE_UNRESOLVED', 'Multiple default configurations', { paths: effective.map(file => file.path).sort() }));
  }
  if (selected) {
    const result = analyzeConfig(selected, { packagePath });
    facts.config = result.config === null ? null : { path: selected.path, ...result.config };
    issues.push(...result.issues);
  }
  if (compatibility !== null) {
    const result = selectCompatibleProfile(compatibility, profiles);
    facts.environment_profile = result.profile;
    issues.push(...result.issues);
  }
  return { facts, issues };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) input += chunk;
  try { process.stdout.write(JSON.stringify(analyzeRecipeInputs(JSON.parse(input)))); }
  catch { process.stdout.write(JSON.stringify({ facts: {}, issues: [issue('AUTO_RECIPE_INPUT_UNSUPPORTED', 'Invalid helper JSON or input')] })); }
}

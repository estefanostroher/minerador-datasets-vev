import ts from 'typescript';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { walk, safePath } from './virtual-project.mjs';

export const testConfiguration = /(^|\/)(?:(?:vitest\.(?:config|workspace)|vite\.config|jest\.(?:config|preset)|mocha\.config)\.(?:[cm]?[jt]sx?|jsonc?|ya?ml)|\.(?:mocha|jest)rc(?:\.(?:[cm]?[jt]s|jsonc?|ya?ml))?|mocha\.opts)$/;
const frameworkSpecifiers = new Map([['vitest', 'vitest'], ['@jest/globals', 'jest'], ['jest', 'jest'], ['mocha', 'mocha'], ['node:test', 'node:test']]);

/** Confere o inventário e bytes textuais; a autoridade de integridade continua no store Python. */
export function verifySnapshotInput(input) {
  if (!Object.hasOwn(input, 'snapshot')) return false;
  const reference = input.snapshot;
  if (!reference || reference.format !== 'git-bare-v1' || reference.status !== 'complete' || !/^[a-f0-9]{40}$/.test(reference.commit_sha) || !/^[a-f0-9]{40}$/.test(reference.tree_oid) || !/^[a-f0-9]{64}$/.test(reference.manifest_sha256) || !Array.isArray(input.inventory) || typeof input.analysis_complete !== 'boolean') throw new Error('Snapshot inválido.');
  const entries = new Map();
  let bytes = 0;
  for (const entry of input.inventory) {
    const key = entry.path ?? `base64:${entry.path_base64}`;
    if (entries.has(key) || typeof key !== 'string' || !/^[a-f0-9]{40}$/.test(entry.oid) || !['blob', 'commit'].includes(entry.type)) throw new Error('Inventário inválido.');
    entries.set(key, entry);
    if (entry.type === 'blob') {
      if (!Number.isSafeInteger(entry.size) || entry.size < 0) throw new Error('Tamanho inválido.');
      bytes += entry.size;
    }
  }
  if (entries.size !== reference.file_count || bytes !== reference.total_blob_bytes) throw new Error('Inventário incompleto.');
  for (const file of input.files) {
    const entry = entries.get(file.path);
    const raw = Buffer.from(file.content, 'utf8');
    const oid = createHash('sha1').update(`blob ${raw.length}\0`).update(raw).digest('hex');
    if (!entry || entry.type !== 'blob' || entry.mode === '120000' || entry.oid !== oid || entry.size !== raw.length || file.sha256 !== createHash('sha256').update(raw).digest('hex')) throw new Error('Texto não corresponde ao inventário.');
  }
  return true;
}

/** Extrai valores declarativos sem importar, chamar ou avaliar código candidato. */
function literalValue(node, checker, seen = new Set()) {
  if (!node || seen.has(node)) return undefined;
  seen.add(node);
  if (ts.isStringLiteralLike(node)) return node.text;
  if (ts.isNumericLiteral(node)) return Number(node.text);
  if (node.kind === ts.SyntaxKind.TrueKeyword) return true;
  if (node.kind === ts.SyntaxKind.FalseKeyword) return false;
  if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isSatisfiesExpression(node)) return literalValue(node.expression, checker, seen);
  if (ts.isIdentifier(node)) {
    let symbol = checker.getSymbolAtLocation(node);
    if (symbol?.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
    const declaration = symbol?.valueDeclaration;
    if (declaration && ts.isVariableDeclaration(declaration) && declaration.parent.flags & ts.NodeFlags.Const) return literalValue(declaration.initializer, checker, seen);
    if (declaration && ts.isExportAssignment(declaration)) return literalValue(declaration.expression, checker, seen);
  }
  if (ts.isArrayLiteralExpression(node)) {
    const result = [];
    for (const element of node.elements) {
      if (ts.isSpreadElement(element)) {
        const value = literalValue(element.expression, checker, new Set(seen));
        if (Array.isArray(value)) result.push(...value);
        else result.push(undefined);
      } else result.push(literalValue(element, checker, new Set(seen)));
    }
    return result;
  }
  if (ts.isObjectLiteralExpression(node)) {
    const result = Object.create(null);
    for (const property of node.properties) {
      if (ts.isPropertyAssignment(property)) {
        const name = ts.isComputedPropertyName(property.name) ? literalValue(property.name.expression, checker, new Set(seen)) : property.name.text;
        if (typeof name === 'string' || typeof name === 'number') result[name] = literalValue(property.initializer, checker, new Set(seen));
      }
      if (ts.isShorthandPropertyAssignment(property)) {
        const declaration = checker.getShorthandAssignmentValueSymbol(property)?.valueDeclaration;
        result[property.name.text] = declaration && ts.isVariableDeclaration(declaration) && declaration.parent.flags & ts.NodeFlags.Const ? literalValue(declaration.initializer, checker, new Set(seen)) : undefined;
      }
      if (ts.isSpreadAssignment(property)) {
        const value = literalValue(property.expression, checker, new Set(seen));
        if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
        Object.assign(result, value);
      }
    }
    return result;
  }
  // defineConfig é apenas envelope sintático conhecido, nunca uma chamada real.
  if (ts.isCallExpression(node) && node.arguments.length === 1 && ts.isIdentifier(node.expression)) {
    const symbol = checker.getSymbolAtLocation(node.expression);
    for (const declaration of symbol?.declarations ?? []) {
      if (!ts.isImportSpecifier(declaration) || (declaration.propertyName ?? declaration.name).text !== 'defineConfig') continue;
      const statement = declaration.parent.parent.parent;
      if (ts.isImportDeclaration(statement) && ['vitest', 'vitest/config', 'vite'].includes(statement.moduleSpecifier.text)) return literalValue(node.arguments[0], checker, seen);
    }
  }
  return undefined;
}

/** Interpreta exportações declarativas conhecidas sem importar o módulo candidato. */
function configurationValue(project, filePath, seen = new Set()) {
  if (seen.has(filePath)) return undefined;
  seen.add(filePath);
  const content = project.files.get(filePath);
  if (content === undefined) return undefined;
  if (/\.jsonc?$/.test(filePath) || /(?:^|\/)\.mocharc$/.test(filePath)) return ts.parseConfigFileTextToJson(filePath, content).config;
  const program = project.contextProgramFor(filePath);
  const source = program?.getSourceFile('/candidate/' + filePath);
  if (!source) return undefined;
  const checker = program.getTypeChecker();
  const module = checker.getSymbolAtLocation(source);
  let symbol = module && checker.getExportsOfModule(module).find(/** Seleciona a identidade efetivamente exportada como default. */ item => item.name === 'default');
  if (symbol?.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
  const declaration = symbol?.valueDeclaration;
  if (declaration && ts.isVariableDeclaration(declaration) && declaration.parent.flags & ts.NodeFlags.Const) return literalValue(declaration.initializer, checker);
  if (declaration && ts.isExportAssignment(declaration)) return literalValue(declaration.expression, checker);
  for (const statement of source.statements) {
    if (ts.isExportAssignment(statement)) return literalValue(statement.expression, checker);
    if (ts.isExpressionStatement(statement) && ts.isBinaryExpression(statement.expression) && statement.expression.operatorToken.kind === ts.SyntaxKind.EqualsToken && ['module.exports', 'exports.default'].includes(statement.expression.left.getText(source))) return literalValue(statement.expression.right, checker);
    if (ts.isExportDeclaration(statement) && statement.moduleSpecifier && statement.exportClause && ts.isNamedExports(statement.exportClause) && statement.exportClause.elements.some(/** Reconhece reexportação explícita da configuração padrão. */ element => element.name.text === 'default' && (element.propertyName ?? element.name).text === 'default')) {
      const dependency = project.dependencies.find(/** Recupera identidade da configuração reexportada. */ item => item.from === filePath && item.specifier === statement.moduleSpecifier.text && item.resolved_path);
      if (dependency) return configurationValue(project, dependency.resolved_path, seen);
    }
  }
  return undefined;
}

/** Indexa configurações, setups e requisitos observados sem transformá-los em erros globais. */
export function prepareSnapshotContext(project, input) {
  const requirements = [];
  const configurations = new Map();
  /** Registra cada requisito com origem e estado estritamente não avaliado. */
  function requirement(type, origin) { requirements.push({ type, path: origin, status: 'not_evaluated' }); }
  for (const entry of input.inventory) {
    if (entry.type === 'commit') requirement('gitlink', entry.path ?? entry.path_base64);
    if (entry.lfs_pointer) requirement('lfs_pointer', entry.path ?? entry.path_base64);
  }
  for (const dependency of project.dependencies) if (!dependency.resolved_path) requirement('external_dependency', dependency.from);
  for (const [filePath, content] of project.files) {
    const source = project.contextProgramFor(filePath)?.getSourceFile('/candidate/' + filePath);
    if (testConfiguration.test(filePath) || /(^|\/)package.json$/.test(filePath)) {
      const value = configurationValue(project, filePath);
      if (testConfiguration.test(filePath)) requirement('test_configuration', filePath);
      if (value) configurations.set(filePath, value);
    }
    if (/(^|\/)tsconfig[^/]*\.json$/.test(filePath)) {
      const config = ts.parseConfigFileTextToJson(filePath, content).config;
      if (config?.extends && !config.extends.startsWith?.('.')) requirement('external_dependency', filePath);
      if (config?.compilerOptions?.types?.length) requirement('external_dependency', filePath);
    }
    if (!source) continue;
    const preprocessed = ts.preProcessFile(content);
    if (preprocessed.typeReferenceDirectives.length || preprocessed.libReferenceDirectives.length) requirement('external_dependency', filePath);
    walk(source, /** Classifica ambiente e carregadores como requisitos da validação futura. */ node => {
      if (ts.isIdentifier(node) && ['process', 'global', 'globalThis', 'window'].includes(node.text)) requirement('environment', filePath);
      if (ts.isIdentifier(node) && ['require', 'createRequire', 'eval', 'module'].includes(node.text)) requirement('dynamic_loading', filePath);
      if (!ts.isCallExpression(node) && !ts.isNewExpression(node)) return;
      const name = node.expression.getText(source);
      if (node.expression.kind === ts.SyntaxKind.ImportKeyword) requirement('dynamic_loading', filePath);
      if (/(?:readFile|readFileSync|open|openSync|readdir|readdirSync|fetch)$/.test(name) || name === 'URL') requirement('external_resource', filePath);
    });
  }
  const setupPaths = new Set();
  const substitutions = new Map();
  const jestConfigurations = new Set();
  const presetEdges = new Map();
  /** Conserva a configuração de origem até a seleção do contexto de cada teste. */
  function markSubstitution(filePath, matches) {
    if (!substitutions.has(filePath)) substitutions.set(filePath, []);
    substitutions.get(filePath).push(matches);
  }
  for (const [filePath, value] of configurations) {
    if (/(^|\/)(?:jest\.config\.|\.jestrc(?:\.|$))/.test(filePath) || /(^|\/)package\.json$/.test(filePath) && value.jest) jestConfigurations.add(filePath);
    const configs = [value, value.test, value.jest, value.mocha, value.vitest].filter(Boolean);
    for (const config of configs) {
      const aliases = config.resolve?.alias ?? config.alias;
      const mappings = Array.isArray(aliases) ? aliases.map(/** Normaliza aliases declarativos com find literal. */ item => [item?.find, item?.replacement]) : Object.entries(aliases ?? {});
      for (const [find, replacement] of mappings) {
        if (typeof find !== 'string' || typeof replacement !== 'string') continue;
        markSubstitution(filePath, /** Respeita correspondência exata ou prefixo de segmento do alias. */ specifier => specifier === find || specifier.startsWith(find + '/'));
      }
      for (const expression of Object.keys(config.moduleNameMapper ?? {})) {
        let matcher;
        try { matcher = new RegExp(expression); } catch { continue; }
        markSubstitution(filePath, /** Aplica mapeamento declarativo ao specifier, não à presença da dependência. */ specifier => matcher.test(specifier));
      }
    }
    for (const config of configs) for (const key of ['setupFiles', 'setupFilesAfterEnv', 'globalSetup', 'preset', 'presets', 'require', 'import']) {
      const values = Array.isArray(config[key]) ? config[key] : [config[key]];
      for (const specifier of values) {
        if (typeof specifier !== 'string') continue;
        const base = path.posix.normalize(path.posix.join(path.posix.dirname(filePath), specifier.replace(/^<rootDir>\//, '')));
        const target = [base, ...['.ts', '.js', '.mts', '.mjs', '/index.ts', '/index.js'].map(/** Completa caminhos declarativos sem consultar disco. */ suffix => base + suffix)].find(/** Mantém aresta inclusive para fonte omitida pelo orçamento de análise. */ name => safePath(name) && project.inventoryPaths.has(name));
        if (target) {
          setupPaths.add(target);
          project.dependencies.push({ from: filePath, specifier, resolved_path: target });
          const nested = configurationValue(project, target);
          if (nested && !configurations.has(target)) configurations.set(target, nested);
          if (['preset', 'presets'].includes(key) && (config === value || config === value.jest)) {
            if (!presetEdges.has(filePath)) presetEdges.set(filePath, new Set());
            presetEdges.get(filePath).add(target);
          }
        } else requirement(specifier.startsWith('.') || specifier.startsWith('<rootDir>') ? 'external_resource' : 'external_dependency', filePath);
      }
    }
  }
  /** Segue presets Jest resolvidos respeitando booleano explícito e terminando em ciclos. */
  function automockEnabled(filePath, seen = new Set()) {
    if (seen.has(filePath)) return false;
    seen.add(filePath);
    const value = configurations.get(filePath);
    const config = /(^|\/)package\.json$/.test(filePath) ? value?.jest : value;
    if (typeof config?.automock === 'boolean') return config.automock;
    for (const target of presetEdges.get(filePath) ?? []) if (automockEnabled(target, new Set(seen))) return true;
    return false;
  }
  const edges = new Map();
  for (const dependency of project.dependencies) if (dependency.resolved_path) {
    if (!edges.has(dependency.from)) edges.set(dependency.from, new Set());
    edges.get(dependency.from).add(dependency.resolved_path);
  }
  /** Obtém dependências transitivas de fontes/configurações, sem executar seus conteúdos. */
  function closure(roots) {
    const reached = new Set(roots);
    for (const source of reached) for (const target of edges.get(source) ?? []) reached.add(target);
    return reached;
  }
  const contexts = new Map();
  /** Seleciona configurações ancestrais mais próximas e conserva presets/setups vinculados. */
  function contextFor(filePath) {
    if (contexts.has(filePath)) return contexts.get(filePath);
    const local = [...configurations.keys()].filter(/** Descarta configurações irmãs sem vínculo com o teste. */ name => {
      const directory = path.posix.dirname(name);
      return testConfiguration.test(name) && (directory === '.' || filePath.startsWith(directory + '/'));
    });
    const depth = Math.max(-1, ...local.map(/** Mede profundidade sem confundir raiz com diretório de primeiro nível. */ name => path.posix.dirname(name) === '.' ? 0 : path.posix.dirname(name).split('/').length));
    const roots = local.filter(/** Mantém somente o escopo efetivo mais próximo. */ name => (path.posix.dirname(name) === '.' ? 0 : path.posix.dirname(name).split('/').length) === depth);
    for (const name of configurations.keys()) if (/(^|\/)package\.json$/.test(name)) {
      const directory = path.posix.dirname(name);
      if (directory === '.' || filePath.startsWith(directory + '/')) roots.push(name);
    }
    const configPaths = closure(roots), sourcePaths = closure([filePath]);
    const substitutedPaths = new Set(), substitutedFrameworks = new Set(), automockFrameworks = new Set(), globalFrameworks = new Map();
    for (const name of configPaths) {
      const value = configurations.get(name);
      if (value?.test && typeof value.test.globals === 'boolean' && /vitest|vite/.test(name)) globalFrameworks.set(path.posix.dirname(filePath), value.test.globals ? 'vitest' : null);
      if (jestConfigurations.has(name) && automockEnabled(name)) automockFrameworks.add('jest');
      for (const matches of substitutions.get(name) ?? []) {
        for (const dependency of project.dependencies) if (sourcePaths.has(dependency.from) && dependency.resolved_path && matches(dependency.specifier)) substitutedPaths.add(dependency.resolved_path);
        for (const [specifier, framework] of frameworkSpecifiers) if (matches(specifier)) substitutedFrameworks.add(framework);
      }
    }
    const result = { substitutedPaths, substitutedFrameworks, automockFrameworks, globalFrameworks, sourcePaths: closure([filePath, ...configPaths]) };
    contexts.set(filePath, result);
    return result;
  }
  // Setups resolvidos também são analisados pelo coletor normal de mocks e APIs.
  project.setupPaths = setupPaths;
  project.contextFor = contextFor;
  project.executionRequirements = requirements;
}

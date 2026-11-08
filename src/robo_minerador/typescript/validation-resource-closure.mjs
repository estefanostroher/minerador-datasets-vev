import ts from 'typescript';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { analyzeResources } from './validation-resources.mjs';
import { resolveResource, resolveSnapshotData } from './validation-resource-resolution.mjs';

/** Fecha somente raízes relevantes; nomes do manifesto são a autoridade para resolver imports. */
export function analyzeResourceClosure(input) {
  if (ts.version !== input.models.typescript) throw Error('Compilador do fechamento divergente.');
  const texts = new Map(input.files.map(file => [file.path, file.content]));
  const inventory = new Set(input.inventory_paths), reached = new Set(input.roots), edges = [], limitations = [];
  const configurations = [], configFiles = new Set(input.configuration_paths), configCertificates = [];
  const compilerFiles = new Set();
  /** Registra dependências reais ou não resolvidas sem inventar uma fonte faltante. */
  function dependency(from, specifier, kind) {
    if (kind === 'import' && Object.hasOwn(input.models.apis, specifier)) {
      edges.push({ from, specifier, kind, model: specifier }); return;
    }
    try {
      const target = resolveResource(from, specifier, inventory, input.vitest_bound);
      edges.push({ from, specifier, kind, path: target }); reached.add(target);
      if (kind === 'compiler_configuration') compilerFiles.add(target);
    } catch (error) { limitations.push(from + ': ' + error.message); }
  }
  /** Acrescenta configurações de resolução ancestrais; não inclui configs Vitest de pacotes irmãos. */
  function ancillary(file) {
    let directory = path.posix.dirname(file);
    for (;;) {
      for (const name of ['package.json', 'tsconfig.json', 'jsconfig.json', '.npmrc']) {
        const candidate = directory === '.' ? name : directory + '/' + name;
        if (inventory.has(candidate) && candidate !== file) {
          reached.add(candidate); edges.push({ from: file, path: candidate, kind: 'resolution_configuration' });
        }
      }
      if (directory === '.') break;
      directory = path.posix.dirname(directory);
    }
  }
  /** Interpreta exclusivamente literais e o envelope nominal defineConfig conhecido. */
  function literal(node, definitions) {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
    if (node.kind === ts.SyntaxKind.TrueKeyword) return true;
    if (node.kind === ts.SyntaxKind.FalseKeyword) return false;
    if (ts.isNumericLiteral(node)) return Number(node.text);
    if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isSatisfiesExpression(node)) return literal(node.expression, definitions);
    if (ts.isArrayLiteralExpression(node)) return node.elements.map(element => literal(element, definitions));
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && definitions.has(node.expression.text) && node.arguments.length === 1) return literal(node.arguments[0], definitions);
    if (ts.isObjectLiteralExpression(node)) {
      const value = Object.create(null);
      for (const property of node.properties) {
        if (!ts.isPropertyAssignment(property) || ts.isComputedPropertyName(property.name) || Object.hasOwn(value, property.name.text)
            || property.name.text === '__proto__') throw Error('Propriedade de configuração não modelada.');
        value[property.name.text] = literal(property.initializer, definitions);
      }
      return value;
    }
    throw Error('Expressão de configuração não modelada.');
  }
  /** Deriva setups e isolamento do código original, independentemente das flags do relatório. */
  function configuration(file, ast) {
    const definitions = new Set();
    let value;
    for (const statement of ast.statements) {
      if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier)
          && ['vitest/config', 'vite'].includes(statement.moduleSpecifier.text) && !statement.attributes
          && !statement.importClause?.name && ts.isNamedImports(statement.importClause?.namedBindings)) {
        for (const binding of statement.importClause.namedBindings.elements) {
          if ((binding.propertyName ?? binding.name).text !== 'defineConfig' || binding.isTypeOnly) throw Error('Import de configuração não modelado.');
          definitions.add(binding.name.text);
        }
      } else if (ts.isExportAssignment(statement) && !statement.isExportEquals && value === undefined) value = literal(statement.expression, definitions);
      else if (!ts.isEmptyStatement(statement)) throw Error('Configuração com efeitos ou composição não modelados.');
    }
    if (!value || Object.keys(value).some(key => key !== 'test') || !value.test || typeof value.test !== 'object'
        || Object.keys(value.test).some(key => !['include', 'exclude', 'setupFiles', 'globalSetup', 'environment', 'isolate', 'pool'].includes(key))
        || value.test.environment !== undefined && value.test.environment !== 'node'
        || value.test.pool !== undefined && value.test.pool !== 'forks'
        || value.test.isolate !== undefined && value.test.isolate !== true) throw Error('Configuração fora do modelo local isolado.');
    const setups = {};
    for (const key of ['setupFiles', 'globalSetup']) {
      setups[key] = [];
      for (const setup of [value.test[key] ?? []].flat()) {
        if (typeof setup !== 'string') throw Error('Setup sem caminho literal.');
        const before = edges.length;
        dependency(file, setup.startsWith('.') ? setup : './' + setup, key);
        const resolved = edges.slice(before).find(edge => edge.path)?.path;
        if (resolved) setups[key].push(resolved);
      }
    }
    configurations.push({ path: file, ...setups, environment: value.test.environment ?? 'node',
      isolate: value.test.isolate ?? true, pool: value.test.pool ?? 'forks' });
  }
  for (const file of reached) {
    ancillary(file);
    const text = texts.get(file);
    if (text === undefined) { limitations.push('Fonte necessária ausente: ' + file); continue; }
    if (configFiles.has(file)) {
      try {
        const ast = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
        if (ast.parseDiagnostics.length) throw Error('Sintaxe de configuração inválida.');
        configuration(file, ast);
        configCertificates.push({ path: file, sha256: createHash('sha256').update(text).digest('hex'), imports: [], limitations: [],
          findings: text ? [{ start_line: 1, end_line: text.split(/\r\n|\r|\n/).length - (/[\r\n]$/.test(text) ? 1 : 0),
            resource: 'none', role: 'not_used', rule: 'declarative_local_configuration' }] : [] });
      } catch (error) { limitations.push(file + ': ' + error.message); }
      continue;
    }
    if (/\.(?:[cm]?[jt]s|[jt]sx)$/.test(file)) {
      const ast = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
      /** Censa imports/reexports estáticos; a análise de efeitos ainda deve compreender toda a AST. */
      function visit(node) {
        if (ts.isTypeAliasDeclaration(node) || ts.isInterfaceDeclaration(node)) return;
        if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && !node.isTypeOnly && !node.importClause?.isTypeOnly
            && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) dependency(file, node.moduleSpecifier.text, 'import');
        if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) limitations.push('Carregamento dinâmico no fechamento: ' + file);
        if (ts.isNewExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'URL'
            && node.arguments?.length === 2 && ts.isStringLiteral(node.arguments[0])
            && ts.isPropertyAccessExpression(node.arguments[1]) && ts.isMetaProperty(node.arguments[1].expression)
            && node.arguments[1].expression.keywordToken === ts.SyntaxKind.ImportKeyword && node.arguments[1].expression.name.text === 'meta'
            && node.arguments[1].name.text === 'url') {
          try {
            const target = resolveSnapshotData(file, node.arguments[0].text, inventory);
            edges.push({ from: file, specifier: node.arguments[0].text, kind: 'snapshot_data', path: target }); reached.add(target);
          } catch (error) { limitations.push(file + ': ' + error.message); }
        }
        ts.forEachChild(node, visit);
      }
      visit(ast);
    } else if (compilerFiles.has(file) || /(?:^|\/)(?:tsconfig|jsconfig)\.json$/.test(file)) {
      const parsed = ts.parseConfigFileTextToJson(file, text);
      if (parsed.error || !parsed.config || parsed.config.compilerOptions?.plugins
          || parsed.config.compilerOptions?.baseUrl || parsed.config.compilerOptions?.rootDirs
          || parsed.config.compilerOptions?.moduleSuffixes) limitations.push('Resolução TypeScript não modelada: ' + file);
      else if (parsed.config.extends) {
        for (const specifier of [parsed.config.extends].flat()) {
          if (typeof specifier !== 'string') limitations.push('extends TypeScript não literal: ' + file);
          else dependency(file, specifier, 'compiler_configuration');
        }
      }
    }
  }
  const selected = [...reached].filter(file => texts.has(file) && !configFiles.has(file));
  const effects = analyzeResources({ ...input, files: selected.map(file => ({ path: file, content: texts.get(file) })) });
  const files = [...effects.files, ...configCertificates].sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  return { closure_paths: [...reached].sort(), edges, configurations, files,
    complete: effects.complete && limitations.length === 0 && files.length === reached.size, limitations };
}

/** Lê somente JSON/textos originais; nenhum módulo candidato é importado pelo auxiliar. */
async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  console.log(JSON.stringify(analyzeResourceClosure(JSON.parse(Buffer.concat(chunks).toString('utf8')))));
}
if (process.argv[1] && new URL(import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\')
    === process.argv[1].replaceAll('/', '\\')) await main();

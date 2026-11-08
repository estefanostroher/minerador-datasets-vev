import ts from 'typescript';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { resolveResource, resolveSnapshotData } from './validation-resource-resolution.mjs';

/** Sinaliza sintaxe/efeito fora da linguagem fechada, nunca ausência de recurso. */
class Unsupported extends Error {}

/** Representa dados sem capacidades; valores opacos não ganham métodos arbitrários. */
function data(value = undefined) { return { kind: 'data', value }; }

/** Interpreta AST como efeitos abstratos, sem avaliar JavaScript candidato. */
export function analyzeResources(input) {
  if (ts.version !== input.models.typescript) throw Error('TypeScript do auditor divergente.');
  const sources = new Map(input.files.map(file => [file.path, file.content]));
  const records = new Map(), modules = new Map(), visiting = new Set();
  const loaded = new Set(input.loaded_paths);
  let steps = 0;
  /** Delimita formatos inertes; o fechamento dos imports e carregamentos ainda é obrigatório. */
  function inert(file) {
    return input.models.inert_file_names.includes(path.posix.basename(file).toUpperCase())
      || input.models.inert_suffixes.includes(path.posix.extname(file).toLowerCase());
  }
  /** Impõe orçamento estrutural determinístico, também no replay portátil. */
  function budget() { if (++steps > 100000) throw new Unsupported('Orçamento estrutural excedido.'); }
  /** Recusa capacidades onde a operação só compreende dados. */
  function plain(value) { if (value?.kind !== 'data') throw new Unsupported('Valor com capacidade não modelada.'); return value.value; }
  /** Resolve apenas imports relativos inequívocos presentes nas fontes originais. */
  function resolve(from, specifier) {
    try { return resolveResource(from, specifier, new Set(input.inventory_paths ?? sources.keys()), input.vitest_bound); }
    catch (error) { throw new Unsupported(error.message); }
  }
  /** Analisa um módulo inteiro; ciclos e arquivos não compreendidos ficam explícitos. */
  function module(file, imported = false) {
    budget();
    if (!sources.has(file)) throw new Unsupported('Fonte original necessária ausente: ' + file);
    if (inert(file) && imported) throw new Unsupported('Documento/asset alcançado como módulo executável.');
    if (visiting.has(file)) throw new Unsupported('Ciclo de imports não modelado.');
    if (modules.has(file)) return modules.get(file);
    visiting.add(file);
    const text = sources.get(file), findings = [], limitations = [], imports = [];
    const record = { path: file, sha256: createHash('sha256').update(text).digest('hex'), findings, imports, limitations };
    records.set(file, record);
    const ast = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
    const exports = new Map(), env = new Map([['URL', { kind: 'api', name: 'URL' }], ['JSON', { kind: 'json_namespace' }]]);
    const functions = new Set();
    let currentNode = ast;
    /** Publica regra e intervalo original de cada operação compreendida. */
    function finding(node, resource, rule, role = 'effective_dependency') {
      findings.push({ start_line: ast.getLineAndCharacterOfPosition(node.getStart(ast)).line + 1,
        end_line: ast.getLineAndCharacterOfPosition(node.end).line + 1, resource, role, rule,
        start: node.getStart(ast), end: node.end, expression: node.getText(ast) });
    }
    /** Executa abstratamente um callback com parâmetros de proveniência conhecida. */
    function callback(fn, args = [], active = false) {
      if (fn?.kind !== 'function') throw new Unsupported('Callback não resolvido.');
      if (fn.checking) throw new Unsupported('Recursão não modelada.');
      fn.checking = true;
      fn.validated = true;
      try {
        const local = new Map(fn.env);
        for (const [index, parameter] of fn.node.parameters.entries()) {
          if (!ts.isIdentifier(parameter.name) || parameter.initializer || parameter.dotDotDotToken || parameter.modifiers?.length) throw new Unsupported('Parâmetro não modelado.');
          local.set(parameter.name.text, args[index] ?? data());
        }
        if (ts.isBlock(fn.node.body)) return block(fn.node.body.statements, local, active);
        return expression(fn.node.body, local, active);
      } finally { fn.checking = false; }
    }
    /** Aplica somente modelos de APIs nominalmente resolvidas e sem substituições. */
    function invoke(api, args, node, active, construct = false) {
      const name = api.name;
      if (api.kind === 'function') return api.evaluate(args, active);
      if (api.kind === 'mock' && !construct) { args.forEach(plain); return data(); }
      if (api.kind !== 'api') throw new Unsupported('Chamada sem modelo.');
      if (name === 'URL' && construct && args.length === 2 && args[1].kind === 'module_url') {
        try { return { kind: 'snapshot_path', path: resolveSnapshotData(file, plain(args[0]), new Set(input.inventory_paths ?? sources.keys())) }; }
        catch (error) { throw new Unsupported(error.message); }
      }
      if (name === 'JSON.parse' && !construct && args.length === 1 && typeof plain(args[0]) === 'string') return data(JSON.parse(args[0].value));
      if (name === 'vitest/config.defineConfig' && args.length === 1 && !construct) return args[0];
      if (['vitest.test', 'vitest.it', 'vitest.describe', 'vitest.each'].includes(name)) {
        if (construct || args.length !== 2 || typeof plain(args[0]) !== 'string') throw new Unsupported('Registro Vitest não modelado.');
        callback(args[1]); return data();
      }
      if (/^vitest\.(?:beforeAll|afterAll|beforeEach|afterEach)$/.test(name)) {
        if (construct || args.length !== 1) throw new Unsupported('Hook não modelado.');
        callback(args[0]); return data();
      }
      if (name === 'vitest.expect' && args.length === 1 && !construct) return { kind: 'assertion' };
      if (name === 'vitest.vi.fn' && !construct && args.length <= 1) {
        if (args.length) callback(args[0]);
        finding(node, 'mock', 'vitest'); return { kind: 'mock' };
      }
      if (name === 'each' && args.length === 1 && Array.isArray(plain(args[0]))) return { kind: 'api', name: 'vitest.each' };
      if (name === 'assertion' && !construct) { args.forEach(plain); return data(); }
      if (name.startsWith('node:assert/strict.') && !construct) {
        const method = name.slice('node:assert/strict.'.length);
        if (['match', 'doesNotMatch'].includes(method)) {
          if (args.length < 2 || args.length > 3 || typeof plain(args[0]) !== 'string' || args[1].kind !== 'regexp') throw new Unsupported('Assertion regexp fora do modelo.');
          args.slice(2).forEach(plain); return data();
        }
        if (!['ok', 'equal', 'notEqual', 'strictEqual', 'notStrictEqual', 'deepEqual', 'notDeepEqual', 'deepStrictEqual', 'notDeepStrictEqual'].includes(method)
            || args.length < (method === 'ok' ? 1 : 2) || args.length > (method === 'ok' ? 2 : 3)) throw new Unsupported('Assertion Node fora do modelo.');
        args.forEach(plain); return data();
      }
      if (name === 'node:os.tmpdir' && args.length === 0 && !construct) return { kind: 'temp_root' };
      if (name === 'node:path.join' && args.length >= 2 && !construct) {
        if (!['temp_root', 'temp_path'].includes(args[0].kind) || args.slice(1).some(value =>
          typeof plain(value) !== 'string' || !value.value || value.value === '..' || /[\\/\0]/.test(value.value))) throw new Unsupported('Path sem proveniência temporária fechada.');
        if (args[0].kind === 'temp_root' && args.length !== 2) throw new Unsupported('Prefixo temporário aninhado não modelado.');
        return { kind: args[0].kind === 'temp_root' ? 'temp_prefix' : 'temp_path', origin: args[0].origin };
      }
      if (['node:fs.mkdtempSync', 'node:fs/promises.mkdtemp'].includes(name) && args.length === 1 && args[0].kind === 'temp_prefix' && !construct) {
        finding(node, 'temporary_files', 'temporary_files');
        const value = { kind: 'temp_path', origin: node.getStart(ast) };
        return name.startsWith('node:fs/promises.') ? { kind: 'promise', value } : value;
      }
      if (['node:fs.readFileSync', 'node:fs.writeFileSync', 'node:fs.rmSync', 'node:fs/promises.readFile', 'node:fs/promises.writeFile', 'node:fs/promises.rm'].includes(name) && !construct) {
        if (['node:fs.readFileSync', 'node:fs/promises.readFile'].includes(name) && args[0]?.kind === 'snapshot_path'
            && args.length === 2 && plain(args[1]) === 'utf8') {
          const target = args[0].path;
          if (!sources.has(target)) throw new Unsupported('Dados JSON originais ausentes: ' + target);
          // Validar como JSON impede tratar bytes de código/configuração opaca como dados modelados.
          JSON.parse(sources.get(target));
          imports.push({ configuration: 'snapshot_data', path: target }); module(target);
          finding(node, 'none', 'snapshot_json');
          const value = data(sources.get(target));
          return name === 'node:fs/promises.readFile' ? { kind: 'promise', value } : value;
        }
        if (args[0]?.kind !== 'temp_path' || args.length < 1 || args.length > 3) throw new Unsupported('Filesystem fora de temporário comprovado.');
        args.slice(1).forEach(plain); finding(node, 'temporary_files', 'temporary_files');
        return name.startsWith('node:fs/promises.') ? { kind: 'promise', value: data() } : data();
      }
      if (name === 'node:sqlite.DatabaseSync' && construct && args.length === 1 && plain(args[0]) === ':memory:') {
        finding(node, 'embedded_database', 'embedded_database'); return { kind: 'database' };
      }
      if (name === 'database.close' && !construct && args.length === 0) return data();
      if (name === 'node:http.createServer' && !construct && args.length === 1) {
        callback(args[0], [{ kind: 'request' }, { kind: 'response' }]);
        finding(node, 'simulation_server', 'simulation_server'); return { kind: 'server' };
      }
      if (name === 'response.end' && !construct && args.length <= 1 || name === 'response.writeHead' && !construct && args.length <= 2) {
        args.forEach(plain); return data();
      }
      if (name === 'server.listen' && !construct && args.length === 2 && plain(args[0]) === 0 && plain(args[1]) === '127.0.0.1') return { kind: 'server' };
      if (name === 'server.close' && !construct && args.length === 0) return data();
      if (['node:http.get', 'node:https.get'].includes(name) && !construct && args.length === 1 && typeof plain(args[0]) === 'string') {
        const url = new URL(args[0].value);
        if (!['http:', 'https:'].includes(url.protocol) || !input.models.real_service_hosts.includes(url.hostname)) throw new Unsupported('Natureza do endpoint não comprovada.');
        if (!active) throw new Unsupported('Chamada remota diferida sem alcance incondicional comprovado.');
        finding(node, 'real_service', 'real_service');
        Object.assign(findings.at(-1), { api: name, request_url: url.href }); return data();
      }
      throw new Unsupported('Operação sem modelo: ' + name);
    }
    /** Resolve expressões sem acesso arbitrário a propriedades, protótipos ou globais. */
    function expression(node, local, active) {
      try { return evaluateExpression(node, local, active); }
      catch (error) {
        if (error instanceof Unsupported && !error.node) error.node = node;
        throw error;
      }
    }
    /** Mantém o nó da primeira operação desconhecida, mesmo atravessando callbacks/imports. */
    function evaluateExpression(node, local, active) {
      budget();
      if (!node) return data();
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return data(node.text);
      if (ts.isNumericLiteral(node)) return data(Number(node.text));
      if (ts.isRegularExpressionLiteral(node)) return { kind: 'regexp' };
      if (node.kind === ts.SyntaxKind.TrueKeyword) return data(true);
      if (node.kind === ts.SyntaxKind.FalseKeyword) return data(false);
      if (node.kind === ts.SyntaxKind.NullKeyword) return data(null);
      if (ts.isIdentifier(node)) {
        if (!local.has(node.text)) throw new Unsupported('Binding não resolvido: ' + node.text);
        return local.get(node.text);
      }
      if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isSatisfiesExpression(node)) return expression(node.expression, local, active);
      if (ts.isAwaitExpression(node)) {
        const value = expression(node.expression, local, active);
        if (value.kind !== 'promise') throw new Unsupported('Await sem promessa modelada.');
        return value.value;
      }
      if (ts.isArrayLiteralExpression(node)) return data(node.elements.map(element => plain(expression(element, local, active))));
      if (ts.isObjectLiteralExpression(node)) {
        const value = Object.create(null);
        for (const property of node.properties) {
          if (ts.isShorthandPropertyAssignment(property) && !property.objectAssignmentInitializer && property.name.text !== '__proto__') {
            value[property.name.text] = plain(expression(property.name, local, active)); continue;
          }
          if (!ts.isPropertyAssignment(property) || ts.isComputedPropertyName(property.name) || property.name.text === '__proto__') throw new Unsupported('Propriedade não modelada.');
          value[property.name.text] = plain(expression(property.initializer, local, active));
        }
        return data(value);
      }
      if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
        if (node.asteriskToken) throw new Unsupported('Generator não modelado.');
        const fn = { kind: 'function', node, env: new Map(local) };
        functions.add(fn);
        /** Mantém a origem lexical do interpretador quando a função cruza um import. */
        fn.evaluate = (args, effective) => callback(fn, args, effective);
        return fn;
      }
      if (ts.isBinaryExpression(node) && ['+', '-', '*', '/', '%', '===', '!==', '<', '<=', '>', '>='].includes(ts.tokenToString(node.operatorToken.kind))) {
        plain(expression(node.left, local, active)); plain(expression(node.right, local, active)); return data();
      }
      if (ts.isPrefixUnaryExpression(node) && [ts.SyntaxKind.ExclamationToken, ts.SyntaxKind.MinusToken, ts.SyntaxKind.PlusToken].includes(node.operator)) {
        plain(expression(node.operand, local, active)); return data();
      }
      if (ts.isPropertyAccessExpression(node) && !node.questionDotToken) {
        if (ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword
            && node.expression.name.text === 'meta' && node.name.text === 'url') return { kind: 'module_url' };
        const receiver = expression(node.expression, local, active), name = node.name.text;
        if (receiver.kind === 'json_namespace' && name === 'parse') return { kind: 'api', name: 'JSON.parse' };
        if (receiver.kind === 'assert_namespace' && input.models.apis['node:assert/strict'].includes(name)) return { kind: 'api', name: 'node:assert/strict.' + name };
        if (receiver.kind === 'api' && receiver.name === 'vitest.vi' && name === 'fn') return { kind: 'api', name: 'vitest.vi.fn' };
        if (receiver.kind === 'api' && ['vitest.test', 'vitest.it', 'vitest.describe'].includes(receiver.name) && name === 'each') return { kind: 'api', name: 'each' };
        if (receiver.kind === 'assertion' && ['toBe', 'toEqual', 'toStrictEqual', 'toBeTruthy', 'toBeFalsy', 'toBeUndefined', 'toBeNull'].includes(name)) return { kind: 'api', name: 'assertion' };
        if (receiver.kind === 'database' && name === 'close' || receiver.kind === 'server' && ['listen', 'close'].includes(name)
            || receiver.kind === 'response' && ['end', 'writeHead'].includes(name)) return { kind: 'api', name: receiver.kind + '.' + name };
        throw new Unsupported('Acesso a propriedade sem modelo.');
      }
      if ((ts.isCallExpression(node) || ts.isNewExpression(node)) && !node.questionDotToken) {
        return invoke(expression(node.expression, local, active), (node.arguments ?? []).map(argument => expression(argument, local, active)), node, active, ts.isNewExpression(node));
      }
      throw new Unsupported('Expressão sem modelo: ' + ts.SyntaxKind[node.kind]);
    }
    /** Inspeciona todos os statements, sem saltar código após returns ou ramos opacos. */
    function block(statements, local, active) {
      let result = data();
      for (const statement of statements) {
        budget();
        currentNode = statement;
        if (ts.isTypeAliasDeclaration(statement) || ts.isInterfaceDeclaration(statement)) continue;
        if (statement.modifiers?.some(modifier => ![ts.SyntaxKind.ExportKeyword, ts.SyntaxKind.AsyncKeyword, ts.SyntaxKind.DefaultKeyword].includes(modifier.kind))) throw new Unsupported('Modificador não modelado.');
        if (ts.isImportDeclaration(statement)) {
          if (statement.importClause?.isTypeOnly) continue;
          if (!ts.isStringLiteral(statement.moduleSpecifier) || statement.attributes) throw new Unsupported('Import não modelado.');
          const specifier = statement.moduleSpecifier.text;
          const api = input.models.apis[specifier];
          let imported;
          if (api) {
            if (specifier.startsWith('vitest') && !input.vitest_bound) throw new Unsupported('Implementação Vitest não conferida.');
            imported = new Map(api.map(name => [name, { kind: 'api', name: specifier + '.' + name }]));
            if (specifier === 'node:assert/strict') imported.set('default', { kind: 'assert_namespace' });
            imports.push({ specifier, model: specifier.startsWith('vitest') ? 'vitest' : 'node' });
          } else {
            const target = resolve(file, specifier); imported = module(target, true); imports.push({ specifier, path: target });
          }
          const clause = statement.importClause;
          if (clause?.name) {
            if (!imported.has('default')) throw new Unsupported('Default import não resolvido.');
            local.set(clause.name.text, imported.get('default'));
          }
          if (clause?.namedBindings) {
            if (!ts.isNamedImports(clause.namedBindings)) throw new Unsupported('Namespace import não modelado.');
            for (const element of clause.namedBindings.elements) {
              if (element.isTypeOnly) continue;
              const value = imported.get((element.propertyName ?? element.name).text);
              if (!value || local.has(element.name.text)) throw new Unsupported('Import binding não modelado: ' + specifier + ': ' + (element.propertyName ?? element.name).text);
              local.set(element.name.text, value);
            }
          }
        } else if (ts.isExportDeclaration(statement)) {
          if (statement.isTypeOnly) continue;
          if (statement.attributes || statement.exportClause && !ts.isNamedExports(statement.exportClause)) throw new Unsupported('Reexport não modelado.');
          let imported = local;
          if (statement.moduleSpecifier) {
            if (!ts.isStringLiteral(statement.moduleSpecifier)) throw new Unsupported('Reexport não literal.');
            const specifier = statement.moduleSpecifier.text, target = resolve(file, specifier);
            imported = module(target, true); imports.push({ specifier, path: target });
          }
          const bindings = statement.exportClause ? statement.exportClause.elements.filter(element => !element.isTypeOnly)
            .map(element => [element.name.text, (element.propertyName ?? element.name).text])
            : [...imported.keys()].filter(name => name !== 'default').map(name => [name, name]);
          for (const [name, original] of bindings) {
            if (!imported.has(original) || exports.has(name)) throw new Unsupported('Reexport ausente/ambíguo: ' + name);
            exports.set(name, imported.get(original));
          }
        } else if (ts.isVariableStatement(statement) && statement.declarationList.flags & ts.NodeFlags.Const) {
          for (const declaration of statement.declarationList.declarations) {
            if (!ts.isIdentifier(declaration.name) || local.has(declaration.name.text) || !declaration.initializer) throw new Unsupported('Declaração não modelada.');
            const value = expression(declaration.initializer, local, active);
            if (value.kind === 'function') callback(value);
            local.set(declaration.name.text, value);
            if (statement.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) exports.set(declaration.name.text, value);
          }
        } else if (ts.isFunctionDeclaration(statement) && statement.name && statement.body && !statement.asteriskToken) {
          const value = { kind: 'function', node: statement, env: new Map(local) };
          functions.add(value);
          /** Preserva bindings e coordenadas do módulo original em chamadas locais. */
          value.evaluate = (args, effective) => callback(value, args, effective);
          callback(value); local.set(statement.name.text, value);
          if (statement.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) exports.set(statement.name.text, value);
        } else if (ts.isExportAssignment(statement) && !statement.isExportEquals) exports.set('default', expression(statement.expression, local, active));
        else if (ts.isExpressionStatement(statement)) expression(statement.expression, local, active);
        else if (ts.isReturnStatement(statement)) { result = expression(statement.expression, local, false); active = false; }
        else if (ts.isBlock(statement)) block(statement.statements, new Map(local), active);
        else if (ts.isForOfStatement(statement) && !statement.awaitModifier && ts.isVariableDeclarationList(statement.initializer)
            && statement.initializer.flags & ts.NodeFlags.Const && statement.initializer.declarations.length === 1
            && ts.isIdentifier(statement.initializer.declarations[0].name) && Array.isArray(plain(expression(statement.expression, local, active)))) {
          const nested = new Map(local); nested.set(statement.initializer.declarations[0].name.text, data());
          if (!ts.isBlock(statement.statement)) throw new Unsupported('Loop sem bloco não modelado.');
          block(statement.statement.statements, nested, false);
        } else if (!ts.isEmptyStatement(statement)) throw new Unsupported('Statement sem modelo: ' + ts.SyntaxKind[statement.kind]);
      }
      return result;
    }
    try {
      if (inert(file) && loaded.has(file)) throw new Unsupported('Documento/asset carregado como módulo executável.');
      if (inert(file)) {
        // Sem import/carregamento, o grafo fechado não possui operação capaz de ler/executar este conteúdo.
      } else if (path.posix.basename(file) === '.npmrc') {
        if (text.trim() !== 'legacy-peer-deps=true') throw new Unsupported('Configuração npm fora do modelo.');
      } else if (/\.json$/.test(file)) {
        const value = JSON.parse(text); exports.set('default', data(value));
        if (file.endsWith('package.json')) {
           if (value.vitest || value.vite || value.jest || value.mocha) throw new Unsupported('Manifesto com configuração não modelada.');
          for (const [name, script] of Object.entries(value.scripts ?? {})) {
            if (/^(?:pre|post)(?:test|test:unit)$/.test(name)) throw new Unsupported('Lifecycle candidato não modelado.');
            if (['test', 'test:unit'].includes(name) && !/^(?:vitest run(?: --project [\w-]+)?|pnpm -r (?:run )?test:unit)$/.test(script)) throw new Unsupported('Script unitário não modelado.');
          }
        }
      } else if (/\.(?:[cm]?[jt]s|[jt]sx)$/.test(file)) {
        if (ast.parseDiagnostics.length || ast.referencedFiles.length || ast.typeReferenceDirectives.length) throw new Unsupported('Fonte inválida ou referência ambiente não modelada.');
        block(ast.statements, env, loaded.has(file));
        // Corpos diferidos sem consumidor modelado também precisam ser compreendidos.
        for (const fn of functions) if (!fn.validated) callback(fn);
        if (/(?:^|\/)(?:vitest|vite)\.(?:config|workspace)\./.test(file)) {
          const config = plain(exports.get('default'));
          if (!config || Object.keys(config).some(key => key !== 'test') || !config.test || Object.keys(config.test).some(key => !['include', 'exclude', 'setupFiles', 'globalSetup', 'environment'].includes(key))
              || config.test.environment && config.test.environment !== 'node') throw new Unsupported('Configuração fora do modelo fechado.');
          for (const key of ['setupFiles', 'globalSetup']) for (const setup of [config.test[key] ?? []].flat()) {
            if (typeof setup !== 'string') throw new Unsupported('Setup não declarativo.');
            const target = resolve(file, setup.startsWith('.') ? setup : './' + setup); module(target, true); imports.push({ configuration: key, path: target });
          }
        }
      } else throw new Unsupported('Formato sem modelo estrutural.');
      // A regra integral só é emitida depois de visitar toda a AST com linguagem fechada.
      const lines = text.split(/\r\n|\r|\n/).length - (/[\r\n]$/.test(text) ? 1 : 0);
      if (text.length) findings.unshift({ start_line: 1, end_line: lines, resource: 'none', role: 'not_used', rule: inert(file) ? 'closed_graph_inert' : 'immutable_data' });
    } catch (error) {
      if (!(error instanceof Unsupported || error instanceof SyntaxError)) throw error;
      limitations.push(error.message);
      const location = error.node?.getSourceFile() === ast ? error.node : currentNode;
      record.limitation_details = [{ message: error.message,
        start_line: ast.getLineAndCharacterOfPosition(location.getStart(ast)).line + 1,
        expression: location.getText(ast) }];
      findings.splice(0, findings.length);
      if (text.length) findings.push({ start_line: 1, end_line: text.split(/\r\n|\r|\n/).length - (/[\r\n]$/.test(text) ? 1 : 0), resource: 'unknown', role: 'unknown', rule: 'unmodeled' });
    } finally { visiting.delete(file); }
    modules.set(file, exports);
    return exports;
  }
  for (const file of sources.keys()) module(file);
  const files = [...records.values()].sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  return { complete: files.every(file => file.limitations.length === 0), files };
}

/** Lê exclusivamente JSON com textos originais, nunca importa arquivos candidatos. */
async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  console.log(JSON.stringify(analyzeResources(JSON.parse(Buffer.concat(chunks).toString('utf8')))));
}
if (process.argv[1] && new URL(import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\')
    === process.argv[1].replaceAll('/', '\\')) await main();

import ts from 'typescript';
import path from 'node:path';
import { walk } from './virtual-project.mjs';

const frameworks = new Map([['vitest', 'vitest'], ['@jest/globals', 'jest'], ['node:test', 'node:test'], ['mocha', 'mocha']]);
const registrationNames = new Set(['test', 'it', 'specify', 'describe', 'suite', 'context']);

/** Confere identidade externa antes de confiar na grafia de um framework reconhecido. */
function externalFramework(moduleSpecifier, checker) {
  if (!frameworks.has(moduleSpecifier.text)) return null;
  const module = checker.getSymbolAtLocation(moduleSpecifier);
  if (module?.declarations?.some(/** Descarta módulos locais que sobrescrevem o specifier conhecido. */ declaration => declaration.getSourceFile().fileName.startsWith('/candidate/'))) return null;
  return frameworks.get(moduleSpecifier.text);
}

/** Segue reexportações de APIs mesmo sem declarações instaladas dos frameworks. */
function exportedApi(moduleSpecifier, name, checker, seen = new Set()) {
  const framework = externalFramework(moduleSpecifier, checker);
  if (framework) return { framework, name, apiPath: moduleSpecifier.getSourceFile().fileName };
  const module = checker.getSymbolAtLocation(moduleSpecifier);
  const key = `${module?.declarations?.[0]?.getSourceFile().fileName}:${name}`;
  if (!module || seen.has(key)) return null;
  seen.add(key);
  const selected = checker.getExportsOfModule(module).find(/** Respeita exportações explícitas que sobrescrevem exportações estrela. */ symbol => symbol.name === name);
  for (const declaration of selected?.declarations ?? []) {
    if (ts.isExportSpecifier(declaration)) {
      const statement = declaration.parent.parent;
      if (statement.moduleSpecifier) return exportedApi(statement.moduleSpecifier, (declaration.propertyName ?? declaration.name).text, checker, seen);
      return importedApi(declaration.propertyName ?? declaration.name, checker, seen);
    }
    if (declaration.name && !ts.isExportDeclaration(declaration)) return importedApi(declaration.name, checker, seen);
  }
  for (const source of module.declarations ?? []) {
    if (!ts.isSourceFile(source)) continue;
    for (const statement of source.statements) {
      if (!ts.isExportDeclaration(statement)) continue;
      if (!statement.exportClause && statement.moduleSpecifier) {
        const result = exportedApi(statement.moduleSpecifier, name, checker, seen);
        if (result) return result;
      }
      if (!statement.exportClause || !ts.isNamedExports(statement.exportClause)) continue;
      for (const element of statement.exportClause.elements) {
        if (element.name.text !== name) continue;
        if (statement.moduleSpecifier) return exportedApi(statement.moduleSpecifier, (element.propertyName ?? element.name).text, checker, seen);
        return importedApi(element.propertyName ?? element.name, checker, seen);
      }
    }
  }
  return null;
}

/** Resolve aliases sem aceitar coincidência textual como identidade. */
export function unalias(checker, symbol) {
  const seen = new Set();
  while (symbol && symbol.flags & ts.SymbolFlags.Alias && !seen.has(symbol)) {
    seen.add(symbol);
    symbol = checker.getAliasedSymbol(symbol);
  }
  return symbol;
}

/** Remove parênteses e coerções sem usá-las como prova de tipo. */
function unwrap(expression) {
  while (expression && (ts.isParenthesizedExpression(expression) || ts.isAsExpression(expression) || ts.isNonNullExpression(expression))) expression = expression.expression;
  return expression;
}

/** Reconhece objeto global não sombreado, inclusive aliases constantes, sem exigir tipos npm. */
function globalReceiver(expression, checker, seen = new Set()) {
  expression = unwrap(expression);
  if (!expression || !ts.isIdentifier(expression)) return false;
  const symbol = checker.getSymbolAtLocation(expression);
  if (['globalThis', 'global', 'window'].includes(expression.text) && (!symbol || symbol.declarations?.every(/** Descarta vínculos locais que sombreiam o objeto global. */ declaration => declaration.getSourceFile().isDeclarationFile))) return true;
  if (!symbol || seen.has(symbol)) return false;
  seen.add(symbol);
  for (const declaration of symbol.declarations ?? []) if (ts.isVariableDeclaration(declaration) && declaration.initializer && declaration.parent.flags & ts.NodeFlags.Const) return globalReceiver(declaration.initializer, checker, seen);
  return false;
}

/** Identifica a importação efetiva de um identificador ou namespace. */
function importedApi(expression, checker, seen = new Set()) {
  expression = unwrap(expression);
  if (!expression) return null;
  if (ts.isPropertyAccessExpression(expression)) {
    const base = importedApi(expression.expression, checker, seen);
    if (base?.namespace) return { ...base, namespace: false, name: expression.name.text };
    return null;
  }
  if (!ts.isIdentifier(expression)) return null;
  const symbol = checker.getSymbolAtLocation(expression);
  if (!symbol || seen.has(symbol)) return null;
  seen.add(symbol);
  for (const declaration of symbol.declarations ?? []) {
    if (ts.isImportSpecifier(declaration) || ts.isImportClause(declaration) || ts.isNamespaceImport(declaration)) {
      let current = declaration;
      while (current && !ts.isImportDeclaration(current)) current = current.parent;
      if (!current || !ts.isStringLiteral(current.moduleSpecifier)) continue;
      const specifier = current.moduleSpecifier.text;
      const framework = externalFramework(current.moduleSpecifier, checker);
      if (framework) {
        if (ts.isImportClause(declaration) && specifier !== 'node:test') return null;
        const name = ts.isImportSpecifier(declaration) ? (declaration.propertyName ?? declaration.name).text : 'test';
        return { framework, name: name === 'default' && specifier === 'node:test' ? 'test' : name, namespace: ts.isNamespaceImport(declaration) };
      }
      if (ts.isImportSpecifier(declaration)) {
        const result = exportedApi(current.moduleSpecifier, (declaration.propertyName ?? declaration.name).text, checker, seen);
        if (result) return result;
      }
      const target = unalias(checker, symbol);
      for (const original of target?.declarations ?? []) {
        if (original !== declaration && original.name) {
          const result = importedApi(original.name, checker, seen);
          if (result) return result;
        }
      }
    }
    if (ts.isVariableDeclaration(declaration) && declaration.initializer && declaration.parent.flags & ts.NodeFlags.Const) return importedApi(declaration.initializer, checker, seen);
  }
  return null;
}

/** Procura o manifesto mais próximo para comprovar APIs globais. */
function globalFramework(filePath, files, configured = new Map()) {
  let directory = path.posix.dirname(filePath);
  while (true) {
    if (configured.has(directory)) return configured.get(directory);
    const manifest = files.get(directory === '.' ? 'package.json' : `${directory}/package.json`);
    if (manifest) {
      try {
        const data = JSON.parse(manifest);
        const packages = { ...data.dependencies, ...data.devDependencies };
        const candidates = ['jest', 'mocha'].filter(/** Seleciona frameworks com globais conhecidos. */ name => packages[name]);
        if (packages.vitest) {
          const config = [...files].find(/** Aceita somente configuração declarativa JSON explícita. */ ([name]) => name === (directory === '.' ? 'vitest.config.json' : `${directory}/vitest.config.json`));
          if (config && ts.parseConfigFileTextToJson(config[0], config[1]).config?.test?.globals === true) candidates.push('vitest');
        }
        return candidates.length === 1 ? candidates[0] : null;
      } catch { return null; }
    }
    if (directory === '.') return null;
    directory = path.posix.dirname(directory);
  }
}

/** Detecta dependências de teste quando a configuração efetiva dos globais é desconhecida. */
function potentialGlobals(filePath, files) {
  let directory = path.posix.dirname(filePath);
  while (true) {
    const manifest = files.get(directory === '.' ? 'package.json' : `${directory}/package.json`);
    if (manifest) {
      try {
        const data = JSON.parse(manifest);
        const packages = { ...data.dependencies, ...data.devDependencies };
        return Boolean(packages.vitest || packages.jest || packages.mocha);
      } catch { return true; }
    }
    if (directory === '.') return false;
    directory = path.posix.dirname(directory);
  }
}

/** Normaliza nomes literais de propriedades sem avaliar expressões dinâmicas. */
function propertyName(node) {
  if (ts.isIdentifier(node) || ts.isStringLiteralLike(node) || ts.isNumericLiteral(node)) return node.text;
  if (ts.isComputedPropertyName(node) && ts.isStringLiteralLike(node.expression)) return node.expression.text;
  return null;
}

/** Reconhece registros de testes e suítes, incluindo modificadores e tabelas each. */
function testRegistration(call, checker, filePath, files, configured) {
  let expression = call.expression;
  const modifiers = [];
  let parameterized = false;
  while (true) {
    if (ts.isCallExpression(expression) || ts.isTaggedTemplateExpression(expression)) {
      const factory = ts.isCallExpression(expression) ? expression.expression : expression.tag;
      if (ts.isPropertyAccessExpression(factory) && ['each', 'for'].includes(factory.name.text)) {
        parameterized = true;
        expression = factory.expression;
        continue;
      }
      return null;
    }
    if (ts.isPropertyAccessExpression(expression) && ['skip', 'todo', 'only', 'concurrent', 'sequential', 'fails'].includes(expression.name.text)) {
      modifiers.push(expression.name.text);
      expression = expression.expression;
      continue;
    }
    break;
  }
  let api = importedApi(expression, checker);
  const globalSymbol = checker.getSymbolAtLocation(expression);
  const ambientGlobal = !globalSymbol || globalSymbol.declarations?.every(/** Aceita apenas declarações ambientes do global comprovado pelo manifesto. */ declaration => declaration.getSourceFile().isDeclarationFile);
  if (!api && ts.isIdentifier(expression) && ambientGlobal) {
    const framework = globalFramework(filePath, files, configured);
    if (framework) api = { framework, name: expression.text, global: true };
  }
  if (!api) return null;
  const isTest = ['test', 'it'].includes(api.name) || api.framework === 'mocha' && api.name === 'specify';
  const isSuite = ['describe', 'suite'].includes(api.name) || api.framework === 'mocha' && api.name === 'context';
  if (!isTest && !isSuite) return null;
  const kind = isTest ? 'test' : 'suite';
  const callback = call.arguments.find(/** Localiza o callback sintático do teste. */ argument => ts.isArrowFunction(argument) || ts.isFunctionExpression(argument));
  if (!callback) return { ...api, kind, callback: null, modifiers, parameterized };
  const title = call.arguments[0];
  let uncertainModifiers = false;
  let knownSkip = modifiers.includes('skip') || modifiers.includes('todo');
  if (api.framework === 'node:test') {
    for (const argument of call.arguments.slice(1)) {
      if (argument === callback) continue;
      if (!ts.isObjectLiteralExpression(argument)) { uncertainModifiers = true; continue; }
      for (const property of argument.properties) {
        if (ts.isSpreadAssignment(property)) { uncertainModifiers = true; continue; }
        const name = property.name && propertyName(property.name);
        if (name === null) { uncertainModifiers = true; continue; }
        if (!['skip', 'todo', 'only'].includes(name)) continue;
        if (!ts.isPropertyAssignment(property)) { modifiers.push(name); uncertainModifiers = true; continue; }
        if (property.initializer.kind === ts.SyntaxKind.FalseKeyword) continue;
        modifiers.push(name);
        const literal = property.initializer.kind === ts.SyntaxKind.TrueKeyword || ts.isStringLiteralLike(property.initializer);
        if (!literal) uncertainModifiers = true;
        else if (['skip', 'todo'].includes(name)) knownSkip = true;
      }
    }
  }
  return { ...api, kind, callback, modifiers, parameterized, skipped: uncertainModifiers ? null : knownSkip, uncertainModifiers, name: title && ts.isStringLiteralLike(title) ? title.text : title?.getText() ?? '' };
}

/** Valida ancestralidade de registro e herda estado das suítes reconhecidas. */
function registrationContext(node, registration, suites) {
  const modifiers = [...registration.modifiers];
  let skipped = registration.skipped;
  let uncertainModifiers = registration.uncertainModifiers;
  let uncertainRegistration = false;
  for (let ancestor = node.parent; ancestor; ancestor = ancestor.parent) {
    if (ts.isFunctionLike(ancestor)) {
      const suite = suites.get(ancestor);
      if (!suite) { uncertainRegistration = true; continue; }
      for (const modifier of suite.modifiers) if (!modifiers.includes(modifier)) modifiers.push(modifier);
      if (suite.skipped === true) skipped = true;
      else if (suite.skipped === null && skipped !== true) skipped = null;
      uncertainModifiers ||= suite.uncertainModifiers;
    }
    if (ts.isIfStatement(ancestor) || ts.isConditionalExpression(ancestor) || ts.isIterationStatement(ancestor, false) || ts.isSwitchStatement(ancestor) || ts.isTryStatement(ancestor)) uncertainRegistration = true;
    if (ts.isClassDeclaration(ancestor) || ts.isClassExpression(ancestor)) uncertainRegistration = true;
  }
  return { ...registration, modifiers, skipped, uncertainModifiers, uncertainRegistration };
}

/** Resolve identidade de classe declarada ou expressão ligada a constante local. */
function classFor(expression, checker) {
  expression = unwrap(expression);
  if (ts.isClassExpression(expression)) return expression;
  const symbol = unalias(checker, checker.getSymbolAtLocation(expression));
  for (const declaration of symbol?.declarations ?? []) {
    if (ts.isClassDeclaration(declaration)) return declaration;
    if (ts.isVariableDeclaration(declaration) && declaration.parent.flags & ts.NodeFlags.Const && declaration.initializer && ts.isClassExpression(unwrap(declaration.initializer))) return unwrap(declaration.initializer);
  }
  return null;
}

/** Detecta construtores próprios ou herdados capazes de substituir a instância criada. */
function constructorMayReplace(classNode, checker, seen = new Set()) {
  if (seen.has(classNode) || classNode.getSourceFile().isDeclarationFile) return true;
  seen.add(classNode);
  let replacement = false;
  /** Examina retornos do construtor sem incluir callbacks ou funções internas. */
  function visit(node) {
    if (ts.isFunctionLike(node)) return;
    if (ts.isReturnStatement(node) && node.expression && unwrap(node.expression).kind !== ts.SyntaxKind.ThisKeyword) replacement = true;
    ts.forEachChild(node, visit);
  }
  for (const member of classNode.members) if (ts.isConstructorDeclaration(member)) {
    if (!member.body) { replacement = true; continue; }
    visit(member.body);
  }
  if (replacement) return true;
  for (const heritage of classNode.heritageClauses ?? []) {
    if (heritage.token !== ts.SyntaxKind.ExtendsKeyword) continue;
    for (const base of heritage.types) {
      const parent = classFor(base.expression, checker);
      if (!parent || constructorMayReplace(parent, checker, seen)) return true;
    }
  }
  return false;
}

/** Resolve somente receptores concretos sem despacho polimórfico ou substituição no construtor. */
function receiverClass(expression, checker) {
  expression = unwrap(expression);
  if (ts.isNewExpression(expression)) {
    const classNode = classFor(expression.expression, checker);
    return classNode && !constructorMayReplace(classNode, checker) ? classNode : null;
  }
  if (!ts.isIdentifier(expression)) return null;
  const symbol = unalias(checker, checker.getSymbolAtLocation(expression));
  const classDeclaration = classFor(expression, checker);
  if (classDeclaration) return { staticClass: classDeclaration };
  const variable = symbol?.declarations?.find(/** Exige instância local imutável e sem anotação polimórfica. */ declaration => ts.isVariableDeclaration(declaration) && !declaration.type && declaration.parent.flags & ts.NodeFlags.Const);
  if (!variable?.initializer || !ts.isNewExpression(unwrap(variable.initializer))) return null;
  return receiverClass(variable.initializer, checker);
}

/** Resolve chamadas de funções e métodos para declarações reais. */
export function resolveCall(call, checker, preserveCandidate = false) {
  const expression = unwrap(call.expression);
  const type = checker.getTypeAtLocation(expression);
  const uncertainType = Boolean(type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.Union));
  if (uncertainType && !preserveCandidate) return { target: null, ambiguous: true };
  const symbol = unalias(checker, checker.getSymbolAtLocation(ts.isPropertyAccessExpression(expression) ? expression.name : expression));
  let declarations = symbol?.declarations ?? [];
  const aliases = new Set();
  while (true) {
    const alias = declarations.find(/** Reconhece alias local imutável de função, sem aceitar nomes iguais. */ declaration => ts.isVariableDeclaration(declaration) && declaration.parent.flags & ts.NodeFlags.Const && declaration.initializer && (ts.isIdentifier(declaration.initializer) || ts.isPropertyAccessExpression(declaration.initializer)));
    if (!alias || aliases.has(alias)) break;
    aliases.add(alias);
    const initializer = alias.initializer;
    declarations = unalias(checker, checker.getSymbolAtLocation(ts.isPropertyAccessExpression(initializer) ? initializer.name : initializer))?.declarations ?? [];
  }
  const target = declarations.find(/** Seleciona implementação concreta, ignorando overloads sem corpo. */ declaration => (ts.isFunctionDeclaration(declaration) || ts.isMethodDeclaration(declaration)) && declaration.body);
  if (!target) {
    const mutableAlias = declarations.some(/** Sinaliza referência local que pode ser alias mutável, sem atribuir execução. */ declaration => ts.isVariableDeclaration(declaration) && declaration.initializer && (ts.isIdentifier(declaration.initializer) || ts.isPropertyAccessExpression(declaration.initializer)));
    const abstractMember = declarations.some(/** Mantém despachos por interface ou membro abstrato como pendentes. */ declaration => ts.isMethodSignature(declaration) || ts.isPropertySignature(declaration) || ts.isMethodDeclaration(declaration));
    return { target: null, ambiguous: uncertainType || mutableAlias || abstractMember };
  }
  if (ts.isMethodDeclaration(target)) {
    if (!ts.isPropertyAccessExpression(expression)) return { target: null, ambiguous: true };
    const receiver = receiverClass(expression.expression, checker);
    const isStatic = target.modifiers?.some(/** Verifica modificador estático. */ modifier => modifier.kind === ts.SyntaxKind.StaticKeyword) ?? false;
    if (!receiver || (isStatic ? receiver.staticClass !== target.parent : receiver !== target.parent)) return { target, ambiguous: true };
  }
  return { target, ambiguous: uncertainType };
}

/** Limita pendências a referências locais, sem penalizar APIs npm externas comuns. */
function relevantUncertainty(call, checker) {
  let expression = unwrap(call.expression);
  while (ts.isPropertyAccessExpression(expression)) expression = unwrap(expression.expression);
  if (!ts.isIdentifier(expression)) return false;
  const symbol = checker.getSymbolAtLocation(expression);
  if (!symbol) return false;
  for (const declaration of symbol.declarations ?? []) {
    let current = declaration;
    while (current && !ts.isImportDeclaration(current) && !ts.isSourceFile(current)) current = current.parent;
    if (current && ts.isImportDeclaration(current)) return current.moduleSpecifier.text.startsWith('.');
    if (!declaration.getSourceFile().isDeclarationFile) return true;
  }
  return false;
}

/** Coleta testes comprovados e referências diretas, isolando callbacks diferidos. */
export function collectTests(project) {
  const records = [];
  const testPaths = new Set();
  const pendingTargets = new Set();
  const pendingPaths = new Set();
  const uncertainCalls = [];
  const mockedPaths = new Set();
  const mockedTargets = new Set();
  const effects = new Map();
  const sourcePaths = [...project.files.keys()].filter(/** Seleciona fontes analisáveis de testes. */ name => /\.[cm]?[jt]sx?$/.test(name));
  for (const filePath of sourcePaths) {
    const program = project.programFor(filePath);
    const source = program?.getSourceFile('/candidate/' + filePath);
    const effectProgram = project.snapshotMode ? project.contextProgramFor(filePath) : program;
    const effectSource = effectProgram?.getSourceFile('/candidate/' + filePath);
    if (!effectSource) continue;
    const context = project.snapshotMode ? project.contextFor(filePath) : null;
    const configured = context?.globalFrameworks;
    const localMockedPaths = project.snapshotMode ? new Set() : mockedPaths;
    const localMockedTargets = project.snapshotMode ? new Set() : mockedTargets;
    const overriddenGlobals = new Set(), overriddenFrameworks = new Set();
    effects.set(filePath, { mockedPaths: localMockedPaths, mockedTargets: localMockedTargets, overriddenGlobals, overriddenFrameworks });
    const effectChecker = effectProgram.getTypeChecker();
    walk(effectSource, /** Coleta apenas efeitos de contexto; AST auxiliar nunca comprova chamadas científicas. */ node => {
      const checker = effectChecker, source = effectSource;
      if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
        const left = node.left;
        if (project.snapshotMode && ts.isPropertyAccessExpression(left) && ['globalThis', 'global', 'window'].includes(left.expression.getText(source)) && ['test', 'it', 'describe', 'suite', 'specify'].includes(left.name.text)) overriddenGlobals.add(left.name.text);
        const symbol = unalias(checker, checker.getSymbolAtLocation(ts.isPropertyAccessExpression(left) ? left.name : left));
        for (const declaration of symbol?.declarations ?? []) if (ts.isMethodDeclaration(declaration) || ts.isFunctionDeclaration(declaration)) localMockedTargets.add(identity(declaration, project));
      }
      if (!ts.isCallExpression(node)) return;
      if (!ts.isPropertyAccessExpression(node.expression)) return;
      const api = importedApi(node.expression.expression, checker);
      const mockSymbol = checker.getSymbolAtLocation(node.expression.expression);
      const globalMock = ts.isIdentifier(node.expression.expression) && ['jest', 'vi'].includes(node.expression.expression.text) && (!mockSymbol || mockSymbol.declarations?.every(/** Aceita declaração ambiente do objeto global comprovado. */ declaration => declaration.getSourceFile().isDeclarationFile)) && globalFramework(filePath, project.files, configured);
      if (!(api && ['vi', 'jest'].includes(api.name)) && !globalMock) return;
      if (project.snapshotMode && node.expression.name.text === 'stubGlobal' && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0]) && registrationNames.has(node.arguments[0].text)) overriddenGlobals.add(node.arguments[0].text);
      if (['mock', 'doMock', 'unstable_mockModule'].includes(node.expression.name.text)) {
        const specifier = node.arguments[0];
        if (specifier && ts.isStringLiteralLike(specifier)) {
          if (project.snapshotMode && frameworks.has(specifier.text)) overriddenFrameworks.add(frameworks.get(specifier.text));
          const dependencies = project.dependencies.filter(/** Recupera todas as identidades possíveis do módulo mockado. */ dependency => dependency.from === filePath && dependency.specifier === specifier.text && dependency.resolved_path);
          if (dependencies.length) for (const dependency of dependencies) localMockedPaths.add(dependency.resolved_path);
          else pendingPaths.add(filePath);
        } else pendingPaths.add(filePath);
      }
      if (node.expression.name.text === 'spyOn') {
        const [receiver, property] = node.arguments;
        if (receiver && property && ts.isStringLiteralLike(property)) {
          if (project.snapshotMode && registrationNames.has(property.text)) {
            if (globalReceiver(receiver, checker)) overriddenGlobals.add(property.text);
            const receiverApi = importedApi(receiver, checker);
            if (receiverApi?.namespace) overriddenFrameworks.add(receiverApi.framework);
          }
          const member = checker.getPropertyOfType(checker.getTypeAtLocation(receiver), property.text);
          for (const declaration of unalias(checker, member)?.declarations ?? []) {
            // Propriedades de objetos locais não são unidades científicas nem APIs de registro.
            if (project.snapshotMode && !(ts.isFunctionDeclaration(declaration) || ts.isMethodDeclaration(declaration) && (ts.isClassDeclaration(declaration.parent) || ts.isClassExpression(declaration.parent)))) continue;
            localMockedTargets.add(identity(declaration, project));
          }
        } else pendingPaths.add(filePath);
      }
    });
    if (!source) continue;
    const checker = program.getTypeChecker();
    const registrations = [];
    walk(source, /** Comprova registros somente no programa original da fonte, preservando opções e identidades. */ node => {
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && externalFramework(node.moduleSpecifier, checker)) testPaths.add(filePath);
      if (!ts.isCallExpression(node)) return;
      const registration = testRegistration(node, checker, filePath, project.files, configured);
      if (registration) {
        testPaths.add(filePath);
        registrations.push({ node, registration });
        if (registration.uncertainModifiers) pendingPaths.add(filePath);
      }
      let rootApi = node.expression;
      while (ts.isPropertyAccessExpression(rootApi)) rootApi = rootApi.expression;
      if (!registration && ts.isIdentifier(rootApi) && ['test', 'it', 'specify'].includes(rootApi.text) && !checker.getSymbolAtLocation(rootApi) && potentialGlobals(filePath, project.files) && !globalFramework(filePath, project.files, configured)) {
        testPaths.add(filePath);
        pendingPaths.add(filePath);
      }
      if (ts.isPropertyAccessExpression(node.expression) && ['extend', 'skipIf', 'runIf', 'scoped'].includes(node.expression.name.text)) {
        const base = importedApi(node.expression.expression, checker);
        if (base && ['test', 'it'].includes(base.name)) { testPaths.add(filePath); pendingPaths.add(filePath); }
      }
    });
    const suites = new Map();
    for (const { registration } of registrations) if (registration.kind === 'suite' && registration.callback) suites.set(registration.callback, registration);
    for (const entry of registrations) {
      const node = entry.node;
      const registration = registrationContext(node, entry.registration, suites);
      if (registration.uncertainRegistration || registration.uncertainModifiers) pendingPaths.add(filePath);
      if (!registration.callback) {
        if (!registration.modifiers.includes('todo')) pendingPaths.add(filePath);
        continue;
      }
      if (registration.kind === 'suite') continue;
      /** Separa execução imediata, nomes computados e inicializadores de instância diferidos. */
      function visit(body, deferred = false) {
        if (ts.isPropertyDeclaration(body)) {
          if (ts.isComputedPropertyName(body.name)) visit(body.name.expression, deferred);
          const isStatic = body.modifiers?.some(/** Identifica campos avaliados na definição da classe. */ modifier => modifier.kind === ts.SyntaxKind.StaticKeyword) ?? false;
          if (body.initializer) visit(body.initializer, deferred || !isStatic);
          return;
        }
        if (ts.isFunctionLike(body)) {
          if ((ts.isMethodDeclaration(body) || ts.isGetAccessorDeclaration(body) || ts.isSetAccessorDeclaration(body)) && ts.isComputedPropertyName(body.name)) visit(body.name.expression, deferred || !body.body);
          return;
        }
        if (ts.isCallExpression(body)) {
          const resolved = resolveCall(body, checker, Boolean(project.snapshotMode));
          if (project.snapshotMode && (resolved.ambiguous || deferred)) uncertainCalls.push({ filePath,
            line: source.getLineAndCharacterOfPosition(body.getStart()).line + 1,
            expression: body.expression.getText(source), targetId: resolved.target ? identity(resolved.target, project) : null });
          if (resolved.target) {
            const targetId = identity(resolved.target, project);
            if (resolved.ambiguous || deferred) pendingTargets.add(targetId);
            else records.push({ targetId, filePath, node, call: body, registration });
          } else if (resolved.ambiguous && relevantUncertainty(body, checker)) pendingPaths.add(filePath);
        }
        ts.forEachChild(body, /** Preserva o estado diferido ao percorrer expressões internas. */ child => visit(child, deferred));
      }
      visit(registration.callback.body);
    }
  }
  /** Propaga substituições por reexportação sem misturar escopos de testes distintos. */
  function expandMocks(paths) {
    const queue = [...paths];
    for (const filePath of queue) {
      const source = (project.snapshotMode ? project.contextProgramFor(filePath) : project.programFor(filePath))?.getSourceFile('/candidate/' + filePath);
      if (!source) continue;
      for (const statement of source.statements) {
        if (!ts.isExportDeclaration(statement) || !statement.moduleSpecifier) continue;
        const dependencies = project.dependencies.filter(/** Recupera alvos da reexportação substituída. */ dependency => dependency.from === filePath && dependency.specifier === statement.moduleSpecifier.text && dependency.resolved_path);
        for (const dependency of dependencies) if (!paths.has(dependency.resolved_path)) { paths.add(dependency.resolved_path); queue.push(dependency.resolved_path); }
      }
    }
  }
  if (project.snapshotMode) for (const record of records) {
    const context = project.contextFor(record.filePath);
    const paths = new Set(context.substitutedPaths), targets = new Set();
    const frameworks = new Set(context.substitutedFrameworks), globals = new Set();
    for (const source of context.sourcePaths) {
      const effect = effects.get(source);
      if (!effect) continue;
      for (const value of effect.mockedPaths) paths.add(value);
      for (const value of effect.mockedTargets) targets.add(value);
      for (const value of effect.overriddenFrameworks) frameworks.add(value);
      for (const value of effect.overriddenGlobals) globals.add(value);
    }
    expandMocks(paths);
    if (frameworks.has(record.registration.framework) || record.registration.global && globals.size || record.registration.apiPath && paths.has(project.relative(record.registration.apiPath))) record.registration = { ...record.registration, uncertainRegistration: true };
    record.mockedTarget = context.automockFrameworks.has(record.registration.framework) || paths.has(record.targetId.slice(0, record.targetId.lastIndexOf(':'))) || targets.has(record.targetId);
    for (const value of paths) mockedPaths.add(value);
    for (const value of targets) mockedTargets.add(value);
  }
  // Reexports de módulos mockados também impedem comprovar execução dos símbolos originais.
  if (!project.snapshotMode) expandMocks(mockedPaths);
  return { records, testPaths, pendingTargets, pendingPaths, mockedPaths, mockedTargets, uncertainCalls };
}

/** Forma identidade estável entre programas TypeScript de configurações distintas. */
export function identity(node, project) {
  return `${project.relative(node.getSourceFile().fileName)}:${node.getStart()}`;
}

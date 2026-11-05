import ts from 'typescript';
import path from 'node:path';
import { snapshotConfigurations } from './snapshot-configurations.mjs';
import { workspaceExports } from './workspace-exports.mjs';

/** Valida caminhos relativos sem permitir acesso ao disco candidato. */
export function safePath(value) {
  return typeof value === 'string' && value.length > 0 && !/[\\:\u0000-\u001f\u007f]/.test(value) && !value.startsWith('/') && value.split('/').every(/** Confere cada segmento do caminho. */ part => part && part !== '.' && part !== '..');
}

/** Percorre a árvore sintática sem executar os conteúdos. */
export function walk(node, visit) {
  visit(node);
  ts.forEachChild(node, /** Visita recursivamente os filhos sintáticos. */ child => walk(child, visit));
}

/** Obtém nome lexical ou identidade de exportação padrão anônima. */
export function declarationName(node) {
  return node.name?.getText(node.getSourceFile()) ?? 'default';
}

/** Recupera nome de classe explícito ou vínculo constante de classe anônima. */
export function className(node) {
  if (node.name) return node.name.getText(node.getSourceFile());
  if (ts.isClassExpression(node) && ts.isVariableDeclaration(node.parent) && ts.isIdentifier(node.parent.name) && node.parent.parent.flags & ts.NodeFlags.Const) return node.parent.name.text;
  return null;
}

/** Constrói programas isolados por configuração usando somente arquivos em memória. */
export function createProject(input) {
  if (!input || !Array.isArray(input.files) || (input.issues !== undefined && !Array.isArray(input.issues))) throw new Error('Entrada de protocolo inválida.');
  const files = new Map();
  for (const file of input.files) {
    if (!safePath(file.path) || typeof file.content !== 'string' || files.has(file.path)) throw new Error('Arquivo de entrada inválido ou duplicado.');
    files.set(file.path, file.content);
  }
  const orderedFiles = [...files].sort(/** Ordena caminhos por comparação lexical independente de localidade. */ ([left], [right]) => left < right ? -1 : left > right ? 1 : 0);
  files.clear();
  for (const [name, content] of orderedFiles) files.set(name, content);
  const issues = [...(input.issues ?? [])];
  const dependencies = [];
  const unresolved = [];
  const inventoryPaths = new Set((input.inventory ?? []).map(/** Indexa presença de recursos sem carregá-los como fontes. */ entry => entry.path).filter(Boolean));
  const packageNames = new Set();
  for (const [name, content] of files) {
    if (!/(^|\/)package\.json$/.test(name)) continue;
    try {
      const manifest = JSON.parse(content);
      if (typeof manifest.name === 'string' && manifest.name) packageNames.add(manifest.name);
    }
    catch { issues.push({ code: 'INVALID_MANIFEST', message: 'Manifesto JSON inválido impede comprovar APIs globais.', path: name }); }
  }
  const root = '/candidate/';
  const configs = new Map();
  /** Converte nomes virtuais em caminhos do protocolo. */
  function relative(name) { return name.startsWith(root) ? name.slice(root.length) : name; }
  /** Lê exclusivamente conteúdos fornecidos pelo coordenador. */
  function readFile(name) { return files.get(relative(name)); }
  /** Verifica presença exclusivamente no snapshot virtual. */
  function fileExists(name) { return files.has(relative(name)); }
  /** Informa diretórios derivados dos caminhos virtuais. */
  function directoryExists(name) {
    const prefix = relative(name).replace(/\/$/, '') + '/';
    return name === '/candidate' || name === root || [...files.keys()].some(/** Procura descendentes virtuais. */ key => key.startsWith(prefix));
  }
  const scopedConfigs = input.snapshot ? snapshotConfigurations(files, inventoryPaths, issues, dependencies, unresolved) : null;
  const workspaces = input.snapshot ? workspaceExports(files) : null;
  if (scopedConfigs) for (const [name, options] of scopedConfigs.options) configs.set(name, options);
  if (!input.snapshot) for (const [name, content] of files) {
    if (!/(^|\/)tsconfig[^/]*\.json$/.test(name)) continue;
    const parsed = ts.parseConfigFileTextToJson(name, content);
    if (parsed.error) issues.push({ code: 'INVALID_TSCONFIG', message: 'Configuração TypeScript inválida.', path: name });
    const base = path.posix.dirname(root + name);
    const result = ts.parseJsonConfigFileContent(parsed.config ?? {}, {
      readDirectory: /** Mantém inclusão de fontes sob controle do snapshot. */ () => [],
      readFile: /** Preserva dependências de configuração realmente lidas, inclusive extends transitivos. */ target => {
        const text = readFile(target);
        if (text !== undefined && relative(target) !== name) dependencies.push({ from: name, specifier: relative(target), resolved_path: relative(target) });
        return text;
      }, fileExists, directoryExists, useCaseSensitiveFileNames: true,
    }, base, {}, root + name);
    if (result.errors.some(/** Ignora ausência de entradas e pacotes de configuração externos apenas no V3. */ error => ![18002, 18003].includes(error.code) && !(input.snapshot && error.code === 6053 && typeof parsed.config?.extends === 'string' && !parsed.config.extends.startsWith('.')))) issues.push({ code: 'UNSUPPORTED_TSCONFIG', message: 'Configuração não pôde ser interpretada integralmente.', path: name });
    for (const reference of result.projectReferences ?? []) {
      const target = relative(ts.resolveProjectReferencePath(reference));
      if (files.has(target)) dependencies.push({ from: name, specifier: target, resolved_path: target });
      else {
        unresolved.push({ from: name, specifier: target });
        issues.push({ code: 'UNRESOLVED_PROJECT_REFERENCE', message: 'Projeto referenciado pela configuração não está preservado no snapshot.', path: name });
      }
    }
    const directory = path.posix.dirname(name);
    if (configs.has(directory)) issues.push({ code: 'MULTIPLE_TSCONFIGS', message: 'Múltiplas configurações no mesmo diretório exigem validação da configuração efetiva.', path: name });
    if (!configs.has(directory) || path.posix.basename(name) === 'tsconfig.json') configs.set(directory, result.options);
  }
  const programs = new Map();
  const contextPrograms = new Map();
  const sources = [...files.keys()].filter(/** Seleciona fontes e declarações locais. */ name => /\.[cm]?[jt]sx?$/.test(name));
  for (const name of sources) {
    const preprocessed = ts.preProcessFile(files.get(name));
    const references = preprocessed.referencedFiles;
    for (const reference of references) {
      const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(name), reference.fileName));
      if (safePath(resolved) && files.has(resolved)) dependencies.push({ from: name, specifier: reference.fileName, resolved_path: resolved });
      else {
        unresolved.push({ from: name, specifier: reference.fileName });
        issues.push({ code: 'UNRESOLVED_REFERENCE_PATH', message: 'Diretiva de referência aponta para contexto local indisponível.', path: name });
      }
    }
    for (const reference of [...preprocessed.typeReferenceDirectives, ...preprocessed.libReferenceDirectives]) {
      const resolved = preprocessed.typeReferenceDirectives.includes(reference) && ts.resolveTypeReferenceDirective(reference.fileName, root + name, configs.get(configFor(name)) ?? {}, { readFile, fileExists, directoryExists }).resolvedTypeReferenceDirective;
      if (resolved && files.has(relative(resolved.resolvedFileName))) dependencies.push({ from: name, specifier: reference.fileName, resolved_path: relative(resolved.resolvedFileName) });
      else if (!input.snapshot) {
        // A diretiva altera o escopo ambiente; a disponibilidade não é presumida por nome de pacote.
        unresolved.push({ from: name, specifier: reference.fileName });
        issues.push({ code: 'UNRESOLVED_AMBIENT_REFERENCE', message: 'Diretiva ambiente exige contexto não comprovado no snapshot.', path: name });
      }
    }
  }
  /** Identifica a configuração efetiva mais próxima, compartilhada por raízes e consultas. */
  function configFor(name) {
    if (scopedConfigs) return scopedConfigs.select(name)[0] ?? '.';
    let directory = path.posix.dirname(name);
    while (true) {
      if (configs.has(directory)) return directory;
      if (directory === '.') return '.';
      directory = path.posix.dirname(directory);
    }
  }
  const sourceGroups = new Map();
  /** Obtém opções pela origem da fonte, inclusive imports entre workspaces/configurações distintas. */
  function optionsFor(name, selected = configFor(name)) {
    return { target: ts.ScriptTarget.Latest, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler, allowJs: true, jsx: ts.JsxEmit.Preserve, noLib: true, ...configs.get(selected), noEmit: true };
  }
  for (const name of sources) {
    const selected = configFor(name);
    if (scopedConfigs && selected !== '.') dependencies.push({ from: name, specifier: selected, resolved_path: selected });
    if (!sourceGroups.has(selected)) sourceGroups.set(selected, []);
    sourceGroups.get(selected).push(root + name);
  }
  for (const [selected, rootNames] of sourceGroups) {
    const options = optionsFor(relative(rootNames[0]));
    const host = {
      getSourceFile: /** Analisa texto virtual sem carregar bibliotecas externas. */ (name, version) => {
        const text = readFile(name);
        return text === undefined ? undefined : ts.createSourceFile(name, text, version, true);
      },
      getDefaultLibFileName: /** Desativa bibliotecas não fornecidas. */ () => '/unavailable.d.ts',
      writeFile: /** Impede emissão de arquivos. */ () => {},
      getCurrentDirectory: /** Define raiz virtual estável. */ () => '/candidate',
      getDirectories: /** Não enumera diretórios reais. */ () => [],
      getCanonicalFileName: /** Preserva distinção de caixa dos caminhos POSIX. */ name => name,
      useCaseSensitiveFileNames: /** Mantém resolução determinística entre plataformas. */ () => true,
      getNewLine: /** Usa separador estável. */ () => '\n',
      readFile, fileExists, directoryExists,
      resolveModuleNames: /** Resolve dependências apenas no snapshot virtual. */ (names, containingFile) => names.map(/** Registra a resolução local ou referência relativa ausente. */ specifier => {
        const from = relative(containingFile);
        const effectiveOptions = scopedConfigs ? optionsFor(from) : options;
        let result = ts.resolveModuleName(specifier, containingFile, effectiveOptions, { readFile, fileExists, directoryExists }).resolvedModule;
        const workspace = workspaces?.resolve(specifier);
        if (workspace?.matched) {
          result ??= workspace.resolved;
          if (workspace.manifest) dependencies.push({ from, specifier: 'manifest:' + specifier, resolved_path: workspace.manifest });
          if (workspace.ambiguous) issues.push({ code: 'AMBIGUOUS_MODULE_RESOLUTION', message: 'Exports locais declaram múltiplas identidades preservadas para ' + specifier + '.', path: from });
        }
        if (scopedConfigs) {
          for (const alternative of scopedConfigs.select(from)) {
            if (alternative === configFor(from)) continue;
            const alternateOptions = optionsFor(from, alternative);
            const other = ts.resolveModuleName(specifier, containingFile, alternateOptions, { readFile, fileExists, directoryExists }).resolvedModule ?? workspace?.resolved;
            if (other?.resolvedFileName !== result?.resolvedFileName) issues.push({ code: 'AMBIGUOUS_MODULE_RESOLUTION', message: 'Identidade de ' + specifier + ' diverge entre ' + configFor(from) + ' e ' + alternative + '.', path: from });
          }
        }
        const resource = specifier.startsWith('.') ? path.posix.normalize(path.posix.join(path.posix.dirname(from), specifier)) : null;
        if (result) dependencies.push({ from, specifier, resolved_path: relative(result.resolvedFileName) });
        else if (input.snapshot && resource && inventoryPaths.has(resource) && !/\.(?:[cm]?[jt]sx?|jsonc?)$/.test(resource)) dependencies.push({ from, specifier, resolved_path: resource });
        else if (input.snapshot && /(^|\/)node_modules\//.test(resource ?? specifier)) dependencies.push({ from, specifier, resolved_path: null });
        else if (/^(?:[./\\#]|[A-Za-z]:[\\/])/.test(specifier) || [...packageNames].some(/** Não confunde autorreferências e pacotes locais com dependências npm externas. */ name => specifier === name || specifier.startsWith(name + '/')) || effectiveOptions.paths?.[specifier] || Object.keys(effectiveOptions.paths ?? {}).some(/** Reconhece aliases com curinga. */ key => key.includes('*') && specifier.startsWith(key.split('*')[0]))) {
          unresolved.push({ from, specifier });
        } else dependencies.push({ from, specifier, resolved_path: null });
        return result;
      }),
    };
    const program = ts.createProgram(rootNames, options, host);
    if (!input.snapshot && program.getOptionsDiagnostics().some(/** Detecta tipos explícitos indisponíveis sem exigir bibliotecas padrão implícitas. */ diagnostic => diagnostic.code === 2688)) issues.push({ code: 'UNRESOLVED_CONFIG_TYPES', message: 'Tipos ambientes da configuração efetiva não foram resolvidos no snapshot.' });
    programs.set(selected, program);
    // O programa auxiliar apenas retém ASTs de contexto; o resolvedor acima continua usando as opções originais.
    if (input.snapshot && options.allowJs === false) contextPrograms.set(selected, ts.createProgram(rootNames, { ...options, allowJs: true }, host));
  }
  const resolutions = new Map();
  for (const dependency of dependencies) {
    const key = `${dependency.from}:${dependency.specifier}`;
    if (resolutions.has(key) && resolutions.get(key) !== dependency.resolved_path) issues.push({ code: 'AMBIGUOUS_MODULE_RESOLUTION', message: 'Configurações locais produzem resoluções divergentes para a mesma importação.', path: dependency.from });
    resolutions.set(key, dependency.resolved_path);
  }
  /** Escolhe o programa da configuração mais próxima da fonte. */
  function programFor(name) {
    return programs.get(configFor(name));
  }
  /** Fornece AST e símbolos de contexto sem substituir o programa que comprova unidades e chamadas. */
  function contextProgramFor(name) {
    return contextPrograms.get(configFor(name)) ?? programFor(name);
  }
  /** Expõe internamente as configurações aplicáveis para localizar declarações omitidas. */
  function configPathsFor(name) { return scopedConfigs?.select(name) ?? []; }
  if (input.snapshot) for (const name of sources) {
    const program = contextProgramFor(name);
    const source = program?.getSourceFile(root + name);
    if (!source) {
      issues.push({ code: 'UNANALYZED_TEXT', message: 'Fonte preservada sem AST disponível para conferir contexto estático necessário.', path: name });
      continue;
    }
    if (!programFor(name)?.getSourceFile(root + name) && source.parseDiagnostics.length) issues.push({ code: 'INVALID_SOURCE', message: 'Fonte de contexto contém sintaxe não interpretada integralmente.', path: name });
    walk(source, /** Resolve specifiers literais de mocks mesmo sem importação do alvo no setup. */ node => {
      if (!ts.isCallExpression(node) || !ts.isPropertyAccessExpression(node.expression) || !['mock', 'doMock', 'unstable_mockModule'].includes(node.expression.name.text)) return;
      const specifier = node.arguments[0];
      if (!specifier || !ts.isStringLiteralLike(specifier)) return;
      const result = ts.resolveModuleName(specifier.text, root + name, optionsFor(name), { readFile, fileExists, directoryExists }).resolvedModule ?? workspaces?.resolve(specifier.text).resolved;
      if (result) dependencies.push({ from: name, specifier: specifier.text, resolved_path: relative(result.resolvedFileName) });
    });
  }
  return { files, issues, dependencies, unresolved, relative, programFor, contextProgramFor, inventoryPaths, configPathsFor };
}

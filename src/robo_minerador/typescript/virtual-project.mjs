import ts from 'typescript';
import path from 'node:path';

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
  for (const [name, content] of files) {
    if (!/(^|\/)package\.json$/.test(name)) continue;
    try { JSON.parse(content); }
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
  for (const [name, content] of files) {
    if (!/(^|\/)tsconfig[^/]*\.json$/.test(name)) continue;
    const parsed = ts.parseConfigFileTextToJson(name, content);
    if (parsed.error) issues.push({ code: 'INVALID_TSCONFIG', message: 'Configuração TypeScript inválida.', path: name });
    const base = path.posix.dirname(root + name);
    const result = ts.parseJsonConfigFileContent(parsed.config ?? {}, {
      readDirectory: /** Mantém inclusão de fontes sob controle do snapshot. */ () => [],
      readFile, fileExists, directoryExists, useCaseSensitiveFileNames: true,
    }, base, {}, root + name);
    if (result.errors.some(/** Ignora apenas ausência de entradas, controladas pelo auxiliar. */ error => ![18002, 18003].includes(error.code))) issues.push({ code: 'UNSUPPORTED_TSCONFIG', message: 'Configuração não pôde ser interpretada integralmente.', path: name });
    const directory = path.posix.dirname(name);
    if (configs.has(directory)) issues.push({ code: 'MULTIPLE_TSCONFIGS', message: 'Múltiplas configurações no mesmo diretório exigem validação da configuração efetiva.', path: name });
    if (!configs.has(directory) || path.posix.basename(name) === 'tsconfig.json') configs.set(directory, result.options);
  }
  const programs = new Map();
  const sources = [...files.keys()].filter(/** Seleciona fontes e declarações locais. */ name => /\.[cm]?[jt]sx?$/.test(name));
  for (const name of sources) {
    const references = ts.preProcessFile(files.get(name)).referencedFiles;
    for (const reference of references) {
      const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(name), reference.fileName));
      if (safePath(resolved) && files.has(resolved)) dependencies.push({ from: name, specifier: reference.fileName, resolved_path: resolved });
      else {
        unresolved.push({ from: name, specifier: reference.fileName });
        issues.push({ code: 'UNRESOLVED_REFERENCE_PATH', message: 'Diretiva de referência aponta para contexto local indisponível.', path: name });
      }
    }
  }
  /** Identifica a configuração efetiva mais próxima, compartilhada por raízes e consultas. */
  function configFor(name) {
    let directory = path.posix.dirname(name);
    while (true) {
      if (configs.has(directory)) return directory;
      if (directory === '.') return '.';
      directory = path.posix.dirname(directory);
    }
  }
  const sourceGroups = new Map();
  for (const name of sources) {
    const selected = configFor(name);
    if (!sourceGroups.has(selected)) sourceGroups.set(selected, []);
    sourceGroups.get(selected).push(root + name);
  }
  for (const [selected, rootNames] of sourceGroups) {
    const options = { target: ts.ScriptTarget.Latest, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler, allowJs: true, jsx: ts.JsxEmit.Preserve, noLib: true, ...configs.get(selected), noEmit: true };
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
        const result = ts.resolveModuleName(specifier, containingFile, options, { readFile, fileExists, directoryExists }).resolvedModule;
        const from = relative(containingFile);
        if (result) dependencies.push({ from, specifier, resolved_path: relative(result.resolvedFileName) });
        else if (specifier.startsWith('.') || options.paths?.[specifier] || Object.keys(options.paths ?? {}).some(/** Reconhece aliases com curinga. */ key => key.includes('*') && specifier.startsWith(key.split('*')[0]))) {
          unresolved.push({ from, specifier });
        } else dependencies.push({ from, specifier, resolved_path: null });
        return result;
      }),
    };
    const program = ts.createProgram(rootNames, options, host);
    programs.set(selected, program);
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
  return { files, issues, dependencies, unresolved, relative, programFor };
}

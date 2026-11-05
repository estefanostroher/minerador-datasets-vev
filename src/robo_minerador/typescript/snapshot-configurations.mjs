import ts from 'typescript';
import path from 'node:path';

/** Indexa configurações e entradas virtuais; nenhuma configuração candidata é executada. */
export function snapshotConfigurations(files, inventoryPaths, issues, dependencies, unresolved) {
  const root = '/candidate/';
  const records = new Map();
  const directories = new Map();
  const inherited = new Set();
  const referenced = new Set();
  const selections = new Map();
  /** Converte nomes virtuais para a identidade relativa do protocolo. */
  function relative(name) { return name === '/candidate' ? '' : name.startsWith(root) ? name.slice(root.length) : name; }
  /** Enumera arquivos/diretórios em memória para aplicar includes e excludes TypeScript. */
  function entries(directory) {
    const local = relative(directory).replace(/\/$/, '');
    const prefix = local ? local + '/' : '';
    const names = [], children = new Set();
    for (const name of inventoryPaths) {
      if (!name.startsWith(prefix)) continue;
      const tail = name.slice(prefix.length), slash = tail.indexOf('/');
      if (slash < 0) names.push(tail);
      else children.add(tail.slice(0, slash));
    }
    return { files: names, directories: [...children] };
  }
  for (const [name, content] of files) {
    if (!/(^|\/)tsconfig[^/]*\.json$/.test(name)) continue;
    const parsed = ts.parseConfigFileTextToJson(name, content);
    if (parsed.error) issues.push({ code: 'INVALID_TSCONFIG', message: 'Configuração TypeScript inválida.', path: name });
    const config = parsed.config ?? {};
    const result = ts.parseJsonConfigFileContent(config, {
      useCaseSensitiveFileNames: true,
      readDirectory: /** Aplica a semântica do compilador exclusivamente ao inventário textual. */ (directory, extensions, excludes, includes, depth) => ts.matchFiles(directory, extensions, excludes, includes, true, root, depth, entries, /** Mantém nomes virtuais canônicos. */ value => value),
      fileExists: /** Não consulta arquivos instalados no host. */ target => files.has(relative(target)),
      readFile: /** Preserva somente extends efetivamente lidos, sem tornar project references globais. */ target => {
        const local = relative(target), text = files.get(local);
        if (text !== undefined && local !== name) {
          inherited.add(local);
          dependencies.push({ from: name, specifier: local, resolved_path: local });
        }
        return text;
      },
    }, path.posix.dirname(root + name), {}, root + name);
    if (result.errors.some(/** Tipos/configurações npm ausentes são requisitos; erros locais continuam auditáveis. */ error => ![18002, 18003, 2688].includes(error.code) && !(error.code === 6053 && typeof config.extends === 'string' && !/^[./]/.test(config.extends)))) issues.push({ code: 'UNSUPPORTED_TSCONFIG', message: 'Configuração local não pôde ser interpretada integralmente.', path: name });
    const references = [];
    for (const reference of result.projectReferences ?? []) {
      const target = relative(ts.resolveProjectReferencePath(reference));
      references.push(target);
      referenced.add(target);
      if (!files.has(target)) {
        unresolved.push({ from: name, specifier: target });
        issues.push({ code: 'UNRESOLVED_PROJECT_REFERENCE', message: 'Projeto referenciado indisponível no snapshot.', path: name });
      }
    }
    records.set(name, { options: result.options, inputs: new Set(result.fileNames.map(relative)), config, references,
      invalid: Boolean(parsed.error), explicitScope: ['files', 'include', 'exclude'].some(/** Somente escopo declarado pode fazer uma configuração irmã competir com a primária. */ key => Object.hasOwn(config, key)) });
    const directory = path.posix.dirname(name);
    if (!directories.has(directory)) directories.set(directory, []);
    directories.get(directory).push(name);
  }
  for (const names of directories.values()) if (names.length > 1) {
    for (const name of names) issues.push({ code: 'MULTIPLE_TSCONFIGS', message: 'Configurações coexistentes são selecionadas por escopo; somente resolução divergente necessária bloqueia identidade.', path: name });
  }
  /** Seleciona configurações aplicáveis na ancestralidade, preferindo tsconfig.json quando inclui a fonte. */
  function applicable(name) {
    let directory = path.posix.dirname(name);
    while (true) {
      const primary = directory === '.' ? 'tsconfig.json' : directory + '/tsconfig.json';
      const primaryApplies = records.get(primary)?.inputs.has(name);
      const candidates = (directories.get(directory) ?? []).filter(/** Templates herdados não competem com configurações de entradas concretas. */ configPath => {
        const record = records.get(configPath);
        if (primaryApplies && configPath !== primary && (record.invalid || !record.explicitScope && !referenced.has(configPath))) return false;
        return (!inherited.has(configPath) || path.posix.basename(configPath) === 'tsconfig.json') && record.inputs.has(name);
      });
      if (candidates.length) return candidates.sort(/** Prioriza a configuração primária sem presumir equivalência das alternativas. */ (left, right) => Number(path.posix.basename(right) === 'tsconfig.json') - Number(path.posix.basename(left) === 'tsconfig.json') || (left < right ? -1 : 1));
      // Configuração primária omitida do texto continua necessária, mesmo sem AST/config parseada.
      if (!files.has(primary) && inventoryPaths.has(primary)) return [primary];
      if (directory === '.') return [];
      directory = path.posix.dirname(directory);
    }
  }
  /** Retorna e memoriza a seleção usada por todas as consultas da mesma fonte. */
  function select(name) {
    if (!selections.has(name)) selections.set(name, applicable(name));
    return selections.get(name);
  }
  const options = new Map([...records].map(/** Compartilha opções somente por identidade de configuração. */ ([name, record]) => [name, record.options]));
  return { options, select, records };
}

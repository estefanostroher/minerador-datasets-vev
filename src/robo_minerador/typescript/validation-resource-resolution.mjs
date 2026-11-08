import path from 'node:path';

/** Resolve o subconjunto relativo comum ao loader TS/Vite, sem consultar disco ou node_modules. */
export function resolveResource(from, specifier, inventory, remap = false) {
  if (!specifier.startsWith('./') && !specifier.startsWith('../')) throw Error('Import sem modelo de resolução: ' + specifier);
  if (/[\\\0%?#]/.test(specifier) || path.posix.isAbsolute(specifier)) throw Error('Import com caminho não canônico: ' + specifier);
  const base = path.posix.normalize(path.posix.join(path.posix.dirname(from), specifier));
  if (base === '..' || base.startsWith('../') || base.startsWith('/')) throw Error('Import fora da raiz original: ' + specifier);
  const extension = path.posix.extname(base);
  let candidates = [base];
  if (remap && ['.js', '.jsx', '.mjs', '.cjs'].includes(extension)) {
    const replacements = { '.js': ['.ts', '.tsx'], '.jsx': ['.tsx'], '.mjs': ['.mts'], '.cjs': ['.cts'] };
    candidates.push(...replacements[extension].map(suffix => base.slice(0, -extension.length) + suffix));
  } else if (!extension) {
    candidates.push(...['.ts', '.tsx', '.js', '.jsx', '.mts', '.mjs', '.cts', '.cjs', '.json',
      '/index.ts', '/index.tsx', '/index.js'].map(suffix => base + suffix));
  }
  const matches = [...new Set(candidates)].filter(candidate => inventory.has(candidate));
  // A prioridade do compilador não prova qual fonte o loader executou em um grafo ambíguo.
  if (matches.length !== 1 || /\.d\.[cm]?ts$/.test(matches[0])) throw Error('Import local ausente/ambíguo: ' + specifier);
  return matches[0];
}

/** Reconhece somente URL relativa de dados JSON baseada no módulo original, nunca no cwd. */
export function resolveSnapshotData(from, specifier, inventory) {
  if (typeof specifier !== 'string' || !specifier.endsWith('.json')) throw Error('Leitura original fora do modelo JSON.');
  return resolveResource(from, specifier, inventory, false);
}

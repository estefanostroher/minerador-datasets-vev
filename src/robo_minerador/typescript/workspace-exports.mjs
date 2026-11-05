import ts from 'typescript';
import path from 'node:path';

/** Indexa apenas nomes/exports declarados em manifests preservados, sem adivinhar diretórios npm. */
export function workspaceExports(files) {
  const packages = new Map();
  for (const [filePath, content] of files) {
    if (!/(^|\/)package\.json$/.test(filePath) || /(^|\/)node_modules\//.test(filePath)) continue;
    try {
      const manifest = JSON.parse(content);
      if (typeof manifest.name !== 'string' || !manifest.name) continue;
      if (!packages.has(manifest.name)) packages.set(manifest.name, []);
      packages.get(manifest.name).push({ path: filePath, exports: manifest.exports });
    } catch { /* O diagnóstico de manifesto permanece no projeto virtual. */ }
  }
  /** Recupera folhas de condições estáticas reconhecidas sem escolher alvos ausentes como identidade. */
  function targets(value, result = []) {
    if (typeof value === 'string') result.push(value);
    else if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const key of ['source', 'types', 'import', 'default']) if (Object.hasOwn(value, key)) targets(value[key], result);
    }
    return result;
  }
  /** Resolve nome exato/subpath exportado; múltiplos alvos concretos mantêm ambiguidade. */
  function resolve(specifier) {
    const name = specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0];
    const manifests = packages.get(name);
    if (!manifests) return { matched: false };
    if (manifests.length !== 1) return { matched: true, ambiguous: true };
    const manifest = manifests[0], subpath = specifier === name ? '.' : '.' + specifier.slice(name.length);
    let value = manifest.exports;
    let wildcard = null;
    if (value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).some(/** Distingue mapa de subpaths de mapa de condições. */ key => key.startsWith('.'))) {
      if (Object.hasOwn(value, subpath)) value = value[subpath];
      else {
        const patterns = Object.keys(value).filter(/** Aceita apenas padrões declarados com um curinga e correspondência completa. */ key => {
          const [prefix, suffix, extra] = key.split('*');
          return suffix !== undefined && extra === undefined && subpath.startsWith(prefix) && subpath.endsWith(suffix) && subpath.length >= prefix.length + suffix.length;
        }).sort(/** Respeita a maior especificidade do prefixo e depois do padrão exportado. */ (left, right) => right.indexOf('*') - left.indexOf('*') || right.length - left.length || (left < right ? -1 : 1));
        const key = patterns[0];
        if (key) {
          const [prefix, suffix] = key.split('*');
          wildcard = subpath.slice(prefix.length, subpath.length - suffix.length);
          value = value[key];
        } else value = undefined;
      }
    }
    else if (subpath !== '.') value = undefined;
    const paths = new Set();
    for (const declared of targets(value)) {
      const target = wildcard === null ? declared : declared.replaceAll('*', wildcard);
      if (!target.startsWith('./')) continue;
      const directory = path.posix.dirname(manifest.path);
      const resolved = path.posix.normalize(path.posix.join(directory, target));
      if ((directory === '.' ? !resolved.startsWith('../') : resolved.startsWith(directory + '/')) && files.has(resolved) && /\.[cm]?[jt]sx?$/.test(resolved)) paths.add(resolved);
    }
    const implementations = [...paths].filter(/** TS e JS concretos competem igualmente; declarações não são implementações. */ target => !/\.d\.[cm]?ts$/.test(target));
    const selected = implementations.length ? implementations : [...paths];
    if (selected.length !== 1) return { matched: true, ambiguous: selected.length > 1, manifest: manifest.path };
    return { matched: true, manifest: manifest.path, resolved: { resolvedFileName: '/candidate/' + selected[0], extension: ts.extensionFromPath(selected[0]), isExternalLibraryImport: false } };
  }
  return { resolve };
}

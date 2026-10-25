import { readdir, readFile, realpath, stat } from 'node:fs/promises';
import { isAbsolute, posix, relative, resolve, sep } from 'node:path';
import { InMemoryFileSystemHost, Project, SourceFile, ts } from 'ts-morph';

export class SnapshotError extends Error {}
export interface SnapshotProgram { project: Project; root: string; sourceFiles: SourceFile[]; texts: ReadonlyMap<string, string> }
export interface FileLimits { maxFiles: number; maxBytes: number }
const defaults: FileLimits = { maxFiles: 10000, maxBytes: 32 * 1024 * 1024 };
export function inside(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel === '' || (!rel.startsWith('..' + sep) && rel !== '..' && !isAbsolute(rel));
}
export function safeRelative(path: string): boolean {
  return !!path && !/[\0\\:]/.test(path) && !path.startsWith('/') && !path.split('/').some(p => p === '..' || p === '.' || !p);
}
export function sourcePath(file: SourceFile): string { return file.getFilePath().replace(/^\/snapshot\//, ''); }
export function isTestPath(path: string): boolean { return /(^|\/)(__tests__|tests?|fixtures|__fixtures__)(\/|$)|\.(test|spec)\.[cm]?[jt]sx?$/.test(path); }
export function isConfigPath(path: string): boolean { return /(^|\/)[^/]*config\.[cm]?[jt]s$/.test(path); }

async function snapshotTexts(root: string, limits: FileLimits): Promise<Map<string, string>> {
  const files = new Map<string, string>();
  let bytes = 0, count = 0;
  async function walk(directory: string, ancestors: Set<string>): Promise<void> {
    const actual = await realpath(directory);
    if (!inside(root, actual)) throw new SnapshotError('snapshot_escape');
    if (ancestors.has(actual)) throw new SnapshotError('snapshot_symlink_cycle');
    const next = new Set([...ancestors, actual]);
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (['node_modules', '.git'].includes(entry.name)) continue;
      const path = resolve(directory, entry.name);
      const physical = await realpath(path);
      if (!inside(root, physical)) throw new SnapshotError('snapshot_escape');
      if (++count > limits.maxFiles) throw new SnapshotError('snapshot_file_limit');
      const info = await stat(path);
      if (info.isDirectory()) { await walk(path, next); continue; }
      if (!info.isFile()) throw new SnapshotError('snapshot_special_file');
      if (!/\.(json|md|markdown|[cm]?[jt]sx?)$/.test(entry.name)) continue;
      if ((bytes += info.size) > limits.maxBytes) throw new SnapshotError('snapshot_byte_limit');
      const data = await readFile(path);
      if (data.length !== info.size) throw new SnapshotError('snapshot_changed');
      const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(data);
      files.set(relative(root, path).split(sep).join('/'), text);
    }
  }
  await walk(root, new Set());
  return files;
}
function configDestination(files: Map<string, string>, path: string, ref: unknown, kind: 'extends' | 'reference'): string {
  if (typeof ref !== 'string' || !ref || /[\0\\:]/.test(ref) || ref.startsWith('/') ||
      (kind === 'extends' && !ref.startsWith('.'))) {
    throw new SnapshotError('unsafe_config_reference');
  }
  const target = posix.normalize(posix.join(posix.dirname(path), ref));
  if (target === '.' && kind === 'reference' && files.has('tsconfig.json')) return 'tsconfig.json';
  if (!safeRelative(target)) throw new SnapshotError('unsafe_config_reference');
  const candidates = kind === 'extends' ? [target, target + '.json'] : [target, target + '/tsconfig.json'];
  const destination = candidates.find(p => p.endsWith('.json') && files.has(p));
  if (!destination) throw new SnapshotError('unsafe_config_reference');
  return destination;
}
function checkConfigs(files: Map<string, string>, roots: string[]): void {
  const active = new Set<string>();
  const checked = new Set<string>();
  function visit(path: string): void {
    if (active.has(path)) throw new SnapshotError('config_cycle');
    if (checked.has(path)) return;
    const text = files.get(path);
    if (text === undefined) throw new SnapshotError('missing_config');
    // Only requested/reachable TypeScript configs are interpreted as JSONC.
    const parsed = ts.parseConfigFileTextToJson(path, text);
    const value: unknown = parsed.config;
    if (parsed.error || !value || typeof value !== 'object' || Array.isArray(value)) throw new SnapshotError('invalid_config');
    const config = value as Record<string, unknown>;
    active.add(path);
    if (config.extends !== undefined) {
      const bases = Array.isArray(config.extends) ? config.extends : [config.extends];
      for (const base of bases) visit(configDestination(files, path, base, 'extends'));
    }
    if (config.references !== undefined) {
      if (!Array.isArray(config.references)) throw new SnapshotError('invalid_config');
      for (const reference of config.references) {
        if (!reference || typeof reference !== 'object' || !('path' in reference)) throw new SnapshotError('invalid_config');
        visit(configDestination(files, path, reference.path, 'reference'));
      }
    }
    active.delete(path);
    checked.add(path);
  }
  for (const root of roots) visit(root);
}
export async function openSnapshot(root: string, configs: string[], allowedRoot: string, limits: FileLimits = defaults): Promise<SnapshotProgram[]> {
  if (!isAbsolute(root) || root.includes('\0') || root.split(/[\\/]/).includes('..') || configs.some(p => !safeRelative(p))) throw new SnapshotError('invalid_snapshot_path');
  if (!inside(resolve(allowedRoot), resolve(root))) throw new SnapshotError('snapshot_escape');
  const allowed = await realpath(allowedRoot);
  let actual: string;
  try { actual = await realpath(root); } catch { throw new SnapshotError('invalid_snapshot_path'); }
  if (!inside(allowed, actual)) throw new SnapshotError('snapshot_escape');
  const files = await snapshotTexts(actual, limits);
  checkConfigs(files, configs);
  return configs.map(config => {
    if (!files.has(config)) throw new SnapshotError('missing_config');
    const fs = new InMemoryFileSystemHost();
    for (const [path, text] of files) {
      // ts-morph strips a leading BOM. A same-width whitespace sentinel keeps
      // compiler offsets identical to original UTF-16 indices; no target includes BOM.
      fs.writeFileSync('/snapshot/' + path, text.replace(/^\uFEFF/, ' '));
    }
    let project: Project;
    let sourceFiles: SourceFile[];
    try {
      project = new Project({ fileSystem: fs, tsConfigFilePath: '/snapshot/' + config, skipFileDependencyResolution: true });
      sourceFiles = project.getSourceFiles();
    }
    catch { throw new SnapshotError('invalid_config'); }
    project.resolveSourceFileDependencies();
    // Dependencies are context, not automatically eligible research units.
    return { project, root: actual, sourceFiles, texts: files };
  });
}

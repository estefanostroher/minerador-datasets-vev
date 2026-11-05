import ts from 'typescript';
import path from 'node:path';
import { testConfiguration } from './snapshot-context.mjs';

const localized = new Set(['INVALID_SOURCE', 'UNRESOLVED_LOCAL_IMPORT', 'UNRESOLVED_REFERENCE_PATH', 'AMBIGUOUS_MODULE_RESOLUTION', 'ANALYSIS_TEXT_LIMIT', 'UNANALYZED_TEXT', 'INVALID_MANIFEST', 'INVALID_TSCONFIG', 'UNSUPPORTED_TSCONFIG', 'UNRESOLVED_PROJECT_REFERENCE']);
const advisory = new Set(['UNCERTAIN_TEST_EXECUTION', 'MULTIPLE_TSCONFIGS', 'UNRESOLVED_CONFIG_TYPES', 'UNRESOLVED_AMBIENT_REFERENCE']);

/** Localiza preservação de evidências V3 sem exigir resolução universal de chamadas ou tipos npm. */
export function snapshotImpact(project) {
  const edges = new Map(), closures = new Map();
  const configurations = [], manifests = [], ambient = [], omittedAmbient = [];
  for (const dependency of project.dependencies) {
    if (!dependency.resolved_path) continue;
    if (!edges.has(dependency.from)) edges.set(dependency.from, new Set());
    edges.get(dependency.from).add(dependency.resolved_path);
  }
  for (const filePath of project.inventoryPaths) {
    if (testConfiguration.test(filePath)) configurations.push(filePath);
    if (/(^|\/)package\.json$/.test(filePath)) manifests.push(filePath);
    const source = project.programFor(filePath)?.getSourceFile('/candidate/' + filePath);
    if (!source) {
      if (/\.d\.[cm]?ts$/.test(filePath) && !project.files.has(filePath)) omittedAmbient.push(filePath);
      continue;
    }
    if (source.isDeclarationFile && !ts.isExternalModule(source) || source.statements.some(/** Ampliação global explícita pode participar da identidade selecionada. */ statement => ts.isModuleDeclaration(statement) && Boolean(statement.flags & ts.NodeFlags.GlobalAugmentation))) ambient.push(filePath);
  }
  /** Calcula fechamento de dependências necessárias, incluindo configuração efetiva por fonte. */
  function closure(roots) {
    const reached = new Set();
    for (const root of roots) {
      if (!closures.has(root)) {
        const paths = new Set([root]);
        for (const filePath of paths) for (const target of edges.get(filePath) ?? []) paths.add(target);
        closures.set(root, paths);
      }
      for (const filePath of closures.get(root)) reached.add(filePath);
    }
    return reached;
  }
  /** Confere ancestralidade sem presumir que configurações de pacotes irmãos são efetivas. */
  function ancestor(filePath, root) {
    const directory = path.posix.dirname(filePath);
    return directory === '.' || root.startsWith(directory + '/');
  }
  /** Expõe somente bloqueios que alcançam implementação ou testemunhas selecionadas. */
  function blockers(roots) {
    const globals = new Set();
    for (const root of roots) {
      const local = configurations.filter(/** Considera configurações de teste apenas na ancestralidade da evidência. */ filePath => ancestor(filePath, root));
      const depth = Math.max(-1, ...local.map(/** Determina o escopo de configuração mais próximo. */ filePath => path.posix.dirname(filePath).split('/').length));
      for (const filePath of local) if (path.posix.dirname(filePath).split('/').length === depth) globals.add(filePath);
      for (const filePath of manifests) if (ancestor(filePath, root)) globals.add(filePath);
      const program = project.programFor(root);
      for (const filePath of ambient) if (program?.getSourceFile('/candidate/' + filePath)) globals.add(filePath);
      for (const filePath of omittedAmbient) if (project.configPathsFor(filePath)[0] === project.configPathsFor(root)[0]) globals.add(filePath);
    }
    const reached = closure([...roots, ...globals]);
    const result = [];
    for (const issue of project.issues) {
      if (advisory.has(issue.code)) continue;
      if (!localized.has(issue.code) || !issue.path || reached.has(issue.path)) result.push(issue.code + ' [' + (issue.path ?? 'escopo não localizado') + ']: ' + issue.message);
    }
    for (const reference of project.unresolved) if (reached.has(reference.from)) result.push('UNRESOLVED_LOCAL_IMPORT [' + reference.from + ']: identidade local não resolvida para ' + reference.specifier);
    return [...new Set(result)].sort();
  }
  /** Usa a mesma lista de bloqueios para decisão e justificativa auditável. */
  function incomplete(roots) { return blockers(roots).length > 0; }
  return { closure, incomplete, blockers };
}

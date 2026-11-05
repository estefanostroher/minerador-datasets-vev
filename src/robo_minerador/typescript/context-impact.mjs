import ts from 'typescript';
import { walk } from './virtual-project.mjs';
import { snapshotImpact } from './snapshot-impact.mjs';

const localCodes = new Set(['INVALID_SOURCE', 'UNRESOLVED_LOCAL_IMPORT', 'UNRESOLVED_REFERENCE_PATH', 'AMBIGUOUS_MODULE_RESOLUTION', 'UNCERTAIN_TEST_EXECUTION', 'ANALYSIS_TEXT_LIMIT', 'UNANALYZED_TEXT']);
const testConfiguration = /(^|\/)(?:(?:vitest\.(?:config|workspace)|vite\.config|jest\.(?:config|preset)|mocha\.config)\.(?:[cm]?[jt]sx?|jsonc?|ya?ml)|\.(?:mocha|jest)rc(?:\.(?:[cm]?[jt]s|jsonc?|ya?ml))?|mocha\.opts)$/;

/** Indexa dependências e raízes ambientes sem inferir independência por diretório. */
export function contextImpact(project) {
  if (project.snapshotMode) return snapshotImpact(project);
  const edges = new Map();
  const globalRoots = new Set();
  const unknownResources = new Set();
  const closures = new Map();
  for (const dependency of project.dependencies) {
    if (/^(?:node:)?(?:fs|process|module)(?:\/|$)/.test(dependency.specifier)) unknownResources.add(dependency.from);
    if (!dependency.resolved_path) continue;
    if (!edges.has(dependency.from)) edges.set(dependency.from, new Set());
    edges.get(dependency.from).add(dependency.resolved_path);
  }
  for (const [filePath, content] of project.files) {
    if (/(^|\/)(?:package\.json|tsconfig[^/]*\.json)$/.test(filePath)) globalRoots.add(filePath);
    // Não executar configurações nem presumir completude de setups, presets, plugins ou recursos declarados.
    if (testConfiguration.test(filePath)) unknownResources.add(filePath);
    if (/(^|\/)package\.json$/.test(filePath)) {
      try {
        const manifest = JSON.parse(content);
        if (manifest && ['jest', 'mocha', 'vitest', 'vite'].some(/** Detecta configuração embutida sem inferir seus efeitos por campos parciais. */ name => Object.hasOwn(manifest, name))) unknownResources.add(filePath);
      } catch { /* Manifestos inválidos já possuem diagnóstico em createProject. */ }
    }
    const source = project.programFor(filePath)?.getSourceFile('/candidate/' + filePath);
    if (!source) continue;
    // Scripts, módulos ambientes e ampliações globais podem alterar símbolos fora dos imports.
    if (!ts.isExternalModule(source) || source.isDeclarationFile || source.parseDiagnostics.length) globalRoots.add(filePath);
    walk(source, /** Conserva recursos dinâmicos e efeitos globais cujo alcance não foi provado. */ node => {
      if (ts.isModuleDeclaration(node)) globalRoots.add(filePath);
      // Referências a carregadores também cobrem aliases, sem afirmar que houve execução.
      if (ts.isIdentifier(node) && ['globalThis', 'global', 'window', 'eval', 'process', 'require', 'module', 'createRequire'].includes(node.text)) {
        globalRoots.add(filePath);
        unknownResources.add(filePath);
      }
      if (!ts.isCallExpression(node) && !ts.isNewExpression(node)) return;
      const expression = node.expression;
      const name = expression.getText(source);
      if (expression.kind === ts.SyntaxKind.ImportKeyword || name === 'require' || /(?:readFile|readFileSync|open|openSync|readdir|readdirSync|fetch)$/.test(name) || name === 'URL') unknownResources.add(filePath);
      if (/\b(?:globalThis|global|window)\b/.test(name)) globalRoots.add(filePath);
      if (/\.(?:mock|doMock|unstable_mockModule|spyOn)$/.test(name)) {
        const specifier = node.arguments?.[0];
        const bounded = specifier && ts.isStringLiteralLike(specifier) && project.dependencies.some(/** Exige uma identidade já resolvida para limitar o efeito do mock. */ dependency => dependency.from === filePath && dependency.specifier === specifier.text && dependency.resolved_path);
        if (!bounded) {
          globalRoots.add(filePath);
          unknownResources.add(filePath);
        }
      }
    });
  }
  /** Calcula fechamento iterativo, compartilhando o índice e terminando em ciclos. */
  function closure(roots) {
    if (roots.length === 1 && closures.has(roots[0])) return closures.get(roots[0]);
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
  // Sem prova do recurso efetivo, nem um módulo externo prova independência do alvo acessado.
  for (const filePath of unknownResources) {
    globalRoots.add(filePath);
    project.issues.push({ code: 'UNPROVEN_RESOURCE_SCOPE', message: 'Configuração de testes, ambiente, acesso dinâmico, recurso ou efeito global sem alcance comprovado no snapshot.', path: filePath });
  }
  const globals = closure(globalRoots);
  /** Mantém desconhecidas as issues sem classificação localizada comprovada. */
  function issueBlocks(issue, reached) {
    return !localCodes.has(issue.code) || !issue.path || !project.files.has(issue.path) || reached.has(issue.path);
  }
  /** Avalia implementação e evidências positivas, conservando incertezas de alcance global. */
  function incomplete(roots, uncertainPaths = []) {
    const reached = new Set(globals);
    for (const filePath of closure(roots)) reached.add(filePath);
    for (const filePath of uncertainPaths) {
      if (closure([filePath]).has(roots[0])) return true;
    }
    return project.issues.some(/** Confere impacto sem apagar o diagnóstico original. */ issue => issueBlocks(issue, reached)) ||
      project.unresolved.some(/** Referências ausentes só bloqueiam quando alcançadas. */ reference => reached.has(reference.from)) ||
      [...unknownResources].some(/** Recursos não comprovados bloqueiam suas raízes consumidoras. */ filePath => reached.has(filePath));
  }
  return { closure, incomplete };
}

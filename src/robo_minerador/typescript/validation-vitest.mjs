import { transformSource, sourceHash } from './validation-transform.mjs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

/** Cria plugin sem substituir include/exclude/setup/aliases/projects/concurrency.
 * O bootstrap ainda precisa incorporar o plugin às configurações originais e
 * comprovar censo/fechamento global antes de habilitar execução científica.
 */
export function createValidationPlugin(plans, root, reportLimitation, reportTransform, reportResourceModule) {
  const runtimeModule = fileURLToPath(new URL('./validation-runner.mjs', import.meta.url)).replaceAll('\\', '/');
  const byPath = new Map(plans.map(/** Indexa somente arquivos do plano verificado. */
    function key(plan) { return [plan.path, plan]; }));
  return {
    name: 'robo-minerador-validation-v1',
    enforce: 'pre',
    /** Mantém transformação em memória e exige hash exato dos bytes originais. */
    transform(source, id) {
      const clean = id.split('?')[0];
      const relative = path.relative(root, clean).replaceAll('\\', '/');
      if (!relative.startsWith('../') && !relative.startsWith('.robo-validation-') && !relative.split('/').includes('node_modules')) {
        reportResourceModule?.({ path: relative, sha256: sourceHash(source), origin: 'vite_transform' });
      }
      const plan = byPath.get(relative);
      if (!plan) return null;
      if (id !== clean) throw new Error('Import com query não comprovado pelo instrumento.');
      const runtime = '__robo_validation_probe';
      const result = transformSource(source, plan, runtime,
        `import {getWorkerRuntime as ${runtime}_get} from ${JSON.stringify(runtimeModule)};const ${runtime}=${runtime}_get();`);
      for (const limitation of result.limitations) reportLimitation(limitation);
      reportTransform?.({ path: relative, sha256: result.source_sha256, instrumented_sha256: sourceHash(result.code),
        source_map: { version: result.map.version, sources: [relative], names: result.map.names, mappings: result.map.mappings } });
      // Vite resolve sources relativos ao módulo. Em pacote aninhado, repetir
      // o caminho relativo ao workspace duplicaria diretórios e perderia location.
      return { code: result.code, map: { ...result.map, sources: [clean] } };
    },
  };
}

/** Expõe cobertura indisponível separando atribuição por caso da métrica global. */
export function unavailableCoverage(reason) {
  return {
    supported: false,
    associated_tests: { status: 'unavailable', scope: 'associated_tests', lines: null, branches: null, reason },
    full_unit_suite: { status: 'unavailable', scope: 'full_unit_suite', lines: null, branches: null, reason },
  };
}

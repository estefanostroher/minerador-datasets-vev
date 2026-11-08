import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { createValidationPlugin } from './validation-vitest.mjs';
import { ValidationReporter } from './validation-reporter.mjs';
import { settings, writeRecord } from './validation-channel.mjs';

/** Injeta opções aditivas no startVitest chamado pela própria CLI original.
 * npm/pnpm, scripts, lifecycle e parsing dos argumentos continuam originais.
 * O loader altera apenas esta chamada no módulo de framework previamente hasheado.
 */
export async function instrumentStart(startVitest, anchor, ...args) {
  const config = settings();
  const installation = config.installations.find(/** Identifica a instalação da CLI efetivamente carregada. */
    function matches(value) { return value.cli_path === fileURLToPath(anchor); });
  if (!installation) throw new Error('Processo Vitest não negociado.');
  const suiteId = randomUUID();
  process.env.ROBO_VALIDATION_SUITE_ID = suiteId;
  process.env.ROBO_VALIDATION_VITEST_ROOT = installation.root;
  // O framework não pode oferecer instalação automática de providers/ambientes.
  process.env.VITEST_SKIP_INSTALL_CHECKS = '1';
  new ValidationReporter(suiteId, installation.tools, config.workspace_root, installation);
  const attached = new Set();
  const plugin = createValidationPlugin(config.plan.plans, config.workspace_root,
    /** Publica limitações sem convertê-las em contagem exata de zero. */
    function limitation(value) { writeRecord('limitation', randomUUID(), value); },
    /** Declara arquivos realmente processados; ausência não pode virar zero chamadas. */
      function transformed(value) { writeRecord('transform', suiteId + ':' + value.path, { ...value, suite_id: suiteId }); },
    /** Captura inclusive helpers/setups sem pontos de chamada candidata. */
    function resourceModule(value) { writeRecord('resource-module', suiteId + ':' + value.path + ':' + value.sha256, { ...value, suite_id: suiteId }); });
  const optionsIndex = typeof args[0] === 'string' ? 2 : 1;
  const originalOptions = args[optionsIndex] ?? {};
  const { reporters: pluralReporters, reporter: singularReporters, ...remainingOptions } = originalOptions;
  const cliReporters = pluralReporters ?? singularReporters;
  args[optionsIndex] = { ...remainingOptions, includeTaskLocation: true };
  /** Acrescenta reporter antes da criação do ciclo de execução interno do Vitest. */
  function additiveConfig(original) {
    if (original.test?.runner && original.test.runner !== '/instrument/validation-entry.mjs') {
      throw new Error('Runner original customizado não possui integração comprovada.');
    }
    const provider = originalOptions.coverage?.provider ?? original.test?.coverage?.provider ?? 'v8';
    const available = installation.coverage_providers?.some(/** Habilita somente provider já instalado e de mesma versão. */
      function compatible(value) { return value.provider === provider && value.compatible; });
    const coverage = available && originalOptions.coverage?.enabled !== false
      ? { ...original.test?.coverage, enabled: true, provider } : original.test?.coverage;
    return { test: { ...(coverage ? { coverage } : {}), includeTaskLocation: true, runner: '/instrument/validation-entry.mjs',
      reporters: [...(cliReporters ?? original.test?.reporters ?? ['default']), '/instrument/validation-reporter-entry.mjs'] } };
  }
  plugin.config = additiveConfig;
  /** Acrescenta runner e reporter aos projetos resolvidos pelo framework. */
  function configureVitest({ project, vitest }) {
    writeRecord('resolution', suiteId, { option_keys: Object.keys(originalOptions),
      cli_reporters: cliReporters ?? null, singular_reporter: originalOptions.reporter ?? null,
      resolved_reporters: vitest.reporters.map(/** Registra somente classes de reporters para negociação auditável. */
        function name(value) { return value.constructor.name; }),
      config_reporters: vitest.config.reporters.map(/** Descreve apenas formato/configuração dos reporters. */
        function name(value) { return typeof value === 'string' ? value : Array.isArray(value) ? value[0] : value.constructor.name; }) });
    if (project.config.runner && project.config.runner !== '/instrument/validation-entry.mjs') {
      throw new Error('Runner original customizado não possui integração comprovada.');
    }
    if (project.config.browser?.enabled || project.config.pool !== 'forks') {
      throw new Error('Pool/browser fora das formas comprovadas pelo instrumento.');
    }
    project.config.runner = '/instrument/validation-entry.mjs';
    project.config.includeTaskLocation = true;
    attached.add(vitest);
  }
  plugin.configureVitest = configureVitest;
  const overridesIndex = typeof args[0] === 'string' ? 3 : 2;
  const overrides = args[overridesIndex] ?? {};
  args[overridesIndex] = { ...overrides, plugins: [...(overrides.plugins ?? []), plugin] };
  const result = await Reflect.apply(startVitest, undefined, args);
  if (!attached.size) throw new Error('Hook configureVitest não alcançou nenhum projeto.');
  return result;
}

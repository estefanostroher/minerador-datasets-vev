import { settings } from './validation-channel.mjs';
import { ValidationReporter } from './validation-reporter.mjs';

/** Carrega reporter pelo mecanismo nativo do Vitest, evitando clonagem de protótipos por config. */
export default class ConfiguredValidationReporter extends ValidationReporter {
  /** Recupera somente metadados confiáveis do processo Vitest efetivo. */
  constructor() {
    const config = settings();
    const installation = config.installations.find(/** Identifica pacote herdado do bootstrap. */
      function same(value) { return value.root === process.env.ROBO_VALIDATION_VITEST_ROOT; });
    if (!installation || !process.env.ROBO_VALIDATION_SUITE_ID) throw new Error('Reporter sem sessão negociada.');
    super(process.env.ROBO_VALIDATION_SUITE_ID, installation.tools, config.workspace_root, installation);
  }
}

import path from 'node:path';
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { writeRecord } from './validation-channel.mjs';

/** Captura configuração efetiva e fontes do loader, tanto da raiz como do projeto selecionado. */
function configurationFacts(project, root) {
  const config = project.config, vite = project.vite.config;
  /** Preserva referência opaca como ausência de prova, sem executar configuração. */
  function relative(file) { return typeof file === 'string' ? path.relative(root, path.resolve(config.root, file)).replaceAll('\\', '/') || '.' : null; }
  /** Normaliza somente listas de caminhos já resolvidas pelo framework. */
  function paths(values) { return (Array.isArray(values) ? values : values ? [values] : []).map(relative); }
  const configFile = typeof vite.configFile === 'string' ? relative(vite.configFile) : null;
  const dependencies = [...new Set([...(configFile ? [configFile] : []), ...paths(vite.configFileDependencies)])];
  const globalSetup = paths(config.globalSetup);
  const sources = [...new Set([...dependencies, ...globalSetup])].map(/** Vincula bytes originais ao loader efetivo. */
    function source(file) {
      if (typeof file !== 'string' || file.startsWith('../')) return { path: file, sha256: null };
      try { return { path: file, sha256: createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex') }; }
      catch { return { path: file, sha256: null }; }
    });
  return { project_id: JSON.stringify([config.root, project.name ?? '']), root: relative(config.root),
    config_file: configFile, config_dependencies: dependencies, config_sources: sources,
    pool: config.pool, isolate: config.isolate, environment: config.environment,
    setup_files: paths(config.setupFiles), global_setup: globalSetup };
}

/** Mantém censo independente dos streams de eventos produzidos pelos workers. */
export class ValidationReporter {
  /** Abre relatório do processo Vitest antes da coleta, preservando identidade efetiva. */
  constructor(suiteId, tools, root, installation = {}) {
    this.value = { suite_id: suiteId, tools, root, cwd: process.cwd(), pid: process.pid,
      started: true, terminal: false, planned_modules: null, modules: [], tasks: [], reason: null, errors: [], coverage: null,
      resource_installation: { vitest_root: installation.root ?? null, vite_root: installation.vite_root ?? null }, resource_contexts: [] };
    this.resourceRootProject = null;
    writeRecord('suite', suiteId, this.value);
  }

  /** Mantém referência ao projeto raiz para não omitir hooks compartilhados de configurações compostas. */
  onInit(context) {
    this.resourceRootProject = typeof context.getRootProject === 'function' ? context.getRootProject() : null;
  }

  /** Censa especificações selecionadas pelo escopo original antes de importar testes. */
  onTestRunStart(specifications) {
    if (this.value.planned_modules !== null) throw new Error('Segunda passagem Vitest recusada.');
    const root = this.value.root;
    const resourceContexts = this.value.resource_contexts;
    this.value.resource_root_configuration = this.resourceRootProject ? configurationFacts(this.resourceRootProject, root) : null;
    this.value.planned_modules = specifications.map(/** Preserva todos os módulos/projetos esperados independentemente dos probes. */
      function specification(value) {
        if (value.project.config.runner !== '/instrument/validation-entry.mjs'
            || !value.project.vite.config.plugins.some(/** Exige plugin também nos projetos filhos, antes de importar testes. */
              function instrument(plugin) { return plugin.name === 'robo-minerador-validation-v1'; })) {
          throw new Error('Projeto sem pipeline aditivo comprovado do instrumento.');
        }
        if (value.project.config.server?.deps?.external?.length || value.project.config.server?.deps?.external === true
            || value.project.config.experimental?.viteModuleRunner === false || value.project.config.experimental?.nodeLoader === false) {
          throw new Error('Externalização explícita de fontes não possui observação original comprovada.');
        }
        resourceContexts.push({ file: path.relative(root, value.moduleId).replaceAll('\\', '/'),
          ...configurationFacts(value.project, root) });
        return { file: path.relative(root, value.moduleId).replaceAll('\\', '/'),
          project_id: JSON.stringify([value.project.config.root, value.project.name ?? '']), id: value.taskId };
      });
    writeRecord('suite', this.value.suite_id, this.value);
  }

  /** Registra todo módulo coletado, inclusive sem qualquer alvo candidato. */
  onTestModuleCollected(module) {
    const key = module.id;
    const value = { id: key, file: path.relative(this.value.root, module.moduleId).replaceAll('\\', '/'),
      worker_id: module.meta().__robo_worker_id ?? null, project_id: JSON.stringify([module.project.config.root, module.project.name ?? '']) };
    const index = this.value.modules.findIndex(/** Impede duplicar módulo em atualizações da coleta. */
      function same(item) { return item.id === key; });
    if (index >= 0) throw new Error('Módulo coletado novamente na mesma passagem.');
    this.value.modules.push(value);
    writeRecord('suite', this.value.suite_id, this.value);
  }

  /** Preserva cobertura global na mesma execução, sem atribuí-la aos associados. */
  onCoverage(coverage) {
    this.value.coverage = typeof coverage.toJSON === 'function' ? coverage.toJSON() : coverage;
  }

  /** Registra casos reconhecidos e resultado terminal original, não inferido dos probes. */
  onTestRunEnd(modules, errors, reason) {
    for (const module of modules) {
      const moduleRecord = this.value.modules.find(/** Recupera emissor declarado durante coleta independente. */
        function same(value) { return value.id === module.id; });
      if (!moduleRecord) {
        this.value.modules.push({ id: module.id, file: path.relative(this.value.root, module.moduleId).replaceAll('\\', '/'),
          worker_id: module.meta().__robo_worker_id ?? null, project_id: JSON.stringify([module.project.config.root, module.project.name ?? '']) });
      }
      for (const task of module.children.allTests()) {
        this.value.tasks.push({ id: task.id, file: path.relative(this.value.root, module.moduleId).replaceAll('\\', '/'),
          project_id: JSON.stringify([task.project.config.root, task.project.name ?? '']),
          location: task.location ?? null, result: task.result(), options: task.options });
      }
    }
    this.value.terminal = true;
    this.value.reason = reason;
    this.value.errors = errors;
    this.value.module_results = modules.map(/** Mantém falhas de coleta/hook e sucesso nativo de cada módulo. */
      function result(module) { return { id: module.id, state: module.state(), errors: module.errors() }; });
    writeRecord('suite', this.value.suite_id, this.value);
  }
}

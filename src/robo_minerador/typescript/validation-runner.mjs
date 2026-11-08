import { createRuntime, newEmitterId } from './validation-runtime.mjs';
import { createHash } from 'node:crypto';

const runtimeKey = Symbol.for('robo-minerador.validation.runtime.v1');

/** Recupera emissor do worker; ausência nunca equivale a zero chamadas. */
export function getWorkerRuntime() {
  if (!globalThis[runtimeKey]) throw new Error('Worker sem instrumento confiável inicializado.');
  return globalThis[runtimeKey];
}

/** Acrescenta ciclo de evidência ao TestRunner da instalação efetiva do Vitest.
 * options fornece identity, tools, declarations, writeEvent e normalizeFile.
 * Cada declaração inclui id/file/registration_line/registration_column/modifiers.
 * registration_* é a posição AST exata emitida pelo runner (callee ou aplicação
 * da tabela each), distinta do início da declaração original preservada no plano.
 * Versões precisam de prova Docker independente antes de habilitar o bootstrap.
 */
export function createValidationRunner(BaseRunner, options) {
  if (typeof BaseRunner.getTestFn !== 'function') throw new Error('API getTestFn indisponível.');
  const version = options.tools?.vitest;
  const node = process.versions.node;
  const tested = (version === '4.1.10' && node === '22.23.4' && ['7.3.6', '8.3.6'].includes(options.tools.vite))
    || (version === '4.1.11' && node === '22.23.4' && ['8.3.0', '8.3.6'].includes(options.tools.vite))
    || (version === '5.0.5' && node === '24.22.2' && ['8.3.4', '8.3.6'].includes(options.tools.vite))
    || (version === '5.0.5' && node === '26.13.0' && ['8.3.5', '8.3.6'].includes(options.tools.vite));
  if (!tested || options.tools.node !== node
      || options.tools.typescript !== '6.0.3') {
    throw new Error('Combinação de APIs fora da matriz programática comprovada em fixtures Docker.');
  }
  return class ValidationRunner extends BaseRunner {
    /** Abre emissor próprio, sem variável global de caso ativo. */
    constructor(config) {
      super(config);
      this.emitterId = newEmitterId();
      this.evidenceCases = new Map();
      this.evidenceLimitations = [];
      this.evidenceDeclarations = {};
      this.evidenceFiles = [];
      this.evidenceRuntime = createRuntime({ ...options.identity, emitter_id: this.emitterId }, options.writeEvent);
      this.evidenceRuntime.start('worker', options.tools);
      globalThis[runtimeKey] = this.evidenceRuntime;
    }

    /** Declara emissor no metadado de coleta transportado pelo próprio Vitest ao reporter. */
    onCollectStart(file) {
      super.onCollectStart(file);
      file.meta.__robo_worker_id = this.emitterId;
      this.evidenceFiles.push(file);
    }

    /** Liga localização remapeada a uma única declaração original, sem usar título. */
    async onBeforeRunTask(task) {
      await super.onBeforeRunTask(task);
      this.startEvidenceTask(task);
    }

    /** Registra um caso, permitindo identidade runtime sem testemunha positiva estática. */
    startEvidenceTask(task) {
      if (this.evidenceCases.has(task)) return;
      const file = options.normalizeFile(task.file.filepath);
      const declarations = options.declarations.filter(/** Exige posição original inequívoca. */
        function matches(declaration) {
          const esbuildApplication = options.tools.vite === '7.3.6' && declaration.registration_kind === 'application';
          return declaration.file === file
            && (esbuildApplication ? declaration.application_last_line : declaration.registration_line) === task.location?.line
            && (esbuildApplication ? declaration.application_last_column : declaration.registration_column) === task.location?.column;
        });
      if (declarations.length !== 1 && !options.allow_unassociated) {
        this.evidenceLimitations.push('Declaração runtime não mapeada: ' + file + ':' + task.id + ':' + JSON.stringify(task.location));
        return;
      }
      const declaration = declarations.length === 1 ? { ...declarations[0] } : {
        id: 'runtime-' + createHash('sha256').update(JSON.stringify([file, task.location ?? null, task.id])).digest('hex'),
        file, start: 0, end: 1, modifiers: [], static_verified: false, registration_observed: task.location ?? null,
      };
      if (!declaration.associated) {
        declaration.modifiers = [...declaration.modifiers];
        if (task.fails && !declaration.modifiers.includes('fails')) declaration.modifiers.push('fails');
      }
      this.evidenceDeclarations[declaration.id] = declaration;
      const occurrence = [...this.evidenceCases.values()].filter(/** Conta registros distintos da mesma declaração. */
        function sameDeclaration(value) { return value.declarationId === declaration.id; }).length;
      const caseId = this.evidenceRuntime.startCase({
        project_id: JSON.stringify([this.config.root, this.config.name ?? '']), file,
        declaration_id: declaration.id, occurrence, worker_id: this.emitterId,
        runtime_id: task.id, title: task.name, modifiers: declaration.modifiers,
      });
      const state = { caseId, declarationId: declaration.id, attempts: [], attempt: -1, finished: false };
      this.evidenceCases.set(task, state);
      if (task.mode === 'skip' || task.mode === 'todo' || task.result?.state === 'skip') {
        this.evidenceRuntime.finishCase(caseId, task.mode === 'skip' ? 'skipped' : 'todo', []);
        state.finished = true;
      } else if (task.result?.state === 'fail') {
        // beforeAll pode falhar antes de qualquer tentativa do corpo do teste.
        this.evidenceRuntime.finishCase(caseId, 'failed', []);
        state.finished = true;
      }
    }

    /** Inicia tentativa sem criar um novo caso em retry ou repeat. */
    onBeforeTryTask(task, attempt) {
      super.onBeforeTryTask(task, attempt);
      const state = this.evidenceCases.get(task);
      if (!state) return;
      state.attempt++;
      state.attempts.push({ index: state.attempt, retry: attempt.retry, repeat: attempt.repeats,
        status: 'failed', expected_failure: !!task.fails });
    }

    /** Executa somente callback original; hooks continuam sob controle do Vitest. */
    runTask(task) {
      const callback = BaseRunner.getTestFn(task);
      if (!callback) throw new Error('Callback original do runner indisponível.');
      const state = this.evidenceCases.get(task);
      if (!state) return callback();
      return this.evidenceRuntime.runCase(state.caseId, state.attempt, callback);
    }

    /** Preserva falha observada antes da inversão de fails e do retry verde. */
    async onAfterRetryTask(task, attempt) {
      await super.onAfterRetryTask?.(task, attempt);
      const state = this.evidenceCases.get(task);
      if (!state || !state.attempts.length) return;
      state.attempts.at(-1).status = task.result?.state === 'pass' ? 'passed'
        : task.result?.state === 'skip' ? 'skipped' : 'failed';
    }

    /** Fecha o caso com resultado do framework sem apagar as tentativas anteriores. */
    async onAfterRunTask(task) {
      await super.onAfterRunTask(task);
      const state = this.evidenceCases.get(task);
      if (!state || state.finished) return;
      const status = task.result?.state === 'pass' ? 'passed' : task.result?.state === 'skip' ? 'skipped' : 'failed';
      this.evidenceRuntime.finishCase(state.caseId, status, state.attempts);
      state.finished = true;
    }

    /** Fecha emissor com lacunas explícitas; não afirma censo global de subprocessos. */
    async onAfterRunFiles(files) {
      await super.onAfterRunFiles(files);
      const self = this;
      /** Inclui casos de suítes skipped que não passaram pelo hook de execução. */
      function visitSkipped(task) {
        if (task.type === 'test' && !self.evidenceCases.has(task)
            && (task.mode === 'skip' || task.mode === 'todo' || task.result?.state === 'skip' || task.result?.state === 'fail')) self.startEvidenceTask(task);
        for (const child of task.tasks ?? []) visitSkipped(child);
      }
      for (const file of new Set([...(files ?? []), ...this.evidenceFiles])) visitSkipped(file);
      for (const state of this.evidenceCases.values()) {
        if (!state.finished) {
          const error = new Error('ROBO_INSTRUMENT_FINALIZATION_FAILURE: Caso não finalizado pelo runner: ' + state.caseId);
          error.code = 'ROBO_INSTRUMENT_FINALIZATION_FAILURE';
          throw error;
        }
      }
      this.evidenceRuntime.finish(!this.evidenceLimitations.length, [], this.evidenceLimitations);
      options.writeWorkerRecord?.(this.emitterId, { emitter_id: this.emitterId, suite_id: options.tools.suite_id ?? null,
        declarations: this.evidenceDeclarations, closed: true });
    }
  };
}

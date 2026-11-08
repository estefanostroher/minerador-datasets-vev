import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID, createHash } from 'node:crypto';
import { isPromise } from 'node:util/types';

const apply = Reflect.apply;
const descriptor = Object.getOwnPropertyDescriptor;
const promiseFinally = Promise.prototype.finally;

/** Calcula a identidade do caso sem usar título como chave de associação. */
export function caseIdentity(value) {
  return createHash('sha256').update(JSON.stringify([
    value.project_id, value.file, value.declaration_id, value.occurrence,
    value.worker_id, value.runtime_id,
  ])).digest('hex');
}

/** Cria um emissor isolado; o consumidor fornece transporte síncrono sem perda. */
export function createRuntime(identity, writeEvent) {
  const context = new AsyncLocalStorage();
  const cases = new Map();
  let sequence = 0;
  let closed = false;
  let callOrdinal = 0;

  /** Publica sequência contígua; erro de transporte interrompe a instrumentação. */
  function emit(event, fields) {
    if (closed) throw new Error('Emissor já encerrado.');
    writeEvent({ protocol_version: 1, ...identity, sequence: ++sequence, event, ...fields });
  }

  /** Registra o caso uma vez, independentemente de tentativas e repetições. */
  function startCase(value) {
    const caseId = caseIdentity(value);
    if (cases.has(caseId)) throw new Error('Caso duplicado no emissor.');
    const state = { caseId, value, active: true };
    cases.set(caseId, state);
    emit('case_started', { case_id: caseId, case: value });
    return caseId;
  }

  /** Limita contexto ao corpo executado pelo runner, sem envolver hooks. */
  function runCase(caseId, attempt, callback) {
    const state = cases.get(caseId);
    if (!state?.active || !Number.isSafeInteger(attempt) || attempt < 0) {
      throw new Error('Contexto de caso inválido.');
    }
    const lease = { active: true };
    /** Revoga atribuição ao terminar o corpo, antes de hooks de teardown. */
    function closeLease() { lease.active = false; }
    try {
      const result = context.run({ state, attempt, token: null, lease }, callback);
      if (isPromise(result)) return apply(promiseFinally, result, [closeLease]);
      closeLease();
      return result;
    } catch (error) { closeLease(); throw error; }
  }

  /** Fecha a atribuição inclusive para tarefas assíncronas ainda pendentes. */
  function finishCase(caseId, status, attempts) {
    const state = cases.get(caseId);
    if (!state?.active) throw new Error('Fechamento de caso inválido.');
    state.active = false;
    emit('case_finished', { case_id: caseId, status, attempts });
  }

  /** Captura função/receptor antes dos argumentos, mas ativa token somente depois. */
  function prepare(pointId, targetId, callable, receiver) {
    /** Executa o original mantendo identidade de retorno, argumentos, this e throw. */
    function invoke(...args) {
      const current = context.getStore();
      if (!current?.state.active || !current.lease.active) return apply(callable, receiver, args);
      const token = { callId: identity.emitter_id + ':' + ++callOrdinal, targetId, callable, consumed: false, open: true };
      emit('direct_call', { case_id: current.state.caseId, attempt: current.attempt,
        call_id: token.callId, point_id: pointId, instance_id: targetId });
      /** Invoca sem await adicional e sem substituir o corpo original. */
      function applyOriginal() { return apply(callable, receiver, args); }
      try { return context.run({ ...current, token }, applyOriginal); }
      finally { token.open = false; }
    }
    return invoke;
  }

  /** Avalia getter do método antes dos argumentos e preserva o receptor único. */
  function member(pointId, targetId, receiver, property) {
    return prepare(pointId, targetId, receiver[property], receiver);
  }

  /** Captura método sem consultar um binding Object potencialmente sombreado na fonte. */
  function originalMethod(receiver, property) { return descriptor(receiver, property).value; }

  /** Comprova entrada no próprio corpo, consumindo token antes de recursão/helpers. */
  function enter(targetId, original) {
    const current = context.getStore();
    const token = current?.token;
    if (!current?.state.active || !current.lease.active || !token?.open || token.consumed
        || token.targetId !== targetId || token.callable !== original) return;
    token.consumed = true;
    emit('target_entered', { case_id: current.state.caseId, attempt: current.attempt,
      call_id: token.callId, instance_id: targetId });
  }

  /** Encerra emissor apenas depois de todos os casos e explicita lacunas observadas. */
  function finish(complete, emitterIds = [], limitations = []) {
    if ([...cases.values()].some(/** Detecta corpo/caso sem fechamento. */ function isActive(value) { return value.active; })) {
      throw new Error('Emissor possui casos abertos.');
    }
    emit('run_finished', { complete, emitter_ids: emitterIds, limitations });
    closed = true;
  }

  /** Abre o emissor com versões efetivas e papel explicitamente declarado. */
  function start(role, tools) { emit('run_started', { role, tools }); }

  return { start, startCase, runCase, finishCase, prepare, member, originalMethod, enter, finish };
}

/** Gera identidade exclusiva mesmo entre threads do mesmo processo. */
export function newEmitterId() { return `${process.pid}:${randomUUID()}`; }

import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import ts from 'typescript';
import { createRuntime } from '../validation-runtime.mjs';
import { sourceHash, transformSource } from '../validation-transform.mjs';

/** Produz plano explícito só para chamadas selecionadas da fixture confiável. */
function planFor(source, names, expressions) {
  const ast = ts.createSourceFile('fixture.ts', source, ts.ScriptTarget.Latest, true);
  const targets = [], points = [];
  /** Indexa offsets completos e mantém múltiplas chamadas na mesma linha distintas. */
  function visit(node) {
    if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && names.includes(node.name?.text)) {
      targets.push({ id: node.name.text, start: node.getStart(ast), end: node.end });
    }
    if (ts.isCallExpression(node) && expressions.includes(node.expression.getText(ast))) {
      points.push({ id: 'p' + points.length, instance_id: node.expression.getText(ast).split('.').at(-1),
        start: node.getStart(ast), end: node.end });
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return { path: 'fixture.ts', sha256: sourceHash(source), targets, points };
}

/** Executa código da fixture em contexto controlado, sem importar candidatos. */
function execute(source, instrumented, names, expressions, runtime) {
  const plan = planFor(source, names, expressions);
  const transformed = transformSource(source, plan, '__probe');
  assert.deepEqual(transformed.limitations, []);
  const code = ts.transpileModule(instrumented ? transformed.code : source, {
    compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.None },
  }).outputText;
  return vm.runInNewContext(code, { __probe: runtime, Promise, setTimeout });
}

/** Cria emissor e caso único para verificar correlação sem integração presumida. */
function recording() {
  const events = [];
  /** Mantém o trace completo em memória apenas nesta fixture. */
  function write(event) { events.push(event); }
  const runtime = createRuntime({ session_id: 'session', repository_id: 1, commit_sha: 'a'.repeat(40),
    instrument_id: 'instrument', emitter_id: 'worker' }, write);
  runtime.start('worker', { node: process.versions.node, typescript: ts.version });
  const caseId = runtime.startCase({ project_id: 'project', file: 'fixture.ts', declaration_id: 'decl',
    occurrence: 0, worker_id: 'worker', runtime_id: 'runtime-0', title: 'igual', modifiers: [] });
  return { runtime, events, caseId };
}

test('duas chamadas preservam retorno/throw/this/getter e correspondem a um caso',
  /** Compara os efeitos originais e instrumentados, inclusive ordem de getter e argumento. */
  function semanticEquivalence() {
    const source = `const effects: string[] = [];
      function compute(value: number) { 'use strict'; effects.push('body'); if (this !== void 0) return -1; if (value < 0) throw value; return value + 1; }
      class Counter { value = 3; add(value: number) { return this.value + value; } }
      const receiver = { value: 3, get add() { effects.push('get'); return Counter.prototype.add; } };
      function argument() { effects.push('arg'); return 2; }
      const a = compute(1), b = compute(2);
      const c = receiver.add(argument());
      /** Sombreia undefined no escopo da chamada sem mudar seu receptor original. */
      function shadowed(undefined) { return compute(1); }
      const d = shadowed({});
      let thrown; try { compute(-7); } catch (error) { thrown = error; }
      JSON.stringify({a,b,c,d,thrown,effects});`;
    // Campos com inicialização ficam fora do alvo: a comparação de this usa método sem efeitos na classe.
    const fixture = source.replace('class Counter { value = 3;', 'class Counter {');
    const state = recording();
    const baseline = execute(fixture, false, ['compute', 'add'], ['compute', 'receiver.add'], state.runtime);
    assert.equal(JSON.parse(baseline).d, 2);
    const observed = state.runtime.runCase(state.caseId, 0,
      /** Executa corpo simulado do runner no contexto exclusivo. */
      function body() { return execute(fixture, true, ['compute', 'add'], ['compute', 'receiver.add'], state.runtime); });
    assert.equal(observed, baseline);
    assert.equal(state.events.filter(/** Seleciona provas de entrada original. */
      function entries(event) { return event.event === 'target_entered'; }).length, 5);
    assert.equal(new Set(state.events.filter(/** Seleciona chamadas. */
      function calls(event) { return event.event === 'direct_call'; }).map(/** Recupera caso. */
      function id(event) { return event.case_id; })).size, 1);
  });

test('argumentos, mock encaminhador e chamada indireta não roubam token original',
  /** Diferencia atingir o callsite de realmente invocar o mesmo callable original. */
  function associationSoundness() {
    const state = recording();
    const source = `function compute(value) { return value + 1; }
      const original = compute;
      function wrapped(value) { return original(value); }
      const api = { compute: wrapped };
      api.compute(original(9));`;
    state.runtime.runCase(state.caseId, 0, /** Executa a substituição controlada. */
      function body() { execute(source, true, ['compute'], ['api.compute'], state.runtime); });
    assert.equal(state.events.filter(/** Seleciona chegada ao ponto direto. */
      function calls(event) { return event.event === 'direct_call'; }).length, 1);
    assert.equal(state.events.filter(/** Seleciona prova original indevida. */
      function entries(event) { return event.event === 'target_entered'; }).length, 0);
  });

test('concorrência, retorno Promise e tarefas tardias mantêm isolamento',
  /** Verifica contaminação entre casos com mesmo título e continuação após término. */
  async function asyncIsolation() {
    const state = recording();
    const second = state.runtime.startCase({ project_id: 'project', file: 'fixture.ts', declaration_id: 'decl',
      occurrence: 1, worker_id: 'worker', runtime_id: 'runtime-1', title: 'igual', modifiers: [] });
    const third = state.runtime.startCase({ project_id: 'project', file: 'fixture.ts', declaration_id: 'decl',
      occurrence: 2, worker_id: 'worker', runtime_id: 'runtime-2', title: 'igual', modifiers: [] });
    const originalPromise = Promise.resolve(4);
    /** Simula entrada da mesma função original assíncrona. */
    function original() { state.runtime.enter('target', original); return originalPromise; }
    const invoke = state.runtime.prepare('point', 'target', original, undefined);
    let late;
    /** Mantém tarefa da primeira execução para depois do fechamento. */
    async function first() {
      assert.equal(invoke(), originalPromise);
      await Promise.resolve();
      late = new Promise(/** Agenda trabalho que sobrevive ao caso. */
        function schedule(resolve) { setTimeout(/** Invoca após revogação. */
          function deferred() { invoke(); resolve(); }, 10); });
      invoke();
    }
    /** Intercala caso concorrente sem variável global de teste ativo. */
    async function other() { await Promise.resolve(); invoke(); }
    const crossRealm = vm.runInNewContext(`/** Mantém contexto através de Promise de outro realm. */
      (async function crossRealm(){invoke();await Promise.resolve();invoke();})`, { invoke });
    await Promise.all([state.runtime.runCase(state.caseId, 0, first), state.runtime.runCase(second, 0, other),
      state.runtime.runCase(third, 0, crossRealm)]);
    const attempts = [{ index: 0, retry: 0, repeat: 0, status: 'passed', expected_failure: false }];
    state.runtime.finishCase(state.caseId, 'passed', attempts);
    state.runtime.finishCase(second, 'passed', attempts);
    state.runtime.finishCase(third, 'passed', attempts);
    await late;
    const direct = state.events.filter(/** Seleciona chamadas atribuídas. */
      function calls(event) { return event.event === 'direct_call'; });
    assert.deepEqual(direct.map(/** Extrai identidade sem usar título. */
      function identity(event) { return event.case_id; }).sort(), [state.caseId, state.caseId, second, third, third].sort());
  });

test('optional chaining e parâmetro default ficam pendentes sem reescrita semântica',
  /** Recusa formas cujo token poderia ser consumido antes da entrada original. */
  function unsupportedSyntax() {
    const source = 'function compute(value = compute(0)) { return value; } const api = null; api?.compute(2);';
    const plan = planFor(source, ['compute'], ['api?.compute']);
    const result = transformSource(source, plan, '__probe');
    assert.equal(result.code, source);
    assert.equal(result.limitations.length, 2);
    assert.throws(/** Recusa bytes diferentes dos offsets aprovados. */
      function tampered() { transformSource(source + ' ', plan, '__probe'); }, /Fonte diverge/);
  });

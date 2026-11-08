import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { analyze } from '../analyze.mjs';
import { buildPlan } from '../validation-plan.mjs';

/** Produz unidades da análise real com IDs calculados pela identidade da T1. */
function verifiedInput(contents) {
  const files = Object.entries(contents).map(/** Preserva textos sem importar código da fixture. */
    function file([path, content]) { return { path, content }; });
  const analysis = analyze({ files, issues: [] });
  const identity = { repository_id: 42, commit_sha: 'a'.repeat(40) };
  const ordinals = new Map();
  for (const unit of analysis.instances) {
    const fn = unit.function;
    const key = JSON.stringify([fn.path, fn.start_line]);
    const ordinal = ordinals.get(key) ?? 0;
    ordinals.set(key, ordinal + 1);
    unit.instance_id = createHash('sha256').update(JSON.stringify([
      identity.repository_id, identity.commit_sha, fn.path, [fn.start_line, fn.end_line, ordinal], fn.kind, fn.name,
    ])).digest('hex');
  }
  return { files, identity, instances: analysis.instances.filter(/** Seleciona somente as unidades realmente aprovadas. */
    function approved(unit) { return unit.decision === 'approved'; }) };
}

test('plano preserva ordinal anterior rejeitado e dois callsites em um caso',
  /** Protege identidade lexical e cardinalidade na fronteira T1/instrumento. */
  function lexicalIdentity() {
    const input = verifiedInput({
      'target.ts': '/** Retorna constante. */ export function constant(){return 1;} /** Soma uma unidade. */ export function compute(value:number){return value+1;}',
      'README.md': '[compute](target.ts#compute) Soma uma unidade ao valor recebido.\n',
      'target.test.ts': "import {test,expect} from 'vitest';import {compute} from './target';\n"
        + "test('igual',/** Exercita dois pontos. */function twice(){compute(1);compute(2);});\n"
        + "test('igual',/** Caso sem associação. */function unrelated(){expect(true).toBe(true);});",
    });
    assert.equal(input.instances.length, 1);
    const plan = buildPlan(input);
    assert.deepEqual(plan.limitations, []);
    assert.equal(Object.keys(plan.targets).length, 1);
    assert.equal(Object.keys(plan.points).length, 2);
    assert.equal(new Set(Object.values(plan.points).map(/** Recupera declaração original dos dois pontos. */
      function declaration(point) { return point.declaration_id; })).size, 1);
    assert.ok(Object.values(plan.declarations).some(/** Confirma que o censo não se restringe às testemunhas positivas. */
      function unassociated(declaration) { return declaration.file === 'target.test.ts' && !declaration.associated; }));
  });

test('declarações na mesma linha sem coluna publicada permanecem pendentes',
  /** Não escolhe entre callbacks de mesmo intervalo/título pela semelhança do nome. */
  function ambiguousDeclaration() {
    const input = verifiedInput({
      'target.ts': '/** Soma uma unidade. */ export function compute(value:number){return value+1;}',
      'target.test.ts': "import {test} from 'vitest';import {compute} from './target';\n"
        + "test('igual',/** Primeiro corpo. */function first(){compute(1);});test('igual',/** Segundo corpo. */function second(){compute(2);});",
    });
    const plan = buildPlan(input);
    assert.ok(plan.limitations.length > 0);
    assert.equal(Object.keys(plan.points).length, 0);
  });

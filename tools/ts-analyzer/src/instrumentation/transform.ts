/** Text edits on an immutable snapshot; no printer rewrites the original source. */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { Node, SyntaxKind, ts } from 'ts-morph';
import { discoverCalls } from '../calls.js';
import { discoverTargets } from '../targets.js';
import { SnapshotProgram, sourcePath, isTestPath } from '../project.js';
import { Issue, Target, TestCall } from '../protocol.js';

export const RUNTIME_NAME = '__robo_t11_runtime';
export const sha256 = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex');
export interface Edit { start: number; end: number; text: string; overlay_start: number; overlay_end: number }
export interface OverlayFile {
  path: string; original_sha256: string; overlay_sha256: string; source: string; edits: Edit[];
  transpilation: {typescript_version: string; input_sha256: string; javascript: string; source_map: string};
}
export interface Declaration {
  file: string; original_position: number; overlay_start: number; overlay_end: number; parameterized: boolean;
}
export interface Instrumentation {
  schema_version: '1.0'; purpose: 'observational_supplement'; status: 'ready' | 'not_evaluated';
  framework: 'jest' | 'vitest'; framework_version: string; typescript_version: string;
  files: OverlayFile[]; declarations: Declaration[]; targets: Target[];
  callsites: (TestCall & {id: string})[]; limitations: Issue[];
}
interface PendingEdit {start: number; end: number; text: string}
const q = JSON.stringify;
function prologueEnd(block: Node & {getStatements(): Node[]}): number {
  let end = block.getStart() + (Node.isBlock(block) ? 1 : 0);
  for (const statement of block.getStatements()) {
    if (!Node.isExpressionStatement(statement) || !Node.isStringLiteral(statement.getExpression())) break;
    end = statement.getEnd();
  }
  return end;
}
function caseCall(node: Node): {parameterized: boolean; concurrent: boolean} | undefined {
  if (!Node.isCallExpression(node)) return;
  const title = node.getArguments()[0];
  if (!title || !(Node.isStringLiteral(title) || Node.isNoSubstitutionTemplateLiteral(title))) return;
  let expression: Node = node.getExpression();
  const modifiers: string[] = [];
  let parameterized = false;
  while (Node.isPropertyAccessExpression(expression) || Node.isCallExpression(expression) || Node.isTaggedTemplateExpression(expression)) {
    if (Node.isPropertyAccessExpression(expression)) { modifiers.push(expression.getName()); expression = expression.getExpression(); }
    else if (Node.isCallExpression(expression)) { parameterized = true; expression = expression.getExpression(); }
    else { parameterized = true; expression = expression.getTag(); }
  }
  if (!Node.isIdentifier(expression) || !['test', 'it'].includes(expression.getText())) return;
  return {parameterized, concurrent: modifiers.includes('concurrent')};
}
function offsetAfter(edits: Edit[], original: number): number {
  let shift = 0;
  for (const e of edits) if (e.end <= original) shift += e.text.length - (e.end - e.start);
  return original + shift;
}
export function instrument(programs: SnapshotProgram[], framework: 'jest' | 'vitest', version: string): Instrumentation {
  const targets = discoverTargets(programs).targets;
  const calls = discoverCalls(programs, targets, framework).test_calls;
  const result: Instrumentation = {schema_version: '1.0', purpose: 'observational_supplement', status: 'ready',
    framework, framework_version: version, typescript_version: ts.version, files: [], declarations: [], targets,
    callsites: [], limitations: []};
  const unsupported = (message: string, path?: string) => result.limitations.push({code: 'unsupported_instrumentation', message, ...(path ? {path} : {})});
  if (!(framework === 'jest' ? ['29.7.0', '30.0.0'] : ['3.2.4', '4.0.0']).includes(version)) unsupported('Exact framework patch is not supported');
  const files = new Map(programs.flatMap(p => p.sourceFiles.map(f => [sourcePath(f), f] as const)));
  const edits = new Map<string, PendingEdit[]>();
  const add = (path: string, start: number, end: number, text: string) => {
    const list = edits.get(path) ?? []; list.push({start, end, text}); edits.set(path, list);
  };
  const declarations: {file: string; position: number; end: number; parameterized: boolean}[] = [];
  for (const [path, file] of files) {
    if (file.getDescendantsOfKind(SyntaxKind.Identifier).some(n => n.getText().startsWith('__robo_'))) unsupported('Reserved probe binding collision', path);
    if (!isTestPath(path)) continue;
    // Inherited/concurrent modes are rejected before producing any overlay.
    if (file.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression).some(n => n.getName() === 'concurrent')) unsupported('Concurrent contexts require a separate tested mode', path);
    for (const call of file.getDescendantsOfKind(SyntaxKind.CallExpression)) {
      const info = caseCall(call);
      if (!info) continue;
      const callback = call.getArguments()[1];
      if (callback && !(Node.isArrowFunction(callback) || Node.isFunctionExpression(callback))) { unsupported('Named case callbacks are not supported', path); continue; }
      if (callback && (Node.isArrowFunction(callback) || Node.isFunctionExpression(callback)) && !info.parameterized && callback.getParameters().length) unsupported('Callback/done mode is not supported', path);
      declarations.push({file: path, position: call.getStart(), end: call.getExpression().getEnd(), parameterized: info.parameterized});
    }
  }
  for (const target of targets) {
    const file = files.get(target.path);
    const node = file?.getDescendants().find(n => n.getStart() === target.start_offset && (Node.isFunctionDeclaration(n) || Node.isMethodDeclaration(n)));
    if (!node || !(Node.isFunctionDeclaration(node) || Node.isMethodDeclaration(node))) { unsupported('Target AST unavailable', target.path); continue; }
    const body = node.getBody();
    if (!body || !Node.isBlock(body)) { unsupported('Target body unavailable', target.path); continue; }
    if (node.getAsteriskToken() || (Node.isMethodDeclaration(node) && node.getDecorators().length)) { unsupported('Generators/decorated targets are unsupported', target.path); continue; }
    const identity = '__robo_original_' + target.start_offset;
    let capture: string;
    let captureAt: number;
    if (Node.isFunctionDeclaration(node)) {
      const name = node.getName();
      if (!name || !Node.isSourceFile(node.getParent())) { unsupported('Only named module-level functions are supported', target.path); continue; }
      capture = name; captureAt = prologueEnd(file!);
    } else {
      const owner = node.getParent();
      if (!Node.isClassDeclaration(owner) || !owner.getName() || !Node.isIdentifier(node.getNameNode()) || node.hasModifier(SyntaxKind.PrivateKeyword)) { unsupported('Only named public class methods are supported', target.path); continue; }
      if (owner.getDecorators().length || owner.getStaticBlocks().length || owner.getProperties().some(p => p.isStatic() && p.getInitializer())) {
        unsupported('Class decorators/static initialization can replace original method identities', target.path); continue;
      }
      capture = `${owner.getName()}${node.isStatic() ? '' : '.prototype'}.${node.getName()}`;
      captureAt = owner.getEnd();
    }
    add(target.path, captureAt, captureAt, `\nconst ${identity} = ${capture};\n`);
    add(target.path, prologueEnd(body), prologueEnd(body), `\n${RUNTIME_NAME}.entry(${q(target.id)}, ${identity});\n`);
  }
  for (const call of calls.filter(c => c.link_kind === 'direct')) {
    const file = files.get(call.callsite_path)!;
    const node = file.getDescendantsOfKind(SyntaxKind.CallExpression).find(n => n.getStart() === call.start_offset && n.getEnd() === call.end_offset)!;
    if (!node) { unsupported('Call AST unavailable', call.callsite_path); continue; }
    if (node.hasQuestionDotToken() || node.getDescendants().some(n => [SyntaxKind.AwaitExpression, SyntaxKind.YieldExpression, SyntaxKind.SuperKeyword, SyntaxKind.SpreadElement, SyntaxKind.QuestionDotToken].includes(n.getKind()))) {
      unsupported('Optional/spread/await/yield/super invocation requires a separate transform', call.callsite_path); continue;
    }
    const id = sha256(JSON.stringify([call.callsite_path, call.start_offset, call.symbol_target_id]));
    const expression = node.getExpression();
    if (Node.isIdentifier(expression)) {
      add(call.callsite_path, expression.getStart(), expression.getStart(), `${RUNTIME_NAME}.fn(`);
      add(call.callsite_path, expression.getEnd(), expression.getEnd(), `,${q(id)},${q(call.symbol_target_id)})`);
    } else if (Node.isPropertyAccessExpression(expression) && Node.isIdentifier(expression.getNameNode())) {
      const receiver = expression.getExpression();
      add(call.callsite_path, receiver.getStart(), receiver.getStart(), `${RUNTIME_NAME}.method(`);
      add(call.callsite_path, receiver.getEnd(), expression.getEnd(), `,${q(expression.getName())},${q(id)},${q(call.symbol_target_id)})`);
    } else { unsupported('Unsupported callee syntax', call.callsite_path); continue; }
    result.callsites.push({...call, id});
  }
  if (result.limitations.length) { result.status = 'not_evaluated'; result.callsites = []; return result; }
  for (const [path, pending] of edits) {
    const file = files.get(path)!;
    const original = programs.find(p => p.texts.has(path))!.texts.get(path)!;
    const importAt = prologueEnd(file);
    pending.unshift({start: importAt, end: importAt, text: `\nimport { runtime as ${RUNTIME_NAME} } from '/opt/robo-reporters/runtime.cjs';\n`});
    pending.sort((a,b) => a.start - b.start || a.end - b.end);
    const mapped: Edit[] = [];
    let source = '', cursor = 0;
    for (const edit of pending) {
      if (edit.start < cursor) throw Error('Overlapping instrumentation edits');
      source += original.slice(cursor, edit.start);
      mapped.push({...edit, overlay_start: source.length, overlay_end: source.length + edit.text.length});
      source += edit.text; cursor = edit.end;
    }
    source += original.slice(cursor);
    const emitted = ts.transpileModule(source, {fileName: path, compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: framework === 'jest' ? ts.ModuleKind.CommonJS : ts.ModuleKind.ESNext,
      sourceMap: true, inlineSources: true,
    }});
    result.files.push({path, original_sha256: sha256(original), overlay_sha256: sha256(source), source, edits: mapped,
      transpilation: {typescript_version: ts.version, input_sha256: sha256(source), javascript: emitted.outputText, source_map: emitted.sourceMapText!}});
  }
  for (const declaration of declarations) {
    const file = result.files.find(f => f.path === declaration.file);
    result.declarations.push({file: declaration.file, original_position: declaration.position,
      overlay_start: file ? offsetAfter(file.edits, declaration.position) : declaration.position,
      overlay_end: file ? offsetAfter(file.edits, declaration.end) : declaration.end, parameterized: declaration.parameterized});
  }
  return result;
}

export function runtimeSupplement(): string {
  // Transpile the built analyzer runtime, never any project module, to portable CJS.
  const source = readFileSync(new URL('./runtime.js', import.meta.url), 'utf8');
  return ts.transpileModule(source, {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}}).outputText;
}

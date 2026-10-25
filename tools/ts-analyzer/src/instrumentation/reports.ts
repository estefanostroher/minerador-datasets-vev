/** Restore only source-verified declaration coordinates before the T10 normalizer. */
import ts from 'typescript';
import { Instrumentation, sha256 } from './transform.js';

export function restoreReport(raw: any, sources: Record<string, string>, manifest: Instrumentation): any {
  if (manifest.status !== 'ready' || manifest.framework !== raw.framework || manifest.framework_version !== raw.version) throw Error('overlay mode mismatch');
  const copy = structuredClone(raw);
  function restore(file: string, item: any): void {
    const overlay = manifest.files.find(f => f.path === file);
    if (!overlay || sources[file] === undefined || sha256(sources[file]) !== overlay.original_sha256 ||
        sha256(overlay.source) !== overlay.overlay_sha256 || raw.source_hashes?.[file] !== overlay.overlay_sha256) throw Error('overlay source hash mismatch');
    copy.source_hashes[file] = overlay.original_sha256;
    const source = ts.createSourceFile(file, overlay.source, ts.ScriptTarget.Latest, true);
    const starts = source.getLineStarts();
    const location = item.location;
    if (!location || location.line < 1 || location.line > starts.length || location.column < 1) { delete item.location; return; }
    const offset = starts[location.line - 1] + location.column - 1;
    const found = manifest.declarations.filter(d => d.file === file && offset >= d.overlay_start && offset <= d.overlay_end);
    if (found.length !== 1) { delete item.location; return; }
    const original = ts.createSourceFile(file, sources[file], ts.ScriptTarget.Latest, true);
    const restored = original.getLineAndCharacterOfPosition(found[0].original_position);
    item.location = {line: restored.line + 1, column: restored.character + 1};
  }
  if (raw.framework === 'jest') {
    for (const suite of copy.results.testResults) for (const item of suite.testResults) restore(suite.testFilePath.slice('/workspace/'.length), item);
  } else {
    function visit(tasks: any[], file: string): void {
      for (const task of tasks) {
        if (task.type === 'suite') visit(task.tasks, file);
        else if (task.type === 'test') restore(file, task);
      }
    }
    for (const file of copy.files) visit(file.tasks, file.filepath.slice('/workspace/'.length));
  }
  return copy;
}

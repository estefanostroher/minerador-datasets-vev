import { beforeEach } from '/workspace/node_modules/vitest/dist/index.js';
import { getFn, setFn } from '/workspace/node_modules/@vitest/runner/dist/index.js';
import runtimeModule from './runtime.cjs';
import adapter from './adapter.cjs';
const seen = new WeakSet();
beforeEach(context => {
  const root = context.task.file;
  if (seen.has(root)) return;
  seen.add(root);
  const file = root.filepath.slice('/workspace/'.length);
  const counts = new Map();
  function visit(tasks) {
    for (const task of tasks) {
      if (task.type === 'suite') { visit(task.tasks); continue; }
      const declaration = adapter.locate(file, task.location);
      if (!declaration) continue;
      const index = counts.get(declaration.original_position) ?? 0;
      counts.set(declaration.original_position, index + 1);
      const parameter = declaration.parameterized ? index : null;
      adapter.collect(runtimeModule.runtime, file, declaration, task.id, parameter);
      const fn = getFn(task);
      if (fn) setFn(task, adapter.wrap(runtimeModule.runtime, fn, file, declaration, task.id, parameter));
    }
  }
  visit(root.tasks);
});

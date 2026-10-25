const { TestEnvironment } = require('/workspace/node_modules/jest-environment-node');
const adapter = require('./adapter.cjs');
const fs = require('node:fs');
module.exports = class TraceEnvironment extends TestEnvironment {
  constructor(config, context) { super(config, context); this.testPath = context.testPath; }
  async handleTestEvent(event, state) {
    if (event.name !== 'run_start') return;
    const file = this.testPath.slice('/workspace/'.length);
    const runtime = this.global.__robo_t11_api;
    const counts = new Map();
    function visit(block) {
      for (const child of block.children) {
        if (child.type === 'describeBlock') { visit(child); continue; }
        // circus stores the registration stack for each *collected* row, including
        // skipped/todo rows. IDs therefore do not depend on callbacks executed.
        const locations = adapter.stackLocations(file, child.asyncError?.stack ?? '');
        const declarations = locations.map(loc => adapter.locate(file, loc)).filter(Boolean);
        const declaration = declarations[0];
        fs.mkdirSync('/workspace/.robo-reports', {recursive: true});
        fs.appendFileSync('/workspace/.robo-reports/collection.jsonl', JSON.stringify({
          file, stack: String(child.asyncError?.stack ?? ''), locations, declaration,
        }) + '\n');
        if (!declaration || declarations.some(d => d.original_position !== declaration.original_position)) continue;
        const index = counts.get(declaration.original_position) ?? 0;
        counts.set(declaration.original_position, index + 1);
        const parameter = declaration.parameterized ? index : null;
        const id = String(index);
        adapter.collect(runtime, file, declaration, id, parameter);
        if (child.fn) child.fn = adapter.wrap(runtime, child.fn, file, declaration, id, parameter);
      }
    }
    visit(state.rootDescribeBlock);
  }
};

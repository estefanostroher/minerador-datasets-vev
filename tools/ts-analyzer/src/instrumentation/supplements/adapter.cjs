// Used inside framework workers only. Coordinates refer to the overlay TS input.
const fs = require('node:fs');
const manifest = require('./manifest.cjs');
const sources = new Map();
exports.stackLocations = (file, stack) => String(stack).split('\n').flatMap(line => {
  const match = line.match(/(?:\(|\s)(\/workspace\/[^()]+):(\d+):(\d+)\)?$/);
  return match && match[1] === '/workspace/' + file ? [{line: Number(match[2]), column: Number(match[3])}] : [];
});
exports.locate = (file, location) => {
  if (!location || !Number.isInteger(location.line) || !Number.isInteger(location.column)) return;
  if (!sources.has(file)) sources.set(file, fs.readFileSync('/workspace/' + file, 'utf8'));
  const text = sources.get(file);
  const lines = text.split('\n');
  if (location.line < 1 || location.line > lines.length || location.column < 1) return;
  const offset = lines.slice(0, location.line - 1).reduce((n, line) => n + line.length + 1, 0) + location.column - 1;
  const matches = manifest.declarations.filter(d => d.file === file && offset >= d.overlay_start && offset <= d.overlay_end);
  return matches.length === 1 ? matches[0] : undefined;
};
exports.collect = (runtime, file, declaration, id, parameter) => runtime.collected(file, declaration.original_position, id, parameter);
exports.wrap = (runtime, fn, file, declaration, id, parameter) => function(...args) {
  return runtime.runCase(file, declaration.original_position, id, parameter, () => Reflect.apply(fn, this, args));
};

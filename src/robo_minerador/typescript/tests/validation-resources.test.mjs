import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createValidationPlugin } from '../validation-vitest.mjs';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import { once } from 'node:events';
import { observeNativeResources } from '../validation-resource-runtime.mjs';

test('censo de recursos inclui setup sem sonda e mantém bytes originais', () => {
  const modules = [];
  const plugin = createValidationPlugin([], '/workspace', () => {}, () => {}, value => modules.push(value));
  const source = "export const setup='fixture';\n";
  assert.equal(plugin.transform(source, '/workspace/setup.ts'), null);
  assert.deepEqual(modules, [{ path: 'setup.ts', sha256: createHash('sha256').update(source).digest('hex'), origin: 'vite_transform' }]);
  plugin.transform(source, '/workspace/node_modules/dependency/index.js');
  assert.equal(modules.length, 1);
});

test('evento nativo confirma requisição alcançada sem substituir ClientRequest', async () => {
  const events = [];
  const restore = observeNativeResources(fileURLToPath(new URL('.', import.meta.url)), value => events.push(value));
  const server = http.createServer((request, response) => response.end('fixture'));
  try {
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const url = `http://127.0.0.1:${server.address().port}/fixture`;
    const request = http.get(url);
    assert.ok(request instanceof http.ClientRequest);
    const [response] = await once(request, 'response');
    response.resume();
    await once(response, 'end');
    assert.equal(events.length, 1);
    assert.equal(events[0].url, url);
    assert.equal(events[0].api, 'node:http.get');
    assert.equal(events[0].path, 'validation-resources.test.mjs');
    assert.equal(events[0].sha256, createHash('sha256').update(fs.readFileSync(fileURLToPath(import.meta.url))).digest('hex'));
    assert.ok(events[0].line > 0 && events[0].column > 0);
  } finally {
    restore();
    server.close();
    await once(server, 'close');
  }
});

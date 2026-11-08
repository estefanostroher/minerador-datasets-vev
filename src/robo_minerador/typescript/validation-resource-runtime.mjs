import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { syncBuiltinESMExports } from 'node:module';
import http from 'node:http';
import https from 'node:https';

/** Observa get nativo preservando receptor, argumentos, retorno e exceções originais. */
export function observeNativeResources(root, record) {
  const originals = [];
  for (const [specifier, client] of [['node:http', http], ['node:https', https]]) {
    const original = client.get;
    originals.push([client, original]);
    /** Só emite prova após a implementação nativa construir a requisição real. */
    function observedGet(...args) {
      const error = {};
      Error.captureStackTrace(error, observedGet);
      const result = Reflect.apply(original, this, args);
      if (typeof args[0] === 'string') {
        for (const frame of String(error.stack).split('\n')) {
          const match = frame.match(/\(?((?:file:\/\/\/|\/|[A-Za-z]:[\\/])[^()\n]+):(\d+):(\d+)\)?$/);
          if (!match) continue;
          const file = match[1].startsWith('file:') ? fileURLToPath(match[1]) : match[1];
          const relative = path.relative(root, file).replaceAll('\\', '/');
          if (relative.startsWith('../') || path.isAbsolute(relative) || relative.split('/').includes('node_modules') || relative.startsWith('.robo-validation-')) continue;
          const sha256 = createHash('sha256').update(fs.readFileSync(file)).digest('hex');
          record({ path: relative, sha256, api: specifier + '.get', url: new URL(args[0]).href,
            line: Number(match[2]), column: Number(match[3]), origin: 'native_get' });
          break;
        }
      }
      return result;
    }
    client.get = observedGet;
  }
  syncBuiltinESMExports();
  /** Permite à fixture devolver os módulos nativos ao estado anterior sem nova requisição. */
  return function restore() {
    for (const [client, original] of originals) client.get = original;
    syncBuiltinESMExports();
  };
}

import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

/** Lê apenas configuração do instrumento em montagem confiável readonly. */
export function settings() {
  const value = JSON.parse(fs.readFileSync(new URL('./settings.json', import.meta.url), 'utf8'));
  return value;
}

/** Obtém diretório exclusivo da passagem, recusando escape ou ligação simbólica. */
export function channelDirectory() {
  const config = settings();
  const directory = process.env.ROBO_VALIDATION_CHANNEL;
  if (!directory || !directory.startsWith(config.output_root + '/')
      || fs.realpathSync(directory) !== directory) throw new Error('Canal de evidência inválido.');
  return directory;
}

/** Usa nome determinístico seguro, inclusive para IDs com caracteres de separador. */
export function recordPath(kind, id, extension = 'json') {
  return path.join(channelDirectory(), kind + '-' + createHash('sha256').update(id).digest('hex') + '.' + extension);
}

/** Publica registro completo atomicamente; falhas de escrita nunca são ignoradas. */
export function writeRecord(kind, id, value) {
  const file = recordPath(kind, id);
  const temporary = file + '.' + process.pid + '.tmp';
  fs.writeFileSync(temporary, JSON.stringify(value));
  fs.renameSync(temporary, file);
}

/** Persiste um evento por write síncrono em stream exclusivo do emissor. */
export function writeEvent(event) {
  fs.appendFileSync(recordPath('events', event.emitter_id, 'jsonl'), JSON.stringify(event) + '\n');
}

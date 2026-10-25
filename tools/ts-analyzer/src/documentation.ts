import { posix } from 'node:path';
import { fromMarkdown } from 'mdast-util-from-markdown';
import type { Nodes, Link } from 'mdast';
import { Node, ts } from 'ts-morph';
import { Documentation, DocumentMarker, DocumentSource, Target } from './protocol.js';
import { safeRelative, SnapshotProgram } from './project.js';
import { targetNode } from './metrics.js';

function markers(text: string, start: number): DocumentMarker[] {
  const found: DocumentMarker[] = [];
  const patterns: [DocumentMarker['kind'], RegExp][] = [
    ['inputs', /@param\b|\b(?:input|entrada|parameters?|parâmetros?)\b/i],
    ['outputs', /@returns?\b|\b(?:returns?|retorna|output|saída)\b/i],
    ['constraints', /\b(?:must|requires?|only|deve|restriç|requer|somente|apenas)\b/i],
    ['errors', /@throws?\b|\b(?:throws?|errors?|erros?|lança|exceptions?)\b/i],
  ];
  for (const match of text.matchAll(/[^\r\n\u2028\u2029]+/g)) {
    for (const [kind, pattern] of patterns) if (pattern.test(match[0])) {
      const offset = start + match.index;
      found.push({ kind, text: match[0], start_offset: offset, end_offset: offset + match[0].length });
    }
  }
  return found;
}
function links(node: Nodes): Link[] {
  if (node.type === 'link') return [node];
  return 'children' in node ? node.children.flatMap(child => links(child)) : [];
}
function bound(link: Link, path: string, target: Target): boolean {
  // No URL fetching, host reads or fragment guessing. Only local path#qualified_name.
  const hash = link.url.indexOf('#');
  if (hash < 1) return false;
  let destination: string, symbol: string;
  try { destination = decodeURIComponent(link.url.slice(0, hash)); symbol = decodeURIComponent(link.url.slice(hash + 1)); }
  catch { return false; }
  if (/[\0\\:?]/.test(destination) || destination.startsWith('/') || symbol !== target.qualified_name) return false;
  const resolved = posix.normalize(posix.join(posix.dirname(path), destination));
  return safeRelative(resolved) && resolved === target.path;
}
function markdownSources(texts: ReadonlyMap<string, string>, target: Target): DocumentSource[] {
  const sources: DocumentSource[] = [];
  for (const [path, text] of texts) {
    if (!/\.(md|markdown)$/.test(path)) continue;
    const children = fromMarkdown(text).children;
    for (let i = 0; i < children.length; i++) {
      const heading = children[i];
      if (heading.type !== 'heading' || !links(heading).some(link => bound(link, path, target))) continue;
      let end = text.length;
      for (const next of children.slice(i + 1)) {
        if (next.type === 'heading') { end = next.position!.start.offset!; break; }
      }
      const start = heading.position!.start.offset!;
      const original = text.slice(start, end);
      sources.push({ path, start_offset: start, end_offset: end, text: original, association: 'markdown_path_symbol', symbol: target.qualified_name, markers: markers(original, start) });
    }
  }
  return sources;
}
function description(source: DocumentSource): string {
  // A temporary classification view only. Stored evidence always uses original text.
  if (source.association === 'markdown_path_symbol') {
    const prose = (node: Nodes): string => {
      if (node.type === 'text') return node.value;
      if (['heading', 'code', 'inlineCode', 'html', 'image'].includes(node.type)) return '';
      return 'children' in node ? node.children.map(prose).join(' ') : '';
    };
    return prose(fromMarkdown(source.text)).trim();
  }
  return source.text.replace(/^\/\*\*|\*\/$/g, '').split(/\r\n|\r|\n/)
    .map(line => line.replace(/^\s*\* ?/, '').trim()).join('\n').split(/(?:^|\n)\s*@/)[0].trim();
}
function behavioral(text: string): boolean {
  // Explicit action with a non-type object. Generic prose remains unresolved.
  const match = /\b(?:returns?|computes?|calculates?|converts?|validates?|rejects?|throws?|normalizes?|removes?|adds?|increments?|sorts?|filters?|retorna|calcula|converte|valida|rejeita|lança|normaliza|remove|adiciona|incrementa|ordena|filtra)\s+(.+)/i.exec(text);
  if (!match) return false;
  const object = match[1].replace(/[.!?;:\s]+$/, '').trim();
  const typeCandidate = object.replace(/^(?:a|an|the|um|uma|uns|umas|o|os|as)\s+/i, '');
  // Parse only a possible TS type, never natural-language semantics. Require the
  // entire complement to fit one alias so descriptive prose is not discarded.
  const prefix = 'type DocumentationType = ';
  const parsed = ts.createSourceFile('documentation-type.ts', prefix + typeCandidate + ';', ts.ScriptTarget.Latest, false, ts.ScriptKind.TS);
  const alias = parsed.statements[0];
  if (parsed.statements.length === 1 && alias && ts.isTypeAliasDeclaration(alias) && alias.type.getEnd() === prefix.length + typeCandidate.length) return false;
  if (/^\{.*\}$|^[\w$]+(?:<.*>)?(?:\[\])?(?:\s*[|&]\s*[\w$]+(?:\[\])?)*$/.test(object)) return false;
  return !/^(?:(?:a|an|the|um|uma)\s+)?(?:string|number|boolean|object|array|void|undefined|null|integer|value|result|valor|resultado)(?:\[\])?$/i.test(object) && /[\p{L}]/u.test(object);
}
export function discoverDocumentation(programs: SnapshotProgram[], targets: Target[]): Documentation[] {
  return targets.map(target => {
    const node = targetNode(programs, target);
    const sources: DocumentSource[] = [];
    if (node && (Node.isFunctionDeclaration(node) || Node.isMethodDeclaration(node))) {
      const declarations = [node, ...node.getOverloads()];
      for (const declaration of declarations) for (const doc of declaration.getJsDocs()) {
        // ts-morph association only; never search unrelated preceding comments.
        const text = doc.getSourceFile().getFullText().slice(doc.getStart(), doc.getEnd());
        sources.push({ path: target.path, start_offset: doc.getStart(), end_offset: doc.getEnd(), text, association: declaration === node ? 'adjacent_jsdoc' : 'overload_jsdoc', symbol: target.qualified_name, markers: markers(text, doc.getStart()) });
      }
    }
    const texts = new Map<string, string>();
    for (const program of programs) for (const [path, text] of program.texts) texts.set(path, text);
    sources.push(...markdownSources(texts, target));
    const descriptions = sources.map(description);
    const approved = descriptions.some(behavioral);
    const freeText = descriptions.some(text => text.length > 0);
    return {
      symbol_target_id: target.id, sources,
      status: approved ? 'approved' : freeText ? 'not_evaluated' : 'rejected',
      reason: approved ? 'behavioral_description' : freeText ? 'documentation_behavior_unresolved' : sources.length ? 'documentation_signature_only' : 'documentation_absent',
      evidence: sources.map(source => `${source.path}:${source.start_offset}:${source.end_offset}`),
    };
  });
}

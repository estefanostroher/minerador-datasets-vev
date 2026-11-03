import ts from 'typescript';
import path from 'node:path';
import { declarationName, className } from './virtual-project.mjs';

/** Distingue descrição textual de tags exclusivamente tipadas e assinaturas. */
function descriptive(text, symbolName) {
  const prose = text.replace(/```[\s\S]*?```/g, '').replace(/^\s*\/\*+|\*\/\s*$/g, '').split('\n').map(/** Remove decoração e identidade de links, preservando prosa e descrições comportamentais das tags. */ line => {
    if (/^\s*\/\/\/\s*</.test(line)) return '';
    line = line.replace(/^\s*#{1,6}\s+/, '').replace(/\s+#+\s*$/, '').replace(/\[[^\]]*\]\([^)]*\)/g, '').replace(/^\s*(?:\*|\/\/)?\s?/, '').trim();
    if (line === symbolName) return '';
    if (/^(?:eslint-|prettier-|biome-ignore|istanbul ignore|c8 ignore)/.test(line)) return '';
    if (/^@param\b/.test(line)) return line.replace(/^@param\s+(?:\{[^}]*\}\s*)?\S+\s*/, '');
    if (/^@(returns?|throws|exception)\b/.test(line)) return line.replace(/^@(returns?|throws|exception)\s*(?:\{[^}]*\})?\s*/, '');
    return line.startsWith('@') ? '' : line;
  }).join(' ').trim();
  return /\p{L}/u.test(prose) && prose !== symbolName && !/^\s*(?:(?:export\s+)?(?:function|class)\s|[\w$]+\s*\([^)]*\)\s*(?::|$))/.test(prose);
}

/** Confere fragmentos de símbolo, classe ou linha contra uma declaração concreta. */
function anchorMatches(anchor, node) {
  if (!anchor) return true;
  const name = declarationName(node);
  const owner = ts.isMethodDeclaration(node) ? className(node.parent) : null;
  if (anchor === name || owner && [`${owner}.${name}`, `${owner}::${name}`, `${owner}#${name}`].includes(anchor)) return true;
  const line = /^L(\d+)(?:-L?\d+)?$/.exec(anchor);
  if (line) {
    const source = node.getSourceFile();
    return Number(line[1]) >= source.getLineAndCharacterOfPosition(node.getStart()).line + 1 && Number(line[1]) <= source.getLineAndCharacterOfPosition(node.getEnd()).line + 1;
  }
  return false;
}

/** Preserva somente a seção textual indicada por um link de origem. */
function linkedSection(content, anchor, context) {
  const lines = content.split('\n');
  if (!anchor) return sectionAt(lines, 0, context, true);
  for (let index = 0; index < lines.length; index++) {
    const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(lines[index]);
    if (!heading) continue;
    const slug = heading[2].toLowerCase().replace(/[^\p{L}\p{N}_\-\s]/gu, '').replace(/\s/g, '-');
    if (slug === anchor) return sectionAt(lines, index, context);
  }
  return null;
}

/** Exige fronteiras de caminho e símbolo para não aceitar referências por prefixo ou sufixo. */
function exactReference(text, reference) {
  const escaped = reference.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![\\p{L}\\p{N}_$./\\\\:@%#~+?-])${escaped}(?![\\p{L}\\p{N}_$])`, 'u').test(text);
}

/** Determina titularidade de cabeçalhos vinculados sem inferir associação por nomes parecidos. */
function headingOwnership(line, { docPath, node, project, units }) {
  if (!/^#{1,6}\s/.test(line)) return null;
  const links = [...line.matchAll(/\]\(([^)]+)\)/g)];
  let ownership = null;
  for (const link of links) {
    const separator = link[1].indexOf('#');
    const target = separator < 0 ? link[1] : link[1].slice(0, separator);
    const anchor = separator < 0 ? null : link[1].slice(separator + 1);
    const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(docPath), target));
    const matching = units.filter(/** Resolve identidade do símbolo explicitamente vinculado pelo cabeçalho. */ unit => {
      const unitPath = project.relative(unit.getSourceFile().fileName);
      return (unitPath === resolved || unitPath === target) && anchorMatches(anchor, unit);
    });
    if (matching.length !== 1) return 'ambiguous';
    const current = matching[0] === node ? 'same' : 'other';
    if (ownership && ownership !== current) return 'ambiguous';
    ownership = current;
  }
  return ownership;
}

/** Delimita prosa própria da seção, excluindo subseções vinculadas a outros símbolos. */
function sectionAt(lines, index, context, wholeDocument = false) {
  const heading = /^(#{1,6})\s/.exec(lines[index]);
  const initialOwner = headingOwnership(lines[index], context);
  if (initialOwner === 'ambiguous' || initialOwner === 'other') return { original_text: '', start_line: index + 1, end_line: index + 1, ambiguous: true };
  let end = index + 1;
  let ambiguous = false;
  if (heading || wholeDocument) {
    while (end < lines.length) {
      const next = /^(#{1,6})\s/.exec(lines[end]);
      if (!wholeDocument && next && next[1].length <= heading[1].length) break;
      const owner = headingOwnership(lines[end], context);
      if (owner === 'other' || owner === 'ambiguous') { ambiguous = owner === 'ambiguous'; break; }
      end++;
    }
  }
  return { original_text: lines.slice(index, end).join('\n'), start_line: index + 1, end_line: end, ambiguous };
}

/** Recupera documentação original anexada e vínculos externos explícitos. */
export function documentationFor(node, project, units) {
  const source = node.getSourceFile();
  const filePath = project.relative(source.fileName);
  const documentation = [];
  const name = declarationName(node);
  let ambiguous = false;
  const checker = project.programFor(filePath).getTypeChecker();
  const symbol = node.name && checker.getSymbolAtLocation(node.name);
  const declarations = symbol?.declarations ?? [node];
  const seen = new Set();
  for (const declaration of declarations) {
    const declarationSource = declaration.getSourceFile();
    const ranges = ts.getLeadingCommentRanges(declarationSource.text, declaration.getFullStart()) ?? [];
    for (const range of ranges) {
      const key = `${declarationSource.fileName}:${range.pos}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const text = declarationSource.text.slice(range.pos, range.end);
      if (descriptive(text, name)) documentation.push({ kind: 'attached_comment', path: project.relative(declarationSource.fileName), start_line: declarationSource.getLineAndCharacterOfPosition(range.pos).line + 1, end_line: declarationSource.getLineAndCharacterOfPosition(range.end).line + 1, original_text: text, association: { kind: 'attached' } });
      const links = [...text.matchAll(/(?:@see\s+|@link\s+|\]\()([^\s)}]+\.(?:markdown|mdx?|txt|rst)(?:#[^\s)}]+)?)(?=\*\/|[\s)}]|$)/gi)];
      for (const link of links) {
        const [target, anchor] = link[1].split('#');
        const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(filePath), target));
        const docPath = project.files.has(resolved) ? resolved : target;
        const content = project.files.get(docPath);
        if (content === undefined) { ambiguous = true; continue; }
        const linked = linkedSection(content, anchor, { docPath, node, project, units });
        if (!linked) { ambiguous = true; continue; }
        const { ambiguous: uncertain, ...section } = linked;
        ambiguous ||= uncertain;
        if (descriptive(section.original_text, name)) documentation.push({ kind: 'external', path: docPath, ...section, association: { kind: 'source_link', source_path: filePath } });
      }
    }
  }
  for (const [docPath, content] of project.files) {
    if (!/\.(markdown|md|mdx|txt|rst)$/i.test(docPath)) continue;
    const lines = content.split('\n');
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index];
      const links = [...line.matchAll(/\]\(([^)]+)\)/g)];
      let associated = false;
      for (const link of links) {
        const separator = link[1].indexOf('#');
        const target = separator < 0 ? link[1] : link[1].slice(0, separator);
        const anchor = separator < 0 ? null : link[1].slice(separator + 1);
        const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(docPath), target));
        if (resolved !== filePath && target !== filePath) continue;
        const matching = units.filter(/** Conta unidades que poderiam receber o vínculo. */ unit => project.relative(unit.getSourceFile().fileName) === filePath && anchorMatches(anchor, unit));
        if (anchor && !anchorMatches(anchor, node)) continue;
        if (matching.length !== 1) { ambiguous = true; continue; }
        associated = true;
      }
      const escaped = `${filePath}#${name}`;
      const explicit = exactReference(line, escaped) || exactReference(line, `${filePath}::${name}`);
      if (!associated && explicit && !links.length) {
        const matching = units.filter(/** Impede vincular métodos homônimos de classes distintas. */ unit => project.relative(unit.getSourceFile().fileName) === filePath && declarationName(unit) === name);
        if (matching.length === 1) associated = true;
        else ambiguous = true;
      }
      if (!associated && exactReference(line, filePath) && !links.length) ambiguous = true;
      if (associated) {
        const { ambiguous: uncertain, ...section } = sectionAt(lines, index, { docPath, node, project, units });
        ambiguous ||= uncertain;
        if (descriptive(section.original_text.replace(/\[[^\]]*\]\([^)]*\)/g, '').replaceAll(escaped, ''), name)) documentation.push({ kind: 'external', path: docPath, ...section, association: { kind: 'explicit_reference', target: escaped } });
      }
    }
  }
  return { documentation, ambiguous };
}

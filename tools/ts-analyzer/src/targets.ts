import { createHash } from 'node:crypto';
import { Node, SyntaxKind, FunctionDeclaration, MethodDeclaration } from 'ts-morph';
import { SnapshotProgram, sourcePath, isTestPath, isConfigPath } from './project.js';
import { Issue, Target } from './protocol.js';

export function staticId(path: string, start: number): string {
  return 'sha256:' + createHash('sha256').update(JSON.stringify(['1.0', path, start])).digest('hex');
}
export function implementationText(node: FunctionDeclaration | MethodDeclaration): string {
  if (!node.getBody()) throw new Error('target_without_implementation');
  return node.getSourceFile().getFullText().slice(node.getStart(), node.getEnd());
}
export function discoverTargets(programs: SnapshotProgram[]): { targets: Target[]; limitations: Issue[] } {
  const targets = new Map<string, Target>();
  const limitations: Issue[] = [];
  for (const program of programs) for (const file of program.sourceFiles) {
    const path = sourcePath(file);
    if (!/\.(?:ts|tsx|mts|cts)$/.test(path)) {
      limitations.push({
        code: 'javascript_context_only', path,
        message: 'Non-TypeScript source is context only and cannot be a TypeScript target',
      });
      continue;
    }
    if (file.isDeclarationFile() || isTestPath(path) || isConfigPath(path)) continue;
    for (const node of file.getDescendants()) {
      if (Node.isArrowFunction(node) || Node.isFunctionExpression(node) || Node.isConstructorDeclaration(node) || Node.isGetAccessorDeclaration(node) || Node.isSetAccessorDeclaration(node)) {
        limitations.push({ code: 'unsupported_declaration', message: 'Declaration is outside T08 target kinds', path });
      }
      if (!(Node.isFunctionDeclaration(node) || Node.isMethodDeclaration(node)) || !node.getBody()) continue;
      // Named helpers nested inside other functions are context, not independent units.
      if (node.getAncestors().some(n => Node.isFunctionLikeDeclaration(n))) continue;
      const owner = node.getParentIfKind(SyntaxKind.ClassDeclaration);
      if (Node.isMethodDeclaration(node) && (!owner || !owner.getName())) {
        limitations.push({ code: 'unsupported_declaration', message: 'Method requires a named class', path }); continue;
      }
      const name = node.getName();
      if (!name) { limitations.push({ code: 'unsupported_declaration', message: 'Anonymous declaration', path }); continue; }
      const start = node.getStart(), end = node.getEnd();
      const begin = file.compilerNode.getLineAndCharacterOfPosition(start);
      const finish = file.compilerNode.getLineAndCharacterOfPosition(end);
      const id = staticId(path, start);
      targets.set(id, { id, kind: Node.isMethodDeclaration(node) ? 'method' : 'function', qualified_name: owner ? owner.getName() + '.' + name : name, path, start_offset: start, end_offset: end, start_line: begin.line + 1, start_column: begin.character, end_line: finish.line + 1, end_column: finish.character, code: implementationText(node), signature: file.getFullText().slice(start, node.getBody()!.getStart()) });
    }
  }
  return { targets: [...targets.values()].sort((a,b) => a.path.localeCompare(b.path) || a.start_offset - b.start_offset), limitations };
}

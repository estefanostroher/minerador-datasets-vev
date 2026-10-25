import { Node, SyntaxKind } from 'ts-morph';
import { Decision, Metrics, Target } from './protocol.js';
import { SnapshotProgram } from './project.js';
import { assessTriviality } from './triviality.js';

function decisions(node: Node): Decision[] {
  const found: Decision[] = [];
  const body = Node.isBodyable(node) || Node.isBodied(node) ? node.getBody() : undefined;
  if (!body) return found;
  const visit = (child: Node): void => {
    if (Node.isFunctionLikeDeclaration(child) || Node.isClassDeclaration(child) || Node.isClassExpression(child)) return;
    let decision: Node | undefined;
    if (Node.isIfStatement(child) || Node.isForStatement(child) || Node.isForInStatement(child) || Node.isForOfStatement(child) || Node.isWhileStatement(child) || Node.isDoStatement(child) || Node.isCatchClause(child) || Node.isCaseClause(child) || Node.isConditionalExpression(child)) decision = child;
    if (Node.isBinaryExpression(child) && [SyntaxKind.AmpersandAmpersandToken, SyntaxKind.BarBarToken, SyntaxKind.QuestionQuestionToken].includes(child.getOperatorToken().getKind())) decision = child.getOperatorToken();
    if (decision) found.push({ kind: decision.getKindName(), start_offset: decision.getStart(), end_offset: decision.getEnd(), text: decision.getText() });
    child.forEachChild(visit);
  };
  visit(body);
  return found;
}
export function cyclomaticComplexity(node: Node): number { return 1 + decisions(node).length; }
export function measureTarget(node: Node, targetId: string): Metrics {
  const supported = Node.isFunctionLikeDeclaration(node) && (Node.isBodyable(node) || Node.isBodied(node)) && !!node.getBody();
  const measured = decisions(node);
  return {
    symbol_target_id: targetId, status: supported ? 'approved' : 'not_evaluated',
    reason: supported ? 'static_measurement' : 'unsupported_body', method: 'cyclomatic-ts-v1',
    cyclomatic_complexity: supported ? 1 + measured.length : null,
    physical_lines: node.getSourceFile().getFullText().slice(node.getStart(), node.getEnd()).split(/\r\n|\r|\n|\u2028|\u2029/).length,
    decisions: measured, triviality: assessTriviality(node),
  };
}
export function targetNode(programs: SnapshotProgram[], target: Target): Node | undefined {
  for (const program of programs) {
    const file = program.project.getSourceFile('/snapshot/' + target.path);
    const node = file?.getDescendants().find(n => n.getStart() === target.start_offset && n.getEnd() === target.end_offset && (Node.isFunctionDeclaration(n) || Node.isMethodDeclaration(n)));
    if (node) return node;
  }
  return undefined;
}
export function discoverMetrics(programs: SnapshotProgram[], targets: Target[]): Metrics[] {
  return targets.map(target => {
    const node = targetNode(programs, target);
    if (node) return measureTarget(node, target.id);
    return { symbol_target_id: target.id, status: 'not_evaluated', reason: 'target_unavailable', method: 'cyclomatic-ts-v1', cyclomatic_complexity: null, physical_lines: target.code.split(/\r\n|\r|\n|\u2028|\u2029/).length, decisions: [], triviality: { status: 'not_evaluated', reason: 'target_unavailable', evidence: [] } };
  });
}

import { Node, SyntaxKind } from 'ts-morph';
import { Evaluation } from './protocol.js';

// Rejection means the H-F06 non-triviality criterion failed, never target removal.
export function assessTriviality(node: Node): Evaluation {
  const result = (status: Evaluation['status'], reason: string, evidence: string[] = []): Evaluation => ({ status, reason, evidence });
  if (!Node.isFunctionLikeDeclaration(node) || !(Node.isBodyable(node) || Node.isBodied(node))) return result('not_evaluated', 'unsupported_shape');
  if ((Node.isAsyncable(node) && node.isAsync()) || (Node.isGeneratorable(node) && node.isGenerator())) return result('not_evaluated', 'execution_effects_unresolved');
  const body = node.getBody();
  if (!body || !Node.isBlock(body)) return result('not_evaluated', 'unsupported_shape');
  const parameters = node.getParameters();
  if (parameters.some(p => !Node.isIdentifier(p.getNameNode()) || p.getInitializer() || p.isRestParameter() || p.getDecorators().length)) {
    return result('not_evaluated', 'parameter_effects_unresolved');
  }
  const statements = body.getStatements();
  const statement = statements[0];
  const expression = statement && Node.isReturnStatement(statement) ? statement.getExpression() : undefined;
  const reject = (reason: string) => result('rejected', reason, [body.getText()]);
  if (statements.length === 1 && expression) {
    if (Node.isStringLiteral(expression) || Node.isNumericLiteral(expression) || Node.isNoSubstitutionTemplateLiteral(expression) || Node.isBigIntLiteral(expression) || [SyntaxKind.TrueKeyword, SyntaxKind.FalseKeyword, SyntaxKind.NullKeyword].includes(expression.getKind()) ||
        (Node.isPrefixUnaryExpression(expression) && [SyntaxKind.PlusToken, SyntaxKind.MinusToken].includes(expression.getOperatorToken()) && (Node.isNumericLiteral(expression.getOperand()) || Node.isBigIntLiteral(expression.getOperand())))) {
      return reject('constant_literal_return');
    }
    if (Node.isGetAccessorDeclaration(node) && Node.isPropertyAccessExpression(expression) && expression.getExpression().getKind() === SyntaxKind.ThisKeyword) return reject('simple_getter');
    if (Node.isCallExpression(expression) && !expression.getQuestionDotTokenNode()) {
      const callee = expression.getExpression();
      const simpleCallee = Node.isIdentifier(callee) || (Node.isPropertyAccessExpression(callee) && callee.getExpression().getKind() === SyntaxKind.ThisKeyword && !callee.getQuestionDotTokenNode());
      const name = Node.isFunctionDeclaration(node) || Node.isMethodDeclaration(node) ? node.getName() : undefined;
      if (name && (callee.getText() === name || callee.getText() === 'this.' + name)) return result('not_evaluated', 'recursive_call_unresolved');
      const names = parameters.map(p => p.getName());
      // Exact, ordered forwarding only. No evaluation, spread, defaults or access chains.
      if (simpleCallee && expression.getArguments().length === names.length && expression.getArguments().every((arg, i) => Node.isIdentifier(arg) && arg.getText() === names[i])) return reject('direct_delegation');
    }
  }
  if (Node.isSetAccessorDeclaration(node) && parameters.length === 1 && statements.length === 1 && Node.isExpressionStatement(statement)) {
    const assignment = statement.getExpression();
    if (Node.isBinaryExpression(assignment) && assignment.getOperatorToken().getKind() === SyntaxKind.EqualsToken) {
      const left = assignment.getLeft(), right = assignment.getRight();
      if (Node.isPropertyAccessExpression(left) && left.getExpression().getKind() === SyntaxKind.ThisKeyword && Node.isIdentifier(right) && right.getText() === parameters[0].getName()) return reject('simple_setter');
    }
  }
  // Positive evidence of logic, not a numerical threshold; opaque shapes stay unresolved.
  const logic: Node[] = [];
  body.forEachDescendant((child, traversal) => {
    if (Node.isFunctionLikeDeclaration(child) || Node.isClassDeclaration(child) || Node.isClassExpression(child)) { traversal.skip(); return; }
    if (Node.isIfStatement(child) || Node.isConditionalExpression(child) || Node.isThrowStatement(child) || Node.isForStatement(child) || Node.isWhileStatement(child) || Node.isSwitchStatement(child) ||
        (!Node.isGetAccessorDeclaration(node) && !Node.isSetAccessorDeclaration(node) && Node.isBinaryExpression(child))) logic.push(child);
  });
  if (logic.length && !(expression && Node.isCallExpression(expression) && statements.length === 1)) return result('approved', 'additional_logic', logic.map(n => n.getText()));
  return result('not_evaluated', 'triviality_shape_unresolved');
}

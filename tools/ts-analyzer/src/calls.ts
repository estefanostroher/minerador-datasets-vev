import { Node, CallExpression, Expression, SyntaxKind, Symbol as MorphSymbol } from 'ts-morph';
import { SnapshotProgram, sourcePath, isTestPath } from './project.js';
import { Issue, Target, TestCall } from './protocol.js';
import { staticId } from './targets.js';

interface FrameworkCall { kind: 'test' | 'it' | 'describe'; parameterized: boolean }
function frameworkBinding(expression: Expression, framework: 'jest' | 'vitest'): string | undefined {
  if (Node.isPropertyAccessExpression(expression)) {
    const receiver = expression.getExpression();
    const declaration = receiver.getSymbol()?.getDeclarations()[0];
    if (declaration && Node.isNamespaceImport(declaration)) {
      const module = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration)?.getModuleSpecifierValue();
      if (module === (framework === 'jest' ? '@jest/globals' : 'vitest')) return expression.getName();
    }
    return undefined;
  }
  if (!Node.isIdentifier(expression)) return undefined;
  const declarations = expression.getSymbol()?.getDeclarations() ?? [];
  const imported = declarations.find(Node.isImportSpecifier);
  if (imported) {
    const module = imported.getFirstAncestorByKind(SyntaxKind.ImportDeclaration)?.getModuleSpecifierValue();
    if (module === (framework === 'jest' ? '@jest/globals' : 'vitest')) return imported.getName();
    return undefined;
  }
  if (declarations.length === 0 || declarations.every(d => d.getSourceFile().isDeclarationFile() && /(?:@types\/jest|vitest)/.test(d.getSourceFile().getFilePath()))) return expression.getText();
  return undefined;
}
function frameworkCall(call: CallExpression, framework: 'jest' | 'vitest'): FrameworkCall | undefined {
  let expression = call.getExpression();
  let parameterized = false;
  if (Node.isCallExpression(expression)) {
    const factory = expression.getExpression();
    if (!Node.isPropertyAccessExpression(factory) || factory.getName() !== 'each') return undefined;
    parameterized = true;
    expression = factory.getExpression();
  } else if (Node.isTaggedTemplateExpression(expression)) {
    const factory = expression.getTag();
    if (!Node.isPropertyAccessExpression(factory) || factory.getName() !== 'each') return undefined;
    parameterized = true;
    expression = factory.getExpression();
  }
  const kind = frameworkBinding(expression, framework);
  return kind === 'test' || kind === 'it' || kind === 'describe' ? { kind, parameterized } : undefined;
}
function title(call: CallExpression): string | undefined {
  const argument = call.getArguments()[0];
  return argument && (Node.isStringLiteral(argument) || Node.isNoSubstitutionTemplateLiteral(argument)) ? argument.getLiteralText() : undefined;
}
function originalSymbol(symbol: MorphSymbol | undefined): MorphSymbol | undefined {
  const visited = new Set<MorphSymbol>();
  while (symbol?.isAlias() && !visited.has(symbol)) { visited.add(symbol); symbol = symbol.getAliasedSymbol(); }
  return symbol;
}
function resolutionPaths(expression: Expression): string[] {
  const paths: string[] = [];
  for (const declaration of expression.getSymbol()?.getDeclarations() ?? []) {
    paths.push(sourcePath(declaration.getSourceFile()));
    if (Node.isImportSpecifier(declaration)) {
      const module = declaration.getImportDeclaration().getModuleSpecifierSourceFile();
      if (module) paths.push(sourcePath(module));
    }
  }
  return paths;
}
function recordMethodWrite(symbol: MorphSymbol | undefined, writes: Set<string>): void {
  for (const declaration of originalSymbol(symbol)?.getDeclarations() ?? []) {
    if (Node.isMethodDeclaration(declaration)) {
      writes.add(staticId(sourcePath(declaration.getSourceFile()), declaration.getStart()));
    }
  }
}
function recordWriteDestination(node: Node, writes: Set<string>): void {
  if (Node.isParenthesizedExpression(node) || Node.isNonNullExpression(node) || Node.isAsExpression(node) || Node.isTypeAssertion(node)) {
    recordWriteDestination(node.getExpression(), writes);
  } else if (Node.isObjectLiteralExpression(node)) {
    for (const property of node.getProperties()) {
      if (Node.isPropertyAssignment(property)) recordWriteDestination(property.getInitializerOrThrow(), writes);
      else if (Node.isSpreadAssignment(property)) recordWriteDestination(property.getExpression(), writes);
    }
  } else if (Node.isArrayLiteralExpression(node)) {
    for (const element of node.getElements()) recordWriteDestination(element, writes);
  } else if (Node.isSpreadElement(node)) {
    recordWriteDestination(node.getExpression(), writes);
  } else if (Node.isBinaryExpression(node) && node.getOperatorToken().getKind() === SyntaxKind.EqualsToken) {
    // A default initializer is read, not a destination of the destructuring.
    recordWriteDestination(node.getLeft(), writes);
  } else {
    recordMethodWrite(node.getSymbol(), writes);
    if (!Node.isElementAccessExpression(node)) return;
    const type = node.getExpression().getType();
    const key = node.getArgumentExpression();
    if (key && (Node.isStringLiteral(key) || Node.isNoSubstitutionTemplateLiteral(key))) {
      recordMethodWrite(type.getProperty(key.getLiteralText()), writes);
    } else {
      // Unknown keys may overwrite any method of this receiver's class;
      // property symbols preserve class identity across aliases and imports.
      for (const property of type.getProperties()) recordMethodWrite(property, writes);
    }
  }
}
function methodWrites(program: SnapshotProgram): Set<string> {
  const writes = new Set<string>();
  // Include imported context files, not only tsconfig roots or test files.
  // These are possible AST writes, irrespective of whether a loop/update runs.
  // Static links stay runtime_confirmed=false; execution proof belongs to T11.
  for (const file of program.project.getSourceFiles()) {
    if (file.isDeclarationFile()) continue;
    for (const node of file.getDescendants()) {
      if (Node.isDeleteExpression(node)) {
        recordWriteDestination(node.getExpression(), writes);
      } else if (Node.isForOfStatement(node) || Node.isForInStatement(node)) {
        const initializer = node.getInitializer();
        if (!Node.isVariableDeclarationList(initializer)) recordWriteDestination(initializer, writes);
      } else if (Node.isPrefixUnaryExpression(node) || Node.isPostfixUnaryExpression(node)) {
        const operator = node.getOperatorToken();
        if (operator === SyntaxKind.PlusPlusToken || operator === SyntaxKind.MinusMinusToken) {
          recordWriteDestination(node.getOperand(), writes);
        }
      } else if (Node.isBinaryExpression(node)) {
        const operator = node.getOperatorToken().getKind();
        if (operator >= SyntaxKind.FirstAssignment && operator <= SyntaxKind.LastAssignment) {
          recordWriteDestination(node.getLeft(), writes);
        }
      }
    }
  }
  return writes;
}
function resolution(call: CallExpression, targets: Target[], writes: Set<string>): Pick<TestCall, 'symbol_target_id' | 'link_kind' | 'reason' | 'path_resolution'> {
  const expression = call.getExpression();
  const unresolved = (reason: TestCall['reason']): ReturnType<typeof resolution> => ({ symbol_target_id: null, link_kind: 'unresolved', reason, path_resolution: [] });
  if (Node.isElementAccessExpression(expression)) return unresolved('ambiguous_receiver');
  if (Node.isPropertyAccessExpression(expression)) {
    const receiver = expression.getExpression();
    const type = receiver.getType();
    if (type.isAny() || type.isUnknown() || type.isUnion() || type.isIntersection()) return unresolved('ambiguous_receiver');
  }
  const symbol = originalSymbol(expression.getSymbol());
  const declarations = symbol?.getDeclarations() ?? [];
  for (const declaration of declarations) {
    if (!(Node.isFunctionDeclaration(declaration) || Node.isMethodDeclaration(declaration)) || !declaration.getBody()) continue;
    if (Node.isMethodDeclaration(declaration)) {
      if (writes.has(staticId(sourcePath(declaration.getSourceFile()), declaration.getStart()))) return unresolved('ambiguous_receiver');
      const owner = declaration.getParentIfKind(SyntaxKind.ClassDeclaration);
      if (!owner) continue;
      if (declaration.isStatic()) {
        if (!Node.isPropertyAccessExpression(expression) ||
            originalSymbol(expression.getExpression().getSymbol()) !== owner.getSymbol()) {
          return unresolved('ambiguous_receiver');
        }
      } else {
        if (!Node.isPropertyAccessExpression(expression)) return unresolved('ambiguous_receiver');
        const receiver = expression.getExpression();
        // An explicit construction is statically exact; general polymorphic dispatch is not.
        let construction: Node = receiver;
        if (Node.isIdentifier(receiver)) {
          const variable = receiver.getSymbol()?.getDeclarations()[0];
          if (variable && Node.isVariableDeclaration(variable) && variable.getVariableStatement()?.getDeclarationKind() === 'const') construction = variable.getInitializer() ?? receiver;
        }
        if (!Node.isNewExpression(construction) || originalSymbol(construction.getExpression().getSymbol()) !== owner.getSymbol()) return unresolved('ambiguous_receiver');
      }
    }
    const target = targets.find(t => t.path === sourcePath(declaration.getSourceFile()) && t.start_offset === declaration.getStart());
    if (target) return { symbol_target_id: target.id, link_kind: 'direct', reason: 'resolved_symbol', path_resolution: [...new Set([sourcePath(call.getSourceFile()), ...resolutionPaths(expression), target.path])] };
  }
  return unresolved('target_not_found');
}
export function discoverCalls(programs: SnapshotProgram[], targets: Target[], framework: 'jest' | 'vitest'): { test_calls: TestCall[]; limitations: Issue[] } {
  const result = new Map<string, TestCall>();
  const limitations: Issue[] = [{ code: 'static_only', message: 'Static links require runtime confirmation in T11; parameterized executions are not counted' }];
  for (const program of programs) {
    const writes = methodWrites(program);
    for (const file of program.sourceFiles) {
      const path = sourcePath(file);
      if (!isTestPath(path)) continue;
      for (const declaration of file.getDescendantsOfKind(SyntaxKind.CallExpression)) {
        const info = frameworkCall(declaration, framework);
        if (!info || info.kind === 'describe') continue;
        const ownTitle = title(declaration);
        if (ownTitle === undefined) { limitations.push({ code: 'dynamic_case_title', message: 'Case title is not a static string', path }); continue; }
        const callback = declaration.getArguments()[1];
        if (!callback || !(Node.isArrowFunction(callback) || Node.isFunctionExpression(callback))) continue;
        const groups = declaration.getAncestors().filter(Node.isCallExpression).filter(c => frameworkCall(c, framework)?.kind === 'describe').reverse();
        const caseTitle = [...groups.map(g => title(g) ?? '<dynamic>'), ownTitle].join(' ');
        const caseId = staticId(path, declaration.getStart());
        const mockPotential = file.getDescendantsOfKind(SyntaxKind.CallExpression).some(c => /^(jest|vi)\.(mock|doMock|spyOn|fn)$/.test(c.getExpression().getText()));
        function visit(node: Node): void {
          if (Node.isFunctionDeclaration(node) || Node.isMethodDeclaration(node)) return;
          if (Node.isFunctionExpression(node) && node.getName()) return;
          if (Node.isArrowFunction(node) || Node.isFunctionExpression(node)) {
            const parent = node.getParent();
            if (!Node.isCallExpression(parent) || !parent.getArguments().includes(node)) return;
          }
          if (Node.isCallExpression(node)) {
            if (frameworkCall(node, framework)) return;
            const binding = frameworkBinding(node.getExpression(), framework);
            if (binding && ['beforeEach', 'afterEach', 'beforeAll', 'afterAll'].includes(binding)) return;
            const link = resolution(node, targets, writes);
            result.set(`${path}:${node.getStart()}:${caseId}`, { ...link, callsite_path: path, start_offset: node.getStart(), end_offset: node.getEnd(), case_declaration_id: caseId, case_title: caseTitle, case_start_offset: declaration.getStart(), parameterized: info!.parameterized, runtime_confirmed: false, mock_potential: mockPotential });
          }
          node.forEachChild(visit);
        }
        visit(callback.getBody());
      }
    }
  }
  return { test_calls: [...result.values()].sort((a,b) => a.callsite_path.localeCompare(b.callsite_path) || a.start_offset - b.start_offset), limitations };
}

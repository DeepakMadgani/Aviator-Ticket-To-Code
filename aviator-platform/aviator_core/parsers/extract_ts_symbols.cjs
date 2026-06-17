/**
 * TypeScript Compiler API extractor — runs via Node.js.
 *
 * Usage:
 *   node extract_ts_symbols.cjs <file_path> <repo_root> [ts_lib_path]
 *
 * Outputs a single JSON object to stdout:
 * {
 *   "classes":   [{name, qualified_name, decorators: [{name, args}], start_line, end_line}],
 *   "imports":   [{name, from_module, start_line}],
 *   "di_params": [{class_name, param_name, type_name, start_line}],
 *   "exports":   [{name, kind, start_line}],
 *   "template_url": "<relative path to HTML>",
 *   "style_urls":   ["<relative path to SCSS>", ...]
 * }
 *
 * Compatible with CommonJS (no ESM), works with TypeScript >= 4.x.
 */
"use strict";

const path = require("path");
const fs   = require("fs");

// ── Locate typescript ──────────────────────────────────────────────────────
function findTypescript(repoRoot) {
  const candidates = [
    path.join(repoRoot, "node_modules", "typescript"),
    path.join(repoRoot, "xchange-ui", "node_modules", "typescript"),
    path.join(repoRoot, "ui", "node_modules", "typescript"),
    path.join(repoRoot, "frontend", "node_modules", "typescript"),
  ];
  // Also try global node_modules
  try {
    return require.resolve("typescript");
  } catch (_) {}
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, "lib", "typescript.js"))) {
      return path.join(c, "lib", "typescript.js");
    }
  }
  return null;
}

// ── Main extraction ────────────────────────────────────────────────────────
const [,, filePath, repoRoot, tsLibPath] = process.argv;

if (!filePath || !repoRoot) {
  process.stderr.write("Usage: node extract_ts_symbols.cjs <file> <repo_root>\n");
  process.exit(1);
}

const resolvedTsLib = tsLibPath || findTypescript(repoRoot);
if (!resolvedTsLib) {
  process.stderr.write("typescript package not found\n");
  process.exit(2);
}

let ts;
try {
  ts = require(resolvedTsLib);
} catch (e) {
  process.stderr.write(`Failed to load typescript: ${e.message}\n`);
  process.exit(2);
}

const source = fs.readFileSync(filePath, "utf8");
const relPath = path.relative(repoRoot, filePath).replace(/\\/g, "/");
const fileDir = path.dirname(filePath);

const sourceFile = ts.createSourceFile(
  filePath,
  source,
  ts.ScriptTarget.Latest,
  /*setParentNodes*/ true,
);

const result = {
  classes:      [],
  imports:      [],
  di_params:    [],
  exports:      [],
  template_url: null,
  style_urls:   [],
};

function lineOf(node) {
  return sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1;
}

function endLineOf(node) {
  return sourceFile.getLineAndCharacterOfPosition(node.getEnd()).line + 1;
}

// ── Decode a @Component / @NgModule / etc. argument object ────────────────
function decodeObjectLiteral(node) {
  const obj = {};
  if (!node || node.kind !== ts.SyntaxKind.ObjectLiteralExpression) return obj;
  for (const prop of node.properties || []) {
    if (!prop.name) continue;
    const key = prop.name.text || prop.name.escapedText || "";
    if (!key) continue;
    const val = prop.initializer;
    if (!val) continue;
    if (val.kind === ts.SyntaxKind.StringLiteral || val.kind === ts.SyntaxKind.NoSubstitutionTemplateLiteral) {
      obj[key] = val.text;
    } else if (val.kind === ts.SyntaxKind.ArrayLiteralExpression) {
      obj[key] = (val.elements || [])
        .filter(e => e.kind === ts.SyntaxKind.StringLiteral)
        .map(e => e.text);
    }
  }
  return obj;
}

// ── Resolve a relative template/style path relative to the TS file ────────
function resolveTsRelative(rawUrl) {
  if (!rawUrl || rawUrl.startsWith("http") || rawUrl.startsWith("~")) return null;
  try {
    const abs = path.resolve(fileDir, rawUrl);
    const rel = path.relative(repoRoot, abs).replace(/\\/g, "/");
    return rel;
  } catch (_) {
    return null;
  }
}

// ── Walk the AST ──────────────────────────────────────────────────────────
function visit(node) {
  // Class declarations
  if (
    node.kind === ts.SyntaxKind.ClassDeclaration &&
    node.name
  ) {
    const className = node.name.text || node.name.escapedText;
    const qualifiedName = `${relPath}::${className}`;
    const decorators = [];

    // Walk modifiers (TypeScript 4.8+) or decorators (older API)
    const decoratorNodes =
      ts.getDecorators
        ? ts.getDecorators(node) || []
        : node.decorators || [];

    for (const dec of decoratorNodes) {
      const expr = dec.expression;
      let decName = "";
      let decArgs = {};

      if (expr.kind === ts.SyntaxKind.CallExpression) {
        decName = expr.expression.text || expr.expression.escapedText || "";
        const firstArg = (expr.arguments || [])[0];
        decArgs = decodeObjectLiteral(firstArg);
      } else if (expr.kind === ts.SyntaxKind.Identifier) {
        decName = expr.text || expr.escapedText || "";
      }

      if (!decName) continue;
      decorators.push({ name: decName, args: decArgs });

      // Extract template/style relationships from @Component
      if (decName === "Component") {
        if (decArgs.templateUrl) {
          const rel = resolveTsRelative(decArgs.templateUrl);
          if (rel) result.template_url = rel;
        }
        if (decArgs.styleUrls && Array.isArray(decArgs.styleUrls)) {
          for (const su of decArgs.styleUrls) {
            const rel = resolveTsRelative(su);
            if (rel) result.style_urls.push(rel);
          }
        }
        // single styleUrl (Angular 15+)
        if (decArgs.styleUrl) {
          const rel = resolveTsRelative(decArgs.styleUrl);
          if (rel) result.style_urls.push(rel);
        }
      }
    }

    // Extract constructor DI parameters
    const members = node.members || [];
    for (const member of members) {
      if (member.kind !== ts.SyntaxKind.Constructor) continue;
      for (const param of (member.parameters || [])) {
        if (!param.name || !param.type) continue;
        const paramName = param.name.text || param.name.escapedText || "";
        let typeName = "";
        if (param.type.kind === ts.SyntaxKind.TypeReference && param.type.typeName) {
          typeName = param.type.typeName.text || param.type.typeName.escapedText || "";
        }
        if (paramName && typeName) {
          result.di_params.push({
            class_name:  className,
            param_name:  paramName,
            type_name:   typeName,
            start_line:  lineOf(param),
          });
        }
      }
    }

    result.classes.push({
      name:           className,
      qualified_name: qualifiedName,
      decorators:     decorators,
      start_line:     lineOf(node),
      end_line:       endLineOf(node),
    });
  }

  // Import declarations
  if (node.kind === ts.SyntaxKind.ImportDeclaration && node.moduleSpecifier) {
    const fromModule = node.moduleSpecifier.text || "";
    const clause     = node.importClause;
    if (clause) {
      // Default import
      if (clause.name) {
        result.imports.push({
          name:        clause.name.text || clause.name.escapedText,
          from_module: fromModule,
          start_line:  lineOf(node),
        });
      }
      // Named imports
      if (clause.namedBindings && clause.namedBindings.elements) {
        for (const el of clause.namedBindings.elements) {
          result.imports.push({
            name:        el.name.text || el.name.escapedText,
            from_module: fromModule,
            start_line:  lineOf(node),
          });
        }
      }
    }
  }

  // Top-level exported variables/functions
  if (
    node.kind === ts.SyntaxKind.VariableStatement &&
    node.modifiers &&
    node.modifiers.some(m => m.kind === ts.SyntaxKind.ExportKeyword)
  ) {
    for (const decl of (node.declarationList?.declarations || [])) {
      if (decl.name) {
        result.exports.push({
          name:       decl.name.text || decl.name.escapedText || "",
          kind:       "const",
          start_line: lineOf(decl),
        });
      }
    }
  }
  if (
    node.kind === ts.SyntaxKind.FunctionDeclaration &&
    node.name &&
    node.modifiers &&
    node.modifiers.some(m => m.kind === ts.SyntaxKind.ExportKeyword)
  ) {
    result.exports.push({
      name:       node.name.text || node.name.escapedText || "",
      kind:       "function",
      start_line: lineOf(node),
    });
  }

  ts.forEachChild(node, visit);
}

visit(sourceFile);
process.stdout.write(JSON.stringify(result));

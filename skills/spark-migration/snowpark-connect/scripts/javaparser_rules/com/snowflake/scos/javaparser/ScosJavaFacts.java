// com.snowflake.scos.javaparser.ScosJavaFacts
//
// Deterministic Java AST fact extraction for the SCOS MIGRATE analyzer.
// Mirrors ScosMigrateFacts.scala exactly — the Python analyzer's
// fact-consumption code (analyze_java.py) works unchanged against this output.
//
// Every fact carries a 1-based line number so findings map back to source lines.
// The JSON schema is FROZEN (see scripts/javaparser_rules/README.md).
//
// Usage:
//   java -cp <jar> com.snowflake.scos.javaparser.ScosJavaFacts \
//       --source <file-or-dir> [--output <path>]
// or via the dispatcher:
//   java -jar scos-javaparser-runner.jar facts --source <dir> [--output <path>]
//
// Output: JSON to stdout (or --output). Exit 0 always; per-file `parse_ok`
// surfaces parse failures without aborting the run.
//
// JSON top-level:  { source, file_count, parse_errors, files: [...] }
// Per-file:        { path, parse_ok, imports, calls, selects, new_types,
//                    spark_sql, infix, interpolations, session_created }
//   imports        [ { ref, line } ]
//   calls          [ { method, recv_leaf, recv, args, arg_exprs, line } ]
//   selects        [ { member, recv_leaf, line } ]
//   new_types      [ { type, line } ]
//   spark_sql      [ { text, line } ]
//   infix          [ { op, lhs, rhs, line } ]
//   interpolations [ ]   (always empty — Java has no string interpolation)
//   session_created  bool
package com.snowflake.scos.javaparser;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.expr.BinaryExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.expr.StringLiteralExpr;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public class ScosJavaFacts {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    public static void main(String[] args) throws Exception {
        String source = "", output = "";
        for (int i = 0; i < args.length - 1; i++) {
            switch (args[i]) {
                case "--source": source = args[++i]; break;
                case "--output": output = args[++i]; break;
                default: break;
            }
        }
        if (source.isEmpty()) {
            System.err.println("ScosJavaFacts: --source <file-or-dir> is required");
            System.exit(2);
        }

        Path root = Paths.get(source).toAbsolutePath().normalize();
        List<Path> files = collectJavaFiles(root);

        ArrayNode fileResults = MAPPER.createArrayNode();
        int parseErrors = 0;
        for (Path f : files) {
            ObjectNode fr = analyzeFile(f);
            if (!fr.get("parse_ok").asBoolean()) parseErrors++;
            fileResults.add(fr);
        }

        ObjectNode out = MAPPER.createObjectNode();
        out.put("source", source);
        out.put("file_count", files.size());
        out.put("parse_errors", parseErrors);
        out.set("files", fileResults);

        String rendered = MAPPER.writerWithDefaultPrettyPrinter().writeValueAsString(out);
        if (!output.isEmpty()) {
            Path outPath = Paths.get(output).toAbsolutePath();
            if (outPath.getParent() != null) Files.createDirectories(outPath.getParent());
            Files.write(outPath, (rendered + "\n").getBytes(StandardCharsets.UTF_8));
            System.err.println("[scos-java-facts] wrote " + outPath + " (" + files.size() + " file(s))");
        } else {
            System.out.println(rendered);
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // File collection
    // ─────────────────────────────────────────────────────────────────────────

    private static List<Path> collectJavaFiles(Path root) throws IOException {
        List<Path> result = new ArrayList<>();
        if (Files.isRegularFile(root)) {
            if (root.toString().endsWith(".java")) result.add(root);
        } else if (Files.isDirectory(root)) {
            Files.walk(root)
                .filter(p -> Files.isRegularFile(p) && p.toString().endsWith(".java"))
                .sorted()
                .forEach(result::add);
        }
        return result;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Per-file analysis
    // ─────────────────────────────────────────────────────────────────────────

    static ObjectNode analyzeFile(Path p) {
        ObjectNode node = MAPPER.createObjectNode();
        node.put("path", p.toString());

        String code;
        try {
            code = new String(Files.readAllBytes(p), StandardCharsets.UTF_8);
        } catch (IOException e) {
            node.put("parse_ok", false);
            node.put("error", "read error: " + e.getMessage());
            return emptyFileNode(node);
        }
        return analyzeSource(code, p.toString(), node);
    }

    /** Package-visible overload used by smoke tests. */
    static ObjectNode analyzeSource(String code, String filePath) {
        ObjectNode node = MAPPER.createObjectNode();
        node.put("path", filePath);
        return analyzeSource(code, filePath, node);
    }

    private static ObjectNode analyzeSource(String code, String filePath, ObjectNode node) {
        ParserConfiguration config = new ParserConfiguration();
        config.setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_17);
        JavaParser parser = new JavaParser(config);
        ParseResult<CompilationUnit> result = parser.parse(code);

        if (!result.isSuccessful() || !result.getResult().isPresent()) {
            String err = result.getProblems().isEmpty() ? "unknown parse error"
                    : result.getProblems().get(0).getMessage();
            node.put("parse_ok", false);
            node.put("error", err);
            return emptyFileNode(node);
        }

        CompilationUnit cu = result.getResult().get();
        node.put("parse_ok", true);

        ArrayNode imports       = MAPPER.createArrayNode();
        ArrayNode calls         = MAPPER.createArrayNode();
        ArrayNode selects       = MAPPER.createArrayNode();
        ArrayNode newTypes      = MAPPER.createArrayNode();
        ArrayNode sparkSql      = MAPPER.createArrayNode();
        ArrayNode infixOps      = MAPPER.createArrayNode();
        ArrayNode interpolations = MAPPER.createArrayNode(); // always empty in Java
        boolean[] sessionCreated = {false};

        // ── Imports ──────────────────────────────────────────────────────────
        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(ImportDeclaration id, Void arg) {
                super.visit(id, arg);
                String ref = id.getNameAsString();
                if (id.isAsterisk()) ref += ".*";
                int line = id.getBegin().map(pos -> pos.line).orElse(0);
                ObjectNode imp = MAPPER.createObjectNode();
                imp.put("ref", ref);
                imp.put("line", line);
                imports.add(imp);
            }
        }, null);

        // ── Method calls ──────────────────────────────────────────────────────
        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String method = mce.getNameAsString();
                int line = mce.getBegin().map(pos -> pos.line).orElse(0);

                Optional<Expression> scopeOpt = mce.getScope();
                String recvLeaf = "";
                String recv     = "";
                if (scopeOpt.isPresent()) {
                    Expression scope = scopeOpt.get();
                    recvLeaf = leafName(scope);
                    recv     = tailSyntax(scope.toString());
                }

                // spark_sql: x.sql("...") where recv leaf or chain contains "spark"/"sf"
                if (method.equals("sql") &&
                        (recvLeaf.equals("spark") || recvLeaf.equals("sf")
                                || recv.contains("spark"))) {
                    for (Expression a : mce.getArguments()) {
                        if (a instanceof StringLiteralExpr) {
                            ObjectNode sqlNode = MAPPER.createObjectNode();
                            sqlNode.put("text", ((StringLiteralExpr) a).asString());
                            sqlNode.put("line", line);
                            sparkSql.add(sqlNode);
                        }
                    }
                }

                // session_created: getOrCreate() whose FULL scope chain contains SparkSession+builder.
                // Use the un-truncated scope string; recv is tail-truncated so it may miss the prefix.
                if (method.equals("getOrCreate") && scopeOpt.isPresent()) {
                    String fullScope = scopeOpt.get().toString().replaceAll("\\s+", " ");
                    if (fullScope.contains("SparkSession") && fullScope.contains("builder")) {
                        sessionCreated[0] = true;
                    }
                }

                // String-literal args (the "args" field)
                ArrayNode argsNode = MAPPER.createArrayNode();
                for (Expression a : mce.getArguments()) {
                    if (a instanceof StringLiteralExpr) {
                        argsNode.add(((StringLiteralExpr) a).asString());
                    }
                }

                // All arg expressions, bounded syntax (the "arg_exprs" field)
                ArrayNode argExprsNode = MAPPER.createArrayNode();
                for (Expression a : mce.getArguments()) {
                    argExprsNode.add(headSyntax(a.toString()));
                }

                ObjectNode callNode = MAPPER.createObjectNode();
                callNode.put("method",    method);
                callNode.put("recv_leaf", recvLeaf);
                callNode.put("recv",      recv);
                callNode.set("args",      argsNode);
                callNode.set("arg_exprs", argExprsNode);
                callNode.put("line",      line);
                calls.add(callNode);
            }
        }, null);

        // ── Field accesses (selects) ──────────────────────────────────────────
        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(FieldAccessExpr fae, Void arg) {
                super.visit(fae, arg);
                String member   = fae.getNameAsString();
                String recvLeaf = leafName(fae.getScope());
                int line        = fae.getBegin().map(pos -> pos.line).orElse(0);
                ObjectNode sel  = MAPPER.createObjectNode();
                sel.put("member",    member);
                sel.put("recv_leaf", recvLeaf);
                sel.put("line",      line);
                selects.add(sel);
            }
        }, null);

        // ── Object creations (new_types) ──────────────────────────────────────
        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(ObjectCreationExpr oce, Void arg) {
                super.visit(oce, arg);
                String type = oce.getType().getNameAsString();
                int line    = oce.getBegin().map(pos -> pos.line).orElse(0);
                ObjectNode nt = MAPPER.createObjectNode();
                nt.put("type", type);
                nt.put("line", line);
                newTypes.add(nt);
            }
        }, null);

        // ── Binary expressions (infix) ────────────────────────────────────────
        // lhs keeps its TAIL and rhs its HEAD so Python operand-shape regexes match.
        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(BinaryExpr be, Void arg) {
                super.visit(be, arg);
                String op   = be.getOperator().asString();
                String lhs  = tailSyntax(be.getLeft().toString());
                String rhs  = headSyntax(be.getRight().toString());
                int line    = be.getBegin().map(pos -> pos.line).orElse(0);
                ObjectNode inf = MAPPER.createObjectNode();
                inf.put("op",   op);
                inf.put("lhs",  lhs);
                inf.put("rhs",  rhs);
                inf.put("line", line);
                infixOps.add(inf);
            }
        }, null);

        node.set("imports",        imports);
        node.set("calls",          calls);
        node.set("selects",        selects);
        node.set("new_types",      newTypes);
        node.set("spark_sql",      sparkSql);
        node.set("infix",          infixOps);
        node.set("interpolations", interpolations);
        node.put("session_created", sessionCreated[0]);
        return node;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Helpers (package-visible so ScosJavaRewrite can reuse them)
    // ─────────────────────────────────────────────────────────────────────────

    /** Leaf identifier of a (possibly chained) expression — mirrors ScosMigrateFacts.leafName. */
    static String leafName(Expression e) {
        if (e instanceof NameExpr)        return ((NameExpr) e).getNameAsString();
        if (e instanceof FieldAccessExpr) return ((FieldAccessExpr) e).getNameAsString();
        if (e instanceof MethodCallExpr)  return ((MethodCallExpr) e).getNameAsString();
        String s = e.toString();
        return s.length() > 40 ? s.substring(s.length() - 40) : s;
    }

    /** Bounded tail syntax ≤80 chars with whitespace collapsed — for recv. */
    static String tailSyntax(String s) {
        String n = s.replaceAll("\\s+", " ");
        return n.length() > 80 ? n.substring(n.length() - 80) : n;
    }

    /** Bounded head syntax ≤80 chars with whitespace collapsed — for arg_exprs / rhs. */
    static String headSyntax(String s) {
        String n = s.replaceAll("\\s+", " ");
        return n.length() > 80 ? n.substring(0, 80) : n;
    }

    private static ObjectNode emptyFileNode(ObjectNode base) {
        base.set("imports",        MAPPER.createArrayNode());
        base.set("calls",          MAPPER.createArrayNode());
        base.set("selects",        MAPPER.createArrayNode());
        base.set("new_types",      MAPPER.createArrayNode());
        base.set("spark_sql",      MAPPER.createArrayNode());
        base.set("infix",          MAPPER.createArrayNode());
        base.set("interpolations", MAPPER.createArrayNode());
        base.put("session_created", false);
        return base;
    }
}

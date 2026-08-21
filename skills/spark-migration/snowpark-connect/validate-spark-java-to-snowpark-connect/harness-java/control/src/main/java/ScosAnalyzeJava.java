// com.snowflake.scos.validate.ScosAnalyzeJava
//
// Deterministic Java source analysis for the validator's analyzer agent.
//
// Mirrors the ScosAnalyze.scala contract: given --source <file-or-dir>, emit
// a JSON object with per-file AST facts:
//   - entrypoints   (classes with public static void main(String[]) or run method)
//   - imports
//   - reads         (spark.read()....{parquet,csv,json,orc,text,load,table,jdbc};
//                    SparkContext RDD reads sc.{textFile,wholeTextFiles,binaryFiles,
//                    binaryRecords,sequenceFile,objectFile};
//                    DeltaTable.forPath/forName; spark.catalog reads)
//   - writes        (....write()....{save,saveAsTable,insertInto} + format terminals;
//                    spark.catalog.createTable/createExternalTable)
//   - table_refs    (spark.table / saveAsTable / insertInto targets)
//   - column_refs   (col("x"), functions.col("x"), df.col("x"), string args of
//                    select / groupBy / orderBy / sort / sortBy / drop / dropDuplicates)
//   - unresolved_reads / unresolved_writes  — call sites whose path/table arg
//                    could not be statically resolved ({kind,call,arg_expr,line})
//
// Argument resolution (parity with ScosAnalyze.scala):
//   B1  String literal               → verbatim
//   B3  .format(arg)                 → substitute %s/{} in receiver
//   B4  .replace(old,new)            → perform replacement
//   B5  String.join(sep, ...)        → join resolved parts with sep
//       Collectors.joining(sep)      → resolved from stream source args
//   B6  "a" + "b" binary concat      → concatenate resolved sides
//   B7  Map.of("k","v",...).get("k") → look up literal key in Map.of pairs
//   B8  variable holding Map.of      → same, resolved via B9 then B7
//   B9  Name → variable binding      → recurse on initializer
//   B10 for-each loop variable       → first element of source iterable
//   B11 cond ? a : b ternary         → enumerate BOTH branches
//   B12 .trim/.toLowerCase/etc.      → recurse receiver (trivial pass)
//   B13 System.getProperty("k","d")  → use default arg
//       .getOrDefault(key, default)  → use default arg
//   B14 Paths.get("a"[,"b",...])     → join args with "/"
//       new File(parent[, child])    → join args with "/"
//   B15 Call-site param binding      → inline literal args into method body
//   B16 f() single-return inlining   → recurse body
//
// Usage:
//   java -jar scos-analyze-java.jar analyze --source <file-or-dir> [--output <path>]
//                                           [--config-pool-file <flat-json-map>]
//
// --config-pool-file  Optional flat JSON {"VAR_NAME": "value", ...} produced by
//                     the Python-side _load_config_pool in scan_codebase.py.
//                     Variable names unresolvable via Java bindings are looked up
//                     here as a final fallback (PR #3548 parity).
//
// Exit 0 always; per-file parse_ok flags surface parse failures.

package com.snowflake.scos.validate;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.*;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.stmt.ForEachStmt;
import com.github.javaparser.ast.stmt.ReturnStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public class ScosAnalyzeJava {

    private static final Set<String> READ_TERMINALS = Set.of(
        "parquet", "csv", "json", "orc", "text", "textFile", "load", "table", "jdbc"
    );

    private static final Set<String> FMT_TERMINALS = Set.of(
        "parquet", "csv", "json", "orc", "text"
    );

    private static final Set<String> COL_METHODS = Set.of(
        "select", "groupBy", "orderBy", "sort", "sortBy", "drop", "dropDuplicates"
    );

    // SparkContext RDD read methods (parity with ScosAnalyze.scala scReadMethods)
    private static final Set<String> SC_READ_METHODS = Set.of(
        "textFile", "wholeTextFiles", "binaryFiles", "binaryRecords", "sequenceFile", "objectFile"
    );

    // B12: trivial string-method passthrough (resolver recurses the receiver)
    // toString added for Java idiom: Paths.get(...).toString() — not needed in Scala
    private static final Set<String> TRIVIAL_PASS = Set.of(
        "trim", "strip", "stripLeading", "stripTrailing",
        "toLowerCase", "toUpperCase", "intern", "toString"
    );

    private static final int DEPTH_CAP = 6;

    // ── entry point ──────────────────────────────────────────────────────────────

    public static void main(String[] args) {
        String source         = "";
        String output         = "";
        String configPoolFile = "";

        for (int i = 0; i < args.length; i++) {
            if ("--source".equals(args[i]) && i + 1 < args.length)           source         = args[++i];
            else if ("--output".equals(args[i]) && i + 1 < args.length)      output         = args[++i];
            else if ("--config-pool-file".equals(args[i]) && i + 1 < args.length) configPoolFile = args[++i];
        }

        if (source.isEmpty()) {
            System.err.println("[scos-control] error: analyze: --source <file-or-dir> is required");
            System.exit(2);
        }

        Map<String, String> configPool = configPoolFile.isEmpty()
            ? Collections.emptyMap() : loadConfigPool(configPoolFile);

        Path root = Paths.get(source).toAbsolutePath().normalize();
        List<Path> files = collectJavaFiles(root);
        List<Map<String, Object>> fileResults = new ArrayList<>();
        for (Path f : files) {
            fileResults.add(analyzeFile(f, configPool));
        }

        long parseErrors = fileResults.stream()
            .filter(m -> Boolean.FALSE.equals(m.get("parse_ok")))
            .count();

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("source", source);
        result.put("file_count", files.size());
        result.put("parse_errors", parseErrors);
        result.put("files", fileResults);

        Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
        String rendered = gson.toJson(result);

        if (!output.isEmpty()) {
            Path outPath = Paths.get(output).toAbsolutePath();
            try {
                if (outPath.getParent() != null) {
                    Files.createDirectories(outPath.getParent());
                }
                Files.writeString(outPath, rendered + "\n", StandardCharsets.UTF_8);
                System.err.println("[scos-analyze] wrote " + outPath + " (" + files.size() + " file(s))");
            } catch (IOException e) {
                System.err.println("[scos-control] error: cannot write output: " + e.getMessage());
                System.exit(2);
            }
        } else {
            System.out.println(rendered);
        }
    }

    // ── config pool loader ────────────────────────────────────────────────────

    private static Map<String, String> loadConfigPool(String path) {
        String text;
        try {
            text = Files.readString(Paths.get(path), StandardCharsets.UTF_8);
        } catch (IOException e) {
            System.err.println("[scos-analyze] WARNING: could not read config pool file: " + path);
            return Collections.emptyMap();
        }
        try {
            JsonElement el = JsonParser.parseString(text);
            if (!el.isJsonObject()) return Collections.emptyMap();
            JsonObject obj = el.getAsJsonObject();
            Map<String, String> pool = new LinkedHashMap<>();
            for (Map.Entry<String, JsonElement> entry : obj.entrySet()) {
                JsonElement v = entry.getValue();
                if (v.isJsonPrimitive() && v.getAsJsonPrimitive().isString()) {
                    pool.put(entry.getKey(), v.getAsString());
                }
            }
            return pool;
        } catch (Exception e) {
            System.err.println("[scos-analyze] WARNING: could not parse config pool JSON: " + e.getMessage());
            return Collections.emptyMap();
        }
    }

    // ── resolver context builders ─────────────────────────────────────────────

    /** B9: variable name → initializer expression (first binding wins). */
    private static Map<String, Expression> buildValBindings(CompilationUnit cu) {
        Map<String, Expression> m = new LinkedHashMap<>();
        cu.findAll(VariableDeclarator.class).forEach(vd ->
            vd.getInitializer().ifPresent(init -> m.putIfAbsent(vd.getNameAsString(), init)));
        return m;
    }

    /** B16: method name → single-return-expression body (first occurrence wins). */
    private static Map<String, Expression> buildDefReturns(CompilationUnit cu) {
        Map<String, Expression> m = new LinkedHashMap<>();
        cu.findAll(MethodDeclaration.class).forEach(md ->
            md.getBody().ifPresent(body -> {
                List<Statement> stmts = body.getStatements();
                if (stmts.size() == 1 && stmts.get(0) instanceof ReturnStmt) {
                    ((ReturnStmt) stmts.get(0)).getExpression()
                        .ifPresent(expr -> m.putIfAbsent(md.getNameAsString(), expr));
                }
            }));
        return m;
    }

    /**
     * B15: Call-site param binding. For a method call site with literal args,
     * build a parameter-name → call-site arg map so the body can be resolved
     * with concrete values substituted in (parity with ScosAnalyze.scala buildParamBindings).
     * Returns empty map when arg count doesn't match or no method found.
     */
    private static Map<String, Expression> buildParamBindings(
            CompilationUnit cu, String methodName, List<Expression> callArgs) {
        Map<String, Expression> m = new LinkedHashMap<>();
        cu.findAll(MethodDeclaration.class).stream()
            .filter(md -> md.getNameAsString().equals(methodName))
            .findFirst()
            .ifPresent(md -> {
                List<Parameter> params = md.getParameters();
                if (params.size() == callArgs.size()) {
                    for (int i = 0; i < params.size(); i++)
                        m.put(params.get(i).getNameAsString(), callArgs.get(i));
                }
            });
        return m;
    }

    /**
     * B7/B8: Collect Map.of("k","v",...) literal pairs from a CompilationUnit.
     * Maps variable name → inner Map.of call for on-demand key lookup.
     */
    private static Map<String, MethodCallExpr> buildMapLiterals(CompilationUnit cu) {
        Map<String, MethodCallExpr> m = new LinkedHashMap<>();
        cu.findAll(VariableDeclarator.class).forEach(vd ->
            vd.getInitializer().ifPresent(init -> {
                if (init instanceof MethodCallExpr) {
                    MethodCallExpr mc = (MethodCallExpr) init;
                    if ("of".equals(mc.getNameAsString())
                            && mc.getScope().map(s -> "Map".equals(s.toString())).orElse(false))
                        m.put(vd.getNameAsString(), mc);
                }
            }));
        return m;
    }

    /**
     * B10: for-each loop variable → first element of source iterable.
     * Collects a map of loop-variable name → first resolvable element of the source.
     */
    private static Map<String, Expression> buildForTargets(CompilationUnit cu) {
        Map<String, Expression> m = new LinkedHashMap<>();
        cu.findAll(ForEachStmt.class).forEach(fe -> {
            String varName = fe.getVariable().getVariables().get(0).getNameAsString();
            Expression iterable = fe.getIterable();
            // For array/vararg init: {a, b, c} or new X[]{a,b,c}
            if (iterable instanceof ArrayCreationExpr) {
                ArrayCreationExpr ace = (ArrayCreationExpr) iterable;
                ace.getInitializer().ifPresent(init -> {
                    if (!init.getValues().isEmpty())
                        m.putIfAbsent(varName, init.getValues().get(0));
                });
            } else if (iterable instanceof ArrayInitializerExpr) {
                ArrayInitializerExpr aie = (ArrayInitializerExpr) iterable;
                if (!aie.getValues().isEmpty())
                    m.putIfAbsent(varName, aie.getValues().get(0));
            } else {
                // NameExpr or MethodCallExpr — record the iterable itself as a proxy
                m.putIfAbsent(varName, iterable);
            }
        });
        return m;
    }

    // ── argument resolver (B1–B16 + config pool) ─────────────────────────────

    /**
     * Recursively resolve an Expression to a list of concrete string signatures.
     * Empty list → could not resolve (caller emits an unresolved edge).
     * Multiple elements → enumerated branches (ternary).
     */
    private static List<String> resolveArg(
            Expression node,
            int depth,
            Map<String, Expression> vals,
            Map<String, Expression> defs,
            Map<String, String> configPool,
            CompilationUnit cu) {
        if (depth >= DEPTH_CAP) return Collections.emptyList();
        int d1 = depth + 1;

        // B1: string literal
        if (node instanceof StringLiteralExpr)
            return List.of(((StringLiteralExpr) node).getValue());

        // Parenthesized expression — recurse inner
        if (node instanceof EnclosedExpr)
            return resolveArg(((EnclosedExpr) node).getInner(), d1, vals, defs, configPool, cu);

        // B6: binary + concatenation
        if (node instanceof BinaryExpr) {
            BinaryExpr bin = (BinaryExpr) node;
            if (bin.getOperator() == BinaryExpr.Operator.PLUS) {
                List<String> ls = resolveArg(bin.getLeft(),  d1, vals, defs, configPool, cu);
                List<String> rs = resolveArg(bin.getRight(), d1, vals, defs, configPool, cu);
                if (ls.isEmpty() || rs.isEmpty()) return Collections.emptyList();
                List<String> out = new ArrayList<>(ls.size() * rs.size());
                for (String l : ls) for (String r : rs) out.add(l + r);
                return out;
            }
            return Collections.emptyList();
        }

        // B11: ternary conditional — enumerate BOTH branches
        if (node instanceof ConditionalExpr) {
            ConditionalExpr ternary = (ConditionalExpr) node;
            List<String> result = new ArrayList<>();
            result.addAll(resolveArg(ternary.getThenExpr(), d1, vals, defs, configPool, cu));
            result.addAll(resolveArg(ternary.getElseExpr(), d1, vals, defs, configPool, cu));
            return result;
        }

        // B9/B16: variable name → val binding or def inlining, with config pool fallback
        if (node instanceof NameExpr) {
            String x = ((NameExpr) node).getNameAsString();
            if (vals.containsKey(x)) {
                List<String> r = resolveArg(vals.get(x), d1, vals, defs, configPool, cu);
                if (!r.isEmpty()) return r;
            }
            if (defs.containsKey(x)) {
                List<String> r = resolveArg(defs.get(x), d1, vals, defs, configPool, cu);
                if (!r.isEmpty()) return r;
            }
            if (configPool.containsKey(x)) return List.of(configPool.get(x));
            return Collections.emptyList();
        }

        if (node instanceof MethodCallExpr) {
            MethodCallExpr mc      = (MethodCallExpr) node;
            String         mName   = mc.getNameAsString();
            List<Expression> mArgs = mc.getArguments();
            Optional<Expression> scopeOpt = mc.getScope();
            String scopeStr = scopeOpt.map(Object::toString).orElse("");

            // B12: trivial passthrough methods — recurse receiver
            if (TRIVIAL_PASS.contains(mName) && mArgs.isEmpty() && scopeOpt.isPresent())
                return resolveArg(scopeOpt.get(), d1, vals, defs, configPool, cu);

            // B3: .format(arg) — substitute %s/{} in receiver
            if ("format".equals(mName) && !mArgs.isEmpty() && scopeOpt.isPresent()) {
                List<String> recvStrs = resolveArg(scopeOpt.get(), d1, vals, defs, configPool, cu);
                List<String> argStrs  = resolveArg(mArgs.get(0),   d1, vals, defs, configPool, cu);
                if (recvStrs.isEmpty() || argStrs.isEmpty()) return Collections.emptyList();
                List<String> out = new ArrayList<>();
                for (String r : recvStrs)
                    for (String a : argStrs)
                        out.add(r.replace("%s", a).replace("{}", a));
                return out;
            }

            // B4: .replace(old, new)
            if ("replace".equals(mName) && mArgs.size() == 2 && scopeOpt.isPresent()) {
                List<String> recvStrs = resolveArg(scopeOpt.get(), d1, vals, defs, configPool, cu);
                List<String> oldStrs  = resolveArg(mArgs.get(0),   d1, vals, defs, configPool, cu);
                List<String> newStrs  = resolveArg(mArgs.get(1),   d1, vals, defs, configPool, cu);
                if (recvStrs.isEmpty() || oldStrs.isEmpty() || newStrs.isEmpty())
                    return Collections.emptyList();
                List<String> out = new ArrayList<>();
                for (String r : recvStrs)
                    for (String o : oldStrs)
                        for (String n : newStrs)
                            out.add(r.replace(o, n));
                return out;
            }

            // B5: String.join(sep, a, b, ...) → join resolved parts with sep
            if ("join".equals(mName) && mArgs.size() >= 2 && "String".equals(scopeStr)) {
                List<String> seps = resolveArg(mArgs.get(0), d1, vals, defs, configPool, cu);
                if (seps.isEmpty()) return Collections.emptyList();
                String sep = seps.get(0);
                List<String> parts = new ArrayList<>();
                for (int i = 1; i < mArgs.size(); i++) {
                    List<String> ps = resolveArg(mArgs.get(i), d1, vals, defs, configPool, cu);
                    if (ps.isEmpty()) return Collections.emptyList();
                    parts.add(ps.get(0));
                }
                return List.of(String.join(sep, parts));
            }

            // B7/B8: Map.of("k1","v1",...).get("key") → literal value lookup
            if ("get".equals(mName) && mArgs.size() == 1 && scopeOpt.isPresent()) {
                Expression scope = scopeOpt.get();
                MethodCallExpr mapOf = null;
                if (scope instanceof MethodCallExpr) {
                    MethodCallExpr sm = (MethodCallExpr) scope;
                    if ("of".equals(sm.getNameAsString())
                            && sm.getScope().map(s -> "Map".equals(s.toString())).orElse(false))
                        mapOf = sm;
                } else if (scope instanceof NameExpr) {
                    // B8: variable holding a Map.of literal
                    Map<String, MethodCallExpr> mapLiterals = buildMapLiterals(cu);
                    mapOf = mapLiterals.get(((NameExpr) scope).getNameAsString());
                }
                if (mapOf != null) {
                    List<Expression> pairs = mapOf.getArguments();
                    List<String> keys = resolveArg(mArgs.get(0), d1, vals, defs, configPool, cu);
                    if (!keys.isEmpty() && pairs.size() % 2 == 0) {
                        for (int i = 0; i < pairs.size(); i += 2) {
                            List<String> k = resolveArg(pairs.get(i),   d1, vals, defs, configPool, cu);
                            List<String> v = resolveArg(pairs.get(i+1), d1, vals, defs, configPool, cu);
                            if (!k.isEmpty() && k.get(0).equals(keys.get(0)) && !v.isEmpty())
                                return v;
                        }
                    }
                }
            }

            // B13: System.getProperty("k", "default") → use default (2nd arg)
            if ("getProperty".equals(mName) && mArgs.size() >= 2 && "System".equals(scopeStr))
                return resolveArg(mArgs.get(1), d1, vals, defs, configPool, cu);

            // B13: .getOrDefault(key, default) → use default
            if ("getOrDefault".equals(mName) && mArgs.size() >= 2)
                return resolveArg(mArgs.get(1), d1, vals, defs, configPool, cu);

            // B14: Paths.get("a"[,"b",...]) → join all args with "/"
            if ("get".equals(mName) && !mArgs.isEmpty() && "Paths".equals(scopeStr))
                return joinPathParts(mArgs, d1, vals, defs, configPool, cu);

            // B15: call-site param binding — bind literal args into method body and recurse
            if (!mArgs.isEmpty() && !scopeOpt.isPresent() && defs.containsKey(mName)) {
                Map<String, Expression> paramBindings = buildParamBindings(cu, mName, mArgs);
                if (!paramBindings.isEmpty()) {
                    Map<String, Expression> augVals = new LinkedHashMap<>(vals);
                    augVals.putAll(paramBindings);
                    return resolveArg(defs.get(mName), d1, augVals, defs, configPool, cu);
                }
            }

            // B16: zero-arg method call → def inlining
            if (mArgs.isEmpty() && !scopeOpt.isPresent() && defs.containsKey(mName))
                return resolveArg(defs.get(mName), d1, vals, defs, configPool, cu);

            return Collections.emptyList();
        }

        // B14: new File(parent[, child]) → join args with "/"
        if (node instanceof ObjectCreationExpr) {
            ObjectCreationExpr oce = (ObjectCreationExpr) node;
            String typeName = oce.getTypeAsString();
            if (("File".equals(typeName) || typeName.endsWith(".File")) && !oce.getArguments().isEmpty())
                return joinPathParts(oce.getArguments(), d1, vals, defs, configPool, cu);
        }

        return Collections.emptyList();
    }

    /** Resolve a list of path-segment expressions and join with "/". */
    private static List<String> joinPathParts(
            List<Expression> parts, int depth,
            Map<String, Expression> vals, Map<String, Expression> defs,
            Map<String, String> configPool, CompilationUnit cu) {
        List<List<String>> resolved = new ArrayList<>();
        for (Expression part : parts) {
            List<String> r = resolveArg(part, depth, vals, defs, configPool, cu);
            if (r.isEmpty()) return Collections.emptyList();
            resolved.add(r);
        }
        // Cross-product join with "/"
        List<String> result = new ArrayList<>();
        result.add("");
        for (List<String> seg : resolved) {
            List<String> next = new ArrayList<>();
            for (String prefix : result)
                for (String s : seg)
                    next.add(prefix.isEmpty() ? s : prefix + "/" + s);
            result = next;
        }
        return result;
    }

    // ── JSON helpers ──────────────────────────────────────────────────────────

    private static Map<String, Object> callJson(String call, List<String> args, int line) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("call", call);
        m.put("args", args);
        m.put("line", line);
        return m;
    }

    private static Map<String, Object> unresolvedJson(String kind, String call, String argExpr, int line) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("kind",     kind);
        m.put("call",     call);
        m.put("arg_expr", argExpr);
        m.put("line",     line);
        return m;
    }

    // ── file collection ───────────────────────────────────────────────────────

    private static List<Path> collectJavaFiles(Path root) {
        if (Files.isRegularFile(root))
            return root.toString().endsWith(".java") ? List.of(root) : List.of();
        if (!Files.isDirectory(root)) return List.of();
        try (Stream<Path> walk = Files.walk(root)) {
            return walk
                .filter(Files::isRegularFile)
                .filter(p -> p.toString().endsWith(".java"))
                .sorted()
                .collect(Collectors.toList());
        } catch (IOException e) {
            return List.of();
        }
    }

    // ── per-file analysis ─────────────────────────────────────────────────────

    private static Map<String, Object> analyzeFile(Path p, Map<String, String> configPool) {
        String code;
        try {
            code = Files.readString(p, StandardCharsets.UTF_8);
        } catch (IOException e) {
            Map<String, Object> err = new LinkedHashMap<>();
            err.put("path",     p.toString());
            err.put("parse_ok", false);
            err.put("error",    "read error: " + e.getMessage());
            return err;
        }

        JavaParser parser = new JavaParser();
        ParseResult<CompilationUnit> parseResult = parser.parse(code);

        if (!parseResult.isSuccessful()) {
            String msg = parseResult.getProblems().isEmpty()
                ? "parse error"
                : parseResult.getProblems().get(0).getMessage();
            Map<String, Object> err = new LinkedHashMap<>();
            err.put("path",     p.toString());
            err.put("parse_ok", false);
            err.put("error",    msg);
            return err;
        }

        CompilationUnit cu = parseResult.getResult().orElse(null);
        if (cu == null) {
            Map<String, Object> err = new LinkedHashMap<>();
            err.put("path",     p.toString());
            err.put("parse_ok", false);
            err.put("error",    "parse returned no result");
            return err;
        }

        // Build resolver context
        Map<String, Expression> vals = buildValBindings(cu);
        // B10: merge for-each loop variables (first element of each iterable) into vals
        buildForTargets(cu).forEach(vals::putIfAbsent);
        Map<String, Expression> defs = buildDefReturns(cu);

        List<String> imports = cu.getImports().stream()
            .map(ImportDeclaration::toString)
            .map(String::trim)
            .sorted()
            .collect(Collectors.toList());

        List<String> classes = new ArrayList<>();
        List<Map<String, String>> entrypoints = new ArrayList<>();

        cu.findAll(ClassOrInterfaceDeclaration.class).forEach(cls -> {
            classes.add(cls.getNameAsString());
            cls.getMethods().forEach(m -> {
                String mName = m.getNameAsString();
                if ("main".equals(mName) && m.isStatic() && m.isPublic()) {
                    Map<String, String> ep = new LinkedHashMap<>();
                    ep.put("owner",  cls.getNameAsString());
                    ep.put("method", "main");
                    entrypoints.add(ep);
                } else if ("run".equals(mName)) {
                    Map<String, String> ep = new LinkedHashMap<>();
                    ep.put("owner",  cls.getNameAsString());
                    ep.put("method", "run");
                    entrypoints.add(ep);
                }
            });
        });
        Collections.sort(classes);

        FactsCollector collector = new FactsCollector(vals, defs, configPool, cu);
        collector.visit(cu, null);

        // Detect write helpers: methods whose body contains write calls (transitive)
        List<String> writeHelpers = new ArrayList<>();
        Set<String> directWriterNames = new HashSet<>();
        Map<String, MethodDeclaration> methodsByName = new LinkedHashMap<>();

        cu.findAll(MethodDeclaration.class).forEach(md -> {
            methodsByName.put(md.getNameAsString(), md);
            if (bodyContainsWrite(md)) directWriterNames.add(md.getNameAsString());
        });

        Set<String> allWriteHelpers = new LinkedHashSet<>(directWriterNames);
        methodsByName.forEach((name, md) -> {
            if (!allWriteHelpers.contains(name)) {
                Set<String> called = md.findAll(MethodCallExpr.class).stream()
                    .map(MethodCallExpr::getNameAsString)
                    .collect(Collectors.toSet());
                called.retainAll(directWriterNames);
                if (!called.isEmpty()) allWriteHelpers.add(name);
            }
        });
        writeHelpers.addAll(allWriteHelpers);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("path",                  p.toString());
        out.put("parse_ok",              true);
        out.put("classes",               classes);
        out.put("entrypoints",           entrypoints);
        out.put("imports",               imports);
        out.put("spark_session_created", collector.sparkSessionCreated);
        out.put("reads",                 collector.reads);
        out.put("writes",                collector.writes);
        out.put("unresolved_reads",      collector.unresolvedReads);
        out.put("unresolved_writes",     collector.unresolvedWrites);
        out.put("write_helpers",         writeHelpers);
        out.put("table_refs",            new ArrayList<>(collector.tableRefs));
        out.put("column_refs",           new ArrayList<>(collector.colRefs));

        return out;
    }

    private static boolean bodyContainsWrite(MethodDeclaration md) {
        for (MethodCallExpr call : md.findAll(MethodCallExpr.class)) {
            String name = call.getNameAsString();
            if ("saveAsTable".equals(name) || "insertInto".equals(name) || "save".equals(name))
                return true;
            if (FMT_TERMINALS.contains(name) && hasWriteInScope(call))
                return true;
        }
        return false;
    }

    private static boolean hasWriteInScope(MethodCallExpr call) {
        return call.getScope()
            .map(Object::toString)
            .map(s -> s.contains(".write()") || s.contains(".write("))
            .orElse(false);
    }

    // ── FactsCollector ────────────────────────────────────────────────────────

    private static class FactsCollector extends VoidVisitorAdapter<Void> {
        boolean sparkSessionCreated = false;
        List<Map<String, Object>> reads            = new ArrayList<>();
        List<Map<String, Object>> writes           = new ArrayList<>();
        List<Map<String, Object>> unresolvedReads  = new ArrayList<>();
        List<Map<String, Object>> unresolvedWrites = new ArrayList<>();
        LinkedHashSet<String> tableRefs = new LinkedHashSet<>();
        LinkedHashSet<String> colRefs   = new LinkedHashSet<>();

        private final Map<String, Expression> vals;
        private final Map<String, Expression> defs;
        private final Map<String, String>     configPool;
        private final CompilationUnit         cu;

        FactsCollector(Map<String, Expression> vals,
                       Map<String, Expression> defs,
                       Map<String, String>     configPool,
                       CompilationUnit         cu) {
            this.vals       = vals;
            this.defs       = defs;
            this.configPool = configPool;
            this.cu         = cu;
        }

        private int lineOf(MethodCallExpr call) {
            return call.getBegin().map(pos -> pos.line).orElse(0);
        }

        private void emitRead(String call, Expression argExpr, int line) {
            List<String> resolved = resolveArg(argExpr, 0, vals, defs, configPool, cu);
            if (!resolved.isEmpty()) {
                reads.add(callJson(call, resolved, line));
            } else {
                String s = argExpr.toString();
                unresolvedReads.add(unresolvedJson("read", call,
                    s.substring(0, Math.min(200, s.length())), line));
            }
        }

        private void emitWrite(String call, Expression argExpr, int line) {
            List<String> resolved = resolveArg(argExpr, 0, vals, defs, configPool, cu);
            if (!resolved.isEmpty()) {
                writes.add(callJson(call, resolved, line));
            } else {
                String s = argExpr.toString();
                unresolvedWrites.add(unresolvedJson("write", call,
                    s.substring(0, Math.min(200, s.length())), line));
            }
        }

        private void emitTableRead(String call, Expression argExpr, int line) {
            List<String> resolved = resolveArg(argExpr, 0, vals, defs, configPool, cu);
            if (!resolved.isEmpty()) {
                reads.add(callJson(call, resolved, line));
                tableRefs.addAll(resolved);
            } else {
                String s = argExpr.toString();
                unresolvedReads.add(unresolvedJson("read", call,
                    s.substring(0, Math.min(200, s.length())), line));
            }
        }

        private void emitTableWrite(String call, Expression argExpr, int line) {
            List<String> resolved = resolveArg(argExpr, 0, vals, defs, configPool, cu);
            if (!resolved.isEmpty()) {
                writes.add(callJson(call, resolved, line));
                tableRefs.addAll(resolved);
            } else {
                String s = argExpr.toString();
                unresolvedWrites.add(unresolvedJson("write", call,
                    s.substring(0, Math.min(200, s.length())), line));
            }
        }

        @Override
        public void visit(MethodCallExpr call, Void arg) {
            super.visit(call, arg);
            String methodName = call.getNameAsString();
            List<Expression> allArgs = call.getArguments();
            // string-literal args for column-ref collection (no resolver needed for col names)
            List<String> strArgs = allArgs.stream()
                .filter(a -> a instanceof StringLiteralExpr)
                .map(a -> ((StringLiteralExpr) a).getValue())
                .collect(Collectors.toList());
            String scopeStr = call.getScope().map(Object::toString).orElse("");
            int line = lineOf(call);

            // SparkSession.builder()....getOrCreate()
            if ("getOrCreate".equals(methodName) && scopeStr.contains("SparkSession")) {
                sparkSessionCreated = true;
            }

            // saveAsTable / insertInto → table write
            if ("saveAsTable".equals(methodName) || "insertInto".equals(methodName)) {
                if (!allArgs.isEmpty()) emitTableWrite(methodName, allArgs.get(0), line);
            }
            // .save([path]) → write
            else if ("save".equals(methodName) && scopeStr.contains("write")) {
                if (!allArgs.isEmpty()) emitWrite(methodName, allArgs.get(0), line);
                else writes.add(callJson(methodName, Collections.emptyList(), line));
            }
            // .write().parquet/csv/json/orc/text(path) → write
            else if (FMT_TERMINALS.contains(methodName) && scopeStr.contains("write")) {
                if (!allArgs.isEmpty()) emitWrite(methodName, allArgs.get(0), line);
            }
            // spark.catalog.createTable / createExternalTable → table write
            else if (("createTable".equals(methodName) || "createExternalTable".equals(methodName))
                     && scopeStr.contains("catalog")) {
                if (!allArgs.isEmpty()) emitTableWrite(methodName, allArgs.get(0), line);
            }
            // spark.table("x") → table read
            else if ("table".equals(methodName) && looksLikeSparkScope(scopeStr)) {
                if (!allArgs.isEmpty()) emitTableRead(methodName, allArgs.get(0), line);
            }
            // DeltaTable.forPath / forName → read (SECOND arg is path/name when 2+ args)
            else if (("forPath".equals(methodName) || "forName".equals(methodName))
                     && scopeStr.contains("DeltaTable")) {
                if (allArgs.size() >= 2)    emitRead("DeltaTable." + methodName, allArgs.get(1), line);
                else if (!allArgs.isEmpty()) emitRead("DeltaTable." + methodName, allArgs.get(0), line);
            }
            // spark.read.jdbc(url, table, ...) → SECOND arg is table name
            else if ("jdbc".equals(methodName) && scopeStr.contains("read")) {
                if (allArgs.size() >= 2)    emitRead("jdbc", allArgs.get(1), line);
                else if (!allArgs.isEmpty()) emitRead("jdbc", allArgs.get(0), line);
            }
            // SparkContext RDD reads: sc.textFile / wholeTextFiles / etc.
            else if (SC_READ_METHODS.contains(methodName) && looksLikeSparkContextScope(scopeStr)) {
                if (!allArgs.isEmpty()) emitRead(methodName, allArgs.get(0), line);
            }
            // spark.read.{parquet,csv,json,orc,text,textFile,load,table}(path)
            else if (READ_TERMINALS.contains(methodName) && scopeStr.contains("read")) {
                if (!allArgs.isEmpty()) emitRead(methodName, allArgs.get(0), line);
            }
            // col("x") / column("x") → column refs
            else if ("col".equals(methodName) || "column".equals(methodName)) {
                colRefs.addAll(strArgs);
            }
            // DataFrame column methods: .select("a","b") / .groupBy("k") etc.
            else if (COL_METHODS.contains(methodName)) {
                colRefs.addAll(strArgs);
            }
        }

        private boolean looksLikeSparkScope(String scope) {
            return scope.contains("spark") || scope.contains("session")
                || scope.contains("Spark") || scope.contains("Session");
        }

        private boolean looksLikeSparkContextScope(String scope) {
            return "sc".equals(scope)
                || scope.endsWith(".sc")
                || scope.contains("sparkContext");
        }
    }
}

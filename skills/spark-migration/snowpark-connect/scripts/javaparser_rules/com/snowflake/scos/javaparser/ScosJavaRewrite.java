// com.snowflake.scos.javaparser.ScosJavaRewrite
//
// Java AST rewrite/annotate rules for the SCOS Spark-Java → Snowpark Connect
// migration skill (Phase 0.5c).  Parity with SCOSRules.scala / scos.scalafix.conf.
//
// Rules (14, mirrors the Scala list in scos.scalafix.conf):
//   ScosSparkSessionBuilderRewrite         drop .master/.enableHiveSupport/.remote, emit PRESERVED-CONFIG markers
//   ScosCheckpointToCache                  .checkpoint/.localCheckpoint → .cache + EWI comment
//   ScosMapSubscriptToElementAt            col.getItem(key) → element_at(col, key)
//   ScosWildcardReadAnnotate               wildcard path in spark.read.*("path/*")
//   ScosSaveAsTableDropStorageOpts         drop .format()/.option("path",..) from saveAsTable chains
//   ScosExternalCloudReadAnnotate          cloud-URI read → perf-hint comment
//   ScosSelfJoinUnaliasedAnnotate          df.join(df, ...) → alias reminder
//   ScosSparkContextPropertyFallbackAnnotate sc.parallelize / sc.broadcast warn
//   ScosUdtfCompatibilityModeAnnotate      UDTF class → compat-mode reminder
//   ScosUnionByNameAllowMissingAnnotate    .unionByName(other, true) → schema-align reminder
//   ScosDriverHotPathAnnotate              .collect()/.toLocalIterator() inside loop → perf tip
//   ScosTempViewMultiUseCache              multi-use temp view → insert recv.cache()
//   ScosSystemGetenvRewrite                System.getenv(k) → System.getProperty(k)
//   ScosDeltaTableAnnotate                 DeltaTable.forPath/forName/forUid/forAddress → SPRKCNTSCL1000 EWI
//
// Ported from Scalafix for Java↔Scala parity (SNOW-3715354) — 19 rules:
//   ScosApproxCountDistinctDropRsd         approxCountDistinct(col, rsd) → approxCountDistinct(col)
//   ScosDbUtilsSecretsGetStub              dbutils.secrets.get/getBytes → (String) null + TODO
//   ScosDbUtilsWidgetsToProperty           dbutils.widgets.* → System.get/setProperty
//   ScosDeltaWriteToParquet                .write.format("delta") → .format("parquet")
//   ScosDisplayToShow                      display(df) → df.show()
//   ScosDisplayMethodToShow                df.display() → df.show()
//   ScosHadoopConfCredentialAnnotate       hadoopConfiguration().set(fs.s3...) → TODO
//   ScosPartitionNoopStrip                 drop no-op .coalesce()/.repartition()
//   ScosRddExclusiveMethodAnnotate         reduceByKey/mapPartitions/... → TODO
//   ScosRddImportAnnotate                  import org.apache.spark.rdd/api.java.Java*RDD → TODO
//   ScosRddPersistToCache                  df.rdd().persist() → df.persist()
//   ScosScTextfileToReadText               sc.textFile(p) → spark.read().text(p)
//   ScosScWholeTextFilesAnnotate           sc.wholeTextFiles(p) → TODO
//   ScosSnowflakeConnectorIO               .format("snowflake") read/write → SCOS-native
//   ScosSparkConfigNoopAnnotate            spark.conf().set(spark.executor...) → TODO
//   ScosSparkContextGetOrCreateRewrite     SparkContext.getOrCreate() → SnowparkConnectSession
//   ScosSparkContextNoopCommentOut         sc.stop()/close()/setLogLevel() → block comment
//   ScosSparkIoDetectAnnotate              jdbc/iceberg/table I/O → SPRKCNTSCL6000 / 3200
//   ScosUnpersistDropBlockingArg           df.unpersist(true) → df.unpersist()
//
// Intentionally NOT ported (Scala-only language features, no Java analogue):
//   ScosSqlContextImplicitsRewrite  — Scala implicit conversions; Java has no `import x.implicits._`
//   ScosScRangeToSparkRange         — sc.range() is a Scala SparkContext API absent from JavaSparkContext
//
// CLI:
//   --source <file>         Java file to process
//   --rule   <RuleName>     Name of rule to apply (case-sensitive)
//   --stdout                (optional, output always goes to stdout regardless)
//   --list-rules            Print all rule names and exit
//
// Exit 0 always.  If the file does not parse or the rule does not match,
// the original source is printed unchanged (no-op), mirroring scalafix --stdout.
//
// Marker conventions (must stay in sync with verify_phase.py):
//   // SCOS-RECIPE-PRESERVED-CONFIG: k=v          (ScosSparkSessionBuilderRewrite)
//   // SCOS: [SPRKCNTSCL<code>] <message>         (EWI-style annotation)
//   // SCOS: TODO - <message>                     (action required)
//   // SCOS: Performance tip - <message>           (perf hint)
//   // SCOS-WARN: <message>                        (non-extractable config warning)
package com.snowflake.scos.javaparser;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.expr.BooleanLiteralExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.StringLiteralExpr;
import com.github.javaparser.ast.stmt.DoStmt;
import com.github.javaparser.ast.stmt.ForEachStmt;
import com.github.javaparser.ast.stmt.ForStmt;
import com.github.javaparser.ast.stmt.WhileStmt;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

public class ScosJavaRewrite {

    // ── Canonical rule list (must stay in sync with scos.scalafix.conf) ──────
    static final List<String> ALL_RULES = Arrays.asList(
        "ScosSparkSessionBuilderRewrite",
        "ScosCheckpointToCache",
        "ScosMapSubscriptToElementAt",
        "ScosWildcardReadAnnotate",
        "ScosSaveAsTableDropStorageOpts",
        "ScosExternalCloudReadAnnotate",
        "ScosSelfJoinUnaliasedAnnotate",
        "ScosSparkContextPropertyFallbackAnnotate",
        "ScosUdtfCompatibilityModeAnnotate",
        "ScosUnionByNameAllowMissingAnnotate",
        "ScosDriverHotPathAnnotate",
        "ScosTempViewMultiUseCache",
        "ScosSystemGetenvRewrite",
        "ScosDeltaTableAnnotate",
        // ── Ported from Scalafix (SNOW-3715354) ──
        "ScosApproxCountDistinctDropRsd",
        "ScosDbUtilsSecretsGetStub",
        "ScosDbUtilsWidgetsToProperty",
        "ScosDeltaWriteToParquet",
        "ScosDisplayToShow",
        "ScosDisplayMethodToShow",
        "ScosHadoopConfCredentialAnnotate",
        "ScosPartitionNoopStrip",
        "ScosRddExclusiveMethodAnnotate",
        "ScosRddImportAnnotate",
        "ScosRddPersistToCache",
        "ScosScTextfileToReadText",
        "ScosScWholeTextFilesAnnotate",
        "ScosSnowflakeConnectorIO",
        "ScosSparkConfigNoopAnnotate",
        "ScosSparkContextGetOrCreateRewrite",
        "ScosSparkContextNoopCommentOut",
        "ScosSparkIoDetectAnnotate",
        "ScosUnpersistDropBlockingArg"
    );

    // ── Entry point ───────────────────────────────────────────────────────────

    public static void main(String[] args) throws Exception {
        String source = "", rule = "";
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--source": if (i + 1 < args.length) source = args[++i]; break;
                case "--rule":   if (i + 1 < args.length) rule   = args[++i]; break;
                case "--stdout": break; // always stdout; flag accepted for compat
                case "--list-rules":
                    ALL_RULES.forEach(System.out::println);
                    return;
                default: break;
            }
        }
        if (source.isEmpty() || rule.isEmpty()) {
            System.err.println("ScosJavaRewrite: --source <file> --rule <RuleName> [--stdout]");
            System.exit(2);
        }
        Path filePath = Paths.get(source).toAbsolutePath().normalize();
        String code   = new String(Files.readAllBytes(filePath), StandardCharsets.UTF_8);
        String result = applyRule(code, filePath.toString(), rule);
        // Always print full file to stdout — mirrors scalafix --stdout semantics
        System.out.print(result);
    }

    /** Apply a single named rule. Returns original source if rule doesn't match or parse fails. */
    static String applyRule(String source, String filePath, String rule) {
        switch (rule) {
            case "ScosSparkSessionBuilderRewrite":
                return ruleSparkSessionBuilder(source, filePath);
            case "ScosCheckpointToCache":
                return ruleCheckpointToCache(source, filePath);
            case "ScosMapSubscriptToElementAt":
                return ruleMapSubscriptToElementAt(source, filePath);
            case "ScosWildcardReadAnnotate":
                return ruleWildcardReadAnnotate(source, filePath);
            case "ScosSaveAsTableDropStorageOpts":
                return ruleSaveAsTableDropStorageOpts(source, filePath);
            case "ScosExternalCloudReadAnnotate":
                return ruleExternalCloudReadAnnotate(source, filePath);
            case "ScosSelfJoinUnaliasedAnnotate":
                return ruleSelfJoinUnaliasedAnnotate(source, filePath);
            case "ScosSparkContextPropertyFallbackAnnotate":
                return ruleSparkContextPropertyFallback(source, filePath);
            case "ScosUdtfCompatibilityModeAnnotate":
                return ruleUdtfCompatibilityMode(source, filePath);
            case "ScosUnionByNameAllowMissingAnnotate":
                return ruleUnionByNameAllowMissing(source, filePath);
            case "ScosDriverHotPathAnnotate":
                return ruleDriverHotPath(source, filePath);
            case "ScosTempViewMultiUseCache":
                return ruleTempViewMultiUseCache(source, filePath);
            case "ScosSystemGetenvRewrite":
                return ruleSystemGetenvRewrite(source, filePath);
            case "ScosDeltaTableAnnotate":
                return ruleDeltaTableAnnotate(source, filePath);
            // ── Ported from Scalafix (SNOW-3715354) ──
            case "ScosApproxCountDistinctDropRsd":
                return ruleApproxCountDistinctDropRsd(source, filePath);
            case "ScosDbUtilsSecretsGetStub":
                return ruleDbUtilsSecretsGetStub(source, filePath);
            case "ScosDbUtilsWidgetsToProperty":
                return ruleDbUtilsWidgetsToProperty(source, filePath);
            case "ScosDeltaWriteToParquet":
                return ruleDeltaWriteToParquet(source, filePath);
            case "ScosDisplayToShow":
                return ruleDisplayToShow(source, filePath);
            case "ScosDisplayMethodToShow":
                return ruleDisplayMethodToShow(source, filePath);
            case "ScosHadoopConfCredentialAnnotate":
                return ruleHadoopConfCredentialAnnotate(source, filePath);
            case "ScosPartitionNoopStrip":
                return rulePartitionNoopStrip(source, filePath);
            case "ScosRddExclusiveMethodAnnotate":
                return ruleRddExclusiveMethodAnnotate(source, filePath);
            case "ScosRddImportAnnotate":
                return ruleRddImportAnnotate(source, filePath);
            case "ScosRddPersistToCache":
                return ruleRddPersistToCache(source, filePath);
            case "ScosScTextfileToReadText":
                return ruleScTextfileToReadText(source, filePath);
            case "ScosScWholeTextFilesAnnotate":
                return ruleScWholeTextFilesAnnotate(source, filePath);
            case "ScosSnowflakeConnectorIO":
                return ruleSnowflakeConnectorIO(source, filePath);
            case "ScosSparkConfigNoopAnnotate":
                return ruleSparkConfigNoopAnnotate(source, filePath);
            case "ScosSparkContextGetOrCreateRewrite":
                return ruleSparkContextGetOrCreateRewrite(source, filePath);
            case "ScosSparkContextNoopCommentOut":
                return ruleSparkContextNoopCommentOut(source, filePath);
            case "ScosSparkIoDetectAnnotate":
                return ruleSparkIoDetectAnnotate(source, filePath);
            case "ScosUnpersistDropBlockingArg":
                return ruleUnpersistDropBlockingArg(source, filePath);
            default:
                System.err.println("[scos-java-rewrite] unknown rule: " + rule);
                return source;
        }
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 1: ScosSparkSessionBuilderRewrite
    //
    // Finds SparkSession.builder()…getOrCreate() chains and, for non-test files:
    //   • Drops .master(...), .enableHiveSupport(), .remote(...) from the chain
    //   • Emits // SCOS-RECIPE-PRESERVED-CONFIG: k=v markers for each .config(k,v)
    //   • Emits // SCOS-WARN: for non-extractable .config() forms
    //
    // Test files (name ends with Test/Spec/Suite/IT.java) are left untouched —
    // only markers are emitted so local integration harnesses keep master("local[*]").
    //
    // Idempotent: after the rewrite the chain no longer contains the dropped calls,
    // so a second pass is a no-op.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String MARKER_PREFIX = "// SCOS-RECIPE-PRESERVED-CONFIG: ";
    private static final String WARN_NON_EXTRACTABLE =
        "// SCOS-WARN: dropped non-extractable .config(...) \u2014 manual review required";

    private static String ruleSparkSessionBuilder(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        String normalizedPath = filePath.replace('\\', '/');
        // Keep in sync with update_imports_java.py _TEST_FILE_RE and
        // verify_phase.py _TEST_JAVA_FILE_RE. "Tests.java" was missing here, so a
        // FooTests.java had its session rewritten while the Python side skipped it.
        boolean isTest = filePath.endsWith("Test.java") || filePath.endsWith("Tests.java")
                || filePath.endsWith("Spec.java")
                || filePath.endsWith("Suite.java") || filePath.endsWith("IT.java")
                || normalizedPath.contains("/src/test/") || normalizedPath.contains("/test/");

        List<RangeEdit> edits = new ArrayList<>();
        int[] lineOffsets = buildLineOffsets(source);

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("getOrCreate")) return;
                Optional<Expression> scopeOpt = mce.getScope();
                if (!scopeOpt.isPresent()) return;
                Expression inner = scopeOpt.get();
                String innerStr = inner.toString();
                if (!innerStr.contains("SparkSession") || !innerStr.contains("builder")) return;
                if (!mce.getRange().isPresent()) return;

                // Collect config pairs from the chain
                List<String[]> configs = new ArrayList<>();
                boolean[] hasNonExtractable = {false};
                collectConfigs(inner, configs, hasNonExtractable);

                // Build marker lines
                List<String> markerLines = new ArrayList<>();
                for (String[] kv : configs) {
                    markerLines.add(MARKER_PREFIX + kv[0] + "=" + kv[1]);
                }
                if (hasNonExtractable[0]) markerLines.add(WARN_NON_EXTRACTABLE);
                String markerText = markerLines.isEmpty() ? ""
                        : String.join("\n", markerLines) + "\n";

                // Build indent from the line where the chain begins
                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));

                String rebuiltChain;
                if (!isTest && chainNeedsRewrite(inner)) {
                    rebuiltChain = markerText + indent
                            + rebuildBuilderChain(inner, source, lineOffsets)
                            + ".getOrCreate()";
                } else {
                    // Test file or already-rewritten: prepend markers only
                    if (markerText.isEmpty()) return;
                    String originalCall = getSourceRange(source, lineOffsets, range);
                    rebuiltChain = markerText + indent + originalCall;
                }

                edits.add(new RangeEdit(range, rebuiltChain));
            }
        }, null);

        return applyEdits(source, edits);
    }

    /** Recursively collect .config(k,v) pairs from a builder scope chain. */
    private static void collectConfigs(Expression chain, List<String[]> result,
                                       boolean[] hasNonExtractable) {
        if (!(chain instanceof MethodCallExpr)) return;
        MethodCallExpr mce = (MethodCallExpr) chain;
        // Recurse first (left-to-right order)
        mce.getScope().ifPresent(s -> collectConfigs(s, result, hasNonExtractable));
        if (!mce.getNameAsString().equals("config")) return;
        NodeList<Expression> args = mce.getArguments();
        if (args.size() == 2) {
            String k = literalOrSyntax(args.get(0));
            String v = literalOrSyntax(args.get(1));
            result.add(new String[]{k, v});
        } else {
            hasNonExtractable[0] = true;
        }
    }

    private static boolean chainNeedsRewrite(Expression tree) {
        if (!(tree instanceof MethodCallExpr)) return false;
        MethodCallExpr mce = (MethodCallExpr) tree;
        String name = mce.getNameAsString();
        if (name.equals("master") || name.equals("enableHiveSupport") || name.equals("remote"))
            return true;
        if (tree.toString().contains("SparkSession")) return true;
        return mce.getScope().map(ScosJavaRewrite::chainNeedsRewrite).orElse(false);
    }

    /** Rebuild the builder chain dropping master/enableHiveSupport/remote. */
    private static String rebuildBuilderChain(Expression chain,
                                               String source, int[] lineOffsets) {
        if (!(chain instanceof MethodCallExpr)) return chain.toString();
        MethodCallExpr mce = (MethodCallExpr) chain;
        String name = mce.getNameAsString();
        Set<String> drop = new HashSet<>(Arrays.asList("master", "enableHiveSupport", "remote"));
        // Rename SparkSession → SnowparkConnectSession in the rebuilt chain
        return rebuildChain(chain, drop)
                .replaceFirst("\\bSparkSession\\b", "SnowparkConnectSession");
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 2: ScosCheckpointToCache
    //
    // Replaces .checkpoint(...) / .localCheckpoint(...) with .cache() and
    // inserts an EWI annotation comment above. Handles multi-line chains.
    // ═════════════════════════════════════════════════════════════════════════

    private static String ruleCheckpointToCache(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String name = mce.getNameAsString();
                if (!name.equals("checkpoint") && !name.equals("localCheckpoint")) return;
                if (!mce.getRange().isPresent()) return;
                if (!mce.getScope().isPresent()) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String scopeText = getSourceRange(source, lineOffsets, mce.getScope().get().getRange().get());
                String comment = "// SCOS: [SPRKCNTSCL1000] " + name
                        + "() not available in Snowpark Connect \u2014 replaced with cache()";
                String replacement = comment + "\n" + indent + scopeText + ".cache()";
                edits.add(new RangeEdit(range, replacement));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 3: ScosMapSubscriptToElementAt
    //
    // Rewrites col.getItem(key) → element_at(col, key).
    // In Java Spark, Column.getItem() is the analog of Scala's mapCol(col("k"))
    // map-subscript syntax. element_at() works for both arrays and maps.
    // ═════════════════════════════════════════════════════════════════════════

    private static String ruleMapSubscriptToElementAt(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("getItem")) return;
                if (!mce.getScope().isPresent()) return;
                if (!mce.getRange().isPresent()) return;
                if (mce.getArguments().size() != 1) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String scopeText = getSourceRange(source, lineOffsets,
                        mce.getScope().get().getRange().get());
                String argText = getSourceRange(source, lineOffsets,
                        mce.getArguments().get(0).getRange().get());
                String replacement = "element_at(" + scopeText + ", " + argText + ")";
                edits.add(new RangeEdit(range, replacement));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 4: ScosWildcardReadAnnotate
    //
    // Inserts a TODO comment above spark.read().<fmt>("path/*") or
    // spark.read().format("csv").load("path/*") calls whose path contains *.
    // ═════════════════════════════════════════════════════════════════════════

    private static final Set<String> READ_FORMATS =
        new HashSet<>(Arrays.asList("csv", "json", "parquet", "text", "load", "orc", "avro"));
    private static final String WILDCARD_TODO =
        "// SCOS: TODO - wildcard pattern in path; replace with explicit file list.";
    private static final String WILDCARD_INTERPOLATED_TODO =
        "// SCOS: TODO - verify interpolated path contains no wildcard; replace with explicit file list if so.";

    private static String ruleWildcardReadAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String name = mce.getNameAsString();
                if (!READ_FORMATS.contains(name)) return;
                // Require scope to be a no-arg read() call on a simple receiver to
                // avoid false-positives like myReadHelper.csv("data/*.csv").
                Expression scopeExprW = mce.getScope().orElse(null);
                if (!(scopeExprW instanceof MethodCallExpr)) return;
                MethodCallExpr readCallW = (MethodCallExpr) scopeExprW;
                if (!readCallW.getNameAsString().equals("read")) return;
                if (!readCallW.getArguments().isEmpty()) return;
                if (!readCallW.getScope().isPresent() || !(readCallW.getScope().get() instanceof NameExpr)) return;
                if (!mce.getBegin().isPresent()) return;
                int line = mce.getBegin().get().line;

                // Check string literal args for wildcard
                for (Expression a : mce.getArguments()) {
                    if (a instanceof StringLiteralExpr) {
                        if (((StringLiteralExpr) a).asString().contains("*")) {
                            annots.add(new AnnotEntry(line, WILDCARD_TODO));
                            return;
                        }
                    }
                }
                // Conservatively flag interpolated strings (Java ternary / concat expressions)
                boolean hasComplexArg = mce.getArguments().stream()
                        .anyMatch(a -> !(a instanceof StringLiteralExpr) &&
                                (a.toString().contains("+") || a.toString().contains("format(")));
                if (hasComplexArg) {
                    annots.add(new AnnotEntry(line, WILDCARD_INTERPOLATED_TODO));
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 5: ScosSaveAsTableDropStorageOpts
    //
    // Drops unsupported .format(...) and .option("path", …) from a
    // DataFrameWriter chain that ends in .saveAsTable(...).
    // Adds a comment noting the drop.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String SAVEASTABLE_COMMENT =
        "// SCOS: dropped unsupported .format()/.option(\"path\", \u2026) from saveAsTable chain"
        + " (Snowpark Connect manages table storage internally)";

    private static String ruleSaveAsTableDropStorageOpts(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("saveAsTable")) return;
                if (!mce.getScope().isPresent()) return;
                if (!mce.getRange().isPresent()) return;
                if (!chainHasStorageDrop(mce.getScope().get())) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String argsText = mce.getArguments().stream()
                        .map(Expression::toString)
                        .collect(Collectors.joining(", "));
                String newScope = rebuildChain(mce.getScope().get(),
                        new HashSet<>(Collections.singletonList("format")));
                // Also drop option("path", ...) from newScope — handled in rebuildChain via isStorageDrop
                String replacement = SAVEASTABLE_COMMENT + "\n"
                        + indent + rebuildDropStorageChain(mce.getScope().get())
                        + ".saveAsTable(" + argsText + ")";
                edits.add(new RangeEdit(range, replacement));
            }
        }, null);

        return applyEdits(source, edits);
    }

    private static boolean chainHasStorageDrop(Expression e) {
        if (!(e instanceof MethodCallExpr)) return false;
        MethodCallExpr mce = (MethodCallExpr) e;
        if (isStorageDrop(mce)) return true;
        return mce.getScope().map(ScosJavaRewrite::chainHasStorageDrop).orElse(false);
    }

    private static boolean isStorageDrop(MethodCallExpr mce) {
        String name = mce.getNameAsString();
        if (name.equals("format")) return true;
        if (name.equals("option") && mce.getArguments().size() >= 1) {
            Expression first = mce.getArguments().get(0);
            if (first instanceof StringLiteralExpr &&
                    ((StringLiteralExpr) first).asString().equals("path")) return true;
        }
        return false;
    }

    private static String rebuildDropStorageChain(Expression chain) {
        if (!(chain instanceof MethodCallExpr)) return chain.toString();
        MethodCallExpr mce = (MethodCallExpr) chain;
        if (isStorageDrop(mce)) {
            return mce.getScope().map(ScosJavaRewrite::rebuildDropStorageChain).orElse("");
        }
        String scope = mce.getScope().map(ScosJavaRewrite::rebuildDropStorageChain).orElse("");
        String argsText = mce.getArguments().stream()
                .map(Expression::toString)
                .collect(Collectors.joining(", "));
        return (scope.isEmpty() ? "" : scope + ".") + mce.getNameAsString() + "(" + argsText + ")";
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 6: ScosExternalCloudReadAnnotate
    //
    // Adds a performance-hint comment above spark.read().<fmt>("s3://…") reads
    // (s3/gs/abfss/wasbs/…) recommending migration to a Snowflake stage.
    // ═════════════════════════════════════════════════════════════════════════

    private static final Set<String> CLOUD_SCHEMES = new HashSet<>(Arrays.asList(
        "s3", "s3a", "gs", "gcs", "abfs", "abfss", "wasb", "wasbs", "azure", "adl", "oss", "oci"
    ));

    private static String ruleExternalCloudReadAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String name = mce.getNameAsString();
                if (!READ_FORMATS.contains(name) && !name.equals("load")) return;
                // Require scope to be a no-arg read() call on a simple receiver to
                // avoid false-positives like myReadHelper.csv("s3://...").
                Expression scopeExprC = mce.getScope().orElse(null);
                if (!(scopeExprC instanceof MethodCallExpr)) return;
                MethodCallExpr readCallC = (MethodCallExpr) scopeExprC;
                if (!readCallC.getNameAsString().equals("read")) return;
                if (!readCallC.getArguments().isEmpty()) return;
                if (!readCallC.getScope().isPresent() || !(readCallC.getScope().get() instanceof NameExpr)) return;
                if (!mce.getBegin().isPresent()) return;

                for (Expression a : mce.getArguments()) {
                    if (a instanceof StringLiteralExpr) {
                        String path = ((StringLiteralExpr) a).asString();
                        String scheme = cloudScheme(path);
                        if (scheme != null) {
                            annots.add(new AnnotEntry(mce.getBegin().get().line,
                                "// SCOS: Performance tip - " + scheme
                                + " read; consider migrating to a Snowflake stage for best performance"));
                            return;
                        }
                    }
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    private static String cloudScheme(String path) {
        int idx = path.indexOf("://");
        if (idx <= 0) return null;
        String s = path.substring(0, idx).toLowerCase();
        return CLOUD_SCHEMES.contains(s) ? s : null;
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 7: ScosSelfJoinUnaliasedAnnotate
    //
    // Annotates df.join(df, …) where the bare receiver name equals the bare
    // first-argument name — unaliased self-join → ambiguous column refs.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String SELF_JOIN_COMMENT =
        "// SCOS: TODO - self-join requires explicit aliases (e.g., df.alias(\"a\").join(df.alias(\"b\"), ...))";

    private static String ruleSelfJoinUnaliasedAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("join")) return;
                if (!mce.getScope().isPresent()) return;
                if (mce.getArguments().isEmpty()) return;
                if (!mce.getBegin().isPresent()) return;

                String recv = mce.getScope().get() instanceof NameExpr
                        ? ((NameExpr) mce.getScope().get()).getNameAsString() : null;
                String firstArg = mce.getArguments().get(0) instanceof NameExpr
                        ? ((NameExpr) mce.getArguments().get(0)).getNameAsString() : null;
                if (recv != null && recv.equals(firstArg)) {
                    annots.add(new AnnotEntry(mce.getBegin().get().line, SELF_JOIN_COMMENT));
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 8: ScosSparkContextPropertyFallbackAnnotate
    //
    // Annotates sc.parallelize(...) / sc.broadcast(...) and
    // spark.sparkContext().parallelize(...) forms — unsupported/limited in
    // Spark Connect / Snowpark Connect.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String PARALLELIZE_COMMENT =
        "// SCOS: [SPRKCNTSCL1500] sc.parallelize is unsupported in Snowpark Connect. "
        + "Convert to createDataFrame(javaList, schema) \u2014 use List<Row> with a schema, "
        + "or List<Object[]>. Do NOT nest createDataFrame calls.";
    private static final String BROADCAST_COMMENT =
        "// SCOS: TODO - sc.broadcast not supported; "
        + "pass value directly or use spark.sparkContext().broadcast (limited)";

    private static String ruleSparkContextPropertyFallback(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String name = mce.getNameAsString();
                if (!name.equals("parallelize") && !name.equals("broadcast")) return;
                if (!mce.getScope().isPresent()) return;
                if (!mce.getBegin().isPresent()) return;

                Expression scope = mce.getScope().get();
                boolean isSc = (scope instanceof NameExpr &&
                        ((NameExpr) scope).getNameAsString().equals("sc"))
                        || scope.toString().contains("sparkContext");
                if (!isSc) return;

                String comment = name.equals("parallelize") ? PARALLELIZE_COMMENT : BROADCAST_COMMENT;
                annots.add(new AnnotEntry(mce.getBegin().get().line, comment));
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 9: ScosUdtfCompatibilityModeAnnotate
    //
    // Annotates any class implementing UserDefinedTableFunction / GenericUDTF
    // with the required per-session compatibility-mode flag reminder.
    // ═════════════════════════════════════════════════════════════════════════

    private static final Set<String> UDTF_BASES =
        new HashSet<>(Arrays.asList(
            "UserDefinedTableFunction", "GenericUDTF",
            "UserDefinedAggregateFunction", "Aggregator",
            "UDF1",  "UDF2",  "UDF3",  "UDF4",  "UDF5",
            "UDF6",  "UDF7",  "UDF8",  "UDF9",  "UDF10",
            "UDF11", "UDF12", "UDF13", "UDF14", "UDF15",
            "UDF16", "UDF17", "UDF18", "UDF19", "UDF20",
            "UDF21", "UDF22"));
    private static final String UDTF_COMMENT =
        "// SCOS: TODO - UDF/UDTF compatibility mode may be required; "
        + "set spark.sql.execution.udtf.compatibility.mode=true if UDF returns unexpected results";

    private static String ruleUdtfCompatibilityMode(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(ClassOrInterfaceDeclaration cid, Void arg) {
                super.visit(cid, arg);
                if (!cid.getBegin().isPresent()) return;
                boolean extendsUdtf = cid.getImplementedTypes().stream()
                        .map(ClassOrInterfaceType::getNameAsString)
                        .anyMatch(UDTF_BASES::contains);
                if (!extendsUdtf) {
                    extendsUdtf = cid.getExtendedTypes().stream()
                            .map(ClassOrInterfaceType::getNameAsString)
                            .anyMatch(UDTF_BASES::contains);
                }
                if (extendsUdtf) {
                    annots.add(new AnnotEntry(cid.getBegin().get().line, UDTF_COMMENT));
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 10: ScosUnionByNameAllowMissingAnnotate
    //
    // Annotates .unionByName(other, true) — Java Spark API uses a boolean
    // parameter (no named arguments), so we match on the 2-arg form where
    // the second argument is a boolean literal true.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String UNION_BY_NAME_COMMENT =
        "// SCOS: TODO - schema-align before unionByName; "
        + "allowMissingColumns may behave differently on SCOS";

    private static String ruleUnionByNameAllowMissing(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("unionByName")) return;
                if (!mce.getBegin().isPresent()) return;
                NodeList<Expression> args = mce.getArguments();
                // Match .unionByName(other, true) — 2-arg form
                if (args.size() == 2 && args.get(1) instanceof BooleanLiteralExpr
                        && ((BooleanLiteralExpr) args.get(1)).getValue()) {
                    annots.add(new AnnotEntry(mce.getBegin().get().line, UNION_BY_NAME_COMMENT));
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 11: ScosDriverHotPathAnnotate
    //
    // Annotates .collect() / .toLocalIterator() / .collectAsList() calls
    // that sit inside a loop (enclosing-scope hot path check, not heuristic).
    // ═════════════════════════════════════════════════════════════════════════

    private static final Set<String> MATERIALIZERS =
        new HashSet<>(Arrays.asList("collect", "toLocalIterator", "collectAsList"));
    private static final String HOTPATH_COMMENT =
        "// SCOS: Performance tip - driver materialization in hot path; consider .show() or write-to-table";

    private static String ruleDriverHotPath(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!MATERIALIZERS.contains(mce.getNameAsString())) return;
                if (!mce.getBegin().isPresent()) return;
                if (!mce.getArguments().isEmpty()) return; // .collect() takes no args
                if (isInsideLoop(mce)) {
                    annots.add(new AnnotEntry(mce.getBegin().get().line, HOTPATH_COMMENT));
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 12: ScosTempViewMultiUseCache
    //
    // Inserts recv.cache(); before recv.createOrReplaceTempView("v") when the
    // view name appears in ≥2 FROM clauses across the file's SQL string literals.
    // Skips when recv is already cached earlier in the same source (idempotent).
    // ═════════════════════════════════════════════════════════════════════════

    private static String ruleTempViewMultiUseCache(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        // Collect all string literals for FROM-count analysis
        List<String> allStrings = new ArrayList<>();
        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(StringLiteralExpr sle, Void arg) {
                super.visit(sle, arg);
                allStrings.add(sle.asString());
            }
        }, null);

        List<AnnotEntry> insertions = new ArrayList<>();
        final int[] lineOffsets = buildLineOffsets(source);

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("createOrReplaceTempView")) return;
                if (!mce.getScope().isPresent()) return;
                if (!mce.getBegin().isPresent()) return;
                if (mce.getArguments().size() != 1) return;
                Expression firstArg = mce.getArguments().get(0);
                if (!(firstArg instanceof StringLiteralExpr)) return;

                String view = ((StringLiteralExpr) firstArg).asString();
                String recv = mce.getScope().get().toString();
                if (fromCount(view, allStrings) < 2) return;
                if (alreadyCached(source, recv, mce.getBegin().get().line)) return;

                int line = mce.getBegin().get().line;
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, line));
                insertions.add(new AnnotEntry(line,
                    "// SCOS: Performance tip - multi-use temp view '" + view + "'; .cache() inserted below\n"
                    + indent + recv + ".cache();"));
            }
        }, null);

        return insertAnnotations(source, insertions);
    }

    private static int fromCount(String view, List<String> strings) {
        Pattern pat = Pattern.compile("(?i)\\bFROM\\s+" + Pattern.quote(view) + "\\b");
        return (int) strings.stream().filter(s -> pat.matcher(s).find()).count();
    }

    private static boolean alreadyCached(String source, String recv, int beforeLine) {
        // Use a word-boundary regex to avoid substring false-positives (e.g. recv="df"
        // incorrectly matching "myDf.cache()").
        java.util.regex.Pattern cachePattern = java.util.regex.Pattern.compile(
            "(?<![\\w$])" + java.util.regex.Pattern.quote(recv) + "\\.(?:cache|persist)\\s*\\(");
        String[] lines = source.split("\n", -1);
        for (int i = 0; i < beforeLine - 1 && i < lines.length; i++) {
            if (cachePattern.matcher(lines[i]).find()) return true;
        }
        return false;
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 13: ScosSystemGetenvRewrite
    //
    // Rewrites System.getenv("KEY") → System.getProperty("KEY").
    // OS environment variables are not accessible in the Snowpark Connect
    // serverless runtime; System.getProperty reads JVM properties that can
    // be injected at session-start time via SparkSession.config().
    // ═════════════════════════════════════════════════════════════════════════

    private static String ruleSystemGetenvRewrite(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("getenv")) return;
                if (!mce.getScope().isPresent()) return;
                if (!mce.getRange().isPresent()) return;
                Expression scope = mce.getScope().get();
                if (!(scope instanceof NameExpr)) return;
                if (!((NameExpr) scope).getNameAsString().equals("System")) return;

                // Replace the full MethodCallExpr span, keeping args verbatim.
                com.github.javaparser.Range range = mce.getRange().get();
                String argsText = mce.getArguments().stream()
                        .map(a -> getSourceRange(source, lineOffsets, a.getRange().get()))
                        .collect(Collectors.joining(", "));
                edits.add(new RangeEdit(range, "System.getProperty(" + argsText + ")"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Rule 14: ScosDeltaTableAnnotate
    //
    // Inserts an EWI annotation comment above DeltaTable.forPath / forName /
    // forUid / forAddress call sites. The DeltaTable API is not available in
    // Snowpark Connect; usage must be removed or replaced.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String DELTA_TABLE_COMMENT =
        "// SCOS: TODO - [SPRKCNTSCL1000] DeltaTable API not available in Snowpark Connect; remove or replace";

    private static final Set<String> DELTA_TABLE_METHODS = new HashSet<>(Arrays.asList(
        "forPath", "forName", "forUid", "forAddress"
    ));

    private static String ruleDeltaTableAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!DELTA_TABLE_METHODS.contains(mce.getNameAsString())) return;
                if (!mce.getScope().isPresent()) return;
                if (!mce.getBegin().isPresent()) return;

                Expression scope = mce.getScope().get();
                boolean isDeltaTable = false;
                if (scope instanceof NameExpr) {
                    isDeltaTable = ((NameExpr) scope).getNameAsString().equals("DeltaTable");
                } else if (scope instanceof FieldAccessExpr) {
                    isDeltaTable = ((FieldAccessExpr) scope).getNameAsString().equals("DeltaTable");
                }
                if (!isDeltaTable) return;

                annots.add(new AnnotEntry(mce.getBegin().get().line, DELTA_TABLE_COMMENT));
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Shared helpers
    // ═════════════════════════════════════════════════════════════════════════

    /** Parse source tolerating failures; returns null on failure (rule becomes no-op). */
    private static CompilationUnit tryParse(String source) {
        ParserConfiguration config = new ParserConfiguration();
        config.setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_17);
        ParseResult<CompilationUnit> result = new JavaParser(config).parse(source);
        return (result.isSuccessful() && result.getResult().isPresent())
                ? result.getResult().get() : null;
    }

    /** True if node is lexically inside a for/while/do loop. */
    private static boolean isInsideLoop(Node node) {
        Optional<Node> parent = node.getParentNode();
        while (parent.isPresent()) {
            Node p = parent.get();
            if (p instanceof ForStmt || p instanceof ForEachStmt
                    || p instanceof WhileStmt || p instanceof DoStmt) return true;
            parent = p.getParentNode();
        }
        return false;
    }

    /** Recursively rebuild a method-call chain dropping the named methods. */
    private static String rebuildChain(Expression chain, Set<String> toDrop) {
        if (chain instanceof MethodCallExpr) {
            MethodCallExpr mce = (MethodCallExpr) chain;
            String name = mce.getNameAsString();
            if (toDrop.contains(name)) {
                return mce.getScope().map(s -> rebuildChain(s, toDrop)).orElse("");
            }
            String scope = mce.getScope().map(s -> rebuildChain(s, toDrop)).orElse("");
            String argsText = mce.getArguments().stream()
                    .map(Expression::toString)
                    .collect(Collectors.joining(", "));
            return (scope.isEmpty() ? "" : scope + ".") + name + "(" + argsText + ")";
        }
        if (chain instanceof FieldAccessExpr) {
            FieldAccessExpr fae = (FieldAccessExpr) chain;
            return rebuildChain(fae.getScope(), toDrop) + "." + fae.getNameAsString();
        }
        return chain.toString();
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Shared helpers for the Scalafix-ported rules (SNOW-3715354)
    // ═════════════════════════════════════════════════════════════════════════

    /** True when the expression denotes a SparkContext handle: `sc`,
     *  `jsc`, `X.sparkContext()` or `X.sparkContext` (Scala isSc analogue). */
    private static boolean isSparkContextRecv(Expression e) {
        if (e instanceof NameExpr) {
            String n = ((NameExpr) e).getNameAsString();
            return n.equals("sc") || n.equals("jsc") || n.equals("javaSparkContext");
        }
        if (e instanceof MethodCallExpr) {
            MethodCallExpr m = (MethodCallExpr) e;
            return m.getNameAsString().equals("sparkContext") && m.getArguments().isEmpty();
        }
        if (e instanceof FieldAccessExpr) {
            return ((FieldAccessExpr) e).getNameAsString().equals("sparkContext");
        }
        return false;
    }

    /** True when the receiver is the Spark `functions` module (functions.coalesce
     *  is a column function, NOT the DataFrame no-op). */
    private static boolean isFunctionsModule(Expression e) {
        if (e instanceof NameExpr) {
            String n = ((NameExpr) e).getNameAsString();
            return n.equals("F") || n.equals("f") || n.equals("functions");
        }
        if (e instanceof FieldAccessExpr) {
            return ((FieldAccessExpr) e).getNameAsString().equals("functions");
        }
        return false;
    }

    /** Walk up a call chain looking for .read()/.write()/.readStream()/.writeStream().
     *  Returns the role name, or "" when the chain has no I/O anchor. */
    private static String chainRole(Expression e) {
        Expression cur = e;
        while (cur != null) {
            if (cur instanceof MethodCallExpr) {
                MethodCallExpr m = (MethodCallExpr) cur;
                String n = m.getNameAsString();
                if (m.getArguments().isEmpty()
                        && (n.equals("read") || n.equals("write")
                            || n.equals("readStream") || n.equals("writeStream"))) {
                    return n;
                }
                cur = m.getScope().orElse(null);
            } else if (cur instanceof FieldAccessExpr) {
                FieldAccessExpr fa = (FieldAccessExpr) cur;
                String n = fa.getNameAsString();
                if (n.equals("read") || n.equals("write")
                        || n.equals("readStream") || n.equals("writeStream")) {
                    return n;
                }
                cur = fa.getScope();
            } else {
                return "";
            }
        }
        return "";
    }

    /** First .format("x") argument in the chain, lower-cased, or "". */
    private static String chainFormat(Expression e) {
        Expression cur = e;
        while (cur != null) {
            if (cur instanceof MethodCallExpr) {
                MethodCallExpr m = (MethodCallExpr) cur;
                if (m.getNameAsString().equals("format") && m.getArguments().size() == 1
                        && m.getArguments().get(0) instanceof StringLiteralExpr) {
                    return ((StringLiteralExpr) m.getArguments().get(0)).asString().toLowerCase();
                }
                cur = m.getScope().orElse(null);
            } else if (cur instanceof FieldAccessExpr) {
                cur = ((FieldAccessExpr) cur).getScope();
            } else {
                return "";
            }
        }
        return "";
    }

    /** Collect literal .option("k", v) pairs from a chain (last write wins). */
    private static java.util.Map<String, Expression> chainOptions(Expression e) {
        java.util.Map<String, Expression> out = new java.util.LinkedHashMap<>();
        Expression cur = e;
        while (cur != null) {
            if (cur instanceof MethodCallExpr) {
                MethodCallExpr m = (MethodCallExpr) cur;
                if (m.getNameAsString().equals("option") && m.getArguments().size() == 2
                        && m.getArguments().get(0) instanceof StringLiteralExpr) {
                    String k = ((StringLiteralExpr) m.getArguments().get(0)).asString().toLowerCase();
                    out.putIfAbsent(k, m.getArguments().get(1));
                }
                cur = m.getScope().orElse(null);
            } else if (cur instanceof FieldAccessExpr) {
                cur = ((FieldAccessExpr) cur).getScope();
            } else {
                break;
            }
        }
        return out;
    }

    /** Receiver text of the first `.read()`/`.write()` anchor in the chain. */
    private static String chainAnchorReceiver(String source, int[] lineOffsets, Expression e) {
        Expression cur = e;
        while (cur != null) {
            if (cur instanceof MethodCallExpr) {
                MethodCallExpr m = (MethodCallExpr) cur;
                String n = m.getNameAsString();
                if (m.getArguments().isEmpty()
                        && (n.equals("read") || n.equals("write")
                            || n.equals("readStream") || n.equals("writeStream"))) {
                    if (m.getScope().isPresent() && m.getScope().get().getRange().isPresent()) {
                        return getSourceRange(source, lineOffsets, m.getScope().get().getRange().get());
                    }
                    return "";
                }
                cur = m.getScope().orElse(null);
            } else if (cur instanceof FieldAccessExpr) {
                cur = ((FieldAccessExpr) cur).getScope();
            } else {
                return "";
            }
        }
        return "";
    }

    /** True when a `dbutils.<module>` receiver is present (e.g. dbutils.widgets). */
    private static boolean isDbUtilsModule(Expression e, String module) {
        String name = null;
        Expression scope = null;
        if (e instanceof MethodCallExpr) {
            MethodCallExpr m = (MethodCallExpr) e;
            if (!m.getArguments().isEmpty()) return false;
            name = m.getNameAsString();
            scope = m.getScope().orElse(null);
        } else if (e instanceof FieldAccessExpr) {
            FieldAccessExpr fa = (FieldAccessExpr) e;
            name = fa.getNameAsString();
            scope = fa.getScope();
        }
        if (name == null || !name.equals(module) || scope == null) return false;
        if (scope instanceof NameExpr) {
            return ((NameExpr) scope).getNameAsString().equals("dbutils");
        }
        if (scope instanceof MethodCallExpr) {
            return ((MethodCallExpr) scope).getNameAsString().equals("dbutils");
        }
        if (scope instanceof FieldAccessExpr) {
            return ((FieldAccessExpr) scope).getNameAsString().equals("dbutils");
        }
        return false;
    }

    /** Comma-joined source text of an argument list. */
    private static String argsText(MethodCallExpr mce) {
        return mce.getArguments().stream()
                .map(Expression::toString)
                .collect(Collectors.joining(", "));
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosApproxCountDistinctDropRsd
    //
    // approxCountDistinct(col, rsd) → approxCountDistinct(col). SCOS ignores the
    // relative-standard-deviation argument, so passing it is a signature error.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String APPROX_COMMENT =
        "// SCOS: [SPRKCNTSCL1000] ScosApproxCountDistinctDropRsd: rsd arg dropped "
        + "\u2014 approxCountDistinct(col, rsd) \u2192 approxCountDistinct(col)";

    private static String ruleApproxCountDistinctDropRsd(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String n = mce.getNameAsString();
                if (!n.equals("approxCountDistinct") && !n.equals("approx_count_distinct")) return;
                if (mce.getArguments().size() != 2) return;
                if (!mce.getRange().isPresent()) return;
                if (!mce.getArguments().get(0).getRange().isPresent()) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String colText = getSourceRange(source, lineOffsets,
                        mce.getArguments().get(0).getRange().get());
                String prefix = mce.getScope().isPresent()
                        ? getSourceRange(source, lineOffsets, mce.getScope().get().getRange().get()) + "."
                        : "";
                edits.add(new RangeEdit(range,
                        APPROX_COMMENT + "\n" + indent + prefix + n + "(" + colText + ")"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosDbUtilsSecretsGetStub
    //
    // dbutils.secrets.get/getBytes(...) → (String) null + TODO. Java has no
    // `null.asInstanceOf[String]`; the cast keeps the expression well-typed.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String SECRETS_STUB =
        "(String) null /* SCOS-TODO: [SPRKCNTSCL1500] ScosDbUtilsSecretsGetStub: "
        + "dbutils.secrets has no SCOS equivalent; stubbed to null "
        + "\u2014 migrate to Snowflake Secrets */";

    private static String ruleDbUtilsSecretsGetStub(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String n = mce.getNameAsString();
                if (!n.equals("get") && !n.equals("getBytes")) return;
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;
                if (!isDbUtilsModule(mce.getScope().get(), "secrets")) return;
                edits.add(new RangeEdit(mce.getRange().get(), SECRETS_STUB));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosDbUtilsWidgetsToProperty
    //
    //   dbutils.widgets.get/getArgument(...)          → System.getProperty(...)
    //   dbutils.widgets.text/dropdown/combobox/
    //     multiselect(key, default, ...)              → System.setProperty(key, default)
    //   dbutils.widgets.remove/removeAll(...)         → block comment (the trailing
    //                                                   `;` becomes an empty statement)
    // ═════════════════════════════════════════════════════════════════════════

    private static final String WIDGETS_TODO =
        "// SCOS-TODO: [SPRKCNTSCL1500] ScosDbUtilsWidgetsToProperty: "
        + "dbutils.widgets has no SCOS equivalent; mapped to System.getProperty";
    private static final Set<String> WIDGET_DECLARE =
        new HashSet<>(Arrays.asList("text", "dropdown", "combobox", "multiselect"));

    private static String ruleDbUtilsWidgetsToProperty(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;
                if (!isDbUtilsModule(mce.getScope().get(), "widgets")) return;

                String n = mce.getNameAsString();
                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));

                if (n.equals("get") || n.equals("getArgument")) {
                    edits.add(new RangeEdit(range, WIDGETS_TODO + "\n" + indent
                            + "System.getProperty(" + argsText(mce) + ")"));
                } else if (WIDGET_DECLARE.contains(n) && mce.getArguments().size() >= 2
                        && mce.getArguments().get(0) instanceof StringLiteralExpr
                        && mce.getArguments().get(1) instanceof StringLiteralExpr) {
                    String k = mce.getArguments().get(0).toString();
                    String d = mce.getArguments().get(1).toString();
                    edits.add(new RangeEdit(range, WIDGETS_TODO + "\n" + indent
                            + "System.setProperty(" + k + ", " + d + ")"));
                } else if (n.equals("remove") || n.equals("removeAll")) {
                    edits.add(new RangeEdit(range,
                            "/* SCOS: [SPRKCNTSCL1500] ScosDbUtilsWidgetsToProperty: "
                            + "dbutils.widgets." + n + "() stripped \u2014 no JVM equivalent */"));
                }
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosDeltaWriteToParquet
    //
    // .write().format("delta") → .format("parquet"). Skips the whole file when the
    // DeltaTable transactional API is used (no safe Parquet equivalent).
    // ═════════════════════════════════════════════════════════════════════════

    private static final String DELTA_WRITE_COMMENT =
        "// SCOS: [SPRKCNTSCL1000] ScosDeltaWriteToParquet: .format(\"delta\") not supported \u2014 "
        + "rewrote to .format(\"parquet\"); ACID/merge/time-travel are lost \u2014 verify path is a stage";
    private static final Set<String> DELTA_TABLE_ENTRIES =
        new HashSet<>(Arrays.asList("forPath", "forName", "forUid"));

    private static String ruleDeltaWriteToParquet(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;

        // File-level guard: DeltaTable.forPath/forName/forUid present → skip.
        final boolean[] hasDeltaTableApi = {false};
        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!DELTA_TABLE_ENTRIES.contains(mce.getNameAsString())) return;
                if (!mce.getScope().isPresent()) return;
                Expression s = mce.getScope().get();
                if (s instanceof NameExpr
                        && ((NameExpr) s).getNameAsString().equals("DeltaTable")) {
                    hasDeltaTableApi[0] = true;
                }
            }
        }, null);
        if (hasDeltaTableApi[0]) return source;

        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("format")) return;
                if (mce.getArguments().size() != 1) return;
                if (!(mce.getArguments().get(0) instanceof StringLiteralExpr)) return;
                if (!((StringLiteralExpr) mce.getArguments().get(0)).asString()
                        .equalsIgnoreCase("delta")) return;
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;

                String role = chainRole(mce.getScope().get());
                if (!role.equals("write") && !role.equals("writeStream")) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String recvText = getSourceRange(source, lineOffsets,
                        mce.getScope().get().getRange().get());
                edits.add(new RangeEdit(range, DELTA_WRITE_COMMENT + "\n" + indent
                        + recvText + ".format(\"parquet\")"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosDisplayToShow
    //
    // Bare Databricks global display(df) → df.show(). Only the single-argument,
    // no-receiver form (obj.display(x) and display() are left alone).
    // ═════════════════════════════════════════════════════════════════════════

    private static final String DISPLAY_COMMENT =
        "// SCOS: [SPRKCNTSCL1500] ScosDisplayToShow: display() not available \u2014 "
        + "replaced with .show() (note: .show() prints 20 rows; pass n for more)";

    private static String ruleDisplayToShow(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("display")) return;
                if (mce.getScope().isPresent()) return;      // method form → other rule
                if (mce.getArguments().size() != 1) return;
                if (!mce.getRange().isPresent()) return;
                if (!mce.getArguments().get(0).getRange().isPresent()) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String argT = getSourceRange(source, lineOffsets,
                        mce.getArguments().get(0).getRange().get());
                edits.add(new RangeEdit(range,
                        DISPLAY_COMMENT + "\n" + indent + argT + ".show()"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosDisplayMethodToShow
    //
    // Databricks Runtime 13+ DataFrame.display() (zero-arg method) → .show().
    // ═════════════════════════════════════════════════════════════════════════

    private static final String DISPLAY_METHOD_COMMENT =
        "// SCOS: [SPRKCNTSCL1500] ScosDisplayMethodToShow: df.display() not available \u2014 "
        + "replaced with .show() (note: .show() prints 20 rows; pass n for more)";

    private static String ruleDisplayMethodToShow(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("display")) return;
                if (!mce.getArguments().isEmpty()) return;    // zero-arg only
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;
                if (!mce.getScope().get().getRange().isPresent()) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String recvText = getSourceRange(source, lineOffsets,
                        mce.getScope().get().getRange().get());
                edits.add(new RangeEdit(range,
                        DISPLAY_METHOD_COMMENT + "\n" + indent + recvText + ".show()"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosHadoopConfCredentialAnnotate
    //
    // Annotates hadoopConfiguration().set("fs.s3a...", ...) and conf().set(...)
    // credential settings — no effect in SCOS (storage access is via Snowflake).
    // ═════════════════════════════════════════════════════════════════════════

    private static final List<String> HADOOP_PREFIXES = Arrays.asList(
        "fs.s3", "fs.azure", "fs.gs", "fs.adl", "fs.abfs",
        "spark.hadoop.fs", "hadoop.fs", "dfs.adls");

    private static final String HADOOP_CONF_COMMENT =
        "// SCOS: TODO - [SPRKCNTSCL1000] ScosHadoopConfCredentialAnnotate: "
        + "Hadoop credential config has no effect in SCOS; "
        + "storage access is governed by the Snowflake connection";

    private static boolean isHadoopKey(Expression e) {
        if (!(e instanceof StringLiteralExpr)) return false;
        String v = ((StringLiteralExpr) e).asString();
        for (String p : HADOOP_PREFIXES) {
            if (v.startsWith(p)) return true;
        }
        return false;
    }

    /** True when the receiver is a `hadoopConfiguration()`/`conf()` accessor. */
    private static boolean isConfAccessor(Expression e, String accessor) {
        if (e instanceof MethodCallExpr) {
            MethodCallExpr m = (MethodCallExpr) e;
            return m.getNameAsString().equals(accessor) && m.getArguments().isEmpty();
        }
        if (e instanceof FieldAccessExpr) {
            return ((FieldAccessExpr) e).getNameAsString().equals(accessor);
        }
        return false;
    }

    private static String ruleHadoopConfCredentialAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("set")) return;
                if (mce.getArguments().size() != 2) return;
                if (!mce.getScope().isPresent() || !mce.getBegin().isPresent()) return;
                if (!isHadoopKey(mce.getArguments().get(0))) return;
                Expression recv = mce.getScope().get();
                if (isConfAccessor(recv, "hadoopConfiguration") || isConfAccessor(recv, "conf")) {
                    annots.add(new AnnotEntry(mce.getBegin().get().line, HADOOP_CONF_COMMENT));
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosPartitionNoopStrip
    //
    // Removes no-op .coalesce()/.repartition()/.repartitionByRange() from
    // DataFrame chains — Snowflake manages partitioning. functions.coalesce()
    // (the column function) is explicitly preserved.
    // ═════════════════════════════════════════════════════════════════════════

    private static final Set<String> PARTITION_NOOPS =
        new HashSet<>(Arrays.asList("coalesce", "repartition", "repartitionByRange"));

    private static final String PARTITION_NOOP_COMMENT =
        "// SCOS: [SPRKCNTSCL1500] ScosPartitionNoopStrip: removed no-op "
        + ".coalesce()/.repartition() \u2014 Snowflake manages partitioning (no effect in SCOS)";

    /** True when this call is a DataFrame-level partition no-op (not functions.coalesce). */
    private static boolean isPartitionNoopCall(Expression e) {
        if (!(e instanceof MethodCallExpr)) return false;
        MethodCallExpr m = (MethodCallExpr) e;
        if (!PARTITION_NOOPS.contains(m.getNameAsString())) return false;
        if (!m.getScope().isPresent()) return false;
        return !isFunctionsModule(m.getScope().get());
    }

    /** Recursively drop partition no-ops from a chain, preserving functions.coalesce(). */
    private static String stripPartitionNoops(String source, int[] lineOffsets, Expression e) {
        if (isPartitionNoopCall(e)) {
            return stripPartitionNoops(source, lineOffsets,
                    ((MethodCallExpr) e).getScope().get());
        }
        if (e.getRange().isPresent()) {
            return getSourceRange(source, lineOffsets, e.getRange().get());
        }
        return e.toString();
    }

    private static String rulePartitionNoopStrip(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!isPartitionNoopCall(mce)) return;
                if (!mce.getRange().isPresent()) return;

                // Only the OUTERMOST no-op in a run emits an edit. Emitting one per
                // call produced overlapping RangeEdits on chains such as
                // df.repartition(10).coalesce(2), which corrupted the output.
                Optional<Node> parent = mce.getParentNode();
                if (parent.isPresent() && parent.get() instanceof MethodCallExpr) {
                    MethodCallExpr p = (MethodCallExpr) parent.get();
                    if (isPartitionNoopCall(p) && p.getScope().isPresent()
                            && p.getScope().get() == mce) {
                        return;
                    }
                }

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String kept = stripPartitionNoops(source, lineOffsets, mce);
                edits.add(new RangeEdit(range,
                        PARTITION_NOOP_COMMENT + "\n" + indent + kept));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosRddExclusiveMethodAnnotate
    //
    // Annotates RDD/PairRDD-exclusive methods with no direct DataFrame analogue.
    // Java adds the JavaPairRDD-specific *ToPair variants.
    // ═════════════════════════════════════════════════════════════════════════

    private static final Set<String> RDD_EXCLUSIVE = new HashSet<>(Arrays.asList(
        "reduceByKey", "reduceByKeyLocally", "groupByKey", "aggregateByKey",
        "foldByKey", "combineByKey", "sampleByKey", "countByKey", "countByValue",
        "mapValues", "flatMapValues", "keyBy", "zipWithIndex", "zipWithUniqueId",
        "sortByKey", "mapPartitions", "mapPartitionsWithIndex",
        "takeOrdered", "takeSample", "saveAsTextFile",
        // Java-only PairRDD surface (no Scala equivalent name)
        "mapToPair", "flatMapToPair", "mapPartitionsToPair", "aggregateByKeyToPair"
    ));

    private static String ruleRddExclusiveMethodAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String m = mce.getNameAsString();
                if (!RDD_EXCLUSIVE.contains(m)) return;
                if (!mce.getScope().isPresent() || !mce.getBegin().isPresent()) return;
                annots.add(new AnnotEntry(mce.getBegin().get().line,
                        "// SCOS: TODO - [SPRKCNTSCL1500] ScosRddExclusiveMethodAnnotate: "
                        + "RDD." + m + "() is unsupported in Snowpark Connect; "
                        + "migrate to the DataFrame equivalent (see references/java/rdd-conversion.md)"));
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosRddImportAnnotate
    //
    // Annotates RDD imports. Java covers both org.apache.spark.rdd.* and the
    // Java-only org.apache.spark.api.java.Java{,Pair}RDD / JavaSparkContext.
    // ═════════════════════════════════════════════════════════════════════════

    private static final List<String> RDD_IMPORT_PREFIXES = Arrays.asList(
        "org.apache.spark.rdd",
        "org.apache.spark.api.java.JavaRDD",
        "org.apache.spark.api.java.JavaPairRDD",
        "org.apache.spark.api.java.JavaDoubleRDD",
        "org.apache.spark.api.java.JavaSparkContext");

    private static final String RDD_IMPORT_COMMENT =
        "// SCOS: TODO - [SPRKCNTSCL1500] ScosRddImportAnnotate: "
        + "RDD imports are not supported in Snowpark Connect; "
        + "rewrite all RDD usages to DataFrames (see references/java/rdd-conversion.md)";

    private static String ruleRddImportAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        List<AnnotEntry> annots = new ArrayList<>();

        for (com.github.javaparser.ast.ImportDeclaration imp : cu.getImports()) {
            if (!imp.getBegin().isPresent()) continue;
            String name = imp.getNameAsString();
            for (String p : RDD_IMPORT_PREFIXES) {
                if (name.startsWith(p)) {
                    annots.add(new AnnotEntry(imp.getBegin().get().line, RDD_IMPORT_COMMENT));
                    break;
                }
            }
        }

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosRddPersistToCache
    //
    // df.rdd().persist(...) / df.rdd().cache() → df.persist(...) / df.cache().
    // Java exposes .rdd() and .toJavaRDD() as methods (Scala uses the .rdd field),
    // so both accessor shapes are handled.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String RDD_PERSIST_COMMENT =
        "// SCOS: [SPRKCNTSCL1000] ScosRddPersistToCache: "
        + "df.rdd().persist/cache() \u2192 df.persist/cache() (.rdd not available in SCOS)";
    private static final Set<String> RDD_ACCESSORS =
        new HashSet<>(Arrays.asList("rdd", "toJavaRDD", "javaRDD"));

    private static String ruleRddPersistToCache(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String m = mce.getNameAsString();
                if (!m.equals("persist") && !m.equals("cache")) return;
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;

                // Receiver must be an RDD accessor; its own scope is the DataFrame.
                Expression recv = mce.getScope().get();
                Expression base = null;
                if (recv instanceof MethodCallExpr) {
                    MethodCallExpr r = (MethodCallExpr) recv;
                    if (RDD_ACCESSORS.contains(r.getNameAsString()) && r.getArguments().isEmpty()) {
                        base = r.getScope().orElse(null);
                    }
                } else if (recv instanceof FieldAccessExpr) {
                    FieldAccessExpr r = (FieldAccessExpr) recv;
                    if (RDD_ACCESSORS.contains(r.getNameAsString())) {
                        base = r.getScope();
                    }
                }
                if (base == null || !base.getRange().isPresent()) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String baseText = getSourceRange(source, lineOffsets, base.getRange().get());
                edits.add(new RangeEdit(range, RDD_PERSIST_COMMENT + "\n" + indent
                        + baseText + "." + m + "(" + argsText(mce) + ")"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosScTextfileToReadText
    //
    // sc.textFile("p"[, numPartitions]) → spark.read().text("p").
    // Note the Java accessor form `read()` rather than Scala's `read`.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String SC_TEXTFILE_COMMENT =
        "// SCOS: [SPRKCNTSCL1500] ScosScTextfileToReadText: "
        + "sc.textFile() \u2192 spark.read().text() (numPartitions arg dropped if present)";

    private static String ruleScTextfileToReadText(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("textFile")) return;
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;
                if (!isSparkContextRecv(mce.getScope().get())) return;
                if (mce.getArguments().isEmpty()) return;
                if (!mce.getArguments().get(0).getRange().isPresent()) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String pathArg = getSourceRange(source, lineOffsets,
                        mce.getArguments().get(0).getRange().get());
                edits.add(new RangeEdit(range, SC_TEXTFILE_COMMENT + "\n" + indent
                        + "spark.read().text(" + pathArg + ")"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosScWholeTextFilesAnnotate
    // ═════════════════════════════════════════════════════════════════════════

    private static final String SC_WHOLETEXT_COMMENT =
        "// SCOS: TODO - [SPRKCNTSCL1500] ScosScWholeTextFilesAnnotate: "
        + "sc.wholeTextFiles() returns (filename, content) pairs with no direct DataFrame "
        + "equivalent; convert to spark.read().text() + per-file grouping";

    private static String ruleScWholeTextFilesAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("wholeTextFiles")) return;
                if (!mce.getScope().isPresent() || !mce.getBegin().isPresent()) return;
                if (!isSparkContextRecv(mce.getScope().get())) return;
                annots.add(new AnnotEntry(mce.getBegin().get().line, SC_WHOLETEXT_COMMENT));
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosSnowflakeConnectorIO
    //
    // Under SCOS the workload runs inside Snowflake, so the external Spark
    // Snowflake connector is unnecessary.
    //   read .format("snowflake").option("query", Q).load()
    //        → new SnowflakeSession(spark).sql(Q)
    //   read .option("dbtable", T)  → new SnowflakeSession(spark).sql("SELECT * FROM T")
    //   write .format("snowflake").option("dbtable", T).save() → .saveAsTable(T)
    // Non-literal options get a TODO annotation only (no rewrite).
    // ═════════════════════════════════════════════════════════════════════════

    private static final Set<String> SF_FORMATS = new HashSet<>(Arrays.asList(
        "snowflake", "net.snowflake.spark.snowflake"));

    private static final String SF_READ_COMMENT =
        "// SCOS: [SPRKCNTSCL1000-Fixed] ScosSnowflakeConnectorIO: "
        + "read.format(\"snowflake\").load() \u2192 new SnowflakeSession(sess).sql() "
        + "(never use bare spark.sql() for Snowflake-specific SQL)";
    private static final String SF_WRITE_COMMENT =
        "// SCOS: [SPRKCNTSCL1000-Fixed] ScosSnowflakeConnectorIO: "
        + "write.format(\"snowflake\").save() \u2192 .write().saveAsTable() (native managed-table write)";
    private static final String SF_TODO_COMMENT =
        "// SCOS: TODO - [SPRKCNTSCL1000-IO] ScosSnowflakeConnectorIO: "
        + "Snowflake connector I/O with non-literal options; "
        + "convert to new SnowflakeSession(sess).sql(...) for reads or "
        + ".write().saveAsTable(...) for writes manually";
    private static final String SF_IMPORT_MARKER =
        "// SCOS-RECIPE-INSERT-IMPORT: com.snowflake.snowpark_connect.client.SnowflakeSession";

    private static String ruleSnowflakeConnectorIO(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();
        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String terminal = mce.getNameAsString();
                if (!terminal.equals("load") && !terminal.equals("save")) return;
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;

                Expression recv = mce.getScope().get();
                if (!SF_FORMATS.contains(chainFormat(recv))) return;
                String role = chainRole(recv);
                if (role.isEmpty()) return;

                java.util.Map<String, Expression> opts = chainOptions(recv);
                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                boolean isRead = role.equals("read") || role.equals("readStream");

                if (isRead) {
                    Expression query = opts.get("query");
                    Expression dbtable = opts.get("dbtable");
                    String sess = chainAnchorReceiver(source, lineOffsets, recv);
                    if (sess.isEmpty()) sess = "spark";
                    if (query instanceof StringLiteralExpr) {
                        edits.add(new RangeEdit(range, SF_READ_COMMENT + "\n" + indent
                                + SF_IMPORT_MARKER + "\n" + indent
                                + "new SnowflakeSession(" + sess + ").sql(" + query + ")"));
                        return;
                    }
                    if (dbtable instanceof StringLiteralExpr) {
                        String t = ((StringLiteralExpr) dbtable).asString();
                        edits.add(new RangeEdit(range, SF_READ_COMMENT + "\n" + indent
                                + SF_IMPORT_MARKER + "\n" + indent
                                + "new SnowflakeSession(" + sess + ").sql(\"SELECT * FROM "
                                + t + "\")"));
                        return;
                    }
                } else {
                    Expression dbtable = opts.get("dbtable");
                    if (dbtable instanceof StringLiteralExpr) {
                        // Drop the connector-specific format/option calls, keep .mode(...).
                        Set<String> drop = new HashSet<>(Arrays.asList("format", "option"));
                        String rebuilt = rebuildChain(recv, drop);
                        edits.add(new RangeEdit(range, SF_WRITE_COMMENT + "\n" + indent
                                + rebuilt + ".saveAsTable(" + dbtable + ")"));
                        return;
                    }
                }
                // Non-literal / unrecognized options: annotate only.
                if (mce.getBegin().isPresent()) {
                    annots.add(new AnnotEntry(mce.getBegin().get().line, SF_TODO_COMMENT));
                }
            }
        }, null);

        String out = applyEdits(source, edits);
        // Annotations are line-based; only safe to apply when no ranges moved.
        if (edits.isEmpty()) {
            out = insertAnnotations(out, annots);
        }
        return out;
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosSparkConfigNoopAnnotate
    //
    // Annotates spark.conf().set("spark.executor...", v) — cluster-sizing keys
    // Snowflake ignores.
    // ═════════════════════════════════════════════════════════════════════════

    private static final List<String> CONFIG_NOOP_PREFIXES = Arrays.asList(
        "spark.executor.", "spark.driver.", "spark.yarn.", "spark.kubernetes.",
        "spark.mesos.", "spark.submit.", "spark.deploy.", "spark.cores.",
        "spark.task.", "spark.scheduler.", "spark.worker.", "spark.network.",
        "spark.rpc.", "spark.locality.", "spark.dynamicAllocation.",
        "spark.speculation.", "spark.blacklist.", "spark.excludeOnFailure.",
        "spark.memory.", "spark.streaming.",
        "spark.databricks.delta.optimizeWrite", "spark.databricks.delta.autoCompact");

    private static final String CONFIG_NOOP_COMMENT =
        "// SCOS: TODO - [SPRKCNTSCL1000] ScosSparkConfigNoopAnnotate: "
        + "this Spark config key has no effect in Snowpark Connect; "
        + "remove or convert to a Snowflake session parameter if applicable";

    private static String ruleSparkConfigNoopAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("set")) return;
                if (mce.getArguments().size() != 2) return;
                if (!mce.getScope().isPresent() || !mce.getBegin().isPresent()) return;
                if (!isConfAccessor(mce.getScope().get(), "conf")) return;
                if (!(mce.getArguments().get(0) instanceof StringLiteralExpr)) return;
                String k = ((StringLiteralExpr) mce.getArguments().get(0)).asString();
                for (String p : CONFIG_NOOP_PREFIXES) {
                    if (k.startsWith(p)) {
                        annots.add(new AnnotEntry(mce.getBegin().get().line, CONFIG_NOOP_COMMENT));
                        return;
                    }
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosSparkContextGetOrCreateRewrite
    //
    // SparkContext.getOrCreate()          → SnowparkConnectSession.builder().getOrCreate()
    // SparkContext.getOrCreate(conf)      → TODO (config must be re-expressed)
    // new SparkContext/JavaSparkContext() → TODO
    // ═════════════════════════════════════════════════════════════════════════

    private static final String SC_RENAME_COMMENT =
        "// SCOS: [SPRKCNTSCL3500-Fixed] ScosSparkContextGetOrCreateRewrite: "
        + "SparkContext.getOrCreate() \u2192 SnowparkConnectSession.builder().getOrCreate()";
    private static final String SC_TODO_COMMENT =
        "// SCOS: TODO - [SPRKCNTSCL3500] ScosSparkContextGetOrCreateRewrite: "
        + "SparkContext construction not supported; "
        + "replace with SnowparkConnectSession.builder().getOrCreate()";
    private static final Set<String> SC_TYPES =
        new HashSet<>(Arrays.asList("SparkContext", "JavaSparkContext"));

    private static String ruleSparkContextGetOrCreateRewrite(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();
        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("getOrCreate")) return;
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;
                Expression s = mce.getScope().get();
                boolean isScType = s instanceof NameExpr
                        && SC_TYPES.contains(((NameExpr) s).getNameAsString());
                if (!isScType) return;

                if (mce.getArguments().isEmpty()) {
                    com.github.javaparser.Range range = mce.getRange().get();
                    String indent = getLeadingWhitespace(
                            getSourceLine(source, lineOffsets, range.begin.line));
                    edits.add(new RangeEdit(range, SC_RENAME_COMMENT + "\n" + indent
                            + "SnowparkConnectSession.builder().getOrCreate()"));
                } else if (mce.getBegin().isPresent()) {
                    annots.add(new AnnotEntry(mce.getBegin().get().line, SC_TODO_COMMENT));
                }
            }

            @Override
            public void visit(com.github.javaparser.ast.expr.ObjectCreationExpr oce, Void arg) {
                super.visit(oce, arg);
                if (!SC_TYPES.contains(oce.getType().getNameAsString())) return;
                if (!oce.getBegin().isPresent()) return;
                annots.add(new AnnotEntry(oce.getBegin().get().line, SC_TODO_COMMENT));
            }
        }, null);

        String out = applyEdits(source, edits);
        if (edits.isEmpty()) {
            out = insertAnnotations(out, annots);
        }
        return out;
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosSparkContextNoopCommentOut
    //
    // sc.stop()/close()/setLogLevel() → block comment. A block comment (not a
    // `//` line comment) is required: the trailing `;` then forms a valid empty
    // statement and a same-line `}` is not swallowed.
    // ═════════════════════════════════════════════════════════════════════════

    private static final Set<String> SC_NOOPS =
        new HashSet<>(Arrays.asList("stop", "close", "setLogLevel"));

    private static String ruleSparkContextNoopCommentOut(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                String m = mce.getNameAsString();
                if (!SC_NOOPS.contains(m)) return;
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;
                if (!isSparkContextRecv(mce.getScope().get())) return;
                edits.add(new RangeEdit(mce.getRange().get(),
                        "/* SCOS: [SPRKCNTSCL1500] ScosSparkContextNoopCommentOut: sc." + m
                        + "() is a no-op in Snowpark Connect (SparkContext not available) */"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosSparkIoDetectAnnotate
    //
    // Annotates I/O not covered by ScosExternalCloudReadAnnotate (cloud URIs) or
    // ScosWildcardReadAnnotate (globs):
    //   JDBC     → SPRKCNTSCL6000-Error   (no JVM driver in Spark Connect)
    //   Iceberg  → SPRKCNTSCL3200-IO
    //   table    → SPRKCNTSCL3200-IO
    // .saveAsTable is deliberately excluded (ScosSaveAsTableDropStorageOpts owns it).
    // ═════════════════════════════════════════════════════════════════════════

    private static final String IO_JDBC_COMMENT =
        "// SCOS: [SPRKCNTSCL6000-Error] spark_io_detect: JDBC source/sink requires "
        + "a JVM driver not available in Spark Connect \u2014 use the Snowflake connector, "
        + "an external table, or load the data to a Snowflake table.";
    private static final String IO_TABLE_READ_COMMENT =
        "// SCOS: [SPRKCNTSCL3200-IO] spark_io_detect: table I/O \u2014 reads from a "
        + "Snowflake table; verify the table name/namespace (database.schema.table) "
        + "resolves to the intended Snowflake table (catalog/schema mapping may differ).";
    private static final String IO_TABLE_WRITE_COMMENT =
        "// SCOS: [SPRKCNTSCL3200-IO] spark_io_detect: table I/O \u2014 writes to a "
        + "Snowflake table; verify the table name/namespace (database.schema.table) "
        + "resolves to the intended Snowflake table (catalog/schema mapping may differ).";

    private static String ruleSparkIoDetectAnnotate(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        List<AnnotEntry> annots = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getScope().isPresent() || !mce.getBegin().isPresent()) return;
                String name = mce.getNameAsString();
                Expression recv = mce.getScope().get();
                int line = mce.getBegin().get().line;

                // df.write().jdbc(url, table, props)
                if (name.equals("jdbc") && !chainRole(recv).isEmpty()) {
                    annots.add(new AnnotEntry(line, IO_JDBC_COMMENT));
                    return;
                }
                // .load() / .save() carrying .format("jdbc"|"iceberg")
                if (name.equals("load") || name.equals("save")) {
                    String role = chainRole(recv);
                    if (role.isEmpty()) return;
                    String fmt = chainFormat(recv);
                    if (fmt.equals("jdbc")) {
                        annots.add(new AnnotEntry(line, IO_JDBC_COMMENT));
                    } else if (fmt.equals("iceberg")) {
                        boolean reading = role.equals("read") || role.equals("readStream");
                        annots.add(new AnnotEntry(line,
                                "// SCOS: [SPRKCNTSCL3200-IO] spark_io_detect: Iceberg catalog table I/O \u2014 "
                                + (reading ? "reads from" : "writes to")
                                + " an Iceberg-managed table; verify the table is accessible in Snowflake "
                                + "(Iceberg Tables, external catalog integration, or migrate to a native "
                                + "Snowflake table)."));
                    }
                    return;
                }
                // spark.read().table(name)
                if (name.equals("table")) {
                    String role = chainRole(recv);
                    if (role.equals("read") || role.equals("readStream")) {
                        annots.add(new AnnotEntry(line, IO_TABLE_READ_COMMENT));
                    }
                    return;
                }
                // .insertInto(name) on a write chain
                if (name.equals("insertInto") && !chainRole(recv).isEmpty()) {
                    annots.add(new AnnotEntry(line, IO_TABLE_WRITE_COMMENT));
                }
            }
        }, null);

        return insertAnnotations(source, annots);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // Ported Rule: ScosUnpersistDropBlockingArg
    //
    // df.unpersist(true) → df.unpersist(). SCOS's unpersist takes no blocking arg.
    // ═════════════════════════════════════════════════════════════════════════

    private static final String UNPERSIST_COMMENT =
        "// SCOS: [SPRKCNTSCL1000] ScosUnpersistDropBlockingArg: "
        + "unpersist() does not accept a blocking arg in Snowpark Connect \u2014 arg dropped";

    private static String ruleUnpersistDropBlockingArg(String source, String filePath) {
        CompilationUnit cu = tryParse(source);
        if (cu == null) return source;
        int[] lineOffsets = buildLineOffsets(source);
        List<RangeEdit> edits = new ArrayList<>();

        cu.accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr mce, Void arg) {
                super.visit(mce, arg);
                if (!mce.getNameAsString().equals("unpersist")) return;
                if (mce.getArguments().isEmpty()) return;
                if (!mce.getScope().isPresent() || !mce.getRange().isPresent()) return;
                if (!mce.getScope().get().getRange().isPresent()) return;

                com.github.javaparser.Range range = mce.getRange().get();
                String indent = getLeadingWhitespace(getSourceLine(source, lineOffsets, range.begin.line));
                String recvText = getSourceRange(source, lineOffsets,
                        mce.getScope().get().getRange().get());
                edits.add(new RangeEdit(range,
                        UNPERSIST_COMMENT + "\n" + indent + recvText + ".unpersist()"));
            }
        }, null);

        return applyEdits(source, edits);
    }

    // ── Text utilities ────────────────────────────────────────────────────────

    /** Build array where lineOffsets[i] = char offset (0-based) of line i+1. */
    static int[] buildLineOffsets(String source) {
        List<Integer> offsets = new ArrayList<>();
        offsets.add(0);
        for (int i = 0; i < source.length(); i++) {
            if (source.charAt(i) == '\n') offsets.add(i + 1);
        }
        return offsets.stream().mapToInt(Integer::intValue).toArray();
    }

    /** Convert 1-based (line, column) to 0-based char offset. */
    private static int toOffset(String source, int[] lineOffsets, int line, int col) {
        if (line < 1 || line > lineOffsets.length) return source.length();
        return lineOffsets[line - 1] + (col - 1);
    }

    /** Extract the raw source text covered by a javaparser Range. */
    static String getSourceRange(String source, int[] lineOffsets,
                                  com.github.javaparser.Range range) {
        int begin = toOffset(source, lineOffsets, range.begin.line, range.begin.column);
        int end   = toOffset(source, lineOffsets, range.end.line,   range.end.column) + 1;
        begin = Math.max(0, Math.min(begin, source.length()));
        end   = Math.max(begin, Math.min(end, source.length()));
        return source.substring(begin, end);
    }

    /** Return the full source line (without trailing \n) for a 1-based line number. */
    private static String getSourceLine(String source, int[] lineOffsets, int line) {
        if (line < 1 || line > lineOffsets.length) return "";
        int start = lineOffsets[line - 1];
        int end   = (line < lineOffsets.length) ? lineOffsets[line] - 1 : source.length();
        String l = source.substring(start, Math.min(end, source.length()));
        return l.endsWith("\r") ? l.substring(0, l.length() - 1) : l;
    }

    /** Extract leading whitespace from a source line. */
    private static String getLeadingWhitespace(String line) {
        StringBuilder sb = new StringBuilder();
        for (char c : line.toCharArray()) {
            if (c == ' ' || c == '\t') sb.append(c); else break;
        }
        return sb.toString();
    }

    /** Bare string if it's a string literal, else javaparser toString. */
    private static String literalOrSyntax(Expression e) {
        return e instanceof StringLiteralExpr
                ? ((StringLiteralExpr) e).asString() : e.toString();
    }

    // ── AnnotEntry: (targetLine, commentText) for insertAnnotations ───────────

    static final class AnnotEntry {
        final int line;    // 1-based: insert comment ABOVE this line
        final String comment;
        AnnotEntry(int line, String comment) { this.line = line; this.comment = comment; }
    }

    /**
     * Insert comment lines above the target line in the source.
     * Indentation is inferred from the target line.
     * Multiple annotations on the same line are inserted in the order given.
     */
    static String insertAnnotations(String source, List<AnnotEntry> annots) {
        if (annots.isEmpty()) return source;
        String[] lines = source.split("\n", -1);

        // Sort ascending; we'll rebuild lines array top-to-bottom
        List<AnnotEntry> sorted = new ArrayList<>(annots);
        sorted.sort((a, b) -> Integer.compare(a.line, b.line));

        List<String> result = new ArrayList<>();
        int annotIdx = 0;
        for (int i = 0; i < lines.length; i++) {
            int lineNum = i + 1; // 1-based
            while (annotIdx < sorted.size() && sorted.get(annotIdx).line == lineNum) {
                String indent = getLeadingWhitespace(lines[i]);
                result.add(indent + sorted.get(annotIdx).comment);
                annotIdx++;
            }
            result.add(lines[i]);
        }
        return String.join("\n", result);
    }

    // ── RangeEdit: replace a source range with new text ───────────────────────

    static final class RangeEdit {
        final com.github.javaparser.Range range;
        final String replacement;
        RangeEdit(com.github.javaparser.Range range, String replacement) {
            this.range = range; this.replacement = replacement;
        }
    }

    /**
     * Apply range edits bottom-to-top so earlier positions remain valid.
     * Edits must be non-overlapping.
     */
    static String applyEdits(String source, List<RangeEdit> edits) {
        if (edits.isEmpty()) return source;
        // Sort descending by begin position
        List<RangeEdit> sorted = new ArrayList<>(edits);
        sorted.sort((a, b) -> {
            if (a.range.begin.line != b.range.begin.line)
                return b.range.begin.line - a.range.begin.line;
            return b.range.begin.column - a.range.begin.column;
        });

        StringBuilder sb = new StringBuilder(source);
        for (RangeEdit edit : sorted) {
            // Recompute offsets from current sb content (prior edits are below this position)
            String current = sb.toString();
            int[] lo = buildLineOffsets(current);
            int begin = toOffset(current, lo, edit.range.begin.line, edit.range.begin.column);
            int end   = toOffset(current, lo, edit.range.end.line,   edit.range.end.column) + 1;
            begin = Math.max(0, Math.min(begin, sb.length()));
            end   = Math.max(begin, Math.min(end, sb.length()));
            sb.replace(begin, end, edit.replacement);
        }
        return sb.toString();
    }
}

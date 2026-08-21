/**
 * SCOSRules.scala — Scalafix syntactic rules for the Spark Scala → Snowpark
 * Connect (SCOS) migration skill.
 *
 * These are SyntacticRule implementations built with Scalameta.  They do NOT
 * require SemanticDB / type-checking — any `.scala` file can be rewritten
 * without a full compilation classpath.
 *
 * Rules provided
 * ──────────────
 *   ScosCheckpointToCache                  (.checkpoint/.localCheckpoint → .cache + EWI)
 *   ScosMapSubscriptToElementAt            (mapCol(col("k")) → element_at(...))
 *   ScosWildcardReadAnnotate               (wildcard read paths)
 *   ScosSparkSessionBuilderRewrite         (rename SparkSession→SnowparkConnectSession, drop .master/.enableHiveSupport/.remote, preserve .config)
 *   ScosSaveAsTableDropStorageOpts         (drop .format/.option("path") from saveAsTable)
 *   ScosExternalCloudReadAnnotate          (s3/gs/abfss/… read perf hint)
 *   ScosSelfJoinUnaliasedAnnotate          (df.join(df, …) unaliased)
 *   ScosSparkContextPropertyFallbackAnnotate (sc.parallelize / sc.broadcast)
 *   ScosUdtfCompatibilityModeAnnotate      (class extends UDTF base)
 *   ScosUnionByNameAllowMissingAnnotate    (unionByName(allowMissingColumns = true))
 *   ScosDriverHotPathAnnotate              (.collect/.toLocalIterator in a loop)
 *   ScosTempViewMultiUseCache              (cache multi-use temp views)
 *   ScosSystemGetenvRewrite                (System.getenv("K") → System.getProperty("K"))
 *   ScosDeltaTableAnnotate                 (DeltaTable.forPath/forName → annotation)
 *   ScosSqlContextImplicitsRewrite         (spark.sqlContext.implicits._ → spark.implicits._)
 *   ScosSparkIoDetectAnnotate              (JDBC/Iceberg/table I/O detection — parity: spark_io_detect)
 *
 * These are the SOLE deterministic pre-processing tier — the regex recipe tier
 * was removed, so every transform runs at the Scalameta AST level.
 *
 * How to compile + run
 * ────────────────────
 * Handled automatically by preprocess_scalafix.py (recommended — does PATH
 * detection, rule compilation, graceful skip, and state updates):
 *
 *   uv run --project <SKILL_DIRECTORY> \
 *     python <SKILL_DIRECTORY>/scripts/preprocess_scalafix.py \
 *     --state <CONVERSION>/migration_state.json
 *
 * Under the hood the rules are compiled and run with PINNED versions
 * (scala 2.12.20, scalafix-cli 0.14.3 — verified on Maven Central):
 *   • Preferred: the sbt wrapper (scripts/scalafix_sbt/) compiles this file and
 *     exports a classpath; the script runs `java -cp <cp> scalafix.cli.Cli`.
 *   • Fallback: scalafix-cli via `cs launch`, with this rule JAR supplied via
 *     `--tool-classpath` (NOT `--classpath`, which is for the target's own
 *     semantic classpath).
 *
 * SemanticDB NOT required
 * ────────────────────────
 * All five rules use SyntacticDocument / SyntacticRule.  No `semanticdb-scalac`
 * plugin or `--classpath` of the project under migration is needed.  The only
 * classpath required is the scalafix-core JAR itself.
 */
package com.snowflake.scos.scalafix

import scalafix.v1._
import scala.meta._

// ─────────────────────────────────────────────────────────────────────────────
// Rule 1: ScosCheckpointToCache
//
// Replaces `.checkpoint(...)` / `.localCheckpoint(...)` with `.cache()` and
// inserts an EWI annotation comment.  Handles multi-line receiver chains at
// the AST level.
// ─────────────────────────────────────────────────────────────────────────────
class ScosCheckpointToCache extends SyntacticRule("ScosCheckpointToCache") {
  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(qual, Term.Name(method)),
            _
          ) if method == "checkpoint" || method == "localCheckpoint" =>
        Patch.replaceTree(
          t,
          s"// SCOS: [SPRKCNTSCL1500] ${method}() not supported — replaced with cache()\n" +
            s"${qual}.cache()"
        )
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 2: ScosMapSubscriptToElementAt
//
// Rewrites `mapCol(col("k"))` → `element_at(mapCol, col("k"))` when the
// call appears inside a select / withColumn expression.  The AST rule
// correctly handles computed column-name expressions and chained receivers
// that the line-anchored regex misses.
// ─────────────────────────────────────────────────────────────────────────────
class ScosMapSubscriptToElementAt extends SyntacticRule("ScosMapSubscriptToElementAt") {

  // Known DataFrame / Column functions and common single-argument Spark SQL
  // functions that must NOT be rewritten even though `f(col("x"))` looks
  // syntactically like a map-subscript. This is a backstop; the primary guard
  // is the positive-evidence `columnNames` check below.
  private val excluded: Set[String] = Set(
    "element_at", "col", "column", "lit", "when", "coalesce", "concat",
    "select", "filter", "where", "agg", "groupBy", "join",
    "withColumn", "drop", "map", "flatMap", "reduce",
    "collect", "count", "sum", "min", "max", "avg", "mean",
    "first", "last", "struct", "array", "explode",
    // datetime
    "hour", "minute", "second", "year", "month", "dayofmonth", "dayofweek",
    "dayofyear", "weekofyear", "quarter", "to_date", "to_timestamp",
    "unix_timestamp", "from_unixtime", "date_format",
    // string
    "length", "lower", "upper", "trim", "ltrim", "rtrim", "initcap", "reverse",
    // numeric / hashing / misc
    "abs", "size", "hash", "md5", "sha1", "sha2", "crc32", "sqrt", "exp",
    "log", "floor", "ceil", "round", "isnull", "isnan", "asc", "desc",
  )

  // Collect identifiers bound to udf(...) anywhere in the file.
  // Handles: val clean_rule = udf(...)  and  def getpointid = udf(...)
  // UDFs are UserDefinedFunction, NOT Column — rewriting them with
  // element_at produces a compile-time type mismatch.
  private def udfNames(implicit doc: SyntacticDocument): Set[String] =
    doc.tree.collect {
      case Defn.Val(_, List(Pat.Var(Term.Name(name))), _,
                    Term.Apply(Term.Name("udf"), _)) => name
      case Defn.Def(_, Term.Name(name), _, _, _,
                    Term.Apply(Term.Name("udf"), _)) => name
    }.toSet

  // Positive evidence: identifiers bound IN THIS FILE to a Column expression
  //   val/var/def name = col(...) | column(...) | $"..."
  // Only these are eligible to be treated as a map-subscript receiver, so a
  // bare imported function like `hour` is never rewritten.
  private def isColumnRhs(rhs: Term): Boolean = rhs match {
    case Term.Apply(Term.Name("col"), _)    => true
    case Term.Apply(Term.Name("column"), _) => true
    case Term.Interpolate(Term.Name("$"), _, _) => true
    case _ => false
  }

  private def columnNames(implicit doc: SyntacticDocument): Set[String] =
    doc.tree.collect {
      case Defn.Val(_, List(Pat.Var(Term.Name(name))), _, rhs) if isColumnRhs(rhs) => name
      case Defn.Var(_, List(Pat.Var(Term.Name(name))), _, Some(rhs)) if isColumnRhs(rhs) => name
      case Defn.Def(_, Term.Name(name), _, _, _, rhs) if isColumnRhs(rhs) => name
    }.toSet

  override def fix(implicit doc: SyntacticDocument): Patch = {
    val skipNames   = excluded ++ udfNames
    val columnBound = columnNames
    doc.tree.collect {
      // Pattern: <ident>(col("<key>"))
      case t @ Term.Apply(
            Term.Name(name),
            List(Term.Apply(Term.Name("col"), List(Lit.String(key))))
          ) if columnBound.contains(name) && !skipNames.contains(name) =>
        Patch.replaceTree(t, s"""element_at($name, col("$key"))""")

      // Pattern: <ident>(col(<expr>))  — non-literal column expression
      case t @ Term.Apply(
            Term.Name(name),
            List(Term.Apply(Term.Name("col"), List(expr)))
          ) if columnBound.contains(name) && !skipNames.contains(name) =>
        Patch.replaceTree(t, s"element_at($name, col(${expr.syntax}))")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 3: ScosWildcardReadAnnotate
//
// Inserts a TODO annotation comment above any `spark.read.<fmt>("…*…")`
// call whose path argument contains a wildcard.  Handles all four common
// formats: csv, json, parquet, text.  String interpolation is flagged via
// the Term.Interpolate branch.
// ─────────────────────────────────────────────────────────────────────────────
class ScosWildcardReadAnnotate extends SyntacticRule("ScosWildcardReadAnnotate") {

  private val readFormats: Set[String] = Set("csv", "json", "parquet", "text")
  private val todoComment =
    "// SCOS: TODO - wildcard pattern in path; replace with explicit file list."

  private def isWildcardPath(path: String): Boolean = path.contains("*")

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      // spark.read.<fmt>("path/with/*")
      case t @ Term.Apply(
            Term.Select(
              Term.Select(Term.Name("spark"), Term.Name("read")),
              Term.Name(fmt)
            ),
            List(Lit.String(path))
          ) if readFormats.contains(fmt) && isWildcardPath(path) =>
        Patch.addLeft(t, s"$todoComment\n")

      // spark.read.<fmt>(s"path/${variable}/*") — string interpolation
      case t @ Term.Apply(
            Term.Select(
              Term.Select(Term.Name("spark"), Term.Name("read")),
              Term.Name(fmt)
            ),
            List(_: Term.Interpolate)
          ) if readFormats.contains(fmt) =>
        // Interpolated paths may contain wildcards — annotate conservatively
        Patch.addLeft(
          t,
          s"// SCOS: TODO - verify interpolated path contains no wildcard; " +
            "replace with explicit file list if so.\n"
        )
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 4: ScosSparkSessionBuilderRewrite
//
// Finds SparkSession.builder()…getOrCreate() chains and, for non-test files:
//   • renames SparkSession → SnowparkConnectSession
//   • drops .master(...), .enableHiveSupport(), .remote(...) from the chain
//   • ensures .builder() has parentheses
//
// In addition, for every file, emits SCOS-RECIPE-PRESERVED-CONFIG markers
// for every .config(k, v) call so the downstream Phase 3 import-updater
// can re-apply them on the SnowparkConnectSession.builder() chain after the
// LLM fixer has run (the Phase 2 marker-survival gate checks they survived).
//
// Test files (name ends with Test/Spec/Suite + .scala) are left on SparkSession
// so local integration harnesses keep their master("local[*]") runner — only
// the PRESERVED-CONFIG markers are emitted.
//
// Follows the same chain-rebuild pattern as ScosSaveAsTableDropStorageOpts.
// Idempotent: after rename the chain no longer contains "SparkSession", so a
// second pass is a no-op.
// ─────────────────────────────────────────────────────────────────────────────
class ScosSparkSessionBuilderRewrite extends SyntacticRule("ScosSparkSessionBuilderRewrite") {

  // Canonical marker format — MUST stay in sync with
  // the Phase 3 verifier (scripts/verify_phase.py `_PRESERVED_CFG_RE` +
  // `_verify_preserved_config`). The verifier re-wraps the captured key/value
  // in quotes when it searches for `.config("<k>", "<v>")`, so the marker MUST
  // carry the BARE inner values — NOT the quoted `.syntax`. Emitting `"k"="v"`
  // here (the previous bug) made the verifier look for `.config(""k"", ...)`
  // and always report the config as unmaterialized.
  private val markerPrefix = "// SCOS-RECIPE-PRESERVED-CONFIG: "

  // Bare string-literal value when the node is a string literal; otherwise the
  // surface syntax (computed key/value expression), normalized to a single line
  // so SCOS-RECIPE-PRESERVED-CONFIG comment markers never span multiple lines.
  // (Scalameta's .syntax pretty-printer can break long string interpolations
  // across lines; only the first line would be prefixed with //, causing Scala
  // parse errors on the continuation lines.)
  private def litOrSyntax(t: Tree): String = t match {
    case Lit.String(s) => s
    case _             =>
      t.syntax.linesIterator.map(_.trim).filter(_.nonEmpty).mkString(" ")
  }

  // SCOS-WARN marker for config forms that cannot be statically extracted
  // (e.g. `.config(externalMap)` / `.config(sparkConf)`). Surfaced so a dropped
  // config is never silent.
  private val warnMarker =
    "// SCOS-WARN: dropped non-extractable .config(...) \u2014 manual review required"

  // EWI marker for the session-builder rename itself.  Without it the rename was
  // invisible to scan_scos_comments / the migration header's Changes Overview.
  // Matches the PySpark sibling recipe (sparkcontext_getorcreate_init_session_rewrite
  // and implicit_spark_inject_bootstrap both stamp a [SPRKCNTPY1001-Fixed] code).
  private val renameMarker =
    "// SCOS: [SPRKCNTSCL3500-Fixed] ScosSparkSessionBuilderRewrite: " +
    "SparkSession.builder renamed to SnowparkConnectSession.builder " +
    "\u2014 SCOS uses a different session class; Phase 3 injects the import."

  // Walk a chained Term.Apply / Term.Select tree collecting every `.config(...)`
  // argument. Returns (extractable (k, v) pairs, sawNonExtractable). Handles both
  // `.config(k, v)` and `.config(Map("a" -> "b", ...))`; any other `.config(...)`
  // form sets the non-extractable flag so a SCOS-WARN is emitted instead of the
  // config being silently lost.
  private def walkConfigs(tree: Tree): (List[(String, String)], Boolean) = tree match {
    case Term.Apply(Term.Select(inner, Term.Name("config")), args) =>
      val (pairs, non) = walkConfigs(inner)
      args match {
        case List(k, v) =>
          (pairs :+ (litOrSyntax(k), litOrSyntax(v)), non)
        case List(Term.Apply(Term.Name("Map"), mapArgs)) =>
          val mp = mapArgs.collect {
            case Term.ApplyInfix(Lit.String(k), Term.Name("->"), _, List(Lit.String(v))) => (k, v)
          }
          (pairs ++ mp, non || mp.size != mapArgs.size)
        case _ =>
          (pairs, true)
      }
    case Term.Apply(Term.Select(inner, _), _) => walkConfigs(inner)
    case Term.Select(inner, _)                => walkConfigs(inner)
    case _                                     => (Nil, false)
  }

  private def isBuilderChain(tree: Tree): Boolean = tree match {
    case Term.Apply(Term.Select(inner, Term.Name("getOrCreate")), Nil) =>
      inner.syntax.contains("SparkSession") && inner.syntax.contains("builder")
    case _ => false
  }

  // True when the chain needs structural rewriting (contains SparkSession or
  // unsupported calls that must be dropped).
  private def dropUnsupported(name: String): Boolean =
    name == "enableHiveSupport" || name == "master" || name == "remote"

  private def chainNeedsRewrite(tree: Tree): Boolean = tree match {
    case Term.Apply(Term.Select(_, Term.Name(m)), _) if dropUnsupported(m) => true
    case Term.Apply(Term.Select(Term.Name("SparkSession"), Term.Name("builder")), _) => true
    case Term.Select(Term.Name("SparkSession"), Term.Name("builder")) => true
    case Term.Apply(Term.Select(inner, _), _) => chainNeedsRewrite(inner)
    case Term.Select(inner, _)                => chainNeedsRewrite(inner)
    case _                                    => false
  }

  // Rebuild the receiver chain: drop unsupported calls and rename SparkSession
  // → SnowparkConnectSession, adding () to a bare .builder if absent.
  // Follows the same pattern as ScosSaveAsTableDropStorageOpts.rebuild().
  private def rebuildChain(tree: Term): Term = tree match {
    case Term.Apply(Term.Select(inner, Term.Name(m)), _) if dropUnsupported(m) =>
      rebuildChain(inner)
    case Term.Apply(Term.Select(Term.Name("SparkSession"), Term.Name("builder")), Nil) =>
      Term.Apply(Term.Select(Term.Name("SnowparkConnectSession"), Term.Name("builder")), Nil)
    case Term.Select(Term.Name("SparkSession"), Term.Name("builder")) =>
      Term.Apply(Term.Select(Term.Name("SnowparkConnectSession"), Term.Name("builder")), Nil)
    case Term.Apply(Term.Select(inner, name), args) =>
      Term.Apply(Term.Select(rebuildChain(inner), name), args)
    case Term.Select(inner, name) =>
      Term.Select(rebuildChain(inner), name)
    case other => other
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    // Test files (by file-name convention) keep SparkSession so local harnesses
    // retain master("local[*]"). Phase 3 replace_session_init handles the TODO.
    val label = doc.input match {
      case Input.VirtualFile(path, _) => path
      case Input.File(path, _)        => path.toString
      case _                          => ""
    }
    val isTestFile = label.endsWith("Test.scala") ||
      label.endsWith("Spec.scala") || label.endsWith("Suite.scala")

    doc.tree.collect {
      case t @ Term.Apply(Term.Select(inner, Term.Name("getOrCreate")), Nil)
          if inner.syntax.contains("SparkSession") && inner.syntax.contains("builder") =>
        val (pairs, hasNonExtractable) = walkConfigs(t)
        // PRESERVED-CONFIG markers (one per extractable pair) then, if any config
        // could not be extracted, a single SCOS-WARN — emitted as one adjacent
        // block immediately above the builder chain so the verifier's adjacency
        // check passes.
        val markerLines =
          pairs.map { case (k, v) => s"$markerPrefix$k=$v" } ++
            (if (hasNonExtractable) List(warnMarker) else Nil)
        val markerText = if (markerLines.nonEmpty) markerLines.mkString("", "\n", "\n") else ""

        if (!isTestFile && chainNeedsRewrite(inner)) {
          // Rebuild: drop unsupported calls + rename SparkSession → SnowparkConnectSession.
          // Prepend the EWI rename marker so scan_scos_comments / the migration header
          // Changes Overview counts this as a session-init change (parity with PySpark
          // sparkcontext_getorcreate_init_session_rewrite + implicit_spark_inject_bootstrap).
          val rebuiltInner = rebuildChain(inner)
          val rebuiltCall = Term.Apply(Term.Select(rebuiltInner, Term.Name("getOrCreate")), Nil)
          Patch.replaceTree(t, renameMarker + "\n" + markerText + rebuiltCall.syntax)
        } else if (markerText.nonEmpty) {
          Patch.addLeft(t, markerText)
        } else {
          Patch.empty
        }
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 5: ScosSaveAsTableDropStorageOpts
//
// Drops unsupported `.format(...)` and `.option("path", …)` calls from a
// DataFrameWriter chain that terminates in `.saveAsTable(...)`.  SCOS-managed
// tables do not accept a writer file-format or an external storage path —
// Snowflake manages table storage internally — so on Snowpark Connect those
// calls are rejected / silently ignored.  This is the Scala method-chain analog
// of the PySpark recipe `saveastable_drop_format_path_kwargs_rewrite` (PySpark
// expresses the same intent through `format=`/`path=` kwargs).
//
//   df.write.format("parquet").option("path", "s3://…").mode("overwrite").saveAsTable("t")
//        →  df.write.mode("overwrite").saveAsTable("t")
//
// `.mode(...)`, `.partitionBy(...)`, and `.option("k", v)` for non-`path` keys
// are preserved verbatim.  Being AST-based it handles multi-line writer chains
// that the line-anchored regex layer cannot.  Idempotent: after the rewrite the
// chain carries no `.format`/`.option("path")`, so a second pass is a no-op.
// ─────────────────────────────────────────────────────────────────────────────
class ScosSaveAsTableDropStorageOpts extends SyntacticRule("ScosSaveAsTableDropStorageOpts") {

  private val comment =
    "// SCOS: dropped unsupported .format()/.option(\"path\", …) from saveAsTable chain " +
      "(Snowpark Connect manages table storage internally)"

  // A single writer-chain call that must be dropped.
  private def isDropped(tree: Tree): Boolean = tree match {
    case Term.Apply(Term.Select(_, Term.Name("format")), _) => true
    case Term.Apply(Term.Select(_, Term.Name("option")), List(Lit.String("path"), _)) => true
    case _ => false
  }

  // True iff the receiver chain contains at least one droppable call.
  private def chainHasDropped(tree: Tree): Boolean = tree match {
    case t if isDropped(t)                    => true
    case Term.Apply(Term.Select(inner, _), _) => chainHasDropped(inner)
    case Term.Select(inner, _)                => chainHasDropped(inner)
    case _                                    => false
  }

  // Rebuild the receiver chain omitting the dropped calls, preserving everything
  // else (including argument lists) unchanged.
  private def rebuild(tree: Term): Term = tree match {
    case Term.Apply(Term.Select(inner, Term.Name("format")), _) =>
      rebuild(inner)
    case Term.Apply(Term.Select(inner, Term.Name("option")), List(Lit.String("path"), _)) =>
      rebuild(inner)
    case Term.Apply(Term.Select(inner, name), args) =>
      Term.Apply(Term.Select(rebuild(inner), name), args)
    case Term.Select(inner, name) =>
      Term.Select(rebuild(inner), name)
    case other => other
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(recv, Term.Name("saveAsTable")), args)
          if chainHasDropped(recv) =>
        val rebuilt = Term.Apply(Term.Select(rebuild(recv), Term.Name("saveAsTable")), args)
        Patch.replaceTree(t, s"$comment\n${rebuilt.syntax}")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared AST helpers for the context-sensitive rules below.  These replace the
// regex tier's line-window heuristics ("loop within N lines") with true
// enclosing-scope analysis — more precise, never tricked by lexical proximity.
// ─────────────────────────────────────────────────────────────────────────────
private object ScosAst {
  @annotation.tailrec
  def hasAncestor(t: Tree, pred: Tree => Boolean): Boolean =
    t.parent match {
      case Some(p) => if (pred(p)) true else hasAncestor(p, pred)
      case None    => false
    }

  // True when the node is lexically inside a for / for-yield / while / do loop.
  def inLoop(t: Tree): Boolean = hasAncestor(t, {
    case _: Term.For | _: Term.ForYield | _: Term.While | _: Term.Do => true
    case _ => false
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 6: ScosExternalCloudReadAnnotate   (mirrors recipe: external_cloud_read_stage_perf_comment)
//
// Adds a perf-hint comment above `spark.read.<fmt>("<cloud-uri>")` reads
// (s3/gs/abfss/wasbs/…) recommending migration to a Snowflake stage.
// ─────────────────────────────────────────────────────────────────────────────
class ScosExternalCloudReadAnnotate extends SyntacticRule("ScosExternalCloudReadAnnotate") {
  private val cloudSchemes =
    Set("s3", "s3a", "gs", "gcs", "abfs", "abfss", "wasb", "wasbs", "azure", "adl", "oss", "oci")

  private def cloudScheme(p: String): Option[String] = {
    val idx = p.indexOf("://")
    if (idx <= 0) None
    else {
      val s = p.substring(0, idx).toLowerCase
      if (cloudSchemes.contains(s)) Some(s) else None
    }
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(Term.Select(Term.Name("spark"), Term.Name("read")), Term.Name(_)),
            List(Lit.String(path))
          ) if cloudScheme(path).isDefined =>
        val scheme = cloudScheme(path).get
        Patch.addLeft(
          t,
          s"// SCOS: Performance tip - $scheme read; consider migrating to a Snowflake stage for best performance\n"
        )
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 7: ScosSelfJoinUnaliasedAnnotate   (mirrors recipe: self_join_unaliased_warn_annotate)
//
// Annotates `df.join(df, …)` where the bare receiver identifier equals the bare
// first-argument identifier (unaliased self-join → ambiguous column refs).
// ─────────────────────────────────────────────────────────────────────────────
class ScosSelfJoinUnaliasedAnnotate extends SyntacticRule("ScosSelfJoinUnaliasedAnnotate") {
  private val comment =
    "// SCOS: TODO - self-join requires explicit aliases (e.g., df.alias(\"a\").join(df.alias(\"b\"), ...))"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(Term.Name(a), Term.Name("join")), args)
          if args.headOption.exists { case Term.Name(b) => a == b; case _ => false } =>
        Patch.addLeft(t, s"$comment\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 8: ScosSparkContextPropertyFallbackAnnotate
//                                       (mirrors recipe: sparkcontext_property_fallback_rewrite)
//
// Annotates `sc.parallelize(...)` / `sc.broadcast(...)` and the
// `spark.sparkContext.*` forms — unsupported / limited in Spark Connect.  The
// call is NOT rewritten (the correct conversion is context-dependent).
// ─────────────────────────────────────────────────────────────────────────────
class ScosSparkContextPropertyFallbackAnnotate
    extends SyntacticRule("ScosSparkContextPropertyFallbackAnnotate") {

  private val parallelizeComment =
    "// SCOS: [SPRKCNTSCL1500] sc.parallelize is unsupported in Snowpark Connect. " +
      "Convert to spark.createDataFrame \u2014 Seq of tuples/case classes: " +
      "createDataFrame(seq).toDF(names\u2026); Seq[Row] with a schema: " +
      "createDataFrame(seq.asJava, schema). Do NOT wrap elements in Tuple1 or nest createDataFrame."
  private val broadcastComment =
    "// SCOS: TODO - sc.broadcast not supported; " +
      "pass value directly or use spark.sparkContext.broadcast (limited)"

  private def isSc(t: Term): Boolean = t match {
    case Term.Name("sc")                           => true
    case Term.Select(_, Term.Name("sparkContext")) => true
    case _                                         => false
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(recv, Term.Name("parallelize")), _) if isSc(recv) =>
        Patch.addLeft(t, s"$parallelizeComment\n")
      case t @ Term.Apply(Term.Select(recv, Term.Name("broadcast")), _) if isSc(recv) =>
        Patch.addLeft(t, s"$broadcastComment\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 9: ScosUdtfCompatibilityModeAnnotate  (mirrors recipe: udtf_enable_compatibility_mode_rewrite)
//
// Annotates any class extending UserDefinedTableFunction / GenericUDTF with the
// required per-session compatibility-mode flag reminder.
// ─────────────────────────────────────────────────────────────────────────────
class ScosUdtfCompatibilityModeAnnotate extends SyntacticRule("ScosUdtfCompatibilityModeAnnotate") {
  private val udtfBases = Set("UserDefinedTableFunction", "GenericUDTF")
  private val comment =
    "// SCOS: TODO - UDTF compatibility mode required; " +
      "set spark.sql.execution.udtf.compatibility.mode=true"

  private def extendsUdtf(templ: Template): Boolean =
    templ.inits.exists { init =>
      init.tpe match {
        case Type.Name(n)                 => udtfBases.contains(n)
        case Type.Select(_, Type.Name(n)) => udtfBases.contains(n)
        case _                            => false
      }
    }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Defn.Class(_, _, _, _, templ) if extendsUdtf(templ) =>
        Patch.addLeft(t, s"$comment\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 10: ScosUnionByNameAllowMissingAnnotate
//                                  (mirrors recipe: unionbyname_allowmissing_schema_align_warn_annotate)
//
// Annotates `.unionByName(other, allowMissingColumns = true)` — SCOS may diverge
// on the missing-column fill behaviour, so a schema-align reminder is inserted.
// ─────────────────────────────────────────────────────────────────────────────
class ScosUnionByNameAllowMissingAnnotate
    extends SyntacticRule("ScosUnionByNameAllowMissingAnnotate") {
  private val comment =
    "// SCOS: TODO - schema-align before unionByName; " +
      "allowMissingColumns may behave differently on SCOS"

  private def allowMissingTrue(args: List[Term]): Boolean =
    args.exists {
      case Term.Assign(Term.Name("allowMissingColumns"), Lit.Boolean(true)) => true
      case _                                                                 => false
    }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(_, Term.Name("unionByName")), args) if allowMissingTrue(args) =>
        Patch.addLeft(t, s"$comment\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 11: ScosDriverHotPathAnnotate  (mirrors recipe: driver_materialization_hotpath_warn_annotate)
//
// Annotates `.collect()` / `.toLocalIterator()` / `.collectAsList()` calls that
// sit inside a loop (true enclosing-scope hot path).  The regex tier also keyed
// off a `def` within 5 lines; the AST rule uses the precise loop-scope signal to
// avoid annotating every one-shot materialization inside an arbitrary method.
// ─────────────────────────────────────────────────────────────────────────────
class ScosDriverHotPathAnnotate extends SyntacticRule("ScosDriverHotPathAnnotate") {
  private val materializers = Set("collect", "toLocalIterator", "collectAsList")
  private val comment =
    "// SCOS: Performance tip - driver materialization in hot path; consider .show() or write-to-table"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(_, Term.Name(m)), Nil)
          if materializers.contains(m) && ScosAst.inLoop(t) =>
        Patch.addLeft(t, s"$comment\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 12: ScosTempViewMultiUseCache  (mirrors recipe: tempview_multiuse_cache_rewrite)
//
// Inserts `<recv>.cache()` before `<recv>.createOrReplaceTempView("v")` when the
// view name is read in ≥2 `FROM v` clauses across the file's SQL string literals.
// Skips when the receiver is already cached/persisted earlier in the same block
// (idempotent re-runs).
// ─────────────────────────────────────────────────────────────────────────────
class ScosTempViewMultiUseCache extends SyntacticRule("ScosTempViewMultiUseCache") {

  private def fromCount(view: String, strings: List[String]): Int = {
    val pat = ("(?i)\\bFROM\\s+" + java.util.regex.Pattern.quote(view) + "\\b").r
    strings.count(s => pat.findFirstIn(s).isDefined)
  }

  private def isCache(s: Tree, recv: String): Boolean = s match {
    case Term.Apply(Term.Select(Term.Name(r), Term.Name(m)), _)
        if r == recv && (m == "cache" || m == "persist") =>
      true
    case _ => false
  }

  private def alreadyCached(t: Tree, recv: String): Boolean = t.parent match {
    case Some(b: Term.Block) => b.stats.exists(s => s.pos.start < t.pos.start && isCache(s, recv))
    case Some(tm: Template)  => tm.stats.exists(s => s.pos.start < t.pos.start && isCache(s, recv))
    case _                   => false
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    val strings: List[String] = doc.tree.collect { case Lit.String(s) => s }
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(Term.Name(recv), Term.Name("createOrReplaceTempView")),
            List(Lit.String(view))
          ) if fromCount(view, strings) >= 2 && !alreadyCached(t, recv) =>
        Patch.replaceTree(t, s"$recv.cache()" + "\n" + s"""$recv.createOrReplaceTempView("$view")""")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 13: ScosSystemGetenvRewrite
//
// Rewrites System.getenv("K") → System.getProperty("K").
//
// EnvUtil.setEnv (harness kit) writes values via System.setProperty + an internal
// override map.  System.getenv reads the OS process environment which the JVM
// cannot mutate in-process, so harness-injected values (SCOS_INPUT_*, SCOS_SINK_*,
// SCOS_OUTPUT_SCHEMA, etc.) are never visible to code calling System.getenv.
// This rewrite is purely syntactic, safe, and has no false positives on string
// literals because it matches the method-call AST form.
// ─────────────────────────────────────────────────────────────────────────────
class ScosSystemGetenvRewrite extends SyntacticRule("ScosSystemGetenvRewrite") {
  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      // System.getenv("literalKey") → System.getProperty("literalKey")
      case t @ Term.Apply(
            Term.Select(Term.Name("System"), Term.Name("getenv")),
            List(arg: Lit.String)
          ) =>
        Patch.replaceTree(t, s"System.getProperty(${arg.syntax})")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 14: ScosDeltaTableAnnotate
//
// Annotates DeltaTable.forPath / DeltaTable.forName / DeltaTable.forUid call sites.
//
// Phase 3 (update_imports_scala.py) deletes `import delta.tables` lines.  Any
// DeltaTable.* call site left without its import causes an unresolved-reference
// compile error that the Phase 2b gate would revert.  This rule adds a visible
// SCOS annotation above each call so the human reviewer knows exactly which lines
// need manual rewriting to spark.read.table() / spark.sql().
//
// Annotation-only: no code rewrite.  The DeltaTable API has no direct SCOS
// equivalent — the rewrite requires knowing the storage path and deciding whether
// the data should become a Snowflake table or a stage read.
// ─────────────────────────────────────────────────────────────────────────────
class ScosDeltaTableAnnotate extends SyntacticRule("ScosDeltaTableAnnotate") {

  private val deltaTableMethods = Set("forPath", "forName", "forUid", "columnExists", "isDeltaTable")

  override def fix(implicit doc: SyntacticDocument): Patch = {
    val comment = "// SCOS: [SPRKCNTSCL1000] DeltaTable API not available in SCOS — " +
                  "rewrite to spark.read.table() or spark.sql(); manual refactor required"
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(Term.Name("DeltaTable"), Term.Name(method)),
            _
          ) if deltaTableMethods.contains(method) =>
        Patch.addLeft(t, s"$comment\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 15: ScosPartitionNoopStrip
//
// Strips .coalesce(n) / .repartition(...) / .repartitionByRange(...) no-ops
// from DataFrame chains: SCOS manages partitioning internally and these methods
// have no effect.
//
// Critical false-positive guard: functions.coalesce(col1, col2) (the Column
// null-coalescing SQL function) must NOT be stripped. We only strip the
// DataFrame *method* form — a call whose receiver is NOT the functions module
// (F / f / functions / <x>.functions). Bare coalesce(...) import-call form
// (no receiver) is never matched because Term.Select is required.
//
// Parity: mirrors PySpark recipe dataframe_partition_noop_strip_rewrite
// (PR #3344, scos-migration-recipes).
// ─────────────────────────────────────────────────────────────────────────────
class ScosPartitionNoopStrip extends SyntacticRule("ScosPartitionNoopStrip") {

  private val noopMethods = Set("coalesce", "repartition", "repartitionByRange")

  private def isFunctionsModule(expr: Term): Boolean = expr match {
    case Term.Name(n) if Set("F", "f", "functions").contains(n) => true
    case Term.Select(_, Term.Name("functions"))                  => true
    case _                                                       => false
  }

  private val ewi =
    "// SCOS: [SPRKCNTSCL1500] ScosPartitionNoopStrip: removed no-op " +
    ".coalesce()/.repartition() \u2014 Snowflake manages partitioning (no effect in SCOS)"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(recv, Term.Name(method)),
            _
          ) if noopMethods.contains(method) && !isFunctionsModule(recv) =>
        Patch.replaceTree(t, s"$ewi\n${recv.syntax}")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 16: ScosDeltaWriteToParquet
//
// Rewrites .write.format("delta") → .write.format("parquet").
// Delta is not a supported write format in SCOS / Snowpark Connect;
// Parquet writes are routed through a stage and work correctly.
//
// Gating (mirrors the Python recipe delta_write_to_parquet_rewrite PR #3344):
//   * Only fires on a DataFrameWriter chain — the .format("delta") receiver
//     must contain a .write or .writeStream selector upstream.
//   * Skips files that use DeltaTable transactional API
//     (DeltaTable.forPath / .forName / .forUid) because those have no
//     safe Parquet equivalent and must be handled manually.
//
// Parity: mirrors PySpark recipe delta_write_to_parquet_rewrite
// (PR #3344, scos-migration-recipes).
// ─────────────────────────────────────────────────────────────────────────────
class ScosDeltaWriteToParquet extends SyntacticRule("ScosDeltaWriteToParquet") {

  private def hasWriteReceiver(tree: Term): Boolean = tree match {
    case Term.Select(_, Term.Name("write"))        => true
    case Term.Select(_, Term.Name("writeStream"))  => true
    case Term.Apply(Term.Select(inner, _), _)      => hasWriteReceiver(inner)
    case Term.Select(inner, _)                     => hasWriteReceiver(inner)
    case _                                         => false
  }

  private val comment =
    "// SCOS: [SPRKCNTSCL1000] ScosDeltaWriteToParquet: .format(\"delta\") not supported \u2014 " +
    "rewrote to .format(\"parquet\"); ACID/merge/time-travel are lost \u2014 verify path is a stage"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    // Skip file if DeltaTable transactional API is present (merge/update/upsert)
    val hasDeltaTableApi = doc.tree.collect {
      case Term.Select(Term.Name("DeltaTable"), Term.Name(m))
          if Set("forPath", "forName", "forUid").contains(m) => ()
    }.nonEmpty
    if (hasDeltaTableApi) return Patch.empty

    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(recv, Term.Name("format")),
            List(Lit.String("delta"))
          ) if hasWriteReceiver(recv) =>
        Patch.replaceTree(t, s"""$comment\n${recv.syntax}.format("parquet")""")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 17: ScosDisplayToShow
//
// Rewrites Databricks display(df) → df.show().
// display is a Databricks-notebook-only global that does not exist in SCOS /
// Snowpark Connect; a bare call raises ClassNotFoundError at runtime.
// The standard migration is DataFrame.show().
//
// Only rewrites bare display(<single-positional>) — does NOT match:
//   obj.display(...)   (method call, not the Databricks global)
//   display()          (no argument)
//   display(a, b)      (multiple arguments — streaming / options form)
//
// Parity: mirrors PySpark recipe display_to_show_rewrite (PR #3344,
// scos-migration-recipes).
// ─────────────────────────────────────────────────────────────────────────────
class ScosDisplayToShow extends SyntacticRule("ScosDisplayToShow") {

  private val comment =
    "// SCOS: [SPRKCNTSCL1500] ScosDisplayToShow: display() not available \u2014 " +
    "replaced with .show() (note: .show() prints 20 rows; pass n for more)"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      // Only bare display(singleArg) — NOT method calls (Term.Select receivers)
      case t @ Term.Apply(Term.Name("display"), List(arg)) =>
        Patch.replaceTree(t, s"$comment\n${arg.syntax}.show()")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 20: ScosDisplayMethodToShow
//
// Rewrites the Databricks DataFrame *method* form df.display() → df.show().
//
// Databricks Runtime 13+ exposes DataFrame.display() as a zero-arg instance
// method in addition to the notebook-global display(df) helper. The method
// form fails with a NoSuchMethodException / AttributeError on SCOS / Snowpark
// Connect, so it must be replaced with the standard DataFrame renderer.
//
//   df.display()                        →   df.show()
//   spark.table("t").display()          →   spark.table("t").show()
//   df.filter(cond).display()           →   df.filter(cond).show()
//
// The *bare* global form display(df) is handled by ScosDisplayToShow. This
// rule owns only the zero-arg *method* form (.display() with no arguments).
//
// Negative cases (must NOT trigger):
//   obj.display(x)     — method call with arguments; left for LLM fixer.
//   display(df)        — bare global helper; ScosDisplayToShow handles it.
//   obj.display        — bare select without call.
//
// Parity: mirrors PySpark recipe dataframe_display_method_to_show_rewrite
// (PR #3487, scos-migration-recipes).
// ─────────────────────────────────────────────────────────────────────────────
class ScosDisplayMethodToShow extends SyntacticRule("ScosDisplayMethodToShow") {

  private val comment =
    "// SCOS: [SPRKCNTSCL1500] ScosDisplayMethodToShow: df.display() not available \u2014 " +
    "replaced with .show() (note: .show() prints 20 rows; pass n for more)"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      // <recv>.display() — zero-arg method call only
      case t @ Term.Apply(Term.Select(recv, Term.Name("display")), Nil) =>
        Patch.replaceTree(t, s"$comment\n${recv.syntax}.show()")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 18: ScosDbUtilsWidgetsToProperty
//
// Rewrites dbutils.widgets.* parameter calls to System.getProperty /
// System.setProperty (the JVM equivalent of the Python os.environ rewrite).
//
// dbutils.widgets is a Databricks-notebook-only API; it does not exist in SCOS.
// Rewrites:
//   dbutils.widgets.get("key")             → System.getProperty("key")
//   dbutils.widgets.getArgument("k","d")   → System.getProperty("k", "d")
//   dbutils.widgets.text("k","default")    → System.setProperty("k","default") + TODO
//   dbutils.widgets.remove / removeAll(…)  → comment stub (no JVM equivalent)
//   dbutils.widgets.dropdown/combobox/multiselect("k","d") → System.setProperty
//
// Does NOT match non-dbutils receivers (x.widgets.get(...)).
//
// Parity: mirrors PySpark recipe dbutils_widgets_to_env_rewrite (PR #3344,
// scos-migration-recipes), adapted from os.environ → System.getProperty.
// ─────────────────────────────────────────────────────────────────────────────
class ScosDbUtilsWidgetsToProperty extends SyntacticRule("ScosDbUtilsWidgetsToProperty") {

  private val todoPrefix =
    "// SCOS-TODO: [SPRKCNTSCL1500] ScosDbUtilsWidgetsToProperty: " +
    "dbutils.widgets has no SCOS equivalent; mapped to System.getProperty"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      // dbutils.widgets.get("key") / getArgument("key","default")
      //   → System.getProperty("key") / System.getProperty("key","default")
      case t @ Term.Apply(
            Term.Select(
              Term.Select(Term.Name("dbutils"), Term.Name("widgets")),
              Term.Name("get" | "getArgument")
            ),
            args
          ) =>
        val argsText = args.map(_.syntax).mkString(", ")
        Patch.replaceTree(t, s"$todoPrefix\nSystem.getProperty($argsText)")

      // dbutils.widgets.text / dropdown / combobox / multiselect("key","default",...)
      //   → System.setProperty("key","default")
      case t @ Term.Apply(
            Term.Select(
              Term.Select(Term.Name("dbutils"), Term.Name("widgets")),
              Term.Name("text" | "dropdown" | "combobox" | "multiselect")
            ),
            (keyArg @ Lit.String(_)) :: (defArg @ Lit.String(_)) :: _
          ) =>
        Patch.replaceTree(t,
          s"""$todoPrefix\nSystem.setProperty(${keyArg.syntax}, ${defArg.syntax})""")

      // dbutils.widgets.remove / removeAll — no JVM equivalent; strip with comment
      case t @ Term.Apply(
            Term.Select(
              Term.Select(Term.Name("dbutils"), Term.Name("widgets")),
              Term.Name("remove" | "removeAll")
            ),
            _
          ) =>
        Patch.replaceTree(t,
          "// SCOS: [SPRKCNTSCL1500] ScosDbUtilsWidgetsToProperty: " +
          "dbutils.widgets.remove() stripped \u2014 no JVM equivalent")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 19: ScosDbUtilsSecretsGetStub
//
// Stubs dbutils.secrets.get(...) / dbutils.secrets.getBytes(...) with
// null.asInstanceOf[String] + a migration TODO comment.
//
// Databricks dbutils.secrets has no Snowpark Connect / Snowflake Workspace
// equivalent. Leaving the call causes a ClassNotFoundError / NameError at
// runtime and masks every downstream issue in the file.
//
// Only stubs .get / .getBytes (targeted). Other dbutils.secrets.* methods
// (list / listScopes) are left for LLM fixer.
// Does NOT match non-dbutils receivers.
//
// Parity: mirrors PySpark recipe dbutils_secrets_get_stub_rewrite (PR #3348,
// scos-migration-dbutils-recipes).
// ─────────────────────────────────────────────────────────────────────────────
class ScosDbUtilsSecretsGetStub extends SyntacticRule("ScosDbUtilsSecretsGetStub") {

  private val stub =
    "null.asInstanceOf[String] " +
    "// SCOS-TODO: [SPRKCNTSCL1500] ScosDbUtilsSecretsGetStub: " +
    "dbutils.secrets has no SCOS equivalent; stubbed to null \u2014 migrate to Snowflake Secrets"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(
              Term.Select(Term.Name("dbutils"), Term.Name("secrets")),
              Term.Name("get" | "getBytes")
            ),
            _
          ) =>
        Patch.replaceTree(t, stub)
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 21: ScosSnowflakeConnectorIO
//
// Rewrites the Spark Snowflake connector read/write to SCOS-native form.
//
// Under Snowpark Connect the workload runs inside Snowflake, so the external
// Spark Snowflake connector (.format("snowflake") /
// .format("net.snowflake.spark.snowflake")) is unnecessary.
//
// Reads:
//   spark.read.format("snowflake").option("query", Q).load()
//     → new SnowflakeSession(spark).sql(Q)
//   spark.read.format("snowflake").option("dbtable", T).load()
//     → new SnowflakeSession(spark).sql("SELECT * FROM T")
//
// Writes:
//   df.write.format("snowflake").option("dbtable", T)[.mode(m)].save()
//     → df.write[.mode(m)].saveAsTable(T)
//
// Non-literal / non-extractable options: SCOS-TODO annotation only (no rewrite).
// The read rewrite emits a SCOS-RECIPE-INSERT-IMPORT marker so Phase 3
// (update_imports_scala.py) can inject:
//   import com.snowflake.snowpark_connect.client.SnowflakeSession
//
// Never use bare spark.sql(...) as the replacement for a Snowflake-connector
// read — it is parsed as Spark SQL and breaks on Snowflake-specific syntax.
// SnowflakeSession.sql() wraps the statement with the PRIVATE-SNOWFLAKE-SQL
// pass-through marker.
//
// Parity: mirrors PySpark recipe snowflake_connector_io_to_snowflake_session_rewrite
// (PR #3532, scos-migration-recipes).
// ─────────────────────────────────────────────────────────────────────────────
class ScosSnowflakeConnectorIO extends SyntacticRule("ScosSnowflakeConnectorIO") {

  private val SF_FORMATS: Set[String] = Set("snowflake", "net.snowflake.spark.snowflake")

  private val READ_COMMENT: String =
    "// SCOS: [SPRKCNTSCL1000-Fixed] ScosSnowflakeConnectorIO: " +
    "read.format(\"snowflake\").load() \u2192 new SnowflakeSession(sess).sql() " +
    "(never use bare spark.sql() for Snowflake-specific SQL)"

  private val WRITE_COMMENT: String =
    "// SCOS: [SPRKCNTSCL1000-Fixed] ScosSnowflakeConnectorIO: " +
    "write.format(\"snowflake\").save() \u2192 .write.saveAsTable() (native managed-table write)"

  private val TODO_COMMENT: String =
    "// SCOS: TODO - [SPRKCNTSCL1000-IO] ScosSnowflakeConnectorIO: " +
    "Snowflake connector I/O with non-literal options; " +
    "convert to new SnowflakeSession(sess).sql(...) for reads or " +
    ".write.saveAsTable(...) for writes manually"

  private val IMPORT_MARKER: String =
    "// SCOS-RECIPE-INSERT-IMPORT: com.snowflake.snowpark_connect.client.SnowflakeSession"

  // ── chain analysis ────────────────────────────────────────────────────────
  private sealed trait ChainBase
  private case class ReadBase(recv: Term)  extends ChainBase
  private case class WriteBase(recv: Term) extends ChainBase
  private case object UnknownBase extends ChainBase

  private case class Chain(
    base: ChainBase = UnknownBase,
    formats: List[String] = Nil,
    options: Map[String, Term] = Map.empty,
    mode: Option[Term] = None,
    ambiguous: Boolean = false
  ) {
    def isSF: Boolean = formats.exists(f => SF_FORMATS(f.toLowerCase))
  }

  private def litStr(t: Term): Option[String] = t match {
    case Lit.String(v) => Some(v)
    case _ => None
  }

  /** Walk a Spark builder chain bottom-up, collecting format/option/mode/base. */
  private def walk(node: Term): Chain = node match {
    case Term.Apply(Term.Select(recv, Term.Name("format")), List(arg)) =>
      val c = walk(recv)
      litStr(arg).fold(c)(f => c.copy(formats = f :: c.formats))

    case Term.Apply(Term.Select(recv, Term.Name("option")), List(k, v)) =>
      val c = walk(recv)
      litStr(k).fold(c.copy(ambiguous = true))(key => c.copy(options = c.options + (key -> v)))

    case Term.Apply(Term.Select(recv, Term.Name("options")), _) =>
      walk(recv).copy(ambiguous = true)

    case Term.Apply(Term.Select(recv, Term.Name("mode")), List(m)) =>
      walk(recv).copy(mode = Some(m))

    case Term.Select(recv, n: Term.Name) if n.value == "read" =>
      Chain(base = ReadBase(recv))

    case Term.Select(recv, n: Term.Name)
        if n.value == "write" || n.value == "writeStream" =>
      Chain(base = WriteBase(recv))

    case _ => Chain()
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {

      // ── READ terminator: .load() with no path args ────────────────────────
      case t @ Term.Apply(Term.Select(recv, Term.Name("load")), Nil) =>
        val c = walk(recv)
        c.base match {
          case ReadBase(sess) if c.isSF =>
            if (c.ambiguous ||
                (!c.options.contains("query") && !c.options.contains("dbtable"))) {
              Patch.addLeft(t.tokens.head, TODO_COMMENT + "\n")
            } else {
              val sqlExpr: String =
                if (c.options.contains("query")) c.options("query").syntax
                else {
                  val tb = c.options("dbtable")
                  litStr(tb)
                    .fold("\"SELECT * FROM \" + " + tb.syntax)(n => "\"SELECT * FROM " + n + "\"")
                }
              Patch.replaceTree(t,
                READ_COMMENT + "\n" + IMPORT_MARKER + "\n" +
                "new SnowflakeSession(" + sess.syntax + ").sql(" + sqlExpr + ")")
            }
          case _ => Patch.empty
        }

      // ── WRITE terminator: .save() with no args ────────────────────────────
      case t @ Term.Apply(Term.Select(recv, Term.Name("save")), Nil) =>
        val c = walk(recv)
        c.base match {
          case WriteBase(df) if c.isSF =>
            if (c.ambiguous || !c.options.contains("dbtable")) {
              Patch.addLeft(t.tokens.head, TODO_COMMENT + "\n")
            } else {
              val tbl   = c.options("dbtable").syntax
              val mPart = c.mode.fold("")(m => ".mode(" + m.syntax + ")")
              Patch.replaceTree(t,
                WRITE_COMMENT + "\n" + df.syntax + ".write" + mPart + ".saveAsTable(" + tbl + ")")
            }
          case _ => Patch.empty
        }

    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 22: ScosApproxCountDistinctDropRsd
//
// Drops the rsd (relative standard deviation) positional argument from
// approxCountDistinct / approx_count_distinct, which is not accepted by SCOS:
//
//   approxCountDistinct(col, 0.05)  →  approxCountDistinct(col)
//
// Parity: mirrors PySpark recipe approx_count_distinct_drop_rsd_rewrite.
// ─────────────────────────────────────────────────────────────────────────────
class ScosApproxCountDistinctDropRsd extends SyntacticRule("ScosApproxCountDistinctDropRsd") {

  private val comment =
    "// SCOS: [SPRKCNTSCL1000] ScosApproxCountDistinctDropRsd: rsd arg dropped " +
    "\u2014 approxCountDistinct(col, rsd) \u2192 approxCountDistinct(col)"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            func @ Term.Name("approxCountDistinct" | "approx_count_distinct"),
            List(col, _)
          ) =>
        Patch.replaceTree(t, s"$comment\n${func.syntax}(${col.syntax})")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 23: ScosHadoopConfCredentialAnnotate
//
// Annotates Hadoop/SparkContext credential config calls with a SCOS-TODO.
// These settings have no effect in SCOS (storage access is via Snowflake conn).
//
// Parity: mirrors PySpark recipe hadoop_conf_credential_todo_annotate.
// ─────────────────────────────────────────────────────────────────────────────
class ScosHadoopConfCredentialAnnotate
    extends SyntacticRule("ScosHadoopConfCredentialAnnotate") {

  private val HADOOP_PREFIXES: Set[String] =
    Set("fs.s3", "fs.azure", "fs.gs", "fs.adl", "fs.abfs",
        "spark.hadoop.fs", "hadoop.fs", "dfs.adls")

  private val comment =
    "// SCOS: TODO - [SPRKCNTSCL1000] ScosHadoopConfCredentialAnnotate: " +
    "Hadoop credential config has no effect in SCOS; " +
    "storage access is governed by the Snowflake connection"

  private def isHadoopKey(k: Term): Boolean = k match {
    case Lit.String(v) => HADOOP_PREFIXES.exists(v.startsWith)
    case _ => false
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(
              Term.Apply(Term.Select(_, Term.Name("hadoopConfiguration")), _),
              Term.Name("set")
            ),
            List(k, _)
          ) if isHadoopKey(k) =>
        Patch.addLeft(t.tokens.head, comment + "\n")

      case t @ Term.Apply(
            Term.Select(Term.Select(_, Term.Name("conf")), Term.Name("set")),
            List(k, _)
          ) if isHadoopKey(k) =>
        Patch.addLeft(t.tokens.head, comment + "\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 24: ScosRddImportAnnotate
//
// Annotates import org.apache.spark.rdd.* statements with a SCOS-TODO.
//
// Parity: mirrors PySpark recipe pyspark_rdd_import_todo_annotate.
// ─────────────────────────────────────────────────────────────────────────────
class ScosRddImportAnnotate extends SyntacticRule("ScosRddImportAnnotate") {

  private val comment =
    "// SCOS: TODO - [SPRKCNTSCL1500] ScosRddImportAnnotate: " +
    "org.apache.spark.rdd imports are not supported in Snowpark Connect; " +
    "rewrite all RDD usages to DataFrames (see references/scala/rdd-conversion.md)"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Import(List(Importer(ref, _)))
          if ref.syntax.startsWith("org.apache.spark.rdd") =>
        Patch.addLeft(t.tokens.head, comment + "\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 25: ScosRddExclusiveMethodAnnotate
//
// Annotates calls to RDD-exclusive PairRDD/partition methods that have no
// direct DataFrame equivalent (code preserved, TODO added above).
//
// Parity: mirrors PySpark recipe rdd_exclusive_method_todo_annotate.
// ─────────────────────────────────────────────────────────────────────────────
class ScosRddExclusiveMethodAnnotate extends SyntacticRule("ScosRddExclusiveMethodAnnotate") {

  private val EXCLUSIVE: Set[String] = Set(
    "reduceByKey", "reduceByKeyLocally", "groupByKey", "aggregateByKey",
    "foldByKey", "combineByKey", "sampleByKey", "countByKey", "countByValue",
    "mapValues", "flatMapValues", "keyBy", "zipWithIndex", "zipWithUniqueId",
    "sortByKey", "mapPartitions", "mapPartitionsWithIndex",
    "takeOrdered", "takeSample", "saveAsTextFile",
    // Aggregate recipes + §10 "additional verified RDD operations" — RDD-exclusive
    // names (no DataFrame homonym). MUST stay in agreement with analyze_scala's
    // §10 exclusive tokens (RDD_METHODS additions); the L6 reference-sync test is
    // the drift guard. treeAggregate/treeReduce have a df.agg(...) [Workaround];
    // the rest are RDD-only names surfaced for manual guidance.
    "treeAggregate", "treeReduce", "collectAsMap", "countApprox",
    "countApproxDistinct", "meanApprox", "sumApprox",
    "repartitionAndSortWithinPartitions", "toDebugString"
  )

  private def comment(m: String): String =
    "// SCOS: TODO - [SPRKCNTSCL1500] ScosRddExclusiveMethodAnnotate: " +
    s"RDD.$m() is unsupported in Snowpark Connect; " +
    "migrate to the DataFrame equivalent (see references/scala/rdd-conversion.md)"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(_, Term.Name(m)), _) if EXCLUSIVE(m) =>
        Patch.addLeft(t.tokens.head, comment(m) + "\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 25b: ScosAccumulatorAnnotate
//
// Annotates Spark accumulator markers.  Accumulators are CONVERTIBLE, not a
// blanket no-equivalent gap: a driver-side count/sum/min/max/collect accumulator
// maps to a DataFrame `agg` (§6.10-6.14).  Only cache-hit / mid-job `acc.value` /
// foreachPartition / writeStream.foreachBatch usages are TRUE hard gaps.  The
// annotation is added above the marker (code preserved) so the reviewer routes it
// to the right conversion instead of dropping the whole file to a TODO.
//
// Markers detected (mirrors analyze_scala's accumulator classification):
//   • factory calls  — sc.longAccumulator(…) / spark.sparkContext.doubleAccumulator(…)
//                       / .collectionAccumulator(…)  (any receiver)
//   • type usages    — new LongAccumulator / DoubleAccumulator / CollectionAccumulator
//                       and `class X extends AccumulatorV2[…]`
//
// NOTE: there is NO sc.accumulator / AccumulatorParam API in Scala (that is a
// PySpark spelling), so this rule keys off the real Scala accumulator names, not
// a bare `accumulator` substring. It matches the method/type name on ANY receiver
// (like the sibling RDD rules), so an unrelated user symbol literally named e.g.
// `metrics.longAccumulator(...)` or `com.acme.LongAccumulator` would also be
// annotated — acceptable because the rule only ADDS a comment (code is preserved).
// ─────────────────────────────────────────────────────────────────────────────
class ScosAccumulatorAnnotate extends SyntacticRule("ScosAccumulatorAnnotate") {

  private val factoryMethods =
    Set("longAccumulator", "doubleAccumulator", "collectionAccumulator")
  private val accumulatorTypes =
    Set("LongAccumulator", "DoubleAccumulator", "CollectionAccumulator", "AccumulatorV2")

  private val comment =
    "// SCOS: TODO - [SPRKCNTSCL1500] ScosAccumulatorAnnotate: accumulator is CONVERTIBLE, " +
    "not a blanket TODO — a driver-side count/sum/min/max/collect accumulator maps to a " +
    "DataFrame agg (§6.10-6.14); only cache-hit / mid-job acc.value / foreachPartition / " +
    "writeStream.foreachBatch are hard gaps (see references/scala/rdd-conversion.md)"

  private def isAccumulatorType(t: Type): Boolean = t match {
    case Type.Name(n)                 => accumulatorTypes.contains(n)
    case Type.Select(_, Type.Name(n)) => accumulatorTypes.contains(n)
    case Type.Apply(inner, _)         => isAccumulatorType(inner)
    case _                            => false
  }

  private def extendsAccumulator(templ: Template): Boolean =
    templ.inits.exists(init => isAccumulatorType(init.tpe))

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      // sc.longAccumulator(…) / spark.sparkContext.collectionAccumulator(…) / …
      case t @ Term.Apply(Term.Select(_, Term.Name(m)), _) if factoryMethods.contains(m) =>
        Patch.addLeft(t.tokens.head, comment + "\n")

      // new LongAccumulator / new DoubleAccumulator[…] / new CollectionAccumulator
      case t @ Term.New(Init(tpe, _, _)) if isAccumulatorType(tpe) =>
        Patch.addLeft(t.tokens.head, comment + "\n")

      // class MyAcc extends AccumulatorV2[…] { … }
      case t @ Defn.Class(_, _, _, _, templ) if extendsAccumulator(templ) =>
        Patch.addLeft(t.tokens.head, comment + "\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 26: ScosRddPersistToCache
//
// Rewrites df.rdd.persist(...) / df.rdd.cache() to df.persist() / df.cache().
//
// Parity: mirrors PySpark recipe rdd_persist_to_cache_rewrite.
// ─────────────────────────────────────────────────────────────────────────────
class ScosRddPersistToCache extends SyntacticRule("ScosRddPersistToCache") {

  private val comment =
    "// SCOS: [SPRKCNTSCL1000] ScosRddPersistToCache: " +
    "df.rdd.persist/cache() \u2192 df.persist/cache() (.rdd not available in SCOS)"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(Term.Select(base, Term.Name("rdd")), Term.Name(m)),
            args
          ) if m == "persist" || m == "cache" =>
        val argList =
          if (args.isEmpty) "()"
          else "(" + args.map(_.syntax).mkString(", ") + ")"
        Patch.replaceTree(t, s"$comment\n${base.syntax}.$m$argList")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 27: ScosScRangeToSparkRange
//
// Rewrites sc.range(N) → spark.range(N).
//
// Parity: mirrors PySpark recipe sc_range_to_spark_range_rewrite.
// ─────────────────────────────────────────────────────────────────────────────
class ScosScRangeToSparkRange extends SyntacticRule("ScosScRangeToSparkRange") {

  private val comment =
    "// SCOS: [SPRKCNTSCL1500] ScosScRangeToSparkRange: " +
    "sc.range() \u2192 spark.range() (SparkContext not available in SCOS)"

  private def isSc(t: Term): Boolean = t match {
    case Term.Name("sc")                           => true
    case Term.Select(_, Term.Name("sparkContext")) => true
    case _                                         => false
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(recv, Term.Name("range")), args) if isSc(recv) =>
        val argStr = args.map(_.syntax).mkString(", ")
        Patch.replaceTree(t, s"$comment\nspark.range($argStr)")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 28: ScosScTextfileToReadText
//
// Rewrites sc.textFile("path") → spark.read.text("path") (numPartitions dropped).
//
// Parity: mirrors PySpark recipe sc_textfile_to_read_text_rewrite.
// ─────────────────────────────────────────────────────────────────────────────
class ScosScTextfileToReadText extends SyntacticRule("ScosScTextfileToReadText") {

  private val comment =
    "// SCOS: [SPRKCNTSCL1500] ScosScTextfileToReadText: " +
    "sc.textFile() \u2192 spark.read.text() (numPartitions arg dropped if present)"

  private def isSc(t: Term): Boolean = t match {
    case Term.Name("sc")                           => true
    case Term.Select(_, Term.Name("sparkContext")) => true
    case _                                         => false
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(recv, Term.Name("textFile")), args) if isSc(recv) =>
        val pathArg = args.headOption.fold("???")(_.syntax)
        Patch.replaceTree(t, s"$comment\nspark.read.text($pathArg)")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 29: ScosScWholeTextFilesAnnotate
//
// Annotates sc.wholeTextFiles("path") — no direct DataFrame equivalent.
//
// Parity: mirrors PySpark recipe sc_wholetextfiles_to_read_text_rewrite (TODO form).
// ─────────────────────────────────────────────────────────────────────────────
class ScosScWholeTextFilesAnnotate extends SyntacticRule("ScosScWholeTextFilesAnnotate") {

  private val comment =
    "// SCOS: TODO - [SPRKCNTSCL1500] ScosScWholeTextFilesAnnotate: " +
    "sc.wholeTextFiles() returns (filename, content) pairs with no direct DataFrame " +
    "equivalent; convert to spark.read.text() + per-file grouping"

  private def isSc(t: Term): Boolean = t match {
    case Term.Name("sc")                           => true
    case Term.Select(_, Term.Name("sparkContext")) => true
    case _                                         => false
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(recv, Term.Name("wholeTextFiles")), _) if isSc(recv) =>
        Patch.addLeft(t.tokens.head, comment + "\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 30: ScosSparkContextGetOrCreateRewrite
//
// Rewrites SparkContext bootstrap to SnowparkConnectSession:
//   SparkContext.getOrCreate()  →  SnowparkConnectSession.builder().getOrCreate()
//   SparkContext.getOrCreate(conf) / new SparkContext(conf)  →  SCOS-TODO
//
// Parity: mirrors PySpark recipe sparkcontext_getorcreate_init_session_rewrite.
// ─────────────────────────────────────────────────────────────────────────────
class ScosSparkContextGetOrCreateRewrite
    extends SyntacticRule("ScosSparkContextGetOrCreateRewrite") {

  private val RENAME_COMMENT =
    "// SCOS: [SPRKCNTSCL3500-Fixed] ScosSparkContextGetOrCreateRewrite: " +
    "SparkContext.getOrCreate() \u2192 SnowparkConnectSession.builder().getOrCreate()"

  private val TODO_COMMENT =
    "// SCOS: TODO - [SPRKCNTSCL3500] ScosSparkContextGetOrCreateRewrite: " +
    "SparkContext construction not supported; " +
    "replace with SnowparkConnectSession.builder().getOrCreate()"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(Term.Name("SparkContext"), Term.Name("getOrCreate")), Nil) =>
        Patch.replaceTree(t,
          RENAME_COMMENT + "\n" + "SnowparkConnectSession.builder().getOrCreate()")

      case t @ Term.Apply(
            Term.Select(Term.Name("SparkContext"), Term.Name("getOrCreate")), _ :: _) =>
        Patch.addLeft(t.tokens.head, TODO_COMMENT + "\n")

      case t @ Term.New(Init(Type.Name("SparkContext"), _, _)) =>
        Patch.addLeft(t.tokens.head, TODO_COMMENT + "\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 31: ScosSparkContextNoopCommentOut
//
// Comments out SparkContext lifecycle no-ops (stop/close/setLogLevel).
//
// Parity: mirrors PySpark recipe sparkcontext_noop_comment_out_rewrite.
// ─────────────────────────────────────────────────────────────────────────────
class ScosSparkContextNoopCommentOut extends SyntacticRule("ScosSparkContextNoopCommentOut") {

  private val NOOPS: Set[String] = Set("stop", "close", "setLogLevel")

  private def isSc(t: Term): Boolean = t match {
    case Term.Name("sc")                           => true
    case Term.Select(_, Term.Name("sparkContext")) => true
    case _                                         => false
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(recv, Term.Name(m)), _)
          if isSc(recv) && NOOPS(m) =>
        // Replace with a block comment + unit expression so the result is a valid
        // inline expression anywhere sc.<m>() was (a bare `//` line comment would
        // comment out a trailing `}` / same-line code and break compilation).
        Patch.replaceTree(t,
          s"/* SCOS: [SPRKCNTSCL1500] ScosSparkContextNoopCommentOut: " +
          s"sc.$m() is a no-op in Snowpark Connect (SparkContext not available) */ ()")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 32: ScosSparkConfigNoopAnnotate
//
// Annotates spark.conf.set("key", value) calls for executor/YARN/Kubernetes
// settings that are ignored by Snowflake (SCOS no-ops).
//
// Parity: mirrors PySpark recipe spark_config_noop_annotate.
// ─────────────────────────────────────────────────────────────────────────────
class ScosSparkConfigNoopAnnotate extends SyntacticRule("ScosSparkConfigNoopAnnotate") {

  private val NOOP_PREFIXES: Set[String] = Set(
    "spark.executor.", "spark.driver.", "spark.yarn.", "spark.kubernetes.",
    "spark.mesos.", "spark.submit.", "spark.deploy.", "spark.cores.",
    "spark.task.", "spark.scheduler.", "spark.worker.", "spark.network.",
    "spark.rpc.", "spark.locality.", "spark.dynamicAllocation.",
    "spark.speculation.", "spark.blacklist.", "spark.excludeOnFailure.",
    "spark.memory.", "spark.streaming.",
    "spark.databricks.delta.optimizeWrite", "spark.databricks.delta.autoCompact"
  )

  private val comment =
    "// SCOS: TODO - [SPRKCNTSCL1000] ScosSparkConfigNoopAnnotate: " +
    "this Spark config key has no effect in Snowpark Connect; " +
    "remove or convert to a Snowflake session parameter if applicable"

  private def isNoopKey(k: Term): Boolean = k match {
    case Lit.String(v) => NOOP_PREFIXES.exists(v.startsWith)
    case _ => false
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(
            Term.Select(Term.Select(_, Term.Name("conf")), Term.Name("set")),
            List(k, _)
          ) if isNoopKey(k) =>
        Patch.addLeft(t.tokens.head, comment + "\n")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rule 33: ScosUnpersistDropBlockingArg
//
// Drops the blocking argument from DataFrame.unpersist():
//
//   df.unpersist(blocking = true)  →  df.unpersist()
//   df.unpersist(true)             →  df.unpersist()
//
// Parity: mirrors PySpark recipe unpersist_drop_blocking_arg_rewrite.
// ─────────────────────────────────────────────────────────────────────────────
class ScosUnpersistDropBlockingArg extends SyntacticRule("ScosUnpersistDropBlockingArg") {

  private val comment =
    "// SCOS: [SPRKCNTSCL1000] ScosUnpersistDropBlockingArg: " +
    "unpersist() does not accept a blocking arg in Snowpark Connect \u2014 arg dropped"

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      case t @ Term.Apply(Term.Select(recv, Term.Name("unpersist")), _ :: _) =>
        Patch.replaceTree(t, s"$comment\n${recv.syntax}.unpersist()")
    }.asPatch
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// ScosSparkIoDetectAnnotate  (parity: PySpark spark_io_detect recipe, PR #3575)
//
// Annotates Spark I/O call chains for cases NOT already covered by the existing
// ScosExternalCloudReadAnnotate (cloud URIs) and ScosWildcardReadAnnotate
// (glob paths) rules:
//
//   • JDBC — chain contains .format("jdbc") or terminal .jdbc(url, ...) with
//     a read/write chain anchor → [SPRKCNTSCL6000-Error]
//     (no JVM driver in Spark Connect)
//
//   • Iceberg — chain contains .format("iceberg") with .load()/.save() terminal
//     → [SPRKCNTSCL3200-IO]  (verify the table is accessible in Snowflake)
//
//   • Table read — spark.read.table(name) terminal
//     → [SPRKCNTSCL3200-IO]  (table name/namespace must resolve in Snowflake)
//
//   • Table write insertInto — .insertInto(name) with a write chain
//     → [SPRKCNTSCL3200-IO]  (table name/namespace must resolve in Snowflake)
//
// Deliberately excluded (handled by dedicated rules / the analyzer):
//   • cloud-URI reads  → ScosExternalCloudReadAnnotate
//   • wildcard paths   → ScosWildcardReadAnnotate
//   • streaming APIs   → SPRKCNTSCL2000 in the Python analyzer
//   • .format("delta") / .format("snowflake") → ScosDeltaWriteToParquet /
//     ScosSnowflakeConnectorIO
//
// Note: .saveAsTable is intentionally omitted — ScosSaveAsTableDropStorageOpts
// already rewrites it and annotates the transformed call.
// ─────────────────────────────────────────────────────────────────────────────
class ScosSparkIoDetectAnnotate extends SyntacticRule("ScosSparkIoDetectAnnotate") {

  /** Walk up a method-application chain looking for a .read / .write /
   *  .readStream / .writeStream attribute access.  Returns the role name or
   *  empty string if none found.
   */
  @annotation.tailrec
  private def chainRole(t: Tree): String = t match {
    case Term.Select(_, Term.Name(r))
        if r == "read" || r == "write" || r == "readStream" || r == "writeStream" => r
    case Term.Apply(Term.Select(recv, _), _) => chainRole(recv)
    case Term.Select(recv, _)                => chainRole(recv)
    case _                                   => ""
  }

  /** Walk the chain and return the first .format("x") argument, lower-cased. */
  private def chainFormat(t: Tree): String = t match {
    case Term.Apply(Term.Select(_, Term.Name("format")), List(Lit.String(f))) => f.toLowerCase
    case Term.Apply(Term.Select(recv, _), _) => chainFormat(recv)
    case Term.Select(recv, _)                => chainFormat(recv)
    case _                                   => ""
  }

  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {

      // ── JDBC terminal: df.write.jdbc(url, table, props) ──────────────────
      case t @ Term.Apply(Term.Select(recv, Term.Name("jdbc")), _)
          if chainRole(recv).nonEmpty =>
        Patch.addLeft(t,
          "// SCOS: [SPRKCNTSCL6000-Error] spark_io_detect: JDBC source/sink requires " +
          "a JVM driver not available in Spark Connect \u2014 use the Snowflake connector, " +
          "an external table, or load the data to a Snowflake table.\n")

      // ── .load() / .save() with .format("jdbc") or .format("iceberg") ─────
      case t @ Term.Apply(Term.Select(recv, Term.Name(terminal)), _)
          if (terminal == "load" || terminal == "save") && chainRole(recv).nonEmpty =>
        val fmt = chainFormat(recv)
        if (fmt == "jdbc")
          Patch.addLeft(t,
            "// SCOS: [SPRKCNTSCL6000-Error] spark_io_detect: JDBC source/sink requires " +
            "a JVM driver not available in Spark Connect \u2014 use the Snowflake connector, " +
            "an external table, or load the data to a Snowflake table.\n")
        else if (fmt == "iceberg") {
          val role = chainRole(recv)
          val verb = if (role == "read" || role == "readStream") "reads from" else "writes to"
          Patch.addLeft(t,
            s"// SCOS: [SPRKCNTSCL3200-IO] spark_io_detect: Iceberg catalog table I/O \u2014 " +
            s"$verb an Iceberg-managed table; verify the table is accessible in Snowflake " +
            "(Iceberg Tables, external catalog integration, or migrate to a native Snowflake table).\n")
        } else
          Patch.empty

      // ── spark.read.table(name) ────────────────────────────────────────────
      case t @ Term.Apply(
            Term.Select(Term.Select(_, Term.Name("read")), Term.Name("table")),
            _
          ) =>
        Patch.addLeft(t,
          "// SCOS: [SPRKCNTSCL3200-IO] spark_io_detect: table I/O \u2014 reads from a " +
          "Snowflake table; verify the table name/namespace (database.schema.table) " +
          "resolves to the intended Snowflake table (catalog/schema mapping may differ).\n")

      // ── .insertInto(name) on a write chain ───────────────────────────────
      case t @ Term.Apply(Term.Select(recv, Term.Name("insertInto")), _)
          if chainRole(recv).nonEmpty =>
        Patch.addLeft(t,
          "// SCOS: [SPRKCNTSCL3200-IO] spark_io_detect: table I/O \u2014 writes to a " +
          "Snowflake table; verify the table name/namespace (database.schema.table) " +
          "resolves to the intended Snowflake table (catalog/schema mapping may differ).\n")

    }.asPatch
  }
}

/** ScosSqlContextImplicitsRewrite
 *
 *  `spark.sqlContext.implicits` is the pre-Spark-2.0 path to encoder implicits.
 *  The modern form (Spark 2+) is `spark.implicits` — identical result, shorter.
 *  In SCOS, sqlContext is not exposed, so the old form would cause a compile
 *  error.  This rule rewrites it at the AST level:
 *
 *    import spark.sqlContext.implicits._   →  import spark.implicits._
 *    spark.sqlContext.implicits            →  spark.implicits
 *
 *  The rule handles both import statements and general selector expressions so
 *  that in-method `import spark.sqlContext.implicits._` forms are caught too.
 */
class ScosSqlContextImplicitsRewrite extends SyntacticRule("ScosSqlContextImplicitsRewrite") {
  override def fix(implicit doc: SyntacticDocument): Patch = {
    doc.tree.collect {
      // import spark.sqlContext.implicits._
      case i @ Import(Seq(importer)) if importer.syntax.contains("sqlContext.implicits") =>
        val fixed = importer.syntax.replace("sqlContext.implicits", "implicits")
        Patch.replaceTree(i, s"// SCOS: [SPRKCNTSCL3500] spark.sqlContext.implicits deprecated - replaced with spark.implicits._\nimport $fixed")

      // spark.sqlContext.implicits (non-import usage)
      // Guard: skip when parent is an Importer — the Import case above already handles that
      case t @ Term.Select(
            Term.Select(spark: Term.Name, Term.Name("sqlContext")),
            Term.Name("implicits")
          ) if spark.value == "spark" && !t.parent.exists(_.is[Importer]) =>
        Patch.replaceTree(t, "spark.implicits")
    }.asPatch
  }
}

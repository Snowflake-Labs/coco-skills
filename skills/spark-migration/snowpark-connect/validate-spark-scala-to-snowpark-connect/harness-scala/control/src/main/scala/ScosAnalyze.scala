// com.snowflake.scos.validate.ScosAnalyze
//
// Deterministic Scala source analysis for the validator's data-synthesizer agent.
//
// The data-synthesizer agent reasons about *semantics* (which sources matter, what the
// schemas are). This command gives it deterministic *facts* extracted from the
// AST via Scalameta, so the agent does not have to act as a Scala parser:
//   - entrypoints   (objects/classes that declare a `main`/`run` method)
//   - imports
//   - reads         (spark.read....{parquet,csv,json,orc,text,load,table,jdbc};
//                    SparkContext RDD reads sc.{textFile,wholeTextFiles,binaryFiles,
//                    binaryRecords,sequenceFile,objectFile};
//                    DeltaTable.forPath/forName; spark.catalog reads)
//   - writes        (....write....{parquet,csv,...,save}, saveAsTable, insertInto;
//                    spark.catalog.createTable/createExternalTable)
//   - table_refs    (spark.table / saveAsTable / insertInto targets)
//   - column_refs   (col("x") / column("x") / $"x", plus string args of
//                    select / groupBy / orderBy / sort / sortBy / drop /
//                    dropDuplicates)
//   - struct_schemas (named StructType(Seq(StructField(...))) bindings) and
//                    schema_fields on reads that use .schema(...)
//   - filters       (best-effort literal predicates: col/$"x" === Lit,
//                    .isin(literals|Seq(...)), .filter/.where("col = '…'");
//                    complex predicates are omitted — never invented)
//   - joins         (.join(..., Seq("k1", …)) → join_keys[]; equality-only)
//   - unresolved_reads / unresolved_writes — call sites whose path/table arg
//                    could not be statically resolved (dynamic paths are
//                    recorded rather than dropped, so the data-synthesizer can
//                    still declare a source with an llm_todo).
//
// Argument resolution (parity with PySpark assessment data_edge_ast.py):
//   B1  Lit.String literal             → verbatim
//   B2  s"..." interpolation           → resolve each ${} arg; join
//   B3  .format(arg)                   → substitute %s / {} in receiver
//   B4  .replace(old,new)              → perform replacement
//   B5  Seq(...).mkString(sep)         → join resolved parts
//   B6  "a" + "b" binary concat        → concatenate resolved sides
//   B7  Map("k"->"v")("k")             → resolve value for literal key
//   B8  Map(...)( varKey )             → trace varKey → literal then B7
//   B9  Term.Name → val/var binding    → recurse on RHS
//   B10 for-loop target enumeration    → first element of literal iterable
//   B11 if (c) a else b ternary        → enumerate BOTH branches
//   B12 .trim/.toLowerCase/etc.        → recurse receiver (trivial pass)
//   B13 sys.env.getOrElse("V","d")     → use default arg
//   B14 Paths.get("a"[,"b"])           → join args
//   B16 f() single-return def inline   → recurse body
//
// Usage:
//   java -jar scos-analyze.jar analyze --source <file-or-dir> [--output <path>]
//                                       [--config-pool-file <flat-json-map>]
//
// --config-pool-file  Optional flat JSON ``{"VAR_NAME": "value", …}`` produced
//                     by the Python-side ``_load_config_pool`` in
//                     scan_codebase.py. Variable names unresolvable via Scala
//                     val/def bindings are looked up here as a final fallback
//                     (PR #3548 parity with PySpark config-pool threading).
//
// Output: JSON to stdout (or --output file). Exit 0 always; per-file `parse_ok`
// flags surface parse failures without aborting the whole run.

package com.snowflake.scos.validate

import io.circe.Json            // type alias (term `Json` is the local helper object)
import io.circe.{Json => CJson} // circe builder object, used for CJson.obj(...)
import io.circe.parser.{parse => circeParseJson}
import io.circe.syntax._

import java.nio.file.{Files, Path, Paths}
import scala.meta._
import scala.meta.parsers.Parsed

object ScosAnalyze {

  // ── read/write terminal method-name sets ────────────────────────────────────

  private val readTerminals  = Set("parquet", "csv", "json", "orc", "text", "textFile", "load", "table", "jdbc")
  private val fmtTerminals   = Set("parquet", "csv", "json", "orc", "text")
  // SparkContext RDD read methods. Parity with PySpark data_edge_ast._SC_READ_METHODS.
  private val scReadMethods  = Set(
    "textFile", "wholeTextFiles", "binaryFiles", "binaryRecords", "sequenceFile", "objectFile")

  // External I/O import prefixes → (prefix, kind) for risk detection
  private val externalIoPrefixes: Seq[(String, String)] = Seq(
    "java.sql.DriverManager" -> "jdbc_driver_manager",
    "software.amazon"        -> "aws_sdk",
    "com.amazonaws"          -> "aws_sdk",
    "com.azure.storage"      -> "azure_sdk",
    "com.google.cloud"       -> "gcs_sdk",
    "org.apache.kafka"       -> "kafka",
    "org.mongodb"            -> "mongo",
  )

  // Additional RDD operations beyond the existing sc.*Read methods
  private val rddOpMethods = Set("parallelize", "broadcast", "accumulator", "makeRDD")

  // Streaming signal method names
  private val streamingSignalMethods = Set("readStream", "writeStream", "trigger")

  // ── resolver constants ───────────────────────────────────────────────────────

  private val DEPTH_CAP = 6   // max recursion depth in resolveSignatures

  // B12: trivial string-method passthrough (resolver recurses the receiver)
  private val TRIVIAL_PASS = Set(
    "trim", "strip", "stripLeading", "stripTrailing",
    "toLowerCase", "toUpperCase",
    "stripPrefix", "stripSuffix", "stripMargin",
    "intern"
  )

  // ── per-file resolver context builders ──────────────────────────────────────

  /** B9: val/var name → RHS term (first binding wins). */
  private def buildValBindings(tree: Tree): Map[String, Term] = {
    val m = scala.collection.mutable.Map[String, Term]()
    tree.traverse {
      case d: Defn.Val if d.pats.size == 1 =>
        d.pats.head match {
          case Pat.Var(n) => m.getOrElseUpdate(n.value, d.rhs)
          case _          => ()
        }
      case d: Defn.Var if d.pats.size == 1 =>
        d.pats.head match {
          case Pat.Var(n) => d.rhs.foreach(rhs => m.getOrElseUpdate(n.value, rhs))
          case _          => ()
        }
    }
    m.toMap
  }

  /** B16: function name → single-expression return body. */
  private def buildDefReturns(tree: Tree): Map[String, Term] = {
    val m = scala.collection.mutable.Map[String, Term]()
    tree.traverse {
      case d: Defn.Def =>
        val bodyOpt: Option[Term] = d.body match {
          case Term.Block(List(s: Term)) => Some(s)  // { expr }
          case _: Term.Block             => None      // multi-statement block
          case t                         => Some(t)   // direct expression body
        }
        bodyOpt.foreach(t => m.getOrElseUpdate(d.name.value, t))
    }
    m.toMap
  }

  /** B10: for-loop enumerator → list of iterable elements. */
  private def buildForTargets(tree: Tree): Map[String, List[Term]] = {
    val m = scala.collection.mutable.Map[String, List[Term]]()
    def registerEnum(enums: List[Enumerator]): Unit =
      enums.foreach {
        case Enumerator.Generator(Pat.Var(n), rhs) =>
          val elems = rhs match {
            case Term.Apply(Term.Name("List" | "Seq" | "Vector" | "Set"), args) => args
            case _ => Nil
          }
          if (elems.nonEmpty) m.getOrElseUpdate(n.value, elems)
        case _ => ()
      }
    tree.traverse {
      case Term.For(enums, _)      => registerEnum(enums)
      case Term.ForYield(enums, _) => registerEnum(enums)
    }
    m.toMap
  }

  /**
   * PR #3548 parity — call-site argument expansion.
   *
   * Maps each def's parameter names to the literal (Lit.String) argument
   * values seen at call sites in the same file.  Merged into ``vals`` so
   * that a parameter like ``tableName`` resolves when the def body uses it
   * as a data-edge path argument.
   *
   * Covers the most common 1-hop pattern:
   *   {{{
   *     def load(tableName: String) = spark.read.table(tableName)
   *     load("DB.SCH.ORDERS")   // tableName → "DB.SCH.ORDERS"
   *   }}}
   */
  private def buildParamBindings(tree: Tree): Map[String, Term] = {
    // Step 1: map funcName → ordered list of param names
    val funcParams = scala.collection.mutable.Map[String, List[String]]()
    tree.traverse {
      case d: Defn.Def if d.paramss.nonEmpty =>
        val params = d.paramss.flatten.map(_.name.value)
        if (params.nonEmpty) funcParams(d.name.value) = params
    }
    if (funcParams.isEmpty) return Map.empty

    // Step 2: at every call site Term.Apply(Term.Name(fn), args), bind
    // params to call-site literal args (first literal seen wins).
    val bindings = scala.collection.mutable.Map[String, Term]()
    tree.traverse {
      case ta: Term.Apply =>
        val (fn, callArgs) = ta.fun match {
          case Term.ApplyType(Term.Name(n), _) => (Some(n), ta.argClause.values)
          case Term.Name(n)                    => (Some(n), ta.argClause.values)
          case _                               => (None, Nil)
        }
        fn.foreach { name =>
          funcParams.get(name).foreach { params =>
            params.zip(callArgs).foreach { case (paramName, argNode) =>
              // Only bind if we don't already have a binding for this param
              if (!bindings.contains(paramName)) {
                argNode match {
                  case _: Lit.String => bindings(paramName) = argNode
                  case _             => () // non-literal; leave for resolver
                }
              }
            }
          }
        }
    }
    bindings.toMap
  }

  // ── argument resolver (B1–B16 + config pool) ─────────────────────────────

  /**
   * Recursively resolve a Term to a list of concrete string signatures.
   * Empty list → could not resolve (caller emits an UnresolvedEdge).
   * Multiple elements → enumerated branches (ternary / for-loop).
   */
  private def resolveSignatures(
    node: Term,
    depth: Int,
    vals: Map[String, Term],
    defs: Map[String, Term],
    fors: Map[String, List[Term]],
    configPool: Map[String, String] = Map.empty
  ): List[String] = {
    if (depth >= DEPTH_CAP) return Nil
    val d1 = depth + 1

    node match {

      // B1: string literal
      case Lit.String(s) => List(s)

      // B6: binary + concatenation
      case Term.ApplyInfix(l, Term.Name("+"), _, List(r)) =>
        val ls = resolveSignatures(l, d1, vals, defs, fors)
        val rs = resolveSignatures(r, d1, vals, defs, fors)
        if (ls.isEmpty || rs.isEmpty) Nil
        else for (lv <- ls; rv <- rs) yield lv + rv

      // B11: ternary if/else — enumerate BOTH branches
      case ti: Term.If =>
        resolveSignatures(ti.thenp, d1, vals, defs, fors) ++
        resolveSignatures(ti.elsep, d1, vals, defs, fors)

      // B2: string interpolation s"..." — use instance guard to match regardless
      // of whether prefix is Term.Name or Name.Indeterminate in this Scalameta build.
      case ti: Term.Interpolate if ti.prefix.value == "s" =>
        val iparts = ti.parts
        val iargs  = ti.args
        // parts.length == iargs.length + 1; interleave
        val resolvedArgs: List[Option[String]] = iargs.map { a =>
          resolveSignatures(a, d1, vals, defs, fors) match {
            case h :: _ => Some(h) // take first if multiple branches
            case Nil    => None
          }
        }
        if (resolvedArgs.exists(_.isEmpty)) Nil
        else {
          val sb = new StringBuilder
          iparts.zipWithIndex.foreach { case (Lit.String(p), i) =>
            sb.append(p)
            if (i < iargs.size) sb.append(resolvedArgs(i).getOrElse(""))
            case _ => ()
          }
          List(sb.toString)
        }

      // B12: trivial string-method passthrough — bare accessor form (no parens)
      // e.g. "str".trim, myStr.toLowerCase  → recurse receiver
      case Term.Select(recv, Term.Name(m)) if TRIVIAL_PASS(m) =>
        resolveSignatures(recv, d1, vals, defs, fors)

      // B9/B16: name → val binding OR single-return def inlining
      // Config-pool fallback (PR #3548 parity): if the name is unresolvable
      // via val/def/for bindings, check the config pool — useful for env-style
      // variables like DATABASE_NAME or TABLE_PREFIX that are set in
      // application.json/yaml rather than as Scala literals.
      case Term.Name(x) =>
        fors.get(x) match {
          case Some(elems) =>
            // B10: loop target → first element
            elems.headOption.fold(Nil: List[String])(
              e => resolveSignatures(e, d1, vals, defs, fors, configPool))
          case None =>
            val fromBindings: List[String] =
              vals.get(x).fold(
                // B16: no-paren def reference (def f = expr, called as f not f())
                defs.get(x).fold(Nil: List[String])(
                  body => resolveSignatures(body, d1, vals, defs, fors, configPool))
              )(rhs => resolveSignatures(rhs, d1, vals, defs, fors, configPool))
            if (fromBindings.nonEmpty) fromBindings
            else {
              // Config-pool fallback: resolve bare variable names whose values
              // come from JSON/YAML config files in the workload directory.
              configPool.get(x).fold(Nil: List[String])(v => List(v))
            }
        }

      case ta: Term.Apply =>
        // Unwrap type application (e.g. f[T](arg))
        val (func, applyArgs) = ta.fun match {
          case Term.ApplyType(inner, _) => (inner, ta.argClause.values)
          case other                    => (other, ta.argClause.values)
        }
        func match {

          // B12: trivial passthrough methods (.trim, .toLowerCase, etc.)
          case Term.Select(recv, Term.Name(m)) if TRIVIAL_PASS(m) && applyArgs.isEmpty =>
            resolveSignatures(recv, d1, vals, defs, fors)

          // B4: .replace(old, new)
          case Term.Select(recv, Term.Name("replace")) if applyArgs.size == 2 =>
            val rs = resolveSignatures(recv, d1, vals, defs, fors)
            val os = resolveSignatures(applyArgs(0), d1, vals, defs, fors)
            val ns = resolveSignatures(applyArgs(1), d1, vals, defs, fors)
            if (rs.isEmpty || os.isEmpty || ns.isEmpty) Nil
            else rs.flatMap(r => os.flatMap(o => ns.map(n => r.replace(o, n))))

          // B3: .format(arg) — substitute first %s/{} in receiver
          case Term.Select(recv, Term.Name("format")) if applyArgs.nonEmpty =>
            val rs = resolveSignatures(recv, d1, vals, defs, fors)
            val as = resolveSignatures(applyArgs.head, d1, vals, defs, fors)
            if (rs.isEmpty || as.isEmpty) Nil
            else rs.flatMap(r => as.map(a => r.replace("%s", a).replace("{}", a)))

          // B5: Seq/List(...).mkString(sep)
          case Term.Select(
                Term.Apply(Term.Name("List" | "Seq" | "Vector"), elems),
                Term.Name("mkString")
              ) if applyArgs.size == 1 =>
            val seps = resolveSignatures(applyArgs.head, d1, vals, defs, fors)
            if (seps.isEmpty) Nil
            else {
              val elemStrs = elems.map(e => resolveSignatures(e, d1, vals, defs, fors))
              if (elemStrs.exists(_.isEmpty)) Nil
              else List(elemStrs.map(_.head).mkString(seps.head))
            }

          // B13: sys.env.getOrElse("V", default) — use default
          case Term.Select(
                Term.Select(Term.Name("sys"), Term.Name("env")),
                Term.Name("getOrElse")
              ) if applyArgs.size >= 2 =>
            resolveSignatures(applyArgs(1), d1, vals, defs, fors)

          // B13: sys.env.get("V").getOrElse(default)
          case Term.Select(_, Term.Name("getOrElse")) if applyArgs.size >= 2 =>
            resolveSignatures(applyArgs(1), d1, vals, defs, fors)

          // B13: System.getProperty("k", "default") — use default (2nd arg)
          case Term.Select(Term.Name("System"), Term.Name("getProperty"))
              if applyArgs.size >= 2 =>
            resolveSignatures(applyArgs(1), d1, vals, defs, fors)

          // B14: Paths.get("a"[,"b",...]) — join all args
          case Term.Select(Term.Name("Paths"), Term.Name("get")) if applyArgs.nonEmpty =>
            val parts = applyArgs.map(a => resolveSignatures(a, d1, vals, defs, fors))
            if (parts.exists(_.isEmpty)) Nil
            else List(parts.map(_.head).mkString("/"))

          // B14: new File("a") or new File(parent, child)
          case Term.Name("File") | Term.Select(_, Term.Name("File"))
              if applyArgs.nonEmpty =>
            val parts = applyArgs.map(a => resolveSignatures(a, d1, vals, defs, fors))
            if (parts.exists(_.isEmpty)) Nil
            else List(parts.map(_.head).mkString("/"))

          // B7/B8 Map lookup + B16 function inlining — function-call forms
          case Term.Name(fn) if applyArgs.size == 1 =>
            // B7/B8: check if fn binds to a Map literal
            val keyRes = resolveSignatures(applyArgs.head, d1, vals, defs, fors)
            val mapResult: List[String] = if (keyRes.nonEmpty) {
              vals.get(fn).toList.flatMap {
                case Term.Apply(Term.Name("Map"), entries) =>
                  val mapM: Map[String, Term] = entries.collect {
                    case Term.ApplyInfix(Lit.String(k), Term.Name("->"), _, List(v)) => k -> v
                  }.toMap
                  keyRes.flatMap(k => mapM.get(k).toList.flatMap(v =>
                    resolveSignatures(v, d1, vals, defs, fors)))
                case _ => Nil
              }
            } else Nil
            if (mapResult.nonEmpty) mapResult
            else {
              // B16: single-return function inlining
              defs.get(fn).fold(Nil: List[String])(body =>
                resolveSignatures(body, d1, vals, defs, fors))
            }

          // B16: zero-arg function inlining  f()
          case Term.Name(fn) if applyArgs.isEmpty =>
            defs.get(fn).fold(Nil: List[String])(body =>
              resolveSignatures(body, d1, vals, defs, fors))

          case _ => Nil
        }

      case _ => Nil
    }
  }

  /** Short description of a Term node for UnresolvedEdge diagnostics. */
  private def describeNode(t: Term): String = t match {
    case Term.Name(n)                                => s"variable '${n}'"
    case Term.Select(_, Term.Name(n))                => s"attribute '.${n}'"
    case Term.Apply(Term.Name(fn), _)                => s"call '${fn}(...)'"
    case Term.Apply(Term.Select(_, Term.Name(m)), _) => s"method '.${m}(...)'"
    case _: Term.If                                  => "conditional (if/else)"
    case _: Term.Interpolate                         => "string interpolation"
    case Term.ApplyInfix(_, Term.Name(op), _, _)     => s"operator '${op}'"
    case _                                           => t.productPrefix
  }

  // ── JSON helpers ─────────────────────────────────────────────────────────────

  // callJson with optional schema_fields is defined below (StructType section).

  private def unresolvedJson(kind: String, call: String, argExpr: String, line: Int): Json =
    CJson.obj(
      "kind"     -> kind.asJson,
      "call"     -> call.asJson,
      "arg_expr" -> argExpr.asJson,
      "line"     -> line.asJson,
    )

  private def filterJson(col: String, op: String, values: List[String], line: Int): Json =
    CJson.obj(
      "col"    -> col.asJson,
      "op"     -> op.asJson,
      "values" -> values.asJson,
      "line"   -> line.asJson,
    )

  private def joinJson(keys: List[String], line: Int): Json =
    CJson.obj(
      "join_keys" -> keys.asJson,
      "line"      -> line.asJson,
    )

  // ── StructType / StructField extraction (PySpark Layer A parity) ───────────

  private val SPARK_TYPE_CTORS: Map[String, String] = Map(
    "StringType"    -> "string",
    "IntegerType"   -> "int",
    "IntType"       -> "int",
    "LongType"      -> "long",
    "DoubleType"    -> "double",
    "FloatType"     -> "float",
    "BooleanType"   -> "boolean",
    "ByteType"      -> "byte",
    "ShortType"     -> "short",
    "BinaryType"    -> "binary",
    "DateType"      -> "date",
    "TimestampType" -> "timestamp",
    "CalendarIntervalType" -> "string",
    "NullType"      -> "string",
  )

  /** Map a Spark type AST node to a lean type string (decimal(p,s), array<…>, …). */
  private def sparkTypeName(t: Term): String = t match {
    case Term.Name(n) if SPARK_TYPE_CTORS.contains(n) => SPARK_TYPE_CTORS(n)
    case Term.Select(_, Term.Name(n)) if SPARK_TYPE_CTORS.contains(n) => SPARK_TYPE_CTORS(n)
    // DecimalType(10, 2) / DecimalType.apply(10, 2)
    case Term.Apply(fun, args) =>
      val ctor = fun match {
        case Term.Name(n)              => n
        case Term.Select(_, Term.Name(n)) => n
        case _                         => ""
      }
      if (ctor == "DecimalType") {
        val ints = args.collect {
          case Lit.Int(i)  => i.toString
          case Lit.Long(l) => l.toString
        }
        if (ints.nonEmpty) s"decimal(${ints.mkString(",")})" else "decimal"
      } else if (ctor == "ArrayType") {
        val inner = args.headOption.map(sparkTypeName).getOrElse("string")
        s"array<$inner>"
      } else if (ctor == "MapType") {
        val k = args.headOption.map(sparkTypeName).getOrElse("string")
        val v = if (args.size > 1) sparkTypeName(args(1)) else "string"
        s"map<$k,$v>"
      } else if (ctor == "StructType") {
        val fields = parseStructFields(t)
        val inner = fields.map { f =>
          val n = f.hcursor.get[String]("name").getOrElse("?")
          val ty = f.hcursor.get[String]("type").getOrElse("string")
          s"$n:$ty"
        }.mkString(",")
        s"struct<$inner>"
      } else {
        SPARK_TYPE_CTORS.getOrElse(ctor, "string")
      }
    // DataTypes.StringType etc.
    case Term.Select(Term.Name("DataTypes"), Term.Name(n)) =>
      SPARK_TYPE_CTORS.getOrElse(n.stripSuffix("Type") + "Type",
        SPARK_TYPE_CTORS.getOrElse(n, "string"))
    case _ => "string"
  }

  /** Parse StructField("name", Type[, nullable]) into a JSON field object. */
  private def parseStructField(t: Term): Option[Json] = t match {
    case Term.Apply(fun, args) =>
      val ctor = fun match {
        case Term.Name(n)                 => n
        case Term.Select(_, Term.Name(n)) => n
        case _                            => ""
      }
      if (ctor != "StructField" || args.isEmpty) None
      else {
        val nameOpt = args.head match {
          case Lit.String(s) => Some(s)
          case _             => None
        }
        nameOpt.map { fname =>
          val ftype = if (args.size > 1) sparkTypeName(args(1)) else "string"
          val nullable = args.lift(2) match {
            case Some(Lit.Boolean(b)) => b
            case _ =>
              // named arg: nullable = true/false
              t match {
                case Term.Apply(_, _) =>
                  // Scalameta may put named args in argClause; best-effort scan
                  true
                case _ => true
              }
          }
          // Also check for Term.Assign / named args in newer scalameta
          val nullableFinal = t.collect {
            case Term.Assign(Term.Name("nullable"), Lit.Boolean(b)) => b
          }.headOption.getOrElse(nullable)
          CJson.obj(
            "name"     -> fname.asJson,
            "type"     -> ftype.asJson,
            "nullable" -> nullableFinal.asJson,
          )
        }
      }
    case _ => None
  }

  /** Parse StructType(Seq/List/Array(...StructField...)) → field JSON list. */
  private def parseStructFields(t: Term): List[Json] = t match {
    case Term.Apply(fun, args) =>
      val ctor = fun match {
        case Term.Name(n)                 => n
        case Term.Select(_, Term.Name(n)) => n
        case _                            => ""
      }
      if (ctor != "StructType") Nil
      else {
        val elems: List[Term] = args.headOption match {
          case Some(Term.Apply(Term.Name("Seq" | "List" | "Array" | "Vector"), es)) => es
          case Some(Term.Apply(Term.Select(_, Term.Name("Seq" | "List" | "Array" | "Vector")), es)) => es
          case Some(other) =>
            // StructType(StructField(...), StructField(...)) rare form
            args
          case None => Nil
        }
        elems.flatMap(e => parseStructField(e).toList)
      }
    case _ => Nil
  }

  /** Resolve .schema(arg) — named var or inline StructType. */
  private def resolveSchemaArg(
      arg: Term,
      named: Map[String, List[Json]],
  ): Option[List[Json]] = arg match {
    case Term.Name(n) if named.contains(n) => Some(named(n))
    case Term.Apply(fun, _) =>
      val ctor = fun match {
        case Term.Name(n)                 => n
        case Term.Select(_, Term.Name(n)) => n
        case _                            => ""
      }
      if (ctor == "StructType") {
        val fields = parseStructFields(arg)
        if (fields.nonEmpty) Some(fields) else None
      } else None
    case _ => None
  }

  /**
   * Walk a read receiver chain (``.option().schema(s).parquet``) looking for
   * ``.schema(...)`` and resolve it against named StructType bindings.
   */
  private def findSchemaOnChain(
      t: Term,
      named: Map[String, List[Json]],
  ): Option[List[Json]] = t match {
    case Term.Apply(Term.Select(inner, Term.Name("schema")), args) =>
      args.headOption.flatMap(a => resolveSchemaArg(a, named))
        .orElse(findSchemaOnChain(inner, named))
    case Term.Apply(Term.Select(inner, _), _) => findSchemaOnChain(inner, named)
    case Term.Select(inner, _)                => findSchemaOnChain(inner, named)
    case Term.Apply(inner, _)                 => findSchemaOnChain(inner, named)
    case Term.ApplyType(inner, _)             => findSchemaOnChain(inner, named)
    case _                                    => None
  }

  /** True if method name is a Spark read terminal (parquet/table/jdbc/…). */
  private def isReadTerminal(m: String): Boolean =
    readTerminals.contains(m) || scReadMethods.contains(m) ||
      m == "forPath" || m == "forName"

  /** Base DF variable name of a receiver chain (``ordersDf.select(...)`` → ``ordersDf``). */
  private def baseVarName(t: Term): Option[String] = t match {
    case Term.Name(n)                         => Some(n)
    case Term.Select(qual, _)                 => baseVarName(qual)
    case Term.Apply(Term.Select(qual, _), _)  => baseVarName(qual)
    case Term.Apply(inner, _)                 => baseVarName(inner)
    case Term.ApplyType(inner, _)             => baseVarName(inner)
    case _                                    => None
  }

  /**
   * Layer C: find the static read path/table arg that produces this Term
   * (``spark.read.parquet("x")`` → ``x``; ``ordersDf`` → bound read arg).
   */
  private def findReadArgOnTerm(
      t: Term,
      vals: Map[String, Term],
      defs: Map[String, Term],
      fors: Map[String, List[Term]],
      configPool: Map[String, String],
      dfToSource: scala.collection.Map[String, String],
      depth: Int = 0,
  ): Option[String] = {
    if (depth > DEPTH_CAP) return None
    t match {
      case Term.Apply(Term.Select(qual, Term.Name(m)), args) if isReadTerminal(m) && args.nonEmpty =>
        val argIdx =
          if ((m == "jdbc" || m == "forPath" || m == "forName") && args.size >= 2) 1 else 0
        val resolved = resolveSignatures(args(argIdx), 0, vals, defs, fors, configPool)
        resolved.headOption.orElse(findReadArgOnTerm(qual, vals, defs, fors, configPool, dfToSource, depth + 1))
      case Term.Apply(Term.Select(qual, Term.Name("table")), args) if args.nonEmpty =>
        val resolved = resolveSignatures(args.head, 0, vals, defs, fors, configPool)
        resolved.headOption
      case Term.Apply(Term.Select(qual, _), _) =>
        findReadArgOnTerm(qual, vals, defs, fors, configPool, dfToSource, depth + 1)
      case Term.Select(qual, _) =>
        findReadArgOnTerm(qual, vals, defs, fors, configPool, dfToSource, depth + 1)
      case Term.Apply(inner, _) =>
        findReadArgOnTerm(inner, vals, defs, fors, configPool, dfToSource, depth + 1)
      case Term.Name(n) =>
        dfToSource.get(n).orElse(
          vals.get(n).flatMap(rhs =>
            findReadArgOnTerm(rhs, vals, defs, fors, configPool, dfToSource, depth + 1))
        ).orElse(
          defs.get(n).flatMap(body =>
            findReadArgOnTerm(body, vals, defs, fors, configPool, dfToSource, depth + 1))
        )
      case Term.Block(stats) =>
        stats.reverse.collectFirst { case term: Term => term }
          .flatMap(findReadArgOnTerm(_, vals, defs, fors, configPool, dfToSource, depth + 1))
      case _ => None
    }
  }

  private def callJson(
      call: String,
      args: List[String],
      line: Int,
      schemaFields: Option[List[Json]] = None,
  ): Json = {
    val base = List(
      "call" -> call.asJson,
      "args" -> args.asJson,
      "line" -> line.asJson,
    )
    schemaFields match {
      case Some(fields) if fields.nonEmpty =>
        CJson.obj(base ++ List("schema_fields" -> fields.asJson): _*)
      case _ =>
        CJson.obj(base: _*)
    }
  }

  /** Extract a column name from ``col("x")`` / ``column("x")`` / ``$"x"``. */
  private def colNameFrom(t: Term): Option[String] = t match {
    case Term.Apply(Term.Name("col" | "column"), List(Lit.String(s))) => Some(s)
    case Term.Apply(Term.Select(_, Term.Name("col" | "column")), List(Lit.String(s))) => Some(s)
    case ti: Term.Interpolate if ti.prefix.value == "$" =>
      ti.parts.collect { case Lit.String(s) if s.nonEmpty => s }.headOption
    case _ => None
  }

  /** Collect column names from a join predicate of col===col (and ANDs thereof). */
  private def collectJoinKeysFromPred(t: Term): List[String] = t match {
    case Term.ApplyInfix(lhs, Term.Name("&&" | "and"), _, args) =>
      (collectJoinKeysFromPred(lhs) ++ args.flatMap(collectJoinKeysFromPred)).distinct
    case Term.ApplyInfix(lhs, Term.Name("==="), _, args) =>
      (colNameFrom(lhs).toList ++ args.headOption.flatMap(colNameFrom).toList).distinct
    case _ => Nil
  }

  /**
   * Collect literal scalar / Seq-of-literal args only. Non-literal args yield
   * an empty list so the caller skips the fact (best-effort; never invent).
   */
  private def literalValues(args: List[Term]): List[String] = {
    def one(t: Term): Option[String] = t match {
      case Lit.String(s)   => Some(s)
      case Lit.Int(i)      => Some(i.toString)
      case Lit.Long(l)     => Some(l.toString)
      case Lit.Double(d)   => Some(d.toString)
      case Lit.Float(f)    => Some(f.toString)
      case Lit.Boolean(b)  => Some(b.toString)
      case _               => None
    }
    args match {
      case List(Term.Apply(Term.Name("Seq" | "List" | "Array" | "Vector"), elems)) =>
        val vs = elems.flatMap(e => one(e).toList)
        if (vs.size == elems.size) vs else Nil
      case _ =>
        val vs = args.flatMap(a => one(a).toList)
        if (vs.size == args.size && vs.nonEmpty) vs else Nil
    }
  }

  /** Simple SQL equality: ``col = 'val'`` / ``col = "val"`` / ``col = 123``. */
  private val SimpleSqlEq =
    raw"""(?i)^\s*([A-Za-z_][\w]*)\s*=\s*(?:'([^']*)'|"([^"]*)"|(-?\d+(?:\.\d+)?|true|false))\s*$$""".r

  // ── config pool loader (PR #3548 parity) ─────────────────────────────────

  /**
   * Load a flat JSON map from a file for config-pool resolution.
   *
   * The file must be a JSON object ``{"VAR_NAME": "value", …}`` produced by
   * the Python-side ``_load_config_pool`` in scan_codebase.py. Pass the path
   * via the ``--config-pool-file`` CLI flag; if the flag is absent or the
   * file is unreadable/unparseable, resolution degrades gracefully to the
   * existing val/def/for bindings.
   */
  private def loadConfigPool(path: String): Map[String, String] = {
    val text = try {
      new String(Files.readAllBytes(Paths.get(path)), "UTF-8")
    } catch {
      case _: Exception =>
        System.err.println(s"[scos-analyze] WARNING: could not read config pool file: $path")
        return Map.empty
    }
    circeParseJson(text) match {
      case Right(json) =>
        json.asObject.fold(Map.empty[String, String]) { obj =>
          obj.toMap.collect {
            case (k, v) if v.isString => k -> v.asString.getOrElse("")
          }
        }
      case Left(err) =>
        System.err.println(s"[scos-analyze] WARNING: could not parse config pool JSON: $err")
        Map.empty
    }
  }

  // ── entry point ──────────────────────────────────────────────────────────────

  def main(args: Array[String]): Unit = {
    var source         = ""
    var output         = ""
    var configPoolFile = ""
    args.sliding(2, 2).foreach {
      case Array("--source",          v) => source         = v
      case Array("--output",          v) => output         = v
      case Array("--config-pool-file",v) => configPoolFile = v
      case _                             => ()
    }
    if (source.isEmpty) Json.die(2, "analyze: --source <file-or-dir> is required")

    val configPool = if (configPoolFile.nonEmpty) loadConfigPool(configPoolFile)
                     else Map.empty[String, String]

    val root  = Paths.get(source).toAbsolutePath.normalize()
    val files = collectScalaFiles(root)
    val fileResults = files.map(analyzeFile(_, configPool))

    val out = CJson.obj(
      "source"     -> source.asJson,
      "file_count" -> files.size.asJson,
      "parse_errors" -> fileResults.count(j => !j.hcursor.get[Boolean]("parse_ok").getOrElse(true)).asJson,
      "files"      -> fileResults.asJson,
    )

    val rendered = out.spaces2
    if (output.nonEmpty) {
      val outPath = Paths.get(output).toAbsolutePath
      Option(outPath.getParent).foreach(Files.createDirectories(_))
      Files.write(outPath, (rendered + "\n").getBytes("UTF-8"))
      System.err.println(s"[scos-analyze] wrote $outPath (${files.size} file(s))")
    } else {
      println(rendered)
    }
  }

  private def collectScalaFiles(root: Path): List[Path] = {
    if (Files.isRegularFile(root)) {
      if (root.toString.endsWith(".scala")) List(root) else Nil
    } else if (Files.isDirectory(root)) {
      import scala.collection.JavaConverters._
      val stream = Files.walk(root)
      try {
        stream.iterator().asScala
          .filter(p => Files.isRegularFile(p) && p.toString.endsWith(".scala"))
          .toList.sortBy(_.toString)
      } finally stream.close()
    } else Nil
  }

  // ── per-file analysis ────────────────────────────────────────────────────────

  private def analyzeFile(p: Path, configPool: Map[String, String] = Map.empty): Json = {
    val code = try {
      new String(Files.readAllBytes(p), "UTF-8")
    } catch {
      case e: java.io.IOException =>
        return CJson.obj(
          "path"     -> p.toString.asJson,
          "parse_ok" -> false.asJson,
          "error"    -> s"read error: ${e.getMessage}".asJson,
        )
    }
    val input = Input.VirtualFile(p.toString, code)
    implicit val dialect: Dialect = dialects.Scala213

    input.parse[Source] match {
      case Parsed.Error(pos, msg, _) =>
        CJson.obj(
          "path"     -> p.toString.asJson,
          "parse_ok" -> false.asJson,
          "error"    -> s"$msg (line ${pos.startLine + 1})".asJson,
        )

      case Parsed.Success(tree) =>
        // ── resolver context (single pass each) ──────────────────────────────
        val baseVals    = buildValBindings(tree)
        val paramBinds  = buildParamBindings(tree)  // PR #3548: call-site arg expansion
        // Local vals take precedence over call-site parameter bindings
        val vals = baseVals ++ paramBinds.filter { case (k, _) => !baseVals.contains(k) }
        val defs = buildDefReturns(tree)
        val fors = buildForTargets(tree)

        // ── structural facts ──────────────────────────────────────────────────
        val imports = tree.collect { case i: Importer => i.syntax }.distinct.sorted
        val objects = tree.collect { case o: Defn.Object => o.name.value }.distinct.sorted

        val classes = tree.collect { case c: Defn.Class => c.name.value }.distinct.sorted
        val entrypoints = tree.collect {
          case o: Defn.Object => entrypointMethods(o.name.value, o.templ)
          case c: Defn.Class  => entrypointMethods(c.name.value, c.templ)
        }.flatten

        // ── mutable accumulators ──────────────────────────────────────────────
        val reads          = scala.collection.mutable.ListBuffer[Json]()
        val writes         = scala.collection.mutable.ListBuffer[Json]()
        val unresolvedRds  = scala.collection.mutable.ListBuffer[Json]()
        val unresolvedWrs  = scala.collection.mutable.ListBuffer[Json]()
        val filters        = scala.collection.mutable.ListBuffer[Json]()
        val joins          = scala.collection.mutable.ListBuffer[Json]()
        val tableRefs      = scala.collection.mutable.LinkedHashSet[String]()
        val colRefs        = scala.collection.mutable.LinkedHashSet[String]()
        var sparkSessionCreated = false

        // ── risk / unsupported-construct accumulators ─────────────────────────
        val sqlCalls        = scala.collection.mutable.ListBuffer[Json]()
        val udfs            = scala.collection.mutable.ListBuffer[Json]()
        val rddOps          = scala.collection.mutable.ListBuffer[Json]()
        var streaming       = false
        val externalIo      = scala.collection.mutable.ListBuffer[Json]()
        var reflectionUsage = false

        // Scan imports for external I/O libraries and reflection usage
        imports.foreach { impStr =>
          externalIoPrefixes.foreach { case (prefix, kind) =>
            if (impStr.contains(prefix) &&
                !externalIo.exists(_.hcursor.get[String]("kind").getOrElse("") == kind))
              externalIo += CJson.obj(
                "kind"           -> kind.asJson,
                "import_or_call" -> impStr.asJson,
                "line"           -> 0.asJson,
              )
          }
          if (impStr.contains("scala.reflect") || impStr.contains("java.io.ObjectInputStream") ||
              impStr.contains("com.esotericsoftware.kryo"))
            reflectionUsage = true
        }

        // Positions of negated `.isin(...)` so we do not seed excluded domains.
        val negatedIsinStarts = scala.collection.mutable.Set[Int]()
        tree.traverse {
          case Term.ApplyUnary(Term.Name(op), app @ Term.Apply(Term.Select(_, Term.Name("isin")), _))
              if op == "!" || op == "unary_!" || op == "not" =>
            negatedIsinStarts += app.pos.start
          case _ => ()
        }

        // ── named StructType bindings (Layer A) ───────────────────────────────
        val namedStructs = scala.collection.mutable.Map[String, List[Json]]()
        val structSchemasOut = scala.collection.mutable.ListBuffer[Json]()
        tree.traverse {
          case d: Defn.Val if d.pats.size == 1 =>
            d.pats.head match {
              case Pat.Var(n) =>
                val fields = parseStructFields(d.rhs)
                if (fields.nonEmpty) {
                  namedStructs(n.value) = fields
                  structSchemasOut += CJson.obj(
                    "name"   -> n.value.asJson,
                    "fields" -> fields.asJson,
                    "line"   -> (d.pos.startLine + 1).asJson,
                  )
                }
              case _ => ()
            }
          case d: Defn.Var if d.pats.size == 1 =>
            d.pats.head match {
              case Pat.Var(n) =>
                d.rhs.foreach { rhs =>
                  val fields = parseStructFields(rhs)
                  if (fields.nonEmpty) {
                    namedStructs(n.value) = fields
                    structSchemasOut += CJson.obj(
                      "name"   -> n.value.asJson,
                      "fields" -> fields.asJson,
                      "line"   -> (d.pos.startLine + 1).asJson,
                    )
                  }
                }
              case _ => ()
            }
          case _ => ()
        }
        val namedStructMap = namedStructs.toMap

        // ── emit helpers (resolve → edge, or unresolved edge) ────────────────
        // All helpers pass configPool to resolveSignatures so config-file
        // variable names can be resolved as a final fallback (PR #3548 parity).
        def emitRead(call: String, argTerm: Term, line: Int, receiver: Term): Unit = {
          val resolved = resolveSignatures(argTerm, 0, vals, defs, fors, configPool)
          val schemaFields = findSchemaOnChain(receiver, namedStructMap)
          if (resolved.nonEmpty)
            reads += callJson(call, resolved, line, schemaFields)
          else
            unresolvedRds += unresolvedJson("read", call,
              argTerm.syntax.take(200), line)
        }

        def emitWrite(call: String, argTerm: Term, line: Int): Unit = {
          val resolved = resolveSignatures(argTerm, 0, vals, defs, fors, configPool)
          if (resolved.nonEmpty)
            writes += callJson(call, resolved, line)
          else
            unresolvedWrs += unresolvedJson("write", call,
              argTerm.syntax.take(200), line)
        }

        def emitTableRead(call: String, argTerm: Term, line: Int, receiver: Term): Unit = {
          val resolved = resolveSignatures(argTerm, 0, vals, defs, fors, configPool)
          val schemaFields = findSchemaOnChain(receiver, namedStructMap)
          if (resolved.nonEmpty) {
            reads += callJson(call, resolved, line, schemaFields)
            tableRefs ++= resolved
          } else {
            unresolvedRds += unresolvedJson("read", call,
              argTerm.syntax.take(200), line)
          }
        }

        def emitTableWrite(call: String, argTerm: Term, line: Int): Unit = {
          val resolved = resolveSignatures(argTerm, 0, vals, defs, fors, configPool)
          if (resolved.nonEmpty) {
            writes += callJson(call, resolved, line)
            tableRefs ++= resolved
          } else {
            unresolvedWrs += unresolvedJson("write", call,
              argTerm.syntax.take(200), line)
          }
        }

        // Column-ref methods (string args are column names, not paths)
        val colMethods = Set(
          "select", "groupBy", "orderBy", "sort", "sortBy", "drop", "dropDuplicates",
        )

        // ── main traversal ────────────────────────────────────────────────────
        tree.traverse {
          // col("x") === Lit  /  $"x" === Lit  (positive equality only)
          case tai: Term.ApplyInfix if tai.op.value == "===" =>
            colNameFrom(tai.lhs).foreach { cn =>
              val vals = literalValues(tai.argClause.values)
              if (vals.nonEmpty) {
                filters += filterJson(cn, "===", vals, tai.pos.startLine + 1)
                colRefs += cn
              }
            }

          case ta: Term.Apply =>
            val allArgs = ta.argClause.values
            val strArgs = allArgs.collect { case Lit.String(s) => s }
            val line    = ta.pos.startLine + 1

            // Unwrap type-parameterized calls: sc.objectFile[T](path)
            val fun = ta.fun match {
              case Term.ApplyType(inner, _) => inner
              case other                    => other
            }
            fun match {
              case Term.Select(qual, Term.Name(m)) =>
                val recv = qual.collect { case Term.Name(n) => n }.toSet

                if (m == "getOrCreate" && recv.contains("SparkSession"))
                  sparkSessionCreated = true

                // ── writes ───────────────────────────────────────────────────
                if (m == "saveAsTable" || m == "insertInto") {
                  if (allArgs.nonEmpty) emitTableWrite(m, allArgs.head, line)
                } else if (m == "save" || (recv.contains("write") && fmtTerminals.contains(m))) {
                  if (allArgs.nonEmpty) emitWrite(m, allArgs.head, line)
                  else writes += callJson(m, Nil, line) // bare .save() with no path

                // A1.6: spark.catalog.createTable / createExternalTable
                } else if ((m == "createTable" || m == "createExternalTable") &&
                           recv.contains("catalog")) {
                  if (allArgs.nonEmpty) emitTableWrite(m, allArgs.head, line)

                // ── reads ────────────────────────────────────────────────────
                } else if (m == "table") {
                  // spark.table("name") — table is always a direct read + tableRef
                  if (allArgs.nonEmpty) emitTableRead(m, allArgs.head, line, qual)

                // A1.3: DeltaTable.forPath(spark, path) / forName(spark, name)
                //        SECOND arg is the path/name
                } else if ((m == "forPath" || m == "forName") &&
                           recv.contains("DeltaTable")) {
                  if (allArgs.size >= 2) emitRead(s"DeltaTable.$m", allArgs(1), line, qual)
                  else if (allArgs.size == 1) emitRead(s"DeltaTable.$m", allArgs.head, line, qual)

                // A1.5: spark.read.jdbc(url, table, ...) — SECOND arg is table
                } else if (m == "jdbc" && recv.contains("read")) {
                  if (allArgs.size >= 2)      emitRead("jdbc", allArgs(1), line, qual)
                  else if (allArgs.size == 1) emitRead("jdbc", allArgs.head, line, qual)

                // spark.read.{parquet,csv,...}(path)
                } else if (recv.contains("read") && readTerminals.contains(m)) {
                  if (allArgs.nonEmpty) emitRead(m, allArgs.head, line, qual)

                // A1.2: sc.textFile / wholeTextFiles / binaryFiles / etc.
                } else if (scReadMethods.contains(m) &&
                           (recv.contains("sc") || recv.contains("sparkContext"))) {
                  if (allArgs.nonEmpty) emitRead(m, allArgs.head, line, qual)

                // column refs
                } else if (colMethods.contains(m)) {
                  colRefs ++= strArgs

                // ── filters: col("x").isin(...) / .isin(Seq(...)) ────────────
                } else if (m == "isin" && !negatedIsinStarts.contains(ta.pos.start)) {
                  colNameFrom(qual).foreach { cn =>
                    val vals = literalValues(allArgs)
                    if (vals.nonEmpty) {
                      filters += filterJson(cn, "isin", vals, line)
                      colRefs += cn
                    }
                  }

                // ── filters: .filter/.where("col = 'val'") SQL-string form ───
                } else if ((m == "filter" || m == "where") && allArgs.size == 1) {
                  allArgs.head match {
                    case Lit.String(sql) =>
                      sql match {
                        case SimpleSqlEq(col, sq, dq, lit) =>
                          val v = Option(sq).orElse(Option(dq)).orElse(Option(lit)).getOrElse("")
                          filters += filterJson(col, "=", List(v), line)
                          colRefs += col
                        case _ =>
                          unresolvedRds += unresolvedJson("filter", m, sql.take(200), line)
                      }
                    case _ => () // Column expression handled via === / isin cases
                  }

                // ── joins: .join(other, Seq("k1","k2")) / "key" / col===col ──
                } else if (m == "join" && allArgs.size >= 2) {
                  allArgs(1) match {
                    case Term.Apply(Term.Name("Seq" | "List" | "Array" | "Vector"), elems) =>
                      val keys = elems.collect { case Lit.String(s) => s }
                      if (keys.nonEmpty && keys.size == elems.size) {
                        joins += joinJson(keys, line)
                        colRefs ++= keys
                      }
                    case Lit.String(k) if k.nonEmpty =>
                      joins += joinJson(List(k), line)
                      colRefs += k
                    case eq: Term.ApplyInfix if eq.op.value == "===" =>
                      // Column–column join: $"a" === $"b" / col("a") === col("b")
                      val keys = (
                        colNameFrom(eq.lhs).toList ++
                          eq.argClause.values.headOption.flatMap(colNameFrom).toList
                      ).distinct
                      if (keys.nonEmpty) {
                        joins += joinJson(keys, line)
                        colRefs ++= keys
                      }
                    case andExpr: Term.ApplyInfix
                        if andExpr.op.value == "&&" || andExpr.op.value == "and" =>
                      // Compound AND of column equalities → union join keys
                      val keys = collectJoinKeysFromPred(andExpr)
                      if (keys.nonEmpty) {
                        joins += joinJson(keys, line)
                        colRefs ++= keys
                      }
                    case other =>
                      unresolvedRds += unresolvedJson("join", m, other.syntax.take(200), line)
                  }

                // ── sql("...") calls ──────────────────────────────────────────
                } else if (m == "sql") {
                  allArgs.foreach {
                    case Lit.String(lit) =>
                      val lower = lit.toLowerCase
                      sqlCalls += CJson.obj(
                        "literal"          -> lit.asJson,
                        "has_current_date" -> lower.contains("current_date").asJson,
                        "has_qualify"      -> lower.contains("qualify").asJson,
                        "has_dateadd"      -> lower.contains("dateadd").asJson,
                        "has_sysdate"      -> lower.contains("sysdate").asJson,
                        "has_now"          -> lower.contains("now()").asJson,
                        "line"             -> line.asJson,
                      )
                    case _ => ()
                  }

                // ── UDF registration: spark.udf.register(name, fn) ───────────
                } else if (m == "register" &&
                           (recv.contains("udf") || recv.contains("functions"))) {
                  val udfName = strArgs.headOption.getOrElse("<dynamic>")
                  udfs += CJson.obj(
                    "call" -> "udf.register".asJson,
                    "name" -> udfName.asJson,
                    "line" -> line.asJson,
                  )

                // ── extra RDD ops: sc.parallelize / broadcast / accumulator / makeRDD ──
                } else if (rddOpMethods.contains(m) &&
                           (recv.contains("sc") || recv.contains("sparkContext"))) {
                  rddOps += CJson.obj("call" -> s"sc.$m".asJson, "line" -> line.asJson)

                // ── streaming signals: readStream / writeStream / trigger ─────
                } else if (streamingSignalMethods.contains(m)) {
                  streaming = true
                }

              case Term.Name(fn) if fn == "col" || fn == "column" =>
                colRefs ++= strArgs

              case Term.Name(fn) if fn == "udf" =>
                udfs += CJson.obj("call" -> "udf".asJson, "name" -> "<anonymous>".asJson, "line" -> line.asJson)

              case _ => ()
            }

          case ti: Term.Interpolate if ti.prefix.value == "$" =>
            colRefs ++= ti.parts.collect { case Lit.String(s) => s }
        }

        // ── Layer C: role-aware DF column attribution ─────────────────────────
        // Bind DataFrame vals to the read path/table that produced them, then
        // attribute select/groupBy/... string cols as inputs and withColumn as outputs.
        val dfToSource = scala.collection.mutable.Map[String, String]()
        val srcInputs  = scala.collection.mutable.Map[String, scala.collection.mutable.LinkedHashSet[String]]()
        val srcOutputs = scala.collection.mutable.Map[String, scala.collection.mutable.LinkedHashSet[String]]()

        def bindDf(name: String, rhs: Term): Unit =
          findReadArgOnTerm(rhs, vals, defs, fors, configPool, dfToSource).foreach { arg =>
            dfToSource(name) = arg
          }

        vals.foreach { case (name, rhs) => bindDf(name, rhs) }
        defs.foreach { case (name, body) => bindDf(name, body) }
        // Fixed-point: `val b = a.select(...)` inherits a's source
        var changed = true
        var guard = 0
        while (changed && guard < 8) {
          changed = false
          guard += 1
          vals.foreach { case (name, rhs) =>
            if (!dfToSource.contains(name)) {
              findReadArgOnTerm(rhs, vals, defs, fors, configPool, dfToSource).foreach { arg =>
                dfToSource(name) = arg
                changed = true
              }
            }
          }
        }

        tree.traverse {
          case ta: Term.Apply =>
            val allArgs = ta.argClause.values
            val strArgs = allArgs.collect { case Lit.String(s) => s }
            val fun = ta.fun match {
              case Term.ApplyType(inner, _) => inner
              case other                    => other
            }
            fun match {
              case Term.Select(qual, Term.Name(m)) =>
                baseVarName(qual).flatMap(dfToSource.get).foreach { srcArg =>
                  val inputs  = srcInputs.getOrElseUpdate(srcArg, scala.collection.mutable.LinkedHashSet[String]())
                  val outputs = srcOutputs.getOrElseUpdate(srcArg, scala.collection.mutable.LinkedHashSet[String]())
                  if (colMethods.contains(m)) {
                    inputs ++= strArgs
                    colRefs ++= strArgs
                  } else if (m == "withColumn" && strArgs.nonEmpty) {
                    outputs += strArgs.head
                    colRefs += strArgs.head
                  } else if (m == "withColumnRenamed" && strArgs.size >= 2) {
                    inputs += strArgs.head
                    outputs += strArgs(1)
                    colRefs ++= strArgs
                  } else if ((m == "filter" || m == "where") && allArgs.size == 1) {
                    allArgs.head match {
                      case Lit.String(sql) =>
                        sql match {
                          case SimpleSqlEq(col, _, _, _) =>
                            inputs += col
                            colRefs += col
                          case _ => ()
                        }
                      case _ => ()
                    }
                  }
                }
              case _ => ()
            }
          case _ => ()
        }

        val sourceColumnsJson = (srcInputs.keySet ++ srcOutputs.keySet).toList.sorted.map { arg =>
          val outs = srcOutputs.getOrElse(arg, scala.collection.mutable.LinkedHashSet()).toSet
          val ins  = srcInputs.getOrElse(arg, scala.collection.mutable.LinkedHashSet()).toSet -- outs
          CJson.obj(
            "arg"         -> arg.asJson,
            "input_cols"  -> ins.toList.sorted.asJson,
            "output_cols" -> outs.toList.sorted.asJson,
          )
        }
        val dfBindingsJson = dfToSource.toList.sortBy(_._1).map { case (v, arg) =>
          CJson.obj("var" -> v.asJson, "arg" -> arg.asJson)
        }

        // ── secondary traversal: streaming type refs + reflection ────────────
        tree.traverse {
          case t: Type.Name
              if t.value == "StreamingQuery" || t.value == "DataStreamWriter" ||
                 t.value == "DataStreamReader" =>
            streaming = true
          case Term.ApplyType(Term.Name("classOf"), _) =>
            reflectionUsage = true
          case Term.Name(n)
              if n == "ObjectInputStream" || n == "Kryo" || n == "KryoSerializer" =>
            reflectionUsage = true
          case _ => ()
        }

        // ── build unsupported_constructs ──────────────────────────────────────
        val unsupportedConstructs = scala.collection.mutable.ListBuffer[Json]()
        rddOps.foreach { op =>
          val call = op.hcursor.get[String]("call").getOrElse("")
          val ln   = op.hcursor.get[Int]("line").getOrElse(0)
          unsupportedConstructs += CJson.obj(
            "kind"             -> "rdd_op".asJson,
            "detail"           -> call.asJson,
            "line"             -> ln.asJson,
            "phase_b_blocking" -> true.asJson,
          )
        }
        udfs.foreach { udfEntry =>
          val nm = udfEntry.hcursor.get[String]("name").getOrElse("")
          val ln = udfEntry.hcursor.get[Int]("line").getOrElse(0)
          unsupportedConstructs += CJson.obj(
            "kind"             -> "udf".asJson,
            "detail"           -> nm.asJson,
            "line"             -> ln.asJson,
            // UDFs often work (or are fixable) on SCOS — warn, don't hard-block Phase B.
            "phase_b_blocking" -> false.asJson,
          )
        }
        if (streaming)
          unsupportedConstructs += CJson.obj(
            "kind"             -> "streaming".asJson,
            "detail"           -> "streaming operations detected".asJson,
            "line"             -> 0.asJson,
            "phase_b_blocking" -> true.asJson,
          )
        externalIo.foreach { ioEntry =>
          val kind   = ioEntry.hcursor.get[String]("kind").getOrElse("")
          val detail = ioEntry.hcursor.get[String]("import_or_call").getOrElse("")
          val ln     = ioEntry.hcursor.get[Int]("line").getOrElse(0)
          unsupportedConstructs += CJson.obj(
            "kind"             -> "external_io".asJson,
            "detail"           -> s"$kind: $detail".asJson,
            "line"             -> ln.asJson,
            "phase_b_blocking" -> true.asJson,
          )
        }

        // ── write-helper function detection (transitive) ─────────────────────
        def bodyWrites(body: Tree): Boolean = body.collect {
          case Term.Apply(Term.Select(qual, Term.Name(m)), _) =>
            val recv = qual.collect { case Term.Name(n) => n }.toSet
            m == "saveAsTable" || m == "insertInto" || m == "save" ||
              (recv.contains("write") && fmtTerminals.contains(m))
        }.exists(identity)

        def calledNames(body: Tree): Set[String] = body.collect {
          case Term.Apply(Term.Name(fn), _)               => fn
          case Term.Apply(Term.Select(_, Term.Name(fn)), _) => fn
        }.toSet

        val treeDefs   = tree.collect { case d: Defn.Def => d }
        val directWrts = treeDefs.collect { case d if bodyWrites(d.body) => d.name.value }.toSet
        val writeHelps = scala.collection.mutable.LinkedHashSet[String]() ++ directWrts
        treeDefs.foreach { d =>
          val nm = d.name.value
          if (!writeHelps.contains(nm) && (calledNames(d.body) & directWrts).nonEmpty)
            writeHelps += nm
        }

        // ── output ────────────────────────────────────────────────────────────
        CJson.obj(
          "path"                   -> p.toString.asJson,
          "parse_ok"               -> true.asJson,
          "objects"                -> objects.asJson,
          "classes"                -> classes.asJson,
          "entrypoints"            -> entrypoints.asJson,
          "imports"                -> imports.asJson,
          "spark_session_created"  -> sparkSessionCreated.asJson,
          "reads"                  -> reads.toList.asJson,
          "writes"                 -> writes.toList.asJson,
          "unresolved_reads"       -> unresolvedRds.toList.asJson,
          "unresolved_writes"      -> unresolvedWrs.toList.asJson,
          "write_helpers"          -> writeHelps.toList.asJson,
          "table_refs"             -> tableRefs.toList.asJson,
          "column_refs"            -> colRefs.toList.asJson,
          "filters"                -> filters.toList.asJson,
          "joins"                  -> joins.toList.asJson,
          "struct_schemas"         -> structSchemasOut.toList.asJson,
          "df_bindings"            -> dfBindingsJson.asJson,
          "source_columns"         -> sourceColumnsJson.asJson,
          "sql_calls"              -> sqlCalls.toList.asJson,
          "udfs"                   -> udfs.toList.asJson,
          "rdd_ops"                -> rddOps.toList.asJson,
          "streaming"              -> streaming.asJson,
          "external_io"            -> externalIo.toList.asJson,
          "reflection_usage"       -> reflectionUsage.asJson,
          "unsupported_constructs" -> unsupportedConstructs.toList.asJson,
        )
    }
  }

  private def entrypointMethods(owner: String, templ: Template): List[Json] =
    templ.stats.collect {
      case d: Defn.Def if d.name.value == "main" || d.name.value == "run" =>
        CJson.obj("owner" -> owner.asJson, "method" -> d.name.value.asJson)
    }
}

// com.snowflake.scos.scalafix.ScosMigrateFacts
//
// Deterministic Scala AST fact extraction for the MIGRATE analyzer
// (analyze_scala.py). Mirrors the validate skill's ScosAnalyze, but emits the
// facts the migrate analyzer needs for INCOMPATIBILITY DETECTION — every fact
// carries a 1-based line number so findings map back to source lines.
//
// It is a pure Scalameta SyntacticDocument-free parser (no SemanticDB / types),
// so any `.scala` file parses without a compilation classpath. The Python
// analyzer runs its detection rules against these facts when the JVM toolchain
// is available, and falls back to its own regex detectors when it is not.
//
// Usage:
//   java -cp <classpath> com.snowflake.scos.scalafix.ScosMigrateFacts \
//       --source <file-or-dir> [--output <path>]
//
// Output: JSON to stdout (or --output). Exit 0 always; per-file `parse_ok`
// surfaces parse failures without aborting the run.
package com.snowflake.scos.scalafix

import io.circe.Json
import io.circe.syntax._

import java.nio.file.{Files, Path, Paths}
import scala.meta._
import scala.meta.parsers.Parsed

object ScosMigrateFacts {

  def main(args: Array[String]): Unit = {
    var source = ""
    var output = ""
    args.sliding(2, 2).foreach {
      case Array("--source", v) => source = v
      case Array("--output", v) => output = v
      case _                    => ()
    }
    if (source.isEmpty) {
      System.err.println("ScosMigrateFacts: --source <file-or-dir> is required")
      System.exit(2)
    }

    val root  = Paths.get(source).toAbsolutePath.normalize()
    val files = collectScalaFiles(root)
    val fileResults = files.map(analyzeFile)

    val out = Json.obj(
      "source"       -> source.asJson,
      "file_count"   -> files.size.asJson,
      "parse_errors" -> fileResults.count(j => !j.hcursor.get[Boolean]("parse_ok").getOrElse(true)).asJson,
      "files"        -> fileResults.asJson,
    )

    val rendered = out.spaces2
    if (output.nonEmpty) {
      val outPath = Paths.get(output).toAbsolutePath
      Option(outPath.getParent).foreach(Files.createDirectories(_))
      Files.write(outPath, (rendered + "\n").getBytes("UTF-8"))
      System.err.println(s"[scos-migrate-facts] wrote $outPath (${files.size} file(s))")
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

  // 1-based line for a tree node.
  private def lineOf(t: Tree): Int = t.pos.startLine + 1

  // Leaf identifier of a (possibly chained) receiver, e.g. `spark.read` -> "read",
  // `df` -> "df", `x.sparkContext` -> "sparkContext". Used by receiver-aware rules.
  private def leafName(t: Term): String = t match {
    case Term.Select(_, Term.Name(n)) => n
    case Term.Name(n)                  => n
    case Term.Apply(fun, _)            => leafName(fun)
    case other                         => other.syntax.takeRight(40)
  }

  // Bounded receiver syntax (for rules that need the chain text, e.g. spark.sql).
  private def recvSyntax(t: Term): String = {
    val s = t.syntax.replaceAll("\\s+", " ")
    if (s.length > 80) s.takeRight(80) else s
  }

  private def analyzeFile(p: Path): Json = {
    val code = new String(Files.readAllBytes(p), "UTF-8")
    val input = Input.VirtualFile(p.toString, code)
    implicit val dialect: Dialect = dialects.Scala213

    input.parse[Source] match {
      case Parsed.Error(pos, msg, _) =>
        Json.obj(
          "path"     -> p.toString.asJson,
          "parse_ok" -> false.asJson,
          "error"    -> s"$msg (line ${pos.startLine + 1})".asJson,
        )

      case Parsed.Success(tree) =>
        val imports   = scala.collection.mutable.ListBuffer[Json]()
        val calls     = scala.collection.mutable.ListBuffer[Json]()
        val selects   = scala.collection.mutable.ListBuffer[Json]()
        val newTypes  = scala.collection.mutable.ListBuffer[Json]()
        val sqlStr    = scala.collection.mutable.ListBuffer[Json]()
        val interp    = scala.collection.mutable.ListBuffer[Json]()
        val infixOps  = scala.collection.mutable.ListBuffer[Json]()
        var sessionCreated = false

        tree.traverse {
          // import a.b.c  /  import a.b.{c, d}
          case imp: Import =>
            imp.importers.foreach { er =>
              val base = er.ref.syntax
              er.importees.foreach {
                case Importee.Name(nm)      => imports += importJson(s"$base.${nm.value}", lineOf(imp))
                case Importee.Wildcard()    => imports += importJson(s"$base._", lineOf(imp))
                case other                  => imports += importJson(s"$base.${other.syntax}", lineOf(imp))
              }
              if (er.importees.isEmpty) imports += importJson(base, lineOf(imp))
            }

          // method calls: <recv>.<method>(<args>)  and  <func>(<args>)
          case ta: Term.Apply =>
            val strArgs  = ta.argClause.values.collect { case Lit.String(s) => s }
            // ALL positional args as bounded syntax (numeric/type/name args too),
            // so arg-discriminated rules (substring(...,0), cast(BooleanType)) can
            // reconstruct the call faithfully.
            val argExprs = ta.argClause.values.map(a => headSyntax(a.syntax)).toList
            ta.fun match {
              case Term.Select(qual, Term.Name(m)) =>
                if (m == "getOrCreate" && qual.syntax.contains("SparkSession")) sessionCreated = true
                if (m == "sql" && (leafName(qual) == "spark" || qual.syntax.contains("spark") ||
                                   leafName(qual) == "sf")) {
                  strArgs.foreach(s => sqlStr += sqlJson(s, lineOf(ta)))
                }
                calls += callJson(m, leafName(qual), recvSyntax(qual), strArgs, argExprs, lineOf(ta))
              case Term.Name(fn) =>
                calls += callJson(fn, "", "", strArgs, argExprs, lineOf(ta))
              case _ => ()
            }

          // bare member access (no call): df.rdd, x.sparkContext, df.isEmpty
          case sel @ Term.Select(qual, Term.Name(m)) =>
            selects += selectJson(m, leafName(qual), lineOf(sel))

          // infix operators: col("a") / lit(0), a <=> b, col === lit("x")
          // `lhs` keeps its TAIL and `rhs` its HEAD so the Python operand-shape
          // regexes (column-immediately-before-/, lit("...") right after ===) match.
          case ai @ Term.ApplyInfix(lhs, Term.Name(op), _, args) =>
            val rhs = args.headOption.map(_.syntax).getOrElse("")
            infixOps += infixJson(op, tailSyntax(lhs.syntax), headSyntax(rhs), lineOf(ai))

          // new SparkContext(...) etc.
          case ni: Init =>
            ni.tpe match {
              case Type.Name(tn) => newTypes += newJson(tn, lineOf(ni))
              case _             => ()
            }

          // s"...$col..." interpolation (column refs live in $"x" form)
          case ti: Term.Interpolate if ti.prefix.value == "$" =>
            ti.parts.collect { case Lit.String(s) => s }.foreach(s => interp += interpJson(s, lineOf(ti)))
        }

        Json.obj(
          "path"            -> p.toString.asJson,
          "parse_ok"        -> true.asJson,
          "imports"         -> imports.toList.asJson,
          "calls"           -> calls.toList.asJson,
          "selects"         -> selects.toList.asJson,
          "new_types"       -> newTypes.toList.asJson,
          "spark_sql"       -> sqlStr.toList.asJson,
          "infix"           -> infixOps.toList.asJson,
          "interpolations"  -> interp.toList.asJson,
          "session_created" -> sessionCreated.asJson,
        )
    }
  }

  private def importJson(ref: String, line: Int): Json =
    Json.obj("ref" -> ref.asJson, "line" -> line.asJson)
  private def callJson(method: String, recvLeaf: String, recv: String, args: List[String], argExprs: List[String], line: Int): Json =
    Json.obj("method" -> method.asJson, "recv_leaf" -> recvLeaf.asJson, "recv" -> recv.asJson,
             "args" -> args.asJson, "arg_exprs" -> argExprs.asJson, "line" -> line.asJson)
  private def selectJson(member: String, recvLeaf: String, line: Int): Json =
    Json.obj("member" -> member.asJson, "recv_leaf" -> recvLeaf.asJson, "line" -> line.asJson)
  private def newJson(tpe: String, line: Int): Json =
    Json.obj("type" -> tpe.asJson, "line" -> line.asJson)
  private def sqlJson(text: String, line: Int): Json =
    Json.obj("text" -> text.asJson, "line" -> line.asJson)
  private def infixJson(op: String, lhs: String, rhs: String, line: Int): Json =
    Json.obj("op" -> op.asJson, "lhs" -> lhs.asJson, "rhs" -> rhs.asJson, "line" -> line.asJson)
  private def interpJson(text: String, line: Int): Json =
    Json.obj("text" -> text.asJson, "line" -> line.asJson)

  // Operand syntax bounded for the Python operand-shape regexes: keep the TAIL
  // of the left operand (column immediately before the operator) and the HEAD of
  // the right operand (e.g. `lit("...")` directly after the operator).
  private def tailSyntax(s: String): String = {
    val n = s.replaceAll("\\s+", " ")
    if (n.length > 80) n.takeRight(80) else n
  }
  private def headSyntax(s: String): String = {
    val n = s.replaceAll("\\s+", " ")
    if (n.length > 80) n.take(80) else n
  }
}

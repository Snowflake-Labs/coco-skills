// SCOS Scala Analyze Plane — sbt build
// The deterministic `analyze` command (ScosAnalyze) only: a Scalameta AST
// facts extractor for the data-synthesizer agent. All other control-plane commands
// (state, provision, cleanup, compare, snapshot) now reuse the canonical
// PySpark validator scripts (validate.py, provision.py/cleanup.py,
// harness/comparator.py) at ../../validate-pyspark-to-snowpark-connect/scripts,
// so Spark, the Snowflake JDBC driver, and scopt are no longer needed here.
// Dependencies: circe JSON + Scalameta only.

ThisBuild / scalaVersion := "2.12.19"
ThisBuild / organization := "com.snowflake.scos"

lazy val root = (project in file("."))
  .settings(
    name := "scos-analyze",
    version := "0.1.0",

    // ── circe JSON (parse + encode + generic derivation) ───────────────────
    libraryDependencies ++= Seq(
      "io.circe" %% "circe-core"    % "0.14.9",
      "io.circe" %% "circe-generic" % "0.14.9",
      "io.circe" %% "circe-parser"  % "0.14.9",
    ),

    // ── Scalameta (deterministic Scala source analysis for the `analyze` cmd) ─
    libraryDependencies += "org.scalameta" %% "scalameta" % "4.9.9",

    // ── ScalaTest ──────────────────────────────────────────────────────────
    libraryDependencies += "org.scalatest" %% "scalatest" % "3.2.19" % Test,

    // Assembly fat-jar (for running as standalone CLI)
    assembly / mainClass := Some("com.snowflake.scos.validate.Main"),

    // Write directly to the path the SKILL / agents invoke (target/scos-analyze.jar)
    // so `sbt assembly` alone satisfies the documented prereq check.
    assembly / assemblyOutputPath := target.value / "scos-analyze.jar",

    assembly / assemblyMergeStrategy := {
      case PathList("META-INF", "services", _*)                        => MergeStrategy.concat
      case PathList("META-INF", xs @ _*) if xs.lastOption.exists { n =>
             n.endsWith(".SF") || n.endsWith(".DSA") || n.endsWith(".RSA") } => MergeStrategy.discard
      case PathList("META-INF", _*)                                    => MergeStrategy.discard
      case "reference.conf"                                            => MergeStrategy.concat
      case "module-info.class"                                         => MergeStrategy.discard
      case x if x.endsWith("module-info.class")                        => MergeStrategy.discard
      case _                                                           => MergeStrategy.first
    },
  )

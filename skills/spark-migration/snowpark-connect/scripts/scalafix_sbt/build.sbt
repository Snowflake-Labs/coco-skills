// scalafix_sbt/build.sbt
// ─────────────────────────────────────────────────────────────────────────────
// Pinned sbt wrapper for Phase 0.5b Scalafix pre-processing (SCOS Scala migration).
//
// Purpose
// ───────
// sbt is the build tool that virtually every Scala developer already has on
// PATH (coursier / scalafix-cli usually are NOT).  This project lets
// preprocess_scalafix.py run the AST-grade SCOS rules through sbt instead of
// depending on a standalone Coursier + scalafix-cli toolchain.
//
// sbt is used here purely as a DETERMINISTIC RESOLVER + COMPILER, not via the
// sbt-scalafix plugin.  Its only jobs are:
//   1. Compile the SCOS rules from ../scalafix_rules/SCOSRules.scala (single
//      source of truth — not duplicated here).
//   2. Resolve scalafix-cli (which brings the `scalafix.cli.Cli` main class,
//      scalafix-core, and scalameta).
//
// preprocess_scalafix.py then runs:
//   sbt --batch -error "export Compile/fullClasspath"
// to obtain ONE classpath containing the compiled rule classes + scalafix-cli
// + all transitive deps, and invokes:
//   java -cp <classpath> scalafix.cli.Cli --rules file:scos.scalafix.conf ...
// reusing the existing --diff / --in-place flow unchanged.
//
// Version pinning (verified on Maven Central)
// ────────────────────────────────────────────
//   scala        2.12.20
//   scalafix-cli 0.14.3   (published for scalafix-cli_2.12.20; 0.14.6 is NOT)
// scalafix-cli is published with a FULL Scala-version suffix, so CrossVersion.full
// is required — `%%` (binary) would resolve a nonexistent scalafix-cli_2.12.
// ─────────────────────────────────────────────────────────────────────────────

ThisBuild / organization := "com.snowflake.scos"

lazy val root = (project in file("."))
  .settings(
    name         := "scos-scalafix-runner",
    version      := "0.1.0",
    scalaVersion := "2.12.20",

    // scalafix-cli == the CLI engine (scalafix.cli.Cli) + scalafix-core (the
    // SyntacticRule API the rules compile against) + scalameta.
    libraryDependencies += ("ch.epfl.scala" % "scalafix-cli" % "0.14.3")
      .cross(CrossVersion.full),

    // circe is used by ScosMigrateFacts.scala (the AST fact extractor for the
    // migrate analyzer) to emit JSON. Pinned to match the validate skill's
    // scos-analyze build. scalafix-cli does not depend on circe, so this only
    // adds jars to the exported classpath; it does not change rule behavior.
    libraryDependencies ++= Seq(
      "io.circe" %% "circe-core"   % "0.14.9",
      "io.circe" %% "circe-parser" % "0.14.9",
    ),

    // Compile the canonical rule sources in place — single source of truth.
    // ../scalafix_rules contains SCOSRules.scala (compiled) and scos.scalafix.conf
    // (ignored by the Scala compiler).
    Compile / unmanagedSourceDirectories := Seq(
      baseDirectory.value / ".." / "scalafix_rules"
    ),

    // No need to compile any test sources.
    Test / sources := Seq.empty,

    resolvers += Resolver.mavenCentral,
  )

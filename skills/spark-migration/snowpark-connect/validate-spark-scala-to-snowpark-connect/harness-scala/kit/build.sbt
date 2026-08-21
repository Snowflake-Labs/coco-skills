// harness-scala/kit/build.sbt
// ScalaTest kit for Spark-Scala → Snowpark-Connect (SCOS) A/B differential validation.
// Ported from: validate-pyspark-to-snowpark-connect/scripts/harness/ (Python).

name         := "scos-harness-kit"
organization := "com.snowflake.scos"
version      := "0.1.0-SNAPSHOT"
// Phase A runs the ORIGINAL workload on local Spark. To stay binary-compatible with the
// workload's compiled bytecode (a workload built for Spark 3.3 calls 2-arg Catalyst APIs
// that don't exist in 3.5, etc.), the kit's Spark / Delta / Scala versions can be
// overridden per run via env vars — scos_state.py run-phase-a sets these from the source
// build.sbt. Phase B leaves the defaults (Spark 3.5.x / delta-spark) so the SCOS client
// jar matches. Note the Delta artifact name changed from `delta-core` (Spark 3.3/3.4) to
// `delta-spark` (Spark 3.5+), so the artifact id is overridable too.
scalaVersion := sys.env.getOrElse("SCOS_KIT_SCALA_VERSION", "2.12.19")

val sparkVersion         = sys.env.getOrElse("SCOS_KIT_SPARK_VERSION", "3.5.1")
val deltaArtifact        = sys.env.getOrElse("SCOS_KIT_DELTA_ARTIFACT", "delta-spark")
val deltaVersion         = sys.env.getOrElse("SCOS_KIT_DELTA_VERSION", "3.1.0")
val scalaTestVersion     = "3.2.19"
val snowflakeJdbcVersion = "3.27.0"  // 3.27+ required for programmatic_access_token PAT auth

// SCOS Scala client:
//   Artifact: com.snowflake:snowpark-connect-java-client_2.12:<version>
//   See migrate-spark-scala-to-snowpark-connect/references/fix-rules.md Rule 25.
//   UNCERTAINTY: exact published version not pinned in this repo.
//   ScosTrialFixture initialises it via JVM reflection — NOT a compile dep.
//   Place the real JAR in kit lib/ BEFORE running Phase B trials.
//   Missing JAR → ClassNotFoundException: com.snowflake.snowpark_connect.client.SnowparkConnectSession

libraryDependencies ++= Seq(
  // Spark — needed for Phase A local session; SCOS provides its own in Phase B.
  "org.apache.spark"             %% "spark-sql"              % sparkVersion,
  "org.apache.spark"             %% "spark-catalyst"         % sparkVersion,

  // Hive support — workloads that call `.enableHiveSupport()` (Hive metastore /
  // saveAsTable into a Hive catalog) need this on the Phase A classpath, else
  // they fail with "Unable to instantiate SparkSession with Hive support".
  "org.apache.spark"             %% "spark-hive"             % sparkVersion,

  // Delta Lake — Phase A local SparkSession. Artifact id + version are env-overridable
  // (delta-core for Spark 3.3/3.4, delta-spark for 3.5+) to match the workload's Spark.
  "io.delta"                     %% deltaArtifact            % deltaVersion,

  // Snowflake JDBC — golden schema CLONE / DROP (Phase B setup/teardown).
  "net.snowflake"                %  "snowflake-jdbc"         % snowflakeJdbcVersion,

  // JSON (analysis.json, state.json, _index.json).
  "com.fasterxml.jackson.module" %% "jackson-module-scala"  % "2.15.2",
  "com.fasterxml.jackson.core"   %  "jackson-databind"      % "2.15.2",

  // ScalaTest — full bundle includes funsuite + shouldmatchers.
  "org.scalatest"                %% "scalatest"              % scalaTestVersion,
)

// The migrated workload JAR (compiled by its own sbt/maven/gradle build) goes in lib/.
// ScosTrialFixture/ReflectionEntrypoint loads entrypoints from it via JVM reflection.
// This mirrors Python importlib.util.spec_from_file_location — decouples the
// harness from the workload's build tool.
Compile / unmanagedJars ++= {
  val libDir = baseDirectory.value / "lib"
  if (libDir.isDirectory) (libDir ** "*.jar").classpath
  else Seq.empty
}

// TestTemplate.scala.tmpl is NOT compiled Scala.
// The local-runner agent renders it into per-trial test specs at run time.
// Placed in templates/ (added as resource dir) to keep it off the source set.
Compile / unmanagedResourceDirectories += baseDirectory.value / "templates"

// Classpath ordering is SCOS_FLAVOR-aware:
// Phase B (migrated): unmanaged (lib/) first so spark-connect-client-jvm_2.12 provides
//   SparkSession.Builder.remote() before the managed spark-sql JAR that lacks it.
//   Without this Phase B fails with: NoSuchMethodError: SparkSession$Builder.remote(String)
// Phase A (source): managed spark-sql first so SparkSession.builder() builds a LOCAL
//   session; the connect-client JAR shadows SparkSession and routes to SCOS otherwise.
Test / externalDependencyClasspath := {
  val flavor    = sys.env.getOrElse("SCOS_FLAVOR", "source")
  val unmanaged = (Test / unmanagedClasspath).value
  val managed   = (Test / externalDependencyClasspath).value
  if (flavor == "migrated")
    unmanaged ++ managed.filterNot(f => unmanaged.exists(_.data.getName == f.data.getName))
  else
    managed ++ unmanaged.filterNot(f => managed.exists(_.data.getName == f.data.getName))
}

// Fork JVM per test so system-property overrides (EnvUtil) don't leak.
Test / fork := true
Test / javaOptions ++= Seq(
  "-Dspark.master=local[1]",
  "-Dspark.sql.shuffle.partitions=1",
  "-Dspark.driver.host=127.0.0.1",
  "-Dspark.driver.bindAddress=127.0.0.1",
  "-Dio.netty.tryReflectionSetAccessible=true",
  "--add-opens=java.base/java.lang=ALL-UNNAMED",
  "--add-opens=java.base/java.nio=ALL-UNNAMED",
  "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
  "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
  "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED",
  "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
  "--add-opens=java.base/java.io=ALL-UNNAMED",
  "--add-opens=java.base/java.net=ALL-UNNAMED",
  "--add-opens=java.base/java.util=ALL-UNNAMED",
  "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
  "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED",
  "--add-opens=java.base/sun.nio.cs=ALL-UNNAMED",
  "--add-opens=java.base/sun.util.calendar=ALL-UNNAMED",
  // Arrow off-heap DirectBuffer allocation (required on JDK 17)
  "--add-opens=java.base/jdk.internal.misc=ALL-UNNAMED",
  "--add-opens=jdk.unsupported/sun.misc=ALL-UNNAMED",
)

resolvers += Resolver.mavenCentral

// spark-connect-client-jvm (placed in lib/) calls com.google.common methods added
// in Guava 20+. Force a modern unshaded Guava so the fat JAR finds them at runtime.
dependencyOverrides += "com.google.guava" % "guava" % "32.0.1-jre"

// scala-xml conflict: ScalaTest pulls scala-xml 2.x while older Spark lines (3.3/3.4)
// pull 1.x. scala-xml is binary-compatible across this range, so tell sbt's strict
// eviction check to accept either rather than failing `update`. Harmless on Spark 3.5
// (no conflict there). Without this, kit version alignment for Phase A fails to resolve.
ThisBuild / libraryDependencySchemes += "org.scala-lang.modules" %% "scala-xml" % VersionScheme.Always

// --- Bounded parallel test execution -----------------------------------------
// Each test suite (one rendered spec per entrypoint) runs in its OWN forked JVM
// via testGrouping, so EnvUtil's process-global System.setProperty overrides can
// never race across trials. Those per-suite forks then run in PARALLEL, capped by
// SCOS_TEST_PARALLELISM (default 4; set to 1 for fully serial). The cap protects
// both the machine (Phase A: up to N local Spark JVMs) and Snowflake/SCOS
// (Phase B: up to N concurrent SCOS sessions + schema clones) — lower it if the
// connection rate-limits or the warehouse is small.
//
// Per-trial isolation that makes this safe already exists:
//   * Phase A: a fresh warehouse/checkpoint dir per test (ScosTrialFixture).
//   * Phase B: a uniquely-named golden-schema clone "<GOLDEN>_T<8hex>" per trial
//     (Helpers.cloneGoldenSchemaForTrial), so parallel trials never collide.
Test / parallelExecution := true
Test / testForkedParallel := true
Test / testGrouping := {
  val forkOpts = (Test / forkOptions).value
  (Test / definedTests).value.map { t =>
    Tests.Group(name = t.name, tests = Seq(t), runPolicy = Tests.SubProcess(forkOpts))
  }
}
Global / concurrentRestrictions += {
  val n = sys.env.get("SCOS_TEST_PARALLELISM")
    .flatMap(s => scala.util.Try(s.trim.toInt).toOption)
    .getOrElse(4)
  Tags.limit(Tags.ForkedTestGroup, math.max(1, n))
}

// Per-trial timeout: each workload runs on a daemon thread in ScosTrialFixture.runTrial
// bounded by SCOS_TRIAL_TIMEOUT_SECS (default 300s). On timeout the trial is recorded
// as FAILED and the forked JVM exits cleanly via afterAll(). sbt Tests.Timeout is NOT
// used (removed in sbt 1.10); the daemon-thread approach is both JVM-version-safe and
// works for blocking native calls (gRPC recv) that Thread.interrupt() cannot stop.

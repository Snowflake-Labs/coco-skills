package com.snowflake.scos.kit

/**
 * SubprocessLauncher — entry point for Phase A subprocess execution.
 *
 * This object is invoked as the main class of a child JVM launched by
 * ScosTrialFixture when SCOS_PHASE_A_SUBPROCESS=true.  Running the workload
 * in its own JVM avoids the SerializedLambda classloader conflict that occurs
 * when ReflectionEntrypoint uses a URLClassLoader inside the harness JVM.
 *
 * Usage (internal — invoked by ScosTrialFixture._runSubprocess):
 *
 *   java -cp <workloadJar:sparkJars:harnessJar> \
 *        com.snowflake.scos.kit.SubprocessLauncher \
 *        <entryClass> <entryMethod> [workloadArgs...]
 *
 * Required system properties (set by ScosTrialFixture):
 *   SCOS_CAPTURE_DIR     — directory to write captured output parquet files
 *   SCOS_INPUT_*         — mock data file paths for each external source
 *   SCOS_MOCK_DATA_DIR   — root mock data directory
 *   SCOS_WAREHOUSE_DIR   — local Spark warehouse dir (temp dir per trial)
 *   SCOS_PINNED_TIMESTAMP — fixed timestamp for date determinism
 *
 * The workload is invoked via JVM reflection using the child JVM's own
 * classloader (no URLClassLoader wrapping).  All Spark lambda expressions
 * serialise and deserialise cleanly because driver and executor share the
 * same flat classloader.
 *
 * Write-intercept patches (scos_state.py patch-add entries) redirect
 * Write.dfToDelta and Load.load to write parquet directories under
 * SCOS_CAPTURE_DIR instead of calling saveAsTable or MongoDB.
 * ScosTrialFixture reads those directories after subprocess exit to build
 * the _index.json capture manifest.
 */
object SubprocessLauncher {

  def main(rawArgs: Array[String]): Unit = {
    if (rawArgs.length < 2) {
      System.err.println(
        "[SubprocessLauncher] Usage: SubprocessLauncher <entryClass> <entryMethod> [workloadArgs...]")
      System.exit(2)
    }

    val entryClass  = rawArgs(0)   // e.g. "com.flashfood.petl.pipeline.job.LoadStaticTaxes"
    val entryMethod = rawArgs(1)   // e.g. "run"
    val workloadArgs = rawArgs.drop(2)

    // ----------------------------------------------------------------
    // 1. Initialise Spark — same catalog/config as Helpers.buildLocalSession.
    //    Plain Hive catalog (NOT DeltaCatalog): Scala 2.12 + Java 17 hit
    //    DELTA-3744 / LambdaMetafactory limits when seed/saveAsTable goes
    //    through DeltaCatalog. Workloads can still write delta via
    //    df.write.format("delta").save(path).
    // ----------------------------------------------------------------
    val warehouseDir = sys.props.getOrElse("SCOS_WAREHOUSE_DIR",
      java.nio.file.Files.createTempDirectory("scos-subprocess-wh-").toString)
    sys.props("spark.sql.warehouse.dir") = warehouseDir
    sys.props("derby.system.home") = warehouseDir + "/derby"
    sys.props("spark.master") = "local[1]"
    sys.props("spark.app.name") = s"scos-subprocess-$entryClass"
    sys.props("spark.ui.enabled") = "false"
    sys.props("spark.driver.host") = "127.0.0.1"
    sys.props("spark.driver.bindAddress") = "127.0.0.1"
    sys.props("spark.sql.shuffle.partitions") = "1"
    sys.props("spark.databricks.delta.schema.autoMerge.enabled") = "true"
    sys.props("spark.databricks.delta.commitInfo.enabled") = "false"
    // Do NOT set spark.sql.extensions / spark.sql.catalog.spark_catalog to
    // Delta — must match Helpers.buildLocalSession (Phase A parity).

    // ----------------------------------------------------------------
    // 2. Invoke the workload via reflection.
    //    In this child JVM the classloader is the normal app classloader —
    //    no URLClassLoader wrapper — so Spark lambda serialisation works.
    // ----------------------------------------------------------------
    try {
      // Scala objects have a companion class named <ClassName>$ with a
      // static MODULE$ field holding the singleton instance.
      val moduleClass = Class.forName(entryClass + "$")
      val instance    = moduleClass.getField("MODULE$").get(null)

      if (entryMethod == "main") {
        val m = moduleClass.getMethod("main", classOf[Array[String]])
        m.invoke(instance, workloadArgs.asInstanceOf[AnyRef])
      } else {
        // Try run(Array[String]) first (typical ETL pattern)
        val runMethod = try {
          moduleClass.getMethod(entryMethod, classOf[Array[String]])
        } catch {
          case _: NoSuchMethodException =>
            // Fallback: no-arg or SparkSession-first signatures
            try { moduleClass.getMethod(entryMethod) }
            catch { case _: NoSuchMethodException =>
              throw new RuntimeException(
                s"[SubprocessLauncher] No suitable '$entryMethod' on $entryClass")
            }
        }

        if (runMethod.getParameterCount == 1 &&
            runMethod.getParameterTypes()(0).isAssignableFrom(classOf[Array[String]])) {
          runMethod.invoke(instance, workloadArgs.asInstanceOf[AnyRef])
        } else {
          runMethod.invoke(instance)
        }
      }

      System.out.println(s"[SubprocessLauncher] $entryClass.$entryMethod completed successfully")
      // Use Runtime.halt instead of System.exit: halt skips shutdown hooks and
      // forces immediate JVM termination, bypassing SCOS/GRPC channel teardown
      // which otherwise retries for 10-60 min after spark.stop() is called.
      // This cuts subprocess teardown from ~60 min to ~0. (SKILL-FIX)
      Runtime.getRuntime.halt(0)

    } catch {
      case t: Throwable =>
        // Unwrap InvocationTargetException to surface the workload error
        val cause = t match {
          case ite: java.lang.reflect.InvocationTargetException =>
            Option(ite.getCause).getOrElse(ite)
          case other => other
        }
        System.err.println(s"[SubprocessLauncher] $entryClass.$entryMethod failed: ${cause.getMessage}")
        cause.printStackTrace(System.err)
        Runtime.getRuntime.halt(1)
    }
  }
}

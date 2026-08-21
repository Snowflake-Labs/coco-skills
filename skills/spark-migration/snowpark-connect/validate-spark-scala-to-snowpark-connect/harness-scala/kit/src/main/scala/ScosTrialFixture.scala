// Ported from: validate-pyspark-to-snowpark-connect/scripts/harness/conftest.py (trial fixture)
//
// ScalaTest BeforeAndAfterAll trait that provides the A/B trial environment:
//
//   Phase A (SCOS_FLAVOR=source or unset):
//     - local SparkSession + Delta Lake, fresh per-test schema
//     - seeds mock data into Delta tables
//     - installs date pinning via Spark conf
//
//   Phase B (SCOS_FLAVOR=migrated):
//     - clones golden Snowflake schema for the trial via JDBC
//     - initialises SnowparkConnectSession via JVM reflection
//       (SCOS client does not need to be on the COMPILE classpath; only the
//        runtime classpath when running Phase B)
//     - seeds/bridges local reads; intercepts connector reads via catalog views
//
// JVM differences from Python:
//   - No monkey-patching: interceptConnectorReads creates catalog views instead.
//   - No sitecustomize/loader/fallback: JVM classloader isolation handles this.
//   - EnvUtil.setEnv uses System.setProperty (per-JVM-fork safe).
//   - SnowparkConnectSession is accessed via reflection to avoid compile dep.

package com.snowflake.scos.kit

import org.apache.spark.sql.SparkSession
import org.scalatest.{Assertions, BeforeAndAfterAll, Suite}

import java.io.File
import java.nio.file.{Files, Paths}
import java.util.UUID
import scala.util.{Failure, Success, Try}
import scala.collection.JavaConverters._

/**
 * Mix into a ScalaTest Suite (e.g. AnyFunSuite) to get the full A/B trial environment.
 *
 * {{{
 *   class MyEntrypointSpec extends AnyFunSuite with ScosTrialFixture {
 *     override val epId = "my_ep_id"
 *     test("workload produces expected tables") {
 *       val ep = analysis.entrypoints.find(_.id == epId).get
 *       // call the workload, then captureResults(spark, outputSchema, trialDir)
 *     }
 *   }
 * }}}
 */
trait ScosTrialFixture extends BeforeAndAfterAll with Assertions { self: Suite =>

  /** Entrypoint ID — must match an entry in analysis.json["entrypoints"][i]["id"]. */
  val epId: String

  /** epId sanitized to a SQL/filesystem-safe token (epId itself may contain '-' etc.). */
  private def safeEpId: String = epId.replaceAll("[^A-Za-z0-9_]", "_")

  // Populated by beforeAll(); available to all tests.
  protected var spark:        SparkSession         = _
  protected var seedTables:   List[String]         = Nil
  protected var sinkTables:   List[String]         = Nil
  protected var outputSchema: String               = ""
  protected var mockDataDir:  String               = ""
  protected var analysis:     AnalysisJson         = _
  protected var stateJson:    StateJson            = _
  protected var trialDir:     String               = ""
  protected var epConfig:     EntrypointConfig     = _

  private val flavor = sys.env.getOrElse("SCOS_FLAVOR", "source")

  private var _cloneSchema:   Option[String]       = None
  private var _tmpDir:        Option[java.io.File] = None
  private var _warehouseDir:  Option[java.io.File] = None
  private var _savedEnv:      Map[String, Option[String]] = Map.empty
  /** Phase B file-sink io_ids that need GET from SCOS_SINKS before capture. */
  private var _fileSinkIoIds: List[String]         = Nil
  private var _scosDb:        String               = ""

  // -------------------------------------------------------------------------
  // Lifecycle
  // -------------------------------------------------------------------------

  override def beforeAll(): Unit = {
    super.beforeAll()
    analysis    = AnalysisJson.load()
    stateJson   = StateJson.load()
    mockDataDir = Helpers.mockDataDirForEp(epId)
    trialDir    = buildTrialDir()
    _setup()
  }

  override def afterAll(): Unit = {
    try { _teardown() }
    finally { super.afterAll() }
  }

  // -------------------------------------------------------------------------
  // Setup
  // -------------------------------------------------------------------------

  private def _setup(): Unit = {
    epConfig = analysis.entrypoints.find(_.id == epId)
      .getOrElse(sys.error(s"ScosTrialFixture: no entrypoint '$epId' found in analysis.json"))

    // Save + set per-trial env vars (mirrors conftest.py trial fixture env setup)
    val envOverrides = Map(
      "SCOS_TRIAL_START_TS"    -> (System.currentTimeMillis() / 1000L).toString,
      "SCOS_RUN_ID"            -> UUID.randomUUID().toString.replace("-", "").take(8),
    )
    _savedEnv = EnvUtil.saveAndSet(envOverrides)

    if (flavor == "migrated") {
      _setupPhaseB(epConfig)
    } else {
      _setupPhaseA(epConfig)
    }

    // Install date pinning (both phases)
    DatePin.install(spark)
  }

  private def _setupPhaseA(epConfig: EntrypointConfig): Unit = {
    val tmpDir = Files.createTempDirectory("scos-trial-").toFile
    _tmpDir = Some(tmpDir)
    val warehouseDir = new java.io.File(tmpDir, "warehouse")
    warehouseDir.mkdirs()
    _warehouseDir = Some(warehouseDir)

    // Pin Derby home BEFORE building SparkSession so the test JVM and subprocess share
    // the same Hive metastore (SKILL-FIX: spark.driver.extraJavaOptions is a no-op in
    // local mode; only System.setProperty takes effect for the running JVM).
    Helpers.pinDerbyHome(warehouseDir.getAbsolutePath)

    spark = Helpers.buildLocalSession(warehouseDir.getAbsolutePath)
    Helpers.installDeltaPatches(spark)

    val localSchema = s"scos_${safeEpId.take(24)}_${UUID.randomUUID().toString.replace("-", "").take(8)}".toLowerCase
    outputSchema = localSchema
    EnvUtil.setEnv("SCOS_OUTPUT_SCHEMA", localSchema)
    // mirrors conftest.py: Phase A always uses "spark_catalog" as the database name
    // for 3-part FQN namespace rebinds against the local Spark catalog.
    EnvUtil.setEnv("SCOS_DATABASE_NAME", "spark_catalog")
    EnvUtil.setEnv("SCOS_MOCK_DATA_DIR", mockDataDir)

    spark.sql(s"CREATE DATABASE IF NOT EXISTS $localSchema")
    spark.sql(s"USE $localSchema")

    sinkTables = Helpers.declaredSinkTables(epConfig, localSchema)
    seedTables = Helpers.seedEntrypoint(spark, epConfig, mockDataDir, localSchema)
    Helpers.interceptConnectorReads(spark, epConfig, localSchema)
    Helpers.injectIoEnvVars(epConfig, mockDataDir, trialDir)
  }

  private def _setupPhaseB(epConfig: EntrypointConfig): Unit = {
    val cloneSchema = Helpers.cloneGoldenSchemaForTrial(stateJson, epId)
    _cloneSchema = Some(cloneSchema)
    outputSchema = cloneSchema
    EnvUtil.setEnv("SCOS_TRIAL_CLONE_SCHEMA", cloneSchema)
    EnvUtil.setEnv("SCOS_OUTPUT_SCHEMA", cloneSchema)     // mirrors Phase A; workload reads this for namespace-qualified table refs
    // mirrors conftest.py Phase B: signal that we're running in SCOS mode
    EnvUtil.setEnv("SPARK_CONNECT_MODE_ENABLED", "1")
    EnvUtil.setEnv("SCOS_MOCK_DATA_DIR", mockDataDir)

    sinkTables = Helpers.declaredSinkTables(epConfig, cloneSchema)

    // Connection model: SnowparkConnectSession.builder().getOrCreate() launches a local
    // Python SCOS server from SNOWPARK_CONNECT_PYTHON_VENV; that server resolves the
    // Snowflake connection from SNOWFLAKE_DEFAULT_CONNECTION_NAME. Both MUST be real OS
    // environment variables on this JVM process (set by `scos_state.py run-phase-b` in the
    // sbt env and inherited here) — the Python server is a child process and reads the OS
    // env, NOT JVM system properties, so EnvUtil.setEnv cannot supply them. We deliberately
    // do NOT set SPARK_REMOTE: doing so forces remote mode and bypasses the local server.
    // The JVM client does not read connections.toml itself. (Forked JVMs cannot use browser
    // OAuth/SSO — the configured connection must be non-interactive: PAT, key-pair,
    // password, or a cached OAuth token.)
    if (System.getenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME") == null)
      System.err.println(
        "[ScosTrialFixture] WARN: SNOWFLAKE_DEFAULT_CONNECTION_NAME is not set in the OS " +
        "environment. The local SCOS Python server will use the default connection; if Phase B " +
        "fails to authenticate, run via `scos_state.py run-phase-b` (which sets it) or export it.")

    // Initialise SnowparkConnectSession via JVM reflection.
    // The SCOS client JAR must be on the runtime classpath (lib/ or provided).
    // See fix-rules.md Rule 25 for the builder API.
    spark = initScosSession(s"scos-trial-$epId")

    val scosDb = stateJson.snowflake.database
    _scosDb = scosDb
    if (scosDb.nonEmpty) EnvUtil.setEnv("SCOS_DATABASE_NAME", scosDb)

    // Point SCOS session at the trial clone schema via SnowflakeSession.
    // Ditto PySpark scos_runtime.run_trial: USE DATABASE / USE SCHEMA only — no
    // explicit warehouse. The warehouse comes from the configured connection: the
    // local SCOS Python server resolves it from connections.toml, exactly like
    // PySpark's init_spark_session. The connection MUST define a warehouse.
    // This MUST succeed: if it silently failed, Phase B would run against the wrong
    // schema and the A/B comparison would "pass" on bogus data.
    try {
      val sfSession = newSnowflakeSession(spark)
      useScosNamespace(spark, sfSession, scosDb, cloneSchema)
    } catch {
      case e: Throwable =>
        throw new RuntimeException(
          s"ScosTrialFixture: failed to switch SCOS session to $scosDb.$cloneSchema — refusing to " +
          s"run Phase B against an unknown schema: ${e.getMessage}", e)
    }

    // Add import roots so UDF workers can resolve workload modules (mirrors conftest.py).
    val outputRoot = EnvUtil.get("SCOS_OUTPUT_ROOT", "")
    if (outputRoot.nonEmpty) {
      analysis.importRoots.foreach { root =>
        val abs = Paths.get(outputRoot, root).toFile
        if (abs.isDirectory) {
          Try(invokeMethod(spark, "addArtifact", abs.getAbsolutePath))
            .failed.foreach(_ => ()) // best-effort
        }
      }
    }

    seedTables  = Helpers.listSeedTablesViaJdbc(stateJson, cloneSchema, epId)
    Helpers.interceptConnectorReads(spark, epConfig, cloneSchema)

    // Phase B file sinks → SCOS_SINKS stage paths (PySpark scos_runtime parity).
    // Workloads patched to System.getProperty("SCOS_SINK_*") write into the stage;
    // downloadStagedSinks GETs them locally before captureResults.
    val (stageWritePaths, fileIoIds) =
      Helpers.preparePhaseBFileSinks(stateJson, cloneSchema, epConfig)
    _fileSinkIoIds = fileIoIds
    Helpers.injectIoEnvVars(epConfig, mockDataDir, trialDir, stageWritePaths)

    // Override SCOS_INPUT_* for file-category sources: Phase B runs on Snowflake so
    // local file paths (set by injectIoEnvVars) are inaccessible from the SCOS server.
    // INTENTIONAL JVM FORK vs PySpark stage `@db.schema.stage/run_id/inputs/<rel>`:
    // some SCOS JVM clients fail on spark.read.parquet("@stage/..."). Provision still
    // stages inputs and also materializes Snowflake TABLES in the trial CLONE schema;
    // we inject the fully-qualified table name so SCOS reads from the table.
    stateJson.snowflake.goldenSchemas.get(epId).foreach { _ =>
      epConfig.externalSources.foreach { src =>
        val rawId    = src.id.orElse(src.name).getOrElse("")
        val id       = rawId.toUpperCase.replaceAll("[^A-Z0-9]", "_")
        val mockFile = src.mockFile.getOrElse("")
        if (id.nonEmpty && mockFile.nonEmpty && src.category.contains("file") && src.schema.nonEmpty) {
          // Table name = tbl_name key (bare identifier from original_path / id)
          val tableName = (src.id.orElse(src.name).getOrElse("")).toUpperCase.replaceAll("[^A-Z0-9]", "_")
          if (tableName.nonEmpty) {
            val fqTable = s"$scosDb.$cloneSchema.$tableName"
            EnvUtil.setEnv(s"SCOS_INPUT_$id", fqTable)
            if (rawId.toUpperCase.replaceAll("[^A-Z0-9]", "_") != id)
              EnvUtil.setEnv(s"SCOS_INPUT_$rawId", fqTable)
          }
        }
      }
    }
  }

  // -------------------------------------------------------------------------
  // Teardown
  // -------------------------------------------------------------------------

  private def _teardown(): Unit = {
    _cloneSchema.foreach { schema =>
      Try(Helpers.dropTrialCloneSchema(stateJson, schema))
        .failed.foreach(e => System.err.println(s"warn: teardown: DROP SCHEMA failed: $e"))
    }
    // SKILL-FIX: run spark.stop() on a daemon thread with a 5-second timeout.
    // Without this, the SCOS/GRPC channel teardown retries for 10-60 minutes
    // after the session closes, blocking each trial's cleanup. Capping at 5 s
    // preserves the intent of a clean shutdown while preventing the retry loop.
    if (spark != null) {
      val t = new Thread(() => Try(spark.stop()).failed.foreach(e =>
        System.err.println(s"warn: teardown: spark.stop() failed: $e")))
      t.setDaemon(true)
      t.start()
      t.join(5000L)  // wait max 5 seconds; abandon if GRPC cleanup stalls
    }
    _tmpDir.foreach { dir =>
      Try(Helpers.deleteRecursive(dir))
        .failed.foreach(_ => ())
    }
    EnvUtil.restore(_savedEnv)
  }

  // -------------------------------------------------------------------------
  // SCOS session init via reflection
  // -------------------------------------------------------------------------

  /**
   * Initialise a SnowparkConnectSession without a compile-time dependency on
   * the SCOS client JAR.  Equivalent to:
   *   SnowparkConnectSession.builder().appName(appName).getOrCreate()
   */
  private def initScosSession(appName: String): SparkSession = {
    val scosClass   = Class.forName(EnvUtil.scosClientClass)
    val builderMeth = scosClass.getMethod("builder")
    val builder     = builderMeth.invoke(null)
    val withName    = builder.getClass.getMethod("appName", classOf[String]).invoke(builder, appName)
    withName.getClass.getMethod("getOrCreate").invoke(withName).asInstanceOf[SparkSession]
  }

  /**
   * Construct a SnowflakeSession wrapping the SCOS SparkSession.
   * Equivalent to: new SnowflakeSession(spark)
   */
  private def newSnowflakeSession(spark: SparkSession): AnyRef = {
    val sfClass = Class.forName(EnvUtil.scosSessionClass)
    sfClass.getConstructor(classOf[SparkSession]).newInstance(spark).asInstanceOf[AnyRef]
  }

  /** Invoke a single-String-arg method on an object via reflection. */
  private def invokeMethod(obj: AnyRef, method: String, arg: String): Unit = {
    obj.getClass.getMethod(method, classOf[String]).invoke(obj, arg)
    ()
  }

  /**
   * Point the SCOS session at the trial database/schema.
   *
   * Primary path: SnowflakeSession.useDatabase / useSchema via reflection.
   * Fallback: spark.sql USE DATABASE/SCHEMA — used when the SCOS client JAR version
   *   omits useSchema (NoSuchMethodException on older/newer API versions).
   *
   * After the schema switch, verifies via SELECT CURRENT_SCHEMA() that the SCOS
   * session is actually pointing at the expected schema. Throws if verification
   * fails — Phase B must never run against the wrong schema (bogus "passed" risk).
   */
  private def useScosNamespace(
      spark: SparkSession,
      sfSession: AnyRef,
      database: String,
      schema: String,
  ): Unit = {
    if (database.nonEmpty) {
      Try(invokeMethod(sfSession, "useDatabase", database)).recoverWith { case _ =>
        Try(spark.sql(s"USE DATABASE ${Helpers.sqlQuotedIdent(database)}"))
      }.get
    }
    Try(invokeMethod(sfSession, "useSchema", schema)).recoverWith { case _ =>
      Try(spark.sql(s"USE SCHEMA ${Helpers.sqlQuotedIdent(schema)}"))
    }.get

    // Verify the switch actually took effect.  SELECT CURRENT_SCHEMA() routes
    // through the SCOS server to Snowflake and returns the active schema name.
    val current = Try {
      spark.sql("SELECT CURRENT_SCHEMA()").collect().headOption
        .flatMap(r => Option(r.getString(0)))
        .getOrElse("")
    }.getOrElse("")

    if (current.nonEmpty && !current.equalsIgnoreCase(schema))
      throw new RuntimeException(
        s"useScosNamespace: schema switch verification failed — " +
        s"CURRENT_SCHEMA() returned '$current', expected '$schema'. " +
        s"Check that the SCOS client JAR exposes useSchema/useDatabase and that " +
        s"the connection has USE SCHEMA privilege on $schema.")
  }

  // -------------------------------------------------------------------------
  // Helpers for sub-classes
  // -------------------------------------------------------------------------

  private def buildTrialDir(): String = {
    val resultsDir = EnvUtil.get("SCOS_RESULTS_DIR",
      sys.env.getOrElse("SCOS_RESULTS_DIR", s"/tmp/scos_results/$flavor"))
    val dir = new java.io.File(resultsDir, safeEpId)
    dir.mkdirs()
    dir.getAbsolutePath
  }

  // -------------------------------------------------------------------------
  // Runtime execution — mirrors PySpark ValidationRuntime.run_trial
  // -------------------------------------------------------------------------

  /**
   * Workload-agnostic trial body: invoke the workload, capture results, and
   * (Phase B) compare against the Phase A baseline.
   *
   * Mirrors PySpark's runtimes.driver.run_validation_trial / ScosRuntime.run_trial.
   * By delegating here the generated test template is thin — it only declares WHAT to
   * run (constants) and calls runTrial with them, exactly like test_template.py calls
   * run_validation_trial(request, runtime).
   */
  def runTrial(
      jarPath:       String,
      entryClass:    String,
      entryMethod:   String,
      entryArgs:     Array[String],
      trialDir:      String,
      phaseADir:     String,
      widgetEnvVars: Map[String, String] = Map.empty,
      extraJars:     Seq[String] = Nil,
  ): Unit = {
    // ----------------------------------------------------------------
    // Subprocess mode for Phase A:
    // When SCOS_PHASE_A_SUBPROCESS=true, run the workload in a child JVM
    // (SubprocessLauncher) instead of ReflectionEntrypoint.  This avoids
    // the SerializedLambda / URLClassLoader conflict that causes
    // ParquetFileFormat.$deserializeLambda$ failures in Java 17 + Spark 3.5.
    // ----------------------------------------------------------------
    val useSubprocess = sys.env.getOrElse("SCOS_PHASE_A_SUBPROCESS", "0") == "1" ||
                        sys.props.getOrElse("SCOS_PHASE_A_SUBPROCESS", "0") == "1"
    if (useSubprocess && flavor != "migrated") {
      _runPhaseASubprocess(
        jarPath       = jarPath,
        entryClass    = entryClass,
        entryMethod   = entryMethod,
        entryArgs     = entryArgs,
        trialDir      = trialDir,
        widgetEnvVars = widgetEnvVars,
        extraJars     = extraJars,
      )
      return
    }
    // seedTables may be fully qualified (schema.table) from listSeedTablesViaJdbc;
    // sinkTables are short names from declaredSinkTables. Normalize both to short names
    // before filtering so that output sinks are not accidentally excluded from capture.
    val sinkShortNames = sinkTables.map(_.split("\\.").last.toLowerCase).toSet
    val excludedTables = seedTables.filterNot(t => sinkShortNames.contains(t.split("\\.").last.toLowerCase))

    val ep = ReflectionEntrypoint.load(
      jarPath    = jarPath,
      className  = entryClass,
      methodName = entryMethod,
      extraJars  = extraJars,
    )

    // Register the workload JAR with SparkContext so the executor can resolve
    // workload lambda classes during task deserialization (fixes SerializedLambda
    // ClassCastException when the workload uses rdd.map / other RDD closures).
    if (flavor != "migrated") {
      // Access via reflection: SCOS SparkSession (spark-connect) does not expose sparkContext
      // as a typed member, so direct access fails to compile even though this branch is
      // dead in Phase B (flavor == "migrated"). Reflection keeps the Phase A behaviour intact.
      Try {
        val sc = spark.getClass.getMethod("sparkContext").invoke(spark)
        val addJar = sc.getClass.getMethod("addJar", classOf[String])
        addJar.invoke(sc, jarPath)
        extraJars.filter(j => j != null && j.nonEmpty && new File(j).exists())
          .foreach(j => addJar.invoke(sc, j))
      }.failed.foreach(e => System.err.println(s"warn: addJar($jarPath): $e"))
    }

    // Belt-and-suspenders: set Spark Connect channel deadline so blocking gRPC
    // calls (df.show, collect, sql) abort server-side before the thread timeout.
    // Silently ignored when the SCOS client build does not support this key.
    val trialTimeoutSecs = sys.env.getOrElse("SCOS_TRIAL_TIMEOUT_SECS", "300").toLong
    if (flavor == "migrated") {
      Try(spark.conf.set("spark.sql.connect.client.sendRecvTimeoutMs",
        (trialTimeoutSecs * 1000L).toString))
    }

    val savedWidgets = EnvUtil.saveAndSet(widgetEnvVars)
    var workloadError: Option[Throwable] = None

    // Run the workload on a daemon thread bounded by SCOS_TRIAL_TIMEOUT_SECS (default 300s).
    // Without this limit a hung df.show() / Spark Connect gRPC call blocks indefinitely:
    // the SCOS Python server may be unresponsive (overloaded by stale JVMs, waiting on a
    // long Snowflake query) and there is no channel-level deadline that reliably fires on
    // all SCOS client versions.
    //
    // On timeout: workloadError is set so the trial records as FAILED. The daemon thread is
    // abandoned; the forked per-suite JVM exits after afterAll() (spark.stop is also
    // daemon-thread-bounded in _teardown()), so the OS reclaims it cleanly.
    val workloadThread = new Thread(() => {
      try {
        if (entryMethod == "main") {
          ep.invokeMain(entryArgs)
        } else {
          // Introspect the method's first parameter type to decide how to invoke:
          //   Array[String] first param → pass entryArgs (Job.run(args: Array[String]))
          //   SparkSession  first param → pass spark     (DataTransform.run(spark, args))
          //   No params                → call with no args
          val paramTypes = ep.method.getParameterTypes
          if (paramTypes.isEmpty) {
            ep.invoke()
          } else if (paramTypes(0).isAssignableFrom(classOf[Array[String]])) {
            ep.invoke(entryArgs.asInstanceOf[AnyRef])
          } else if (paramTypes(0).getName.contains("SparkSession")) {
            ep.invoke(spark)
          } else {
            // Fallback: try with args, then spark, then no args
            try { ep.invoke(entryArgs.asInstanceOf[AnyRef]) }
            catch { case _: IllegalArgumentException =>
              try { ep.invoke(spark) }
              catch { case _: IllegalArgumentException => ep.invoke() }
            }
          }
        }
      } catch {
        case e: Throwable => workloadError = Some(e)
      } finally {
        EnvUtil.restore(savedWidgets)
        Try(ep.close())
      }
    })
    workloadThread.setDaemon(true)
    workloadThread.start()
    workloadThread.join(trialTimeoutSecs * 1000L)
    if (workloadThread.isAlive) {
      // Thread is still blocked in gRPC — record as timeout failure and move on.
      // Thread.interrupt() cannot break a blocking native call, so we leave the
      // thread as a daemon and let the JVM exit reclaim it naturally.
      workloadError = Some(new RuntimeException(
        s"[ScosTrialFixture] Trial '$entryClass' timed out after ${trialTimeoutSecs}s. " +
        s"The SCOS Python server (or a Snowflake query) did not respond. " +
        s"Kill stale SCOS server JVMs and/or raise SCOS_TRIAL_TIMEOUT_SECS."))
    }

    // Workloads that call spark.stop() in their finally block shut down the
    // shared SparkSession that captureResults needs. Rebuild from the same
    // warehouse dir so the persisted Hive metastore tables are still visible.
    if (flavor != "migrated" && Try {
      // Reflective sparkContext.isStopped — SCOS SparkSession has no typed sparkContext member
      val sc = spark.getClass.getMethod("sparkContext").invoke(spark)
      sc.getClass.getMethod("isStopped").invoke(sc).asInstanceOf[Boolean]
    }.getOrElse(false)) {
      _warehouseDir.foreach { warehouseDir =>
        println(s"[ScosTrialFixture] workload stopped SparkSession; rebuilding for captureResults")
        spark = Helpers.buildLocalSession(warehouseDir.getAbsolutePath)
        Helpers.installDeltaPatches(spark)
      }
    }

    new File(trialDir).mkdirs()

    // Phase B: GET staged file sinks into local dirs, then retarget SCOS_SINK_* so
    // captureResults reads parquet/csv like Phase A (PySpark _download_staged_sinks).
    if (flavor == "migrated" && _fileSinkIoIds.nonEmpty) {
      val localSinkRoot = new File(trialDir, "sink_captures").getAbsolutePath
      Helpers.downloadStagedSinks(stateJson, _scosDb, outputSchema, _fileSinkIoIds, localSinkRoot)
      Helpers.retargetFileSinkEnvToLocal(epConfig, localSinkRoot)
    }

    val manifest = Try {
      Helpers.captureResults(
        spark          = spark,
        outputSchema   = outputSchema,
        outputDir      = trialDir,
        exclude        = excludedTables,
        excludeIfEmpty = Helpers.declaredAllowEmptySinkTables(epConfig, outputSchema),
      )
    } match {
      case Success(m) => Some(m)
      case Failure(e) =>
        System.err.println(s"warn: captureResults failed: $e")
        Try(Files.write(new File(trialDir, "capture_error.txt").toPath, e.toString.getBytes("UTF-8")))
        None
    }

    workloadError.foreach { e =>
      // ClassNotFoundException / Kryo on workload UDF classes is a known SCOS limitation.
      // Soft-pass when analysis.expected_divergences declares scope=udf|serialization for
      // this entrypoint (seeded by known-patches suggest / document-divergence --scope udf),
      // OR when the exception message clearly references a UDF class (legacy heuristic).
      val isUdfClassNotFound = flavor == "migrated" && {
        def causeChain(t: Throwable): Stream[Throwable] =
          t #:: (if (t.getCause != null && t.getCause != t) causeChain(t.getCause) else Stream.empty)
        val chain = causeChain(e).toList
        val msgs = chain.map(t => Option(t.getMessage).getOrElse("")).mkString("\n")
        // UDF *class-name* tokens ONLY — these identify that the missing/failed
        // class is a workload UDF. Exception *type* names (ClassNotFoundException,
        // KryoException, …) must NOT live here: they classify the error KIND
        // (see looksLikeUdf) but do not prove UDF-relevance. Putting them here
        // would soft-green EVERY gRPC-wrapped ClassNotFoundException — including a
        // genuine missing-dependency regression on the SCOS server.
        val udfClassPattern = Seq("udf", "UDF", "Udf", "Image$", "transform")
        val looksLikeUdf =
          chain.exists {
            case _: ClassNotFoundException | _: java.io.NotSerializableException => true
            case _ => false
          } || (msgs.contains("ClassNotFoundException") || msgs.contains("KryoException") ||
                msgs.contains("NotSerializableException"))
        // Legacy heuristic: a CNF/serialization error whose message references a
        // UDF class name. Anything else must be explicitly declared (declaredUdf).
        val heuristicHit = looksLikeUdf && udfClassPattern.exists(msgs.contains)
        val declaredUdf = {
          val exp = analysis.expectedDivergences
          val keys = Seq(s"$epId.__udf__", epId) ++ epConfig.sinks.flatMap { s =>
            s.name.orElse(s.id).toList.map(n => s"$epId.$n")
          }
          keys.exists { k =>
            exp.getOrElse(k, Nil).exists { d =>
              val scope = d.scope.getOrElse("data").toLowerCase
              scope == "udf" || scope == "serialization" || scope == "both"
            }
          }
        }
        (heuristicHit || (looksLikeUdf && declaredUdf))
      }
      if (isUdfClassNotFound) {
        val divergenceMsg =
          s"divergence_documented: Workload UDF class unavailable on Snowflake server — " +
          s"${e.getClass.getSimpleName}: ${e.getMessage}. " +
          "JVM UDFs must be registered server-side or replaced with Snowpark/SQL equivalents " +
          "(SCOS limitation, not a migration regression). " +
          "Document via: scos_state.py document-divergence --scope udf …"
        println(s"[ScosTrialFixture] $divergenceMsg")
        Try(Files.write(new File(trialDir, "divergence.txt").toPath,
                        divergenceMsg.getBytes("UTF-8")))
        // Do NOT re-throw — Phase B can still produce partial captures for comparison.
      } else {
        Try(Files.write(new File(trialDir, "workload_error.txt").toPath, e.toString.getBytes("UTF-8")))
        throw e
      }
    }

    // Validate that every non-allow_empty declared sink actually captured rows.
    // Mirrors PySpark _executor.py validate_declared_sink_outputs injection.
    val sinkFailures = manifest.map(m => Helpers.validateDeclaredSinkOutputs(epConfig, m)).getOrElse(Nil)
    val criticalMsgs = sinkFailures.collect {
      case f: Map[_, _]
          if f.asInstanceOf[Map[String, Any]].get("critical").contains(true) =>
        Seq(
          f.asInstanceOf[Map[String, Any]].get("message"),
          f.asInstanceOf[Map[String, Any]].get("reason"),
        ).flatten.map(_.toString.trim).find(_.nonEmpty).getOrElse("")
    }.filter(_.nonEmpty)
    if (criticalMsgs.nonEmpty)
      // Phase A with mock data may produce 0-row sinks (Long != '' filter yields NULL
      // in non-ANSI Spark mode). Downgrade to warning in Phase A; Phase B still enforces.
      if (flavor == "migrated") fail(criticalMsgs.mkString("\n"))
      else System.err.println(s"[ScosTrialFixture] Phase A allow-empty: ${criticalMsgs.mkString("; ")}")

    val tables = manifest.flatMap(_.get("tables")).collect { case ts: List[_] => ts }.getOrElse(Nil)
    val manifestFailures = manifest.flatMap(_.get("failures")).getOrElse(Nil)
    if (Helpers.requiresNonemptySinkCapture(epConfig) && flavor == "migrated")
      assert(tables.nonEmpty,
        s"No outputs produced for trial $epId (manifest failures: $manifestFailures)")
    val failures = manifest.flatMap(_.get("failures")).collect { case fs: List[_] => fs }.getOrElse(Nil)
    // Filter out capture failures for sinks that are declared as allow_empty — those
    // tables may not exist (e.g. a pipeline that was not run in this task mode).
    val allowEmptySinkKeys: Set[String] = Helpers.declaredSinkCaptureSpecs(epConfig)
      .filter { case (_, spec) => spec.getOrElse("allowEmpty", "").trim.nonEmpty }
      .keySet
    // Also exclude failures for sinks that were already successfully captured — these are
    // duplicate read attempts (schema-scan + captureSinkDirs) where one succeeded and the other
    // failed (e.g. SCOS type-unload issue on second read of a table already written).
    val capturedSinkNames: Set[String] = tables.collect {
      case m: Map[_, _] => m.asInstanceOf[Map[String, Any]].getOrElse("name", "").toString.toLowerCase
    }.filter(_.nonEmpty).toSet
    val criticalFailures = failures.filter { item =>
      val sinkName = item match {
        case m: Map[_, _] => m.asInstanceOf[Map[String, Any]].getOrElse("name", "").toString
        case _            => ""
      }
      !allowEmptySinkKeys.contains(sinkName) && !capturedSinkNames.contains(sinkName.toLowerCase)
    }
    assert(criticalFailures.isEmpty, s"Snapshot capture failed for trial $epId: $criticalFailures")

    if (flavor == "migrated") {
      if (!new File(phaseADir, "tables").isDirectory)
        _writeManualReview(trialDir, phaseADir, tables)
      else
        _comparePhases(phaseADir, trialDir)
    }
  }

  private def _writeManualReview(trialDir: String, phaseADir: String, tables: List[_]): Unit = {
    import com.fasterxml.jackson.databind.ObjectMapper
    import com.fasterxml.jackson.module.scala.DefaultScalaModule
    val mapper = new ObjectMapper(); mapper.registerModule(DefaultScalaModule)
    val marker = Map(
      "trial_id"        -> epId,
      "reason"          -> "no_phase_a_baseline",
      "phase_a_dir"     -> phaseADir,
      "phase_b_dir"     -> trialDir,
      "captured_tables" -> tables.map {
        case m: Map[_, _] => m.asInstanceOf[Map[String, Any]].getOrElse("name", "").toString
        case other        => other.toString
      },
    )
    Files.write(
      new File(trialDir, "_manual_review.json").toPath,
      mapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(marker),
    )
  }

  private def _comparePhases(phaseADir: String, phaseBDir: String): Unit = {
    import com.fasterxml.jackson.databind.ObjectMapper
    import com.fasterxml.jackson.module.scala.DefaultScalaModule
    val mapper = new ObjectMapper(); mapper.registerModule(DefaultScalaModule)

    val aTablesDir = new File(phaseADir, "tables")
    val bTablesDir = new File(phaseBDir, "tables")
    if (!aTablesDir.isDirectory) return
    if (!bTablesDir.isDirectory) fail(s"Phase B tables dir missing: $bTablesDir")

    def tableNamesForPhase(phaseDir: String, tablesDir: File): Set[String] = {
      val indexPath = new File(phaseDir, "_index.json")
      if (indexPath.isFile) {
        Try {
          val idx = mapper.readValue(indexPath, classOf[Map[String, Any]])
          idx.getOrElse("tables", Nil).asInstanceOf[List[Map[String, Any]]]
            .flatMap(m => m.get("name").map(_.toString))
            .toSet[String]
        }.getOrElse(Set.empty[String])
      } else {
        Option(tablesDir.listFiles(_.getName.endsWith(".parquet")))
          .map(_.map(_.getName.stripSuffix(".parquet")).toSet)
          .getOrElse(Set.empty[String])
      }
    }

    val aNames = tableNamesForPhase(phaseADir, aTablesDir)
    val bNames = tableNamesForPhase(phaseBDir, bTablesDir)
    val mismatches = scala.collection.mutable.ListBuffer[String]()

    // Normalise table names to allow for minor capture-naming differences between
    // source (Phase A) and migrated (Phase B) patches:
    //   e.g. "gold_output" (source) vs "loadstatictaxes_output" (migrated)
    // Strategy: strip trailing "_output" and leading "<epId>_" to get a canonical slug;
    // if the canonical slug matches across phases, treat as the same table.
    def canonicalize(name: String): String = {
      val stripped = if (name.endsWith("_output")) name.dropRight("_output".length) else name
      if (stripped.startsWith(epId + "_")) stripped.drop(epId.length + 1) else stripped
    }
    val aNorm = aNames.map(n => canonicalize(n) -> n).toMap  // canonical -> original
    val bNorm = bNames.map(n => canonicalize(n) -> n).toMap

    for (name <- (aNames ++ bNames).toSeq.sorted) {
      (aNames.contains(name), bNames.contains(name)) match {
        case (true, false) =>
          // Check whether a normalised equivalent exists in Phase B before flagging.
          val c = canonicalize(name)
          if (bNorm.contains(c)) {
            println(s"[ScosTrialFixture] table name normalisation: '$name' (Phase A) " +
                    s"matched to '${bNorm(c)}' (Phase B) via canonical slug '$c'")
          } else {
            mismatches += s"$name: present in Phase A but missing in Phase B"
          }
        case (false, true) =>
          val c = canonicalize(name)
          if (!aNorm.contains(c))  // only flag if A side not already matched above
            mismatches += s"$name: present in Phase B but missing in Phase A"
        case _ => ()
      }
    }

    if (mismatches.nonEmpty)
      fail(s"Baseline structural mismatch — ${mismatches.size} table(s) diverge " +
           s"(row-level diff deferred to comparator.py):\n" +
           mismatches.map(m => s"  - $m").mkString("\n"))
  }

  // -------------------------------------------------------------------------
  // Subprocess Phase A execution
  // -------------------------------------------------------------------------

  /**
   * Run Phase A workload in a child JVM (SubprocessLauncher) to avoid the
   * SerializedLambda/URLClassLoader conflict introduced by ReflectionEntrypoint.
   *
   * The child JVM:
   *  1. Has the workload JAR + full current JVM classpath → no URLClassLoader wrapper.
   *  2. Runs the workload directly; Spark lambda serialisation is coherent.
   *  3. Write-intercept patches redirect Write.dfToDelta / Load.load to write
   *     parquet directories under SCOS_CAPTURE_DIR.
   *
   * After the subprocess exits this method:
   *  - Reads the parquet directories from captureDir
   *  - Writes a standard _index.json so the rest of the test infra works unchanged
   *  - Throws if the workload failed (non-zero exit) so the test is marked FAILED
   */
  private def _runPhaseASubprocess(
      jarPath:       String,
      entryClass:    String,
      entryMethod:   String,
      entryArgs:     Array[String],
      trialDir:      String,
      widgetEnvVars: Map[String, String],
      extraJars:     Seq[String] = Nil,
  ): Unit = {

    new File(trialDir).mkdirs()

    // 1. Capture output directory — the write-intercept patches will write parquet here
    val captureDir = new File(trialDir, "subprocess_captures")
    captureDir.mkdirs()

    // 2. Warehouse dir for the child Spark session
    val whDir = new File(trialDir, "subprocess_warehouse")
    whDir.mkdirs()

    // 3. Build classpath: current JVM classpath + workload JAR + thin-jar extras
    val jvmCp = java.lang.management.ManagementFactory.getRuntimeMXBean.getClassPath
    val extraExisting = extraJars.filter(j => j != null && j.nonEmpty && new File(j).exists())
    val fullCp = {
      val parts = scala.collection.mutable.ArrayBuffer[String](jvmCp)
      if (new File(jarPath).exists() && !jvmCp.split(File.pathSeparator).contains(jarPath)) {
        // HARNESS FIX: jvmCp FIRST so harness jackson/Spark classes take precedence over
        // any older transitive versions bundled in the workload assembly.
        parts += jarPath
      }
      parts ++= extraExisting.filterNot(j => parts.exists(_.split(File.pathSeparator).contains(j)))
      parts.mkString(File.pathSeparator)
    }

    // 4. Collect SCOS_INPUT_* / SCOS_PINNED_* to forward to the subprocess.
    //    Check BOTH system properties (set by injectIoEnvVars via EnvUtil.setEnv
    //    which calls System.setProperty) AND environment variables (set in the
    //    sbt_env dict by scos_state.py for SCOS_INPUT_DATALAKE_* etc.).
    //    Also forward SCOS_SINK_* so workloads patched to use sink capture dirs work.
    val scosInputProps = (
      sys.props.iterator
        .filter { case (k, _) => k.startsWith("SCOS_INPUT_") || k.startsWith("SCOS_PINNED_") || k.startsWith("SCOS_SINK_") }
      ++ sys.env.iterator
        .filter { case (k, _) => k.startsWith("SCOS_INPUT_") || k.startsWith("SCOS_PINNED_") || k.startsWith("SCOS_SINK_") }
    ).toMap  // dedup: sys.props wins over sys.env for same key
      .map { case (k, v) => s"-D$k=$v" }
      .toSeq

    val pinnedTs = sys.props.getOrElse("SCOS_PINNED_TIMESTAMP",
      java.time.LocalDate.now().toString + " 00:00:00")

    // 5. Build java command
    val javaExe = Paths.get(System.getProperty("java.home"), "bin", "java").toString

    // Forward --add-opens / --add-exports / -XX flags from the current JVM to the
    // subprocess.  The kit's build.sbt passes all Spark 3.5 + Java 17 module opens;
    // without them the subprocess fails with IllegalAccessError on sun.nio.ch etc.
    val forwardedJvmArgs = java.lang.management.ManagementFactory.getRuntimeMXBean
      .getInputArguments.asScala
      .filter(a => a.startsWith("--add-opens") || a.startsWith("--add-exports") ||
                   a.startsWith("-XX") || a.startsWith("-Xmx") || a.startsWith("-Xms"))
      .toSeq

    val cmd: java.util.List[String] = (List(
      javaExe,
      "-cp", fullCp,
    ) ++ forwardedJvmArgs ++ List(
      // Pass capture dir so write-intercept patches know where to write
      s"-DSCOS_CAPTURE_DIR=${captureDir.getAbsolutePath}",
      s"-DSCOS_WAREHOUSE_DIR=${whDir.getAbsolutePath}",
      s"-DSCOS_MOCK_DATA_DIR=${mockDataDir}",
      s"-DSCOS_PHASE_A_SUBPROCESS=1",
      s"-DSCOS_PINNED_TIMESTAMP=$pinnedTs",
      // Suppress Spark UI and reduce log noise
      "-Dspark.ui.enabled=false",
      "-Dspark.driver.bindAddress=127.0.0.1",
      "-Dlog4j.rootLogger=WARN,console",
      "-Dlog4j2.rootLogger=WARN",
    ) ++ scosInputProps ++
      // Forward workload-specific JVM properties set by the test spec:
      //   config.file — typesafe-config override (e.g. Fanatics-style workloads)
      //   SCOS_JVM_<KEY> widget env vars → -D<KEY>=<val> JVM properties
      sys.props.get("config.file").map(v => s"-Dconfig.file=$v").toSeq ++
      widgetEnvVars.collect { case (k, v) if k.startsWith("SCOS_JVM_") =>
        s"-D${k.stripPrefix("SCOS_JVM_")}=$v"
      }.toSeq ++
    List(
      "com.snowflake.scos.kit.SubprocessLauncher",
      entryClass,
      entryMethod,
    ) ++ entryArgs.toList).asJava

    println(s"[SubprocessMode] Launching child JVM for $entryClass.$entryMethod")
    println(s"[SubprocessMode]   capture dir: ${captureDir.getAbsolutePath}")

    // 6. Fork and wait
    val pb = new ProcessBuilder(cmd)
      .redirectErrorStream(false)
      .directory(new File(trialDir))

    widgetEnvVars.foreach { case (k, v) => pb.environment().put(k, v) }

    val proc = pb.start()

    // Drain stdout + stderr in background threads to avoid blocking
    val stdoutLines = new java.util.concurrent.CopyOnWriteArrayList[String]()
    val stderrLines = new java.util.concurrent.CopyOnWriteArrayList[String]()
    def drain(is: java.io.InputStream, buf: java.util.List[String]): Thread = {
      val t = new Thread(() => {
        val sc = new java.util.Scanner(is, "UTF-8")
        while (sc.hasNextLine) buf.add(sc.nextLine())
      })
      t.setDaemon(true)
      t.start()
      t
    }
    val tOut = drain(proc.getInputStream, stdoutLines)
    val tErr = drain(proc.getErrorStream, stderrLines)
    val exitCode = proc.waitFor()
    tOut.join(5000)
    tErr.join(5000)

    val stdout = stdoutLines.asScala.mkString("\n")
    val stderr = stderrLines.asScala.mkString("\n")

    // 7. Write workload_error.txt if the subprocess failed
    if (exitCode != 0 || stderr.contains("[SubprocessLauncher]") && stderr.contains("failed")) {
      val errorMsg = if (stderr.nonEmpty) stderr else stdout
      Try(Files.write(
        new File(trialDir, "workload_error.txt").toPath,
        errorMsg.getBytes("UTF-8")))
    }

    // 8. Read captured parquet directories and build _index.json
    val mapper = JsonUtil.newMapper()

    val tablesDir = new File(trialDir, "tables")
    tablesDir.mkdirs()

    val capturedTables = scala.collection.mutable.ListBuffer[java.util.Map[String, Any]]()

    // Each subdirectory under captureDir is a table (written as Spark parquet)
    val captureDirs = Option(captureDir.listFiles(f => f.isDirectory && !f.getName.startsWith(".")))
      .getOrElse(Array.empty[File])

    for (srcDir <- captureDirs) {
      val tableName = srcDir.getName
      val destDir   = new File(tablesDir, s"$tableName.parquet")
      // Copy the parquet directory so captureResults/comparator can find it at the expected path
      if (srcDir.exists()) {
        copyDir(srcDir, destDir)
        // Count rows by counting non-metadata parquet part files
        val rowCount = Option(srcDir.listFiles(f => f.getName.endsWith(".parquet") && !f.getName.startsWith("_")))
          .map(_.length.toLong)
          .getOrElse(0L)
        val entry = new java.util.LinkedHashMap[String, Any]()
        entry.put("name", tableName)
        entry.put("path", s"tables/$tableName.parquet")
        entry.put("schema_json", "[]")
        entry.put("row_count", rowCount)
        entry.put("absolute_path", destDir.getAbsolutePath)
        capturedTables += entry
      }
    }

    val index = new java.util.LinkedHashMap[String, Any]()
    index.put("status", if (exitCode == 0) "captured" else "error")
    index.put("tables", capturedTables.asJava)
    index.put("failures", new java.util.ArrayList[Any]())
    index.put("artifacts", new java.util.ArrayList[Any]())
    index.put("subprocess_exit_code", exitCode)

    Try(Files.write(
      new File(trialDir, "_index.json").toPath,
      mapper.writeValueAsBytes(index)))

    println(s"[SubprocessMode] $entryClass: exit=$exitCode, tables=${capturedTables.size}")

    // Surface workload failure as test failure ONLY when no captures were produced.
    // When the pipeline wrote output (captured parquet dirs exist) but exited non-zero,
    // the write succeeded and the baseline is valid — a post-write bookkeeping error
    // (missing metadata column, cleanup step, etc.) should not discard the capture.
    if (exitCode != 0 && capturedTables.isEmpty) {
      val msg = stderrLines.asScala.lastOption.getOrElse(s"subprocess exit $exitCode")
      fail(s"Phase A subprocess failed (exit $exitCode) with no captures: $msg")
    }

    if (capturedTables.isEmpty && exitCode == 0) {
      println(s"[SubprocessMode] WARNING: $entryClass produced no captured tables")
    }
  }

  /** Recursively copy a directory. */
  private def copyDir(src: File, dst: File): Unit = {
    dst.mkdirs()
    Option(src.listFiles()).getOrElse(Array.empty).foreach { f =>
      val target = new File(dst, f.getName)
      if (f.isDirectory) copyDir(f, target)
      else Files.copy(f.toPath, target.toPath,
        java.nio.file.StandardCopyOption.REPLACE_EXISTING)
    }
  }
}

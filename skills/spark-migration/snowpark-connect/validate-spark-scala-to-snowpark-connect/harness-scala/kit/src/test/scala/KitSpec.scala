// Kit-level tests for the SCOS Scala validation harness.
//
// Covers the runtime behaviour the control-plane tests cannot reach:
//   - EnvUtil override/restore semantics (and its documented JVM limitation)
//   - SCOS Phase-B reflection class-name overrides (SCOS_CLIENT_CLASS / SCOS_SESSION_CLASS)
//   - captureResults: excludes seeded inputs + skips allow_empty sinks
//   - declaredAllowEmptySinkTables / requiresNonemptySinkCapture / validateDeclaredSinkOutputs
//
// AST risk signals (sql_calls / udfs / rdd_ops / external_io / unsupported_constructs)
// live in harness-scala/control ScosAnalyze.scala and are covered by
// scripts/tests/test_ast_to_analysis.py + scripts/tests/test_prevalidate.py
// (the control project has no ScalaTest suite of its own).
//
// Runs in the forked test JVM configured in build.sbt (local[1] Spark + Delta + add-opens).

package com.snowflake.scos.kit

import org.scalatest.BeforeAndAfterAll
import org.scalatest.funsuite.AnyFunSuite
import org.scalatest.matchers.should.Matchers

import java.io.File
import java.nio.file.{Files, Path}
import java.util.jar.{JarEntry, JarOutputStream}
import javax.tools.ToolProvider

class KitSpec extends AnyFunSuite with Matchers with BeforeAndAfterAll {

  /** Compile in-memory .java sources (name -> source) with the system javac and
    * package the resulting .class files into a jar under `tmpDir`, for testing
    * ReflectionEntrypoint against real Java bytecode shapes. */
  private def compileJavaSourcesToJar(tmpDir: Path, sources: Map[String, String]): File = {
    val srcDir = tmpDir.resolve("src").toFile
    srcDir.mkdirs()
    val javaFiles = sources.map { case (className, code) =>
      val f = new File(srcDir, s"$className.java")
      Files.write(f.toPath, code.getBytes("UTF-8"))
      f
    }.toArray
    val compiler = ToolProvider.getSystemJavaCompiler
    require(compiler != null, "system Java compiler not available — run tests on a JDK, not a JRE")
    val classesDir = tmpDir.resolve("classes").toFile
    classesDir.mkdirs()
    val rc = compiler.run(
      null, null, null,
      (Seq("-d", classesDir.getAbsolutePath) ++ javaFiles.map(_.getAbsolutePath)): _*
    )
    require(rc == 0, "javac failed compiling ReflectionEntrypoint test fixture sources")

    val jarFile = tmpDir.resolve("fixture.jar").toFile
    val jos = new JarOutputStream(new java.io.FileOutputStream(jarFile))
    try {
      def addDir(base: File, dir: File): Unit =
        dir.listFiles().foreach { f =>
          if (f.isDirectory) addDir(base, f)
          else {
            val relPath = base.toPath.relativize(f.toPath).toString.replace('\\', '/')
            jos.putNextEntry(new JarEntry(relPath))
            Files.copy(f.toPath, jos)
            jos.closeEntry()
          }
        }
      addDir(classesDir, classesDir)
    } finally jos.close()
    jarFile
  }

  private def wipeRecursively(f: File): Unit = {
    if (f.isDirectory) Option(f.listFiles()).foreach(_.foreach(wipeRecursively))
    f.delete()
  }

  // ── EnvUtil ────────────────────────────────────────────────────────────────

  test("EnvUtil.get resolves override map first, then default") {
    EnvUtil.unsetEnv("SCOS_TEST_KEY")
    EnvUtil.get("SCOS_TEST_KEY", "fallback") shouldBe "fallback"
    EnvUtil.setEnv("SCOS_TEST_KEY", "from-override")
    EnvUtil.get("SCOS_TEST_KEY", "fallback") shouldBe "from-override"
    // setEnv mirrors into system properties (the documented in-process channel)
    System.getProperty("SCOS_TEST_KEY") shouldBe "from-override"
    EnvUtil.unsetEnv("SCOS_TEST_KEY")
    EnvUtil.get("SCOS_TEST_KEY", "fallback") shouldBe "fallback"
    System.getProperty("SCOS_TEST_KEY") shouldBe null
  }

  test("EnvUtil.saveAndSet / restore round-trips prior state") {
    EnvUtil.unsetEnv("SCOS_RT_A")
    EnvUtil.setEnv("SCOS_RT_B", "orig-b")
    val saved = EnvUtil.saveAndSet(Map("SCOS_RT_A" -> "new-a", "SCOS_RT_B" -> "new-b"))
    EnvUtil.get("SCOS_RT_A") shouldBe "new-a"
    EnvUtil.get("SCOS_RT_B") shouldBe "new-b"
    EnvUtil.restore(saved)
    // A had no prior value → cleared; B restored to its original value
    EnvUtil.get("SCOS_RT_A", "absent") shouldBe "absent"
    EnvUtil.get("SCOS_RT_B") shouldBe "orig-b"
    EnvUtil.unsetEnv("SCOS_RT_B")
  }

  // ── SCOS class overrides ─────────────────────────────────────────────────────

  test("EnvUtil normalizes stage-path env values to a trailing slash (C11)") {
    // Key named like a stage path → always trailing-slashed.
    EnvUtil.setEnv("FARECARD_STAGE_PATH", "@DB.SCH.STG/run123")
    EnvUtil.get("FARECARD_STAGE_PATH") shouldBe "@DB.SCH.STG/run123/"
    // Already-slashed value is left untouched (idempotent).
    EnvUtil.setEnv("FARECARD_STAGE_PATH", "@DB.SCH.STG/run123/")
    EnvUtil.get("FARECARD_STAGE_PATH") shouldBe "@DB.SCH.STG/run123/"
    // A stage value pointing at a single file is NOT slashed.
    EnvUtil.setEnv("SOME_STAGE_PATH", "@DB.SCH.STG/run/data.parquet")
    EnvUtil.get("SOME_STAGE_PATH") shouldBe "@DB.SCH.STG/run/data.parquet"
    // A non-stage value with an ordinary key is untouched.
    EnvUtil.setEnv("SCOS_OUTPUT_SCHEMA", "scos_abc_1234")
    EnvUtil.get("SCOS_OUTPUT_SCHEMA") shouldBe "scos_abc_1234"
    Seq("FARECARD_STAGE_PATH", "SOME_STAGE_PATH", "SCOS_OUTPUT_SCHEMA").foreach(EnvUtil.unsetEnv)
  }

  test("SCOS reflection class names honor SCOS_CLIENT_CLASS / SCOS_SESSION_CLASS overrides") {
    EnvUtil.unsetEnv("SCOS_CLIENT_CLASS")
    EnvUtil.unsetEnv("SCOS_SESSION_CLASS")
    EnvUtil.scosClientClass  shouldBe "com.snowflake.snowpark_connect.client.SnowparkConnectSession"
    EnvUtil.scosSessionClass shouldBe "com.snowflake.snowpark_connect.client.SnowflakeSession"

    EnvUtil.setEnv("SCOS_CLIENT_CLASS", "com.example.RenamedClient")
    EnvUtil.setEnv("SCOS_SESSION_CLASS", "com.example.RenamedSession")
    EnvUtil.scosClientClass  shouldBe "com.example.RenamedClient"
    EnvUtil.scosSessionClass shouldBe "com.example.RenamedSession"

    EnvUtil.unsetEnv("SCOS_CLIENT_CLASS")
    EnvUtil.unsetEnv("SCOS_SESSION_CLASS")
  }

  // ── bareTableName ─────────────────────────────────────────────────────────────

  test("bareTableName strips paths/qualifiers down to the table name") {
    Helpers.bareTableName("db.schema.orders")   shouldBe "orders"
    Helpers.bareTableName("s3://bucket/orders")  shouldBe "orders"
    Helpers.bareTableName("orders")              shouldBe "orders"
  }

  test("trySafeIdent and sqlQuotedIdent handle hyphenated namespace tokens") {
    Helpers.trySafeIdent("ops")           shouldBe Some("ops")
    Helpers.trySafeIdent("my-schema")     shouldBe None
    Helpers.sqlQuotedIdent("ops")         shouldBe "ops"
    Helpers.sqlQuotedIdent("my-schema")   shouldBe "\"my-schema\""
  }

  test("warehouseDirFile normalizes file:// warehouse URIs") {
    val f = Helpers.warehouseDirFile("file:///tmp/warehouse")
    f.getAbsolutePath should endWith("/tmp/warehouse")
  }

  // ── captureResults (local Spark) ──────────────────────────────────────────────

  private var spark: org.apache.spark.sql.SparkSession = _
  private var warehouse: File = _

  override def beforeAll(): Unit = {
    warehouse = Files.createTempDirectory("scos-kit-test-").toFile
    spark = Helpers.buildLocalSession(new File(warehouse, "warehouse").getAbsolutePath)
    Helpers.installDeltaPatches(spark)
  }

  override def afterAll(): Unit = {
    if (spark != null) spark.stop()
  }

  test("captureResults excludes seeded inputs and skips empty declared sinks") {
    import org.apache.spark.sql.Row
    import org.apache.spark.sql.types._
    val idName = StructType(Seq(StructField("id", IntegerType), StructField("name", StringType)))
    val schema = "captest"
    spark.sql(s"CREATE DATABASE IF NOT EXISTS $schema")

    // Use createDataFrame (not Seq.toDF) to avoid Scala 2.12 encoder lambdas that fail
    // in Java 17 with too-many-arguments in LambdaMetafactory.altMetafactory.
    spark.createDataFrame(java.util.Arrays.asList(Row(1, "a"), Row(2, "b")), idName)
      .write.mode("overwrite").saveAsTable(s"$schema.seed_in")
    spark.createDataFrame(java.util.Arrays.asList[Row](), idName)
      .write.mode("overwrite").saveAsTable(s"$schema.empty_sink")
    spark.createDataFrame(java.util.Arrays.asList(Row(10, "x")), idName)
      .write.mode("overwrite").saveAsTable(s"$schema.out_real")

    val outDir = new File(warehouse, "results/phase_a/ep1")
    outDir.mkdirs()

    val manifest = Helpers.captureResults(
      spark          = spark,
      outputSchema   = schema,
      outputDir      = outDir.getAbsolutePath,
      exclude        = Seq("seed_in"),
      excludeIfEmpty = Seq("empty_sink"),
    )

    val captured = manifest.get("tables").collect { case ts: List[_] => ts }.getOrElse(Nil)
    val names = captured.collect { case m: Map[_, _] => m.asInstanceOf[Map[String, Any]]("name").toString }.toSet

    names shouldBe Set("out_real")
    names should not contain "seed_in"
    names should not contain "empty_sink"

    // _index.json manifest must be written next to the captured tables.
    new File(outDir, "_index.json").isFile shouldBe true
  }

  // ── allow_empty sink helpers ─────────────────────────────────────────────────

  test("declaredAllowEmptySinkTables returns only sinks with allowEmpty set") {
    val ep = EntrypointConfig(
      id = "ep1",
      sinks = List(
        SinkConfig(id = Some("orders"), name = Some("orders"),
          kind = Some("table"), allowEmpty = None),
        SinkConfig(id = Some("audit"), name = Some("audit"),
          kind = Some("table"), allowEmpty = Some("incremental no-op is valid")),
      ),
    )
    Helpers.declaredAllowEmptySinkTables(ep, "OUT") shouldBe List("out.audit")
  }

  test("requiresNonemptySinkCapture is true when any non-allow_empty sink exists") {
    val epMixed = EntrypointConfig(
      id = "ep2",
      sinks = List(
        SinkConfig(id = Some("t1"), name = Some("t1"), kind = Some("table")),
        SinkConfig(id = Some("t2"), name = Some("t2"), kind = Some("table"), allowEmpty = Some("empty ok")),
      ),
    )
    Helpers.requiresNonemptySinkCapture(epMixed) shouldBe true

    val epAllEmpty = EntrypointConfig(
      id = "ep3",
      sinks = List(
        SinkConfig(id = Some("t3"), name = Some("t3"), kind = Some("table"), allowEmpty = Some("intentional")),
      ),
    )
    Helpers.requiresNonemptySinkCapture(epAllEmpty) shouldBe false

    val epNoSinks = EntrypointConfig(id = "ep4")
    Helpers.requiresNonemptySinkCapture(epNoSinks) shouldBe false
  }

  test("validateDeclaredSinkOutputs fails when a non-allow_empty sink has 0 rows") {
    val ep = EntrypointConfig(
      id = "ep5",
      sinks = List(
        SinkConfig(id = Some("out_tbl"), name = Some("out_tbl"), kind = Some("table")),
      ),
    )
    val manifest: Map[String, Any] = Map(
      "tables" -> List(Map[String, Any]("name" -> "out_tbl", "row_count" -> 0L)),
    )
    val failures = Helpers.validateDeclaredSinkOutputs(ep, manifest)
    failures should have size 1
    failures.head.get("critical") shouldBe Some(true)
    failures.head.get("reason") shouldBe Some("empty_declared_sink")
  }

  test("validateDeclaredSinkOutputs passes when allow_empty sink has 0 rows") {
    val ep = EntrypointConfig(
      id = "ep6",
      sinks = List(
        SinkConfig(id = Some("summary"), name = Some("summary"),
          kind = Some("table"), allowEmpty = Some("empty when no data")),
      ),
    )
    val manifest: Map[String, Any] = Map(
      "tables" -> List(Map[String, Any]("name" -> "summary", "row_count" -> 0L)),
    )
    Helpers.validateDeclaredSinkOutputs(ep, manifest) shouldBe empty
  }

  test("validateDeclaredSinkOutputs fails when declared sink is absent from manifest") {
    val ep = EntrypointConfig(
      id = "ep7",
      sinks = List(
        SinkConfig(id = Some("missing_sink"), name = Some("missing_sink"), kind = Some("table")),
      ),
    )
    val manifest: Map[String, Any] = Map("tables" -> List.empty[Map[String, Any]])
    val failures = Helpers.validateDeclaredSinkOutputs(ep, manifest)
    failures should have size 1
    failures.head.get("critical") shouldBe Some(true)
  }

  test("validateDeclaredSinkOutputs passes when sink has rows") {
    val ep = EntrypointConfig(
      id = "ep8",
      sinks = List(
        SinkConfig(id = Some("result"), name = Some("result"), kind = Some("table")),
      ),
    )
    val manifest: Map[String, Any] = Map(
      "tables" -> List(Map[String, Any]("name" -> "result", "row_count" -> 42L)),
    )
    Helpers.validateDeclaredSinkOutputs(ep, manifest) shouldBe empty
  }

  // ── AnalysisJson.loadFromSchemas (SCOS_SCHEMAS_DIR SoT) ───────────────────────

  test("AnalysisJson.loadFromSchemas assembles sources/sinks from schemas/") {
    val tmp = Files.createTempDirectory("scos-schemas-")
    try {
      val schemas = new File(tmp.toFile, "schemas")
      val epDir = new File(new File(schemas, "entrypoints"), "ep_demo")
      val tables = new File(epDir, "tables")
      tables.mkdirs()
      Files.writeString(
        new File(schemas, "manifest.json").toPath,
        """{"entrypoints":[{"id":"ep_demo","path":"Demo.scala","dir":"entrypoints/ep_demo"}],
           "expected_divergences":{"ep_demo.__udf__":[{"column":"UDF","scope":"udf","reason":"test"}]}}""",
      )
      Files.writeString(
        new File(epDir, "_meta.json").toPath,
        """{"id":"ep_demo","entrypoint_class":"com.example.Demo$","import_roots":["src/main/scala"],"path_redirects":{"s3://in":"/mock/in"}}""",
      )
      Files.writeString(
        new File(tables, "orders.json").toPath,
        """{"_table_key":"orders","access":"read","category":"file","original_path":"/data/orders","mock_file":"orders.parquet","columns":[{"name":"id","type":"long"},{"name":"amt","type":"decimal(10,2)"}]}""",
      )
      Files.writeString(
        new File(tables, "out.json").toPath,
        """{"_table_key":"out","access":"write","category":"table","original_path":"db.sch.out","allow_empty":false,"columns":[{"name":"id","type":"long"}]}""",
      )

      val loaded = AnalysisJson.loadFromSchemas(schemas.getAbsolutePath)
      loaded.entrypoints.map(_.id) shouldBe List("ep_demo")
      loaded.entrypoints.head.entrypointCallable shouldBe Some("com.example.Demo$")
      loaded.entrypoints.head.pathRedirects.get("s3://in") shouldBe Some("/mock/in")
      loaded.externalSources.map(_.id) shouldBe List(Some("orders"))
      loaded.externalSources.head.schema.map(_.name) shouldBe List("id", "amt")
      loaded.sinks.map(_.id) shouldBe List(Some("out"))
      loaded.sinks.head.allowEmpty shouldBe Some("false")
      loaded.expectedDivergences.get("ep_demo.__udf__").flatMap(_.head.column) shouldBe Some("UDF")

      // load() prefers SCOS_SCHEMAS_DIR over missing analysis.json
      EnvUtil.unsetEnv("SCOS_ANALYSIS_JSON")
      EnvUtil.setEnv("SCOS_SCHEMAS_DIR", schemas.getAbsolutePath)
      val viaEnv = AnalysisJson.load()
      viaEnv.entrypoints.head.id shouldBe "ep_demo"
      EnvUtil.unsetEnv("SCOS_SCHEMAS_DIR")
    } finally {
      def wipe(f: File): Unit = {
        if (f.isDirectory) Option(f.listFiles()).foreach(_.foreach(wipe))
        f.delete()
      }
      wipe(tmp.toFile)
    }
  }

  // ── resolveSparkType: decimal fidelity ───────────────────────────────────────

  test("resolveSparkType preserves decimal/numeric precision and scale") {
    import org.apache.spark.sql.types._
    Helpers.resolveSparkType("decimal(18,4)")  shouldBe DecimalType(18, 4)
    Helpers.resolveSparkType("DECIMAL(10, 2)") shouldBe DecimalType(10, 2)
    Helpers.resolveSparkType("numeric(38,0)")  shouldBe DecimalType(38, 0)
    Helpers.resolveSparkType("decimal")        shouldBe DecimalType(38, 18) // unparametrized default
    Helpers.resolveSparkType("DecimalType(9,3)") shouldBe DecimalType(9, 3) // class-name form
    // sanity: non-decimal types still resolve
    Helpers.resolveSparkType("long")           shouldBe LongType
    Helpers.resolveSparkType("string")         shouldBe StringType
  }

  // ── ReflectionEntrypoint: Java entrypoint shapes ─────────────────────────────
  // Regression coverage for the Java validator's dominant entrypoint shape
  // (`public static void main(String[])`), which the reflective loader mishandled
  // before it resolved the method's static-ness before deciding whether an
  // instance was needed at all.

  test("ReflectionEntrypoint resolves a static Java main with no instantiation, " +
       "and invokeMain works when the instance is null") {
    val tmp = Files.createTempDirectory("scos-reflection-static-main")
    val outFile = tmp.resolve("out.txt").toFile
    try {
      val jar = compileJavaSourcesToJar(tmp, Map(
        // No explicit constructor is declared; a static entrypoint must never
        // try to instantiate this class at all.
        "StaticMainJob" ->
          """public class StaticMainJob {
            |    public static void main(String[] args) throws Exception {
            |        java.nio.file.Files.write(
            |            java.nio.file.Paths.get(args[0]),
            |            ("ran-with-" + args.length + "-args").getBytes());
            |    }
            |}
            |""".stripMargin
      ))
      val ep = ReflectionEntrypoint.load(jar.getAbsolutePath, "StaticMainJob", methodName = "main")
      try {
        ep.instance shouldBe null
        java.lang.reflect.Modifier.isStatic(ep.method.getModifiers) shouldBe true
        ep.invokeMain(Array(outFile.getAbsolutePath, "extra"))
        new String(Files.readAllBytes(outFile.toPath), "UTF-8") shouldBe "ran-with-2-args"
      } finally ep.close()
    } finally wipeRecursively(tmp.toFile)
  }

  test("ReflectionEntrypoint instantiates a class with a private constructor " +
       "for a non-static entrypoint method") {
    val tmp = Files.createTempDirectory("scos-reflection-private-ctor")
    val outFile = tmp.resolve("out.txt").toFile
    try {
      val jar = compileJavaSourcesToJar(tmp, Map(
        "PrivateCtorJob" ->
          """public class PrivateCtorJob {
            |    private PrivateCtorJob() {}
            |    public void run(String[] args) throws Exception {
            |        java.nio.file.Files.write(
            |            java.nio.file.Paths.get(args[0]), "instance-ran".getBytes());
            |    }
            |}
            |""".stripMargin
      ))
      // Before the fix this threw IllegalAccessException: the constructor was
      // never made accessible before newInstance().
      val ep = ReflectionEntrypoint.load(jar.getAbsolutePath, "PrivateCtorJob", methodName = "run")
      try {
        ep.instance should not be null
        java.lang.reflect.Modifier.isStatic(ep.method.getModifiers) shouldBe false
        ep.invoke(Array(outFile.getAbsolutePath): Array[String])
        new String(Files.readAllBytes(outFile.toPath), "UTF-8") shouldBe "instance-ran"
      } finally ep.close()
    } finally wipeRecursively(tmp.toFile)
  }

  test("ReflectionEntrypoint still resolves a Scala-object-shaped MODULE$ singleton " +
       "for a non-static entrypoint (no regression from resolving method before instance)") {
    val tmp = Files.createTempDirectory("scos-reflection-module-shape")
    val outFile = tmp.resolve("out.txt").toFile
    try {
      val jar = compileJavaSourcesToJar(tmp, Map(
        // Mimics the bytecode shape scalac emits for `object Foo`: a public
        // static final MODULE$ field, a private constructor, and instance methods.
        "ScalaLikeModule" ->
          """public class ScalaLikeModule {
            |    public static final ScalaLikeModule MODULE$ = new ScalaLikeModule();
            |    private ScalaLikeModule() {}
            |    public void run(String[] args) throws Exception {
            |        java.nio.file.Files.write(
            |            java.nio.file.Paths.get(args[0]), "module-ran".getBytes());
            |    }
            |}
            |""".stripMargin
      ))
      val ep = ReflectionEntrypoint.load(jar.getAbsolutePath, "ScalaLikeModule", methodName = "run")
      try {
        ep.instance should not be null
        ep.instance.getClass.getField("MODULE$").get(null) shouldBe theSameInstanceAs(ep.instance)
        ep.invoke(Array(outFile.getAbsolutePath): Array[String])
        new String(Files.readAllBytes(outFile.toPath), "UTF-8") shouldBe "module-ran"
      } finally ep.close()
    } finally wipeRecursively(tmp.toFile)
  }

}

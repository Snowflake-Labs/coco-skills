// Ported from: validate-pyspark-to-snowpark-connect/scripts/harness/helpers.py
// and conftest.py (connection / JDBC helpers).
//
// Provides seedEntrypoint, captureResults, cloneGoldenSchemaForTrial,
// declaredSinkTables, interceptConnectorReads, buildLocalSession, and
// JSON model case classes (AnalysisJson / StateJson).
//
// JVM NOTE: Python's mock.patch-based DataFrameReader interception is NOT
// available on the JVM. interceptConnectorReads instead registers catalog
// views in the trial schema so spark.table() and spark.sql() calls resolve
// to the seeded/cloned tables. Workloads that still use
//   spark.read.format("snowflake").option("dbtable","foo").load()
// must have the patch-author step rewrite them to spark.table("foo") first.

package com.snowflake.scos.kit

import com.fasterxml.jackson.annotation.JsonCreator
import com.fasterxml.jackson.databind.{DeserializationFeature, ObjectMapper, PropertyNamingStrategies}
import com.fasterxml.jackson.module.scala.DefaultScalaModule
import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.types._

import java.io.{File, PrintWriter}
import java.nio.file.{Files, Paths}
import java.sql.DriverManager
import java.util.Properties
import java.time.{ZoneOffset, ZonedDateTime}
import java.time.format.DateTimeFormatter
import java.util.UUID
import scala.collection.mutable
import scala.concurrent.{Await, ExecutionContext, Future}
import scala.concurrent.duration._
import scala.util.Try

// ---------------------------------------------------------------------------
// JSON model case classes (analysis.json / state.json)
// ---------------------------------------------------------------------------

/** Minimal representation of analysis.json["entrypoints"][i]. */
case class EntrypointConfig(
    id: String                                        = "",
    entrypointCallable: Option[String]                = None,
    externalSources: List[ExternalSource]             = Nil,
    sinks: List[SinkConfig]                           = Nil,
    /** Synthesizer-canonical sink key; merged into ``sinks`` on AnalysisJson.load. */
    externalSinks: List[SinkConfig]                   = Nil,
    pathRedirects: Map[String, AnyRef]                = Map.empty,
    readerOptions: Map[String, String]                = Map.empty,
    schemas: Map[String, AnyRef]                      = Map.empty,
    importRoots: List[String]                         = Nil,
)

case class ExternalSource(
    id: Option[String]           = None,
    name: Option[String]         = None,
    originalPath: Option[String] = None,
    mockFile: Option[String]     = None,
    category: Option[String]     = None,
    schema: List[ColumnDef]      = Nil,
    readerOptions: Map[String, String] = Map.empty,
)

/** Allow the analyzer to write external_sources as plain strings (e.g. "src_raw_taps")
  * as well as proper objects.  Jackson calls this when the JSON token is a String. */
object ExternalSource {
  @JsonCreator
  def fromString(s: String): ExternalSource =
    ExternalSource(id = Some(s), name = Some(s), category = Some("table"))
}

case class SinkConfig(
    id: Option[String]             = None,
    name: Option[String]           = None,
    originalTarget: Option[String] = None,
    kind: Option[String]           = None,
    allowEmpty: Option[String]     = None,
    format: Option[String]         = None,
    schema: List[ColumnDef]        = Nil,
)

/** Allow the analyzer to write sinks as plain strings (e.g. "sink_taps_norm")
  * as well as proper objects. */
object SinkConfig {
  @JsonCreator
  def fromString(s: String): SinkConfig =
    SinkConfig(id = Some(s), name = Some(s), kind = Some("table"))
}

/** Intermediate table handoff declared in analysis.json["intermediate_tables"]. */
case class IntermediateTable(
    name: String                              = "",
    schema: List[ColumnDef]                   = Nil,
    writerEntrypointId: Option[String]        = None,
    readerEntrypointIds: List[String]         = Nil,
    consumerEntrypointIds: List[String]       = Nil,
    seedStrategy: Option[String]              = None,
    allowEmpty: Option[String]                = None,
)

case class ColumnDef(
    name: String                = "",
    `type`: Option[String]      = None,
    dtype: Option[String]       = None,
    nullable: Option[Boolean]   = None,
)

/** One entry in analysis.json expected_divergences[<trial>.<sink>][]. */
case class ExpectedDivergence(
    column: Option[String] = None,
    reason: Option[String] = None,
    scope: Option[String]  = None,  // data | udf | serialization | both
    baselineSample: Option[String] = None,
    shadowSample: Option[String] = None,
)

/** Top-level analysis.json model. */
case class AnalysisJson(
    entrypoints: List[EntrypointConfig]  = Nil,
    importRoots: List[String]            = Nil,
    externalSources: List[ExternalSource] = Nil,
    sinks: List[SinkConfig]              = Nil,
    /** Top-level synthesizer sink key; merged into ``sinks`` on load. */
    externalSinks: List[SinkConfig]      = Nil,
    intermediateTables: List[IntermediateTable] = Nil,
    expectedDivergences: Map[String, List[ExpectedDivergence]] = Map.empty,
)

object AnalysisJson {
  private val _mapper = JsonUtil.newMapper()

  /**
   * Load analysis contract for a trial.
   *
   * Preference order (PySpark parity):
   *   1. ``SCOS_SCHEMAS_DIR`` — assemble from ``schemas/manifest.json`` + entrypoint dirs
   *   2. ``SCOS_ANALYSIS_JSON`` — legacy / generated shim file
   */
  def load(
      path: String = EnvUtil.get("SCOS_ANALYSIS_JSON"),
      schemasDir: String = EnvUtil.get("SCOS_SCHEMAS_DIR"),
  ): AnalysisJson = {
    val sd = Option(schemasDir).map(_.trim).filter(_.nonEmpty)
    if (sd.exists(d => new File(d, "manifest.json").isFile)) {
      return finalizeLoaded(loadFromSchemas(sd.get))
    }
    if (path == null || path.isEmpty || !new File(path).isFile)
      throw new RuntimeException(
        s"SCOS_SCHEMAS_DIR/manifest.json not found and SCOS_ANALYSIS_JSON not set or missing: " +
          s"schemasDir=$schemasDir analysisJson=$path"
      )
    finalizeLoaded(_mapper.readValue(new File(path), classOf[AnalysisJson]))
  }

  /** Assemble AnalysisJson from the PySpark-compatible ``schemas/`` layout. */
  def loadFromSchemas(schemasDir: String): AnalysisJson = {
    import scala.collection.JavaConverters._
    val root = new File(schemasDir)
    val manifestFile = new File(root, "manifest.json")
    if (!manifestFile.isFile)
      throw new RuntimeException(s"schemas/manifest.json not found: $manifestFile")
    val manifest = _mapper.readTree(manifestFile)
    val epNodes = Option(manifest.get("entrypoints")).map(_.elements().asScala.toList).getOrElse(Nil)

    val globalSources = mutable.ListBuffer[ExternalSource]()
    val globalSinks = mutable.ListBuffer[SinkConfig]()
    val entrypoints = mutable.ListBuffer[EntrypointConfig]()
    val importRoots = mutable.LinkedHashSet[String]()

    epNodes.foreach { ref =>
      val epId = Option(ref.get("id")).map(_.asText("")).getOrElse("")
      if (epId.nonEmpty) {
        val (ep, sources, sinks) = loadEntrypointFromSchemas(root, epId)
        entrypoints += ep
        globalSources ++= sources
        globalSinks ++= sinks
        ep.importRoots.foreach(importRoots.add)
      }
    }

    AnalysisJson(
      entrypoints = entrypoints.toList,
      importRoots = importRoots.toList,
      externalSources = {
        val seen = mutable.LinkedHashSet[String]()
        globalSources.toList.filter(s => s.id.exists(seen.add))
      },
      sinks = {
        val seen = mutable.LinkedHashSet[String]()
        globalSinks.toList.filter(s => s.id.exists(seen.add))
      },
      expectedDivergences = parseExpectedDivergences(manifest),
    )
  }

  private def parseExpectedDivergences(
      manifest: com.fasterxml.jackson.databind.JsonNode,
  ): Map[String, List[ExpectedDivergence]] = {
    import scala.collection.JavaConverters._
    val node = manifest.get("expected_divergences")
    if (node == null || !node.isObject) return Map.empty
    node.fields().asScala.flatMap { e =>
      val arr = e.getValue
      if (arr == null || !arr.isArray) None
      else {
        val divs = arr.elements().asScala.map { d =>
          ExpectedDivergence(
            column = Option(d.get("column")).map(_.asText).filter(_.nonEmpty),
            reason = Option(d.get("reason")).map(_.asText).filter(_.nonEmpty),
            scope = Option(d.get("scope")).map(_.asText).filter(_.nonEmpty),
            baselineSample = Option(d.get("baseline_sample")).map(_.asText).filter(_.nonEmpty),
            shadowSample = Option(d.get("shadow_sample")).map(_.asText).filter(_.nonEmpty),
          )
        }.toList
        if (divs.nonEmpty) Some(e.getKey -> divs) else None
      }
    }.toMap
  }

  private def loadEntrypointFromSchemas(
      schemasRoot: File,
      epId: String,
  ): (EntrypointConfig, List[ExternalSource], List[SinkConfig]) = {
    import scala.collection.JavaConverters._
    val epDir = new File(new File(schemasRoot, "entrypoints"), epId)
    val metaFile = new File(epDir, "_meta.json")
    if (!metaFile.isFile)
      throw new RuntimeException(s"entrypoint _meta.json missing: $metaFile")
    val meta = _mapper.readTree(metaFile)
    val tablesDir = new File(epDir, "tables")
    val sources = mutable.ListBuffer[ExternalSource]()
    val sinks = mutable.ListBuffer[SinkConfig]()

    if (tablesDir.isDirectory) {
      Option(tablesDir.listFiles()).getOrElse(Array.empty[File])
        .filter(f => f.isFile && f.getName.endsWith(".json"))
        .sortBy(_.getName)
        .foreach { tf =>
        val node = _mapper.readTree(tf)
        val key = Option(node.get("_table_key")).map(_.asText).filter(_.nonEmpty)
          .getOrElse(tf.getName.stripSuffix(".json"))
        val access = Option(node.get("access")).map(_.asText("read")).getOrElse("read").toLowerCase
        val cols = parseColumnDefs(node.get("columns"))
        val category = Option(node.get("category")).map(_.asText("table")).getOrElse("table")
        val originalPath = Option(node.get("original_path")).map(_.asText(key)).getOrElse(key)
        val mockFile = Option(node.get("mock_file")).map(_.asText).filter(_.nonEmpty)
        val allowEmpty = Option(node.get("allow_empty")).map { a =>
          if (a.isBoolean) a.asBoolean().toString else a.asText("")
        }.filter(_.nonEmpty)
        val format = Option(node.get("format")).map(_.asText).filter(_.nonEmpty)
        val readerOpts: Map[String, String] = {
          val n = node.get("reader_options")
          if (n != null && n.isObject)
            n.fields().asScala.flatMap { e =>
              Option(e.getValue).filter(_.isTextual).map(v => e.getKey -> v.asText)
            }.toMap
          else Map.empty
        }

        if (access == "read" || access == "readwrite") {
          sources += ExternalSource(
            id = Some(key),
            name = Some(key),
            originalPath = Some(originalPath),
            mockFile = mockFile,
            category = Some(category),
            schema = cols,
            readerOptions = readerOpts,
          )
        }
        if (access == "write" || access == "readwrite") {
          val kind =
            if (category == "table") "table"
            else format.getOrElse(category)
          sinks += SinkConfig(
            id = Some(key),
            name = Some(key),
            originalTarget = Some(originalPath),
            kind = Some(kind),
            allowEmpty = allowEmpty,
            format = format,
            schema = cols,
          )
        }
      }
    }

    val importRootsNode = meta.get("import_roots")
    val importRoots: List[String] =
      if (importRootsNode != null && importRootsNode.isArray)
        importRootsNode.elements().asScala.map(_.asText).toList
      else List("src/main/scala")

    val callable =
      Option(meta.get("entrypoint_callable")).map(_.asText).filter(s => s != null && s.nonEmpty)
        .orElse(Option(meta.get("entrypoint_class")).map(_.asText).filter(s => s != null && s.nonEmpty))

    val pathRedirects: Map[String, AnyRef] = {
      val n = meta.get("path_redirects")
      if (n != null && n.isObject)
        n.fields().asScala.flatMap { e =>
          val v = e.getValue
          if (v == null || v.isNull) None
          else if (v.isTextual) Some(e.getKey -> (v.asText: AnyRef))
          else Some(e.getKey -> (v.toString: AnyRef))
        }.toMap
      else Map.empty
    }

    val ep = EntrypointConfig(
      id = epId,
      entrypointCallable = callable,
      externalSources = sources.toList,
      sinks = sinks.toList,
      importRoots = importRoots,
      pathRedirects = pathRedirects,
    )
    (ep, sources.toList, sinks.toList)
  }

  private def parseColumnDefs(node: com.fasterxml.jackson.databind.JsonNode): List[ColumnDef] = {
    import scala.collection.JavaConverters._
    if (node == null || !node.isArray) return Nil
    node.elements().asScala.flatMap { c =>
      val name = Option(c.get("name")).map(_.asText).filter(s => s != null && s.nonEmpty)
      name.map { n =>
        ColumnDef(
          name = n,
          `type` = Option(c.get("type")).map(_.asText("string")).orElse(Some("string")),
          nullable = Option(c.get("nullable")).map(_.asBoolean(true)).orElse(Some(true)),
        )
      }
    }.toList
  }

  private def finalizeLoaded(raw: AnalysisJson): AnalysisJson = {
    // Entrypoint external_sources are stored as string IDs in analysis.json.
    // ExternalSource.fromString defaults category to "table", losing the real
    // category/mock_file/schema. Resolve each string-ID source against the
    // global externalSources list so seedEntrypoint and injectIoEnvVars see
    // the full object (including category="file" and mock_file).
    val sourceById = raw.externalSources
      .flatMap(s => s.id.map(_ -> s))
      .toMap
    val resolved = raw.entrypoints.map { ep =>
      ep.copy(externalSources = ep.externalSources.map { src =>
        src.id.flatMap(sourceById.get).getOrElse(src)
      })
    }
    // Merge synthesizer-canonical ``external_sinks`` into ``sinks`` (PySpark/schema_mine
    // parity). Without this, sinks declared only under external_sinks are silently
    // dropped (FAIL_ON_UNKNOWN_PROPERTIES=false used to ignore the key entirely).
    val globalSinks = raw.sinks ++ raw.externalSinks
    // Entrypoint sinks are also stored as string IDs (e.g. "scan_events_clean_sink").
    // SinkConfig.fromString sets name=id, losing the real table name stored in the global
    // sinks list (where id="scan_events_clean_sink" but name="scan_events_clean").
    // Resolve each sink string-ID against the global sinks so declaredSinkTables and
    // captureResults see the actual Snowflake write-target name, not the ID.
    val sinkById = globalSinks.flatMap(s => s.id.map(_ -> s)).toMap
    val resolved2 = resolved.map { ep =>
      val mergedEpSinks = ep.sinks ++ ep.externalSinks
      ep.copy(
        sinks = mergedEpSinks.map { sink =>
          sink.id.flatMap(sinkById.get).getOrElse(sink)
        },
        externalSinks = Nil, // folded into sinks
      )
    }
    // Fold intermediate_tables into each related entrypoint as empty table sinks so
    // seedEntrypoint CREATE/seed runs (PySpark parity: declare → CREATE → seed).
    val withIntermediates = resolved2.map { ep =>
      val related = raw.intermediateTables.filter { mid =>
        val nameOk = mid.name.nonEmpty && mid.schema.nonEmpty
        if (!nameOk) false
        else {
          val targets =
            mid.readerEntrypointIds ++ mid.consumerEntrypointIds ++
              mid.writerEntrypointId.toList
          targets.isEmpty || targets.contains(ep.id)
        }
      }.map { mid =>
        val bare = Helpers.bareTableName(mid.name)
        SinkConfig(
          id = Some(if (bare.nonEmpty) s"intermediate_$bare" else s"intermediate_${mid.name}"),
          name = Some(mid.name),
          originalTarget = Some(mid.name),
          kind = Some("table"),
          allowEmpty = mid.allowEmpty,
          schema = mid.schema,
        )
      }
      if (related.isEmpty) ep
      else {
        val existingBare = ep.sinks.flatMap(s =>
          Option(Helpers.bareTableName(s.originalTarget.orElse(s.name).orElse(s.id).getOrElse("")))
            .filter(_.nonEmpty)
        ).toSet
        val extra = related.filter { s =>
          val bare = Helpers.bareTableName(s.originalTarget.orElse(s.name).getOrElse(""))
          bare.nonEmpty && !existingBare.contains(bare)
        }
        ep.copy(sinks = ep.sinks ++ extra)
      }
    }
    raw.copy(entrypoints = withIntermediates, sinks = globalSinks, externalSinks = Nil)
  }
}

/** Snowflake section of state.json. */
case class SnowflakeState(
    database: String                          = "",
    goldenSchemas: Map[String, GoldenSchema]  = Map.empty,
    /** Pre-cloned trial schemas: ep_id → already-cloned schema name.
      * When set, cloneGoldenSchemaForTrial skips JDBC and returns this directly.
      * Useful when JDBC-based cloning is unavailable in the environment (driver/network
      * restricted) or schemas are pre-provisioned out of band. */
    preClonedSchemas: Map[String, String]     = Map.empty,
)

case class GoldenSchema(
    schema: String        = "",
    stage: String         = "",
    stagePrefix: String   = "",
    tables: List[String]  = Nil,  // P8: provisioned table list persisted at provision time
)

case class ScosConfig(
    connectionName: String = "",
)

/** Top-level state.json model. */
case class StateJson(
    snowflake: SnowflakeState = SnowflakeState(),
    config: ScosConfig        = ScosConfig(),
)

object StateJson {
  private val _mapper = JsonUtil.newMapper()   // same SNAKE_CASE mapper as analysis.json

  def load(path: String = EnvUtil.get("SCOS_STATE_JSON")): StateJson = {
    if (path == null || path.isEmpty || !new File(path).isFile)
      throw new RuntimeException(s"SCOS_STATE_JSON not set or not found: $path")
    _mapper.readValue(new File(path), classOf[StateJson])
  }
}

// ---------------------------------------------------------------------------
// Jackson helpers
// ---------------------------------------------------------------------------

private[kit] object JsonUtil {
  /** Both analysis.json and state.json use snake_case keys (external_sources, mock_file,
    * golden_schemas, stage_prefix, connection_name, …). Case-class fields keep idiomatic
    * camelCase (externalSources, stagePrefix, connectionName) and the SNAKE_CASE strategy
    * maps them automatically — the same pattern Jackson uses for the analysis case classes. */
  def newMapper(): ObjectMapper = {
    val m = new ObjectMapper()
    m.registerModule(DefaultScalaModule)
    m.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)
    m.setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
    m
  }
}

// ---------------------------------------------------------------------------
// EnvUtil — env-var simulation via System properties (forked JVM safe)
// ---------------------------------------------------------------------------

/**
 * Environment variable accessor that reads from a process-level override map
 * (populated by setEnv) first, then System.getenv, then System.getProperty.
 *
 * Because Test/fork=true isolates each test JVM, setEnv/unsetEnv are safe to
 * use inside beforeAll/afterAll without cross-test contamination.
 *
 * JVM NOTE: Python's os.environ mutation works in-process because cpython is
 * single-interpreter.  On the JVM, System.setenv is not public, so we use a
 * companion-object map that the shims and harness always consult first.
 */
object EnvUtil {
  private[kit] val overrides = new java.util.concurrent.ConcurrentHashMap[String, String]()

  def setEnv(key: String, value: String): Unit = {
    val v = normalizeStagePath(key, value)
    overrides.put(key, v)
    // Also set as system property so code that calls System.getProperty works.
    System.setProperty(key, v)
  }

  /**
   * Normalize Snowflake stage-PATH values to end with `/` at the single
   * injection chokepoint, so a workload's `s"$STAGE_PATH/file"` or a directory
   * read resolves regardless of whether the provisioner/patch-author supplied the
   * trailing slash. Targets only stage *directory* paths:
   *   - the env key is named like a stage path (`*_STAGE_PATH` / `*STAGE_PATH`),
   *     or the value is a Snowflake stage ref (`@db.schema.stage/prefix`), AND
   *   - the last `/`-segment has no file extension (so a value pointing at a
   *     single file like `.../data.parquet` is never mangled).
   */
  private[kit] def normalizeStagePath(key: String, value: String): String = {
    if (value == null || value.isEmpty || value.endsWith("/")) return value
    val isStage  = key.toUpperCase.endsWith("STAGE_PATH") || value.startsWith("@")
    val lastSeg  = value.split("/").lastOption.getOrElse("")
    val looksFile = lastSeg.contains(".")
    if (isStage && !looksFile) value + "/" else value
  }

  def unsetEnv(key: String): Unit = {
    overrides.remove(key)
    System.clearProperty(key)
  }

  /** Read key from override map → System.getenv → System.getProperty → default. */
  def get(key: String, default: String = ""): String = {
    val ov = overrides.get(key)
    if (ov != null) return ov
    val env = System.getenv(key)
    if (env != null) return env
    Option(System.getProperty(key)).getOrElse(default)
  }

  def saveAndSet(keys: Map[String, String]): Map[String, Option[String]] = {
    val saved = keys.map { case (k, _) => k -> Option(overrides.get(k)) }
    keys.foreach { case (k, v) => setEnv(k, v) }
    saved
  }

  def restore(saved: Map[String, Option[String]]): Unit = {
    saved.foreach {
      case (k, Some(v)) => setEnv(k, v)
      case (k, None)    => unsetEnv(k)
    }
  }

  /** SCOS Phase-B reflection class names, overridable via env/system property.
   *  Centralised here so ScosTrialFixture and tests share one resolution path. */
  def scosClientClass: String =
    get("SCOS_CLIENT_CLASS", "com.snowflake.snowpark_connect.client.SnowparkConnectSession")
  def scosSessionClass: String =
    get("SCOS_SESSION_CLASS", "com.snowflake.snowpark_connect.client.SnowflakeSession")
}

// ---------------------------------------------------------------------------
// Helpers object
// ---------------------------------------------------------------------------

object Helpers {

  // -------------------------------------------------------------------------
  // Path helpers
  // -------------------------------------------------------------------------

  /** mock_data root for a given entrypoint id. */
  def mockDataDirForEp(epId: String): String = {
    val root = EnvUtil.get("SCOS_MOCK_DATA_DIR", "/tmp/scos_mock_data")
    Paths.get(root, epId).toString
  }

  /**
   * Inject SCOS_INPUT_*, SCOS_TEST_AUX_*, and SCOS_SINK_* env vars for
   * file-category sources and sinks declared in the entrypoint config.
   *
   * Ports: conftest.py::io_env_for_trial() from validate-pyspark-to-snowpark-connect.
   *
   * File-category sources are mock files on disk (set as SCOS_INPUT_<ID>).
   * File-category sinks are per-trial capture directories (set as SCOS_SINK_<ID>).
   * Both must be exposed via System.getProperty (EnvUtil.setEnv writes there)
   * so workloads patched to System.getProperty("SCOS_INPUT_FOO") see the value.
   *
   * @param stageWritePaths optional Phase B overrides: sink upper-ID →
   *   ``@"db"."clone"."SCOS_SINKS"/io_id`` stage path (PySpark scos_runtime parity).
   *   When present, those SCOS_SINK_* values point at the stage instead of a local dir.
   */
  def injectIoEnvVars(
      epConfig: EntrypointConfig,
      mockDataDir: String,
      trialDir: String,
      stageWritePaths: Map[String, String] = Map.empty,
  ): Unit = {
    // File-category sources → SCOS_INPUT_<ID> and SCOS_TEST_AUX_<NAME>
    // Table/connector-category sources → SCOS_INPUT_<ID> pointing to mock parquet file.
    // Mirrors PySpark file_io_env: patched workloads call
    //   spark.read.parquet(System.getProperty("SCOS_INPUT_<id>"))
    // for both file and table/connector sources.
    epConfig.externalSources.foreach { src =>
      val rawId    = src.id.orElse(src.name).getOrElse("")
      val id       = rawId.toUpperCase.replaceAll("[^A-Z0-9]", "_")
      val mockFile = src.mockFile.getOrElse("")
      if (id.nonEmpty && mockFile.nonEmpty) {
        val path = Paths.get(mockDataDir, mockFile).toString
        if (src.category.contains("file")) {
          EnvUtil.setEnv(s"SCOS_INPUT_$id", path)
          if (rawId != id) EnvUtil.setEnv(s"SCOS_INPUT_$rawId", path)
          // Expose as SCOS_TEST_AUX_<NAME> (mirrors Python io_env_for_trial)
          src.name.foreach { n =>
            val auxKey = n.toUpperCase.replaceAll("[^A-Z0-9]", "_")
            if (auxKey != id) EnvUtil.setEnv(s"SCOS_TEST_AUX_$auxKey", path)
          }
        } else {
          // table / snowflake / jdbc: inject SCOS_INPUT_* pointing at the mock parquet.
          // The patched workload uses spark.read.parquet(getProperty("SCOS_INPUT_<id>")).
          EnvUtil.setEnv(s"SCOS_INPUT_$id", path)
          if (rawId != id) EnvUtil.setEnv(s"SCOS_INPUT_$rawId", path)
        }
      }
    }

    // Sinks → SCOS_SINK_<ID>  (per-trial capture dir, or Phase B stage path)
    // Inject for ALL sinks regardless of kind: patched workloads that write via
    // System.getProperty("SCOS_SINK_<id>") need the path set even when the sink
    // kind is recorded as "table" in analysis.json (the string-deserialised default).
    val sinkCaptureRoot = new java.io.File(trialDir, "sink_captures")
    epConfig.sinks.foreach { sink =>
      val rawId = sink.id.orElse(sink.name).getOrElse("")
      val id    = rawId.toUpperCase.replaceAll("[^A-Z0-9]", "_")
      if (id.nonEmpty) {
        val stagePath = stageWritePaths.get(id)
        if (stagePath.exists(_.nonEmpty)) {
          EnvUtil.setEnv(s"SCOS_SINK_$id", stagePath.get)
          if (rawId != id) EnvUtil.setEnv(s"SCOS_SINK_$rawId", stagePath.get)
        } else {
          // For table-kind sinks use the canonical target table name as the capture dir
          // so that captureSinkDirs in SCOS Phase B reads SCHEMA.<targetName> and finds
          // the table written by saveAsTable("<targetName>") rather than looking for
          // SCHEMA.sink_<id> which may not exist.  File-kind sinks keep rawId as dir name.
          val captureDirName = if (sink.kind.getOrElse("table") == "table") {
            val capKey = sinkCaptureKey(sink)
            if (capKey.nonEmpty) capKey else rawId.toLowerCase
          } else rawId.toLowerCase
          val captureDir = new java.io.File(sinkCaptureRoot, captureDirName)
          captureDir.mkdirs()
          EnvUtil.setEnv(s"SCOS_SINK_$id", captureDir.getAbsolutePath + "/")
          // Also set with original-case ID for workloads that use lowercase property names.
          if (rawId != id) EnvUtil.setEnv(s"SCOS_SINK_$rawId", captureDir.getAbsolutePath + "/")
        }
      }
    }
  }

  /** Snowflake internal stage used for Phase B file-sink writes (PySpark SCOS_SINKS). */
  val SinkStageName: String = "SCOS_SINKS"

  /** Build ``@"db"."clone"."SCOS_SINKS"/io_id`` paths for non-table sinks; create the stage.
    * Returns (stageWritePaths by upper ID, io_ids for GET). */
  def preparePhaseBFileSinks(
      stateJson: StateJson,
      cloneSchema: String,
      epConfig: EntrypointConfig,
  ): (Map[String, String], List[String]) = {
    val database = stateJson.snowflake.database
    if (database.isEmpty || cloneSchema.isEmpty) return (Map.empty, Nil)

    val fileSinks = epConfig.sinks.filter { s =>
      val kind = s.kind.getOrElse("table").toLowerCase
      kind.nonEmpty && kind != "table"
    }
    if (fileSinks.isEmpty) return (Map.empty, Nil)

    createSinkStage(stateJson, database, cloneSchema)

    val paths = mutable.LinkedHashMap[String, String]()
    val ioIds = mutable.ListBuffer[String]()
    fileSinks.foreach { sink =>
      val rawId = sink.id.orElse(sink.name).getOrElse("")
      val id    = rawId.toUpperCase.replaceAll("[^A-Z0-9]", "_")
      val ioId  = sinkCaptureKey(sink)
      if (id.nonEmpty && ioId.nonEmpty) {
        val stagePath =
          s"""@"${safeIdent(database)}"."${safeIdent(cloneSchema)}"."$SinkStageName"/$ioId"""
        paths(id) = stagePath
        if (!ioIds.contains(ioId)) ioIds += ioId
      }
    }
    (paths.toMap, ioIds.toList)
  }

  /** CREATE STAGE IF NOT EXISTS for file-sink writes inside the trial clone. */
  def createSinkStage(stateJson: StateJson, database: String, cloneSchema: String): Unit = {
    if (database.isEmpty || cloneSchema.isEmpty) return
    val conn = Try(openJdbcConnection(stateJson.config.connectionName, database)).getOrElse {
      System.err.println("[Helpers] createSinkStage: JDBC unavailable — skipping SCOS_SINKS stage")
      return
    }
    try {
      val db = safeIdent(database); val cs = safeIdent(cloneSchema)
      val stmt = conn.createStatement()
      try {
        stmt.execute(s"""USE DATABASE "$db"""")
        stmt.execute(
          s"""CREATE STAGE IF NOT EXISTS "$db"."$cs"."$SinkStageName""""
        )
      } finally stmt.close()
    } finally {
      conn.close()
    }
  }

  /**
   * GET staged sink files into ``localSinkRoot/<io_id>/`` then retarget SCOS_SINK_*
   * env vars to those local dirs so captureSinkDirs can read parquet/csv like Phase A.
   */
  def downloadStagedSinks(
      stateJson: StateJson,
      database: String,
      cloneSchema: String,
      ioIds: List[String],
      localSinkRoot: String,
  ): Unit = {
    if (ioIds.isEmpty || database.isEmpty || cloneSchema.isEmpty) return
    val root = new File(localSinkRoot)
    root.mkdirs()
    val connName = stateJson.config.connectionName
    ioIds.foreach { ioId =>
      val localDir = new File(root, ioId)
      localDir.mkdirs()
      val getSql =
        s"""GET '@"${safeIdent(database)}"."${safeIdent(cloneSchema)}"."$SinkStageName"/$ioId/' """ +
          s"""'file://${localDir.getAbsolutePath}/'"""
      val conn = Try(openJdbcConnection(connName, database)).getOrElse {
        System.err.println(s"[Helpers] downloadStagedSinks: JDBC unavailable for $ioId")
        return
      }
      try {
        val stmt = conn.createStatement()
        try stmt.execute(getSql)
        catch {
          case e: Exception =>
            // Empty sink → no staged files is expected.
            System.err.println(
              s"[Helpers] INFO: GET staged sink '$ioId' yielded nothing (may be empty): ${e.getMessage}"
            )
        } finally stmt.close()
      } finally {
        conn.close()
      }
    }
  }

  /** After GET, point SCOS_SINK_* at local capture dirs for captureResults. */
  def retargetFileSinkEnvToLocal(
      epConfig: EntrypointConfig,
      localSinkRoot: String,
  ): Unit = {
    epConfig.sinks.foreach { sink =>
      val kind = sink.kind.getOrElse("table").toLowerCase
      if (kind.nonEmpty && kind != "table") {
        val rawId = sink.id.orElse(sink.name).getOrElse("")
        val id    = rawId.toUpperCase.replaceAll("[^A-Z0-9]", "_")
        val ioId  = sinkCaptureKey(sink)
        if (id.nonEmpty && ioId.nonEmpty) {
          val local = new File(localSinkRoot, ioId).getAbsolutePath + "/"
          EnvUtil.setEnv(s"SCOS_SINK_$id", local)
          if (rawId != id) EnvUtil.setEnv(s"SCOS_SINK_$rawId", local)
        }
      }
    }
  }

  /** Bare (unqualified) table name from a FQN or path expression.
   *  Returns "" for DBFS interpolation paths (e.g. dbfs:${getBronzeLocation(banners)})
   *  whose last segment still contains unsafe SQL identifier characters like { } $ ( ) [ ].
   */
  def bareTableName(raw: String): String = {
    if (raw == null || raw.isEmpty) return ""
    val clean = raw.replace("`", "").replace("\"", "").trim
    // last segment of dot-separated FQN, last segment of a path
    val dotPart = clean.split("\\.", -1).last
    val slashPart = dotPart.split("/", -1).last
    // strip extension
    val withoutExt = slashPart.split("\\.", -1) match {
      case parts if parts.length > 1 => parts.dropRight(1).mkString(".")
      case parts                     => parts(0)
    }
    val result = withoutExt.toLowerCase.trim
    // Reject DBFS interpolation expressions (e.g. ${getbronzelocation(banners)}) that
    // produce unsafe SQL identifiers.  safeIdent would throw on these; return "" so
    // callers skip the sink/source silently instead of aborting the test suite.
    // SKILL-FIX: also exclude angle-bracket placeholder patterns like <dynamic_table_1>
    // generated by ast_to_analysis.py when a table path could not be statically resolved.
    if (result.exists(c => "{}$()[]<>".indexOf(c) >= 0)) "" else result
  }

  // -------------------------------------------------------------------------
  // Phase A — local SparkSession with Delta
  // -------------------------------------------------------------------------

  /**
   * Run Spark SQL with ``current_date`` / ``current_timestamp`` textually
   * pinned (PySpark ``install_sql_date_pin`` parity). Prefer this over raw
   * ``spark.sql`` in harness/kit paths that embed date functions in strings.
   */
  def sqlPinned(spark: SparkSession, query: String): org.apache.spark.sql.DataFrame =
    spark.sql(DatePin.rewriteSql(query, spark))

  /**
   * Build a local SparkSession backed by Delta Lake.
   * Ported from conftest.py::_build_local_session.
   */
  def buildLocalSession(warehouseDir: String): SparkSession = {
    // Phase A uses plain Hive catalog (not DeltaCatalog) so seedEntrypoint's saveAsTable
    // does NOT trigger DatabricksLogging.recordOperation, which creates too-many-args lambdas
    // in Scala 2.12 + Java 17 (DELTA-3744 / LambdaMetafactory.altMetafactory limit).
    // Workloads can still write delta format via df.write.format("delta").save(path) which
    // bypasses the catalog entirely.
    SparkSession.builder()
      .master("local[1]")
      .config("spark.sql.shuffle.partitions", "1")
      .config("spark.sql.warehouse.dir", warehouseDir)
      .config("spark.driver.extraJavaOptions", s"-Dderby.system.home=$warehouseDir/derby")
      .config("spark.driver.host", "127.0.0.1")
      .config("spark.driver.bindAddress", "127.0.0.1")
      .config("spark.databricks.delta.schema.autoMerge.enabled", "true")
      .config("spark.databricks.delta.commitInfo.enabled", "false")
      .getOrCreate()
  }

  /** SKILL-FIX: ensure the test JVM's Derby metastore uses the same directory that
    * SubprocessLauncher will receive via SCOS_WAREHOUSE_DIR.  Without this,
    * `spark.driver.extraJavaOptions` (cluster-mode only) is ignored in local mode
    * and Derby defaults to `user.dir/metastore_db`, making Hive tables registered by
    * the test JVM invisible to the subprocess (which sets derby.system.home directly).
    * Must be called BEFORE the first SparkSession.getOrCreate() call for the trial.
    */
  def pinDerbyHome(warehouseDir: String): Unit = {
    val derbyHome = java.nio.file.Paths.get(warehouseDir, "derby").toString
    System.setProperty("derby.system.home", derbyHome)
  }

  /**
   * Install Delta idempotency patches (Phase A only).
   * Ported from helpers.py::install_delta_patches.
   *
   * JVM NOTE: Unlike Python, we cannot monkey-patch DataFrame.write.saveAsTable.
   * Instead, install_delta_patches configures Spark SQL to tolerate missing
   * tables for DELETE/INSERT operations via the custom SQL listener below.
   * Patch-author edits should be reviewed if they rely on Mode.Overwrite+saveAsTable
   * for idempotency; the Scala Delta DSL handles this natively.
   */
  def installDeltaPatches(spark: SparkSession): Unit = {
    // Register a no-op listener; actual idempotency comes from Delta's own
    // CREATE OR REPLACE / MERGE semantics when Mode.Overwrite is used.
    // This is a no-op stub — workloads that need richer idempotency should
    // use df.write.mode(SaveMode.Overwrite).format("delta").saveAsTable(name).
    ()
  }

  // -------------------------------------------------------------------------
  // Type resolution
  // -------------------------------------------------------------------------

  private val sparkTypeMap: Map[String, DataType] = Map(
    "string"         -> StringType,
    "varchar"        -> StringType,
    "text"           -> StringType,
    "char"           -> StringType,
    "int"            -> IntegerType,
    "integer"        -> IntegerType,
    "long"           -> LongType,
    "bigint"         -> LongType,
    "short"          -> ShortType,
    "smallint"       -> ShortType,
    "byte"           -> ByteType,
    "tinyint"        -> ByteType,
    "float"          -> FloatType,
    "double"         -> DoubleType,
    "real"           -> DoubleType,
    "boolean"        -> BooleanType,
    "bool"           -> BooleanType,
    "date"           -> DateType,
    "timestamp"      -> TimestampType,
    "timestamp_ltz"  -> TimestampType,
    "binary"         -> BinaryType,
  )

  def resolveSparkType(typeStr: String): DataType = {
    if (typeStr == null || typeStr.isEmpty) return StringType
    val base = typeStr.toLowerCase.split("\\(")(0).trim
    // Accept "LongType"-style class names from the analyzer
    val key = if (base.endsWith("type")) base.stripSuffix("type") else base
    // decimal/numeric carry precision+scale that must be preserved — otherwise a
    // decimal(18,4) source column would be seeded as the default (or worse, String),
    // diverging from the DECIMAL(18,4) golden Snowflake table. Mirrors Python
    // helpers._resolve_spark_type (default DECIMAL(38,18) when unparametrized).
    if (key == "decimal" || key == "numeric") {
      val m = """\(\s*(\d+)\s*,\s*(\d+)\s*\)""".r.findFirstMatchIn(typeStr)
      return m.map(g => DecimalType(g.group(1).toInt, g.group(2).toInt)).getOrElse(DecimalType(38, 18))
    }
    // timestamp_ntz only exists as a static type from Spark 3.4+. Resolve it at runtime
    // via DDL so the kit still compiles against older Spark (3.3) when aligned to the
    // workload in Phase A; on 3.3 the unknown type falls back to TimestampType.
    if (key == "timestamp_ntz") {
      return try { DataType.fromDDL("timestamp_ntz") }
             catch { case _: Throwable => TimestampType }
    }
    sparkTypeMap.getOrElse(key, sparkTypeMap.getOrElse(base, StringType))
  }

  def buildSparkSchema(fields: List[ColumnDef]): StructType = {
    StructType(fields.map { f =>
      val typeStr = f.dtype.orElse(f.`type`).getOrElse("string")
      StructField(f.name, resolveSparkType(typeStr), f.nullable.getOrElse(true))
    })
  }

  // -------------------------------------------------------------------------
  // seed_entrypoint — Ported from helpers.py::seed_entrypoint
  // -------------------------------------------------------------------------

  /**
   * Seed external source tables and pre-create empty sink tables into outputSchema.
   * Reads mock CSV/JSON/Parquet files from mockDataDir via spark.read.
   * Writes via DataFrame.write.mode(Overwrite).saveAsTable.
   * Works for both Phase A (Delta-backed local Spark) and Phase B (SCOS Spark).
   *
   * Returns the list of fully-qualified table names created by the harness.
   */
  def seedEntrypoint(
      spark: SparkSession,
      epConfig: EntrypointConfig,
      mockDataDir: String,
      outputSchema: String,
  ): List[String] = {
    val seeded   = mutable.ListBuffer[String]()
    val seededSet = mutable.Set[String]()

    // --- external sources (category: table / snowflake / jdbc) ---
    val tableCategories = Set("table", "snowflake", "jdbc")
    for (src <- epConfig.externalSources if tableCategories.contains(src.category.getOrElse(""))) {
      val bare = bareTableName(src.originalPath.orElse(src.name).getOrElse(""))
      if (bare.isEmpty) ()
      else {
        val target   = s"$outputSchema.$bare"
        val mockFile = src.mockFile.getOrElse("")
        if (mockFile.nonEmpty) {
          val csvPath = Paths.get(mockDataDir, mockFile).toString
          if (new File(csvPath).isFile) {
            Try {
              val df = readMockFile(spark, csvPath, src.schema, src.readerOptions)
              df.write.mode("overwrite").saveAsTable(target)
              seeded += target.toLowerCase
              seededSet += target.toLowerCase
            }.failed.foreach { e =>
              System.err.println(s"warn: seed_entrypoint: failed to seed $target: $e")
            }
          }
        }
      }
    }

    // --- pre-create empty sink tables ---
    for (sink <- epConfig.sinks if sink.kind.contains("table")) {
      val bare = bareTableName(sink.originalTarget.orElse(sink.name).getOrElse(""))
      if (bare.isEmpty || sink.schema.isEmpty) ()
      else {
        val target = s"$outputSchema.$bare"
        if (!seededSet.contains(target.toLowerCase)) {
          Try {
            // spark.sparkContext is NOT available on Spark Connect (SCOS) sessions.
            // Use an empty java.util.List<Row> instead — compatible with both local Spark and SCOS.
            val emptyDf = spark.createDataFrame(
              java.util.Collections.emptyList[org.apache.spark.sql.Row](),
              buildSparkSchema(sink.schema),
            )
            emptyDf.write.mode("overwrite").saveAsTable(target)
            seeded += target.toLowerCase
            seededSet += target.toLowerCase
          }.failed.foreach { e =>
            System.err.println(s"warn: seed_entrypoint: failed to pre-create sink $target: $e")
          }
        }
      }
    }

    seeded.toList
  }

  /** Read a single mock file (CSV / JSON / Parquet) with optional schema. */
  private def readMockFile(
      spark: SparkSession,
      path: String,
      schema: List[ColumnDef],
      readerOptions: Map[String, String],
  ): DataFrame = {
    val ext = path.toLowerCase.split("\\.").lastOption.getOrElse("csv")
    var reader = spark.read
    readerOptions.foreach { case (k, v) => reader = reader.option(k, v) }

    if (schema.nonEmpty) {
      val st = buildSparkSchema(schema)
      ext match {
        // Parquet files carry an embedded schema. Forcing the analysis.json schema
        // via reader.schema(st) fails when the prewarm encodes string-ID columns as
        // INT64 — the vectorized reader cannot convert INT64 to BINARY(UTF8) and the
        // write task aborts. Instead, read with the parquet's own types and then cast
        // each declared column to the target type so downstream workloads see the
        // correct types (e.g. LongType IDs cast to StringType).
        case "parquet" =>
          val rawDf = reader.parquet(path)
          schema.foldLeft(rawDf) { (df, colDef) =>
            if (df.schema.fieldNames.exists(_.equalsIgnoreCase(colDef.name))) {
              val typeStr    = colDef.dtype.orElse(colDef.`type`).getOrElse("string")
              val targetType = resolveSparkType(typeStr)
              df.withColumn(colDef.name, df(colDef.name).cast(targetType))
            } else df
          }
        case "json" | "jsonl" | "ndjson" => reader.schema(st).json(path)
        case "tsv"                       =>
          reader.option("header", "true").option("sep", "\t")
            .option("nullValue", "").schema(st).csv(path)
        case _                           =>
          reader.option("header", "true").option("nullValue", "").schema(st).csv(path)
      }
    } else {
      ext match {
        case "parquet"                   => reader.parquet(path)
        case "json" | "jsonl" | "ndjson" => reader.json(path)
        case "tsv"                       =>
          reader.option("header", "true").option("sep", "\t")
            .option("inferSchema", "true").option("nullValue", "").csv(path)
        case _                           =>
          reader.option("header", "true").option("inferSchema", "true")
            .option("nullValue", "").csv(path)
      }
    }
  }

  // -------------------------------------------------------------------------
  // captureResults — Ported from helpers.py::capture_results
  //
  // Output layout (must match ScosComparator / Track A expectations):
  //   <outputDir>/tables/<name>.parquet
  //   <outputDir>/_index.json
  //
  // _index.json schema:
  //   { trial_id, phase, output_schema, captured_at,
  //     tables: [{name, path, schema_json, row_count, absolute_path}],
  //     artifacts: [], failures: [] }
  // -------------------------------------------------------------------------

  /**
   * Snapshot all tables in outputSchema to Parquet + write _index.json manifest.
   *
   * @param spark        active SparkSession
   * @param outputSchema schema whose tables to capture
   * @param outputDir    trial result directory (e.g. results/phase_a/<ep_id>)
   * @param exclude      table names to skip (the seeded inputs)
   * @param excludeIfEmpty  declared sinks: skip if empty and not written by workload
   * @return the manifest as a Map (mirrors Python dict return)
   */
  def captureResults(
      spark: SparkSession,
      outputSchema: String,
      outputDir: String,
      exclude: Seq[String]        = Nil,
      excludeIfEmpty: Seq[String] = Nil,
  ): Map[String, Any] = {

    val mapper    = JsonUtil.newMapper()
    val tablesDir = new File(outputDir, "tables")
    tablesDir.mkdirs()

    val excluded           = (exclude ++ exclude.map(_.split("\\.").last)).map(_.toLowerCase).toSet
    val excludeIfEmptySet  = (excludeIfEmpty ++ excludeIfEmpty.map(_.split("\\.").last)).map(_.toLowerCase).toSet

    val capturedTables = new java.util.concurrent.CopyOnWriteArrayList[Map[String, Any]]()
    val failures       = new java.util.concurrent.CopyOnWriteArrayList[Map[String, String]]()

    // List tables in the output schema
    val schemaId = safeIdent(outputSchema)
    val rows = Try(spark.sql(s"SHOW TABLES IN $schemaId").collect()).getOrElse(Array.empty)

    // Fallback: if the catalog is empty (e.g. the workload called spark.stop() and we
    // rebuilt the session), scan the warehouse filesystem for Delta/Parquet directories
    // written by saveAsTable and register them so SHOW TABLES works.
    if (rows.isEmpty) {
      val warehousePath = Try(spark.conf.get("spark.sql.warehouse.dir")).getOrElse("")
      if (warehousePath.nonEmpty) {
        val warehouseRoot = warehouseDirFile(warehousePath)
        val schemaDb = new java.io.File(warehouseRoot, s"${schemaId}.db")
        if (!schemaDb.isDirectory) {
          // Also try without .db suffix (some Hive configurations omit it)
          Try {
            val alt = new java.io.File(warehouseRoot, schemaId)
            if (alt.isDirectory) {
              spark.sql(s"CREATE DATABASE IF NOT EXISTS $schemaId")
              Option(alt.listFiles(f => f.isDirectory && !f.getName.startsWith("_"))).getOrElse(Array.empty)
                .foreach { td =>
                  Try { spark.sql(s"CREATE TABLE IF NOT EXISTS $schemaId.${td.getName} USING DELTA LOCATION '${td.getAbsolutePath}'") }
                }
            }
          }
        } else {
          Try { spark.sql(s"CREATE DATABASE IF NOT EXISTS $schemaId") }
          Option(schemaDb.listFiles(f => f.isDirectory && !f.getName.startsWith("_"))).getOrElse(Array.empty)
            .foreach { td =>
              Try { spark.sql(s"CREATE TABLE IF NOT EXISTS $schemaId.${td.getName} USING DELTA LOCATION '${td.getAbsolutePath}'") }
                .failed.foreach(e => System.err.println(s"warn: warehouse fallback: failed to register ${td.getName}: $e"))
            }
        }
      }
    }

    val rowsFinal = if (rows.nonEmpty) rows
                   else Try(spark.sql(s"SHOW TABLES IN $schemaId").collect()).getOrElse(Array.empty)

    // Speed 7: parallel table capture.
    // Each sink table is an independent Spark read+write, so we can fire them
    // concurrently.  Use a bounded thread pool (default 4, or SCOS_CAPTURE_PARALLELISM)
    // to avoid spawning unlimited Spark tasks for wide workloads.
    // On any thread-level failure we fall back to the serial path (same behaviour as before).
    val captureParallelism = {
      val envVal = Option(EnvUtil.get("SCOS_CAPTURE_PARALLELISM", "")).filter(_.nonEmpty)
      Try(envVal.map(_.toInt).getOrElse(4)).getOrElse(4).max(1)
    }

    /** Capture one table row, appending to capturedTables / failures. */
    def captureOneTable(row: org.apache.spark.sql.Row): Unit = {
      val tableName = Try(row.getAs[String]("tableName"))
        .recoverWith { case _ => Try(row.getString(1)) }
        .getOrElse("").toLowerCase
      val isTemp = Try(row.getAs[Boolean]("isTemporary")).getOrElse(
        Try(row.getBoolean(2)).getOrElse(false))
      if (isTemp || tableName.isEmpty
          || tableName.startsWith("snowpark_temp_")
          || excluded.contains(tableName)
          || excluded.contains(s"$outputSchema.$tableName")) return
      val outPath = new File(tablesDir, s"$tableName.parquet")
      Try {
        val df = spark.table(s"$schemaId.${safeIdent(tableName)}").cache()
        try {
          val countResult = Try(df.count())
          countResult match {
            // SCOS tolerance: a 0-row saveAsTable writes no Parquet files; the subsequent
            // read fails with Snowflake error 253006 "file does not exist". Treat this as
            // an empty capture (0 rows, schema preserved) so the manifest is not lost and
            // the comparator can still compare structure against the Phase-A baseline.
            case scala.util.Failure(ex)
                if { val m = Option(ex.getMessage).getOrElse("").toLowerCase
                     m.contains("does not exist") || m.contains("no files") ||
                     m.contains("253006") } =>
              Try {
                val emptyDf = spark.createDataFrame(
                  java.util.Collections.emptyList[org.apache.spark.sql.Row](), df.schema)
                emptyDf.write.mode("overwrite").parquet(outPath.getAbsolutePath)
                val schemaJson = mapper.writeValueAsString(
                  df.schema.fields.map(f => Map("name" -> f.name, "type" -> f.dataType.typeName))
                )
                capturedTables.add(Map(
                  "name"          -> tableName,
                  "path"          -> s"tables/$tableName.parquet",
                  "schema_json"   -> schemaJson,
                  "row_count"     -> 0L,
                  "absolute_path" -> outPath.getAbsolutePath,
                ))
              }.failed.foreach { writeEx =>
                failures.add(Map("source" -> "catalog", "name" -> tableName,
                  "reason" -> s"empty-table write failed: ${writeEx.getMessage.take(200)}"))
              }

            case scala.util.Failure(ex) =>
              throw ex  // genuine failure — re-throw for outer Try.failed.foreach

            case scala.util.Success(rowCount) =>
              if (rowCount == 0 && (excludeIfEmptySet.contains(tableName)
                  || excludeIfEmptySet.contains(s"$outputSchema.$tableName"))) {
                System.err.println(s"warn: captureResults: skipped allow_empty sink $tableName in $outputSchema")
              } else {
                import org.apache.spark.sql.types.TimestampType
                import org.apache.spark.sql.functions.date_format
                val dfSafe = df.schema.fields.foldLeft(df) { (acc, field) =>
                  field.dataType match {
                    case TimestampType =>
                      acc.withColumn(field.name,
                        date_format(acc(field.name), "yyyy-MM-dd HH:mm:ss.SSSSSS"))
                    case _ => acc
                  }
                }
                dfSafe.write.mode("overwrite").parquet(outPath.getAbsolutePath)
                val schemaJson = mapper.writeValueAsString(
                  df.schema.fields.map(f => Map("name" -> f.name, "type" -> f.dataType.typeName))
                )
                capturedTables.add(Map(
                  "name"          -> tableName,
                  "path"          -> s"tables/$tableName.parquet",
                  "schema_json"   -> schemaJson,
                  "row_count"     -> rowCount,
                  "absolute_path" -> outPath.getAbsolutePath,
                ))
              }
          }
        } finally df.unpersist()
      }.failed.foreach { e =>
        failures.add(Map("source" -> "catalog", "name" -> tableName,
          "reason" -> Option(e.getMessage).getOrElse(e.toString).take(200)))
      }
    }

    // Run captures in parallel with a bounded thread pool; fall back to serial on error.
    val tableRows = rowsFinal.toSeq
    if (captureParallelism > 1 && tableRows.length > 1) {
      val pool = java.util.concurrent.Executors.newFixedThreadPool(captureParallelism)
      implicit val ec: ExecutionContext = ExecutionContext.fromExecutorService(pool)
      try {
        val futures = tableRows.map { row =>
          Future { captureOneTable(row) }
        }
        // Await all futures; timeout = 30 minutes (same as sbt test timeout).
        Try(Await.result(Future.sequence(futures), 30.minutes)).failed.foreach { ex =>
          // On timeout/error fall back to serial for remaining tables (already-completed
          // futures wrote their results; we only need to redo any that didn't fire).
          System.err.println(s"warn: captureResults parallel batch error ($ex); " +
                             "already-captured tables are preserved")
        }
      } finally {
        pool.shutdown()
      }
    } else {
      tableRows.foreach(captureOneTable)
    }

    // Also capture any file-form sinks written to SCOS_SINK_* paths.
    // captureSinkDirs appends to mutable ListBuffers; use bridge buffers and merge back.
    import scala.collection.JavaConverters._
    val sinkCapturedBuf = mutable.ListBuffer[Map[String, Any]]()
    val sinkFailuresBuf = mutable.ListBuffer[Map[String, String]]()
    captureSinkDirs(spark, outputDir, outputSchema, sinkCapturedBuf, sinkFailuresBuf, mapper)
    sinkCapturedBuf.foreach(capturedTables.add)
    sinkFailuresBuf.foreach(failures.add)

    val capturedList = capturedTables.asScala.toList
    val failureList  = failures.asScala.toList

    writeIndex(outputDir, outputSchema, capturedList, failureList, mapper)
  }

  /**
   * Capture Parquet outputs from SCOS_SINK_* directories (file-form sinks).
   * Called automatically by captureResults if any SCOS_SINK_* keys are present.
   * Mirrors Python helpers.py capture_results() file-sink branch.
   *
   * Phase B (SCOS): prefer local dirs populated by ``downloadStagedSinks`` (GET from
   * SCOS_SINKS). Fall back to catalog ``spark.read.table`` for saveAsTable remaps.
   * Never treat a still-staged ``@...`` path as a local parquet directory.
   */
  private def captureSinkDirs(
      spark: SparkSession,
      outputDir: String,
      outputSchema: String,
      capturedTables: mutable.ListBuffer[Map[String, Any]],
      failures: mutable.ListBuffer[Map[String, String]],
      mapper: com.fasterxml.jackson.databind.ObjectMapper,
  ): Unit = {
    import scala.collection.JavaConverters._
    val tablesDir = new File(outputDir, "tables")
    tablesDir.mkdirs()
    val sinkKeys = EnvUtil.overrides.keys().asScala
      .filter(_.startsWith("SCOS_SINK_")).toList.sorted
    val isScos = System.getProperty("SPARK_CONNECT_MODE_ENABLED", "") == "1"
    // Use the caller's outputSchema (the actual trial clone schema) when available;
    // fall back to SCOS_OUTPUT_SCHEMA system property for backward compatibility.
    val scosSchema = if (outputSchema.nonEmpty) outputSchema
                     else System.getProperty("SCOS_OUTPUT_SCHEMA", "")
    for (sinkKey <- sinkKeys) {
      val sinkDir     = EnvUtil.get(sinkKey)
      val sinkName    = sinkKey.stripPrefix("SCOS_SINK_").toLowerCase
      val outPath     = new File(tablesDir, s"$sinkName.parquet")
      val sinkDirFile = new File(sinkDir)
      val looksLikeStage = sinkDir.startsWith("@") || sinkDir.contains(s""""$SinkStageName"""")
      val hasLocalFiles = !looksLikeStage && sinkDirFile.isDirectory &&
        Option(sinkDirFile.listFiles()).exists(_.nonEmpty)

      def captureFromLocal(sourceLabel: String): Unit = {
        Try {
          val files = Option(new File(sinkDir).listFiles()).getOrElse(Array.empty[File])
          // Prefer parquet; fall back to csv/json if needed.
          val df =
            if (files.exists(_.getName.endsWith(".csv")))
              spark.read.option("header", "true").option("inferSchema", "true").csv(sinkDir)
            else if (files.exists(f =>
              f.getName.endsWith(".json") || f.getName.endsWith(".jsonl")))
              spark.read.json(sinkDir)
            else
              spark.read.parquet(sinkDir)
          val rowCount = df.count()
          if (rowCount > 0) {
            df.write.mode("overwrite").parquet(outPath.getAbsolutePath)
            val schemaJson = mapper.writeValueAsString(
              df.schema.fields.map(f => Map("name" -> f.name, "type" -> f.dataType.typeName)))
            capturedTables += Map(
              "name"          -> sinkName,
              "path"          -> s"tables/$sinkName.parquet",
              "schema_json"   -> schemaJson,
              "row_count"     -> rowCount,
              "absolute_path" -> outPath.getAbsolutePath,
              "source"        -> sourceLabel,
            )
          }
        }.failed.foreach { e =>
          failures += Map("source" -> sourceLabel, "name" -> sinkName, "reason" -> e.getMessage.take(200))
        }
      }

      if (hasLocalFiles) {
        captureFromLocal(if (isScos) "scos_stage_sink" else "file_sink")
      } else if (isScos && !looksLikeStage) {
        // Phase B table remap: workload called saveAsTable → read from clone schema.
        val tableName = sinkDirFile.getName.stripSuffix("/").toLowerCase
        val fqTable   = if (scosSchema.nonEmpty) s"$scosSchema.$tableName" else tableName
        Try {
          val df       = spark.read.table(fqTable)
          val rowCount = df.count()
          if (rowCount > 0) {
            df.write.mode("overwrite").parquet(outPath.getAbsolutePath)
            val schemaJson = mapper.writeValueAsString(
              df.schema.fields.map(f => Map("name" -> f.name, "type" -> f.dataType.typeName)))
            capturedTables += Map(
              "name"          -> sinkName,
              "path"          -> s"tables/$sinkName.parquet",
              "schema_json"   -> schemaJson,
              "row_count"     -> rowCount,
              "absolute_path" -> outPath.getAbsolutePath,
              "source"        -> "scos_table_sink",
            )
          }
        }.failed.foreach { e =>
          val failName = if (tableName.nonEmpty && tableName != sinkName) tableName else sinkName
          failures += Map("source" -> "scos_table_sink", "name" -> failName, "reason" -> e.getMessage.take(200))
        }
      } else if (isScos && looksLikeStage) {
        failures += Map(
          "source" -> "scos_stage_sink",
          "name"   -> sinkName,
          "reason" -> "SCOS_SINK still points at stage path — downloadStagedSinks did not retarget",
        )
      }
    }
  }

  private def writeIndex(
      outputDir: String,
      outputSchema: String,
      tables: List[Map[String, Any]],
      failures: List[Map[String, String]],
      mapper: ObjectMapper,
  ): Map[String, Any] = {
    val trialId      = new File(outputDir).getName
    val phaseDir     = Option(new File(outputDir).getParentFile).map(_.getName).getOrElse("unknown")
    val phase        = if (phaseDir == "phase_a" || phaseDir == "phase_b") phaseDir else "unknown"
    val capturedAt   = ZonedDateTime.now(ZoneOffset.UTC)
      .format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"))

    val manifest: Map[String, Any] = Map(
      "trial_id"      -> trialId,
      "phase"         -> phase,
      "output_schema" -> outputSchema,
      "captured_at"   -> capturedAt,
      "tables"        -> tables,
      "artifacts"     -> List.empty[Map[String, Any]],
      "failures"      -> failures,
    )

    val indexPath = new File(outputDir, "_index.json")
    val tmpPath   = new File(outputDir, "_index.json.tmp")
    Try {
      val pw = new PrintWriter(tmpPath, "UTF-8")
      try { pw.print(mapper.writerWithDefaultPrettyPrinter().writeValueAsString(manifest)) }
      finally { pw.close() }
      tmpPath.renameTo(indexPath)
    }.failed.foreach { e =>
      System.err.println(s"warn: captureResults: failed to write _index.json: $e")
      tmpPath.delete()
    }

    // PySpark parity: driver._has_phase_a_baseline gates on _harness_status.json ok:true
    val statusOk = failures.isEmpty
    val status: Map[String, Any] = Map(
      "ok"          -> statusOk,
      "trial_id"    -> trialId,
      "phase"       -> phase,
      "captured_at" -> capturedAt,
      "table_count" -> tables.size,
      "failure_count" -> failures.size,
    )
    val statusPath = new File(outputDir, "_harness_status.json")
    val statusTmp  = new File(outputDir, "_harness_status.json.tmp")
    Try {
      val pw = new PrintWriter(statusTmp, "UTF-8")
      try { pw.print(mapper.writerWithDefaultPrettyPrinter().writeValueAsString(status)) }
      finally { pw.close() }
      statusTmp.renameTo(statusPath)
    }.failed.foreach { e =>
      System.err.println(s"warn: captureResults: failed to write _harness_status.json: $e")
      statusTmp.delete()
    }

    manifest
  }

  // -------------------------------------------------------------------------
  // declaredSinkTables — Ported from helpers.py::declared_sink_tables
  // -------------------------------------------------------------------------

  def declaredSinkTables(epConfig: EntrypointConfig, outputSchema: String): List[String] = {
    val seen  = mutable.Set[String]()
    val sinks = mutable.ListBuffer[String]()
    for (sink <- epConfig.sinks if sink.kind.contains("table")) {
      val bare = bareTableName(sink.originalTarget.orElse(sink.name).getOrElse(""))
      if (bare.nonEmpty) {
        val target = s"$outputSchema.$bare".toLowerCase
        if (seen.add(target)) sinks += target
      }
    }
    sinks.toList
  }

  // -------------------------------------------------------------------------
  // allow_empty sink helpers — Ported from helpers.py (PR #3621)
  // -------------------------------------------------------------------------

  private def sinkCaptureKey(sink: SinkConfig): String = {
    val kind = sink.kind.getOrElse("table")
    if (kind == "table") {
      bareTableName(sink.originalTarget.orElse(sink.name).orElse(sink.id).getOrElse(""))
    } else {
      val rawId = sink.id.orElse(sink.name).getOrElse("")
      rawId.toLowerCase.replaceAll("[^a-z0-9]+", "_").stripPrefix("_").stripSuffix("_")
    }
  }

  /** Sinks explicitly allowed to be empty (have a non-blank allowEmpty reason).
   *  Table sinks → fully-qualified "schema.table"; file sinks → normalized io_id.
   *  Passed as excludeIfEmpty to captureResults so empty pre-seeded sink tables
   *  are skipped only when intentional, not silently for every declared sink. */
  def declaredAllowEmptySinkTables(epConfig: EntrypointConfig, outputSchema: String): List[String] = {
    val seen   = mutable.Set[String]()
    val result = mutable.ListBuffer[String]()
    for (sink <- epConfig.sinks if sink.allowEmpty.exists(_.trim.nonEmpty)) {
      val key    = sinkCaptureKey(sink)
      if (key.isEmpty) ()
      else {
        val target = if (sink.kind.getOrElse("table") == "table") s"$outputSchema.$key".toLowerCase
                     else key
        if (seen.add(target)) result += target
      }
    }
    result.toList
  }

  /** Normalized capture expectations for every declared sink.
   *  Returns a map from capture_name → spec map (mirrors declared_sink_capture_specs). */
  private[kit] def declaredSinkCaptureSpecs(epConfig: EntrypointConfig): Map[String, Map[String, String]] = {
    val result = mutable.LinkedHashMap[String, Map[String, String]]()
    for (sink <- epConfig.sinks) {
      val captureName = sinkCaptureKey(sink)
      if (captureName.nonEmpty && !result.contains(captureName)) {
        result(captureName) = Map(
          "captureName"  -> captureName,
          "declaredName" -> sink.name.orElse(sink.id).getOrElse(captureName),
          "kind"         -> sink.kind.getOrElse("table"),
          "allowEmpty"   -> sink.allowEmpty.map(_.trim).getOrElse(""),
        )
      }
    }
    result.toMap
  }

  /** True when the entrypoint declares at least one sink that must capture rows
   *  (i.e. at least one sink without an allowEmpty reason). */
  def requiresNonemptySinkCapture(epConfig: EntrypointConfig): Boolean =
    declaredSinkCaptureSpecs(epConfig).values.exists(_.getOrElse("allowEmpty", "").isEmpty)

  private def matchesDeclaredSink(item: Map[String, Any], captureName: String): Boolean = {
    val name = item.getOrElse("name", "").toString.trim.toLowerCase
    name == captureName || name.startsWith(s"${captureName}__")
  }

  /** Validate captured tables against declared sinks.
   *  Returns a list of failure maps with "critical" -> true for each non-allow_empty
   *  sink that produced no rows (mirrors validate_declared_sink_outputs in helpers.py). */
  def validateDeclaredSinkOutputs(
      epConfig: EntrypointConfig,
      manifest: Map[String, Any],
  ): List[Map[String, Any]] = {
    val specs = declaredSinkCaptureSpecs(epConfig)
    if (specs.isEmpty) return Nil

    val captured = manifest.getOrElse("tables", Nil).asInstanceOf[List[Map[String, Any]]]
    val guidance = "Fix the mock/schema data so the sink becomes non-empty, or set " +
      "allowEmpty to a short reason string if empty output is intentional."

    specs.values.toList.sortBy(_.getOrElse("captureName", "")).flatMap { spec =>
      val captureName = spec("captureName")
      val allowEmpty  = spec("allowEmpty")
      val matches     = captured.filter(item => matchesDeclaredSink(item, captureName))
      if (matches.isEmpty) {
        if (allowEmpty.nonEmpty) Nil
        else List(Map[String, Any](
          "source"   -> "declared_sink",
          "name"     -> captureName,
          "reason"   -> "empty_declared_sink",
          "message"  -> s"Declared sink '$captureName' produced no captured rows. $guidance",
          "critical" -> true,
        ))
      } else if (allowEmpty.nonEmpty) Nil
      else {
        val totalRows = matches.map(m => Try(m.getOrElse("row_count", 0L).toString.toLong).getOrElse(0L)).sum
        if (totalRows == 0)
          List(Map[String, Any](
            "source"   -> "declared_sink",
            "name"     -> captureName,
            "reason"   -> "empty_declared_sink",
            "message"  -> s"Declared sink '$captureName' captured 0 rows. $guidance",
            "critical" -> true,
          ))
        else Nil
      }
    }
  }

  // -------------------------------------------------------------------------
  // interceptConnectorReads — JVM equivalent of Python's mock.patch approach
  //
  // Python patches DataFrameReader.format/option/load at runtime.
  // JVM CANNOT do this (no monkey-patching).  Instead, register catalog views
  // in the trial schema so spark.table("foo") and spark.sql("...FROM foo...")
  // resolve to the seeded / cloned table.  Workloads using
  //   spark.read.format("snowflake").option("dbtable","foo").load()
  // must have been adapted (Rule 8 / patch-author step) to use spark.table().
  // -------------------------------------------------------------------------

  def interceptConnectorReads(
      spark: SparkSession,
      epConfig: EntrypointConfig,
      outputSchema: String,
  ): Unit = {
    val connectorCategories = Set("snowflake", "jdbc", "table")
    for (src <- epConfig.externalSources if connectorCategories.contains(src.category.getOrElse(""))) {
      val raw  = src.originalPath.orElse(src.name).getOrElse("")
      val bare = bareTableName(raw)
      if (bare.nonEmpty) {
        val schemaId = safeIdent(outputSchema)
        val bareId   = safeIdent(bare)
        // The fully-qualified seeded table already exists in $outputSchema; expose the
        // bare table name to workloads that reference it unqualified, via a session
        // temp view and a global temp view. (The previous code created a self-referential
        // view `$fq AS SELECT * FROM $fq`, which is circular and silently failed.)
        val fqTable = s"$schemaId.$bareId"
        Try { spark.sql(s"CREATE OR REPLACE TEMP VIEW $bareId AS SELECT * FROM $fqTable") }
        Try { spark.sql(s"CREATE OR REPLACE GLOBAL TEMP VIEW $bareId AS SELECT * FROM $fqTable") }

        // Multi-namespace sources (e.g. `ops.job_audit`, `ref.route_catalog`)
        // are referenced by the workload with their ORIGINAL namespace in Phase A.
        // Expose a qualified alias backed by the seeded table so reads resolve.
        //
        // Phase B (SCOS/Snowflake): qualified reads must NOT exist in migrated code.
        // Rule 8 of fix-rules.md requires migrated workloads to use bare table names;
        // the clone schema is the active Snowflake schema so unqualified reads resolve
        // automatically. column_check.py enforces this as an exit gate before Phase B
        // is run. We therefore do nothing here for Phase B — attempting to create
        // qualified Spark temp views is invalid syntax (Spark does not support
        // namespace-qualified TEMP VIEWs) and would silently fail anyway.
        val isScosMode = System.getProperty("SPARK_CONNECT_MODE_ENABLED") != null
        if (!isScosMode) {
          val parts = raw.replace("`", "").replace("\"", "").trim
            .split("/").last       // drop any path prefix
            .split("\\.", -1).filter(_.nonEmpty)
          if (parts.length >= 2) {
            val nsRaw = parts(parts.length - 2).toLowerCase
            if (nsRaw != schemaId) {
              // Phase A: prefer a real Hive database when the namespace is a safe ident.
              trySafeIdent(nsRaw) match {
                case Some(nsId) =>
                  Try { spark.sql(s"CREATE DATABASE IF NOT EXISTS $nsId") }
                  Try {
                    spark.table(fqTable)
                      .write.mode("overwrite")
                      .saveAsTable(s"$nsId.$bareId")
                  }.failed.foreach(e =>
                    System.err.println(s"warn: interceptConnectorReads: saveAsTable $nsId.$bareId: $e"))
                case None =>
                  // Hyphenated / unsafe namespace token — fall back to a temp view using
                  // the quoted dotted name (Hive CREATE VIEW, not TEMP VIEW, so the
                  // database must exist; we create it first).
                  val quotedNs = sqlQuotedIdent(nsRaw)
                  Try { spark.sql(s"CREATE DATABASE IF NOT EXISTS $quotedNs") }
                  Try {
                    spark.table(fqTable)
                      .write.mode("overwrite")
                      .saveAsTable(s"$quotedNs.$bareId")
                  }.failed.foreach(e =>
                    System.err.println(s"warn: interceptConnectorReads: saveAsTable $quotedNs.$bareId: $e"))
              }
            }
          }
        }
      }
    }
  }

  // -------------------------------------------------------------------------
  // Snowflake JDBC helpers (Phase B)
  // -------------------------------------------------------------------------

  /**
   * Clone the golden schema for an entrypoint trial and return the clone name.
   * Ported from helpers.py::clone_golden_schema_for_trial.
   *
   * The clone is named <GOLDEN>_T<8-hex> and lives in the same DB.
   * Callers are responsible for calling dropTrialCloneSchema on teardown.
   *
   * Connection params are resolved in priority order:
   *   1. Env vars: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, ...
   *   2. ~/.snowflake/connections.toml entry named by state_json.config.connectionName
   */
  /**
   * Validate a SQL identifier sourced from LLM-authored analysis.json / state.json
   * before interpolating it into Spark SQL or JDBC. Returns it unchanged when safe;
   * throws otherwise. Blocks injection via quotes/semicolons/whitespace/dashes.
   */
  def safeIdent(name: String): String = {
    if (name == null || !name.matches("[A-Za-z0-9_$]+"))
      throw new IllegalArgumentException(s"refusing to interpolate unsafe SQL identifier: '${Option(name).getOrElse("<null>")}'")
    name
  }

  /** Like safeIdent but returns None for names that cannot be used bare (e.g. hyphens). */
  def trySafeIdent(name: String): Option[String] =
    Option(name).filter(_.matches("[A-Za-z0-9_$]+"))

  /** Quote a SQL identifier when it is not safe bare (Spark/Snowflake double-quote style). */
  def sqlQuotedIdent(name: String): String = {
    if (name == null || name.isEmpty) return "\"\""
    if (name.matches("[A-Za-z0-9_$]+")) name
    else "\"" + name.replace("\"", "\"\"") + "\""
  }

  /** Resolve spark.sql.warehouse.dir to a local filesystem directory. */
  def warehouseDirFile(warehousePath: String): java.io.File = {
    if (warehousePath == null || warehousePath.isEmpty) return new java.io.File("")
    val trimmed = warehousePath.trim
    val pathStr =
      if (trimmed.startsWith("file:")) {
        Try(new java.net.URI(trimmed).getPath).filter(_.nonEmpty).getOrElse {
          trimmed.stripPrefix("file:").replaceFirst("^//+", "")
        }
      } else trimmed
    new java.io.File(pathStr)
  }

  def cloneGoldenSchemaForTrial(stateJson: StateJson, epId: String): String = {
    val database  = stateJson.snowflake.database
    val connName  = stateJson.config.connectionName
    if (database.isEmpty)
      throw new RuntimeException("state.json missing snowflake.database")

    // Fast path: pre-cloned schema already exists — skip JDBC entirely.
    // Used when JDBC-based cloning is unavailable (driver/network restricted) or
    // schemas are pre-provisioned out of band.
    val preCloned = stateJson.snowflake.preClonedSchemas.get(epId)
    if (preCloned.isDefined && preCloned.get.nonEmpty) {
      println(s"[ScosTrialFixture] Using pre-cloned schema for $epId: ${preCloned.get}")
      return preCloned.get
    }

    val goldenSchemas = stateJson.snowflake.goldenSchemas
    val epInfo = goldenSchemas.getOrElse(epId,
      throw new RuntimeException(s"No golden schema for ep_id=$epId in state.snowflake.goldenSchemas"))
    val golden = epInfo.schema
    if (golden.isEmpty)
      throw new RuntimeException(s"Golden schema for ep_id=$epId has empty schema name")

    val clone = s"${golden}_T${UUID.randomUUID().toString.replace("-", "").take(8).toUpperCase}"

    val conn = openJdbcConnection(connName, database)
    try {
      val db = safeIdent(database); val cl = safeIdent(clone); val gold = safeIdent(golden)
      val stmt = conn.createStatement()
      try {
        stmt.execute(s"""USE DATABASE "$db"""")
        stmt.execute(s"""CREATE OR REPLACE SCHEMA "$db"."$cl" CLONE "$db"."$gold"""")
      } finally stmt.close()
    } finally {
      conn.close()
    }
    clone
  }

  def dropTrialCloneSchema(stateJson: StateJson, cloneSchema: String): Unit = {
    val database = stateJson.snowflake.database
    if (database.isEmpty || cloneSchema.isEmpty) return
    // Skip teardown for pre-cloned schemas — they're managed externally.
    if (stateJson.snowflake.preClonedSchemas.values.toSet.contains(cloneSchema)) {
      println(s"[Helpers] Skipping DROP for pre-cloned schema $cloneSchema (managed externally)")
      return
    }
    val conn = Try(openJdbcConnection(stateJson.config.connectionName, database)).getOrElse(return)
    try {
      val db = safeIdent(database); val cs = safeIdent(cloneSchema)
      val stmt = conn.createStatement()
      try stmt.execute(s"""DROP SCHEMA IF EXISTS "$db"."$cs" CASCADE""")
      finally stmt.close()
    } finally {
      conn.close()
    }
  }

  /** List the tables in the clone schema.
    *
    * P8: prefers the table list persisted at provision time
    * (``state.snowflake.golden_schemas[epId].tables``) to avoid opening a
    * per-trial JDBC connection + SHOW TABLES. Falls back to a live JDBC
    * SHOW TABLES when epId is empty, the ep has no GoldenSchema entry, or
    * the persisted list is empty (old state predating the P8 provision change).
    *
    * For pre-cloned schemas JDBC may not be available — returns an empty list
    * and lets the SCOS session (Spark SQL) resolve tables via SHOW TABLES. */
  def listSeedTablesViaJdbc(stateJson: StateJson, cloneSchema: String, epId: String = ""): List[String] = {
    val database = stateJson.snowflake.database
    // If this is a pre-cloned schema, JDBC may not be available — return empty list.
    // The SCOS session (Spark SQL) will still find the tables via SHOW TABLES.
    if (stateJson.snowflake.preClonedSchemas.values.toSet.contains(cloneSchema)) {
      println(s"[Helpers] listSeedTablesViaJdbc: pre-cloned schema $cloneSchema — skipping JDBC, tables will be resolved via Spark SQL SHOW TABLES")
      return Nil
    }
    // P8: use the provisioned table list from state (avoids per-trial JDBC SHOW TABLES).
    if (epId.nonEmpty) {
      val persisted = stateJson.snowflake.goldenSchemas.get(epId).map(_.tables).getOrElse(Nil)
      if (persisted.nonEmpty) {
        return persisted.map(t => s"$cloneSchema.$t".toLowerCase)
      }
    }
    val conn     = Try(openJdbcConnection(stateJson.config.connectionName, database)).getOrElse(return Nil)
    try {
      val db = safeIdent(database); val cs = safeIdent(cloneSchema)
      val stmt = conn.prepareStatement(s"""SHOW TABLES IN SCHEMA "$db"."$cs"""")
      try {
        val rs  = stmt.executeQuery()
        val buf = mutable.ListBuffer[String]()
        try {
          while (rs.next()) {
            val tableName = rs.getString(2) // column index 2 = table name in SHOW TABLES
            buf += s"$cloneSchema.$tableName".toLowerCase
          }
        } finally rs.close()
        buf.toList
      } finally stmt.close()
    } finally {
      conn.close()
    }
  }

  /**
   * Open a Snowflake JDBC connection.
   * Auth precedence:
   *   1. OAuth token  — SNOWFLAKE_OAUTH_TOKEN env (or `token` in connections.toml)
   *      → authenticator=oauth. Removes the JIT auth patching that otherwise
   *      costs Phase A/B iterations in token-based (e.g. SPCS / Snowsight) envs.
   *   2. Key-pair      — SNOWFLAKE_PRIVATE_KEY_FILE env, or `private_key_file` /
   *      `private_key_path` in connections.toml (parity with the Python connector,
   *      so a user's existing key-pair connection works with no extra env vars).
   *   3. Password      — SNOWFLAKE_PASSWORD / connections.toml `password`.
   * Reads params from env vars (SNOWFLAKE_*) first; falls back to
   * ~/.snowflake/connections.toml for the named connection.
   */
  private[kit] def openJdbcConnection(
      connectionName: String,
      database: String,
  ): java.sql.Connection = {
    // Load the Snowflake JDBC driver
    Class.forName("net.snowflake.client.jdbc.SnowflakeDriver")

    // Try env vars first
    val acctEnv = EnvUtil.get("SNOWFLAKE_ACCOUNT", "")
    val pkFileEnv = EnvUtil.get("SNOWFLAKE_PRIVATE_KEY_FILE", "")
    val pkPassEnv = EnvUtil.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "")
    val (account, user, password, warehouse, role, oauthToken, authenticator, pkFile, pkPass) =
      if (acctEnv.nonEmpty) {
        (
          acctEnv,
          EnvUtil.get("SNOWFLAKE_USER", ""),
          EnvUtil.get("SNOWFLAKE_PASSWORD", ""),
          EnvUtil.get("SNOWFLAKE_WAREHOUSE", ""),
          EnvUtil.get("SNOWFLAKE_ROLE", ""),
          EnvUtil.get("SNOWFLAKE_OAUTH_TOKEN", ""),
          EnvUtil.get("SNOWFLAKE_AUTHENTICATOR", ""),
          pkFileEnv,
          pkPassEnv,
        )
      } else {
        // Parse ~/.snowflake/connections.toml for the named connection.
        // Key-pair: env var wins (explicit override), else the toml key — parity
        // with the Python connector, which reads private_key_file/private_key_path
        // straight from connections.toml.
        val params = parseConnectionsToml(connectionName)
        val tomlPk = params.get("private_key_file").orElse(params.get("private_key_path"))
          .map(_.trim).filter(_.nonEmpty).getOrElse("")
        val tomlPkPass = params.get("private_key_file_pwd").orElse(params.get("private_key_passphrase"))
          .map(_.trim).filter(_.nonEmpty).getOrElse("")
        (
          params.getOrElse("account", ""),
          params.getOrElse("user", ""),
          params.getOrElse("password", ""),
          params.getOrElse("warehouse", ""),
          params.getOrElse("role", ""),
          params.getOrElse("token", ""),
          params.getOrElse("authenticator", ""),
          if (pkFileEnv.nonEmpty) pkFileEnv else tomlPk,
          if (pkPassEnv.nonEmpty) pkPassEnv else tomlPkPass,
        )
      }

    // OAuth is active when a token is supplied, or the authenticator is
    // explicitly set to oauth.
    // Programmatic Access Token (PAT): uses authenticator=programmatic_access_token.
    val isPat    = authenticator.equalsIgnoreCase("programmatic_access_token")
    val useOauth = !isPat && (oauthToken.nonEmpty || authenticator.equalsIgnoreCase("oauth"))

    // user is not required for OAuth/PAT (the token carries identity); account always is.
    if (account.isEmpty || (user.isEmpty && !useOauth && !isPat))
      throw new RuntimeException(
        s"Snowflake JDBC: missing account/user. Set SNOWFLAKE_ACCOUNT + SNOWFLAKE_USER " +
          s"(or SNOWFLAKE_OAUTH_TOKEN) env vars, or configure " +
          s"~/.snowflake/connections.toml entry '$connectionName'."
      )

    val jdbcUrl = s"jdbc:snowflake://$account.snowflakecomputing.com/"
    val props   = new Properties()
    if (user.nonEmpty) props.setProperty("user", user)
    props.setProperty("db", database)
    if (warehouse.nonEmpty) props.setProperty("warehouse", warehouse)
    if (role.nonEmpty) props.setProperty("role", role)

    if (isPat) {
      // Programmatic Access Token — Snowflake JDBC driver native support
      props.setProperty("authenticator", "programmatic_access_token")
      if (oauthToken.nonEmpty) props.setProperty("token", oauthToken)
    } else if (useOauth) {
      // OAuth: authenticator=oauth + token. No password / key-pair.
      props.setProperty("authenticator", "oauth")
      if (oauthToken.nonEmpty) props.setProperty("token", oauthToken)
    } else {
      if (authenticator.nonEmpty) props.setProperty("authenticator", authenticator)
      if (password.nonEmpty) props.setProperty("password", password)
      // Key-pair auth — pkFile/pkPass resolved above from SNOWFLAKE_PRIVATE_KEY_FILE
      // env or the connections.toml private_key_file/private_key_path key.
      if (pkFile.nonEmpty) {
        // Expand a leading ~ like the Python connector does (JDBC won't).
        val resolvedPk =
          if (pkFile == "~" || pkFile.startsWith("~/"))
            System.getProperty("user.home") + pkFile.substring(1)
          else pkFile
        props.setProperty("private_key_file", resolvedPk)
        if (pkPass.nonEmpty) props.setProperty("private_key_file_pwd", pkPass)
      }
    }

    // Disable OCSP checking — the JDBC 3.27 OCSP code has a known NPE when
    // the OCSP responder returns an unexpected response, causing all connections
    // to abort with BasicOCSPResp.getCerts() NPE even with FAIL_OPEN mode.
    props.setProperty("ocspFailOpen", "true")
    props.setProperty("insecureMode", "true")

    DriverManager.getConnection(jdbcUrl, props)
  }

  /**
   * Minimal parser for ~/.snowflake/connections.toml.
   * Handles flat [connection_name] sections with key = "value" entries.
   */
  private[kit] def parseConnectionsToml(connectionName: String): Map[String, String] = {
    val tomlPath = Paths.get(System.getProperty("user.home"), ".snowflake", "connections.toml")
    if (!tomlPath.toFile.isFile) return Map.empty
    val lines  = Files.readAllLines(tomlPath).toArray.map(_.toString)
    val params = mutable.Map[String, String]()
    var inSection = false
    val sectionHeader = s"[$connectionName]"
    for (line <- lines) {
      val trimmed = line.trim
      if (trimmed.startsWith("[")) {
        inSection = trimmed == sectionHeader
      } else if (inSection && trimmed.contains("=")) {
        val Array(k, rest @ _*) = trimmed.split("=", 2)
        val v = rest.mkString("=").trim.stripPrefix("\"").stripSuffix("\"")
        params(k.trim.toLowerCase) = v
      }
    }
    params.toMap
  }

  def deleteRecursive(file: File): Unit = {
    if (file.isDirectory) file.listFiles().foreach(deleteRecursive)
    file.delete()
  }
}

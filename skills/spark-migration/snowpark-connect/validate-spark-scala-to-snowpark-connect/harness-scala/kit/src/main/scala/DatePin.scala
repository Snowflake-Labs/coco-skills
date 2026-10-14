// Ported from: validate-pyspark-to-snowpark-connect/scripts/harness/conftest.py
//              (_install_date_pin)
//
// DatePin: deterministic date pinning for SCOS A/B differential validation.
//
// Python approach:
//   F.current_date = lambda: F.to_date(F.lit(pinned))
//   F.current_timestamp = lambda: F.to_timestamp(F.lit(...))
//   This is monkey-patching pyspark.sql.functions — NOT possible on the JVM.
//
// JVM approach:
//   Register Spark SQL macros / SparkSessionExtensions that replace
//   current_date() and current_timestamp() with deterministic literals.
//   The pinned date is read from SCOS_PINNED_DATE env var (or today's date).
//   Set SCOS_PIN_DATE_DISABLED=1 to skip entirely.
//
//   Implementation: configure the SparkSession with a custom deterministic
//   UDF registered as 'current_date' and 'current_timestamp' overrides using
//   spark.udf.register.  Workloads that call F.current_date() go through the
//   SparkSession catalog and will resolve to our registered UDFs when invoked
//   via spark.sql expressions.
//
//   LIMITATION: scala.org.apache.spark.sql.functions.current_date() is a
//   compiled Catalyst expression and cannot be replaced via UDF registration.
//   Workloads must use spark.sql("SELECT current_date()") or the patch-author
//   step must wrap calls in a deterministic column expression.
//   Document this limitation with a // SCOS: TODO comment in adapted code.

package com.snowflake.scos.kit

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.functions.{lit, to_date, to_timestamp}

import java.time.LocalDate

object DatePin {

  /**
   * Install deterministic date overrides on the SparkSession.
   *
   * Reads SCOS_PINNED_DATE (ISO-8601, e.g. "2024-01-15") from env;
   * defaults to today's date.  Disabled when SCOS_PIN_DATE_DISABLED=1.
   *
   * Registers Spark SQL UDFs:
   *   scos_pinned_date()       -> DateType literal
   *   scos_pinned_timestamp()  -> TimestampType literal
   *
   * Also sets Spark conf "spark.scos.pinned_date" so the value is
   * accessible from within workloads/UDFs that read it via
   *   spark.conf.get("spark.scos.pinned_date")
   */
  def install(spark: SparkSession): Unit = {
    if (EnvUtil.get("SCOS_PIN_DATE_DISABLED", "") == "1") return

    val pinned = {
      val envDate = EnvUtil.get("SCOS_PINNED_DATE", "")
      if (envDate.nonEmpty) envDate else LocalDate.now().toString
    }

    // Store in Spark conf so workloads can read it via spark.conf.
    spark.conf.set("spark.scos.pinned_date", pinned)
    spark.conf.set("spark.scos.pinned_timestamp", s"$pinned 00:00:00")
    // Also publish as system properties so workloads patched to
    // System.getProperty("SCOS_PINNED_DATE") see the value even when it
    // originated from System.getenv (which EnvUtil.get reads but
    // System.getProperty does not cover).
    System.setProperty("SCOS_PINNED_DATE", pinned)
    System.setProperty("SCOS_PINNED_TIMESTAMP", s"$pinned 00:00:00")

    // Register deterministic SQL UDFs.
    // These are called as scos_pinned_date() / scos_pinned_timestamp() in SQL.
    // Patch-author edits should rewrite current_date() → scos_pinned_date() where
    // determinism is required.
    spark.udf.register("scos_pinned_date",      () => java.sql.Date.valueOf(pinned))
    spark.udf.register("scos_pinned_timestamp", () => java.sql.Timestamp.valueOf(s"$pinned 00:00:00"))

    // NOTE: Built-in Catalyst current_date / current_timestamp cannot be replaced
    // via public API. For spark.sql("... current_date() ...") strings, call
    // DatePin.rewriteSql(query) before execution, or rely on patch-author to rewrite
    // workload calls to scos_pinned_date() / DATE'…' literals (PySpark install_sql_date_pin
    // wraps spark.sql in Python; JVM workloads use rewriteSql or patch-author).
  }

  /**
   * Textually pin ``current_date`` / ``current_timestamp`` in a SQL string
   * (PySpark ``install_sql_date_pin`` parity). Use when wrapping ``spark.sql``
   * calls from the harness or from patched workloads.
   */
  def rewriteSql(query: String, spark: SparkSession): String = {
    if (query == null || query.isEmpty) return query
    if (EnvUtil.get("SCOS_PIN_DATE_DISABLED", "") == "1") return query
    val pinned = spark.conf.getOption("spark.scos.pinned_date")
      .orElse(Option(EnvUtil.get("SCOS_PINNED_DATE", "")).filter(_.nonEmpty))
      .getOrElse(LocalDate.now().toString)
    val dateLit = s"DATE'$pinned'"
    val tsLit = s"TIMESTAMP'$pinned 00:00:00'"
    // timestamp before date so current_timestamp is not partially matched
    val withTs = raw"(?i)\bcurrent_timestamp\b\s*(?:\(\s*\))?".r.replaceAllIn(query, tsLit)
    raw"(?i)\bcurrent_date\b\s*(?:\(\s*\))?".r.replaceAllIn(withTs, dateLit)
  }

  /**
   * Convenience: return the pinned date as a Column literal for use in
   * patched workloads:
   *   df.withColumn("run_date", DatePin.pinnedDateCol(spark))
   */
  def pinnedDateCol(spark: SparkSession) = {
    val pinned = spark.conf.getOption("spark.scos.pinned_date")
      .getOrElse(LocalDate.now().toString)
    to_date(lit(pinned))
  }

  def pinnedTimestampCol(spark: SparkSession) = {
    val pinned = spark.conf.getOption("spark.scos.pinned_timestamp")
      .getOrElse(s"${LocalDate.now()} 00:00:00")
    to_timestamp(lit(pinned))
  }
}

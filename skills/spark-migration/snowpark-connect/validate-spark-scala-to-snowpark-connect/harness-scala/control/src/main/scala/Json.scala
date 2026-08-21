// com.snowflake.scos.validate.Json
// After the JVM→Python control-plane move all helpers (appendEvent, safeIdent,
// resolveSchema, bareTableName, writeAtomic, loadFile, ensureEntrypointsList,
// projectSlug, runId) are now the Python source of truth in the canonical
// PySpark validator scripts (validate.py, provision.py). Only `die` is still
// referenced by ScosAnalyze / Main.

package com.snowflake.scos.validate

object Json {

  /** Terminate the JVM with a message on stderr. */
  def die(code: Int, msg: String): Nothing = {
    System.err.println(s"[scos-control] error: $msg")
    sys.exit(code)
  }
}

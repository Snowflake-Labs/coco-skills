// com.snowflake.scos.validate.Main
// Entry point for the scos-analyze fat-jar.
//
// The validator's control plane now reuses the canonical PySpark validator
// scripts (validate.py, provision.py/cleanup.py, harness/comparator.py). The only command
// that still needs the JVM is `analyze` — a Scalameta AST facts extractor, since
// there is no equivalent Scala parser in Python.
//
// Usage:
//   java -jar scos-analyze.jar analyze --source <file-or-dir> [--output <path>]

package com.snowflake.scos.validate

object Main {

  def main(args: Array[String]): Unit = {
    args.headOption match {
      case Some("analyze") =>
        ScosAnalyze.main(args.drop(1))
      case other =>
        System.err.println(
          "Usage: java -jar scos-analyze.jar analyze --source <file-or-dir> [--output <path>]\n" +
          other.map(c => s"Unknown command: $c").getOrElse("No command given") +
          "\n(state/provision/cleanup/compare/snapshot now run via the Python scripts.)"
        )
        sys.exit(2)
    }
  }
}

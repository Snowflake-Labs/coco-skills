// com.snowflake.scos.javaparser.ScosJavaRunner
//
// Dispatcher entry-point for the SCOS Java Phase 0.5c AST tool.
// Analog of the sbt wrapper that invokes scalafix.cli.Cli on the Scala path.
//
// Subcommands:
//   facts   --source <file-or-dir> [--output <path>]
//               Walk .java files, emit JSON facts (mirrors ScosMigrateFacts.scala output).
//   rewrite --source <file> --rule <RuleName> [--stdout]
//               Apply one named rule and print rewritten file to stdout.
//   rewrite --list-rules
//               Print all rule names (one per line) for the Python driver.
//
// Invoked by preprocess_javaparser.py as:
//   java -jar scripts/javaparser_maven/target/scos-javaparser-runner.jar <subcommand> [args...]
// or with explicit class:
//   java -cp <jar> com.snowflake.scos.javaparser.ScosJavaFacts   --source ...
//   java -cp <jar> com.snowflake.scos.javaparser.ScosJavaRewrite --source ... --rule ...
package com.snowflake.scos.javaparser;

import java.util.Arrays;

public class ScosJavaRunner {

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            printUsage();
            System.exit(2);
        }
        String sub  = args[0];
        String[] rest = Arrays.copyOfRange(args, 1, args.length);
        switch (sub) {
            case "facts":   ScosJavaFacts.main(rest);   break;
            case "rewrite": ScosJavaRewrite.main(rest); break;
            default:
                System.err.println("[scos-runner] unknown subcommand: " + sub);
                printUsage();
                System.exit(2);
        }
    }

    private static void printUsage() {
        System.err.println("Usage: scos-javaparser-runner <subcommand> [options]");
        System.err.println("  facts   --source <file-or-dir> [--output <path>]");
        System.err.println("  rewrite --source <file> --rule <RuleName> [--stdout]");
        System.err.println("  rewrite --list-rules");
    }
}

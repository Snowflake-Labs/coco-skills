// com.snowflake.scos.validate.Main
// Entry point for the scos-analyze-java fat-jar.
//
// Usage:
//   java -jar scos-analyze-java.jar analyze --source <file-or-dir> [--output <path>]

package com.snowflake.scos.validate;

public class Main {

    public static void main(String[] args) {
        if (args.length == 0) {
            System.err.println(
                "Usage: java -jar scos-analyze-java.jar analyze --source <file-or-dir> [--output <path>]\n" +
                "No command given\n" +
                "(state/provision/cleanup/compare/snapshot now run via the Python scripts.)");
            System.exit(2);
        }

        String command = args[0];
        if ("analyze".equals(command)) {
            String[] rest = new String[args.length - 1];
            System.arraycopy(args, 1, rest, 0, rest.length);
            ScosAnalyzeJava.main(rest);
        } else {
            System.err.println(
                "Usage: java -jar scos-analyze-java.jar analyze --source <file-or-dir> [--output <path>]\n" +
                "Unknown command: " + command + "\n" +
                "(state/provision/cleanup/compare/snapshot now run via the Python scripts.)");
            System.exit(2);
        }
    }
}

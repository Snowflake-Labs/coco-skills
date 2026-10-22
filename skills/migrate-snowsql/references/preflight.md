# Preflight

Load this page at the start of every migration, and again before connection import.

## Contents

- Record
- SnowSQL version
- Snowflake CLI version and importer
- Old importer

## Record

Record OS, `snowsql --version`, and `snow --version`. Confirm `snow` is Snowflake CLI v3.30 or later. This skill supports v3.30 development builds and later releases. Find project files and config locations with `snow --info` (`default_config_file_path`). Do not print secret values. Leave SnowSQL installed and do not edit its config.

## SnowSQL version

Take the first `X.Y.Z` from `snowsql --version`. Continue only for 1.4 and 1.5. The [client support policy](https://docs.snowflake.com/en/release-notes/requirements) sets the minimum at 1.4.0 and recommends 1.5.0 or later. Confirm current end dates on the [SnowSQL support dates](https://docs.snowflake.com/en/user-guide/snowsql) page.

| Text | Decision |
| --- | --- |
| `1.2.32`, `1.3.3`, no version | Stop. Upgrade to 1.4 or 1.5 before migrating. 1.5 is the recommended line. |
| `1.4.0`, `1.4.5`, `1.5.0`, `1.5.2` | Continue |

1.4 and 1.5 use the same rules. Release notes from 1.4.0 through 1.5.2 do not change scripts, config files, variables, output, exit codes, or directives. 1.4.0 adds the OAuth authorization-code and client-credentials connection parameters. 1.4.5 adds `workload_identity_provider` and authenticator `WORKLOAD_IDENTITY`. Those keys follow the existing connection rules.

## Snowflake CLI version and importer

Refuse `snow helpers import-snowsql-connections` unless both checks below pass. Read the command output and decide. Do not write a script to decide it.

Version. Run `snow --version` and take the first `X.Y.Z` or `X.Y.Z.devN`. A `.devN` build is older than the final release with the same three numbers. The minimum supported version is `3.30.0.dev0`.

| Text | Decision |
| --- | --- |
| `3.29.1`, `3.29.9.dev4`, no version, or a SnowSQL version | Stop |
| `3.30.0.dev0`, `3.30.0.dev17`, `3.30.0`, `3.31.0.dev2`, `3.31.0` | This half passes |

Snowflake CLI v3.30 corrects `snow helpers import-snowsql-connections`. Development builds identified as `3.30.0.devN` are supported before the final release.

`!spool` is in Snowflake CLI 3.31.0, including `3.31.0.dev0` from `main`. A `3.30` build does not have it. Follow the spool rule in the compatibility reference.

Help. Run `snow helpers import-snowsql-connections --help`. Ignore box-drawing characters and line breaks. The text must contain both `does not replace the default file list` and `not copied onto it`.

If either check fails, or the command is missing, say this install does not have the v3.30 importer and stop the import.

## Old importer

<details>
<summary>Old importer help text</summary>

Earlier helper text says a custom config file is imported instead of the default list. That is the old importer. Stop the import.

</details>

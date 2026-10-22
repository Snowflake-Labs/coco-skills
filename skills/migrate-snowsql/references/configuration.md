# Configuration track

Snowflake CLI keeps its own TOML files. SnowSQL keeps its INI files. Installing both is the rollback path. This track is a one-time copy of connection sections. It is not continuous sync, not runtime SnowSQL compatibility, and not config-v2 behavior.

## Contents

- Version gate
- Read-only environment check
- One-time import
- Files, in precedence order
- Merge, aliases, and what is copied
- Types and JWT
- Rollback

Published references:

- [Migrating from SnowSQL to Snowflake CLI](https://docs.snowflake.com/en/user-guide/snowsql-migrate)
- [snow helpers import-snowsql-connections](https://docs.snowflake.com/en/developer-guide/snowflake-cli/command-reference/helpers-commands/import-snowsql-connections)
- [snow helpers check-snowsql-env-vars](https://docs.snowflake.com/en/developer-guide/snowflake-cli/command-reference/helpers-commands/check-snowsql-env-vars)

The corrected importer behavior below is provided by Snowflake CLI v3.30 and later, including v3.30 development builds. Earlier CLI versions have the older importer.

## Version gate

Refuse the import unless Preflight passed. Do not re-run the decision tables. If Preflight stopped, or the importer command is missing, the import is refused.

## Read-only environment check

Run `snow helpers check-snowsql-env-vars` before any import. It prints variable names and suggestions. It does not change the environment and it does not print values. `SNOWSQL_*` variables are not normal Snowflake CLI runtime connection variables. The default connection path does not read them, including `SNOWSQL_REGION` and `SNOWSQL_PRIVATE_KEY_PASSPHRASE`.

| SnowSQL name | Suggestion | Disposition |
| --- | --- | --- |
| `SNOWSQL_ACCOUNT` | `SNOWFLAKE_ACCOUNT` | rewrite required |
| `SNOWSQL_USER` | `SNOWFLAKE_USER` | rewrite required |
| `SNOWSQL_PWD` | `SNOWFLAKE_PASSWORD` | rewrite required |
| `SNOWSQL_DATABASE` | `SNOWFLAKE_DATABASE` | rewrite required |
| `SNOWSQL_SCHEMA` | `SNOWFLAKE_SCHEMA` | rewrite required |
| `SNOWSQL_WAREHOUSE` | `SNOWFLAKE_WAREHOUSE` | rewrite required |
| `SNOWSQL_ROLE` | `SNOWFLAKE_ROLE` | rewrite required |
| `SNOWSQL_HOST` | `SNOWFLAKE_HOST` | rewrite required |
| `SNOWSQL_PORT` | `SNOWFLAKE_PORT` | rewrite required |
| `SNOWSQL_PROTOCOL` | `SNOWFLAKE_PROTOCOL` | rewrite required |
| `SNOWSQL_REGION` | account identifier in `SNOWFLAKE_ACCOUNT`, not the region name | manual review |
| `SNOWSQL_PROXY_HOST` | `HTTPS_PROXY` or `HTTP_PROXY` host | manual review |
| `SNOWSQL_PROXY_PORT` | `HTTPS_PROXY` or `HTTP_PROXY` port | manual review |
| `SNOWSQL_PROXY_USER` | `HTTPS_PROXY` or `HTTP_PROXY` username | manual review |
| `SNOWSQL_PROXY_PWD` | `HTTPS_PROXY` or `HTTP_PROXY` password | manual review |
| `SNOWSQL_PRIVATE_KEY_PASSPHRASE` | `PRIVATE_KEY_PASSPHRASE` | rewrite required |

A generic `SNOWFLAKE_*` variable fills a parameter only when a flag and the selected connection did not set it. `SNOWFLAKE_CONNECTIONS_<NAME>_<KEY>` replaces one key of a saved connection. `<NAME>` is the connection name in uppercase. `<KEY>` is the CLI key, such as `ACCOUNT`, not `ACCOUNTNAME`. `PRIVATE_KEY_PASSPHRASE` wins over a passphrase stored on the connection. `SNOWFLAKE_ENHANCED_EXIT_CODES=1` enables `--enhanced-exit-codes`. It is not a connection variable and it is not SnowSQL `EXIT_ON_ERROR`.

## One-time import

Command: `snow helpers import-snowsql-connections`.

- Copies connection sections into Snowflake CLI config and stops.
- Does not copy `[variables]`, `[connection_options]`, or environment variables.
- Does not watch SnowSQL afterward.
- Is not part of normal startup.
- Two runs on the same files agree, aside from overwrite prompts and a renamed default.
- A missing custom `--snowsql-config-file` fails the command and writes nothing.
- A file that exists but cannot be read fails before anything is written. The error names the path and does not repeat file contents.

Back up the current CLI config first (`config.toml`, and `connections.toml` when that file exists). Then ask for a separate confirmation. After the import, show connection names and key names only. Values are `<redacted>`.

`default_connection_name` is set only from the unnamed `[connections]` section, under the name that section was actually saved with (`--default-connection-name`, default `default`). A section that is only `[connections.default]` does not become the CLI default. Declining the overwrite of the unnamed section leaves the existing connection and `default_connection_name` unchanged.

If `connections.toml` exists, connections are written there as top-level `[name]` sections. `default_connection_name` stays in `config.toml`. While `connections.toml` exists, `[connections.name]` entries in `config.toml` are ignored.

The overwrite prompt defaults to no.

## Files, in precedence order

Missing default files are skipped. The packaged SnowSQL `snowsql.cnf` template is not on this list.

1. `/etc/snowsql.cnf`
2. `/etc/snowflake/snowsql.cnf`
3. `/usr/local/etc/snowsql.cnf`
4. `$HOME/.snowsql.cnf`
5. `$HOME/.snowsql/config`
6. Each `--snowsql-config-file`, in the order given
7. `/usr/lib64/snowflake/snowsql/config` last, when it exists

Custom files merge after the defaults. They do not replace the list. The RPM path still overrides the same keys.

When `WORKSPACE` is set to a non-empty value, only the two user paths (`~/.snowsql.cnf` and `~/.snowsql/config`) are read from that directory. An empty or whitespace-only `WORKSPACE` is unset. The `/etc` paths, custom files, and the RPM path do not move.

On Windows, `~/.snowsql.cnf` and `~/.snowsql/config` still use the user profile unless `WORKSPACE` is set. The importer does not add a program-data directory. If the SnowSQL file is somewhere else, pass `--snowsql-config-file`.

Files are read as UTF-8. A file larger than 128 MB is rejected. `#` starts a comment. An unquoted comma list stays one string (`token = a, b`). One matching quote pair is stripped. A `%` stays literal. Backslashes inside quotes stay as written. This is the SnowSQL 1.4 and 1.5 read, not a Python literal: `password = 123456` stays a string. The 1.5 release notes do not change it.

## Merge, aliases, and what is copied

Raw keys merge before alias resolution. A key in a later file replaces that same raw key from an earlier file. A key that appears only in an earlier file is kept. A later blank on the same raw key clears the earlier value.

A named connection is only the keys in its `[connections.<name>]` section. Keys from the unnamed `[connections]` section are not copied onto it. SnowSQL often puts the account only on the unnamed section. Put the account on the named connection, or use the imported default connection.

An empty `[connections.name]` header is skipped for that file only. It does not erase keys that another file set for the same name.

Aliases, after the raw merge. The first non-blank name wins:

| Saved key | Names, in order |
| --- | --- |
| `account` | `account`, then `accountname` |
| `user` | `user`, then `username` |
| `database` | `database`, then `dbname`, then `databasename` |
| `schema` | `schema`, then `schemaname` |
| `warehouse` | `warehouse`, then `warehousename` |
| `role` | `role`, then `rolename` |
| `password` | `password`, then `pwd` |
| `private_key_file` | `private_key_path`, then `private_key_file` |
| `private_key_passphrase` | `private_key_passphrase`, then `private_key_file_pwd` |
| `proxy_password` | `proxy_password`, then `proxy_pwd` |

`private_key_path` wins over `private_key_file` when it has a value, and is saved as `private_key_file`. `databasename` is a last-resort alias, not a SnowSQL key. A blank or whitespace-only value is skipped, so the next name is used. `user=` with `username=alice` is saved as `user = "alice"`.

A blank final value is removed before `[options]` fills a missing key, so the CLI can prompt or use an environment variable. Boolean `false` and integer `0` stay. A section with nothing left is not saved and does not pick up `[options]`.

From `[options]`, and only when the connection omits that key:

- `login_timeout`
- `client_session_keep_alive`
- `ocsp_fail_open`
- `insecure_mode`
- `client_store_temporary_credential`

A key already set on the connection wins, including `0` and `false`. Other `[options]` settings, including `log_level`, `timing`, and `output_format`, stay unimported. `[variables]` stays unimported.

## Types and JWT

Passwords stay strings. `port` and `proxy_port` stay strings.

Integers: `login_timeout`, `client_prefetch_threads`. A value that does not parse, such as `30s`, stays text.

Booleans when the value is `true`, `false`, `yes`, `no`, `on`, `off`, `1`, or `0`:

- `client_session_keep_alive`
- `client_store_temporary_credential`
- `ocsp_fail_open`
- `insecure_mode`
- `oauth_disable_pkce`
- `oauth_enable_refresh_tokens`
- `oauth_enable_single_use_refresh_tokens`
- `disable_request_pooling`

Any other value stays text. The connector treats a non-empty string as true for several of these, so a leftover `"false"` is not the boolean false. The four `oauth_*` and `disable_request_pooling` keys are coerced only when the connection section sets them. They are not copied from `[options]`.

A private key is kept only when the authenticator is absent or JWT. The JWT check ignores case, so `snowflake_jwt` keeps the key. An absent authenticator becomes `SNOWFLAKE_JWT`. If a non-JWT authenticator survives the merge, that authenticator is kept and `private_key_file` and `private_key_passphrase` are omitted. The command prints which connection dropped the key. It does not print the key.

`proxy_host`, `proxy_port`, `proxy_user`, and `proxy_password` are copied. `region` is stored as `region` and is not rewritten into `host`. An imported `proxy_host` is what the connector uses. `HTTPS_PROXY` and `HTTP_PROXY` apply when that saved `proxy_host` is absent.

Every other connection key is copied under the same name.

## Rollback

1. Leave the SnowSQL binary and the SnowSQL config files untouched.
2. If you added a shell alias `snowsql='snow sql'`, remove that alias. The alias does not make `cat file | snowsql` run the file.
3. Restore the CLI backup taken before the import (`config.toml` and `connections.toml`).
4. Keep using the original scripts with the `snowsql` binary until the report says each file is accepted.

`snow --info` shows `default_config_file_path` for this install. `$SNOWFLAKE_HOME` is used when it is set and the directory exists. When it is unset, the directory is `~/.snowflake` if that directory exists, otherwise `~/.config/snowflake` on Linux, `~/Library/Application Support/snowflake` on macOS, or `%USERPROFILE%\AppData\Local\snowflake` on Windows. A `SNOWFLAKE_HOME` value that points at a missing directory is ignored, and the CLI does not then use `~/.snowflake`. `--config-file` selects a `config.toml`. It does not move `connections.toml`.

# Setup

Works with CoCo CLI on macOS and Linux (on Windows, use WSL). Do not enable the plugin on
native Windows. `python3` 3.9 or newer must be on the `PATH` of the shell CoCo uses to run
hooks; if it is not, every shell command is blocked.

`<plugin directory>` below is the folder CoCo installed the plugin into, or the path you
gave to `--plugin-dir`.

## 1. Start OpenShell

1. Install [NVIDIA OpenShell](https://docs.nvidia.com/openshell/latest/) and start a gateway.
2. Check it from your terminal:

   ```
   openshell status
   ```

The first shell command the agent runs in a project creates a sandbox for it. The name is
`sbx-`, the first six characters of the project name (lower case, other characters
replaced by `-`), and the first eight characters of a SHA-256 hash of the project path,
for example `sbx-my-pro-1aad139b`.

The default OpenShell sandbox is a minimal Ubuntu 24.04. It has no `python3`, no `git`
and no network access, and its system directories are read-only. To use your own tools,
set `openshell.from` to an image that has them and `openshell.policy` to a policy file
that allows the hosts you need (see [Configuration](#3-configuration)).

## 2. Install the plugin

From the project's repository, in your terminal:

```
cortex plugin install zozo123/sandboxed-shell/plugins/sandboxed-shell
```

Or, in a CoCo session:

```
Install the plugin at https://github.com/Snowflake-Labs/coco-skills/tree/main/plugins/sandboxed-shell
```

Or start CoCo with a local copy, after checking it:

```
cortex plugin validate /absolute/path/to/plugins/sandboxed-shell
cortex --plugin-dir /absolute/path/to/plugins/sandboxed-shell
```

Check the setup from your own terminal, in your project directory:

```
python3 <plugin directory>/bin/sbx.py doctor
```

Then ask the agent to run `uname -s`. The answer is `Linux`.

Start sessions inside a project directory. The runner refuses to use your home directory,
or a parent of it, as the project.

## 3. Configuration

All settings are optional and live in one user-level file,
`~/.config/sandboxed-shell/config.json` (`$XDG_CONFIG_HOME/sandboxed-shell/config.json` when
that variable is set). The runner reads no settings file from the
project directory. It does read the environment variables listed below, so do not start a
session from a shell that loads variables from the repository (direnv, a dotenv plugin).

```json
{
  "backend": "openshell",
  "exclude": ["dist", "*.parquet"],
  "secrets": ["sa-*.json"],
  "allow": ["profiles.yml"],
  "max_file_mb": 200,
  "openshell": {
    "gateway": "my-gateway",
    "from": "registry.example.com/dev-image:latest",
    "policy": "policy.yaml",
    "providers": ["github"]
  }
}
```

| Key | Meaning | Default |
|---|---|---|
| `backend` | `openshell` or `auto` (the same) | `auto` |
| `exclude` | More names that are not copied in either direction | built-in list |
| `secrets` | More name patterns that never leave your machine | built-in list |
| `allow` | Names to copy even though a built-in list matches them | none |
| `max_file_mb` | Larger files are not copied in either direction (MiB) | `200` |
| `permission` | `allow` approves rewritten commands without a prompt. `ask` asks for a prompt on every command. Unset keeps your normal permission flow | unset |
| `openshell.gateway` | Gateway name to use | the active gateway |
| `openshell.from`, `openshell.policy`, `openshell.providers` | Image, policy file and credential providers for new sandboxes. A relative policy path is read next to the settings file | OpenShell defaults |

Names match at any depth and ignore letter case. Unknown keys are ignored. A wrong type
for `exclude`, `secrets`, `allow`, `max_file_mb` or `openshell` is reported as a settings
error, and commands return 125 until it is fixed. Values inside `openshell` are not
checked. A `permission` value other than `allow` or `ask` is ignored.

Set `"permission": "allow"` only if you accept that every shell command the agent issues
is approved without a prompt.

Changes to `exclude`, `secrets`, `allow` and `max_file_mb` apply from the next command.
Image, policy and providers are set when a sandbox is created. After changing one of those, delete the sandbox (`sbx.py down --delete`); the
next command creates a new one.

Environment variables for one-off use: `SBX_BACKEND`, `SBX_CONFIG` (path of the settings
file), `SBX_NAME` (sandbox name; OpenShell accepts at most 19 characters), `SBX_STATE_DIR`
(where mirror state is kept, default `~/.cache/sandboxed-shell`; each sandbox's folder in
it is readable by you only). The runner also follows `XDG_CONFIG_HOME` (settings file) and
`XDG_CACHE_HOME` (mirror state), and takes the project directory from
`CORTEX_PROJECT_DIR`, which CoCo sets.

### Snowflake credentials for commands in the sandbox

Do not copy a password or token into the sandbox. An OpenShell provider keeps the stored
secret in the gateway and gives the sandbox a placeholder. The project repository has a
Snowflake provider profile, tested with CoCo itself running inside OpenShell
(`openshell/` in https://github.com/zozo123/sandboxed-shell). It does not work for `snow`
or `dbt`, which compress their sign-in request. A command that logs in to Snowflake still
receives a session token it can use.

`profiles.yml` and `connections.toml` are not copied by default. If your `profiles.yml`
reads its credentials from environment variables and holds no secret, add it to `allow`.

## 4. Day to day

Run these in your own terminal, from the project directory:

```
python3 <plugin directory>/bin/sbx.py status           # backend, sandbox name, state
python3 <plugin directory>/bin/sbx.py sync             # create or start the sandbox if needed, then copy both ways
python3 <plugin directory>/bin/sbx.py down             # stop the sandbox, keep its files
python3 <plugin directory>/bin/sbx.py down --delete    # remove it; the next command starts fresh
```

`down` reports an error if OpenShell refuses. The runner checks the sandbox before every
command.

## Enforce it for a team

A user can switch a plugin off. To leave the agent with no shell tool except the sandbox,
an administrator can combine two documented CoCo controls:

1. **Deny the built-in shell tool** with managed settings. See
   [`managed/managed-settings.example.json`](managed/managed-settings.example.json).
2. **Deploy the bundled MCP server** to every user through `files.mcpFile`. See
   [`managed/mcp.example.json`](managed/mcp.example.json). Copy the plugin folder to a
   root-owned path on each device (the example uses `/opt/sandboxed-shell`); the server
   needs `mcp/server.py` and `bin/sbx.py` in their places. The `mcpFile` path in the
   example is for macOS; on Linux set it to where you put the file, for example
   `/etc/cortex/mcp.json`.

The server exposes two tools. `sandbox_exec` runs a command through the same runner, with
a default time limit of 600 seconds; it returns the output and error output together,
cut to the last 200,000 characters. `sandbox_status` shows the sandbox for the project.

CoCo 1.2.46 starts MCP servers in the folder it was launched from. It passes them neither
the project folder given with `-w` nor your environment variables. So start CoCo inside the
project folder; the tool's description names the folder it copies. If you use an OpenShell
gateway other than the active one, or a settings file in another place, set
`OPENSHELL_GATEWAY`, `XDG_CONFIG_HOME` or `SBX_CONFIG` in the server's `env` block in the
MCP file.

This limits the agent, not the user: the runner trusts the user's `PATH` and settings
file. Other MCP servers and plugins that run commands are not affected. CoCo's own
control for the same need is `settings.forceSandboxEnabled`. CoCo 1.2.46 also has
`cortex managed-settings disable`, which turns managed enforcement off for that CLI on
that device.

What was tested: the managed-settings example passes CoCo's own validator
(`cortex managed-settings encode managed/managed-settings.example.json`), and in CoCo
1.2.46 the same two controls, given as `--disallowed-tools bash` and `--mcp-config`, left
the agent without a shell tool and with `sandbox_exec` working. The files have not been
deployed on a managed device. Roll them out to one device first and check with
`cortex managed-settings status`.

Managed settings file locations: `/Library/Application Support/Cortex/managed-settings.json`
(macOS), `/etc/cortex/managed-settings.json` (Linux and WSL).

## Troubleshooting

| What you see | Cause | What to do |
|---|---|---|
| `exit code 125` and "the 'openshell' command is not installed" | OpenShell is not installed or not on `PATH` | Install OpenShell and start a gateway |
| `OpenShell is not reachable` | No gateway is running, or the wrong one is active | `openshell status`; set `openshell.gateway` |
| `OpenShell is not reachable` from `sandbox_exec` only | CoCo starts MCP servers without your environment variables | Set `OPENSHELL_GATEWAY` (and `XDG_CONFIG_HOME` if you use one) in the server's `env` block |
| A command rule such as `bash(git *)` stopped matching | CoCo checks rules against the rewritten command | Allow or deny the whole `bash` tool, or use the team setup |
| `is your home directory or a parent of it` | The session was started in your home directory | Start it inside a project directory |
| `command not found` for a tool you have locally | The sandbox is a separate Linux machine | Use an image that has the tool (`openshell.from`) |
| Network calls fail in the sandbox | The sandbox policy denies the destination | Add it to your OpenShell policy |
| A file did not appear locally | Its name is on a not-copied list, or the change was refused | For refused changes the runner prints up to eight paths. For names on the not-copied lists it prints nothing. Rename the file, or edit it with the edit tool |
| "the sandbox was replaced while the command was running" | The sandbox was deleted during a command | Run the command again if that is safe |
| "the command is longer than 100000 bytes" | One command cannot be passed as a single argument | Put it in a script file in the project and run that |
| First command in a project is slow | The sandbox is created and the project copied once | Later commands only copy changes |

## Remove

```
python3 <plugin directory>/bin/sbx.py down --delete    # in each project you used
/plugin disable sandboxed-shell
rm -rf ~/.cache/sandboxed-shell ~/.config/sandboxed-shell
```

Use `/plugin uninstall sandboxed-shell` instead of `disable` if you installed the plugin yourself.

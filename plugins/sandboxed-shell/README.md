# Sandboxed shell

Runs the agent's shell commands in an [NVIDIA OpenShell](https://docs.nvidia.com/openshell/latest/)
sandbox instead of on your machine.

Shell commands that an agent runs have the same access to your machine as you do. With
this plugin they run in a sandbox that holds a copy of the project directory and nothing
else from your machine. OpenShell is open source (Apache-2.0) and runs on your machine or
your own hosts, so the project does not leave them.

CoCo CLI has two sandbox options of its own. This plugin is a third, for teams that use
OpenShell, and it replaces neither:

| Option | Where a command runs | What it works on |
|---|---|---|
| `/sandbox` (experimental, off by default) | On your machine, under the operating system's sandbox (`sandbox-exec`, `bubblewrap` or restricted tokens) | Your working directory |
| `cortex --cloud` (generally available) | In a Snowflake-managed container | A mounted workspace, not your local checkout |
| This plugin | In an OpenShell sandbox, a separate Linux environment run by your OpenShell gateway | A copy of your local checkout; changed files come back |

Pick the plugin when your team already runs OpenShell and wants one sandbox and one policy
across its agents, or when commands should see a copy of the project and nothing else from
your machine. The plugin's runner has no way to run a command on your machine, while
`/sandbox` lets the agent ask you to approve running one there unless
`allowUnsandboxedCommands` is `false`. Use one option per session: the plugin together with
`/sandbox` is not tested.

## How it works

1. A `PreToolUse` hook rewrites each `bash` tool call into a call to the bundled runner.
   The original command is passed as one quoted argument. A POSIX shell passes it through
   unchanged (tested with sh, bash, zsh, dash and ksh). The hook hands CoCo the rewritten
   command, with your command at the end, for the permission prompt.
2. The runner copies the files that changed in your project into the sandbox, runs the
   command there in the matching directory, and streams output and the exit code back.
3. Files the command changed are copied back into your project.

The read, edit and write tools keep working on your local files. If no sandbox can be
reached, the command does not run: the call returns exit code 125 and a message. The
runner has no code path that runs a command on your machine. CoCo itself has one: if the
hook does not answer within its time limit, CoCo runs the original command (see
[What is not protected](#what-is-not-protected)).

## Where commands run

In an OpenShell sandbox managed by your OpenShell gateway, on your machine or on hosts you
run. An OpenShell policy file sets which files, network destinations and processes a
command may use; the default policy allows no network at all. Project files go only to the
machine that runs your gateway, and the copy stays there until you delete the sandbox with
`sbx.py down --delete`. The plugin itself makes no network calls.

## What stays on your machine

- **Everything outside the project.** The sandbox gets a copy of the project directory
  only. The runner refuses to use your home directory, or a parent of it, as the project.
- **Secret-looking names.** Files and directories with these names are not copied in
  either direction, at any depth: `.env`, `.env.*` (except `.env.example`, `.env.sample`,
  `.env.template`), `.envrc`, `*.pem`, `*.key`, `*.p8`, `*.p12`, `*.pfx`, `*.jks`,
  `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`, `.netrc`, `.npmrc`, `.pypirc`,
  `.git-credentials`, `.dockercfg`, `.docker`, `.kube`, `kubeconfig`, `.snowflake`,
  `.snowsql`, `.aws`, `.ssh`, `.gnupg`, `.vault-token`, `.htpasswd`, `profiles.yml`,
  `connections.toml`, `*.tfstate`, `*.tfstate.*`, `*.tfvars`, `*service-account*.json`,
  `*service_account*.json`. The match is on the name only and ignores letter case. A
  secret under another name is copied; add such names to `secrets` in the settings file.
- **Agent configuration.** `.cortex`, `.claude`, `.cursor` and `.mcp.json` are not copied
  in either direction.
- **Git credentials.** `config` files under `.git` are copied with `user:token@` removed
  from `http` and `https` URLs and `extraheader` lines dropped. A credential written any
  other way, for example in an `ssh://` URL or in `config.worktree`, is copied as it is.

dbt users: `profiles.yml` is on the list. Give the sandbox its Snowflake connection
through an OpenShell provider, or set `"allow": ["profiles.yml"]` when the file holds no
secret.

## What comes back, and what never does

Files a command changed come back only inside the project directory. These never do:

- Paths outside the project, links that point outside it, and writes that would pass
  through such a link. The check is made on the path after links are resolved.
- Inside any `.git` directory, everything except plain repository data: `HEAD`,
  `ORIG_HEAD`, `FETCH_HEAD`, `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `MERGE_MSG`,
  `MERGE_MODE`, `COMMIT_EDITMSG`, `index`, `packed-refs`, `shallow`, `description`,
  `info/exclude`, and what is under `refs`, `logs`, `objects` and `reftable` except
  `objects/info/alternates`. Hooks, `config`, submodule and
  worktree settings, redirects, links and rebase state stay as they are on your machine.
  A `.git` that arrives as a file or a link is refused.
- Hooks or Git configuration next to a `HEAD` file in a directory under another name.
- An executable file or a link in a project directory that is on your `PATH`, and a link
  that would stand in for such a directory or one of its parents. CoCo runs `bash` and the
  hook runs `python3` by name, so a file planted there could run on your machine at the
  next call when that directory comes before the system ones (direnv's `PATH_add` puts it
  first). If your `PATH` has an empty or relative entry, such as `.` or `bin`, every
  executable file and every link is refused.
- Anything inside a Python environment in the project (a directory holding `pyvenv.cfg` or
  `conda-meta`), whatever it is called, and a new `pyvenv.cfg`. An activated environment's
  `python3`, the hook's included, reads those files every time it starts.
- `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md`, `GEMINI.md` and `.cursorrules`, at any depth.
- Every name on the lists above.

All of these checks ignore letter case and invisible characters, as a macOS or Windows
disk does.

The runner never deletes a local file or directory that it did not copy. If the same
file changed locally and in the sandbox, your version is kept. If a command replaced a
directory with a file and the local directory still holds files of yours, the change is
refused. If part of a `.git` directory could not come back, for example a pack above the
size limit, nothing is deleted in that `.git`. The runner prints the first eight refused
paths of a command. A deletion it does not apply is not printed.

## What is not protected

- **Other files a command changes in the project do come back.** A command can rewrite a
  `Makefile`, a `package.json` script, a CI file or a `.husky` hook. Review the changes
  before you run them outside the sandbox.
- **Other tools.** Only the shell tool is routed to the sandbox. File, SQL, web and MCP
  tools behave as before; govern them with your permission settings.
- **Permission rules written for specific commands.** CoCo checks them against the
  rewritten command, which starts with the path of `python3`. In CoCo 1.2.46 an allow rule
  such as `bash(git *)` no longer approves `git status`, and a deny rule such as
  `bash(rm *)` no longer stops `rm`. Rules for the whole `bash` tool still work. If you rely
  on command rules, use the team setup in [SETUP.md](SETUP.md#enforce-it-for-a-team).
- **A hook that does not answer in time.** If the hook does not answer within its time
  limit (60 s), CoCo 1.2.46 runs the original command on your machine. The hook normally
  answers in about 50 ms. The team setup has no hook and no such case.
- **A plugin can be switched off by its user.** To enforce the sandbox for a team, see
  [SETUP.md](SETUP.md#enforce-it-for-a-team).
- **Credentials you give the sandbox** are usable by commands that run there.

## Prerequisites

- CoCo CLI on macOS or Linux (on Windows, use WSL). Native Windows is not supported: do
  not enable the plugin there. CoCo Desktop is untested.
- `python3` 3.9 or newer, on the `PATH` of the shell CoCo uses to run hooks. No Python
  packages are needed.
- The `openshell` CLI with a reachable gateway (`openshell status` says Connected).
- In the sandbox image: `bash`, GNU coreutils 8.25 or newer, GNU `find` and `xargs`
  (findutils), GNU `sed`, GNU `tar`, and `ps` from procps. The default OpenShell image has
  them. Alpine and other BusyBox images do not work as they are.

The default OpenShell sandbox is a minimal Ubuntu 24.04 with no `python3`, no `git` and no
network access. To use your own tools, point the plugin at an image that has them and a
policy that allows the hosts you need ([SETUP.md](SETUP.md#3-configuration)).

## Quick start

In your terminal:

```
cortex plugin install zozo123/sandboxed-shell/plugins/sandboxed-shell
cortex plugin validate sandboxed-shell
```

Or, in a CoCo session:

```
Install the plugin at https://github.com/Snowflake-Labs/coco-skills/tree/main/plugins/sandboxed-shell
```

Then, in your own terminal, from your project directory:

```
python3 <plugin directory>/bin/sbx.py doctor
```

`<plugin directory>` is the folder CoCo installed the plugin into, or the path you gave
to `--plugin-dir`. Ask the agent to run `uname -s`. It prints `Linux`, whatever your
machine is. Configuration and the team setup are in [SETUP.md](SETUP.md).

## Good to know

- The sandbox is Linux. Files and directories named `node_modules`, `.venv`, `venv`,
  `target`, `__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.tox`, `.nox`,
  `.direnv`, `.gradle`, `.terraform` or `.DS_Store` are not copied, at any depth. Each side
  keeps its own.
- The project lives at `<sandbox working directory>/<project name>`, with the name in
  lower case, each run of other characters replaced by one `-`, and cut to 24 characters.
  `My Project` becomes `my-project`. Paths from your machine do not exist there, so the
  bundled skill tells the agent to use relative paths.
- `cd` carries over to the next command in the same session. Environment variables and
  shell functions do not: each command starts a new shell.
- If the tool call carries a time limit (`timeout_ms` in CoCo), the command and its child
  processes are stopped in the sandbox when it passes. CoCo reports its own timeout message;
  other agents get exit code 124. Stopping the tool call, or `kill_shell` on a background
  command, stops the command too.
- Exit code 125 together with the line `sbx: the sandbox layer failed; nothing was run on
  this machine instead` means the plugin failed, not the command.
- A process that a command leaves running keeps running in the sandbox. Its later output
  is not shown.
- Files larger than 200 MiB are not copied in either direction (`max_file_mb`). A command
  longer than 100,000 bytes is blocked; put it in a script file and run that.
- If the sandbox has two names that differ only in letter case and your disk cannot hold
  both, the second one is refused.
- One sandbox per project directory, created on first use and kept, so files a command
  creates outside the project stay between commands. If the sandbox was stopped, it is
  started again. If it was replaced while a command was running, the call returns 125 and
  the command is not run a second time.
- To run CoCo itself inside OpenShell, with your Snowflake token kept in the OpenShell
  gateway rather than in the sandbox, see `openshell/` in the
  [project repository](https://github.com/zozo123/sandboxed-shell).

## Tested

In CoCo CLI 1.2.46 on macOS (Apple silicon), signed in to a Snowflake account, on 19
November 2026. Commands from the agent, from a subagent and from a background command ran
in the sandbox. `kill_shell` and a time limit stopped them there. A missing `python3`
blocked the call. Git hooks written in the sandbox did not reach the machine. With the
shell tool removed, the MCP tool ran the commands. The two limits under
[What is not protected](#what-is-not-protected) were found in these runs.

67 unit tests pass on Python 3.9 and 3.14, on macOS and on Linux. They run 32 command
strings, 19 of them injection attempts, through sh, bash, zsh, dash and ksh. A canary
file checks that none of them runs on the host.

39 end-to-end tests pass against OpenShell 0.1.2 and 0.1.9 sandboxes on an Apple-silicon
Mac. The plugin adds about 0.4 s to a command. The first command in a project of 300 files takes
1.7 s. Each command scans the project, so large repositories cost more.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Source, tests and issues:
https://github.com/zozo123/sandboxed-shell.

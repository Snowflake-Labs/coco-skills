---
name: "sandboxed-shell"
id: "sandboxed-shell"
title: "Sandboxed Shell Guide"
summary: "Explains the sandbox that shell commands run in: where the project lives, what is copied, and what to do when a command fails there."
description: "Use when shell commands run in an OpenShell sandbox through the sandboxed-shell plugin and something behaves differently than on the user's machine: a path does not exist, a tool is missing, a file did not appear, a command exits with 124 or 125, or the user asks where commands run. Explains the copied project directory, which names are not copied, and how the user can check or reset the sandbox. Does not create, configure or delete sandboxes, and does not cover CoCo's built-in /sandbox or cortex --cloud. Triggers: sandboxed shell, OpenShell sandbox, where does this command run, file not copied, exit code 125."
tools:
  - bash
prompt: "Where do shell commands run in this session, and what can they reach on my machine?"
language: "en"
status: "Published"
author: "zozo123"
authors: ["zozo123"]
type: "community"
categories: ["Security", "Developer Tools"]
---

# Shell commands run in a sandbox

With this plugin active, every command sent to the shell tool runs in a Linux sandbox,
not on the user's machine. The read, edit and write tools still act on the user's local
files. Changed project files are copied into the sandbox before each command, and the
files the command changed are copied back after it.

## What to expect

- **Operating system.** The sandbox is Linux, whatever the user's machine is. Tools
  installed on the user's machine are not there. With the default OpenShell sandbox there
  is no `git`, no `python3` and no network access, and system directories are read-only,
  so `apt` does not work. If a tool is missing, tell the user which one; they choose the
  sandbox image and its network policy.
- **Paths.** The project lives at the directory `pwd` prints in your first command.
  Absolute paths from the user's machine do not exist in the sandbox, so use paths
  relative to the project.
- **Working directory.** `cd` carries over to your next command in the same session.
  Environment variables and shell functions do not: each command starts a new shell.
- **Not copied in either direction.** Dependency and build directories that are specific
  to one machine (`node_modules`, `.venv`, `venv`, `target`, `__pycache__`, tool caches),
  secret-looking names (`.env`, `.env.*`, `*.pem`, `*.key`, `*.p8`, SSH keys, `.netrc`,
  `.npmrc`, `.pypirc`, `profiles.yml`, `connections.toml`, `.snowflake`, `.aws`, `.ssh`,
  `.kube`, Terraform state) and agent configuration (`.cortex`, `.claude`, `.cursor`,
  `.mcp.json`). If a command needs one of them, tell the user. Do not copy secrets into
  other files to get them across.
- **Not written back to the user's machine.** Anything under `.git` except plain
  repository data: hooks and `config` stay as they are locally. Also `AGENTS.md`,
  `CLAUDE.md`, `CLAUDE.local.md`, `GEMINI.md` and `.cursorrules`. Change those with the edit
  tool so the user sees the change, never from the shell.
- **Background processes** keep running after the command returns, but their later output
  is not shown. Redirect it to a file in the project and print it with a later shell
  command such as `tail`.
- **Edits made at the same time.** If a file changed both on the user's machine and in the
  sandbox, the user's version is kept. The first eight such paths are printed.
- **Long commands.** A command longer than 100,000 bytes is blocked. Write it to a script
  file with the write tool and run the file.

## Exit codes that come from the sandbox layer

| Code | Meaning | What to do |
|---|---|---|
| 124 | The command passed its time limit and was stopped, with its child processes. | Run it with a longer limit or in the background. |
| 125, with the line `sbx: the sandbox layer failed; nothing was run on this machine instead` | The plugin failed. The command did not run, or its result is unknown. | Show the user the message. Do not retry in a loop and do not look for another way to run the command. |

Without that line, 125 is the command's own exit code. 124 carries no line: it means the
time limit when the tool call had one and the command ran that long, and otherwise it is
the command's own exit code. In CoCo a command stopped by its time limit is reported by
CoCo itself, as "Command timed out after N seconds".

## When the user asks about the sandbox

The shell tool now runs inside the sandbox, so it cannot inspect the sandbox setup. Ask
the user to run one of these in their own terminal, from the project directory, and read
the result with them:

```
python3 <plugin directory>/bin/sbx.py doctor          # is a backend installed and reachable?
python3 <plugin directory>/bin/sbx.py status          # which sandbox backs this project?
python3 <plugin directory>/bin/sbx.py down --delete   # start over with a fresh sandbox
```

## What this does not cover

Only the shell tool is routed to the sandbox. File tools, SQL tools, web tools and MCP
tools behave as before and are governed by the user's permission settings. Other files a
command changes inside the project do come back, so the user should review changes to
scripts and build files before running them outside the sandbox.

# Examples

**User:** "Where do your commands run?"
Run `uname -s; pwd`. It prints `Linux` and the project's directory in the sandbox.
Explain that the project is a copy and that secret-looking files are not in it.

**User:** "Why did `ls /Users/me/project` fail?"
That path belongs to the user's machine. The same files are in the directory `pwd`
prints; use relative paths.

**User:** "It says exit code 125."
Report the message that came with it. Ask the user to run `sbx.py doctor` in their own
terminal. Do not try to run the command another way.

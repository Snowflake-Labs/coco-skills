#!/usr/bin/env python3
"""PreToolUse hook: send every shell tool call to a sandbox instead of this machine.

The agent harness (Cortex Code CLI or Claude Code) passes the hook event as JSON on
stdin. For the shell tool this hook answers with ``hookSpecificOutput.updatedInput``:
the command is replaced by a call to the ``sbx`` runner that carries the original
command as ONE single-quoted argument, so the host shell never interprets any part
of it. The original text stays readable in permission prompts and transcripts.

Any failure exits with code 2, which blocks the tool call. A command is never run
unsandboxed as a fallback.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
RUNNER = PLUGIN_ROOT / "bin" / "sbx.py"
SHELL_TOOLS = ("bash", "Bash")  # CoCo's matcher is "bash", but CoCo 1.2.46 and Claude Code both send "Bash"
MAX_COMMAND_BYTES = 100_000  # one argument on Linux is limited to 128 KiB
PERMISSIONS = ("allow", "ask")


def user_permission():
    """Optional user-level setting; never read from the project directory."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.environ.get("SBX_CONFIG") or os.path.join(base, "sandboxed-shell", "config.json")
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle).get("permission")
    except (OSError, ValueError, AttributeError):
        return None
    return value if value in PERMISSIONS else None


def quote(part):
    """shlex.quote, and a word that starts with "=" is quoted too: zsh replaces a bare =name
    with the path of the command called name."""
    quoted = shlex.quote(part)
    return f"'{quoted}'" if quoted.startswith("=") else quoted


def rewrite(event):
    """Return the hook response for one event, or None when the event is not a shell call."""
    if event.get("tool_name") not in SHELL_TOOLS:
        return None
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        raise ValueError("shell tool call without a command string")
    if "\x00" in command:
        raise ValueError("command contains a NUL byte")
    if os.name == "nt":
        raise ValueError("this plugin does not support native Windows (use WSL)")
    if len(command.encode("utf-8", "surrogateescape")) > MAX_COMMAND_BYTES:
        raise ValueError(f"the command is longer than {MAX_COMMAND_BYTES} bytes; "
                         "put it in a script file in the project and run that")
    if not RUNNER.is_file():
        raise ValueError(f"sandbox runner is missing at {RUNNER}")

    root = (
        os.environ.get("CORTEX_PROJECT_DIR")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or event.get("cwd")
        or os.getcwd()
    )
    session = re.sub(r"[^A-Za-z0-9_.-]", "", str(event.get("session_id") or ""))[:64] or "default"
    # -I: the interpreter ignores PYTHONPATH and the user site, so no file in the project can shadow a module.
    argv = [sys.executable or "python3", "-I", str(RUNNER), "run", "--root", str(root), "--session", session]
    # The sandbox gets its own copy of the time limit, so the command stops there even if the
    # harness kills this runner without a signal it can catch.
    for key in ("timeout_ms", "timeout"):  # CoCo calls it timeout_ms, Claude Code timeout; both milliseconds
        limit = tool_input.get(key)
        if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0:
            argv += ["--timeout-ms", str(int(limit))]
            break
    argv += ["--", command]

    updated = dict(tool_input)
    updated["command"] = " ".join(quote(part) for part in argv)
    specific = {"hookEventName": "PreToolUse", "updatedInput": updated}
    permission = user_permission()
    if permission:
        specific["permissionDecision"] = permission
        specific["permissionDecisionReason"] = "Runs in the sandbox, not on this machine."
    return {"hookSpecificOutput": specific}


def main():
    response = rewrite(json.load(sys.stdin))
    if response is not None:
        sys.stdout.write(json.dumps(response, allow_nan=False))  # strict JSON, written in one piece or not at all
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # fail closed: block the call, never fall back to the host
        print(
            f"sandboxed-shell: {exc}. The command was blocked and did not run on this machine.",
            file=sys.stderr,
        )
        sys.exit(2)

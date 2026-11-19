#!/usr/bin/env python3
"""MCP server (stdio) that exposes the sandbox as a tool.

Use it where an administrator has switched the built-in shell tool off: the agent
then has exactly one way to run a command, and that way is the sandbox.

Tools: sandbox_exec(command, timeout_seconds?) and sandbox_status().
Standard library only; speaks newline-delimited JSON-RPC 2.0.
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

RUNNER = Path(__file__).resolve().parent.parent / "bin" / "sbx.py"
NAME, VERSION = "sandboxed-shell", "0.2.0"
OUTPUT_LIMIT = 200_000  # characters of command output returned to the model (the tail is kept)
DEFAULT_TIMEOUT_SECONDS = 600

TOOLS = [
    {
        "name": "sandbox_exec",
        "description": (
            "Run a shell command in the project's sandbox (Linux), not on the user's machine. "
            "The project directory is mirrored into the sandbox before the command and changed "
            "files are mirrored back after it. Returns the combined output and the exit code."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."},
                "timeout_seconds": {"type": "integer", "minimum": 1, "description": "Stop the command after this many seconds (default 600)."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "sandbox_status",
        "description": "Show which sandbox backs this project and whether it is running.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def project_root():
    # CoCo 1.2.46 starts MCP servers in the folder it was launched from, passes no project
    # variable, ignores -w here and does not answer roots/list (checked live, 2026-11-19).
    return os.environ.get("CORTEX_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def tools():
    """The tool list, with the folder this server copies named, so a wrong folder is visible."""
    listed = json.loads(json.dumps(TOOLS))
    listed[0]["description"] += (f" The folder copied to the sandbox is {project_root()}, the folder the agent was "
                                 "started in; if that is not the project, tell the user to restart the agent there.")
    return listed


RUNNING = {}  # request id -> runner process, so that a cancelled call can be stopped
WRITING = threading.Lock()


def run_runner(args, timeout=None, request_id=None):
    proc = subprocess.Popen(
        [sys.executable or "python3", "-I", str(RUNNER), *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=project_root(),
    )
    RUNNING[request_id] = proc
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        RUNNING.pop(request_id, None)
    return proc.returncode, output.decode("utf-8", "replace")


def call_tool(name, arguments, request_id=None):
    if name == "sandbox_status":
        code, output = run_runner(["status", "--root", project_root()], timeout=120)
        return {"content": [{"type": "text", "text": output}], "isError": code != 0}
    if name == "sandbox_exec":
        command = arguments.get("command")
        if not isinstance(command, str) or "\x00" in command:
            raise ValueError("'command' must be a string")
        args = ["run", "--root", project_root(), "--session", f"mcp-{os.getpid()}"]
        limit = arguments.get("timeout_seconds")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            limit = DEFAULT_TIMEOUT_SECONDS  # a command with no limit would hold this tool call open for good
        args += ["--timeout-ms", str(limit * 1000)]
        code, output = run_runner(args + ["--", command], request_id=request_id)
        if len(output) > OUTPUT_LIMIT:
            output = f"[output truncated to the last {OUTPUT_LIMIT} characters]\n" + output[-OUTPUT_LIMIT:]
        return {
            "content": [{"type": "text", "text": f"{output}\n[exit code {code}]"}],
            "isError": code == 125,  # 125: the sandbox layer failed and the command did not run
        }
    raise ValueError(f"unknown tool '{name}'")


def handle(message):
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion") or "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": NAME, "version": VERSION},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools()}
    if method == "tools/call":
        try:
            return call_tool(params.get("name"), params.get("arguments") or {}, message.get("id"))
        except Exception as exc:  # report tool failures to the model instead of dropping the connection
            return {"content": [{"type": "text", "text": f"sandbox tool error: {exc}"}], "isError": True}
    raise LookupError(method)


def answer(message):
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    try:
        reply["result"] = handle(message)
    except LookupError:
        reply["error"] = {"code": -32601, "message": f"method not found: {message.get('method')}"}
    except Exception as exc:
        reply["error"] = {"code": -32603, "message": str(exc)}
    with WRITING:
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


def main():
    calls = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("method") == "notifications/cancelled":
            params = message.get("params")
            proc = RUNNING.get(params.get("requestId")) if isinstance(params, dict) else None
            if proc is not None:
                proc.terminate()  # the runner then stops the command in the sandbox
            continue
        if "id" not in message:
            continue  # other notifications need no answer
        if message.get("method") == "tools/call":
            # A command can run for a long time; ping, cancel and the next call must not wait for it.
            calls.append(threading.Thread(target=answer, args=(message,)))
            calls[-1].start()
        else:
            answer(message)
    for call in calls:
        call.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())

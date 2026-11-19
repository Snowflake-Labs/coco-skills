#!/usr/bin/env python3
"""sbx: run an agent's shell command in a sandbox instead of on this machine.

Standard library only (Python 3.9+). Sandbox: NVIDIA OpenShell, on this machine or your own hosts.

  sbx.py run --root DIR [--session ID] [--timeout-ms N] -- 'COMMAND'
  sbx.py up | sync | status | doctor | down [--delete]   [--root DIR]

One command flows like this:
  1. files changed in the project directory are mirrored into the sandbox;
  2. the command runs there, in the matching directory, and its output streams back;
  3. files the command changed are mirrored back.
No part of COMMAND is interpreted on this machine.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import fnmatch
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

VERSION = "0.1.0"
HELPER_VERSION = "5"
EXIT_SBX = 125  # the sandbox layer failed; the command did not run, or its result is unknown
MAX_COMMAND_BYTES = 100_000  # one argument on Linux is limited to 128 KiB
# A longer command travels to the sandbox as a file. OpenShell refuses an argument above
# 32,768 bytes, and base64 makes the argument a third longer than the command.
INLINE_COMMAND_BYTES = 24_000

# Names are matched at any depth and without regard to letter case.
# Machine-specific build output and caches: each side keeps its own copy.
DEFAULT_EXCLUDES = [
    "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".gradle", ".terraform", "target", ".DS_Store",
]
# Secret-looking files and agent configuration: never leave this machine.
DEFAULT_SECRETS = [
    ".env", ".env.*", "*.pem", "id_rsa*", "id_ed25519*", ".netrc", ".npmrc", ".pypirc",
    ".snowflake", ".aws", ".ssh", ".envrc", "id_ecdsa*", "id_dsa*", "*.key", "*.p12", "*.pfx", "*.jks",
    ".git-credentials", ".dockercfg", ".docker", ".kube", "kubeconfig", ".snowsql", "connections.toml",
    "*.tfstate", "*.tfstate.*", "*.tfvars", ".vault-token", ".htpasswd", ".gnupg",
    "*service-account*.json", "*service_account*.json", "*.p8", "profiles.yml",
]
# Agent configuration: it holds tokens (MCP servers) and decides what the agent may do.
AGENT_CONFIG = [".cortex", ".claude", ".cursor", ".mcp.json"]
SECRET_EXCEPTIONS = [".env.example", ".env.sample", ".env.template"]
# Never written back to this machine: files that could make the host run code later.
PULL_DENY = [".git/hooks/", ".git/config", ".cortex/", ".claude/", ".cursor/", ".mcp.json", ".envrc"]
# Instruction files steer the next agent session on this machine. They go into the sandbox
# (a command may read them) and never come back from it.
INSTRUCTION_FILES = {"agents.md", "claude.md", "claude.local.md", "gemini.md", ".cursorrules"}
# Inside any .git directory (a nested repository, a submodule and a worktree have one too) git
# takes commands from hooks and from configuration (core.fsmonitor, core.sshCommand, ...),
# follows redirects (commondir, gitdir, alternates) and replays rebase scripts. So only plain
# repository data comes back; everything else under .git stays as it is on this machine.
GIT_CONFIG_FILES = ("config", "config.worktree", "commondir")
GIT_SAFE_FILES = {"head", "orig_head", "fetch_head", "merge_head", "merge_msg", "merge_mode",
                  "cherry_pick_head", "revert_head", "commit_editmsg", "index", "packed-refs",
                  "shallow", "description", "info/exclude"}
GIT_SAFE_DIRS = {"refs", "logs", "objects", "info", "reftable"}
GIT_SAFE_TREES = ("refs/", "logs/", "objects/", "reftable/")
# State of a rebase or of a sequence of picks. The sandbox may not create it on this machine, but
# it may remove it: a rebase that started here and finished there must not stay "in progress" here.
GIT_REPLAY = {"rebase-merge", "rebase-apply", "sequencer"}
GIT_UNSAFE = {"objects/info/alternates", "objects/info/http-alternates"}
META = ".sbx-meta"

HELPER = r'''#!/usr/bin/env bash
# sbx helper. It runs inside the sandbox. Free-text arguments arrive base64-encoded,
# so no layer between the laptop and this script can re-interpret them.
set -u
export LC_ALL=C
meta="$(cd "$(dirname "$0")" && pwd)"
dec() { printf %s "$1" | base64 -d; }
op="$1"; root="$(dec "$2")"; shift 2

manifest() {  # one NUL-terminated record per entry: "<type> <mode> <size> <ctime> <path>"
  local prune=() keep=() pat
  while IFS= read -r -d '' pat; do
    [ ${#prune[@]} -gt 0 ] && prune+=(-o)
    prune+=(-iname "$pat")
  done < "$meta/excludes"
  while IFS= read -r -d '' pat; do keep+=(! -iname "$pat"); done < "$meta/keep"
  partial=0  # 1 when find could not read everything: an entry missing from the listing is then not proof it is gone
  ( cd "$root" || exit 1
    if [ ${#prune[@]} -gt 0 ]; then
      find . -ignore_readdir_race -mindepth 1 \( \( "${prune[@]}" \) ${keep[@]+"${keep[@]}"} \) -prune -o \
        \( -type d -printf 'd - - - %P\0' -o -printf '%y %m %s %C@ %P\0' \)
    else
      find . -ignore_readdir_race -mindepth 1 \( -type d -printf 'd - - - %P\0' -o -printf '%y %m %s %C@ %P\0' \)
    fi ) > "$meta/scan" 2>/dev/null || partial=1
  sort -z "$meta/scan"
}
count() { tr -cd '\000' < "$1" | wc -c | tr -d ' '; }
tree() {  # a process and all of its descendants
  local child
  printf '%s\n' "$1"
  for child in $(ps -o pid= --ppid "$1" 2>/dev/null); do tree "$child"; done
}

case "$op" in
  apply)  # unpack push.tar from the laptop: deletions first, then files
    ack="${1:-}"
    mkdir -p "$root" || exit 90
    tarf="$meta/push.tar"
    tar -xOf "$tarf" "$meta_name/deleted" 2>/dev/null | ( cd "$root" && while IFS= read -r -d '' p; do
      case "/$p/" in */../*|//*) continue;; esac
      rm -rf -- "./$p"
    done )
    tar -xpf "$tarf" -C "$root" --no-same-owner --exclude="$meta_name" || exit 91
    # Agree on the pull the laptop just acknowledged and on the entries it just sent, and on
    # nothing else. Another command may be writing files in the sandbox right now; a fresh
    # listing taken here would mark those files as already seen and they would never be mirrored.
    if [ -f "$meta/pending" ] && [ -f "$meta/new" ] && [ "$(cat "$meta/pending")" = "$ack" ]; then
      mv -f "$meta/new" "$meta/old"
    fi
    [ -f "$meta/old" ] || : > "$meta/old"
    tar -xOf "$tarf" "$meta_name/pushed" 2>/dev/null | ( cd "$root" && xargs -0 -r sh -c \
      'find "$@" -maxdepth 0 \( -type d -printf "d - - - %p\0" -o -printf "%y %m %s %C@ %p\0" \)' sh ) 2>/dev/null \
      | sed -z 's|^\([^ ]* [^ ]* [^ ]* [^ ]* \)\./|\1|' | sort -z -u - "$meta/old" > "$meta/old.tmp" || exit 92
    mv -f "$meta/old.tmp" "$meta/old"
    rm -f "$tarf" "$meta/new" "$meta/pending"
    printf 'SBX ok\n'
    ;;
  changes)  # what changed in the sandbox since the laptop last agreed; pack it into pull.tar
    ack="${1:-}"
    if [ -f "$meta/pending" ] && [ -f "$meta/new" ] && [ "$(cat "$meta/pending")" = "$ack" ]; then
      mv -f "$meta/new" "$meta/old"
    fi
    rm -f "$meta/pending"
    mkdir -p "$root" || exit 90
    [ -f "$meta/old" ] || : > "$meta/old"
    manifest > "$meta/new" || exit 92
    comm -z -13 "$meta/old" "$meta/new" | cut -z -d' ' -f5- > "$meta/changed"
    # -u: "apply" may leave an older record of an entry next to its current one; comm counts duplicates
    comm -z -23 <(cut -z -d' ' -f5- "$meta/old" | sort -z -u) <(cut -z -d' ' -f5- "$meta/new" | sort -z) > "$meta/deleted"
    [ "$partial" = 0 ] || : > "$meta/deleted"
    nc="$(count "$meta/changed")"; nd="$(count "$meta/deleted")"
    seq="$(date +%s%N)"
    if [ "$nc" -gt 0 ] || [ "$nd" -gt 0 ]; then
      rm -rf "$meta/out"; mkdir -p "$meta/out/$meta_name"
      cp "$meta/deleted" "$meta/out/$meta_name/deleted"
      tar -C "$meta/out" -cf "$meta/pull.tar" "$meta_name" || exit 93
      # --hard-dereference: a second name for the same file travels as a file, not as a link the laptop refuses
      ( cd "$root" && tar --no-recursion --ignore-failed-read --hard-dereference --null -T "$meta/changed" -rf "$meta/pull.tar" ) 2>/dev/null
      [ "$partial" = 0 ] && printf %s "$seq" > "$meta/pending"  # an incomplete listing is never agreed on
    elif [ "$partial" = 0 ]; then
      mv -f "$meta/new" "$meta/old"
    fi
    printf 'SBX %s %s %s %s\n' "$nc" "$nd" "$seq" "$partial"
    ;;
  run)  # run the agent's command; remember the directory it ends in for this session
    session="$(dec "$1")"; limit="$2"; rel="$(dec "$4")"; nonce="$5"
    if [ "$3" = "@" ]; then  # a long command arrives as a file, not as an argument
      SBX_CMD="$(cat "$meta/cmd.$nonce")" || exit 94
      rm -f "$meta/cmd.$nonce"
    else
      SBX_CMD="$(dec "$3")"
    fi
    SBX_CWD_FILE="$meta/cwd.$session"
    start="$root/$rel"
    if [ -s "$SBX_CWD_FILE" ]; then
      saved="$(cat "$SBX_CWD_FILE")"
      [ -d "$saved" ] && start="$saved"
    fi
    cd "$start" 2>/dev/null || { mkdir -p "$root" && cd "$root"; } || exit 94
    mkdir -p "$meta/ran" && : > "$meta/ran/$nonce"
    export SBX_CMD SBX_CWD_FILE
    inner='__sbx_c="$SBX_CMD"; __sbx_f="$SBX_CWD_FILE"; unset SBX_CMD SBX_CWD_FILE; trap '\''pwd > "$__sbx_f" 2>/dev/null'\'' EXIT; eval "$__sbx_c"'
    # The command writes to two pipes that this script relays. A process the command leaves
    # behind then holds only those pipes, so it cannot keep this call open or cost it its exit code.
    io="$meta/io.$nonce"
    mkfifo "$io.out" "$io.err" || exit 94
    cat "$io.out" & out_pid=$!
    cat "$io.err" >&2 & err_pid=$!
    bash -lc "$inner" > "$io.out" 2> "$io.err" </dev/null &
    main=$!
    printf %s "$main" > "$meta/ran/$nonce"  # "stop" finds the command by this
    watchdog=""
    if [ "$limit" -gt 0 ] 2>/dev/null; then
      # Sandboxes may forbid signalling a process group (OpenShell does), so the time limit
      # is enforced here, one process at a time, instead of with timeout(1).
      ( sleep "$limit"; : > "$io.timeout"; victims="$(tree "$main")"
        kill -TERM $victims 2>/dev/null; sleep 5; kill -KILL $victims 2>/dev/null ) >/dev/null 2>&1 &
      watchdog=$!
    fi
    wait "$main"; code=$?
    if [ -n "$watchdog" ]; then
      if [ -f "$io.timeout" ]; then code=124; else kill $(tree "$watchdog") 2>/dev/null; fi
    fi
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do  # at most one pipe buffer is left to relay
      kill -0 "$out_pid" 2>/dev/null || kill -0 "$err_pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$out_pid" 2>/dev/null || kill -0 "$err_pid" 2>/dev/null; then
      # A process the command left behind still holds the pipes. Keep a reader on them (it reads
      # into nothing and ends with that process), or its next line of output kills it with SIGPIPE.
      for pipe in "$io.out" "$io.err"; do
        exec 3<>"$pipe" 4<"$pipe" 3>&-
        cat <&4 >/dev/null 2>&1 &
        exec 4<&-
      done
    fi
    kill "$out_pid" "$err_pid" 2>/dev/null
    rm -f "$io.out" "$io.err" "$io.timeout"
    exit "$code"
    ;;
  stop)  # the laptop gave up on the run with this nonce (interrupted, terminated): end its processes
    pid="$(cat "$meta/ran/$1" 2>/dev/null)"
    if [ -n "$pid" ]; then
      victims="$(tree "$pid")"
      kill -TERM $victims 2>/dev/null
      ( sleep 3; kill -KILL $victims 2>/dev/null ) >/dev/null 2>&1 </dev/null &
    fi
    printf 'SBX ok\n'
    ;;
  ran)  # did the run with this nonce start?
    if [ -f "$meta/ran/$1" ]; then printf 'SBX yes\n'; else printf 'SBX no\n'; fi
    find "$meta/ran" -type f -mmin +120 -delete 2>/dev/null
    ;;
  *) echo "sbx helper: unknown operation $op" >&2; exit 95 ;;
esac
'''.replace("$meta_name", META)


class SbxError(Exception):
    """The sandbox layer failed. The message is shown to the agent and the user."""


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8", "surrogateescape")).decode("ascii")


def warn(message: str) -> None:
    print(f"sbx: {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- configuration

def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(os.environ.get("SBX_CONFIG") or Path(base) / "sandboxed-shell" / "config.json")


def load_config() -> dict:
    """User-level settings only. Nothing is read from the project directory, because a
    repository must not be able to switch its own sandbox off."""
    cfg = {"backend": "auto", "exclude": [], "secrets": [], "allow": [], "max_file_mb": 200, "openshell": {}}
    path = config_path()
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SbxError(f"{path} is not valid JSON: {exc}")
        if not isinstance(loaded, dict):
            raise SbxError(f"{path} must contain a JSON object")
        cfg.update(loaded)
    if os.environ.get("SBX_BACKEND"):
        cfg["backend"] = os.environ["SBX_BACKEND"]
    for key in ("exclude", "secrets", "allow"):
        if not isinstance(cfg[key], list) or not all(isinstance(item, str) for item in cfg[key]):
            raise SbxError(f"'{key}' in {path} must be a list of strings")
    if not isinstance(cfg["openshell"], dict):
        raise SbxError(f"'openshell' in {path} must be an object")
    limit = cfg["max_file_mb"]
    # NaN and Infinity are valid in Python's JSON; a limit below one byte would leave every file out.
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not 1 / (1024 * 1024) <= limit <= 2 ** 40:
        raise SbxError(f"'max_file_mb' in {path} must be a number above zero")
    policy = cfg["openshell"].get("policy")
    if policy:  # a relative path means "next to the settings file", never "in the project"
        cfg["openshell"]["policy"] = os.path.join(path.parent, os.path.expanduser(str(policy)))
    return cfg


_IGNORABLE = re.compile("[\u200c-\u200f\u202a-\u202e\u206a-\u206f\ufeff]")


def fold(name: str) -> str:
    """One spelling for every name that a case-insensitive, normalising file system (macOS,
    Windows) stores as the same file: there .GIT, .Claude and .ENV are .git, .claude and .env."""
    return unicodedata.normalize("NFC", _IGNORABLE.sub("", name)).casefold()


def git_data(inner: str, directory: bool) -> bool:
    """True when a path below a .git directory (already folded) is plain repository data."""
    if inner in GIT_UNSAFE:
        return False
    return inner in GIT_SAFE_FILES or inner.startswith(GIT_SAFE_TREES) or (directory and inner in GIT_SAFE_DIRS)


class Rules:
    """Which names are never mirrored, and which paths are never written back."""

    def __init__(self, cfg: dict):
        self.blocked = DEFAULT_EXCLUDES + DEFAULT_SECRETS + AGENT_CONFIG + cfg["exclude"] + cfg["secrets"]
        self.kept = list(SECRET_EXCEPTIONS) + list(cfg.get("allow") or [])

    def skip(self, name: str) -> bool:
        name = fold(name)
        if name == META:
            return True
        if any(fnmatch.fnmatchcase(name, fold(pattern)) for pattern in self.kept):
            return False
        return any(fnmatch.fnmatchcase(name, fold(pattern)) for pattern in self.blocked)

    def digest(self) -> str:
        """Changes when the lists the helper in the sandbox works with change."""
        return hashlib.sha256(json.dumps([self.blocked, self.kept]).encode("utf-8")).hexdigest()[:16]

    def skip_path(self, rel: str) -> bool:
        return any(self.skip(part) for part in rel.split("/"))

    @staticmethod
    def _git_denied(inner: list, directory: bool) -> bool:
        if not inner:  # .git may arrive as a directory, never as a file or a link that names another one
            return not directory
        if inner[0] in ("modules", "worktrees"):
            # A submodule or worktree keeps its own git directory in here, under a name that
            # may itself contain slashes: the same rule applies to what follows the name.
            if "hooks" in inner or inner[-1] in GIT_CONFIG_FILES:
                return True
            if directory:
                return False
            tails = ["/".join(inner[start:]) for start in range(2, len(inner))]
            return bool(GIT_UNSAFE.intersection(tails)) or not any(git_data(tail, False) for tail in tails)
        return not git_data("/".join(inner), directory)

    @staticmethod
    def delete_denied(rel: str) -> bool:
        """A deletion is refused like a write, except for the state of a rebase or a sequence of picks."""
        if not Rules.pull_denied(rel):
            return False
        parts = [fold(part) for part in rel.split("/")]
        if INSTRUCTION_FILES.intersection(parts) or parts.count(".git") != 1:
            return True
        inner = parts[parts.index(".git") + 1:]
        state = inner[2:] if inner[:1] in (["modules"], ["worktrees"]) else inner[:1]
        return "hooks" in inner or inner[-1] in GIT_CONFIG_FILES or not GIT_REPLAY.intersection(state)

    @staticmethod
    def pull_denied(rel: str, directory: bool = True) -> bool:
        parts = [fold(part) for part in rel.split("/")]
        rel = "/".join(parts)
        if INSTRUCTION_FILES.intersection(parts):
            return True
        if ".git" in parts:
            # Every .git in the path is checked: a repository nested inside .git/refs or .git/objects
            # of another one is still a repository to git.
            return any(part == ".git" and Rules._git_denied(parts[index + 1:], directory)
                       for index, part in enumerate(parts))
        for pattern in PULL_DENY:
            if pattern.endswith("/"):
                if rel == pattern[:-1] or rel.startswith(pattern):
                    return True
            elif rel == pattern:
                return True
        return False


# ---------------------------------------------------------------------- backends

def _popen(argv, **kwargs):
    try:
        return subprocess.Popen(argv, **kwargs)
    except FileNotFoundError:
        raise SbxError(f"'{argv[0]}' is not installed or not on PATH")


def _capture(argv, cwd=None, timeout=300):
    proc = _popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise SbxError(f"'{' '.join(argv[:3])} ...' did not answer within {timeout}s")
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


class Backend:
    name = ""
    cli = ""

    def __init__(self, options: dict):
        self.options = options

    def phase(self, sandbox: str):  # -> (phase, identity); phase is ready, stopped, starting, missing or error
        raise NotImplementedError

    def create(self, sandbox: str) -> None:
        raise NotImplementedError

    def start(self, sandbox: str) -> None:
        raise NotImplementedError

    def stop(self, sandbox: str) -> None:
        raise NotImplementedError

    def delete(self, sandbox: str) -> None:
        raise NotImplementedError

    def exec_argv(self, sandbox: str, argv: list) -> list:
        raise NotImplementedError

    def put(self, sandbox: str, local: str, remote: str) -> None:
        raise NotImplementedError

    def get(self, sandbox: str, remote: str, local: str) -> None:
        raise NotImplementedError

    cwd = None  # working directory for the backend CLI
    trust_seconds = 0  # after a success, skip the "is the sandbox still there" call for this long

    def capture(self, sandbox: str, argv: list, timeout: int = 300):
        return _capture(self.exec_argv(sandbox, argv), cwd=self.cwd, timeout=timeout)

    def wait_ready(self, sandbox: str, seconds: int = 180):
        deadline = time.time() + seconds
        started = False
        while True:
            phase, ident = self.phase(sandbox)
            if phase == "ready":
                return ident
            if phase == "stopped" and not started:  # it finished stopping while we waited
                self.start(sandbox)
                started = True
                continue
            if phase == "missing":  # it was on its way out, not up
                return None
            if phase == "error":
                raise SbxError(f"sandbox '{sandbox}' is in state '{phase}' on {self.name}")
            if time.time() > deadline:
                raise SbxError(f"sandbox '{sandbox}' was not ready after {seconds}s (state '{phase}')")
            time.sleep(1)

    def _checked(self, argv: list, what: str, sandbox: str) -> None:
        code, out, err = _capture(argv, cwd=self.cwd, timeout=300)
        if code != 0 and "not found" not in (err + out).lower():
            raise SbxError(f"could not {what} sandbox '{sandbox}' on {self.name}: {(err or out).strip()[-400:]}")


class OpenShell(Backend):
    """NVIDIA OpenShell: an open-source sandbox runtime that runs on this machine or your own hosts."""

    name = "openshell"
    cli = "openshell"

    def _base(self) -> list:
        gateway = self.options.get("gateway")
        return ["openshell"] + (["--gateway", str(gateway)] if gateway else [])

    def phase(self, sandbox):
        code, out, err = _capture(self._base() + ["sandbox", "get", sandbox, "-o", "json"], timeout=60)
        if code != 0:
            if "not found" in err.lower():
                return "missing", ""
            raise SbxError(f"OpenShell is not reachable: {err.strip() or out.strip()}")
        try:
            info = json.loads(out)
        except ValueError:
            raise SbxError(f"unexpected answer from 'openshell sandbox get': {out[:200]}")
        phase = str(info.get("phase", "")).lower()
        mapped = {"ready": "ready", "stopped": "stopped", "error": "error", "completed": "error", "failed": "error"}
        return mapped.get(phase, "starting"), str(info.get("id", ""))

    def create(self, sandbox):
        argv = self._base() + ["sandbox", "create", "--name", sandbox, "--detach", "--no-tty", "--no-auto-providers"]
        if self.options.get("from"):
            argv += ["--from", str(self.options["from"])]
        if self.options.get("policy"):
            argv += ["--policy", os.path.expanduser(str(self.options["policy"]))]
        for provider in self.options.get("providers") or []:
            argv += ["--provider", str(provider)]
        argv += ["--", "sleep", "infinity"]
        code, out, err = _capture(argv, timeout=900)
        if code != 0:
            raise SbxError(f"could not create OpenShell sandbox '{sandbox}': {(err or out).strip()[-600:]}")

    def start(self, sandbox):
        code, out, err = _capture(self._base() + ["sandbox", "start", sandbox], timeout=300)
        if code != 0:
            raise SbxError(f"could not start OpenShell sandbox '{sandbox}': {(err or out).strip()[-400:]}")

    def stop(self, sandbox):
        self._checked(self._base() + ["sandbox", "stop", sandbox], "stop", sandbox)

    def delete(self, sandbox):
        self._checked(self._base() + ["sandbox", "delete", sandbox], "delete", sandbox)

    def exec_argv(self, sandbox, argv):
        return self._base() + ["sandbox", "exec", "--name", sandbox, "--no-tty", "--"] + list(argv)

    def put(self, sandbox, local, remote):
        # "sandbox upload", not a pipe into exec: OpenShell refuses piped input above 4 MiB.
        code, out, err = _capture(
            self._base() + ["sandbox", "upload", sandbox, local, remote, "--no-git-ignore"], timeout=1800)
        if code != 0:
            raise SbxError(f"upload to sandbox failed: {(err or out).strip()[-400:]}")

    def get(self, sandbox, remote, local):
        with open(local, "wb") as target:
            proc = _popen(self.exec_argv(sandbox, ["cat", remote]),
                          stdin=subprocess.DEVNULL, stdout=target, stderr=subprocess.PIPE)
            _, err = proc.communicate()
        if proc.returncode != 0:
            raise SbxError(f"download from sandbox failed: {err.decode('utf-8', 'replace').strip()[-400:]}")


BACKENDS = {"openshell": OpenShell}


def pick_backend(cfg: dict) -> Backend:
    """NVIDIA OpenShell is the sandbox. The Backend interface stays so another sandbox
    can be added without touching the copying and safety code."""
    wanted = str(cfg.get("backend") or "auto").lower()
    if wanted == "auto":
        wanted = "openshell"
    if wanted not in BACKENDS:
        raise SbxError(f"unknown backend '{wanted}' (choose one of: {', '.join(sorted(BACKENDS))})")
    backend = BACKENDS[wanted](cfg.get(wanted) or {})
    if not shutil.which(backend.cli):
        raise SbxError(f"backend '{wanted}' is selected but the '{backend.cli}' command is not installed")
    return backend


# ------------------------------------------------------------- local file tree

def signature(st: os.stat_result, path: str):
    if stat.S_ISDIR(st.st_mode):
        return ["d"]
    if stat.S_ISLNK(st.st_mode):
        return ["l", os.readlink(path)]
    if stat.S_ISREG(st.st_mode):
        return ["f", st.st_size, st.st_mtime_ns, st.st_mode & 0o7777]
    return None  # sockets, pipes and devices are not mirrored


def scan(root: str, rules: Rules) -> dict:
    """Every mirrored entry under root: relative path -> signature."""
    found = {}
    pending = [""]
    while pending:
        rel = pending.pop()
        try:
            entries = list(os.scandir(os.path.join(root, rel) if rel else root))
        except OSError:
            continue
        for entry in entries:
            if rules.skip(entry.name):
                continue
            path = f"{rel}/{entry.name}" if rel else entry.name
            try:
                sig = signature(entry.stat(follow_symlinks=False), entry.path)
            except OSError:
                continue
            if sig is None:
                continue
            found[path] = sig
            if sig[0] == "d":
                pending.append(path)
    return found


def safe_rel(rel: str) -> bool:
    if not rel or rel.startswith("/") or "\x00" in rel:
        return False
    return all(part not in ("", ".", "..") for part in rel.split("/"))


def inside(root_real: str, path: str) -> bool:
    """True when path, with every existing symlink resolved, stays under the project root."""
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real + os.sep)


def other_spelling(root: str, rel: str, listed: dict) -> bool:
    """True when this disk already holds rel under another spelling. A case-insensitive file
    system (the macOS default) cannot keep 'Makefile' and 'makefile' side by side."""
    here = root
    for part in rel.split("/"):
        names = listed.get(here)
        if names is None:
            try:
                names = listed[here] = set(os.listdir(here))
            except OSError:
                return False
        if part not in names:
            if os.path.lexists(os.path.join(here, part)):
                return True
            names.add(part)
        here = os.path.join(here, part)
    return False


def landing(root_real: str, rel: str):
    """The path a write to rel really reaches, relative to the project, once links in its parent
    directories are followed. None when that is outside the project."""
    parent = os.path.realpath(os.path.join(root_real, os.path.dirname(rel)))
    if parent != root_real and not parent.startswith(root_real + os.sep):
        return None
    return os.path.relpath(os.path.join(parent, os.path.basename(rel)), root_real)


def scrub_git_config(data: bytes) -> bytes:
    """git configuration without the credentials it can carry: user:token@ in an http(s) URL and
    http.extraheader (an Authorization header)."""
    data = re.sub(rb"(?i)(https?://)[^/@\s\"]+@", rb"\1", data)
    return re.sub(rb"(?im)^[ \t]*extraheader[ \t]*=.*\n?", b"", data)


def build_push_tar(root: str, changed: list, deleted: list, target: str, max_bytes: int, unreadable: list = None):
    """Write the tar the helper unpacks. Returns the paths that went in and the ones left out
    for size; paths that could not be read are added to unreadable when a list is given."""
    packed, skipped = [], []
    with tarfile.open(target, "w", format=tarfile.PAX_FORMAT) as tar:
        listing = b"".join(os.fsencode(path) + b"\0" for path in deleted)
        info = tarfile.TarInfo(f"{META}/deleted")
        info.size = len(listing)
        tar.addfile(info, io.BytesIO(listing))
        for rel in sorted(changed):  # parents sort before their children
            full = os.path.join(root, rel)
            try:
                info = tar.gettarinfo(full, arcname=rel)
                if info is None:
                    continue
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if info.isreg():
                    if info.size > max_bytes:
                        skipped.append(rel)
                        continue
                    with open(full, "rb") as handle:
                        if os.path.basename(rel) == "config" and ".git" in rel.split("/"):
                            data = scrub_git_config(handle.read())
                            info.size = len(data)
                            tar.addfile(info, io.BytesIO(data))
                        else:
                            tar.addfile(info, handle)
                else:
                    tar.addfile(info)
                packed.append(rel)
            except OSError:
                # The file vanished or changed mid-read (the next sync picks it up), or it cannot be read.
                if unreadable is not None and os.path.lexists(full):
                    unreadable.append(rel)
                continue
        listing = b"".join(b"./" + os.fsencode(path) + b"\0" for path in packed)
        info = tarfile.TarInfo(f"{META}/pushed")  # the helper records exactly these entries as agreed
        info.size = len(listing)
        tar.addfile(info, io.BytesIO(listing))
    return packed, skipped


def clear_mirrored(root: str, rel: str, known: dict, conflicts: set) -> None:
    """Make room for a file or link that replaces a directory. Only entries this machine mirrored
    and has not changed since are removed. Anything else (a .env, node_modules, a fresh local
    edit) stays, the last rmdir then fails, and the replacement is refused."""
    top = os.path.join(root, rel)
    for current, dirs, files in os.walk(top, topdown=False):
        base = os.path.relpath(current, root).replace(os.sep, "/")
        for entry in files + dirs:
            path, full = f"{base}/{entry}", os.path.join(current, entry)
            if path not in known or path in conflicts:
                continue
            if os.path.isdir(full) and not os.path.islink(full):
                with contextlib.suppress(OSError):
                    os.rmdir(full)
            else:
                os.unlink(full)
    os.rmdir(top)


def makes_git_dir(root_real: str, rel: str, heads: set) -> bool:
    """True when writing rel would put hooks or git configuration next to a HEAD file. Git treats
    such a directory as a repository whatever it is called, as soon as a command runs inside it.
    heads: the directories that get a HEAD from the archive being unpacked."""
    names = rel.split("/")
    parts = [fold(part) for part in names]
    for index, part in enumerate(parts):
        if part == "hooks" or (part in GIT_CONFIG_FILES and index == len(parts) - 1):
            if "/".join(parts[:index]) in heads or os.path.lexists(os.path.join(root_real, *names[:index], "HEAD")):
                return True
    if parts[-1] == "head" and ".git" not in parts:  # the other order: configuration came first
        holder = os.path.join(root_real, *names[:-1])
        return any(os.path.lexists(os.path.join(holder, entry)) for entry in GIT_CONFIG_FILES + ("hooks",))
    return False


def apply_pull_tar(root: str, tar_path: str, rules: Rules, conflicts: set, known: dict, max_bytes: int = 0):
    """Unpack what the sandbox changed. Returns (written, removed, refused)."""
    root_real = os.path.realpath(root)
    written, removed, refused, deleted, listed = [], [], [], [], {}
    incomplete = set()  # git directories that did not get all of their data: nothing is deleted in them

    def git_dir(path):
        folded = [fold(part) for part in path.split("/")]
        return "/".join(folded[:folded.index(".git") + 1]) if ".git" in folded else None
    with tarfile.open(tar_path, "r:") as tar:  # plain tar only: a compressed one could unpack to any size
        members = tar.getmembers()
        heads = {"/".join(fold(part) for part in member.name.split("/")[:-1] if part != ".")
                 for member in members if fold(os.path.basename(member.name)) == "head"}
        for member in members:
            name = member.name
            while name.startswith("./"):
                name = name[2:]
            if name == f"{META}/deleted":
                data = tar.extractfile(member).read()
                deleted = [os.fsdecode(item) for item in data.split(b"\0") if item]
                continue
            if name == META or name.startswith(META + "/"):
                continue
            target = os.path.join(root, name)
            # Every rule is applied twice: to the name in the archive, and to the place the write
            # really reaches when a parent directory is a link (to .git/hooks, for example).
            real = landing(root_real, name) if safe_rel(name) else None
            if real is None or any(rules.skip_path(path) or rules.pull_denied(path, member.isdir())
                                   or path in conflicts or makes_git_dir(root_real, path, heads)
                                   # git writes no links below .git; one there (objects/info -> a plain
                                   # directory) would turn unprotected files into protected ones
                                   or (member.issym() and git_dir(path) is not None)
                                   for path in (name, real)):
                refused.append(name)
                continue
            if other_spelling(root, name, listed):
                refused.append(f"{name} (this disk already has that name in another spelling)")
                continue
            if member.isreg() and (member.issparse() or (max_bytes and member.size > max_bytes)):
                refused.append(f"{name} (too large)")
                incomplete.add(git_dir(name))
                continue
            try:
                kind = os.lstat(target).st_mode
            except OSError:
                kind = None
            if kind is not None and not (stat.S_ISDIR(kind) or stat.S_ISREG(kind) or stat.S_ISLNK(kind)):
                # Sockets, pipes and devices are never copied, so one found here exists only here.
                refused.append(f"{name} (a socket, pipe or device only this machine has is in the way)")
                continue
            try:
                if member.isdir():
                    if os.path.islink(target) or (os.path.exists(target) and not os.path.isdir(target)):
                        os.unlink(target)
                    os.makedirs(target, exist_ok=True)
                elif member.isreg():
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.isdir(target) and not os.path.islink(target):
                        try:
                            clear_mirrored(root, name, known, conflicts)
                        except OSError:
                            refused.append(f"{name} (a directory with files only this machine has is in the way)")
                            continue
                    source = tar.extractfile(member)
                    handle, temp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".sbx-")
                    try:
                        with os.fdopen(handle, "wb") as out:
                            shutil.copyfileobj(source, out)
                        os.chmod(temp, member.mode & 0o777)
                        os.utime(temp, (member.mtime, member.mtime))
                        os.replace(temp, target)
                    except BaseException:
                        with contextlib.suppress(OSError):
                            os.unlink(temp)  # never leave a stray .sbx-* file in the project
                        raise
                elif member.issym():
                    link = member.linkname
                    resolved = os.path.normpath(os.path.join(os.path.dirname(name), link))
                    if (os.path.isabs(link) or resolved == ".." or resolved.startswith("../")
                            or not inside(root_real, os.path.join(os.path.dirname(target), link))):
                        refused.append(name)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.lexists(target):
                        if os.path.isdir(target) and not os.path.islink(target):
                            try:
                                clear_mirrored(root, name, known, conflicts)
                            except OSError:
                                refused.append(f"{name} (a directory with files only this machine has is in the way)")
                                continue
                        else:
                            os.unlink(target)
                    os.symlink(link, target)
                else:
                    refused.append(name)  # hard links, devices and pipes are not mirrored
                    continue
                written.append(name)
            except (OSError, ValueError, OverflowError) as exc:
                refused.append(f"{name} ({getattr(exc, 'strerror', None) or exc})")
                incomplete.add(git_dir(name))
    # Deletions: children before parents, files this machine knew about only, never a non-empty directory.
    for name in sorted(deleted, reverse=True):
        target = os.path.join(root, name)
        real = landing(root_real, name) if safe_rel(name) else None
        if real is None or name not in known or any(
                rules.skip_path(path) or rules.delete_denied(path) or path in conflicts for path in (name, real)):
            continue
        if git_dir(name) is not None and git_dir(name) in incomplete:
            continue  # a pack that did not arrive must not cost this machine the objects it replaces
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                os.rmdir(target)
            elif os.path.lexists(target):
                os.unlink(target)
            removed.append(name)
        except OSError:
            continue
    refused += [f"{path} (nothing deleted in it: not all of its data arrived)" for path in sorted(incomplete - {None})]
    return written, removed, refused


# ----------------------------------------------------------------- one sandbox

class Sandbox:
    def __init__(self, root: str, cfg: dict = None):
        self.cfg = cfg if cfg is not None else load_config()
        self.root = os.path.realpath(root)
        if not os.path.isdir(self.root):
            raise SbxError(f"project directory {self.root} does not exist")
        home = os.path.realpath(os.path.expanduser("~"))
        if home == self.root or home.startswith(self.root.rstrip(os.sep) + os.sep):
            raise SbxError(f"{self.root} is your home directory or a parent of it, and the whole project "
                           "directory is mirrored both ways. Start the agent inside a project directory")
        self.rules = Rules(self.cfg)
        self.backend = pick_backend(self.cfg)
        slug = re.sub(r"[^a-z0-9]+", "-", os.path.basename(self.root).lower()).strip("-")[:24] or "project"
        digest = hashlib.sha256(self.root.encode("utf-8", "surrogateescape")).hexdigest()[:8]
        self.slug = slug
        # OpenShell rejects sandbox names longer than 19 characters.
        self.name = os.environ.get("SBX_NAME") or f"sbx-{slug[:6].strip('-') or 'p'}-{digest}"
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        self.dir = Path(os.environ.get("SBX_STATE_DIR") or Path(base) / "sandboxed-shell") / self.name
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)  # the state lists every file name in the project
        self.state_file = self.dir / "state.json"
        self.state = self._load()

    # -- state on this machine
    def _load(self) -> dict:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("backend") != self.backend.name or state.get("root") != self.root:
            state = {}
        state.setdefault("local", {})
        state.setdefault("ack", "")
        return state

    def save(self) -> None:
        self.state.update(backend=self.backend.name, root=self.root)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state), encoding="utf-8")
        os.replace(temp, self.state_file)

    @contextlib.contextmanager
    def lock(self):
        with open(self.dir / "lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.state = self._load()
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    # -- sandbox lifecycle
    def ensure(self, force: bool = False) -> bool:
        """Make sure the sandbox is running and has the helper. Returns True when the backend
        was asked just now, False when a recent success was trusted instead."""
        state = self.state
        rules = self.rules.digest()  # the helper keeps a copy of the lists: changed settings must reach it
        if (not force and state.get("ready") and state.get("rules") == rules
                and time.time() - state.get("last_ok", 0) < self.backend.trust_seconds):
            return False
        phase, ident = self.backend.phase(self.name)
        if phase == "error":
            raise SbxError(f"sandbox '{self.name}' is in an error state; run 'sbx.py down --delete' and retry")
        if phase in ("stopped", "starting"):
            if phase == "stopped":
                self.backend.start(self.name)
            ident = self.backend.wait_ready(self.name)
            phase = "ready" if ident is not None else "missing"  # "starting" also covers a sandbox being removed
        if phase == "missing":
            warn(f"creating {self.backend.name} sandbox '{self.name}'")
            self.backend.create(self.name)
            ident = self.backend.wait_ready(self.name)
            if ident is None:
                raise SbxError(f"sandbox '{self.name}' disappeared right after it was created on {self.backend.name}")
        if state.get("sandbox_id") != ident or state.get("helper") != HELPER_VERSION or state.get("rules") != rules:
            self._bootstrap(ident)
        state.update(ready=True, last_ok=time.time())
        self.save()
        return True

    def _bootstrap(self, ident: str) -> None:
        code, out, err = self.backend.capture(self.name, ["pwd"])
        base = next((line.strip() for line in out.splitlines() if line.strip().startswith("/")), "")
        if code != 0 or not base:
            raise SbxError(f"could not find the sandbox working directory: {(err or out).strip()[-300:]}")
        meta, remote_root = f"{base}/{META}", f"{base}/{self.slug}"
        with tempfile.TemporaryDirectory() as temp:
            bundle = os.path.join(temp, "bootstrap.tar")
            with tarfile.open(bundle, "w") as tar:
                for member, data, mode in (
                    ("agent.sh", HELPER.encode(), 0o755),
                    ("excludes", b"".join(p.encode() + b"\0" for p in self.rules.blocked), 0o644),
                    ("keep", b"".join(p.encode() + b"\0" for p in self.rules.kept), 0o644),
                ):
                    info = tarfile.TarInfo(member)
                    info.size, info.mode = len(data), mode
                    tar.addfile(info, io.BytesIO(data))
            staged = f"{base}/.sbx-bootstrap.tar"
            self.backend.put(self.name, bundle, staged)
        script = 'mkdir -p "$1" && tar -xf "$2" -C "$1" && rm -f "$2"'
        code, out, err = self.backend.capture(self.name, ["sh", "-c", script, "sh", meta, staged])
        if code != 0:
            raise SbxError(f"could not install the sandbox helper: {(err or out).strip()[-300:]}")
        if self.state.get("sandbox_id") != ident:  # a new sandbox starts empty: mirror everything again
            self.state.update(local={}, ack="")
        self.state.update(sandbox_id=ident, helper=HELPER_VERSION, rules=self.rules.digest(),
                          base=base, meta=meta, remote_root=remote_root)

    def helper(self, operation: str, *args) -> list:
        return ["bash", f"{self.state['meta']}/agent.sh", operation, b64(self.state["remote_root"]), *args]

    def _answer(self, operation: str, *args) -> list:
        code, out, err = self.backend.capture(self.name, self.helper(operation, *args), timeout=1800)
        for line in out.splitlines():
            if line.startswith("SBX "):
                return line.split()[1:]
        raise SbxError(f"sandbox helper '{operation}' failed (exit {code}): {(err or out).strip()[-300:]}")

    # -- mirroring, both ways. When both sides changed the same path, this machine wins.
    def sync(self) -> dict:
        now = scan(self.root, self.rules)
        known = self.state["local"]
        # Files that exist here but were left out of the sandbox (too large, unreadable). They are
        # not offered again until they change, and the sandbox may never overwrite them.
        held = self.state.setdefault("unmirrored", {})
        limit = self.byte_limit()
        if self.state.get("unmirrored_limit") != limit:  # the size limit changed: offer every file again
            held.clear()
            self.state["unmirrored_limit"] = limit
        for path in [path for path in held if path not in now]:
            del held[path]
        changed = [path for path, sig in now.items() if known.get(path) != sig and held.get(path) != sig]
        deleted = [path for path in known if path not in now]
        report = {"pushed": 0, "pulled": 0, "removed": 0, "refused": []}

        counts = self._answer("changes", self.state.get("ack") or "-")
        try:
            remote_changed, remote_deleted, sequence = int(counts[0]), int(counts[1]), counts[2]
        except (ValueError, IndexError):
            raise SbxError("the sandbox helper gave an answer that cannot be read")
        if counts[3:] == ["1"]:
            warn("part of the project could not be read in the sandbox (permissions?); "
                 "deletions made there are not mirrored until it can")
        if remote_changed or remote_deleted:
            with tempfile.TemporaryDirectory() as temp:
                archive = os.path.join(temp, "pull.tar")
                self.backend.get(self.name, f"{self.state['meta']}/pull.tar", archive)
                try:
                    written, removed, refused = apply_pull_tar(
                        self.root, archive, self.rules, set(changed) | set(deleted) | set(held), known, limit)
                except (tarfile.TarError, EOFError) as exc:
                    raise SbxError(f"the sandbox sent an archive that cannot be read: {exc}")
            for path in written:
                full = os.path.join(self.root, path)
                with contextlib.suppress(OSError):
                    sig = signature(os.lstat(full), full)
                    if sig is not None:
                        known[path] = sig
            for path in removed:
                known.pop(path, None)
            self.state["ack"] = sequence
            report.update(pulled=len(written), removed=len(removed), refused=refused)

        if changed or deleted:
            with tempfile.TemporaryDirectory() as temp:
                archive = os.path.join(temp, "push.tar")
                # A directory that became a file or a link here is removed in the sandbox first: its copy
                # there may hold entries that are never mirrored (caches), and nothing unpacks over those.
                replaced = [path for path in changed if known.get(path) == ["d"] and now[path][0] != "d"]
                unreadable = []
                packed, too_big = build_push_tar(self.root, changed, deleted + replaced, archive, limit, unreadable)
                self.backend.put(self.name, archive, f"{self.state['meta']}/push.tar")
            self._answer("apply", self.state.get("ack") or "-")
            for path in packed:
                known[path] = now[path]
                held.pop(path, None)  # it is mirrored now; the sandbox may change it again
            for path in deleted:
                known.pop(path, None)
            for path in too_big + unreadable:
                held[path] = now[path]
            self.state["ack"] = ""
            report["pushed"] = len(packed)
            if too_big:
                warn(f"not copied to the sandbox (larger than {self.cfg.get('max_file_mb', 200)} MiB): "
                     + ", ".join(too_big[:5]) + (" ..." if len(too_big) > 5 else ""))
            if unreadable:
                warn("not copied to the sandbox (could not be read): "
                     + ", ".join(unreadable[:5]) + (" ..." if len(unreadable) > 5 else ""))
        self.state.update(ready=True, last_ok=time.time())
        self.save()
        if report["refused"]:
            warn("kept out of this machine (protected or conflicting paths): "
                 + ", ".join(report["refused"][:8]) + (" ..." if len(report["refused"]) > 8 else ""))
        return report

    def byte_limit(self) -> int:
        return int(float(self.cfg.get("max_file_mb", 200)) * 1024 * 1024)

    def local_dirty(self) -> bool:
        now = scan(self.root, self.rules)
        known = self.state["local"]
        held = self.state.get("unmirrored", {})
        if held and self.state.get("unmirrored_limit") != self.byte_limit():
            return True
        if any(path not in now for path in known):
            return True
        return any(known.get(path) != sig and held.get(path) != sig for path, sig in now.items())

    # -- running the agent's command
    def run(self, command: str, session: str, rel: str, timeout_s: int, nonce: str) -> int:
        text = command.encode("utf-8", "surrogateescape")
        packed = b64(command)
        if len(text) > INLINE_COMMAND_BYTES:  # too long for one argument once encoded: send it as a file
            with tempfile.TemporaryDirectory() as temp:
                script = os.path.join(temp, "command")
                with open(script, "wb") as handle:
                    handle.write(text)
                self.backend.put(self.name, script, f"{self.state['meta']}/cmd.{nonce}")
            packed = "@"
        argv = self.backend.exec_argv(self.name, self.helper(
            "run", b64(session), str(int(timeout_s)), packed, b64(rel), nonce))
        proc = _popen(argv, stdin=subprocess.DEVNULL, cwd=self.backend.cwd)
        try:
            return proc.wait(timeout=timeout_s + 60 if timeout_s else None)
        except subprocess.TimeoutExpired:
            warn(f"the command did not finish within {timeout_s}s and was stopped")
            self.stop(nonce)
            return 124
        except BaseException:  # interrupted or terminated: the command must not keep running in the sandbox
            self.stop(nonce)
            raise
        finally:
            if proc.poll() is None:
                proc.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)
                if proc.poll() is None:
                    proc.kill()

    def stop(self, nonce: str) -> None:
        with contextlib.suppress(Exception):
            self.backend.capture(self.name, self.helper("stop", nonce), timeout=20)

    def started(self, nonce: str) -> bool:
        return self._answer("ran", nonce)[:1] == ["yes"]


# --------------------------------------------------------------------- commands

def identity_at_start(box: "Sandbox"):
    """Ask the backend, in the background, which sandbox answers to this name right now.
    Used when a recent success was trusted instead of asking before the command."""
    seen = {}

    def ask():
        try:
            seen["id"] = box.backend.phase(box.name)[1]
        except Exception:
            seen["id"] = None

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()

    def answer():
        worker.join(120)
        return seen.get("id")
    return answer


def cmd_run(args) -> int:
    if len(args.command.encode("utf-8", "surrogateescape")) > MAX_COMMAND_BYTES:
        raise SbxError(f"the command is longer than {MAX_COMMAND_BYTES} bytes. "
                       "Put it in a script file in the project and run that")
    box = Sandbox(args.root)
    session = re.sub(r"[^A-Za-z0-9_.-]", "", args.session or "")[:64] or "default"
    timeout_s = math.ceil(args.timeout_ms / 1000) if args.timeout_ms and args.timeout_ms > 0 else 0
    here = os.path.realpath(os.getcwd())
    rel = os.path.relpath(here, box.root) if here == box.root or here.startswith(box.root + os.sep) else "."
    rel = "" if rel == "." else rel

    code = EXIT_SBX
    for attempt in (1, 2):
        with box.lock():
            asked = box.ensure(force=attempt > 1)
            ident = box.state.get("sandbox_id")
            if box.local_dirty():
                box.sync()
        at_start = (lambda: ident) if asked else identity_at_start(box)
        nonce = secrets.token_hex(8)
        code = box.run(args.command, session, rel, timeout_s, nonce)
        try:
            with box.lock():
                box.sync()
            return code
        except SbxError as exc:
            # The sandbox went away. Repeat the command only when it is certain that it did not run.
            if attempt == 2:
                raise
            with box.lock():
                box.ensure(force=True)
                same = box.state.get("sandbox_id") == ident
                ran = box.started(nonce) if same else False
            before = None if same else at_start()
            if not same and (before is None or before == ident):  # None: the backend could not say
                raise SbxError(f"the sandbox was replaced while the command was running ({exc}). "
                               "Its result is unknown; run the command again if that is safe")
            if ran:
                with box.lock():
                    box.sync()
                return code
            warn(f"the sandbox was not available ({exc}); it is running again and the command is run now")
    return code


def cmd_up(args) -> int:
    box = Sandbox(args.root)
    with box.lock():
        box.ensure(force=True)
        report = box.sync()
    print(f"sandbox {box.name} on {box.backend.name} is ready at {box.state['remote_root']} "
          f"(pushed {report['pushed']}, pulled {report['pulled']})")
    return 0


def cmd_status(args) -> int:
    box = Sandbox(args.root)
    phase, _ = box.backend.phase(box.name)
    print(json.dumps({
        "backend": box.backend.name,
        "sandbox": box.name,
        "phase": phase,
        "project": box.root,
        "sandbox_path": box.state.get("remote_root"),
        "mirrored_entries": len(box.state["local"]),
        "state_dir": str(box.dir),
    }, indent=2))
    return 0


def cmd_down(args) -> int:
    box = Sandbox(args.root)
    with box.lock():
        if args.delete:
            box.backend.delete(box.name)
            box.state = {"local": {}, "ack": ""}
        else:
            box.backend.stop(box.name)
            box.state["ready"] = False
        box.save()
    print(f"sandbox {box.name} {'deleted' if args.delete else 'stopped'}")
    return 0


def cmd_doctor(args) -> int:
    ok = True

    def check(label, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        print(f"{'ok  ' if passed else 'FAIL'} {label}{': ' + detail if detail else ''}")

    check("python", sys.version_info >= (3, 9), sys.version.split()[0])
    try:
        cfg = load_config()
        check("config", True, f"{config_path()} ({'found' if config_path().is_file() else 'defaults'})")
        backend = pick_backend(cfg)
        check("backend", True, backend.name)
        box = Sandbox(args.root, cfg)
        phase, _ = backend.phase(box.name)
        check("backend reachable", True, f"sandbox {box.name} is {phase}")
    except SbxError as exc:
        check("sandbox", False, str(exc))
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sbx", description="Run shell commands in a sandbox, not on this machine.")
    parser.add_argument("--version", action="version", version=f"sbx {VERSION}")
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="run one command in the sandbox")
    run.add_argument("--root", default=os.getcwd())
    run.add_argument("--session", default="default")
    run.add_argument("--timeout-ms", type=float, default=0)
    run.add_argument("command")
    run.set_defaults(func=cmd_run)
    for name, func, text in (
        ("up", cmd_up, "create the sandbox if needed and mirror the project into it"),
        ("sync", cmd_up, "mirror changes both ways now"),
        ("status", cmd_status, "show the sandbox for this project"),
        ("doctor", cmd_doctor, "check that a sandbox backend is usable"),
        ("down", cmd_down, "stop the sandbox (use --delete to remove it)"),
    ):
        item = sub.add_parser(name, help=text)
        item.add_argument("--root", default=os.getcwd())
        if name == "down":
            item.add_argument("--delete", action="store_true")
        item.set_defaults(func=func)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(129))
    try:
        return args.func(args)
    except SbxError as exc:
        warn(f"{exc}")
        warn("the sandbox layer failed; nothing was run on this machine instead")
        return EXIT_SBX
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # a fault in the runner must not pass for the command's own exit code
        warn(f"internal error ({type(exc).__name__}: {exc})")
        warn("the sandbox layer failed; nothing was run on this machine instead")
        return EXIT_SBX


if __name__ == "__main__":
    sys.exit(main())

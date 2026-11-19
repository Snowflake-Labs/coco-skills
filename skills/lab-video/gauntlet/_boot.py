"""_boot.py — find a Python interpreter that has a given module, and re-exec into it.

On a real machine the dependencies are never all in one interpreter: Playwright usually lives in a
project venv, snowflake-connector-python in the system Python. Rather than hardcode paths (which
rot) or fail with a bare ImportError, each script declares what it needs:

    from _boot import ensure
    ensure("playwright.sync_api")            # rendering / UI probes
    ensure("snowflake.connector")            # data probes

Override the search order with CRAFT_PYTHON=/path/to/python.
"""
import os
import shutil
import subprocess
import sys

_GUARD = "CRAFT_BOOTSTRAPPED"

# Ordered by likelihood. Missing paths are skipped silently.
# PRESLAB_PYTHON / DATALAB_PYTHON are legacy names still documented in two lab READMEs; they are
# honoured so those docs keep working. Prefer CRAFT_PYTHON in anything new.
CANDIDATES = [
    os.environ.get("CRAFT_PYTHON"),
    os.environ.get("PRESLAB_PYTHON"),
    os.environ.get("DATALAB_PYTHON"),
    sys.executable,
    "/opt/anaconda3/bin/python3",
    "/usr/local/bin/python3",
    "/opt/homebrew/bin/python3",
    shutil.which("python3"),
]

HINTS = {
    "playwright.sync_api": (
        "python3 -m venv ~/.craft-venv\n"
        "    ~/.craft-venv/bin/pip install playwright\n"
        "    ~/.craft-venv/bin/playwright install chromium"
    ),
    "snowflake.connector": "pip install snowflake-connector-python",
}


def _has(exe, module):
    if not exe or not os.path.exists(exe):
        return False
    try:
        r = subprocess.run([exe, "-c", "import %s" % module],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def find_python(module):
    """First interpreter that can import `module`, or None."""
    seen = set()
    for exe in CANDIDATES:
        if not exe or exe in seen:
            continue
        seen.add(exe)
        if _has(exe, module):
            return exe
    return None


def ensure(module):
    """Import-check `module` here, or re-exec into an interpreter that has it."""
    try:
        __import__(module)
        return sys.executable
    except ImportError:
        pass

    # Guard is per-module: a script may need two modules from two interpreters, and a single
    # flag would make the second lookup think it had already re-exec'd. Without any guard, a
    # venv that cannot import the module spawns processes forever — much worse than a clear error.
    guard = "%s:%s" % (_GUARD, module)
    if os.environ.get(guard):
        _die(module)

    exe = find_python(module)
    if not exe:
        _die(module)

    os.execve(exe, [exe] + sys.argv, dict(os.environ, **{guard: "1"}))


def ensure_playwright():
    """Back-compat alias for older callers."""
    return ensure("playwright.sync_api")


def _die(module):
    sys.stderr.write(
        "\nNo Python with '%s' was found.\n\nInstall it:\n    %s\n\n"
        "Then either put that interpreter first on PATH or set:\n"
        "    export CRAFT_PYTHON=/path/to/python\n\n"
        % (module, HINTS.get(module, "pip install " + module.split(".")[0]))
    )
    sys.exit(3)


if __name__ == "__main__":
    # Diagnostic: which interpreter would each module resolve to?
    mods = sys.argv[1:] or ["playwright.sync_api", "snowflake.connector"]
    ok = True
    for mod in mods:
        exe = find_python(mod)
        print("%-28s %s" % (mod, exe or "NOT FOUND"))
        ok = ok and bool(exe)
    sys.exit(0 if ok else 1)

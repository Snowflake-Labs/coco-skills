"""In-process CLI runners — avoids per-test Python subprocess spawn overhead."""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Callable


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str


def run_cli(fn: Callable[..., int], argv: list[str]) -> CliResult:
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        try:
            rc = fn(argv)
        except SystemExit as exc:
            code = exc.code
            rc = code if isinstance(code, int) else (0 if code is None else 1)
    return CliResult(rc, out_buf.getvalue(), err_buf.getvalue())


def run_cli_argv(fn: Callable[[], int], argv: list[str]) -> CliResult:
    old = sys.argv
    sys.argv = argv
    try:
        return run_cli(lambda _a=None: fn(), [])
    finally:
        sys.argv = old

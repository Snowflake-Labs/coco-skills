#!/usr/bin/env python3
"""Dependency-free YAML shim for the hydrator engine.

The engine ships inside customer plugin zips and must run on a bare `python3`
with no pip packages (no PyYAML). This module emits and re-reads the *narrow*
YAML dialect the hydrator itself produces for generated ``hydration_hints.yaml``
files. It is deliberately NOT a general YAML parser.

The dialect, produced only by :func:`dump`, is:

* 2-space block indentation, no tabs, no flow collections except empty ``{}``/``[]``.
* Every scalar is serialized with :func:`json.dumps`, so it is simultaneously
  valid JSON and valid YAML and needs no YAML-specific escaping.
* Sequence items are written as a bare ``-`` line followed by the item block
  indented two spaces beneath the dash (never inline ``- key:``), which keeps
  the reader a simple indentation parser.

Because :func:`load` only ever consumes output written by :func:`dump`, a
round-trip test (see tests/test_minyaml.py) is sufficient to trust it.
"""

from __future__ import annotations

import json
from typing import Any

INDENT = "  "


def _dump(value: Any, level: int, out: list[str]) -> None:
    pad = INDENT * level
    if isinstance(value, dict):
        if not value:
            out[-1] += " {}"
            return
        for key, val in value.items():
            _dump_kv(key, val, level, out)
    elif isinstance(value, list):
        if not value:
            out[-1] += " []"
            return
        for item in value:
            out.append(f"{pad}-")
            if isinstance(item, (dict, list)):
                _dump(item, level + 1, out)
            else:
                out[-1] += " " + json.dumps(item)
    else:
        out[-1] += " " + json.dumps(value)


def _dump_kv(key: str, val: Any, level: int, out: list[str]) -> None:
    pad = INDENT * level
    if isinstance(val, dict) and val:
        out.append(f"{pad}{key}:")
        _dump(val, level + 1, out)
    elif isinstance(val, list) and val:
        out.append(f"{pad}{key}:")
        _dump(val, level + 1, out)
    else:
        out.append(f"{pad}{key}:")
        _dump(val, level, out)


def dump(data: dict[str, Any]) -> str:
    """Serialize a dict into the narrow block-YAML dialect."""
    out: list[str] = []
    if not data:
        return "{}\n"
    for key, val in data.items():
        _dump_kv(key, val, 0, out)
    return "\n".join(out) + "\n"


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: list[str], start: int, indent: int) -> tuple[Any, int]:
    idx = start
    # Determine container type from the first line at this indent.
    if idx >= len(lines):
        return {}, idx
    first = lines[idx]
    if first.lstrip(" ").startswith("-"):
        return _parse_seq(lines, start, indent)
    return _parse_map(lines, start, indent)


def _parse_map(lines: list[str], start: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    idx = start
    while idx < len(lines):
        line = lines[idx]
        cur = _indent_of(line)
        if cur < indent:
            break
        if cur > indent:  # defensive; should be consumed by recursion
            idx += 1
            continue
        content = line.strip()
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest:
            result[key] = json.loads(rest)
            idx += 1
        else:
            child, idx = _parse_block(lines, idx + 1, indent + 2)
            result[key] = child
    return result, idx


def _parse_seq(lines: list[str], start: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    idx = start
    while idx < len(lines):
        line = lines[idx]
        cur = _indent_of(line)
        if cur < indent:
            break
        stripped = line.strip()
        if not stripped.startswith("-"):
            break
        rest = stripped[1:].strip()
        if rest:
            result.append(json.loads(rest))
            idx += 1
        else:
            child, idx = _parse_block(lines, idx + 1, indent + 2)
            result.append(child)
    return result, idx


def load(text: str) -> dict[str, Any]:
    """Parse text previously produced by :func:`dump`."""
    raw = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not raw or raw[0].strip() in ("{}", "[]"):
        return {}
    data, _ = _parse_block(raw, 0, 0)
    return data if isinstance(data, dict) else {}

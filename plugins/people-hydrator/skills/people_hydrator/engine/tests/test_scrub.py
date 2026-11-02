"""Content check for external sharing: the plugin must not carry internal or personal data.

Generic rules live here. Organisation-specific private terms (internal system,
database or people names) are NOT stored in the repo: put them one per line in a
file and point SCRUB_DENYLIST at it, e.g.

    SCRUB_DENYLIST=~/.config/people_hydrator/denylist.txt python3 -m pytest engine/tests

Files under templates/*/evals/ are checked too: they ship with the plugin.
"""

import os
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[4]  # .../people-hydrator
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".git"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".sh", ".sql", ".txt", ".toml"}

# Databases that may appear in fully qualified names: product schemas, the synthetic
# test account, and neutral examples used in docs and tests.
ALLOWED_DATABASES = {"SNOWFLAKE", "HR_SYNTHETIC_DATA", "ACME_FINANCE", "HR_DB", "HR", "HR_DEV", "DB", "DB1", "DB2", "X", "NOPE_DB",
                     "INFORMATION_SCHEMA"}
FQN = re.compile(r"\b([A-Z][A-Z0-9_$]{1,})\.([A-Z][A-Z0-9_$]{1,})\.([A-Z][A-Z0-9_$]{2,})\b")
RULES = {
    "personal path": re.compile(r"/Users/[A-Za-z]|/home/[a-z]+/|C:\\\\Users\\\\"),
    "scratch path": re.compile(r"(?<![\w.])/tmp/[A-Za-z]"),
    "email address": re.compile(r"\b[A-Za-z0-9._%+-]+@(?![A-Za-z0-9.-]*example\b)[A-Za-z0-9.-]+\.[a-z]{2,}\b"),
    "private host": re.compile(r"\b[a-z0-9-]+\.(atlassian\.net|slack\.com|sharepoint\.com|my\.salesforce\.com)\b"),
    "account locator": re.compile(r"\b[a-z]{7}-[a-z]{2}\d{5}\b"),
    "confidentiality marker": re.compile(r"\b(company[- ]internal|do not distribute|proprietary and confidential)\b", re.I),
}


def shipped_files():
    for p in PLUGIN_ROOT.rglob("*"):
        if p.is_file() and p.suffix in TEXT_SUFFIXES and not SKIP_DIRS & set(p.parts):
            yield p


def private_terms() -> list[str]:
    path = os.environ.get("SCRUB_DENYLIST")
    if not path:
        return []
    return [t.strip().lower() for t in Path(path).expanduser().read_text().splitlines()
            if t.strip() and not t.startswith("#")]


def findings() -> list[str]:
    out, terms = [], private_terms()
    this = Path(__file__).resolve()
    for p in shipped_files():
        if p.resolve() == this:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(PLUGIN_ROOT)
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in RULES.items():
                if rx.search(line):
                    out.append(f"{rel}:{n}: {name}: {line.strip()[:120]}")
            for db, _s, _o in FQN.findall(line):
                if db not in ALLOWED_DATABASES:
                    out.append(f"{rel}:{n}: unknown database {db}: {line.strip()[:120]}")
            low = line.lower()
            for t in terms:
                if t in low:
                    out.append(f"{rel}:{n}: private term: {line.strip()[:120]}")
    return out


def test_no_internal_or_personal_content():
    found = findings()
    assert not found, "\n" + "\n".join(found[:60]) + (f"\n… {len(found) - 60} more" if len(found) > 60 else "")


if __name__ == "__main__":
    for f in findings():
        print(f)

"""Content check for external sharing: the plugin must not carry internal or personal data.

Scans every text file in the whole finance-hydrator plugin (including
skills/bva_variance_setup), skipping node_modules, package-lock.json and
LICENSE files. Organisation-specific private terms are NOT stored in the repo:
put them one per line in a file and point SCRUB_DENYLIST at it, e.g.

    SCRUB_DENYLIST=~/.config/finance_hydrator/denylist.txt \\
        python3 -m unittest discover -s engine/tests
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from _support import PLUGIN_DIR

SKIP_DIRS = {"node_modules", ".venv", "__pycache__", ".pytest_cache", ".git", ".next"}
SKIP_FILES = {"package-lock.json"}
TEXT_SUFFIXES = {
    ".md", ".py", ".json", ".yaml", ".yml", ".sh", ".sql", ".txt", ".toml",
    ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".svg",
}
RULES = {
    "internal user prefix": re.compile(r"sfc-gh-", re.I),
    "internal connection/account": re.compile(r"\bsnowhouse\b", re.I),
    "company email": re.compile(r"@snowflake\.com\b", re.I),
    "slack channel": re.compile(r"(?<![\w/(.#&-])#[a-z][a-z0-9]*-[a-z0-9-]+\b"),
    "go/ link": re.compile(r"(?<![\w/.-])go/[a-z0-9]"),
    "account locator": re.compile(r"\b[a-z]{7}-[a-z]{2}\d{5}\b"),
    "personal path": re.compile(r"/Users/[A-Za-z]|/home/[a-z]+/|C:\\\\Users\\\\"),
    "internal database": re.compile(r"\bFINANCE\.STG_|\bSNOW_CERTIFIED\b", re.I),
    "private host": re.compile(r"\b[a-z0-9-]+\.(atlassian\.net|slack\.com|sharepoint\.com|my\.salesforce\.com)\b"),
    "confidentiality marker": re.compile(
        r"\b(company[- ]internal|proprietary and confidential)\b", re.I),
}


def shipped_files():
    for path in PLUGIN_DIR.rglob("*"):
        if not path.is_file() or SKIP_DIRS & set(path.parts):
            continue
        if path.name in SKIP_FILES or path.name.upper().startswith("LICENSE"):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {".gitignore"}:
            yield path


def private_terms() -> list[str]:
    path = os.environ.get("SCRUB_DENYLIST")
    if not path:
        return []
    return [t.strip().lower() for t in Path(path).expanduser().read_text().splitlines()
            if t.strip() and not t.startswith("#")]


def findings() -> list[str]:
    out, terms = [], private_terms()
    this = Path(__file__).resolve()
    for path in shipped_files():
        if path.resolve() == this:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(PLUGIN_DIR)
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in RULES.items():
                if rx.search(line):
                    out.append(f"{rel}:{n}: {name}: {line.strip()[:120]}")
            low = line.lower()
            for term in terms:
                if term in low:
                    out.append(f"{rel}:{n}: private term: {line.strip()[:120]}")
    return out


class ScrubTests(unittest.TestCase):
    def test_no_internal_or_personal_content(self):
        found = findings()
        more = f"\n... {len(found) - 60} more" if len(found) > 60 else ""
        self.assertFalse(found, "\n" + "\n".join(found[:60]) + more)

    def test_rules_fire(self):
        samples = {
            "internal user prefix": "user SFC-GH-JDOE",
            "internal connection/account": "connection: snowhouse",
            "company email": "ping jane@snowflake.com",
            "slack channel": "ask in #finance-data-help",
            "go/ link": "see go/finance-wiki",
            "account locator": "abcdefg-xy12345",
            "personal path": "/Users/jdoe/x",
            "internal database": "select * from finance.stg_workday.x",
        }
        for name, sample in samples.items():
            self.assertTrue(RULES[name].search(sample), name)
        for benign in ("[Demo data](#demo-data)", "docs.example/page#label-cortex-agents",
                       "C# code", "cargo/xyz", "&#x27;"):
            self.assertFalse(RULES["slack channel"].search(benign), benign)
            self.assertFalse(RULES["go/ link"].search(benign), benign)


if __name__ == "__main__":
    unittest.main()

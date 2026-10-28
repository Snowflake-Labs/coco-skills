"""Shared helpers for the Business Ontology migration path (Path M).

Kept free of third-party imports at module level so the pure mapping logic in
export_to_bon.py can be unit-tested without snowflake-connector-python.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# The complete Business Ontology relationship vocabulary (R-00). Anything else is
# rejected by the API or silently mis-stored, so validate before every write.
BON_RELATIONSHIP_TYPES = frozenset({
    "HAS_VARIANT", "HAS_PART", "DERIVES", "MEASURES", "IDENTIFIED_BY",
    "CLASSIFIES", "APPLIES_TO", "SCOPES", "EQUIVALENT_TO", "RELATED_TO", "CUSTOM",
})

BON_ITEM_KINDS = frozenset({"TERM", "METRIC", "ENTITY", "DIMENSION_CONCEPT", "MEASURE_CONCEPT"})

# BON silently truncates long descriptions; cap ourselves so nothing is cut mid-word.
MAX_DESCRIPTION = 270

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def check_identifier(value: str, what: str) -> str:
    """Accept only plain unquoted Snowflake identifiers for DB/schema/table names
    that are interpolated into SQL text (they cannot be bound as parameters)."""
    if not value or not _IDENT_RE.match(value):
        raise ValueError(f"Invalid {what} identifier: {value!r}")
    return value.upper()


def humanize(name: str) -> str:
    """PurchaseOrder / PURCHASE_ORDER / purchase-order -> 'Purchase Order'."""
    if not name:
        return name
    s = re.sub(r"[_\-]+", " ", name.strip())
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    words = [w for w in s.split() if w]
    # Keep short acronyms (FIGI, KPI) only when the source was mixed-case; an
    # all-caps source name (PURCHASE_ORDER) carries no acronym signal.
    keep_acronyms = not name.isupper()
    return " ".join(
        w if (keep_acronyms and w.isupper() and len(w) <= 4) else w[0].upper() + w[1:].lower()
        for w in words
    )


def norm_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", (name or "").lower())


def cap_description(text: str | None) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_DESCRIPTION:
        return text
    cut = text[: MAX_DESCRIPTION - 3]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:") + "..."


def checksum(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def parse_json_result(raw: Any) -> dict:
    """SYSTEM$ glossary functions return a JSON string with an error envelope."""
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        return {"error": True, "errorMessage": f"Unparseable response: {str(raw)[:200]}"}
    return out if isinstance(out, dict) else {"error": False, "value": out}


def connect(connection_name: str):
    """Open a Snowflake connection from ~/.snowflake/connections.toml."""
    import warnings

    # The connector emits a FutureWarning about its config options on every connect; it is noise in script output.
    warnings.filterwarnings("ignore", category=FutureWarning, module=r"snowflake\.connector")
    import snowflake.connector  # imported lazily: tests do not need it

    return snowflake.connector.connect(connection_name=connection_name)


def call_system(cur, function: str, *args: Any) -> dict:
    """Call a SYSTEM$ glossary function with client-side bound literals.

    The connector's pyformat binding renders each argument as an escaped string
    literal, which is what these functions require (they reject PARSE_JSON and
    other non-constant arguments). JSON arguments must already be serialised once.
    """
    if not re.match(r"^SYSTEM\$[A-Z_]+$", function):
        raise ValueError(f"Unexpected function name {function!r}")
    placeholders = ", ".join(["%s"] * len(args))
    cur.execute(f"SELECT {function}({placeholders})", args)
    row = cur.fetchone()
    return parse_json_result(row[0] if row else None)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def asset_ref_type(a: dict) -> str | None:
    """Read-side association objects carry objectType ("Column", "View", "Table",
    "Semantic View") rather than the refType used on writes. Normalise either."""
    t = a.get("refType") or a.get("objectType")
    return str(t).upper().replace(" ", "_") if t else None


def asset_key(term: str, a: dict) -> tuple:
    return (term, asset_ref_type(a), (a.get("fqn") or "").upper(), (a.get("dimensionName") or "").upper())

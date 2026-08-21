"""Deterministic edge-case mock data generator + file writers for the validation
skill. Single module: generates rows/values AND writes them in any format (the
former mock_io.py writers are folded in at the bottom of this file).

Takes a mined/declared schema (list of {name, type, nullable}) and produces
realistic, seeded mock rows that deliberately exercise per-column edge cases
(nulls, empty strings, numeric boundaries, unicode, negatives, zero), then
materializes them to the file format the workload's reader expects:

  - csv          -> header + delimited rows            (write_mock_csv)
  - json / jsonl -> one JSON object per line (tabular)  (write_mock_json)
  - parquet      -> typed pyarrow incl. nested types     (write_mock_parquet)
  - json-document (NON-tabular config blobs read via json.load) -> generate_document()

Nested Spark types (struct<...>, array<...>, map<...>) are produced as native
Python dict/list values so the typed parquet/JSON writers serialize them
correctly. Cross-source referential integrity is supported via ``key_pools``
so join keys overlap across sibling sources.

Realism: if the optional ``faker`` package is installed, string columns whose
name implies a semantic type (email, name, city, phone, ...) get realistic
typical values; our own engine still injects edge cases / nulls / boundaries on
top. Without faker we fall back to synthetic tokens (val_###) — structurally
valid but less realistic. Numeric/temporal values always come from the
type-aware pools.

Design choice (vs dbldatagen): pure-Python + stdlib (+ optional faker), no
Spark/JVM, instant for the <100-row mocks the harness needs. See
data-synthesis-technical-proposal.md.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import os
import pathlib
import random
import re
import sys
from typing import Any

# Ensure helpers.py (in harness/) is importable for the schema_hash re-export.
_HARNESS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness")
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)


def _ensure_libstdcxx_preload() -> None:
    """Re-exec this CLI with LD_PRELOAD=<system libstdc++> on Linux.

    The skill venv's numpy/pyarrow wheels can fail to load the host's
    ``libstdc++.so.6`` (GLIBCXX) on some platforms (e.g. Amazon Linux 2023 /
    aarch64). Preloading the system library fixes it. We do this here — scoped
    to the datagen process only, via a one-shot re-exec — so callers never need
    to set ``LD_PRELOAD`` by hand (and we never touch ``LD_LIBRARY_PATH``, which
    would break ``uv``). No-op off Linux, if the library is absent, or once the
    re-exec has already happened.
    """
    if os.environ.get("_SCOS_DATAGEN_PRELOADED"):
        return
    if not sys.platform.startswith("linux"):
        return
    preload = "/usr/lib64/libstdc++.so.6"
    if not os.path.exists(preload):
        return
    env = dict(os.environ)
    existing = env.get("LD_PRELOAD", "")
    if preload not in existing.split(os.pathsep):
        env["LD_PRELOAD"] = preload + (os.pathsep + existing if existing else "")
    env["_SCOS_DATAGEN_PRELOADED"] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


# ---------------------------------------------------------------------------
# Stable content-hash for table entries (used by datagen and the harness provisioning)
# ---------------------------------------------------------------------------

def schema_hash(table_entry: dict) -> str:
    """SHA-256 hex digest of the canonical JSON for fields affecting DDL/generation.

    Canonical implementation lives in helpers.py (kit-resident); this is a
    re-export so callers that ``import datagen`` keep working.
    """
    from helpers import schema_hash as _impl  # type: ignore[import-not-found]
    return _impl(table_entry)


def _canonical_payload(table_entry: dict) -> str:
    """Deterministic JSON string of the hash-relevant fields.

    Canonical implementation lives in helpers.py; re-exported for test compat.
    """
    from helpers import _canonical_payload as _impl  # type: ignore[import-not-found]
    return _impl(table_entry)


# ---------------------------------------------------------------------------
# Type parsing (shared shape with mock_io / schema_mine)
# ---------------------------------------------------------------------------

_SCALAR = {
    "string", "varchar", "text", "char",
    "int", "integer", "long", "bigint", "short", "byte",
    "double", "float", "real", "decimal", "numeric",
    "boolean", "bool", "date", "timestamp", "timestamp_ntz",
    "timestamp_ltz", "timestamp_tz", "binary",
}


def _base(t: str) -> str:
    return t.strip().lower().split("(")[0].split("<")[0].strip()


def _split_top(s: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in s:
        if ch == "<":
            depth += 1; cur.append(ch)
        elif ch == ">":
            depth -= 1; cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


# ---------------------------------------------------------------------------
# Per-type value strategies, including edge cases
# ---------------------------------------------------------------------------

_UNICODE = "café—naïve—Ω—日本—😀"
_DEC_RE = re.compile(r"\((\d+)\s*,\s*(\d+)\)")

# Spark type aliases -> the canonical base datagen generates/writes for. MUST
# mirror helpers._SPARK_TYPE_MAP so the parquet physical type matches the strict
# Spark schema the harness reads with (integer->IntegerType, smallint->ShortType,
# real->DoubleType, numeric->DecimalType, ...).
_TYPE_ALIASES = {
    "integer": "int", "bigint": "long", "smallint": "short", "tinyint": "byte",
    "numeric": "decimal", "real": "double", "bool": "boolean",
}


def _canon_base(t: str) -> str:
    """Canonical base type after alias normalization (smallint->short, etc.)."""
    b = _base(t)
    return _TYPE_ALIASES.get(b, b)

# Optional realism layer: if Faker is installed, string columns whose NAME implies
# a semantic type (email, person, city, ...) get realistic typical values. Edge
# cases / nulls / boundaries are still injected by our own engine on top. Without
# Faker we fall back to synthetic tokens (val_###) -- structurally valid, less real.
try:
    from faker import Faker as _Faker
except Exception:  # pragma: no cover - faker optional
    _Faker = None

# (regex on lowercased column name) -> Faker provider method. First match wins.
def _tokenize(name: str) -> set:
    """Split a column name into lowercase tokens (snake_case + camelCase + digits)
    so matching is on whole words, not substrings (avoids estate->state,
    capacity->city false positives)."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t}


def _faker_string(name: str, fake) -> str | None:
    """Pick a realistic value for a string column from its NAME tokens, or None.
    Token-based (word-level) with compound handling (first/last/user name)."""
    toks = _tokenize(name)
    if not toks:
        return None

    def has(*kw):
        return any(k in toks for k in kw)

    if has("email", "mail"):
        method = "email"
    elif has("uuid", "guid"):
        method = "uuid4"
    elif has("id", "key", "uid", "sk", "fk", "pk"):
        method = "uuid4"            # realistic identifier (shared via key pools)
    elif has("ip", "ipaddr", "ipv4"):
        method = "ipv4"
    elif has("fname") or ("first" in toks and "name" in toks):
        method = "first_name"
    elif has("lname", "surname") or ("last" in toks and "name" in toks):
        method = "last_name"
    elif has("username", "login", "handle") or ("user" in toks and "name" in toks):
        method = "user_name"
    elif has("phone", "mobile", "tel", "telephone", "fax", "msisdn"):
        method = "phone_number"
    elif has("street", "address", "addr"):
        method = "street_address"
    elif has("city", "town"):
        method = "city"
    elif has("state", "province"):
        method = "state"
    elif has("zip", "zipcode", "postal", "postcode"):
        method = "postcode"
    elif has("country"):
        method = "country_code" if has("code", "cd", "iso") else "country"
    elif has("currency"):
        method = "currency_code"
    elif has("url", "uri", "website", "link", "href", "homepage"):
        method = "url"
    elif has("domain"):
        method = "domain_name"
    elif has("company", "organization", "org", "employer", "vendor", "merchant",
             "supplier", "retailer", "manufacturer"):
        method = "company"
    elif has("color", "colour"):
        method = "color_name"
    elif has("job", "occupation", "role", "position", "designation"):
        method = "job"
    elif has("description", "desc", "comment", "comments", "note", "notes",
             "remark", "remarks", "message", "summary", "bio"):
        method = "sentence"
    elif has("customer", "contact", "person", "employee", "borrower", "payee",
             "payer", "owner", "author", "recipient", "sender", "applicant",
             "client", "fullname") or toks == {"name"}:
        method = "name"
    elif has("product", "item", "sku", "category", "brand", "merchandise"):
        method = "word"
    elif has("date", "dob", "birthdate"):
        method = "date"             # string-typed date column -> 'YYYY-MM-DD'
    elif has("code", "cd", "abbr", "abbreviation", "type", "status", "flag"):
        method = "__code"           # short uppercase code (no faker provider)
    else:
        return None
    if method == "__code":
        try:
            return (fake.lexify("???") + fake.numerify("##")).upper()
        except Exception:
            return None
    try:
        return str(getattr(fake, method)())
    except Exception:
        return None


def _date_semantic_string(name: str, rng: random.Random) -> "str | None":
    """For a STRING column whose NAME implies a date/period, return a recent value
    anchored near today (else None). Without this, columns like ``year_month`` get
    junk ``val_###`` mocks and relative-date filters (``year_month >= ...``) keep no
    rows — a clean run with empty output. Only used when no explicit filter
    ``values`` were mined for the column (those take precedence)."""
    toks = set(re.split(r"[^a-z0-9]+", name.lower()))
    flat = name.lower().replace("_", "")
    today = _dt.date.today()

    def back(k: int) -> _dt.date:
        y, m = today.year, today.month - k
        while m <= 0:
            m += 12
            y -= 1
        return _dt.date(y, m, min(today.day, 28))

    k = rng.randint(0, 6)
    # year-month period columns -> 'YYYY-MM'
    is_ym = ("yearmonth" in flat or "yyyymm" in flat
             or ("year" in toks and "month" in toks)
             or bool(toks & {"yearmonth", "yrmonth", "yearmo", "period", "yearmth"})
             or (("month" in toks or "mth" in toks or "mnth" in toks)
                 and not toks & {"amount", "amt"}))
    if is_ym:
        return back(k).strftime("%Y-%m")
    # full-date columns -> 'YYYY-MM-DD'
    is_date = (bool(toks & {"date", "dt", "dob", "day", "asof", "asofdate"})
               or flat.endswith("date") or flat.endswith("dt"))
    if is_date:
        return back(k).strftime("%Y-%m-%d")
    return None


def _scalar_pool(t: str, rng: random.Random) -> dict[str, list]:
    """Return {'typical': [...], 'edge': [...]} value pools for a scalar type."""
    b = _canon_base(t)
    if b in ("string", "varchar", "text", "char"):
        typ = [f"val_{rng.randint(100, 999)}" for _ in range(4)]
        return {"typical": typ, "edge": ["", " leading", "trailing ", _UNICODE, "a" * 256, "NULL"]}
    if b in ("int", "short", "byte"):
        lim = {"int": 2**31 - 1, "short": 2**15 - 1, "byte": 2**7 - 1}[b]
        hi = min(1000, lim)  # keep typical values within the type's range
        return {"typical": [rng.randint(1, hi) for _ in range(4)],
                "edge": [0, 1, -1, lim, -lim - 1]}
    if b in ("long", "bigint"):
        return {"typical": [rng.randint(1, 10**6) for _ in range(4)],
                "edge": [0, 1, -1, 2**63 - 1, -(2**63)]}
    if b in ("double", "float"):
        return {"typical": [round(rng.uniform(0, 1000), 3) for _ in range(4)],
                "edge": [0.0, -1.5, 1e-9, 1e12, -0.0]}
    if b == "decimal":
        m = _DEC_RE.search(t.lower())
        scale = int(m.group(2)) if m else 2
        prec = int(m.group(1)) if m else 10
        hi = 10 ** (prec - scale) - 1
        return {"typical": [round(rng.uniform(0, min(hi, 9999)), scale) for _ in range(4)],
                "edge": [round(0, scale), round(1, scale), hi, round(0.1 ** scale, scale)]}
    if b in ("boolean", "bool"):
        return {"typical": [True, False], "edge": [True, False]}
    if b == "date":
        # Anchor near TODAY (not a fixed past date) so relative-date filters
        # (`WHERE d >= current_date - N`, MTD, last-N-months) actually keep rows.
        # A fixed historical anchor makes every such filter empty, which both
        # phases reproduce (empty==empty) — a hollow pass that tests no data.
        # Offsets guarantee a value inside the last week / month / quarter / ~7mo.
        base = _dt.date.today()
        typ = [(base + _dt.timedelta(days=o + rng.randint(-2, 2))).isoformat()
               for o in (-3, -25, -90, -200)]
        return {"typical": typ, "edge": ["1970-01-01", "2099-12-31", base.isoformat()]}
    if b in ("timestamp", "timestamp_ntz", "timestamp_ltz", "timestamp_tz"):
        base = _dt.datetime.now().replace(microsecond=0)
        typ = [(base + _dt.timedelta(days=o + rng.randint(-2, 2))).strftime("%Y-%m-%d %H:%M:%S")
               for o in (-3, -25, -90, -200)]
        return {"typical": typ, "edge": ["1970-01-01 00:00:00", "2099-12-31 23:59:59"]}
    if b == "binary":
        return {"typical": [b"\x01\x02", b"data"], "edge": [b"", b"\x00"]}
    return {"typical": ["x"], "edge": [""]}


def _gen_nested(t: str, rng: random.Random, depth: int = 0) -> Any:
    """Generate one native value for struct<>/array<>/map<>/scalar."""
    b = _base(t)
    tl = t.strip()
    if b == "struct" and "<" in tl:
        inner = tl[tl.index("<") + 1: tl.rindex(">")]
        obj = {}
        for field in _split_top(inner):
            if ":" not in field:
                continue
            fn, ft = field.split(":", 1)
            obj[fn.strip()] = _gen_nested(ft.strip(), rng, depth + 1)
        return obj
    if b == "array" and "<" in tl:
        elem = tl[tl.index("<") + 1: tl.rindex(">")]
        return [_gen_nested(elem, rng, depth + 1) for _ in range(rng.randint(1, 3))]
    if b == "map" and "<" in tl:
        inner = tl[tl.index("<") + 1: tl.rindex(">")]
        parts = _split_top(inner)
        if len(parts) >= 2:
            return {f"k{i}": _gen_nested(parts[1].strip(), rng, depth + 1) for i in range(2)}
        return {}
    pool = _scalar_pool(b, rng)
    return rng.choice(pool["typical"])


# name-based numeric ranges so semantically-bounded columns look realistic
_RATIO_TOKENS = {"ratio", "rate", "score", "confidence", "probability", "prob", "pct", "percent"}


def _numeric_typical(name: str, b: str, rng: random.Random):
    """Bounded numeric value inferred from the column name, or None to fall back
    to the generic type pool."""
    if b not in ("double", "float", "real", "decimal", "numeric"):
        return None
    toks = _tokenize(name)
    if "lat" in toks or "latitude" in toks:
        return round(rng.uniform(-90, 90), 6)
    if "lon" in toks or "lng" in toks or "longitude" in toks:
        return round(rng.uniform(-180, 180), 6)
    if {"pct", "percent"} & toks:
        return round(rng.uniform(0, 100), 2)
    if _RATIO_TOKENS & toks:
        return round(rng.uniform(0, 1), 4)
    return None


# ---------------------------------------------------------------------------
# Row generation with edge-case coverage
# ---------------------------------------------------------------------------

def _edge_eligible(c: dict, key_pools: dict, categoricals: dict) -> bool:
    if c["name"] in key_pools or c["name"] in categoricals or _is_keylike(c["name"]):
        return False
    return _base(c.get("type", "string")) in _SCALAR


def _coverage_row_target(schema: list[dict], key_pools: dict | None = None,
                         categoricals: dict | None = None, *, min_rows: int = 12) -> int:
    """Rows needed for full per-column coverage (baseline + nulls + one edge each)."""
    cols = [c for c in schema if c.get("name")]
    key_pools = key_pools or {}
    categoricals = categoricals or {}
    nullable = sum(1 for c in cols if c.get("nullable", True) and not _is_keylike(c["name"]))
    edges = sum(1 for c in cols if _edge_eligible(c, key_pools, categoricals))
    return max(min_rows, 1 + nullable + edges)


_TS_BASES = ("timestamp", "timestamp_ntz", "timestamp_ltz", "timestamp_tz")
_INT_BASES = ("int", "integer", "long", "bigint", "short", "byte")
_FLOAT_BASES = ("double", "float", "real", "decimal", "numeric")


def _distinct_values(col_type: str, count: int) -> list:
    """``count`` DISTINCT, type-appropriate, sortable values for a column flagged
    ``"unique": true`` — so the column has no repeated values.

    Temporal/numeric columns get a strictly monotone sequence; strings get
    zero-padded sortable tokens. Booleans (domain of 2) cannot be made distinct
    beyond two rows, so a ``unique`` boolean column is best-effort only.
    """
    b = _canon_base(col_type)
    if b == "date":
        base_d = _dt.date.today()
        return [(base_d - _dt.timedelta(days=i)).isoformat() for i in range(count)]
    if b in _TS_BASES:
        base_t = _dt.datetime.now().replace(microsecond=0)
        return [(base_t - _dt.timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
                for i in range(count)]
    if b in _INT_BASES:
        return list(range(1, count + 1))
    if b in _FLOAT_BASES:
        return [float(i) for i in range(1, count + 1)]
    if b in ("boolean", "bool"):
        # Tiny domain: a boolean column can't be made distinct beyond its
        # cardinality (2), so repeats are unavoidable past two rows.
        return [(True, False)[i % 2] for i in range(count)]  # domain-limited: best effort
    return ["uniq_%06d" % i for i in range(count)]


def _apply_unique_columns(rows: list[dict], cols: list[dict],
                          key_pools: dict | None = None) -> None:
    """Overwrite columns flagged ``"unique": true`` with DISTINCT, type-appropriate
    values (one per row) so a primary-key-like column has no repeated values.

    A column that is ALSO in ``key_pools`` (a shared join pool) is left drawing
    from its pool: ``unique`` + shared-join-key is a known tension left to the
    caller, since forcing distinct values would destroy the join overlap the pool
    exists to provide. Standalone ``unique`` columns are the common case.

    Separately, ``unique`` + range-boundary is range-precedence: a later
    :func:`_inject_range_overlap` may overwrite a boundary column here, and range
    correctness wins (see that function for how it avoids introducing a duplicate).
    """
    key_pools = key_pools or {}
    col_type = {c["name"]: c.get("type", "string") for c in cols}
    for c in cols:
        if not c.get("unique"):
            continue
        name = c["name"]
        if name in key_pools and key_pools[name]:
            continue  # unique + shared-join-key: leave pool behavior (caller's tension)
        vals = _distinct_values(col_type[name], len(rows))
        for i, row in enumerate(rows):
            row[name] = vals[i]


def _range_anchor(col_type: str) -> tuple:
    """(low, mid, high) with ``low <= mid <= high``, type-correct for a range-join
    ``point`` column (V1). Injecting ``mid`` into the point table and ``(low, high)``
    into the partner table guarantees >=1 row satisfies ``low <= point <= high``
    regardless of the two tables being generated independently."""
    b = _canon_base(col_type)
    if b == "date":
        d = _dt.date.today()
        return ((d - _dt.timedelta(days=3650)).isoformat(), d.isoformat(),
                (d + _dt.timedelta(days=3650)).isoformat())
    if b in _TS_BASES:
        t = _dt.datetime.now().replace(microsecond=0)
        f = "%Y-%m-%d %H:%M:%S"
        return ((t - _dt.timedelta(days=3650)).strftime(f), t.strftime(f),
                (t + _dt.timedelta(days=3650)).strftime(f))
    if b in _INT_BASES:
        hi = {"byte": 127, "short": 1000}.get(b, 1000)
        return (0, 1, hi)
    if b in _FLOAT_BASES:
        return (0.0, 1.0, 1000.0)
    return ("!", "M", "~")  # lexicographic: low <= mid <= high by ASCII


def _inject_range_overlap(rows: list[dict], injections: list, cols: list[dict]) -> None:
    """Write range-join anchors (V1) into a table's rows. ``injections`` is a list
    of ``(col, value)`` — the anchor for each point/low/high column this table
    owns. Applied to the first row (and a second when available) to guarantee at
    least one row on each side of the range predicate satisfies it.

    Precedence: ``unique`` + range-boundary is range-precedence. Range-overlap
    correctness (a non-empty join) must win over the ``unique`` nicety. When a
    target column is flagged ``"unique": true`` the anchor is written into a
    single row only (never duplicated across rows 0 and 1), so the shared anchor
    cannot introduce a repeated value into a column a downstream reader trusts as
    distinct — one anchored row is already sufficient to satisfy the predicate."""
    if not rows or not injections:
        return
    col_names = {c["name"] for c in cols}
    unique_cols = {c["name"] for c in cols if c.get("unique")}
    default_targets = [0] if len(rows) < 2 else [0, 1]
    for col, val in injections:
        if col not in col_names:
            continue
        # range wins over unique: a unique boundary column takes the anchor in a
        # single row only, avoiding a duplicate the column promises not to have.
        targets = [0] if col in unique_cols else default_targets
        for ti in targets:
            rows[ti][col] = val


def generate_rows(schema: list[dict], n: int = 12, *, seed: int = 1337,
                  key_pools: dict[str, list] | None = None,
                  categoricals: dict[str, list] | None = None) -> list[dict]:
    """Generate rows for ``schema`` covering per-column edge cases.

    schema:      [{"name","type","nullable"(opt)}]
    key_pools:   {col: [values]} — referential keys shared across sources
                 (rows draw join keys from here so joins actually match).
    categoricals:{col: [allowed values]} — categorical/enum value sets.

    Coverage strategy (deterministic, no probabilistic null sprinkling):
      row 0                 : all typical, non-null (a clean baseline row)
      one row per nullable   : that column is NULL — every nullable non-key column
      one edge per scalar    : one type-boundary value per eligible column
      remaining              : typical random values until ``n`` (auto-raised to fit
                               full coverage when the schema is wider than ``n``)
    """
    rng = random.Random(seed)
    key_pools = key_pools or {}
    categoricals = categoricals or {}
    cols = [c for c in schema if c.get("name")]
    target = _coverage_row_target(cols, key_pools, categoricals, min_rows=n)
    rows: list[dict] = []

    # seeded Faker for realistic typical string values (optional dependency)
    fake = None
    if _Faker is not None:
        fake = _Faker()
        fake.seed_instance(seed)

    def typical(c):
        name, t = c["name"], c.get("type", "string")
        if name in key_pools and key_pools[name]:
            return _coerce_enum_value(rng.choice(key_pools[name]), t)
        if name in categoricals and categoricals[name]:
            return _coerce_enum_value(rng.choice(categoricals[name]), t)
        b = _base(t)
        if b in ("struct", "array", "map"):
            return _gen_nested(t, rng)
        if b in ("string", "varchar", "text", "char"):
            dv = _date_semantic_string(name, rng)
            if dv is not None:
                return dv
            if fake is not None:
                v = _faker_string(name, fake)
                if v is not None:
                    return v
        nv = _numeric_typical(name, b, rng)
        if nv is not None:
            return nv
        return rng.choice(_scalar_pool(b, rng)["typical"])

    def _append_row(**overrides):
        row = {cc["name"]: typical(cc) for cc in cols}
        row.update(overrides)
        rows.append(row)

    # baseline clean row (guaranteed all-populated, non-null)
    _append_row()

    # NULL row for every nullable non-key column.
    for c in cols:
        if c.get("nullable", True) and not _is_keylike(c["name"]):
            _append_row(**{c["name"]: None})

    # one type-boundary edge per eligible scalar column.
    for idx, c in enumerate(cols):
        if not _edge_eligible(c, key_pools, categoricals):
            continue
        not_null = not c.get("nullable", True)
        edges = _scalar_pool(_base(c.get("type", "string")), rng)["edge"]
        ev = edges[idx % len(edges)]
        if not_null and (ev is None or ev == ""):
            continue
        _append_row(**{c["name"]: ev})

    while len(rows) < target:
        _append_row()

    # Columns flagged ``"unique": true`` get DISTINCT values (primary-key-like).
    # No-op when no column carries the flag -> backward compatible.
    _apply_unique_columns(rows, cols, key_pools=key_pools)
    return rows


def _coerce_enum_value(v: Any, col_type: str) -> Any:
    """Coerce an enum/categorical value to match the column's declared type.

    Prevents int literals from .isin() mining being written as '1.0' by
    pandas when a nullable string column promotes ints to float.
    """
    if v is None:
        return None
    b = _base(col_type)
    if b in ("string", "varchar", "text", "char"):
        return str(v)
    if b in ("int", "integer", "long", "bigint", "short", "byte"):
        try:
            return int(v)
        except (ValueError, TypeError):
            return v
    if b in ("float", "double", "real", "decimal", "numeric"):
        try:
            return float(v)
        except (ValueError, TypeError):
            return v
    return v


def categoricals_from_columns(columns: list[dict]) -> dict[str, list]:
    """Pull any value sets the miner/LLM attached (col['values'])."""
    return {c["name"]: c["values"] for c in columns
            if c.get("values") and isinstance(c["values"], list)}


# ---------------------------------------------------------------------------
# Materialization (dispatch by format) + non-relational documents
# ---------------------------------------------------------------------------

def _rows_to_text_lines(rows: list[dict], schema: list[dict]) -> list[str]:
    """Serialize tabular rows as one line per row for ``spark.read.text`` mocks."""
    cols = [c["name"] for c in schema if c.get("name")]
    if len(cols) == 1:
        return ["" if r.get(cols[0]) is None else str(r.get(cols[0], "")) for r in rows]
    return ["\t".join("" if r.get(c) is None else str(r.get(c, "")) for c in cols)
            for r in rows]


def _make_writable(path) -> None:
    """Clear the read-only bit so datagen can (re)generate over an existing mock."""
    p = pathlib.Path(path)
    if p.exists():
        try:
            p.chmod(0o644)
        except OSError:
            pass


def _make_readonly(path) -> None:
    """Lock a generated mock file read-only so agents can't hand-edit it without
    going through the schema -> datagen path (§2: schema is the source of truth)."""
    p = pathlib.Path(path)
    if p.exists():
        try:
            p.chmod(0o444)
        except OSError:
            pass


def materialize(rows: list[dict], schema: list[dict], path, fmt: str,
                options: dict | None = None) -> None:
    """Write rows to ``path`` in the reader's format using the writers below."""
    p = pathlib.Path(path)
    _make_writable(p)
    f = (fmt or "parquet").lower()
    if f in ("csv", "tsv"):
        write_mock_csv(p, rows, options or {})
    elif f in ("json", "jsonl", "ndjson"):
        write_mock_json(p, rows, json_lines=True)
    elif f == "text":
        write_mock_text(p, _rows_to_text_lines(rows, schema))
    elif f == "avro":
        write_mock_avro(p, rows, schema)
    else:  # parquet / orc / delta / unknown -> typed parquet
        write_mock_parquet(p, rows, schema)
    _make_readonly(p)


def generate_document(nested_schema: Any, *, seed: int = 1337) -> Any:
    """Generate ONE non-tabular JSON document (config/manifest read via json.load).

    nested_schema may be:
      - a type string ("struct<a:int,b:array<string>>")
      - a dict {field: type_str | nested_dict}   (LLM-friendly shorthand)
      - a list -> array of the element schema
    """
    rng = random.Random(seed)

    def build(node):
        if isinstance(node, str):
            return _gen_nested(node, rng)
        if isinstance(node, dict):
            return {k: build(v) for k, v in node.items()}
        if isinstance(node, list):
            return [build(node[0])] if node else []
        return None

    return build(nested_schema)


def write_document(doc: Any, path, fmt: str = "json") -> None:
    """Write a generated non-relational document in its native format (yaml when
    requested and pyyaml is present, otherwise json)."""
    import pathlib
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _make_writable(p)
    if (fmt or "json").lower() in ("yaml", "yml"):
        try:
            import yaml
            p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            _make_readonly(p)
            return
        except Exception:
            pass
    import json
    p.write_text(json.dumps(doc, indent=2, default=str) + "\n", encoding="utf-8")
    _make_readonly(p)


# ---------------------------------------------------------------------------
# Workload-level seeding: seed once per shared table, duplicate to each consumer
# ---------------------------------------------------------------------------

def _canon(name: str) -> str:
    """Canonical table key (bare, lowercased) so db.schema.t and t group together."""
    return name.split(".")[-1].strip().lower()


# Identifier-like column tokens -> only these are treated as shared join keys
# (referential pooling). Metric/dimension columns that merely share a name across
# tables must NOT be pooled (that starved them of nulls + realistic variety).
_KEY_TOKENS = {"id", "key", "uid", "guid", "uuid", "code", "cd", "sk", "fk", "pk",
               "no", "num", "nbr", "seq"}
# data-synthesizer placeholder names for dynamic-path reads: entrypoint-local, never shared
_PLACEHOLDER_RE = re.compile(r"^src\d+$")


def _is_keylike(name: str) -> bool:
    return bool(_KEY_TOKENS & _tokenize(name))


def _category_bucket(category: str) -> str:
    """Seed grouping bucket: file reads and catalog loads are materialized
    differently (csv/json vs parquet staging) and must not share one mock."""
    return "file" if category == "file" else "table"


def _group_key(ep_id: str, sname: str, *, category: str = "table") -> str:
    """Grouping key for seed-once-duplicate. Real table names group across
    entrypoints within the same category bucket (``file`` vs ``table``); anonymous
    ``srcN`` placeholders are scoped to their entrypoint so unrelated reads never
    merge. ``links`` (file/csv) and ``LINKS`` (table) therefore seed separately."""
    k = _canon(sname)
    bucket = _category_bucket(category)
    if _PLACEHOLDER_RE.match(k):
        return "%s:%s:%s" % (ep_id, bucket, k)
    return "%s:%s" % (bucket, k)


def _ext_for(fmt: str) -> str:
    f = (fmt or "parquet").lower()
    if f in ("csv", "tsv"):
        return "csv"
    if f in ("json", "jsonl", "ndjson"):
        return "json"
    if f == "text":
        return "txt"
    if f == "avro":
        return "avro"
    return "parquet"


def _doc_ext(fmt: str) -> str:
    """File extension for a non-relational document, matching what
    ``write_document`` actually emits (yaml when requested, else json)."""
    return "yaml" if (fmt or "json").lower() in ("yaml", "yml") else "json"


def _materialize_fmt(*, source_format: str | None, categories: set[str]) -> str:
    """On-disk format for a relational mock.

    Table/connector sources are catalog-load staging (Phase A seed_entrypoint,
    Phase B COPY INTO) — always parquet regardless of the mined reader format.
    File sources are read via spark.read.<fmt>(path) and must match that format.
    """
    if "file" in categories:
        return source_format or "parquet"
    return "parquet"


def _merge_columns(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Union two column lists by name; prefer a specific (non-string) type, narrow
    nullability to False if any occurrence is NOT NULL, and narrow a ``values``
    enum to the intersection of the declared domains. One mock serves every
    consuming entrypoint, so it must satisfy the strictest constraint each of
    them declares. An empty intersection is an irreconcilable conflict: the
    first domain is kept so data still generates, and ``verify_mocks`` reports
    the out-of-domain values against the other consumer."""
    by_name = {c["name"]: dict(c) for c in existing}
    for c in incoming:
        if c["name"] not in by_name:
            by_name[c["name"]] = dict(c)
            continue
        cur = by_name[c["name"]]
        if _base(cur.get("type", "string")) == "string" and _base(c.get("type", "string")) != "string":
            cur["type"] = c["type"]
        cur["nullable"] = cur.get("nullable", True) and c.get("nullable", True)
        if not cur.get("values"):
            if c.get("values"):
                cur["values"] = c["values"]
        elif c.get("values"):
            shared = [v for v in cur["values"] if v in set(c["values"])]
            if shared:
                cur["values"] = shared
    return list(by_name.values())


def _consumer_mock_paths(out_dir, consumers: list, file_stem: str, ext: str) -> list:
    """Per-entrypoint paths for one seed group (``out_dir/<ep>/<stem>.<ext>``)."""
    import pathlib
    root = pathlib.Path(out_dir)
    return [root / ep_id / ("%s.%s" % (file_stem, ext)) for ep_id in consumers]


# Values per shared key pool. Kept small so a handful of rows per table densely
# cover the pool -> joins overlap reliably without bloating the mock.
_POOL_SIZE = 4


def _pool_base_type(cols: list[dict]) -> str:
    """Representative base type for a pool: the first non-string base among the
    member columns (string is the fallback)."""
    for c in cols:
        b = _base(c.get("type", "string"))
        if b != "string":
            return b
    return "string"


def _derive_pool_values(cols: list[dict], rng: random.Random, fake) -> list:
    """The shared value list for a pool, DERIVED from its member columns.

    When members declare ``values``, the pool is the INTERSECTION of those
    domains: every member reads the same mock values, so a value outside any
    member's declared domain would both violate that column's enum and defeat
    the join. Members without a declared domain accept anything and so do not
    constrain it. An empty intersection means the domains are irreconcilable —
    the union is used so data still generates and ``verify_mocks`` reports it.
    Otherwise: realistic Faker strings, else the type's typical scalars (both
    capped at ``_POOL_SIZE``).
    """
    union: list = []
    shared: set | None = None
    for c in cols:
        vs = c.get("values")
        if not isinstance(vs, list) or not vs:
            continue
        for v in vs:
            if v not in union:
                union.append(v)
        shared = set(vs) if shared is None else (shared & set(vs))
    if union:
        narrowed = [v for v in union if v in shared] if shared else []
        return narrowed or union
    base = _pool_base_type(cols)
    if base in ("struct", "array", "map"):
        return []
    name = cols[0].get("name", "") if cols else ""
    pool: list = []
    if base in ("string", "varchar", "text", "char") and fake is not None:
        for _ in range(24):
            v = _faker_string(name, fake)
            if v and v not in pool:
                pool.append(v)
            if len(pool) >= _POOL_SIZE:
                break
    if not pool:
        pool = list(dict.fromkeys(_scalar_pool(base, rng)["typical"]))[:_POOL_SIZE]
    return pool


def commit_mock_hashes(out_dir, pending_hashes: dict) -> None:
    """Write ``_hashes.json`` for each ep_id in *pending_hashes*.

    Call this only after the Snowflake upload succeeds to ensure the hash
    file never records a state that hasn't been persisted in Snowflake.
    *pending_hashes* is the ``"pending_hashes"`` value returned by
    ``seed_workload(..., defer_hash_write=True)``.
    """
    out_root = pathlib.Path(out_dir)
    for ep_id, hashes in pending_hashes.items():
        if not hashes:
            continue
        ep_dir = out_root / ep_id
        ep_dir.mkdir(parents=True, exist_ok=True)
        (ep_dir / "_hashes.json").write_text(
            json.dumps(hashes, indent=2) + "\n"
        )


_SQL_FILE_KEY_RE = re.compile(r"^sql_files\[.*\]$")


def tables_to_gate(problems: dict[str, list[str]], entrypoints: list) -> set[str]:
    """Tables that must not get mock data, given a problem map.

    A problem key is one of:
      - ``"<ep_id>"``            -> the entrypoint itself is invalid; gate every
                                    table it declares (none of them can be trusted)
      - ``"<ep_id>.<table>"``    -> gate that one table
      - ``"sql_files[<path>]"``  -> catalog-level; gates nothing on its own

    Raises ValueError on any other key: an unrecognised key means the producer
    built it wrong, and silently gating nothing is how that bug hides.
    """
    ep_tables = {ep["id"]: set((ep.get("tables") or {}).keys()) for ep in entrypoints}
    gated: set[str] = set()
    for key in problems:
        if key in ep_tables:
            gated.update(f"{key}.{tname}" for tname in ep_tables[key])
            continue
        if _SQL_FILE_KEY_RE.match(key):
            continue
        # A table name may itself contain dots (e.g. "db.out_tbl"), so resolve by
        # entrypoint prefix + membership rather than splitting on the last dot.
        for ep_id, tnames in ep_tables.items():
            prefix = ep_id + "."
            if key.startswith(prefix) and key[len(prefix):] in tnames:
                gated.add(key)
                break
        else:
            raise ValueError(
                f"problem key {key!r} matches no entrypoint or table — "
                f"verify_schema/verify_mocks must key problems by '<ep_id>', "
                f"'<ep_id>.<table>' or 'sql_files[<path>]'"
            )
    return gated


def sweep_mock_dir(out_dir, ep_ids: list, keep_paths: set[str]) -> list[str]:
    """Delete every mock file under ``out_dir/<ep_id>/`` that is not in *keep_paths*.

    This is what makes the mock directory reflect exactly the latest run: data for
    a table that is gated, failed its mock checks, or no longer exists cannot
    survive. Only the *ep_ids* of this run are swept, so a shared mock directory
    keeps other batches' data. Returns the deleted paths. ``_hashes.json`` is
    always kept — a deleted mock regenerates on the next run because the
    file-existence check in ``seed_workload`` fails regardless of the stored hash.
    """
    out_root = pathlib.Path(out_dir)
    deleted: list[str] = []
    for ep_id in ep_ids:
        ep_dir = out_root / ep_id
        if not ep_dir.is_dir():
            continue
        for p in sorted(ep_dir.iterdir()):
            if not p.is_file() or p.name == "_hashes.json":
                continue
            if str(p) not in keep_paths:
                p.unlink()
                deleted.append(str(p))
    return deleted


def seed_workload(entrypoints: list, out_dir, *, n: int = 12, seed: int = 1337,
                  force_all: bool = False, defer_hash_write: bool = False,
                  gated_tables: set[str] | None = None) -> dict:
    """Seed every entrypoint's readable tables for ISOLATED trials.

    Each entrypoint gets its own files under ``out_dir/<entrypoint_id>/``.
    Hash-driven: regenerates a table's mock IFF its schema_hash changed or the
    mock file is missing. Pass ``force_all=True`` (or delete the manifest) to
    regenerate everything unconditionally.

    Uses the unified ``tables`` dict with ``access`` field:
      - access includes "read" (read/readwrite) -> generate mock data
      - access == "write" -> not seeded (harness pre-creates empty)

    Routing is per (entrypoint, table) by ``relational``:
      - relational -> generate rows. A name read by several entrypoints is
        generated ONCE and the same dataset copied into each consumer (isolated
        files, identical content). Columns are unioned across consumers; columns
        shared across tables draw from one join-key pool.
      - non-relational -> generate the document from ``document_schema`` (once per
        name, copied to consumers). If document_schema is missing the contract is
        incomplete -> no document is generated (verify_schema reports it).

    ``defer_hash_write``: when True, skip writing ``_hashes.json`` and instead
    return the pending hash data under ``"pending_hashes"`` in the result dict.
    The caller should write the hashes only after the Snowflake upload succeeds,
    by calling ``commit_mock_hashes(out_dir, result["pending_hashes"])``.

    ``gated_tables``: set of ``"ep_id.table_name"`` keys that have a schema problem.
    No data is generated for them and their ``mock_file`` annotation is dropped, so
    they are absent from the returned ``table_paths``.

    Returns {"seeded": {...}, "skipped": {...}, "documents": {...},
             "pool_warnings": [...],
             "table_paths": {"ep_id.table": [path, ...]}}.
    """
    if gated_tables is None:
        gated_tables = set()
    import pathlib
    import zlib
    _schema_hash = schema_hash

    eps = entrypoints
    skipped: dict[str, dict] = {}

    # Load existing hash manifests for hash-driven skip logic
    _hash_manifests: dict[str, dict] = {}  # ep_id -> {table: hash}
    out_root = pathlib.Path(out_dir)
    for ep in eps:
        hfile = out_root / ep["id"] / "_hashes.json"
        if hfile.is_file():
            try:
                _hash_manifests[ep["id"]] = json.loads(hfile.read_text())
            except Exception:
                _hash_manifests[ep["id"]] = {}
        else:
            _hash_manifests[ep["id"]] = {}

    # route each (entrypoint, table) occurrence. Only tables with access
    # including "read" (read/readwrite) get mocked. write-only tables are
    # not seeded (harness pre-creates them empty).
    mock_reg: dict[str, dict] = {}      # gkey -> {display, file, columns, format, consumers, hashes}
    doc_reg: dict[str, dict] = {}       # gkey -> {display, file, format, schema, consumers}
    group_members: dict[str, list[tuple[str, str]]] = {}   # gkey -> [(ep_id, tname)]
    for ep in eps:
        ep_id = ep["id"]
        for tname, t in ep.get("tables", {}).items():
            if f"{ep_id}.{tname}" in gated_tables:
                continue        # gated: this run generates no data for it
            access = t.get("access", "read")
            if access == "write":
                continue  # write-only tables are not seeded
            gkey = _group_key(ep_id, tname, category=t.get("category", "table"))
            fkey = _canon(tname)
            group_members.setdefault(gkey, []).append((ep_id, tname))
            if t.get("relational") is False:
                schema = t.get("document_schema")
                if not schema:
                    continue    # reported by verify_schema; nothing to generate
                d = doc_reg.setdefault(gkey, {"display": tname, "file": fkey,
                                              "format": t.get("format"), "schema": schema,
                                              "consumers": []})
                d["consumers"].append(ep_id)
            else:  # relational — generate mock data (hash-driven)
                r = mock_reg.setdefault(gkey, {"display": tname, "file": fkey, "columns": [],
                                               "format": t.get("format"), "consumers": [],
                                               "categories": set(), "options": {},
                                               "table_entries": []})
                r["columns"] = _merge_columns(r["columns"], t.get("columns", []))
                r["format"] = r["format"] or t.get("format")
                r["categories"].add(t.get("category", "table"))
                if t.get("reader_options"):
                    r["options"] = {**r.get("options", {}), **t["reader_options"]}
                r["consumers"].append(ep_id)
                r["table_entries"].append((ep_id, tname, t))

    # ---- value pools: columns that must share values so joins/filters match ----
    # A pool groups (gkey, column) nodes whose mocks must overlap. EXPLICIT pools
    # come from each entrypoint's ``joins`` edge list (union-find over the edges,
    # so transitive chains and cross-named keys all collapse into one pool). A
    # type-safe NAME FALLBACK then pools same-(name, base_type) id-like/enum
    # columns that 2+ tables share but no edge connected. Keying the fallback on
    # (name, base_type) is what stops a string ``id`` and a numeric ``id`` in two
    # unrelated joins from colliding. Values are DERIVED from the member columns.
    import collections as _collections

    node_col: dict = {}                     # (gkey, col) -> column dict (first seen)
    for gkey, r in mock_reg.items():
        for c in r["columns"]:
            node_col.setdefault((gkey, c["name"]), c)

    ep_by_id = {ep["id"]: ep for ep in eps}

    def _edge_node(ep_id, ref):
        """Resolve a ``"table.col"`` join endpoint to a ``(gkey, col)`` node."""
        table, _, col = (ref or "").rpartition(".")
        if not table or not col:
            return None
        t = ep_by_id.get(ep_id, {}).get("tables", {}).get(table)
        if t is None:
            return None
        node = (_group_key(ep_id, table, category=t.get("category", "table")), col)
        return node if node in node_col else None

    _uf: dict = {}

    def _find(x):
        _uf.setdefault(x, x)
        root = x
        while _uf[root] != root:
            root = _uf[root]
        while _uf[x] != root:
            _uf[x], x = root, _uf[x]
        return root

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            _uf[ra] = rb

    for ep in eps:
        for edge in ep.get("joins", []) or []:
            a = _edge_node(ep["id"], edge.get("left", ""))
            b = _edge_node(ep["id"], edge.get("right", ""))
            if a and b:
                _union(a, b)

    krng = random.Random(seed)
    kfake = None
    if _Faker is not None:
        kfake = _Faker()
        kfake.seed_instance(seed)

    node_to_pool: dict = {}                 # (gkey, col) -> pool id
    pool_values: dict[str, list] = {}       # pool id -> [values]
    pool_warnings: list[str] = []

    # explicit pools: connected components of size >= 2
    comps: dict = _collections.defaultdict(list)
    for node in list(_uf):
        comps[_find(node)].append(node)
    for root, members in sorted(comps.items(),
                                key=lambda kv: sorted("%s::%s" % m for m in kv[1])):
        if len(members) < 2:
            continue
        pid = "j:" + "|".join(sorted("%s::%s" % m for m in members))
        cols = [node_col[m] for m in members]
        non_str_bases = {_base(c.get("type", "string")) for c in cols} - {"string"}
        if len(non_str_bases) > 1:
            pool_warnings.append(
                "join pool {%s} mixes column types %s; generated values may not match"
                % (", ".join(sorted("%s.%s" % m for m in members)), sorted(non_str_bases)))
        pool_values[pid] = _derive_pool_values(cols, krng, kfake)
        for m in members:
            node_to_pool[m] = pid

    # type-safe name fallback (only columns not already in an explicit pool)
    name_groups: dict = _collections.defaultdict(list)
    for node, c in node_col.items():
        if node in node_to_pool:
            continue
        name_groups[(node[1], _base(c.get("type", "string")))].append(node)
    for (name, _b), members in name_groups.items():
        # A declared domain must never bleed into a same-named sibling that
        # declares none (they may be unrelated columns). Pool enums only when
        # EVERY member constrains the domain, so the shared values satisfy all
        # of them; otherwise drop the constrained members from the group.
        constrained = {m for m in members if node_col[m].get("values")}
        if constrained and len(constrained) != len(members):
            members = [m for m in members if m not in constrained]
        if len({g for g, _ in members}) < 2:
            continue
        cols = [node_col[m] for m in members]
        keyish = (any(c.get("join_key") is True for c in cols)
                  or any(c.get("values") for c in cols)
                  or _is_keylike(name))
        if not keyish:
            continue
        pv = _derive_pool_values(cols, krng, kfake)
        if not pv:
            continue
        pid = "n:%s:%s" % (name, _b)
        pool_values[pid] = pv
        for m in members:
            node_to_pool[m] = pid

    # ---- V1: range-join overlap anchors ----
    # For each entrypoint range edge (A.low <= B.point <= A.high), assign a shared
    # anchor (low <= mid <= high) derived from the POINT column's type and inject
    # ``mid`` into the point table and ``(low, high)`` into the low/high table. The
    # two tables are generated independently, so without this the point date almost
    # never lands inside the partner interval -> empty sink (the Phase-A thrash V1
    # targets). ``range_injections``: gkey -> [(col, anchor_value), ...].
    range_injections: dict = _collections.defaultdict(list)
    for ep in eps:
        for edge in ep.get("range_join_edges", []) or []:
            pnode = _edge_node(ep["id"], edge.get("point", ""))
            lnode = _edge_node(ep["id"], edge.get("low", ""))
            hnode = _edge_node(ep["id"], edge.get("high", ""))
            if not (pnode and lnode and hnode):
                continue
            lo, mid, hi = _range_anchor(node_col[pnode].get("type", "string"))
            range_injections[pnode[0]].append((pnode[1], mid))
            range_injections[lnode[0]].append((lnode[1], lo))
            range_injections[hnode[0]].append((hnode[1], hi))

    # mock relational: generate ONCE per group, copy to each consuming entrypoint
    # Hash-driven: skip if all consumers have current hash and mock exists on disk
    seeded: dict[str, dict] = {}
    for gkey, r in mock_reg.items():
        cols = r["columns"]
        consumers = list(dict.fromkeys(r["consumers"]))
        if not cols:
            continue    # reported by verify_schema; nothing to generate
        cats = r.get("categories") or {"table"}
        fmt = _materialize_fmt(source_format=r.get("format"), categories=cats)
        ext = _ext_for(fmt)
        paths = [str(p) for p in _consumer_mock_paths(out_dir, consumers, r["file"], ext)]

        # per-table key pools: map each pooled column to its shared value list
        # (keyed by name, which is unique within a table, so generate_rows can
        # draw join keys from the right pool unchanged).
        tbl_pools: dict[str, list] = {}
        for c in cols:
            pid = node_to_pool.get((gkey, c["name"]))
            if pid and pool_values.get(pid):
                tbl_pools[c["name"]] = pool_values[pid]

        range_inject = range_injections.get(gkey) or []

        # Hash-driven skip: compute hash from first table_entry (all share merged columns)
        current_hash = None
        if r.get("table_entries"):
            _, _, first_te = r["table_entries"][0]
            # Build a synthetic entry with the merged columns for hashing.
            # ``pool_sig`` makes the hash depend on pool membership/values, so a
            # ``joins`` edit (which changes a column's pool) regenerates the mock.
            # unique/range signatures fold into ``pool_sig`` (the only free-form field
            # the hash reads) so toggling either also reseeds — absent -> unchanged sig
            # -> identical hash (backward compatible).
            hash_entry = dict(first_te)
            hash_entry["columns"] = cols
            pool_sig = {k: list(v) for k, v in sorted(tbl_pools.items())}
            unique_cols = sorted(c["name"] for c in cols if c.get("unique"))
            if unique_cols:
                pool_sig["__unique__"] = unique_cols
            if range_inject:
                pool_sig["__range__"] = sorted(
                    "%s=%s" % (c, v) for c, v in range_inject)
            hash_entry["pool_sig"] = pool_sig
            current_hash = _schema_hash(hash_entry)

        if not force_all and current_hash and paths:
            all_current = True
            for ep_id in consumers:
                ep_hashes = _hash_manifests.get(ep_id, {})
                stored = ep_hashes.get(r["file"])
                p = out_root / ep_id / ("%s.%s" % (r["file"], ext))
                if stored != current_hash or not p.is_file():
                    all_current = False
                    break
            if all_current:
                skipped[gkey] = {"display": r["display"], "format": ext,
                                 "consumers": consumers, "paths": paths}
                continue

        tseed = seed + (zlib.crc32(gkey.encode()) % 100000)
        rows = generate_rows(cols, n=n, seed=tseed, key_pools=tbl_pools,
                             categoricals=categoricals_from_columns(cols))
        _inject_range_overlap(rows, range_inject, cols)  # V1: satisfy range predicate
        for ep_id, p_str in zip(consumers, paths):
            materialize(rows, cols, pathlib.Path(p_str), fmt, r.get("options"))
            # Update hash manifest for this consumer
            _hash_manifests.setdefault(ep_id, {})[r["file"]] = current_hash
        seeded[gkey] = {"display": r["display"], "format": ext, "rows": len(rows),
                        "columns": [c["name"] for c in cols], "consumers": consumers,
                        "paths": paths}

    # non-relational documents: generate from document_schema, copy to consumers
    documents: dict[str, dict] = {}
    for gkey, d in doc_reg.items():
        consumers = list(dict.fromkeys(d["consumers"]))
        ext = _doc_ext(d["format"])
        paths = [str(p) for p in _consumer_mock_paths(out_dir, consumers, d["file"], ext)]
        if not force_all and paths and all(pathlib.Path(p).is_file() for p in paths):
            skipped[gkey] = {"display": d["display"], "format": d["format"],
                             "consumers": consumers, "paths": paths}
            continue
        dseed = seed + (zlib.crc32(gkey.encode()) % 100000)
        doc = generate_document(d["schema"], seed=dseed)
        for p_str in paths:
            write_document(doc, pathlib.Path(p_str), d["format"])
        documents[gkey] = {"display": d["display"], "format": d["format"],
                           "consumers": consumers, "paths": paths}

    # annotate each table with the mock_file the harness/provision should read
    # (relative to mock_data/<ep_id>/). Gated tables have no data this run, so any
    # stale annotation is dropped to keep the schema honest about what exists.
    for ep in eps:
        ep_id = ep["id"]
        for tname, t in ep.get("tables", {}).items():
            access = t.get("access", "read")
            if access == "write":
                continue
            if f"{ep_id}.{tname}" in gated_tables:
                t.pop("mock_file", None)
                continue
            if t.get("relational") is False:
                t["mock_file"] = "%s.%s" % (_canon(tname), _doc_ext(t.get("format")))
            else:
                fmt = _materialize_fmt(
                    source_format=t.get("format"),
                    categories={t.get("category", "table")},
                )
                t["mock_file"] = "%s.%s" % (_canon(tname), _ext_for(fmt))

    # Persist hash manifests to disk — skipped when defer_hash_write=True so
    # the caller can write them only after the Snowflake upload succeeds.
    if not defer_hash_write:
        commit_mock_hashes(str(out_root), _hash_manifests)

    # "<ep_id>.<table>" -> the mock file(s) this run generated or kept for it.
    # The caller uses this to verify the mocks and to sweep everything else away.
    table_paths: dict[str, list[str]] = {}
    for group in (seeded, skipped, documents):
        for gkey, meta in group.items():
            by_ep = dict(zip(meta["consumers"], meta["paths"]))
            for ep_id, tname in group_members.get(gkey, []):
                path = by_ep.get(ep_id)
                if path:
                    table_paths.setdefault(f"{ep_id}.{tname}", []).append(path)

    result: dict = {"seeded": seeded, "skipped": skipped, "documents": documents,
                    "pool_warnings": pool_warnings, "table_paths": table_paths}
    if defer_hash_write:
        result["pending_hashes"] = _hash_manifests
    return result


def _llm_todo_items(entrypoints: list) -> list[tuple[str, str, str]]:
    """Unresolved table-level ``llm_todo`` entries as ``(ep_id, table, reason)``."""
    todos: list[tuple[str, str, str]] = []
    for ep in entrypoints:
        for nm, it in ep.get("tables", {}).items():
            if isinstance(it, dict) and it.get("llm_todo"):
                todos.append((ep.get("id", "?"), nm, it["llm_todo"]))
    return todos


def _remaining_llm_todos(entrypoints: list) -> list[str]:
    """Every unresolved ``llm_todo`` path across entrypoints.

    Completeness is DERIVED from this (no todos == complete) rather than trusting a
    hand-edited ``complete`` bool that nothing recomputes.
    """
    todos: list[str] = []
    for ep_id, tname, _reason in _llm_todo_items(entrypoints):
        todos.append("%s.tables.%s" % (ep_id, tname))
    return todos


def _read_column_values(path: pathlib.Path, col_name: str) -> list:
    """Load one column's values from a generated mock file."""
    ext = path.suffix.lower()
    if ext == ".parquet":
        import pyarrow.parquet as pq
        t = pq.read_table(path)
        if col_name not in t.column_names:
            return []
        return t.column(col_name).to_pylist()
    if ext in (".json", ".jsonl", ".ndjson"):
        vals = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            vals.append(obj.get(col_name))
        return vals
    if ext in (".csv", ".tsv"):
        import csv as _csv
        vals = []
        delim = "\t" if ext == ".tsv" else ","
        with path.open(encoding="utf-8", newline="") as fh:
            for row in _csv.DictReader(fh, delimiter=delim):
                vals.append(row.get(col_name))
        return vals
    return []


def _join_columns_for_ep(ep: dict) -> dict[str, set[str]]:
    """Map column name -> table names that must share join-key values."""
    out: dict[str, set[str]] = {}
    for tname, t in ep.get("tables", {}).items():
        if t.get("relational") is False:
            continue
        access = t.get("access", "read")
        if access == "write":
            continue
        for c in t.get("columns", []):
            nm = c.get("name")
            if not nm:
                continue
            if c.get("join_key") is True or c.get("values") or _is_keylike(nm):
                out.setdefault(nm, set()).add(tname)
    # same-named columns linked by an explicit `joins` edge must also overlap,
    # even when the name is not id-like and carries no enum.
    for e in ep.get("joins", []) or []:
        lt, _, lc = (e.get("left", "") or "").rpartition(".")
        rt, _, rc = (e.get("right", "") or "").rpartition(".")
        if lc and lc == rc and lt and rt:
            out.setdefault(lc, set()).update({lt, rt})
    return {nm: srcs for nm, srcs in out.items() if len(srcs) >= 2}


def _snowflake_ddl_key(col_name: str) -> str:
    """Snowflake CREATE TABLE identity for duplicate detection (harness provisioning)."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", col_name):
        return col_name.upper()
    return col_name


def _snowflake_column_duplicates(columns: list[dict]) -> list[str]:
    """Column names that collide after Snowflake DDL quoting rules."""
    seen: dict[str, str] = {}
    dups: list[str] = []
    for col in columns:
        name = col.get("name", "")
        if not name:
            continue
        key = _snowflake_ddl_key(name)
        if key in seen:
            label = "%s/%s" % (seen[key], name) if seen[key] != name else name
            if label not in dups:
                dups.append(label)
        else:
            seen[key] = name
    return dups


_ENTRYPOINT_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "schemas", "entrypoint.schema.json"
)
_ENTRYPOINT_SCHEMA: dict | None = None


def _entrypoint_json_schema() -> dict:
    global _ENTRYPOINT_SCHEMA
    if _ENTRYPOINT_SCHEMA is None:
        with open(_ENTRYPOINT_SCHEMA_PATH, encoding="utf-8") as fh:
            _ENTRYPOINT_SCHEMA = json.load(fh)
    return _ENTRYPOINT_SCHEMA


def _format_schema_error(ep_id: str, error) -> str:
    loc = ".".join(str(p) for p in error.absolute_path)
    msg = error.message
    # Prescriptive tail for the most common missing-required-property errors so
    # the data-synthesizer doesn't have to reason about how to fix them.
    tail = ""
    if "is a required property" in msg:
        if "'entrypoint_kwargs'" in msg:
            tail = " -- fix: add `\"entrypoint_kwargs\": {}` to _meta.json (or `{\"name\": default_value, ...}` if the entrypoint reads runtime kwargs like a date partition)."
        elif "'source_runtime'" in msg:
            tail = " -- fix: set `\"source_runtime\": \"spark\"` (or `\"databricks\"` for Databricks-native)."
        elif "'run_mode'" in msg:
            tail = " -- fix: set `\"run_mode\": \"script\"` (or `\"callable\"` for function-entrypoints)."
        elif "'import_roots'" in msg:
            tail = " -- fix: set `\"import_roots\": [\".\"]`."
    if loc:
        return "%s.%s: %s%s" % (ep_id, loc, msg, tail)
    return "%s: %s%s" % (ep_id, msg, tail)


def _verify_entrypoint_schema(entrypoints: list) -> list[tuple[str, str]]:
    """Validate each entrypoint against ``schemas/entrypoint.schema.json``.

    Returns ``(key, message)`` pairs keyed to the offending table when the error
    localises inside ``tables.<name>``, else to the entrypoint.
    """
    import jsonschema

    validator = jsonschema.Draft202012Validator(_entrypoint_json_schema())
    problems: list[tuple[str, str]] = []
    for ep in entrypoints:
        ep_id = ep.get("id") or "?"
        tables = ep.get("tables") or {}
        for err in sorted(validator.iter_errors(ep), key=lambda e: list(e.absolute_path)):
            path = list(err.absolute_path)
            key = ep_id
            if len(path) >= 2 and path[0] == "tables" and path[1] in tables:
                key = "%s.%s" % (ep_id, path[1])
            problems.append((key, _format_schema_error(ep_id, err)))
    return problems


def _verify_table_columns(ep_id: str, ep: dict) -> list[tuple[str, str]]:
    """Verify tables have non-empty columns where required."""
    problems: list[tuple[str, str]] = []
    for nm, t in (ep.get("tables") or {}).items():
        if t.get("relational") is False:
            continue
        cols = t.get("columns") or []
        if not cols:
            access = t.get("access", "read")
            category = t.get("category", "table")
            if category in ("table", "connector") or access in ("read", "readwrite"):
                problems.append((
                    "%s.%s" % (ep_id, nm),
                    "%s.tables.%s: table has columns: [] "
                    "-- fix: add the columns actually referenced by the source's "
                    ".select()/.selectExpr()/.withColumn()/.filter() calls; "
                    "use `[{\"name\": \"<col>\", \"type\": \"<spark type>\"}]`. "
                    "Do NOT trace transitive joins/renames; unknown columns can be "
                    "left off and will be inferred at Phase B runtime."
                    % (ep_id, nm)
                ))
    return problems


def _provision_copy_source(source: dict) -> bool:
    """True when the harness COPY INTOs this mock into a Snowflake table."""
    if not source.get("relational", True):
        return False
    return source.get("category", "table") in ("table", "connector")


def _reader_header_false(options: dict) -> bool:
    hdr = options.get("header", True)
    if hdr is False:
        return True
    return str(hdr).strip().lower() in ("false", "0", "no")


def _is_table_like_source(source: dict) -> bool:
    """True when the harness would CREATE TABLE for this relational source."""
    if not source.get("relational", True):
        return False
    if source.get("category", "table") in ("table", "connector"):
        return True
    return source.get("format") == "table" or source.get("reader_method") == "table"


_CTE_TABLE_RE = re.compile(r"^cte[_\d]*$", re.I)


def _bare_table_token(raw: str) -> str:
    """Last segment of a table/path token, lowercased (for SQL/source matching)."""
    if not raw:
        return ""
    if "://" in raw or raw.startswith("/"):
        return ""
    parts = [p for p in raw.replace("`", "").split(".") if p]
    return parts[-1].lower() if parts else raw.lower()


def _skip_sql_catalog_table(tname: str) -> bool:
    b = _bare_table_token(tname).upper()
    return bool(_CTE_TABLE_RE.match(b)) or b == "_PH_"


def _find_declared_table(group: dict, tname: str) -> tuple[str | None, dict | None]:
    tb = _bare_table_token(tname).upper()
    for nm, it in group.items():
        if _bare_table_token(nm).upper() == tb:
            return nm, it
        op = it.get("original_path") or it.get("original_target") or ""
        if op and _bare_table_token(op).upper() == tb:
            return nm, it
    return None, None


def _verify_sql_files_catalog(sql_files: list, entrypoints: list) -> list[tuple[str, str]]:
    """Each ``sql_files`` row must be merged (no ``llm_todo``) and every physical
    table/column in the catalog must appear on some entrypoint.

    Keys a problem to the declared table when the catalog row resolves to one,
    else to ``sql_files[<path>]`` (nothing to gate — no such table exists yet)."""
    problems: list[tuple[str, str]] = []
    for sf in sql_files or []:
        path = sf.get("path", "?")
        cat_key = "sql_files[%s]" % path
        if sf.get("llm_todo"):
            problems.append((cat_key, "sql_files[%s]: unresolved llm_todo — merge tables/columns onto entrypoints, then delete the todo" % path))
        for _tkey, tinfo in (sf.get("tables") or {}).items():
            tname = tinfo.get("name") or _tkey
            if _skip_sql_catalog_table(tname):
                continue
            found = None; found_loc = ""; found_key = cat_key
            for ep in entrypoints:
                nm, rec = _find_declared_table(ep.get("tables") or {}, tname)
                if rec is not None:
                    found = rec
                    found_loc = "%s.tables.%s" % (ep.get("id", "?"), nm)
                    found_key = "%s.%s" % (ep.get("id", "?"), nm); break
            if found is None:
                problems.append((cat_key, "sql_files[%s]: table %s not declared on any entrypoint" % (path, tname))); continue
            roles = set(tinfo.get("roles") or [])
            if "read" in roles and found.get("access", "read") not in ("read", "readwrite"):
                problems.append((found_key, "sql_files[%s]: table %s is read in SQL but declared with access=%r — add as read or readwrite with full columns" % (path, tname, found.get("access"))))
            declared = {c["name"].upper() for c in found.get("columns") or [] if c.get("name")}
            for col in tinfo.get("columns") or []:
                if col.upper() not in declared:
                    problems.append((found_key, "sql_files[%s]: table %s missing column %r (declared at %s)" % (path, tname, col, found_loc)))
    return problems


def _verify_missing_document_schemas(entrypoints: list) -> list[tuple[str, str]]:
    """Report non-relational read tables that still need document_schema."""
    problems: list[tuple[str, str]] = []
    for ep in entrypoints:
        ep_id = ep.get("id") or "?"
        for tname, t in (ep.get("tables") or {}).items():
            if t.get("access", "read") == "write":
                continue
            if t.get("relational") is False and not t.get("document_schema"):
                problems.append((
                    "%s.%s" % (ep_id, tname),
                    "%s.tables.%s: non-relational table missing document_schema "
                    "-- fix: fill document_schema or delete the false-positive entry"
                    % (ep_id, tname)
                ))
    return problems


def verify_schema(entrypoints: list, sql_files=None) -> dict[str, list[str]]:
    """Validate the declared schemas, before any data is generated.

    Returns ``{key: [problems]}``; an empty dict means every schema is valid.
    Keys are ``"<ep_id>"``, ``"<ep_id>.<table>"`` or ``"sql_files[<path>]"`` —
    see :func:`tables_to_gate`. A table with a problem here gets no mock data.
    """
    problems: dict[str, list[str]] = {}

    def add(key: str, msg: str) -> None:
        problems.setdefault(key, []).append(msg)

    for ep_id, tname, reason in _llm_todo_items(entrypoints):
        add(f"{ep_id}.{tname}", f"{ep_id}.tables.{tname}: unresolved llm_todo — {reason}")

    for key, msg in _verify_entrypoint_schema(entrypoints):
        add(key, msg)

    for key, msg in _verify_missing_document_schemas(entrypoints):
        add(key, msg)

    for ep in entrypoints:
        if ep.get("source_runtime") is None:
            add(ep["id"],
                f"{ep['id']}: source_runtime is null — set to 'databricks' or 'spark' (Step 3).")

    if sql_files:
        for key, msg in _verify_sql_files_catalog(sql_files, entrypoints):
            add(key, msg)

    for ep in entrypoints:
        ep_id = ep["id"]
        for key, msg in _verify_table_columns(ep_id, ep):
            add(key, msg)

        for tname, t in ep.get("tables", {}).items():
            table_key = f"{ep_id}.{tname}"
            access = t.get("access", "read")
            cols = t.get("columns") or []
            if cols:
                dups = _snowflake_column_duplicates(cols)
                if dups:
                    add(table_key,
                        f"{ep_id}.tables.{tname}: duplicate Snowflake column name(s) "
                        f"{', '.join(dups)} (unquoted identifiers fold to uppercase)")

            # §5: a .sql template is read from the source file, never mocked
            mf = t.get("mock_file", "")
            if mf and (mf.endswith(".sql") or t.get("format") == "sql"):
                add(table_key,
                    f"{ep_id}.tables.{tname}: SQL template seeded as mock_file ({mf}) — "
                    "SQL must be read from the sibling source file, not mocked")

            # Write file-sinks must declare their write format. The runtime reads a
            # sink back with spark.read.format(t.get("format","parquet")); a missing
            # format silently defaults to parquet and captures zero rows when the
            # workload writes another format, surfacing only as "No outputs produced".
            if access == "write" and t.get("category") == "file" and not t.get("format"):
                add(table_key,
                    f"{ep_id}.tables.{tname}: write file-sink missing 'format' — declare the "
                    "format the workload writes (text/json/csv/parquet/avro) so the "
                    "harness reads the sink back correctly")

    return problems


def verify_mocks(entrypoints: list, out_dir,
                 generated: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Validate the mock files this run generated against their declared schemas.

    *generated* holds the ``"<ep_id>.<table>"`` keys that have data on disk
    (``seed_workload``'s ``table_paths``); every other table is skipped because it
    is already reported by :func:`verify_schema`.

    Returns ``({"<ep_id>.<table>": [problems]}, [join-overlap warnings])``. A table
    in the problem map holds a mock that contradicts its own declared schema and
    must lose it. An empty join overlap is NOT such a contradiction: each mock is
    valid on its own and the defect is in the relationship between them, so it is
    a warning the data-synthesizer must resolve or dismiss rather than a reason to
    starve every table that happens to share the column.
    """
    problems: dict[str, list[str]] = {}
    overlap_warnings: list[str] = []

    def add(key: str, msg: str) -> None:
        problems.setdefault(key, []).append(msg)

    _TMAP = _SPARK_SEED_PARQUET_TYPES
    try:
        import pyarrow.parquet as pq
    except ImportError:
        pq = None  # type: ignore[assignment]

    for ep in entrypoints:
        ep_id = ep["id"]
        join_cols = _join_columns_for_ep(ep)
        join_value_sets: dict[str, dict[str, set]] = {nm: {} for nm in join_cols}
        tbl_files: dict[str, "pathlib.Path"] = {}

        for tname, t in ep.get("tables", {}).items():
            table_key = f"{ep_id}.{tname}"
            if table_key not in generated:
                continue
            mf = t.get("mock_file", "")
            cols = t.get("columns") or []
            if t.get("relational") is False:
                p = pathlib.Path(out_dir) / ep_id / mf
                if not p.is_file():
                    add(table_key,
                        f"{ep_id}/{tname}: non-relational mock_file {mf!r} not found")
                continue
            p = pathlib.Path(out_dir) / ep_id / mf
            if not p.is_file():
                alts = sorted(
                    q.name for q in (pathlib.Path(out_dir) / ep_id).glob(
                        pathlib.Path(mf).stem + ".*"
                    ) if q.is_file()
                )
                hint = ("; on disk: %s" % ", ".join(alts)) if alts else ""
                add(table_key, f"{ep_id}/{tname}: mock_file {mf!r} not found{hint}")
                continue
            tbl_files[tname] = p

            if mf.endswith((".csv", ".tsv")):
                opts = t.get("reader_options") or {}
                delim = opts.get("sep") or opts.get("delimiter") or ("\t" if mf.endswith(".tsv") else ",")
                declared = [c["name"] for c in t.get("columns", []) if c.get("name")]
                ncols = len(declared)
                if ncols > 1:
                    with p.open(encoding="utf-8") as fh:
                        first = fh.readline().rstrip("\n")
                    fields = first.split(delim)
                    got = len(fields)
                    if got != ncols:
                        add(
                            table_key,
                            f"{ep_id}/{tname}: CSV row 1 has {got} field(s) split on {delim!r} but schema "
                            f"declares {ncols} columns -- delimiter mismatch?"
                        )
                    elif not _reader_header_false(opts):
                        header_keys = {_snowflake_ddl_key(h.strip()) for h in fields}
                        decl_keys = {_snowflake_ddl_key(c) for c in declared}
                        if header_keys != decl_keys:
                            add(
                                table_key,
                                f"{ep_id}/{tname}: CSV header columns {sorted(header_keys)} do not match declared schema {sorted(decl_keys)}"
                            )
                if _provision_copy_source(t) and _reader_header_false(opts):
                    add(
                        table_key,
                        f"{ep_id}/{tname}: reader_options.header is false but provision COPY INTO "
                        "uses SKIP_HEADER=1 for table sources"
                    )
                continue

            if mf.endswith((".json", ".jsonl", ".ndjson")):
                declared_cols = {c["name"] for c in t.get("columns", []) if c.get("name")}
                try:
                    first = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
                except (IndexError, json.JSONDecodeError):
                    add(table_key, f"{ep_id}/{tname}: JSON mock is empty or invalid")
                    continue
                missing = declared_cols - set(first.keys())
                if missing:
                    add(
                        table_key,
                        f"{ep_id}/{tname}: JSON mock missing declared column(s) {', '.join(sorted(missing))}"
                    )
                continue

            if not mf.endswith(".parquet") or pq is None:
                continue

            pq_table = pq.read_table(p)
            declared_map = {c["name"]: c for c in t.get("columns", [])}
            for fld in pq_table.schema:
                dc = declared_map.get(fld.name)
                if not dc:
                    continue
                b = _canon_base(dc.get("type", "string"))
                if b in ("struct", "array", "map"):
                    continue
                ft = str(fld.type)
                if b == "decimal":
                    ok_type = ft.startswith("decimal")
                else:
                    ok_type = ft in _TMAP.get(b, {ft})
                if not ok_type:
                    add(
                        table_key,
                        f"{ep_id}/{tname}.{fld.name}: declared {dc['type']} but parquet {fld.type}"
                    )

            pdf = pq_table.to_pandas()
            parquet_keys = {_snowflake_ddl_key(c) for c in pdf.columns}
            missing_cols = sorted(
                c["name"] for c in t.get("columns", [])
                if c.get("name") and _snowflake_ddl_key(c["name"]) not in parquet_keys
            )
            if missing_cols:
                add(
                    table_key,
                    f"{ep_id}/{tname}: parquet mock missing declared column(s): {', '.join(missing_cols)}"
                )
            for cname, dc in declared_map.items():
                if cname not in pdf.columns:
                    continue
                series = pdf[cname]
                nulls = int(series.isna().sum())
                if dc.get("nullable", True) and not _is_keylike(cname) and dc.get("join_key") is not True:
                    if nulls < 1:
                        add(
                            table_key,
                            f"{ep_id}/{tname}.{cname}: nullable column has no null values in mock"
                        )
                elif not dc.get("nullable", True) and nulls > 0:
                    add(
                        table_key,
                        f"{ep_id}/{tname}.{cname}: NOT NULL column has {nulls} null value(s) in mock"
                    )
                domain = dc.get("values")
                if isinstance(domain, list) and domain:
                    # Compare against the domain as the generator writes it: a
                    # string column with int enum literals holds "1", not 1.
                    allowed = {_coerce_enum_value(v, dc.get("type", "string")) for v in domain}
                    seen = {v for v in series.dropna().unique() if v is not None}
                    bad = seen - allowed
                    if bad:
                        add(
                            table_key,
                            f"{ep_id}/{tname}.{cname}: enum values {sorted(bad)} outside declared domain {domain}"
                        )

            for jcol in join_cols:
                if jcol in declared_map:
                    vals = {v for v in _read_column_values(p, jcol) if v is not None and v != ""}
                    join_value_sets[jcol][tname] = vals

        for jcol, per_src in join_value_sets.items():
            if len(per_src) < 2:
                continue
            # Only judge the overlap when every table sharing this join column got
            # data: a gated participant breaks the value pool for the rest.
            if any(f"{ep_id}.{t}" not in generated for t in join_cols.get(jcol, ())):
                continue
            sets = list(per_src.values())
            if not sets[0].intersection(*sets[1:]):
                srcs = ", ".join(sorted(per_src))
                overlap_warnings.append(
                    f"{ep_id}: join overlap empty for column '{jcol}' across tables ({srcs})"
                    " -- add a `joins` edge or a shared `values` domain, or set"
                    " \"join_key\": false on the column to dismiss it"
                )

        for e in ep.get("joins", []) or []:
            lt, _, lc = (e.get("left", "") or "").rpartition(".")
            rt, _, rc = (e.get("right", "") or "").rpartition(".")
            if not (lc and rc) or lc == rc:
                continue
            if lt not in tbl_files or rt not in tbl_files:
                continue
            lv = {v for v in _read_column_values(tbl_files[lt], lc) if v is not None and v != ""}
            rv = {v for v in _read_column_values(tbl_files[rt], rc) if v is not None and v != ""}
            if lv and rv and not (lv & rv):
                overlap_warnings.append(
                    f"{ep_id}: cross-named join overlap empty: {lt}.{lc} vs {rt}.{rc}"
                )

    return problems, overlap_warnings


def _scan_needs_llm(entrypoints: list) -> dict:
    """Pure scan for tables whose schema the LLM still has to supply.

    Drives ``manifest["complete"]``; ``verify_schema`` reports the same gaps as
    problems, so this is only the completeness view.
    """
    nl: dict[str, str] = {}
    for ep in entrypoints:
        for tname, t in ep.get("tables", {}).items():
            access = t.get("access", "read")
            if access == "write":
                continue
            if t.get("relational") is False and not t.get("document_schema"):
                nl[tname] = "non-relational table: fill document_schema"
            elif _is_table_like_source(t) and not t.get("columns"):
                nl[tname] = "table: declare columns"
            elif t.get("relational", True) and not t.get("columns"):
                nl[tname] = "no columns mined; supply the schema"
    return nl


def verify_warnings(entrypoints: list, *, manifest: dict | None = None) -> list[str]:
    """Non-failing warnings for the silent empty-join trap: a column that appears
    in >=2 tables but is neither identifier-like, enum-constrained, nor linked by a
    ``joins`` edge will NOT be pooled by datagen, so joins on it produce no matches
    even though datagen is ok. Surface it so the data-synthesizer's review pass can add a
    ``joins`` edge (or a shared ``values`` domain), or confirm it is not a join key.

    To DISMISS a warning the reviewer has confirmed is not a join key, set
    ``"join_key": false`` on the column; it is then suppressed on
    every subsequent datagen run so genuinely-new warnings stand out."""
    warns: list[str] = []
    for ep in entrypoints:
        seen: dict[str, int] = {}
        enum: set[str] = set()
        confirmed_not_key: set[str] = set()
        forced_key: set[str] = set()
        # columns named by an explicit `joins` edge are already pooled -> not a trap
        joined: set[str] = set()
        for e in ep.get("joins", []) or []:
            for side in ("left", "right"):
                _, _, col = (e.get(side, "") or "").rpartition(".")
                if col:
                    joined.add(col)
        for t in ep.get("tables", {}).values():
            if not t.get("relational", True):
                continue
            if t.get("access", "read") == "write":
                continue
            for c in t.get("columns", []):
                nm = c.get("name")
                if not nm:
                    continue
                seen[nm] = seen.get(nm, 0) + 1
                if c.get("values"):
                    enum.add(nm)
                if c.get("join_key") is False:
                    confirmed_not_key.add(nm)
                if c.get("join_key") is True:
                    forced_key.add(nm)
        for nm, cnt in sorted(seen.items()):
            if cnt >= 2 and nm not in enum and nm not in confirmed_not_key \
                    and nm not in forced_key and nm not in joined and not _is_keylike(nm):
                warns.append(
                    "[%s] column '%s' is in %d sources but is not id-like, has no "
                    "enum 'values', and no 'joins' edge -> datagen will NOT pool it; "
                    "if it is a join key, joins will be empty. Add a 'joins' edge (or a "
                    "shared 'values' domain), or set \"join_key\": false to confirm it "
                    "is not a key."
                    % (ep.get("id", "?"), nm, cnt))
    return warns


# ===========================================================================
# File-format writers: given rows + a declared schema,
# write a mock file in the reader's format. Nested types (struct / array<struct>
# / map) are written as REAL pyarrow nested columns so PySpark reads them
# natively in Phase A; the Snowflake provisioner JSON-stringifies for Phase B.
# ===========================================================================

_COERCE_MAP: dict[str, type | None] = {
    "string": str, "varchar": str, "text": str, "char": str,
    "int": int, "integer": int, "long": int, "bigint": int, "short": int, "byte": int,
    "float": float, "double": float, "real": float, "decimal": float, "numeric": float,
    "boolean": bool, "bool": bool,
    "date": str, "timestamp": str, "timestamp_ntz": str, "timestamp_ltz": str,
    "timestamp_tz": str, "binary": bytes,
}

_PARQUET_TYPE_MAP = {
    "string": "str", "varchar": "str", "text": "str", "char": "str",
    "int": "Int32", "integer": "Int32", "long": "Int64", "bigint": "Int64",
    "short": "Int16", "byte": "Int8",
    "float": "float32", "double": "float64", "real": "float32",
    "decimal": "float64", "numeric": "float64",
    "boolean": "bool", "bool": "bool",
    "date": "date32", "timestamp": "str", "timestamp_ntz": "str", "timestamp_ltz": "str",
    "timestamp_tz": "str", "binary": "object",
}

# Parquet on-disk types that Phase A seeding (IntegerType/DateType via
# reader.schema(...).parquet) and Snowflake COPY INTO both accept. Keyed by the
# CANONICAL base (verify() looks up via _canon_base, so aliases like integer/
# bigint/smallint/tinyint/real/numeric are normalized before lookup).
_SPARK_SEED_PARQUET_TYPES: dict[str, set[str]] = {
    "string": {"large_string", "string"},
    "varchar": {"large_string", "string"},
    "text": {"large_string", "string"},
    "char": {"large_string", "string"},
    "int": {"int32"},
    "long": {"int64"},
    "short": {"int16"},
    "byte": {"int8"},
    "float": {"float"},
    "double": {"double"},
    # decimal is validated by prefix (decimal128(p,s)) in verify(), not this set.
    "decimal": {"double"},
    "boolean": {"bool"},
    "date": {"date32[day]"},
    "timestamp": {"large_string", "string", "timestamp[us]", "timestamp[ns]"},
    "timestamp_ntz": {"large_string", "string", "timestamp[us]", "timestamp[ns]"},
    "timestamp_ltz": {"large_string", "string", "timestamp[us]", "timestamp[ns]"},
    "binary": {"binary"},
}

_AVRO_TYPE_MAP = {
    "string": "string", "varchar": "string", "text": "string", "char": "string",
    "int": "int", "integer": "int", "long": "long", "bigint": "long",
    "short": "int", "byte": "int", "float": "float", "double": "double",
    "real": "float", "decimal": "double", "numeric": "double",
    "boolean": "boolean", "bool": "boolean",
    "date": "string", "timestamp": "string", "timestamp_ntz": "string",
    "timestamp_ltz": "string", "timestamp_tz": "string", "binary": "bytes",
}


def _is_complex_type(declared_type: str) -> bool:
    """True for struct/object-bearing types written as REAL pyarrow nested types
    (so PySpark reads them natively). array<...> and map<...> are also complex
    so they get a proper pa.list_() / pa.map_() column in the parquet writer."""
    tl = declared_type.strip().lower()
    if tl.startswith(("struct", "object", "array", "map")):
        return True
    return "struct" in tl or "object" in tl


def _coerce_value(value: Any, declared_type: str) -> Any:
    """Coerce *value* to the Python type implied by *declared_type* (NULL-ish
    passes through). Raises ValueError on failure."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if _is_complex_type(declared_type):
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return value
        return value
    typ = declared_type.lower().split("(")[0].strip()
    target = _COERCE_MAP.get(typ, str)
    if target is None:
        return value
    if target is bool:
        if isinstance(value, str):
            if value.lower() in ("true", "1", "yes"):
                return True
            if value.lower() in ("false", "0", "no"):
                return False
            raise ValueError(f"Cannot coerce {value!r} to boolean")
        return bool(value)
    if target is bytes:
        return value if isinstance(value, bytes) else str(value).encode()
    return target(value)


def write_mock_csv(path: pathlib.Path, rows: list[dict], options: dict | None = None) -> None:
    """Write rows as a CSV. Delimiter from options['sep'|'delimiter'] (default ',')
    and the header row is omitted when options['header'] is false-y -- both MUST
    match the workload's reader_options or seeding breaks (wrong delimiter -> one
    column; a header row read as data when header=false -> a junk/typed row)."""
    if not rows:
        raise ValueError("write_mock_csv requires at least one row")
    options = options or {}
    delimiter = options.get("sep") or options.get("delimiter") or ","
    hdr = options.get("header", True)
    write_header = str(hdr).strip().lower() not in ("false", "0", "no", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})


def write_mock_json(path: pathlib.Path, rows: list[dict], json_lines: bool = True) -> None:
    """Write rows as JSON Lines (default) or a JSON array."""
    if not rows:
        raise ValueError("write_mock_json requires at least one row")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        if json_lines:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
        else:
            json.dump(rows, f, indent=2, default=str)
            f.write("\n")


def write_mock_text(path: pathlib.Path, lines: list[str]) -> None:
    """Write one value per line (single-column text format)."""
    if not lines:
        raise ValueError("write_mock_text requires at least one line")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(str(line) + "\n")


def _parse_spark_type_to_pyarrow(declared_type: str):
    """Parse a Spark-style type string (struct<>/array<>/map<>/scalar) into a
    pyarrow DataType."""
    import pyarrow as pa
    tl = declared_type.strip()
    tl_lower = tl.lower()
    if tl_lower.startswith("struct<") and tl.endswith(">"):
        inner = tl[7:-1]
        pa_fields = []
        for field_str in _split_top(inner):
            field_str = field_str.strip()
            colon_idx = field_str.find(":")
            if colon_idx == -1:
                pa_fields.append(pa.field(field_str, pa.string()))
            else:
                fname = field_str[:colon_idx].strip()
                ftype_str = field_str[colon_idx + 1:].strip()
                pa_fields.append(pa.field(fname, _parse_spark_type_to_pyarrow(ftype_str)))
        return pa.struct(pa_fields)
    if tl_lower.startswith("array<") and tl.endswith(">"):
        return pa.list_(_parse_spark_type_to_pyarrow(tl[6:-1]))
    if tl_lower.startswith("map<") and tl.endswith(">"):
        parts = _split_top(tl[4:-1])
        if len(parts) >= 2:
            return pa.map_(_parse_spark_type_to_pyarrow(parts[0].strip()),
                           _parse_spark_type_to_pyarrow(parts[1].strip()))
        return pa.string()
    if tl_lower in ("struct", "object"):
        return pa.string()
    scalar_map = {
        "string": pa.string(), "varchar": pa.string(), "text": pa.string(), "char": pa.string(),
        "int": pa.int64(), "integer": pa.int64(), "long": pa.int64(), "bigint": pa.int64(),
        "short": pa.int16(), "byte": pa.int8(), "float": pa.float32(), "double": pa.float64(),
        "real": pa.float32(), "decimal": pa.float64(), "numeric": pa.float64(),
        "boolean": pa.bool_(), "bool": pa.bool_(), "date": pa.string(), "timestamp": pa.string(),
        "timestamp_ntz": pa.string(), "timestamp_ltz": pa.string(), "timestamp_tz": pa.string(),
        "binary": pa.binary(),
    }
    return scalar_map.get(tl_lower.split("(")[0].strip(), pa.string())


def _parquet_scalar_pa_type(col_type: str):
    """PyArrow scalar type for a declared Spark column (parquet writer).

    MUST match the strict Spark schema the harness builds in
    ``helpers._build_spark_schema`` / ``_resolve_spark_type`` and reads with
    ``reader.schema(spark_schema).parquet(path)``. Spark's strict parquet read
    will not convert physically-incompatible columns (DOUBLE->decimal,
    string->timestamp, INT64->date), so the on-disk physical type has to line up
    with the declared logical type (§4: declared type wins)."""
    import pyarrow as pa

    base = _canon_base(col_type)
    if base == "decimal":
        m = _DEC_RE.search(col_type.lower())
        prec = int(m.group(1)) if m else 38
        scale = int(m.group(2)) if m else 18
        return pa.decimal128(prec, scale)
    if base in ("timestamp", "timestamp_ntz", "timestamp_ltz", "timestamp_tz"):
        # Spark TimestampType / TimestampNTZType read parquet INT64 micros.
        return pa.timestamp("us")
    return {
        "string": pa.string(), "varchar": pa.string(), "text": pa.string(), "char": pa.string(),
        "int": pa.int32(),
        "long": pa.int64(),
        "short": pa.int16(), "byte": pa.int8(),
        "float": pa.float32(), "double": pa.float64(),
        "boolean": pa.bool_(),
        "date": pa.date32(),
        "binary": pa.binary(),
    }.get(base, pa.string())


def _write_typed_parquet_table(path: pathlib.Path, rows: list[dict], schema: list[dict],
                               df, complex_cols: dict[str, str]) -> None:
    """Write parquet with explicit scalar types (matches harness seeding)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pa_fields, pa_columns = [], []
    col_order = [c["name"] for c in schema]
    for col_def in schema:
        col_name = col_def["name"]
        if col_name in complex_cols:
            pa_type = _parse_spark_type_to_pyarrow(complex_cols[col_name])
            col_values = []
            for row in rows:
                v = row.get(col_name)
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        pass
                col_values.append(v)
            pa_fields.append(pa.field(col_name, pa_type))
            pa_columns.append(pa.array(col_values, type=pa_type))
            continue
        pa_type = _parquet_scalar_pa_type(col_def["type"])
        if pa.types.is_decimal(pa_type) or pa.types.is_timestamp(pa_type):
            # float -> decimal and string -> timestamp are not implicit pyarrow
            # casts, so build these columns from converted Python values.
            raw = [row.get(col_name) for row in rows] if col_name in df.columns \
                else [None] * len(rows)
            if pa.types.is_decimal(pa_type):
                col_values = [_to_decimal(v, pa_type.scale) for v in raw]
            else:
                col_values = [_to_datetime(v) for v in raw]
            pa_col = pa.array(col_values, type=pa_type)
        elif col_name in df.columns:
            pa_col = pa.Array.from_pandas(df[col_name], type=pa_type)
        else:
            pa_col = pa.nulls(len(rows), type=pa_type)
        pa_fields.append(pa.field(col_name, pa_type, nullable=col_def.get("nullable", True)))
        pa_columns.append(pa_col)
    table = pa.table(dict(zip(col_order, pa_columns)), schema=pa.schema(pa_fields))
    pq.write_table(table, path)


def _to_decimal(value: Any, scale: int):
    """Coerce a generated value to a quantized ``decimal.Decimal`` (None passes
    through) so it can be written as a parquet ``decimal128`` column."""
    import decimal
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    try:
        q = decimal.Decimal(10) ** -scale
        return decimal.Decimal(str(value)).quantize(q, rounding=decimal.ROUND_HALF_UP)
    except (decimal.InvalidOperation, ValueError, TypeError):
        return None


def _to_datetime(value: Any):
    """Coerce a generated value to a ``datetime`` (None passes through) so it can
    be written as a parquet ``timestamp`` column."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    import datetime as _dtm
    if isinstance(value, _dtm.datetime):
        return value
    try:
        import pandas as pd
        ts = pd.to_datetime(value, errors="coerce")
        return None if ts is None or ts is pd.NaT else ts.to_pydatetime()
    except (ValueError, TypeError, ImportError):
        return None


def write_mock_parquet(path: pathlib.Path, rows: list[dict], schema: list[dict]) -> None:
    """Write rows as Parquet via pyarrow with explicit typing. Nested types are
    written as real pyarrow nested columns. Nullable int/long use pandas' nullable
    Int dtype so a null does not promote the column to float. Raises RuntimeError
    if pandas/pyarrow are not installed."""
    if not rows:
        raise ValueError("write_mock_parquet requires at least one row")
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required for Parquet writing. "
                           "Install with: pip install pandas pyarrow") from exc
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for Parquet writing. "
                           "Install with: pip install pyarrow") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    complex_cols: dict[str, str] = {}
    scalar_cols: list[dict] = []
    for col_def in schema:
        if _is_complex_type(col_def["type"]):
            complex_cols[col_def["name"]] = col_def["type"]
        else:
            scalar_cols.append(col_def)

    df = pd.DataFrame(rows)
    for col_def in scalar_cols:
        col_name = col_def["name"]
        col_type = _canon_base(col_def["type"])
        pandas_type = _PARQUET_TYPE_MAP.get(col_type, "str")
        if col_name in df.columns and pandas_type != "object":
            try:
                if pandas_type == "bool":
                    df[col_name] = df[col_name].map(
                        lambda v: None if v is None or (isinstance(v, str) and v == "")
                        else str(v).lower() in ("true", "1", "yes"))
                elif pandas_type in ("Int64", "Int32", "Int16", "Int8"):
                    # nullable integer dtype keeps ints as ints with nulls. The
                    # float(NaN)->Int cast is correct but numpy emits a benign
                    # "invalid value encountered in cast" RuntimeWarning -> silence
                    # it so stderr stays clean (harness treats stderr as a signal).
                    import warnings as _w
                    num = pd.to_numeric(df[col_name], errors="coerce").round()
                    with _w.catch_warnings():
                        _w.simplefilter("ignore", RuntimeWarning)
                        df[col_name] = num.astype(pandas_type)
                elif pandas_type in ("float32", "float64"):
                    df[col_name] = pd.to_numeric(df[col_name], errors="coerce")
                elif pandas_type == "date32":
                    df[col_name] = pd.to_datetime(df[col_name], errors="coerce").dt.date
                else:
                    # string / date / timestamp stored as text. Stringify real
                    # values but PRESERVE nulls -- df.astype("str") (or str() on a
                    # pandas-coerced NaN) would emit the literal "None"/"nan",
                    # silently defeating the guaranteed-null row for categoricals.
                    df[col_name] = df[col_name].map(
                        lambda v: None if (v is None
                                           or (isinstance(v, float) and v != v)) else str(v))
            except (ValueError, TypeError):
                pass

    _write_typed_parquet_table(path, rows, schema, df, complex_cols)


def write_mock_avro(path: pathlib.Path, rows: list[dict], schema: list[dict]) -> None:
    """Write rows as Avro via fastavro. Raises NotImplementedError if missing."""
    if not rows:
        raise ValueError("write_mock_avro requires at least one row")
    try:
        import fastavro
    except ImportError as exc:
        raise NotImplementedError("avro deps missing; downgrade to csv. "
                                  "Install with: pip install fastavro") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    avro_fields = [{"name": c["name"],
                    "type": ["null", _AVRO_TYPE_MAP.get(c["type"].lower().split("(")[0].strip(), "string")],
                    "default": None} for c in schema]
    parsed = fastavro.parse_schema({"type": "record", "name": "MockData", "fields": avro_fields})
    with path.open("wb") as f:
        fastavro.writer(f, parsed, rows)


# ---------------------------------------------------------------------------
# Read-only inspector (Step 5b review): eyeball a generated mock file
# ---------------------------------------------------------------------------

def _peek_load(path: pathlib.Path):
    """Load a mock file into a pandas DataFrame for inspection. Supports
    .parquet / .csv / .json(l) / .txt / .yaml."""
    ext = path.suffix.lower()
    if ext == ".parquet":
        import pyarrow.parquet as pq
        return pq.read_table(path).to_pandas()
    import pandas as pd
    if ext in (".json", ".jsonl", ".ndjson"):
        return pd.read_json(path, lines=True)
    if ext in (".yaml", ".yml"):
        try:
            import yaml
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            return pd.DataFrame([doc])
        return pd.DataFrame({"value": [doc]})
    if ext in (".txt", ".text", ".log"):
        return pd.DataFrame({"value": path.read_text(encoding="utf-8").splitlines()})
    return pd.read_csv(path)


def peek_file(path: pathlib.Path, n: int = 5) -> int:
    """Print, per column: dtype, null count, distinct count, and a few sample
    values -- everything the data-synthesizer's Step 5b review needs to confirm
    types / enums / join-key overlap / null coverage without hand-writing a
    reader. Returns a process exit code."""
    if not path.is_file():
        print(f"ERROR: not a file: {path}", file=sys.stderr)
        return 2
    df = _peek_load(path)
    print(f"{path.name}: {len(df)} rows x {len(df.columns)} cols")
    for c in df.columns:
        col = df[c]
        try:
            nn = int(col.isna().sum())
        except (TypeError, ValueError):
            nn = "n/a"
        try:
            distinct = int(col.nunique(dropna=True))
            sample = [v for v in col.dropna().unique()[:n]]
        except TypeError:
            # array<struct>/list/dict cells are unhashable, so nunique()/unique()
            # raise; show a placeholder + head() sample instead of crashing.
            distinct = "n/a (unhashable: array/struct)"
            sample = [v for v in col.dropna().head(n).tolist()]
        print(f"  {c}: dtype={col.dtype} nulls={nn} distinct={distinct} sample={sample}")
    return 0


# ---------------------------------------------------------------------------
# Split schemas layout (manifest.json + entrypoints/<id>/ directory)
# ---------------------------------------------------------------------------

def entrypoint_dir(ep_id: str) -> str:
    return "entrypoints/%s" % ep_id


def load_entrypoint(schemas_dir, ep_id: str) -> dict:
    """Delegate to helpers.load_entrypoint (directory layout)."""
    from helpers import load_entrypoint as _impl  # type: ignore[import-not-found]
    return _impl(schemas_dir, ep_id)


def _write_json_atomic(path: pathlib.Path, data: dict) -> None:
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".datagen_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.write("\n")
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_schemas_dir(schemas_dir, result: dict) -> None:
    """Write ``schemas/manifest.json`` and per-entrypoint directory layout."""
    root = pathlib.Path(schemas_dir)
    eps = result.get("entrypoints") or []
    weights = result.get("weights") or {}
    manifest = {
        "root": result.get("root"),
        "complete": result.get("complete", False),
        "summary": result.get("summary", {}),
        "expected_divergences": result.get("expected_divergences") or {},
        "entrypoints": [
            {"id": ep["id"], "path": ep.get("path", ep["id"]),
             "dir": entrypoint_dir(ep["id"]),
             "source_runtime": ep.get("source_runtime"),
             "weight": (weights.get(ep["id"]) or {}).get("weight"),
             "weight_breakdown": (weights.get(ep["id"]) or {}).get("weight_breakdown")}
            for ep in eps
        ],
    }
    _write_json_atomic(root / "manifest.json", manifest)
    # sql_files.json is the data-synthesizer's starting catalog of tables each *.sql
    # template reads/writes. The data-synthesizer merges these into each entrypoint's
    # `tables` and then deletes this file as its final step (it is never read by
    # datagen or any runner after generation). Only write it when the miner produced a catalog.
    sql_files = result.get("sql_files")
    if sql_files:
        _write_json_atomic(root / "sql_files.json", sql_files)
    for ep in eps:
        _write_ep_dir(root, ep)


def _write_ep_dir(root: pathlib.Path, ep: dict) -> None:
    """Write one entrypoint to its directory layout: _meta.json + tables/*.json."""
    from helpers import split_entrypoint as _split, _table_filename as _tfn  # type: ignore[import-not-found]
    meta, tables = _split(ep)
    _write_json_atomic(root / entrypoint_dir(ep["id"]) / "_meta.json", meta)
    used: set = set()
    for key, tbl_entry in tables.items():
        fname = _tfn(key, used)
        tbl_data = dict(tbl_entry)
        tbl_data["_table_key"] = key
        _write_json_atomic(
            root / entrypoint_dir(ep["id"]) / "tables" / (fname + ".json"),
            tbl_data,
        )


def read_manifest(schemas_dir) -> dict:
    path = pathlib.Path(schemas_dir) / "manifest.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_entrypoints(schemas_dir, manifest: dict | None = None) -> list:
    root = pathlib.Path(schemas_dir)
    if manifest is None:
        manifest = read_manifest(root)
    from helpers import load_entrypoint as _load  # type: ignore[import-not-found]
    out = []
    for ref in manifest.get("entrypoints") or []:
        out.append(_load(root, ref["id"]))
    return out


def save_manifest(schemas_dir, manifest: dict) -> None:
    _write_json_atomic(pathlib.Path(schemas_dir) / "manifest.json", manifest)


def save_entrypoint(schemas_dir, ep: dict) -> None:
    """Full-sync write: _meta.json + all current table files; delete stale tables."""
    from helpers import split_entrypoint as _split, _table_filename as _tfn  # type: ignore[import-not-found]
    root = pathlib.Path(schemas_dir)
    meta, tables = _split(ep)
    ep_dir = root / entrypoint_dir(ep["id"])
    _write_json_atomic(ep_dir / "_meta.json", meta)
    tables_dir = ep_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    # Pass 1: scan existing files → key→stem mapping + all existing stems
    existing_key_to_stem: dict = {}
    all_stems: set = set()
    for p in list(tables_dir.glob("*.json")):
        all_stems.add(p.stem)
        try:
            tbl = json.loads(p.read_text(encoding="utf-8"))
            key = tbl.get("_table_key")
            if key is not None:
                existing_key_to_stem[key] = p.stem
        except (OSError, json.JSONDecodeError):
            pass

    # Pass 2: write current tables, reusing existing filenames where possible
    used = set(all_stems)
    written_stems: set = set()
    for key, tbl_entry in tables.items():
        if key in existing_key_to_stem:
            stem = existing_key_to_stem[key]
        else:
            stem = _tfn(key, used)
        tbl_data = dict(tbl_entry)
        tbl_data["_table_key"] = key
        _write_json_atomic(tables_dir / (stem + ".json"), tbl_data)
        written_stems.add(stem)

    # Pass 3: delete stale files
    for p in list(tables_dir.glob("*.json")):
        if p.stem not in written_stems:
            p.unlink()


def save_table(schemas_dir, ep_id: str, table_key: str, table_entry: dict) -> None:
    """Write/overwrite a single table file without touching other files or _meta.json.

    Matches an existing file by ``_table_key`` if present; otherwise creates a
    new uniquely-named file.
    """
    from helpers import _table_filename as _tfn  # type: ignore[import-not-found]
    root = pathlib.Path(schemas_dir)
    tables_dir = root / entrypoint_dir(ep_id) / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    # Scan all existing files
    target: pathlib.Path | None = None
    all_stems: set = set()
    for p in list(tables_dir.glob("*.json")):
        all_stems.add(p.stem)
        try:
            tbl = json.loads(p.read_text(encoding="utf-8"))
            if tbl.get("_table_key") == table_key:
                target = p
        except (OSError, json.JSONDecodeError):
            pass

    if target is None:
        stem = _tfn(table_key, all_stems)
        target = tables_dir / (stem + ".json")

    tbl_data = dict(table_entry)
    tbl_data["_table_key"] = table_key
    _write_json_atomic(target, tbl_data)


def recompute_manifest_status(
    manifest: dict, entrypoints: list,
) -> None:
    """Refresh ``summary`` and ``complete`` from entrypoint bodies."""
    summary = manifest.setdefault("summary", {})
    n_tables = sum(len(ep.get("tables") or {}) for ep in entrypoints)
    n_nonrel = sum(
        1
        for ep in entrypoints
        for t in (ep.get("tables") or {}).values()
        if not t.get("relational", True)
    )
    open_todos = sum(
        1
        for ep in entrypoints
        for it in (ep.get("tables") or {}).values()
        if isinstance(it, dict) and it.get("llm_todo")
    )
    summary.update({
        "n_entrypoints": len(entrypoints),
        "n_tables": n_tables,
        "n_non_relational": n_nonrel,
        "open_todos": open_todos,
    })
    needs = _scan_needs_llm(entrypoints)
    manifest["complete"] = not _remaining_llm_todos(entrypoints) and not needs


# ---------------------------------------------------------------------------
# CLI / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _ensure_libstdcxx_preload()
    import json
    import sys
    argv = sys.argv[1:]
    _USAGE = (
        "datagen.py <schemas_dir> [out_dir] [--all]\n"
        "  generate  : datagen.py schemas/ mock_data\n"
        "              -> validates the schemas, generates mocks for every valid\n"
        "                 table (hash-driven: only regenerates when the schema_hash\n"
        "                 changed or the mock is missing), then validates the\n"
        "                 generated mocks. Tables with a problem get no data and\n"
        "                 any stale mock of theirs is deleted, so mock_data always\n"
        "                 reflects the last run. Outputs {ok, gated_tables, problems}.\n"
        "  force-all : datagen.py schemas/ mock_data --all\n"
        "              -> regenerate ALL mocks unconditionally (ignores hashes).\n"
        "  peek      : datagen.py <mock_file> --peek [sample_n=5]  (READ-ONLY)\n"
        "              -> per-column dtype/nulls/distinct/sample for a generated\n"
        "                 .parquet/.csv/.json(l)/.txt mock (Step 5b review).\n"
        "  out_dir defaults to ./mock_data\n"
        "  (a bare schema list arg prints demo rows for that schema)"
    )
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        sys.exit(0)
    # peek mode: inspect one already-generated mock file (no JSON parse)
    if "--peek" in argv:
        rest = [a for a in argv if a != "--peek"]
        if not rest:
            print(_USAGE); sys.exit(2)
        peek_n = int(rest[1]) if len(rest) > 1 else 5
        sys.exit(peek_file(pathlib.Path(rest[0]), peek_n))
    force_all = "--all" in argv
    argv = [a for a in argv if a != "--all"]
    arg = argv[0] if argv else None
    schemas_path = pathlib.Path(arg) if arg else None

    if schemas_path and (schemas_path / "manifest.json").is_file():
        out = argv[1] if len(argv) > 1 else "./mock_data"
        manifest = read_manifest(schemas_path)
        from helpers import SchemaCollisionError, load_entrypoint  # type: ignore[import-not-found]
        # A table-name collision is an entrypoint-level problem, not a run-level
        # abort: that entrypoint cannot be loaded (and so gets no data), while
        # every other entrypoint in the batch still generates.
        ep_ids = [ref["id"] for ref in manifest.get("entrypoints") or []]
        entrypoints = []
        collision_problems: dict = {}
        for ep_id in ep_ids:
            try:
                entrypoints.append(load_entrypoint(schemas_path, ep_id))
            except SchemaCollisionError as e:
                collision_problems[ep_id] = [f"{ep_id}: {e}"]
                print(f"[datagen.py] FATAL: {e}", file=sys.stderr)
        _sql_files_path = schemas_path / "sql_files.json"
        try:
            _sql_files = json.loads(_sql_files_path.read_text()) if _sql_files_path.is_file() else None
        except (json.JSONDecodeError, OSError):
            _sql_files = None

        # 1. validate the schemas; anything with a problem gets no data
        problems = verify_schema(entrypoints, sql_files=_sql_files)
        gated = tables_to_gate(problems, entrypoints)
        # 2. generate data for every remaining (completely valid) table
        man = seed_workload(entrypoints, out, force_all=force_all, gated_tables=gated)
        table_paths = man.pop("table_paths")
        # 3. validate what was generated; a table whose mock is wrong loses it
        mock_problems, overlap_warnings = verify_mocks(entrypoints, out, set(table_paths))
        bad_mocks = tables_to_gate(mock_problems, entrypoints)
        for key, msgs in mock_problems.items():
            problems.setdefault(key, []).extend(msgs)
        gated |= bad_mocks
        # 4. the mock dir now reflects this run exactly — nothing stale survives
        sweep_mock_dir(out, ep_ids, {p for key, paths in table_paths.items()
                                    if key not in bad_mocks for p in paths})
        # A table whose mock was deleted must not keep claiming one.
        for ep in entrypoints:
            for tname, t in ep.get("tables", {}).items():
                if f"{ep['id']}.{tname}" in bad_mocks:
                    t.pop("mock_file", None)
        problems.update(collision_problems)

        warnings = verify_warnings(entrypoints, manifest=manifest) + overlap_warnings
        ok = not problems
        for ep in entrypoints:
            save_entrypoint(schemas_path, ep)
        recompute_manifest_status(manifest, entrypoints)
        save_manifest(schemas_path, manifest)
        out_payload = dict(man)
        out_payload["ok"] = ok
        out_payload["gated_tables"] = sorted(gated) or None
        out_payload["problems"] = problems or None
        out_payload["warnings"] = warnings
        print(json.dumps(out_payload, indent=2, default=str))
        sys.exit(0 if ok else 1)

    obj = json.load(open(arg)) if arg else None

    # otherwise treat the arg as a single schema (or use the demo schema)
    schema = obj if obj is not None else [
        {"name": "session_id", "type": "string", "nullable": False},
        {"name": "rating_value", "type": "double", "nullable": True},
        {"name": "region_code", "type": "string", "nullable": True, "values": ["US", "EU", "APAC"]},
        {"name": "event_ts", "type": "long", "nullable": False},
    ]
    rows = generate_rows(schema, n=8, key_pools={"session_id": ["s1", "s2", "s3"]},
                         categoricals=categoricals_from_columns(schema))
    print(json.dumps(rows, indent=2, default=str))

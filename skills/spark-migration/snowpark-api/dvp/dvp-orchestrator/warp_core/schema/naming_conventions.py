"""
Column type inference from naming conventions.

Provides a configurable, YAML-driven mechanism for inferring column types
based on naming patterns (prefixes, suffixes, exact matches, contains, regex).

Usage:
    from warp_core.schema.naming_conventions import apply_naming_conventions

    # Auto-discovers column_types.yaml next to the workload:
    apply_naming_conventions(asg, workload_root="/path/to/workload")

    # Explicit config file:
    apply_naming_conventions(asg, config_path="/path/to/column_types.yaml")

    # Built-in defaults only:
    apply_naming_conventions(asg)

YAML format (column_types.yaml):

    prefixes:
      TK_: STRING
      DT_: DATE

    suffixes:
      _COUNT: L_INT

    exact:
      RANK: L_INT

    contains:
      AMOUNT: NUMERIC

    regex:
      "^NB\\d+$": L_INT

    # Set to false to disable built-in defaults entirely
    include_defaults: true
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from warp_core.ir.pyspark_models import (
    ASG,
    InferenceConfidence,
    InferenceSource,
    InferredColumn,
)

CONFIG_FILENAME = "column_types.yaml"

BUILTIN_PREFIXES: dict[str, str] = {
    # Standards-backed (Oracle OBIEE/OFSAA)
    "CD_": "STRING",
    "DT_": "DATE",
    # Data Vault 2.0 + Sherlock (MIT/Google): id_ prefix is statistically
    # equivalent to _id suffix as a nominal-data indicator. Must be STRING
    # to preserve leading zeros and avoid precision errors (ISO 20022).
    "ID_": "STRING",    # id_prest, id_rappro, id_source_1, id_ref_1
    # ISO 11179-5 (Naming and Identification Principles): "Date" is a
    # representation term. VizNet corpus: >98% of date_* cols are temporal.
    "DATE_": "DATE",    # date_inscr, date_assign, date_first_access
    # TFD / Convention (identifier key prefixes)
    "PK_": "STRING",
    "FK_": "STRING",
    # Framework conventions (Rails/Django/Spark boolean prefixes)
    "IS_": "BOOLEAN",
    "HAS_": "BOOLEAN",
    "CAN_": "BOOLEAN",
    "SHOULD_": "BOOLEAN",
    # SQL aggregate function aliases (output type follows aggregate semantics)
    "AVG_": "NUMERIC",   # avg_confidence, avg_entities, avg_length
    "MIN_": "NUMERIC",   # min_length, min_value, min_price
    "MAX_": "NUMERIC",   # max_length, max_value, max_price
}

BUILTIN_SUFFIXES: dict[str, str] = {
    # --- Identifiers (STRING) ---
    # Oracle OBIEE/OFSAA + TFD + Google Research
    "_ID": "STRING",
    "_CD": "STRING",
    "_CODE": "STRING",
    "_NAME": "STRING",
    "_NM": "STRING",
    "_DESC": "STRING",
    "_DESCRIPTION": "STRING",
    "_KEY": "STRING",
    "_NO": "STRING",
    "_GUID": "STRING",
    "_UUID": "STRING",
    # --- Free-text content (STRING) ---
    # SchemaPile + clinical/operational data patterns
    "_TEXT": "STRING",    # note_text, deid_text, entity_text, body_text
    "_TYPE": "STRING",    # entity_type, note_type, record_type
    "_METHOD": "STRING",  # processing_method, auth_method, payment_method
    # Schema.org ActionStatusType / EventStatusType / OrderStatus:
    # 100% of status definitions are Text/Enumeration, never a metric.
    "_STATUS": "STRING",  # learning_plan_status, order_status, job_status
    # Sherlock (MIT/Google, 2020) / ISO 20022:
    # Identifiers have 94.2% probability of being nominal (String).
    # Treating _NUMBER as integer risks leading-zero loss (policy numbers, etc).
    "_NUMBER": "STRING",  # policy_number, ambest_number, reference_number
    # --- Temporal ---
    # Oracle OBIEE + IBM + TFD
    "_DT": "DATE",
    "_DATE": "DATE",
    "_AT": "TIMESTAMP",
    "_TS": "TIMESTAMP",
    "_TIMESTAMP": "TIMESTAMP",
    # --- Quantitative (NUMERIC) ---
    # Oracle OFSAA + TFD + Google Research
    "_AMT": "NUMERIC",
    "_AMOUNT": "NUMERIC",
    "_PRICE": "NUMERIC",
    "_COST": "NUMERIC",
    "_RATE": "NUMERIC",
    "_RATIO": "NUMERIC",
    "_PCT": "NUMERIC",
    "_PERCENT": "NUMERIC",
    "_AVG": "NUMERIC",
    # IEEE 754 / SQL aggregate standards: 99.8% of columns containing AVERAGE
    # in their name are mapped to FLOAT64/DECIMAL/NUMERIC (Kaggle/BigQuery data).
    "_AVERAGE": "NUMERIC",  # acr_average, training_average, sos_average
    "_WEIGHT": "NUMERIC",
    "_SCORE": "NUMERIC",
    # --- Counts (INTEGER) ---
    # TFD + common practice
    "_COUNT": "L_INT",
    "_CNT": "L_INT",
    "_QTY": "L_INT",
    "_QUANTITY": "L_INT",
    # --- Boolean ---
    # Oracle OBIEE/OFSAA + TFD + Rails/Django/Spark
    "_FLG": "BOOLEAN",
    "_FLAG": "BOOLEAN",
    "_IND": "BOOLEAN",
}

BUILTIN_EXACT: dict[str, str] = {
    # --- SchemaPile top-15 (221K schemas, 10.8M columns) ---
    "ID": "STRING",
    "NAME": "STRING",
    "DESCRIPTION": "STRING",
    "CREATED_AT": "TIMESTAMP",
    "UPDATED_AT": "TIMESTAMP",
    "DELETED_AT": "TIMESTAMP",
    "EMAIL": "STRING",
    "STATUS": "STRING",
    "TITLE": "STRING",
    "USERNAME": "STRING",
    # --- Sherlock 78 semantic types (KDD 2019, MIT) ---
    "ADDRESS": "STRING",
    "CATEGORY": "STRING",
    "CITY": "STRING",
    "COMPANY": "STRING",
    "COUNTRY": "STRING",
    "CURRENCY": "STRING",
    "GENDER": "STRING",
    "LANGUAGE": "STRING",
    "LOCATION": "STRING",
    "NOTES": "STRING",
    "REGION": "STRING",
    "AGE": "NUMERIC",
    "DURATION": "NUMERIC",
    "RANK": "NUMERIC",
    "RANKING": "NUMERIC",
    "SALES": "NUMERIC",
    "WEIGHT": "NUMERIC",
    "YEAR": "NUMERIC",
    "SCORE": "NUMERIC",
    "DATE": "DATE",
    "TIME": "TIMESTAMP",
    # --- High-Confidence Exact Matches ---
    # ORM/ETL audit temporal (Hibernate, Django, Eloquent, Rails)
    "PUSHED_AT": "TIMESTAMP",
    "LAST_MODIFIED": "TIMESTAMP",
    "MODIFIED_AT": "TIMESTAMP",
    "VALID_FROM": "TIMESTAMP",
    "VALID_TO": "TIMESTAMP",
    # Audit date (DW / transactional systems)
    "CREATED_DATE": "DATE",
    "MODIFIED_DATE": "DATE",
    "UPDATE_DATE": "DATE",
    "BIRTH_DATE": "DATE",
    "EFFECTIVE_DATE": "DATE",
    "END_DATE": "DATE",
    "START_DATE": "DATE",
    # Boolean indicators (framework conventions)
    # IANA Time Zone Database standard: timezone is always a string identifier.
    "TIMEZONE": "STRING",
    # SQL:2016 + Kimball Star Schema: COUNT() returns INTEGER; a column named
    # "count" is almost universally a fact measure of record cardinality.
    "COUNT": "L_INT",
    # ITIL 4 / ISO 20000: severity is a qualitative classification dimension
    # (Critical/High/Medium/Low) exchanged as text between ticketing systems.
    "SEVERITY": "STRING",
    # ML Metadata (MLMD) / Vertex AI / scikit-learn: confidence is a float
    # probability score in [0,1] — never categorical in documented ML systems.
    "CONFIDENCE": "NUMERIC",
    "IS_ACTIVE": "BOOLEAN",
    "IS_DELETED": "BOOLEAN",
    "IS_ENABLED": "BOOLEAN",
    "IS_VALID": "BOOLEAN",
    "HAS_ACCESS": "BOOLEAN",
    "IS_ADMIN": "BOOLEAN",
    # Count / audit numeric
    "ROW_COUNT": "L_INT",
    "VERSION": "L_INT",
    "RETRY_COUNT": "L_INT",
    "TOTAL_ROWS": "L_INT",
    "RECORD_COUNT": "L_INT",
    # --- Framework conventions (Rails/Django) ---
    "CREATED_BY": "STRING",
    "UPDATED_BY": "STRING",
    "DELETED_BY": "STRING",
    "CREATED_ON": "DATE",
    "UPDATED_ON": "DATE",
    # --- Common practice (low-risk) ---
    "STATE": "STRING",
    "BRAND": "STRING",
}


@dataclass
class NamingConventionConfig:
    """Resolved naming-convention rules ready for matching."""

    prefixes: dict[str, str] = field(default_factory=dict)
    suffixes: dict[str, str] = field(default_factory=dict)
    exact: dict[str, str] = field(default_factory=dict)
    contains: dict[str, str] = field(default_factory=dict)
    regex: list[tuple[re.Pattern, str]] = field(default_factory=list)

    def match(self, column_name: str) -> str | None:
        """Return the inferred type for *column_name*, or ``None``."""
        upper = column_name.upper()

        if upper in self.exact:
            return self.exact[upper]

        for prefix, typ in self.prefixes.items():
            if upper.startswith(prefix):
                return typ

        for suffix, typ in self.suffixes.items():
            if upper.endswith(suffix):
                return typ

        for fragment, typ in self.contains.items():
            if fragment in upper:
                return typ

        for pattern, typ in self.regex:
            if pattern.search(upper):
                return typ

        return None


def load_config(
    config_path: str | Path | None = None,
    workload_root: str | Path | None = None,
    include_defaults: bool = True,
) -> NamingConventionConfig:
    """Build a NamingConventionConfig from YAML + built-in defaults.

    Resolution order (first match wins):
      1. User-supplied rules (from YAML)
      2. Built-in defaults (unless include_defaults=False)
    """
    user_data: dict[str, Any] = {}

    resolved_path = _find_config_file(config_path, workload_root)
    if resolved_path:
        user_data = _read_yaml(resolved_path)

    if user_data.get("include_defaults") is False:
        include_defaults = False

    prefixes: dict[str, str] = {}
    suffixes: dict[str, str] = {}
    exact: dict[str, str] = {}
    contains: dict[str, str] = {}
    regex_patterns: list[tuple[re.Pattern, str]] = []

    _merge_dict(prefixes, _upper_keys(user_data.get("prefixes", {})))
    _merge_dict(suffixes, _upper_keys(user_data.get("suffixes", {})))
    _merge_dict(exact, _upper_keys(user_data.get("exact", {})))
    _merge_dict(contains, _upper_keys(user_data.get("contains", {})))

    for pattern_str, typ in (user_data.get("regex") or {}).items():
        try:
            regex_patterns.append((re.compile(pattern_str, re.IGNORECASE), typ))
        except re.error:
            pass

    if include_defaults:
        _merge_dict(prefixes, BUILTIN_PREFIXES, overwrite=False)
        _merge_dict(suffixes, BUILTIN_SUFFIXES, overwrite=False)
        _merge_dict(exact, BUILTIN_EXACT, overwrite=False)

    return NamingConventionConfig(
        prefixes=prefixes,
        suffixes=suffixes,
        exact=exact,
        contains=contains,
        regex=regex_patterns,
    )


def apply_naming_conventions(
    asg: ASG,
    *,
    config_path: str | Path | None = None,
    workload_root: str | Path | None = None,
    config: NamingConventionConfig | None = None,
) -> None:
    """Apply naming-convention type inference to all UNKNOWN columns in the ASG."""
    if config is None:
        root = workload_root or getattr(
            asg.extraction_metadata, "workload_root", None
        )
        config = load_config(config_path=config_path, workload_root=root)

    def _maybe_infer(col: InferredColumn) -> None:
        if col.inferred_type and col.inferred_type != "UNKNOWN":
            return
        typ = config.match(col.name)
        if typ:
            col.inferred_type = typ
            col.source = InferenceSource.NAMING_CONVENTION
            col.confidence = InferenceConfidence.MEDIUM

    for node in list(asg.data_in) + list(asg.data_out):
        for col in node.inferred_columns or []:
            _maybe_infer(col)

    for tx in asg.transformations:
        for col in tx.inferred_input or []:
            _maybe_infer(col)
        for col in tx.inferred_output or []:
            _maybe_infer(col)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_config_file(
    config_path: str | Path | None,
    workload_root: str | Path | None,
) -> Path | None:
    if config_path:
        p = Path(config_path)
        return p if p.is_file() else None
    if workload_root:
        current = Path(workload_root).resolve()
        for _ in range(10):
            candidate = current / CONFIG_FILENAME
            if candidate.is_file():
                return candidate
            if (current / ".git").exists() or current == current.parent:
                break
            current = current.parent
    return None


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _upper_keys(d: Any) -> dict[str, str]:
    if not isinstance(d, dict):
        return {}
    return {str(k).upper(): str(v) for k, v in d.items()}


def _merge_dict(
    target: dict[str, str],
    source: dict[str, str],
    *,
    overwrite: bool = True,
) -> None:
    for k, v in source.items():
        if overwrite or k not in target:
            target[k] = v

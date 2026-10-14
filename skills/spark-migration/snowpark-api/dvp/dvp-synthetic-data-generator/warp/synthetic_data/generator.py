"""
Synthetic Data Generator - Generate test data that satisfies workload constraints.

Input sources (collaborative architecture):
- data_io.json (REQUIRED): Input schemas and column types
- ASG JSON (OPTIONAL): column_constraints, column_relationships
- entrypoints.json (OPTIONAL): Filter by specific entry point

Output: CSV files per input table in synthetic_data/ folder

Phase 1 algorithms:
- Topological DAG generation with cardinality modeling
- Null-aware domain solver (interval arithmetic + relative time)
- IPOG-lite pairwise coverage for branch combinations

Phase 2 additions:
- Hybrid Z3 solver for cross-column constraints and contradiction confirmation
- Adversarial noise injection (boundary, format, null, type, temporal)
- Verification gap detection (constraint contradictions, circular dependencies, Z3 unsat)

Phase 3 additions:
- Validator Loop (The Oracle): ASG flow simulator for expected output computation
- Expected results generation (expected_results.json) for automated DVP
- Transformation coverage tracking (visited, evaluated, filter drops)
"""

from __future__ import annotations

import csv
import json
import math
import random
import re as _re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from itertools import combinations
from pathlib import Path
from typing import Any

from warp_core.diagnostics import (
    DiagnosticIssue,
    DiagnosticReport,
    Severity,
    IssueCategory,
    SyntheticIssueCode,
)

_RELATIVE_TIME_PATTERNS = _re.compile(
    r"(current_date|now)\s*\(\s*\)", _re.IGNORECASE
)

_PYSPARK_LITERAL_RE = _re.compile(
    r"""^(?:F\.)?lit\(\s*(['"]?)(.*?)\1\s*\)$""", _re.IGNORECASE
)
_WRAPPED_QUOTES_RE = _re.compile(r"""^['"](.+)['"]$""")


class GenerationStrategy(str, Enum):
    """Available data generation strategies."""
    CONSTRAINT_FIRST = "constraint_first"
    JOIN_AWARE = "join_aware"
    BRANCH_COVERAGE = "branch_coverage"


@dataclass
class ColumnSpec:
    """Specification for a column."""
    name: str
    data_type: str = "UNKNOWN"
    is_nullable: bool = True
    is_join_key: bool = False
    default_value: str | None = None


@dataclass
class TableSpec:
    """Specification for generating a table."""
    name: str
    columns: list[ColumnSpec] = field(default_factory=list)


def _bound_to_day_offset(value: float | None, base: datetime, default: int) -> int:
    """Convert a constraint bound to a day offset from *base*.

    Handles two cases:
    - YYYYMMDD integer (e.g. 20220501) — parsed as a real date, delta in days computed.
    - Plain day offset (e.g. -365) — used directly, clamped to ±36500 (100 years).
    """
    if value is None:
        return default
    v = int(value)
    # YYYYMMDD literals: 8 digits between 19000101 and 20991231
    if 19_000_101 <= v <= 20_991_231:
        try:
            target = datetime(v // 10000, (v % 10000) // 100, v % 100)
            return (target - base).days
        except ValueError:
            pass
    return max(-36_500, min(36_500, v))


def _bound_to_sec_offset(value: float | None, base: datetime, default: int) -> int:
    """Convert a constraint bound to a second offset from *base*.

    Handles Unix timestamps (> ~1e9) by computing delta in seconds from *base*.
    Plain second offsets are clamped to ±3_153_600_000 (100 years).
    """
    if value is None:
        return default
    v = int(value)
    # Unix timestamp heuristic: positive value larger than year-2000 epoch seconds
    if v > 946_684_800:
        try:
            target = datetime.fromtimestamp(v)
            return int((target - base).total_seconds())
        except (OSError, OverflowError, ValueError):
            pass
    return max(-3_153_600_000, min(3_153_600_000, v))


@dataclass
class DomainState:
    """Resolved generation domain for a single column.

    Encapsulates the valid range, enumerated values, null probability,
    and fixed value derived from ColumnConstraint entries.
    """
    low: float | None = None
    high: float | None = None
    enum_values: list[Any] | None = None
    null_probability: float = 0.05
    fixed_value: Any | None = None


class SyntheticDataGenerator:
    """
    Generates synthetic data that satisfies workload constraints.

    Strategies compose as layers:
    - JOIN_AWARE (default): DAG ordering + key sampling + cardinality + domain solver
    - CONSTRAINT_FIRST: Domain solver for all values (no DAG ordering)
    - BRANCH_COVERAGE: Pairwise covering array + DAG + domain solver
    """

    def __init__(
        self,
        data_io: list[dict],
        constraints: list[dict] | None = None,
        relationships: list[dict] | None = None,
    ):
        self.data_io = data_io
        self._constraints = constraints or []
        self._relationships = relationships or []
        self._tables: dict[str, TableSpec] = {}
        self._join_key_pools: dict[str, list] = {}
        self._branch_values: dict[str, list[list]] = {}
        self._source_map: dict[str, str] = {}
        self._generated_data: dict[str, list[dict]] = {}
        self._reference_timestamp: datetime = datetime.now()
        self._issues: DiagnosticReport = DiagnosticReport(tool_name="synthetic_data")

        self._analyze()

    @property
    def issues(self) -> DiagnosticReport:
        return self._issues

    def _add_issue(
        self,
        code: str,
        severity: Severity,
        category: IssueCategory,
        message: str,
        context: dict,
        suggestion: str = "",
    ) -> None:
        self._issues.add(DiagnosticIssue(
            code=code,
            severity=severity,
            category=category,
            message=message,
            context=context,
            suggestion=suggestion,
        ))

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _build_source_map(self) -> None:
        """Map source IDs (e.g. ``in_002``) to table names.

        Checks data_io first, then falls back to ASG data_in entries
        stored in ``self._asg_data_in`` (populated by ``from_files``).
        """
        for item in self.data_io:
            src_id = item.get("id", "")
            name = item.get("name", "")
            if src_id and name:
                self._source_map[src_id] = name

        for item in getattr(self, "_asg_data_in", []):
            src_id = item.get("id", "")
            name = item.get("name", "")
            if src_id and name and src_id not in self._source_map:
                self._source_map[src_id] = name

    def _analyze(self) -> None:
        """Analyze inputs to build table specifications."""
        self._build_source_map()

        for rel in self._relationships:
            for side in ("left_column", "right_column"):
                key = rel.get(side)
                if key and key not in self._join_key_pools:
                    self._join_key_pools[key] = list(range(1, 101))

        for c in self._constraints:
            if c.get("constraint_type") in ("in", "enum") and c.get("value"):
                col = c.get("column_name")
                if col not in self._branch_values:
                    self._branch_values[col] = []
                self._branch_values[col].append(c.get("value", []))

        for item in self.data_io:
            if item.get("role") != "input":
                continue

            name = item.get("name", "")
            if not name or name == "unknown":
                self._add_issue(
                    code=SyntheticIssueCode.NO_SCHEMA,
                    severity=Severity.WARNING,
                    category=IssueCategory.MISSING_SCHEMA,
                    message="Input skipped: no name or 'unknown'",
                    context={
                        "item_name": name or "N/A",
                        "item_type": item.get("type"),
                        "item_format": item.get("format"),
                    },
                    suggestion="Ensure input has a valid name in ASG",
                )
                continue

            columns = []
            for col in item.get("columns", []):
                col_name = col.get("name", "")
                if not col_name:
                    continue
                col_type = col.get("type", "UNKNOWN")
                if col_type == "UNKNOWN":
                    self._add_issue(
                        code=SyntheticIssueCode.TYPE_UNKNOWN,
                        severity=Severity.INFO,
                        category=IssueCategory.UNKNOWN_TYPE,
                        message="Column has unknown type, using STRING default",
                        context={"table": name, "column": col_name},
                        suggestion="Add type inference pattern or provide schema",
                    )
                columns.append(ColumnSpec(
                    name=col_name,
                    data_type=col_type,
                    is_join_key=col_name in self._join_key_pools,
                    default_value=col.get("default_value"),
                ))

            if columns:
                self._tables[name] = TableSpec(name=name, columns=columns)
            else:
                self._add_issue(
                    code=SyntheticIssueCode.NO_SCHEMA,
                    severity=Severity.WARNING,
                    category=IssueCategory.MISSING_SCHEMA,
                    message="Input has no columns to generate",
                    context={"table": name, "item_type": item.get("type")},
                    suggestion="Check if input schema is being captured",
                )

    # ------------------------------------------------------------------
    # Domain Solver  (Phase 1.2)
    # ------------------------------------------------------------------

    def _resolve_domain(
        self, col_name: str, data_type: str, table_name: str = ""
    ) -> DomainState:
        """Compute the valid generation domain for *col_name* via interval
        arithmetic over all matching ``ColumnConstraint`` entries."""
        domain = DomainState()
        col_constraints = [
            c for c in self._constraints
            if c.get("column_name") == col_name
        ]
        if not col_constraints:
            return domain

        has_null_directive = False
        for c in col_constraints:
            ct = c.get("constraint_type", "")
            raw_val = c.get("value")

            if ct == "not_null":
                domain.null_probability = 0.0
                has_null_directive = True
                continue
            if ct == "is_null":
                domain.null_probability = 0.2
                has_null_directive = True
                continue

            val = self._coerce_constraint_value(raw_val, data_type)
            if val is None and ct not in ("like", "rlike", "not_equals"):
                continue

            if ct == "equals":
                domain.fixed_value = val
            elif ct in ("in", "enum"):
                vals = raw_val if isinstance(raw_val, list) else [raw_val]
                domain.enum_values = [self._sanitize_constraint_value(v) for v in vals]
            elif ct == "gt":
                nval = self._to_number(val)
                if nval is not None:
                    domain.low = max(domain.low, nval) if domain.low is not None else nval
            elif ct == "gte":
                nval = self._to_number(val)
                if nval is not None:
                    domain.low = max(domain.low, nval) if domain.low is not None else nval
            elif ct == "lt":
                nval = self._to_number(val)
                if nval is not None:
                    domain.high = min(domain.high, nval) if domain.high is not None else nval
            elif ct == "lte":
                nval = self._to_number(val)
                if nval is not None:
                    domain.high = min(domain.high, nval) if domain.high is not None else nval
            elif ct == "between":
                if isinstance(raw_val, list) and len(raw_val) == 2:
                    lo = self._to_number(raw_val[0])
                    hi = self._to_number(raw_val[1])
                    if lo is not None:
                        domain.low = max(domain.low, lo) if domain.low is not None else lo
                    if hi is not None:
                        domain.high = min(domain.high, hi) if domain.high is not None else hi

        if not has_null_directive:
            domain.null_probability = 0.05

        return domain

    @staticmethod
    def _sanitize_constraint_value(raw: Any) -> Any:
        """Strip PySpark literal wrappers and embedded quotes from ASG values.

        Handles patterns like ``F.lit('PICKUP')``, ``lit("PROD")``,
        and bare quoted strings ``'PICKUP'``.
        """
        if not isinstance(raw, str):
            return raw
        m = _PYSPARK_LITERAL_RE.match(raw.strip())
        if m:
            return m.group(2)
        m = _WRAPPED_QUOTES_RE.match(raw.strip())
        if m:
            return m.group(1)
        return raw

    def _coerce_constraint_value(self, raw: Any, data_type: str) -> Any:
        """Best-effort coercion of a constraint value to the column type.

        If *raw* contains template variables (f-string or bare identifiers),
        a ``ParameterResolver`` attempts to substitute them before coercion.
        """
        if raw is None:
            return None
        raw = self._sanitize_constraint_value(raw)
        if isinstance(raw, (int, float)):
            return raw
        if isinstance(raw, str):
            if "{" in raw or _re.search(r"[A-Z][A-Za-z_]\w{2,}", raw):
                resolver = self._get_parameter_resolver()
                raw = resolver.resolve_in_condition(raw)
            if _RELATIVE_TIME_PATTERNS.search(raw):
                return self._resolve_relative_time(raw, data_type)
            try:
                return float(raw) if "." in raw else int(raw)
            except (ValueError, TypeError):
                pass
        return raw

    def _get_parameter_resolver(self):
        """Lazy accessor for a ParameterResolver bound to the current timestamp."""
        if not hasattr(self, "_param_resolver"):
            from synthetic_data.validator import ParameterResolver
            self._param_resolver = ParameterResolver(
                reference_timestamp=self._reference_timestamp,
            )
        return self._param_resolver

    def _resolve_relative_time(self, expr: str, data_type: str) -> Any:
        """Resolve ``current_date()`` / ``now()`` relative to the
        reference timestamp captured at generation time."""
        base = self._reference_timestamp
        interval_match = _re.search(
            r"[+-]\s*(?:interval\s+)?(\d+)\s*(day|hour|minute|second|month|year)s?",
            expr, _re.IGNORECASE,
        )
        if interval_match:
            amount = int(interval_match.group(1))
            unit = interval_match.group(2).lower()
            sign = -1 if "-" in expr.split(interval_match.group(0))[0] else 1
            delta = timedelta()
            if unit == "day":
                delta = timedelta(days=amount * sign)
            elif unit == "hour":
                delta = timedelta(hours=amount * sign)
            elif unit == "minute":
                delta = timedelta(minutes=amount * sign)
            elif unit == "second":
                delta = timedelta(seconds=amount * sign)
            elif unit == "month":
                delta = timedelta(days=30 * amount * sign)
            elif unit == "year":
                delta = timedelta(days=365 * amount * sign)
            base = base + delta

        dt_upper = (data_type or "").upper()
        if dt_upper in ("DATE",):
            return base.strftime("%Y-%m-%d")
        return base.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _to_number(val: Any) -> float | None:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
        return None

    # ------------------------------------------------------------------
    # DAG Builder  (Phase 1.1)
    # ------------------------------------------------------------------

    def _build_dependency_dag(self) -> dict[str, set[str]]:
        """Build ``{child_table: {parent_tables}}`` from relationships."""
        dag: dict[str, set[str]] = defaultdict(set)
        all_tables = set(self._tables.keys())

        for rel in self._relationships:
            left_src = rel.get("left_source", "")
            right_src = rel.get("right_source", "")
            left_name = self._source_map.get(left_src, left_src)
            right_name = self._source_map.get(right_src, right_src)

            if left_name not in all_tables or right_name not in all_tables:
                continue
            if left_name == right_name:
                continue

            dag[right_name].add(left_name)
            if left_name not in dag:
                dag[left_name] = set()

        for t in all_tables:
            if t not in dag:
                dag[t] = set()

        return dict(dag)

    def _topological_sort(self, dag: dict[str, set[str]]) -> list[str]:
        """Kahn's algorithm.  Parents come first.  Cycles are broken with
        a warning so generation can proceed."""
        in_degree: dict[str, int] = {node: 0 for node in dag}
        children: dict[str, list[str]] = defaultdict(list)
        for child, parents in dag.items():
            in_degree.setdefault(child, 0)
            for p in parents:
                children[p].append(child)
                in_degree[child] = in_degree.get(child, 0) + 1
                in_degree.setdefault(p, 0)

        queue = sorted(n for n, d in in_degree.items() if d == 0)
        order: list[str] = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for child in sorted(children.get(node, [])):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        remaining = [n for n in dag if n not in order]
        if remaining:
            self._add_issue(
                code=SyntheticIssueCode.NO_SCHEMA,
                severity=Severity.WARNING,
                category=IssueCategory.MISSING_SCHEMA,
                message="Cycle detected in table dependencies; breaking arbitrarily",
                context={"tables_in_cycle": remaining},
                suggestion="Check column_relationships for circular joins",
            )
            if hasattr(self, "_verification_gaps"):
                self._verification_gaps.append({
                    "gap_id": f"GAP_STR_CYCLE_{'_'.join(sorted(remaining)[:3])}",
                    "category": "structural",
                    "sub_type": "circular_dependency",
                    "severity": "high",
                    "subject": f"Tables: {', '.join(sorted(remaining))}",
                    "detail": (
                        f"Tables {sorted(remaining)} form a circular dependency "
                        f"in column_relationships, preventing deterministic generation order"
                    ),
                    "suggestion": "Break the cycle by removing or reorienting one relationship.",
                    "ai_hints": {
                        "action": "break_circular_join",
                        "tables_in_cycle": sorted(remaining),
                    },
                })
            order.extend(sorted(remaining))

        return order

    def _get_join_parents(self, table_name: str) -> dict[str, tuple[str, str]]:
        """Return ``{local_col: (parent_table, parent_col)}`` for *table_name*."""
        parents: dict[str, tuple[str, str]] = {}
        all_tables = set(self._tables.keys())

        for rel in self._relationships:
            left_src = rel.get("left_source", "")
            right_src = rel.get("right_source", "")
            left_name = self._source_map.get(left_src, left_src)
            right_name = self._source_map.get(right_src, right_src)

            if right_name == table_name and left_name in all_tables and left_name != table_name:
                local_col = rel.get("right_column", "")
                parent_col = rel.get("left_column", "")
                if local_col and parent_col:
                    parents[local_col] = (left_name, parent_col)
            elif left_name == table_name and right_name in all_tables and right_name != table_name:
                local_col = rel.get("left_column", "")
                parent_col = rel.get("right_column", "")
                if local_col and parent_col:
                    parents[local_col] = (right_name, parent_col)

        return parents

    def _get_join_type_for(self, table_name: str, col_name: str) -> str:
        """Return the join type for a relationship involving *col_name*."""
        for rel in self._relationships:
            left_src = rel.get("left_source", "")
            right_src = rel.get("right_source", "")
            left_name = self._source_map.get(left_src, left_src)
            right_name = self._source_map.get(right_src, right_src)

            if (right_name == table_name and rel.get("right_column") == col_name) or \
               (left_name == table_name and rel.get("left_column") == col_name):
                return (rel.get("join_type") or "inner").lower()
        return "inner"

    # ------------------------------------------------------------------
    # Cardinality Modeling  (Phase 1.1)
    # ------------------------------------------------------------------

    @staticmethod
    def _sample_key_pareto(
        parent_keys: list[Any], count: int, miss_rate: float = 0.0,
    ) -> list[Any]:
        """Sample *count* keys from *parent_keys* with Pareto-like skew.

        ~80%% of selections come from the top ~20%% of keys.
        If *miss_rate* > 0, a fraction of keys will NOT exist in
        *parent_keys* (for LEFT JOIN testing).
        """
        if not parent_keys:
            return list(range(1, count + 1))

        unique = list(dict.fromkeys(parent_keys))
        n = len(unique)

        weights = [1.0 / (i + 1) ** 0.8 for i in range(n)]
        total_w = sum(weights)
        weights = [w / total_w for w in weights]

        result: list[Any] = []
        for _ in range(count):
            if miss_rate > 0 and random.random() < miss_rate:
                result.append(_make_missing_key(unique))
            else:
                result.append(random.choices(unique, weights=weights, k=1)[0])
        return result

    # ------------------------------------------------------------------
    # Pairwise Coverage -- IPOG-lite  (Phase 1.3)
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_pairwise_rows(
        constrained_cols: dict[str, list[Any]],
    ) -> list[dict[str, Any]]:
        """Greedy pairwise (2-wise) covering array.

        Returns the minimal set of partial rows (only the constrained
        columns) such that every value-pair between any two columns
        appears at least once.
        """
        col_names = sorted(constrained_cols.keys())
        if len(col_names) < 2:
            if col_names:
                return [{col_names[0]: v} for v in constrained_cols[col_names[0]]]
            return []

        uncovered: set[tuple[str, Any, str, Any]] = set()
        for c1, c2 in combinations(col_names, 2):
            for v1 in constrained_cols[c1]:
                for v2 in constrained_cols[c2]:
                    uncovered.add((c1, v1, c2, v2))

        rows: list[dict[str, Any]] = []
        while uncovered:
            best_row: dict[str, Any] = {}
            best_score = -1

            for c in col_names:
                best_row[c] = constrained_cols[c][0]

            for _ in range(min(50, len(uncovered))):
                candidate = {
                    c: random.choice(constrained_cols[c]) for c in col_names
                }
                score = sum(
                    1 for pair in uncovered
                    if candidate.get(pair[0]) == pair[1]
                    and candidate.get(pair[2]) == pair[3]
                )
                if score > best_score:
                    best_score = score
                    best_row = candidate

            covered_this_row = {
                pair for pair in uncovered
                if best_row.get(pair[0]) == pair[1]
                and best_row.get(pair[2]) == pair[3]
            }
            uncovered -= covered_this_row
            rows.append(best_row)

        return rows

    # ------------------------------------------------------------------
    # Public generate entry-point
    # ------------------------------------------------------------------

    def generate(
        self,
        strategy: GenerationStrategy = GenerationStrategy.JOIN_AWARE,
        rows_per_table: int = 20,
        branch: str | None = None,
        noise_rate: float = 0.0,
    ) -> dict[str, list[dict]]:
        """Generate synthetic data for all input tables.

        Parameters
        ----------
        noise_rate : float
            Fraction of values to corrupt with adversarial noise (Phase 2).
            0.0 = off (default).  0.05-0.10 recommended for resilience testing.
        """
        from synthetic_data.adversarial import NoiseInjector

        self._reference_timestamp = datetime.now()
        self._generated_data = {}
        self._noise_injector = NoiseInjector(
            noise_rate=noise_rate,
            reference_timestamp=self._reference_timestamp,
        )
        self._verification_gaps: list[dict] = []
        self._z3_templates: dict[str, list[dict]] = {}
        self._z3_results: list[dict] = []

        self._resolve_z3_templates()

        if strategy == GenerationStrategy.CONSTRAINT_FIRST:
            data = self._generate_constraint_first(rows_per_table, branch)
        elif strategy == GenerationStrategy.BRANCH_COVERAGE:
            data = self._generate_branch_coverage(rows_per_table)
        else:
            data = self._generate_join_aware(rows_per_table)

        if self._noise_injector.active:
            data = self._apply_noise(data)

        self._generated_data = data
        return data

    # ------------------------------------------------------------------
    # Strategy: JOIN_AWARE  (DAG + domain solver + cardinality)
    # ------------------------------------------------------------------

    def _generate_join_aware(self, rows_per_table: int) -> dict[str, list[dict]]:
        """Generate data with topological ordering, domain-constrained
        values, and Pareto cardinality for join keys."""
        dag = self._build_dependency_dag()
        order = self._topological_sort(dag)

        table_order = [t for t in order if t in self._tables]
        for t in self._tables:
            if t not in table_order:
                table_order.append(t)

        data: dict[str, list[dict]] = {}

        for table_name in table_order:
            table_spec = self._tables[table_name]
            join_parents = self._get_join_parents(table_name)

            domains: dict[str, DomainState] = {}
            for col in table_spec.columns:
                domains[col.name] = self._resolve_domain(
                    col.name, col.data_type, table_name,
                )

            parent_key_samples: dict[str, list[Any]] = {}
            for local_col, (parent_table, parent_col) in join_parents.items():
                parent_rows = data.get(parent_table, [])
                if parent_rows:
                    parent_keys = [r[parent_col] for r in parent_rows if parent_col in r]
                else:
                    parent_keys = list(range(1, rows_per_table + 1))

                jt = self._get_join_type_for(table_name, local_col)
                miss = 0.1 if jt in ("left", "left_outer", "left_anti") else 0.0
                parent_key_samples[local_col] = self._sample_key_pareto(
                    parent_keys, rows_per_table, miss_rate=miss,
                )

            rows: list[dict] = []
            for i in range(rows_per_table):
                row: dict[str, Any] = {}
                for col in table_spec.columns:
                    if col.name in parent_key_samples:
                        row[col.name] = parent_key_samples[col.name][i]
                    else:
                        row[col.name] = self._generate_value_with_domain(
                            col, domains.get(col.name),
                        )
                rows.append(row)

            data[table_name] = rows

        self._generated_data = data
        return data

    # ------------------------------------------------------------------
    # Strategy: CONSTRAINT_FIRST  (domain solver, no DAG)
    # ------------------------------------------------------------------

    def _generate_constraint_first(
        self, rows_per_table: int, branch: str | None,
    ) -> dict[str, list[dict]]:
        """Domain solver on every column; tables generated independently.
        Join keys use a flat shared pool for basic overlap."""
        shared_pool = list(range(1, rows_per_table + 50))
        data: dict[str, list[dict]] = {}

        for table_name, table_spec in self._tables.items():
            domains: dict[str, DomainState] = {}
            for col in table_spec.columns:
                domains[col.name] = self._resolve_domain(
                    col.name, col.data_type, table_name,
                )

            rows: list[dict] = []
            for i in range(rows_per_table):
                row: dict[str, Any] = {}
                for col in table_spec.columns:
                    if col.is_join_key:
                        row[col.name] = shared_pool[i % len(shared_pool)]
                    else:
                        row[col.name] = self._generate_value_with_domain(
                            col, domains.get(col.name),
                        )
                rows.append(row)
            data[table_name] = rows

        self._generated_data = data
        return data

    # ------------------------------------------------------------------
    # Strategy: BRANCH_COVERAGE  (pairwise + DAG + domain solver)
    # ------------------------------------------------------------------

    def _generate_branch_coverage(self, rows_per_table: int) -> dict[str, list[dict]]:
        """Pairwise covering array for constrained columns, then fill
        remaining rows with domain-constrained random values."""
        dag = self._build_dependency_dag()
        order = self._topological_sort(dag)

        table_order = [t for t in order if t in self._tables]
        for t in self._tables:
            if t not in table_order:
                table_order.append(t)

        all_branch_values: dict[str, list[Any]] = {}
        for col, value_lists in self._branch_values.items():
            merged: list[Any] = []
            for vals in value_lists:
                for v in vals:
                    if v not in merged:
                        merged.append(v)
            if merged:
                all_branch_values[col] = merged

        data: dict[str, list[dict]] = {}

        for table_name in table_order:
            table_spec = self._tables[table_name]
            join_parents = self._get_join_parents(table_name)

            domains: dict[str, DomainState] = {}
            for col in table_spec.columns:
                domains[col.name] = self._resolve_domain(
                    col.name, col.data_type, table_name,
                )

            table_constrained = {
                c: all_branch_values[c]
                for c in all_branch_values
                if any(col.name == c for col in table_spec.columns)
            }
            pairwise_rows = (
                self._generate_pairwise_rows(table_constrained)
                if len(table_constrained) >= 2
                else []
            )

            needed = max(rows_per_table, len(pairwise_rows))
            parent_key_samples: dict[str, list[Any]] = {}
            for local_col, (parent_table, parent_col) in join_parents.items():
                parent_rows = data.get(parent_table, [])
                if parent_rows:
                    parent_keys = [r[parent_col] for r in parent_rows if parent_col in r]
                else:
                    parent_keys = list(range(1, rows_per_table + 1))
                jt = self._get_join_type_for(table_name, local_col)
                miss = 0.1 if jt in ("left", "left_outer", "left_anti") else 0.0
                parent_key_samples[local_col] = self._sample_key_pareto(
                    parent_keys, needed, miss_rate=miss,
                )

            rows: list[dict] = []
            for idx, pw_row in enumerate(pairwise_rows):
                row: dict[str, Any] = {}
                for col in table_spec.columns:
                    if col.name in pw_row:
                        row[col.name] = pw_row[col.name]
                    elif col.name in parent_key_samples:
                        row[col.name] = parent_key_samples[col.name][
                            idx % len(parent_key_samples[col.name])
                        ]
                    else:
                        row[col.name] = self._generate_value_with_domain(
                            col, domains.get(col.name),
                        )
                rows.append(row)

            remaining = max(0, rows_per_table - len(rows))
            offset = len(rows)
            for i in range(remaining):
                row = {}
                for col in table_spec.columns:
                    if col.name in parent_key_samples:
                        idx = (offset + i) % len(parent_key_samples[col.name])
                        row[col.name] = parent_key_samples[col.name][idx]
                    else:
                        row[col.name] = self._generate_value_with_domain(
                            col, domains.get(col.name),
                        )
                rows.append(row)

            data[table_name] = rows

        self._generated_data = data
        return data

    # ------------------------------------------------------------------
    # Value generation
    # ------------------------------------------------------------------

    def _generate_value_with_domain(
        self, col: ColumnSpec, domain: DomainState | None,
    ) -> Any:
        """Generate a single value for *col* respecting *domain*."""
        if domain is None:
            domain = DomainState()

        if domain.null_probability > 0 and random.random() < domain.null_probability:
            return None

        if domain.fixed_value is not None:
            return domain.fixed_value

        if domain.enum_values is not None and domain.enum_values:
            return random.choice(domain.enum_values)

        z3_val = self._get_z3_value(col.name)
        if z3_val is not None and random.random() < 0.5:
            return z3_val

        if col.default_value and not str(col.default_value).startswith("runtime:"):
            if random.random() < 0.3:
                return col.default_value

        return self._generate_typed_value(col.data_type, col.name, domain)

    def _generate_value(self, col: ColumnSpec) -> Any:
        """Legacy entry point -- builds a DomainState on the fly."""
        domain = self._resolve_domain(col.name, col.data_type)
        return self._generate_value_with_domain(col, domain)

    def _generate_typed_value(
        self,
        data_type: str,
        col_name: str = "",
        domain: DomainState | None = None,
    ) -> Any:
        """Generate a value of the given type within *domain* bounds."""
        dt = data_type.upper() if data_type else "UNKNOWN"
        d = domain or DomainState()

        if dt == "UNKNOWN":
            name_lower = col_name.lower()
            if "_id" in name_lower or name_lower.endswith("id"):
                dt = "INT"
            elif "_dt" in name_lower or "date" in name_lower:
                dt = "DATE"
            elif "_amt" in name_lower or "amount" in name_lower or "price" in name_lower:
                dt = "DECIMAL"
            elif "email" in name_lower:
                dt = "EMAIL"
            elif "name" in name_lower:
                dt = "STRING"
            elif "country" in name_lower:
                dt = "COUNTRY"

        if dt in ("INT", "INTEGER", "LONG", "BIGINT", "SHORT", "SMALLINT"):
            lo = int(d.low) if d.low is not None else 1
            hi = int(d.high) if d.high is not None else 10000
            if lo > hi:
                lo, hi = hi, lo
            if lo == hi:
                return lo
            return random.randint(lo, hi)

        if dt in ("DECIMAL", "DOUBLE", "FLOAT", "NUMBER", "NUMERIC"):
            lo = d.low if d.low is not None else 0.01
            hi = d.high if d.high is not None else 9999.99
            if lo > hi:
                lo, hi = hi, lo
            if lo == hi:
                return round(lo, 2)
            return round(random.uniform(lo, hi), 2)

        if dt == "STRING":
            return "".join(random.choices(string.ascii_lowercase, k=8))

        if dt == "DATE":
            base = self._reference_timestamp
            lo_days = _bound_to_day_offset(d.low, base, default=-365)
            hi_days = _bound_to_day_offset(d.high, base, default=0)
            if lo_days > hi_days:
                lo_days, hi_days = hi_days, lo_days
            offset = random.randint(lo_days, hi_days) if lo_days != hi_days else lo_days
            dt_val = base + timedelta(days=offset)
            return dt_val.strftime("%Y-%m-%d")

        if dt == "BOOLEAN":
            return random.choice([True, False])

        if dt == "TIMESTAMP":
            base = self._reference_timestamp
            lo_sec = _bound_to_sec_offset(d.low, base, default=-86400 * 30)
            hi_sec = _bound_to_sec_offset(d.high, base, default=0)
            if lo_sec > hi_sec:
                lo_sec, hi_sec = hi_sec, lo_sec
            offset = random.randint(lo_sec, hi_sec) if lo_sec != hi_sec else lo_sec
            ts_val = base + timedelta(seconds=offset)
            return ts_val.strftime("%Y-%m-%d %H:%M:%S")

        if dt == "EMAIL":
            user = ''.join(random.choices(string.ascii_lowercase, k=6))
            return f"{user}@example.com"

        if dt == "COUNTRY":
            return random.choice(["US", "MX", "BR", "DE", "FR", "ES", "IT", "UK"])

        return "".join(random.choices(string.ascii_lowercase, k=6))

    # ------------------------------------------------------------------
    # Z3 Hybrid Solver (Phase 2.1)
    # ------------------------------------------------------------------

    def _resolve_z3_templates(self) -> None:
        """Dispatch Z3 for constraint sets that need symbolic solving.

        Groups constraints by column, checks each group with the
        Z3Dispatcher, and generates templates for qualifying sets.
        Templates are stored in ``self._z3_templates[column_name]``.
        """
        from synthetic_data.logic_solver import (
            Z3Dispatcher, Z3TemplateSolver, is_available,
        )

        if not is_available() or not self._constraints:
            return

        has_contradictions = bool(self._detect_constraint_contradictions())

        decision = Z3Dispatcher.should_use_z3(
            self._constraints,
            has_contradictions=has_contradictions,
        )

        if not decision.use_z3:
            return

        self._z3_results.append({
            "reason": decision.reason,
            "cross_column_count": len(decision.cross_column_constraints),
        })

        from collections import defaultdict
        by_source: dict[str, list[dict]] = defaultdict(list)
        for c in self._constraints:
            src = c.get("source_transformation", "_global")
            by_source[src].append(c)

        solver = Z3TemplateSolver(max_templates=50, timeout_ms=5000)

        for src, group in by_source.items():
            numeric_group = [
                c for c in group
                if c.get("constraint_type") in (
                    "gt", "gte", "lt", "lte", "equals", "not_equals",
                    "between", "in", "enum",
                )
            ]
            if not numeric_group:
                continue

            result = solver.solve(numeric_group)

            self._z3_results.append({
                "source": src,
                "status": result.status,
                "templates": len(result.templates),
                "skipped": result.skipped_constraints,
                "time_ms": result.z3_time_ms,
            })

            if result.status == "unsat":
                cols = result.unsat_core_columns
                col_slug = "_".join(cols[:3])
                self._verification_gaps.append({
                    "gap_id": f"GAP_STR_Z3_{src}_{col_slug}",
                    "category": "structural",
                    "sub_type": "z3_unsat",
                    "severity": "high",
                    "subject": f"Transformation {src}: columns {cols}",
                    "detail": (
                        f"Z3 proved constraints on columns {cols} in {src} "
                        f"are unsatisfiable -- this filter can never produce results"
                    ),
                    "suggestion": (
                        "Check if the filter logic has dead code or "
                        "contradictory conditions.  If the constraints "
                        "come from different branches (IF/ELSE), the parser "
                        "may have flattened mutually exclusive conditions."
                    ),
                    "location": {
                        "asg_node_id": src,
                    },
                    "ai_hints": {
                        "action": "review_dead_code",
                        "source_transformation": src,
                        "columns": cols,
                        "z3_status": "unsat",
                        "unsat_core": cols,
                    },
                })

            if result.templates:
                for template in result.templates:
                    for col, val in template.items():
                        if col not in self._z3_templates:
                            self._z3_templates[col] = []
                        self._z3_templates[col].append(val)

    def _get_z3_value(self, col_name: str) -> Any | None:
        """Pick a value from Z3 templates for *col_name*, if available."""
        pool = self._z3_templates.get(col_name)
        if pool:
            return random.choice(pool)
        return None

    def get_z3_summary(self) -> dict:
        """Return Z3 solver statistics after generation."""
        from synthetic_data.logic_solver import is_available
        return {
            "z3_available": is_available(),
            "dispatch_results": getattr(self, "_z3_results", []),
            "columns_with_templates": list(getattr(self, "_z3_templates", {}).keys()),
            "total_template_values": sum(
                len(v) for v in getattr(self, "_z3_templates", {}).values()
            ),
        }

    # ------------------------------------------------------------------
    # Adversarial Noise (Phase 2.2)
    # ------------------------------------------------------------------

    def _apply_noise(self, data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        """Post-process generated data with adversarial noise injection."""
        noised: dict[str, list[dict]] = {}
        for table_name, rows in data.items():
            table_spec = self._tables.get(table_name)
            if not table_spec:
                noised[table_name] = rows
                continue

            col_types = {c.name: c.data_type for c in table_spec.columns}
            domains = {
                c.name: self._resolve_domain(c.name, c.data_type, table_name)
                for c in table_spec.columns
            }

            noised_rows = []
            for row in rows:
                noised_rows.append(
                    self._noise_injector.inject_row(
                        row, col_types, table_name, domains,
                    )
                )
            noised[table_name] = noised_rows

        return noised

    # ------------------------------------------------------------------
    # Verification Gaps (Phase 2 -- gap integration)
    # ------------------------------------------------------------------

    def get_verification_gaps(self) -> list[dict]:
        """Return verification gaps discovered during generation.

        These are findings that only surface when the ASG metadata
        is executed (DAG built, constraints intersected), not just read.
        Called by the pipeline after ``generate()`` to merge into gaps.json.

        Returns gap dicts compatible with ``GapItem`` construction.
        """
        gaps = list(getattr(self, "_verification_gaps", []))

        for gap in self._detect_constraint_contradictions():
            gaps.append(gap)

        return gaps

    def _detect_constraint_contradictions(self) -> list[dict]:
        """Find columns where constraints produce an empty domain."""
        gaps: list[dict] = []

        for table_name, table_spec in self._tables.items():
            for col in table_spec.columns:
                domain = self._resolve_domain(col.name, col.data_type, table_name)

                if domain.low is not None and domain.high is not None and domain.low > domain.high:
                    sources = [
                        {
                            "type": c.get("constraint_type"),
                            "value": c.get("value"),
                            "source": c.get("source_transformation", ""),
                        }
                        for c in self._constraints
                        if c.get("column_name") == col.name
                        and c.get("constraint_type") in ("gt", "gte", "lt", "lte", "between")
                    ]
                    gaps.append({
                        "gap_id": f"GAP_STR_LOGIC_{table_name}_{col.name}",
                        "category": "structural",
                        "sub_type": "constraint_contradiction",
                        "severity": "high",
                        "subject": f"{table_name}.{col.name}",
                        "detail": (
                            f"Column '{col.name}' has contradictory constraints: "
                            f"low={domain.low} > high={domain.high} yields an empty domain"
                        ),
                        "suggestion": (
                            "Review if there is overlapping branch logic "
                            "(IF/ELSE) or if the parser merged mutually "
                            "exclusive filter conditions."
                        ),
                        "location": {
                            "scope": table_name,
                        },
                        "ai_hints": {
                            "action": "review_filter_logic",
                            "table": table_name,
                            "column": col.name,
                            "resolved_low": domain.low,
                            "resolved_high": domain.high,
                            "constraints": sources,
                        },
                    })

                if (
                    domain.fixed_value is not None
                    and domain.enum_values is not None
                    and domain.fixed_value not in domain.enum_values
                ):
                    gaps.append({
                        "gap_id": f"GAP_STR_LOGIC_{table_name}_{col.name}_enum",
                        "category": "structural",
                        "sub_type": "constraint_contradiction",
                        "severity": "high",
                        "subject": f"{table_name}.{col.name}",
                        "detail": (
                            f"Column '{col.name}' has equals={domain.fixed_value!r} "
                            f"but that value is not in the IN list {domain.enum_values!r}"
                        ),
                        "suggestion": (
                            "Check if the equals and IN constraints target "
                            "different branches.  This may indicate the "
                            "parser flattened mutually exclusive conditions."
                        ),
                        "location": {
                            "scope": table_name,
                        },
                        "ai_hints": {
                            "action": "review_filter_logic",
                            "table": table_name,
                            "column": col.name,
                            "fixed_value": domain.fixed_value,
                            "enum_values": domain.enum_values,
                        },
                    })

        return gaps

    def get_noise_summary(self) -> dict:
        """Return adversarial injection statistics after generation."""
        injector = getattr(self, "_noise_injector", None)
        if injector is None:
            return {"total_injections": 0}
        return injector.get_summary()


    # ------------------------------------------------------------------
    # Validator Loop (Phase 3 -- The Oracle)
    # ------------------------------------------------------------------

    def validate(
        self,
        transformations: list[dict],
        data_out: list[dict] | None = None,
    ) -> dict:
        """Trace generated rows through the ASG transformation graph.

        Produces expected output values for automated DVP.  Call after
        ``generate()`` -- uses ``self._generated_data`` as input.

        Returns a dict with ``tables`` (expected rows per sink) and
        ``coverage`` (transformation visit statistics).
        """
        from synthetic_data.validator import RowTracer

        if not self._generated_data:
            return {"tables": {}, "coverage": {}}

        tracer = RowTracer(
            transformations,
            data_out=data_out,
            reference_timestamp=self._reference_timestamp,
        )
        result = tracer.trace(
            self._generated_data,
            source_map=self._source_map,
        )

        self._validation_result = result
        return result.to_dict()

    def get_validation_summary(self) -> dict:
        """Return validation coverage summary after validate()."""
        result = getattr(self, "_validation_result", None)
        if result is None:
            return {"validated": False}
        return {
            "validated": True,
            "sink_coverage": result.coverage.sink_coverage,
            "visited": result.coverage.visited,
            "total_transformations": result.coverage.total_transformations,
            "filter_drops": len(result.coverage.filter_drops),
            "expected_output_tables": len(result.expected_outputs),
        }

    # ------------------------------------------------------------------
    # CSV output
    # ------------------------------------------------------------------

    def write_csv_files(
        self,
        output_dir: Path,
        strategy: GenerationStrategy = GenerationStrategy.JOIN_AWARE,
        rows_per_table: int = 20,
        noise_rate: float = 0.0,
        transformations: list[dict] | None = None,
        data_out: list[dict] | None = None,
    ) -> list[Path]:
        """Generate data and write CSV files.

        If *transformations* is provided, runs the Validator Loop and
        writes ``expected_results.json`` alongside the CSVs.
        """
        data = self.generate(strategy, rows_per_table, noise_rate=noise_rate)

        validation_result: dict | None = None
        if transformations:
            validation_result = self.validate(transformations, data_out)

        data_dir = output_dir / "synthetic_data"
        data_dir.mkdir(parents=True, exist_ok=True)

        metadata = {
            "reference_timestamp": self._reference_timestamp.isoformat(),
            "strategy": strategy.value,
            "rows_per_table": rows_per_table,
            "noise_rate": noise_rate,
            "noise_summary": self.get_noise_summary(),
            "verification_gaps": len(self.get_verification_gaps()),
            "z3_summary": self.get_z3_summary(),
            "validation_summary": self.get_validation_summary(),
            "tables": list(data.keys()),
        }
        meta_path = data_dir / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

        created_files: list[Path] = [meta_path]

        for table_name, rows in data.items():
            if not rows or not rows[0]:
                continue

            clean_name = self._sanitize_filename(table_name)
            filepath = data_dir / f"{clean_name}.csv"
            headers = list(rows[0].keys())

            with open(filepath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)

            created_files.append(filepath)

        if validation_result and validation_result.get("tables"):
            expected_path = data_dir / "expected_results.json"
            with open(expected_path, "w") as f:
                json.dump(validation_result, f, indent=2, default=str)
            created_files.append(expected_path)

        return created_files

    @staticmethod
    def _sanitize_filename(table_name: str) -> str:
        clean = table_name
        clean = _re.sub(r'^(s3|file|hdfs|gs):/+', '', clean)
        clean = clean.replace('/', '_').replace('\\', '_')
        clean = clean.replace('\n', '_').replace('\r', '_')
        clean = _re.sub(r'[<>:"|?*\s\n\r\t]', '_', clean)

        if 'select' in clean.lower() or len(clean) > 100:
            match = _re.search(r'[a-zA-Z_][a-zA-Z0-9_]{2,30}', clean)
            clean = match.group(0) if match else f"table_{hash(clean) % 10000}"

        clean = clean.strip('_.')
        for suffix in ('.csv', '.parquet'):
            if clean.endswith(suffix):
                clean = clean[: -len(suffix)]
        if len(clean) > 100:
            clean = clean[:100]
        return clean or "unknown_table"

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_files(
        cls,
        data_io_path: str | Path,
        asg_path: str | Path | None = None,
    ) -> "SyntheticDataGenerator":
        """Create generator from JSON files."""
        with open(data_io_path) as f:
            data_io = json.load(f)

        constraints = None
        relationships = None
        asg_data_in: list[dict] = []

        if asg_path:
            with open(asg_path) as f:
                asg = json.load(f)
            constraints = asg.get("column_constraints", [])
            relationships = asg.get("column_relationships", [])
            asg_data_in = asg.get("data_in", [])

        inst = cls(data_io, constraints, relationships)
        if asg_data_in:
            inst._asg_data_in = asg_data_in
            inst._build_source_map()
        return inst

    def get_analysis(self) -> dict:
        """Get analysis summary."""
        return {
            "tables": {
                name: [col.name for col in spec.columns]
                for name, spec in self._tables.items()
            },
            "constraints_count": len(self._constraints),
            "relationships_count": len(self._relationships),
            "join_keys": list(self._join_key_pools.keys()),
            "branches": self._branch_values,
        }


def _make_missing_key(existing: list[Any]) -> Any:
    """Create a key value guaranteed not to be in *existing*."""
    if not existing:
        return -1
    if all(isinstance(k, int) for k in existing):
        return max(existing) + random.randint(1000, 9999)
    return f"MISSING_{random.randint(1000, 9999)}"

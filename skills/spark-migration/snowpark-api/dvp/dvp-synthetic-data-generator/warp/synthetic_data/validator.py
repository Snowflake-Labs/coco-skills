"""
ASG Flow Simulator -- Validator Loop (Phase 3).

Traces synthetic rows through the ASG transformation graph to compute
expected output values, enabling automated Data Validation Process (DVP).

Architecture:
- ``ParameterResolver``: Infers mock values for unresolved runtime parameters
- ``ExpressionEvaluator``: Parses and evaluates ASG condition/expression strings
- ``StateMapper``: Applies transformation operations to row state
- ``RowTracer``: Orchestrates DAG traversal, feeding rows through chains
- ``CoverageTracker``: Records visit counts, filter drops, sink coverage
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


# -- Parameter Resolver ----------------------------------------------------

_FSTRING_VAR_RE = re.compile(r"\{(\w+)\}")
_PYSPARK_LIT_FSTR_RE = re.compile(
    r"""(?:F\.)?lit\(\s*f['"]([^'"]*\{[^}]+\}[^'"]*)['"]\s*\)""",
)
_ASSIGNMENT_RE = re.compile(
    r"""(?:val\s+)?(\w+)\s*=\s*['"]([^'"]+)['"]\s*$""",
    re.MULTILINE,
)
_KNOWN_KEYWORDS = frozenset({
    "col", "lit", "Seq", "true", "false", "null", "None",
    "AND", "OR", "NOT", "IN", "LIKE", "BETWEEN", "isin",
    "when", "otherwise", "between", "filter", "where",
})


class ParameterResolver:
    """Infers mock values for unresolved runtime parameters.

    Uses a four-tier hierarchy (highest confidence first):

    0. **Constant propagation** -- literal assignments found in the ASG.
    1. **Contextual sniffing** -- format strings in ``strptime``,
       ``to_date``, or ``substring`` patterns.
    2. **Relational sniffing** -- the column being compared hints at
       the parameter type.
    3. **Naming heuristics** -- suffix conventions like ``_YM``, ``_dt``.
    """

    _FORMAT_MOCKS: dict[str, str] = {
        "%Y%m": "{ym}",
        "%Y-%m-%d": "{ymd}",
        "%Y-%m": "{ym_dash}",
        "yyyyMMdd": "{ymd_compact}",
        "yyyyMM": "{ym}",
        "yyyy-MM-dd": "{ymd}",
        "yyyy-MM": "{ym_dash}",
    }

    def __init__(
        self,
        reference_timestamp: datetime | None = None,
        known_constants: dict[str, str] | None = None,
    ):
        self._ref = reference_timestamp or datetime.now()
        self._constants = known_constants or {}

    def resolve(
        self,
        var_name: str,
        expression_context: str = "",
    ) -> str | None:
        """Return a value for *var_name* or ``None`` if unresolvable."""
        result = self._propagate_constant(var_name)
        if result is not None:
            return result
        result = self._sniff_context(var_name, expression_context)
        if result is not None:
            return result
        result = self._sniff_relational(expression_context)
        if result is not None:
            return result
        return self._infer_by_name(var_name)

    def _propagate_constant(self, var_name: str) -> str | None:
        return self._constants.get(var_name)

    def _sniff_context(self, var_name: str, ctx: str) -> str | None:
        for fmt, tpl in self._FORMAT_MOCKS.items():
            if fmt in ctx:
                return self._render(tpl)
        if "substring(0,4)" in ctx or "substring(0, 4)" in ctx:
            return self._render("{ym_dash}")
        return None

    def _sniff_relational(self, ctx: str) -> str | None:
        cols_in_ctx = re.findall(r'col\(["\'](\w+)["\']\)', ctx)
        for col_name in cols_in_ctx:
            inferred = self._infer_by_name(col_name)
            if inferred is not None:
                return inferred
        return None

    def _infer_by_name(self, name: str) -> str | None:
        low = name.lower()
        if low.endswith("_ym") or "year_month" in low or low == "income_ym":
            return self._render("{ym}")
        if (
            low.endswith("_dt") or low.endswith("_date")
            or low.startswith("date") or "_date" in low
        ):
            return self._render("{ymd}")
        if "round" in low:
            return f"{self._ref.year} R1"
        if low.endswith("_cnt") or low == "cnt" or "count" in low:
            return "100"
        if "period" in low:
            return self._render("{ym}")
        if "start" in low:
            return self._render("{ymd_compact}")
        return None

    def _render(self, tpl: str) -> str:
        ref = self._ref
        return (
            tpl.replace("{ym}", ref.strftime("%Y%m"))
            .replace("{ym_dash}", ref.strftime("%Y-%m"))
            .replace("{ymd}", ref.strftime("%Y-%m-%d"))
            .replace("{ymd_compact}", ref.strftime("%Y%m%d"))
        )

    def resolve_in_condition(self, condition: str) -> str:
        """Substitute all template variables in *condition*."""
        resolved = condition

        for m in _PYSPARK_LIT_FSTR_RE.finditer(condition):
            inner = m.group(1)
            vars_found = _FSTRING_VAR_RE.findall(inner)
            replacement = inner
            for var in vars_found:
                mock = self.resolve(var, condition)
                if mock is not None:
                    replacement = replacement.replace(f"{{{var}}}", mock)
            resolved = resolved.replace(m.group(1), replacement)

        remaining = _FSTRING_VAR_RE.findall(resolved)
        for var in remaining:
            if var not in _KNOWN_KEYWORDS:
                mock = self.resolve(var, condition)
                if mock is not None:
                    resolved = resolved.replace(f"{{{var}}}", mock)

        tokens = re.findall(
            r"(?<![\"'(\w])([A-Z][A-Za-z_]\w*|[a-z]+[A-Z]\w*)(?![\"')\w(])",
            resolved,
        )
        for tok in tokens:
            if tok in _KNOWN_KEYWORDS or len(tok) <= 2:
                continue
            if re.search(
                rf'(?:col|lit|F\.lit|Seq)\s*\(\s*["\']?{re.escape(tok)}',
                resolved,
            ):
                continue
            mock = self.resolve(tok, condition)
            if mock is not None:
                resolved = re.sub(
                    rf"(?<![\"'\w]){re.escape(tok)}(?![\"'\w(])",
                    f'"{mock}"',
                    resolved,
                )

        return resolved

    @staticmethod
    def scan_constants(transformations: list[dict]) -> dict[str, str]:
        """Scan ASG transformations for literal variable assignments."""
        constants: dict[str, str] = {}
        for tx in transformations:
            params = tx.get("parameters", {})
            expr = params.get("expression", "")
            cond = params.get("condition", "")
            for text in (expr, cond):
                if not text:
                    continue
                for m in _ASSIGNMENT_RE.finditer(text):
                    constants[m.group(1)] = m.group(2)
        return constants


# -- Expression Evaluator --------------------------------------------------

_COL_RE = re.compile(r'(?:F\.)?col\(["\'](\w+)["\']\)')
_LIT_RE = re.compile(r'(?:F\.)?lit\((?:f["\'])?([^)]+)\)')
_ISIN_RE = re.compile(r'(?:F\.)?col\(["\'](\w+)["\']\)\.isin\((?:Seq\()?([^)]+)\)?\)')

_CMP_OPS = {
    "===": "==", "==": "==", "=!=": "!=", "!=": "!=",
    ">=": ">=", "<=": "<=", ">": ">", "<": "<",
}


class ExpressionEvaluator:
    """Evaluates ASG expression strings against a row dict.

    Handles filter conditions, arithmetic, literal assignments,
    and when/otherwise chains.  Returns ``None`` for expressions
    it cannot parse (graceful degradation -- the row passes through).

    If a ``ParameterResolver`` is provided, unresolved runtime
    parameters (f-string templates, bare Scala identifiers) are
    substituted before evaluation.
    """

    def __init__(self, resolver: ParameterResolver | None = None):
        self._resolver = resolver

    @staticmethod
    def _normalize_pyspark(cond: str) -> str:
        """Normalize PySpark-specific syntax for evaluation.

        Converts ``F.col(...)`` to ``col(...)`` and ``F.lit(f'...')``
        or ``F.lit('...')`` to ``lit("...")``.
        """
        cond = re.sub(
            r"""(?:F\.)?lit\(f['"]([^'"]*)['"]?\)""",
            lambda m: 'lit("' + m.group(1) + '")',
            cond,
        )
        cond = re.sub(r"F\.lit\(", "lit(", cond)
        cond = re.sub(r"F\.col\(", "col(", cond)
        return cond

    def evaluate_condition(self, condition: str, row: dict[str, Any]) -> bool | None:
        """Evaluate a filter condition against *row*.

        Returns ``True`` if the row passes, ``False`` if filtered out,
        or ``None`` if the condition cannot be parsed.
        """
        if not condition:
            return None

        if self._resolver:
            condition = self._resolver.resolve_in_condition(condition)

        condition = self._normalize_pyspark(condition)
        return self._eval_core(condition, row)

    def _eval_core(self, condition: str, row: dict[str, Any]) -> bool | None:
        """Core evaluator (no resolver/normalizer -- already applied)."""
        cond = condition.strip()
        if cond.startswith("(") and cond.endswith(")"):
            # Only strip if the parens are a true outer wrapper (depth stays > 0)
            depth = 0
            matched = True
            for i, ch in enumerate(cond[1:-1]):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth < 0:
                        matched = False
                        break
            if matched:
                cond = cond[1:-1].strip()

        if " | " in cond or " || " in cond:
            return self._eval_or(cond, row)
        if " & " in cond or " && " in cond:
            return self._eval_and(cond, row)

        isin = _ISIN_RE.search(condition)
        if isin:
            return self._eval_isin(isin, row)

        for op_str, op_name in _CMP_OPS.items():
            if op_str in cond:
                return self._eval_comparison(cond, op_str, op_name, row)

        return None

    def evaluate_expression(self, expression: str, row: dict[str, Any]) -> Any:
        """Evaluate a withColumn expression and return the computed value.

        Returns ``None`` if the expression cannot be evaluated.
        """
        if not expression:
            return None

        expr = expression.strip()
        if expr.startswith("(") and "," in expr:
            _, _, expr = expr.partition(",")
            expr = expr.strip()
            if expr.endswith(")"):
                expr = expr[:-1].strip()

        if expr.startswith("when(") or expr.startswith("\n") and "when(" in expr:
            return self._eval_when_chain(expr, row)

        lit = _LIT_RE.fullmatch(expr)
        if lit:
            return self._parse_literal(lit.group(1).strip())

        col = _COL_RE.fullmatch(expr)
        if col:
            return row.get(col.group(1))

        for op in ("+", "-", "*", "/"):
            if op in expr and "col(" in expr:
                return self._eval_arithmetic(expr, row, op)

        return None

    def _eval_or(self, cond: str, row: dict[str, Any]) -> bool | None:
        parts = re.split(r'\s*\|\|?\s*', cond)
        results = []
        for p in parts:
            p = p.strip()
            if p.startswith("(") and p.endswith(")"):
                p = p[1:-1].strip()
            r = self._eval_core(p, row)
            if r is None:
                return None
            results.append(r)
        return any(results)

    def _eval_and(self, cond: str, row: dict[str, Any]) -> bool | None:
        parts = re.split(r'\s*&&?\s*', cond)
        results = []
        for p in parts:
            p = p.strip()
            if p.startswith("(") and p.endswith(")"):
                p = p[1:-1].strip()
            r = self._eval_core(p, row)
            if r is None:
                return None
            results.append(r)
        return all(results)

    def _eval_isin(self, match: re.Match, row: dict[str, Any]) -> bool | None:
        col_name = match.group(1)
        vals_str = match.group(2)
        row_val = row.get(col_name)
        if row_val is None:
            return False
        vals = [self._parse_literal(v.strip()) for v in vals_str.split(",")]
        return row_val in vals

    def _eval_comparison(
        self, cond: str, op_str: str, op_name: str, row: dict[str, Any],
    ) -> bool | None:
        parts = cond.split(op_str, 1)
        if len(parts) != 2:
            return None
        lhs_raw, rhs_raw = parts[0].strip(), parts[1].strip()

        lhs = self._resolve_value(lhs_raw, row)
        rhs = self._resolve_value(rhs_raw, row)

        if lhs is None or rhs is None:
            return None

        try:
            lhs_d, rhs_d = self._to_decimal(lhs), self._to_decimal(rhs)
            if lhs_d is not None and rhs_d is not None:
                lhs, rhs = lhs_d, rhs_d
        except (InvalidOperation, ValueError, TypeError):
            pass

        try:
            if op_name == "==":
                return lhs == rhs
            if op_name == "!=":
                return lhs != rhs
            if op_name == ">=":
                return lhs >= rhs
            if op_name == "<=":
                return lhs <= rhs
            if op_name == ">":
                return lhs > rhs
            if op_name == "<":
                return lhs < rhs
        except TypeError:
            return None
        return None

    def _eval_when_chain(self, expr: str, row: dict[str, Any]) -> Any:
        flat = " ".join(expr.split())

        when_blocks = self._extract_when_blocks(flat)
        for cond_str, val_str in when_blocks:
            result = self.evaluate_condition(cond_str.strip(), row)
            if result is True:
                return self._resolve_value(val_str.strip(), row)

        otherwise_match = re.search(r'\.otherwise\(', flat)
        if otherwise_match:
            start = otherwise_match.end()
            val_str = self._extract_balanced(flat, start)
            if val_str is not None:
                return self._resolve_value(val_str.strip(), row)

        return None

    @staticmethod
    def _extract_balanced(text: str, start: int) -> str | None:
        """Extract content from *start* until the matching close paren."""
        depth = 0
        i = start
        begin = start
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                if depth == 0:
                    return text[begin:i]
                depth -= 1
            i += 1
        return text[begin:]

    @staticmethod
    def _extract_when_blocks(flat: str) -> list[tuple[str, str]]:
        """Parse when(cond, value) blocks handling nested parentheses."""
        blocks = []
        idx = 0
        while idx < len(flat):
            pos = flat.find("when(", idx)
            if pos == -1:
                break
            start = pos + 5
            depth = 0
            comma_pos = -1
            i = start
            end_pos = -1
            while i < len(flat):
                ch = flat[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        end_pos = i
                        break
                    depth -= 1
                elif ch == "," and depth == 0 and comma_pos == -1:
                    comma_pos = i
                i += 1
            if comma_pos != -1 and end_pos != -1:
                cond = flat[start:comma_pos]
                val = flat[comma_pos + 1:end_pos]
                blocks.append((cond, val))
            idx = end_pos + 1 if end_pos != -1 else len(flat)
        return blocks

    def _eval_arithmetic(
        self, expr: str, row: dict[str, Any], op: str,
    ) -> Any:
        cols = _COL_RE.findall(expr)
        if not cols:
            return None

        result_expr = expr
        for col_name in cols:
            val = row.get(col_name)
            if val is None:
                return None
            result_expr = result_expr.replace(
                f'col("{col_name}")', str(val),
            ).replace(
                f"col('{col_name}')", str(val),
            )

        lits = _LIT_RE.findall(result_expr)
        for lit_val in lits:
            parsed = self._parse_literal(lit_val.strip())
            result_expr = result_expr.replace(f"lit({lit_val})", str(parsed))

        clean = re.sub(r'[^0-9+\-*/.()\s]', '', result_expr).strip()
        if not clean:
            return None
        try:
            return float(eval(clean))  # noqa: S307
        except Exception:
            return None

    def _resolve_value(self, raw: str, row: dict[str, Any]) -> Any:
        raw = raw.strip()
        col = _COL_RE.match(raw)
        if col:
            return row.get(col.group(1))
        lit = _LIT_RE.match(raw)
        if lit:
            return self._parse_literal(lit.group(1).strip())
        return self._parse_literal(raw)

    @staticmethod
    def _parse_literal(val: str) -> Any:
        val = val.strip()
        if val.startswith('"') and val.endswith('"'):
            return val[1:-1]
        if val.startswith("'") and val.endswith("'"):
            return val[1:-1]
        if val == "true":
            return True
        if val == "false":
            return False
        if val == "null":
            return None
        try:
            if "." in val:
                return float(val)
            return int(val)
        except (ValueError, TypeError):
            return val

    @staticmethod
    def _to_decimal(val: Any) -> Decimal | None:
        if isinstance(val, Decimal):
            return val
        if isinstance(val, (int, float)):
            return Decimal(str(val))
        if isinstance(val, str):
            try:
                return Decimal(val)
            except InvalidOperation:
                return None
        return None


# -- State Mapper ----------------------------------------------------------

class StateMapper:
    """Applies a single transformation operation to a set of row dicts."""

    def __init__(self, evaluator: ExpressionEvaluator | None = None):
        self._eval = evaluator or ExpressionEvaluator()

    def apply(
        self, operation: str, params: dict, rows: list[dict[str, Any]],
        secondary_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply *operation* with *params* to *rows*.

        For joins, *secondary_rows* provides the right-side input.
        Returns the resulting rows (may be fewer, more, or transformed).
        """
        handler = getattr(self, f"_op_{operation}", None)
        if handler:
            return handler(params, rows, secondary_rows)
        return list(rows)

    def _op_filter(
        self, params: dict, rows: list[dict], _sec: Any,
    ) -> list[dict]:
        cond = params.get("condition", "")
        result = []
        for row in rows:
            passed = self._eval.evaluate_condition(cond, row)
            if passed is None or passed is True:
                result.append(row)
        return result

    _op_where = _op_filter

    def _op_select(
        self, params: dict, rows: list[dict], _sec: Any,
    ) -> list[dict]:
        cols = params.get("columns", [])
        if not cols:
            return list(rows)
        return [{c: row.get(c) for c in cols} for row in rows]

    def _op_drop(
        self, params: dict, rows: list[dict], _sec: Any,
    ) -> list[dict]:
        drop_cols = set(params.get("columns", []))
        if not drop_cols:
            return list(rows)
        return [
            {k: v for k, v in row.items() if k not in drop_cols}
            for row in rows
        ]

    def _op_withColumn(
        self, params: dict, rows: list[dict], _sec: Any,
    ) -> list[dict]:
        col_name = params.get("column_name", "")
        expression = params.get("expression", "")
        if not col_name:
            return list(rows)
        result = []
        for row in rows:
            new_row = dict(row)
            val = self._eval.evaluate_expression(expression, row)
            if val is not None:
                new_row[col_name] = val
            elif col_name not in new_row:
                new_row[col_name] = None
            result.append(new_row)
        return result

    def _op_withColumnRenamed(
        self, params: dict, rows: list[dict], _sec: Any,
    ) -> list[dict]:
        old_name = params.get("old_name", params.get("existing", ""))
        new_name = params.get("new_name", params.get("column_name", ""))
        if not old_name or not new_name:
            return list(rows)
        result = []
        for row in rows:
            new_row = {}
            for k, v in row.items():
                new_row[new_name if k == old_name else k] = v
            result.append(new_row)
        return result

    def _op_join(
        self, params: dict, rows: list[dict],
        secondary_rows: list[dict] | None,
    ) -> list[dict]:
        join_keys = params.get("join_condition", [])
        join_type = params.get("join_type", "inner").lower()

        if not secondary_rows or not join_keys:
            return list(rows)

        right_index: dict[tuple, list[dict]] = {}
        for r in secondary_rows:
            key = tuple(r.get(k) for k in join_keys)
            right_index.setdefault(key, []).append(r)

        result = []
        for left in rows:
            key = tuple(left.get(k) for k in join_keys)
            matches = right_index.get(key, [])
            if matches:
                for right in matches:
                    merged = dict(left)
                    for rk, rv in right.items():
                        if rk not in merged:
                            merged[rk] = rv
                    result.append(merged)
            elif join_type in ("left", "left_outer", "leftouter"):
                result.append(dict(left))

        return result

    def _op_distinct(
        self, params: dict, rows: list[dict], _sec: Any,
    ) -> list[dict]:
        seen: set[tuple] = set()
        result = []
        for row in rows:
            key = tuple(sorted(row.items()))
            if key not in seen:
                seen.add(key)
                result.append(row)
        return result

    def _op_union(
        self, params: dict, rows: list[dict],
        secondary_rows: list[dict] | None,
    ) -> list[dict]:
        result = list(rows)
        if secondary_rows:
            result.extend(secondary_rows)
        return result

    _op_unionByName = _op_union


# -- Coverage Tracker ------------------------------------------------------

@dataclass
class FilterDrop:
    tx_id: str
    condition: str
    rows_dropped: int


@dataclass
class CoverageReport:
    total_transformations: int = 0
    visited: int = 0
    fully_evaluated: int = 0
    pass_through: int = 0
    sink_nodes_reached: int = 0
    total_sink_nodes: int = 0
    filter_drops: list[FilterDrop] = field(default_factory=list)

    @property
    def sink_coverage(self) -> float:
        if self.total_sink_nodes == 0:
            return 0.0
        return round(self.sink_nodes_reached / self.total_sink_nodes, 4)

    def to_dict(self) -> dict:
        return {
            "total_transformations": self.total_transformations,
            "visited": self.visited,
            "fully_evaluated": self.fully_evaluated,
            "pass_through": self.pass_through,
            "sink_coverage": self.sink_coverage,
            "filter_drop_points": [
                {"tx_id": d.tx_id, "condition": d.condition,
                 "rows_dropped": d.rows_dropped}
                for d in self.filter_drops
            ],
        }


class CoverageTracker:
    """Tracks which transformation nodes were visited and row outcomes."""

    def __init__(self) -> None:
        self._visited: set[str] = set()
        self._evaluated: set[str] = set()
        self._pass_through: set[str] = set()
        self._filter_drops: list[FilterDrop] = []
        self._sinks_reached: set[str] = set()

    def record_visit(self, tx_id: str, evaluated: bool = True) -> None:
        self._visited.add(tx_id)
        if evaluated:
            self._evaluated.add(tx_id)
        else:
            self._pass_through.add(tx_id)

    def record_filter_drop(
        self, tx_id: str, condition: str, dropped: int,
    ) -> None:
        if dropped > 0:
            self._filter_drops.append(FilterDrop(tx_id, condition, dropped))

    def record_sink_reached(self, tx_id: str) -> None:
        self._sinks_reached.add(tx_id)

    def build_report(
        self, total_txs: int, total_sinks: int,
    ) -> CoverageReport:
        return CoverageReport(
            total_transformations=total_txs,
            visited=len(self._visited),
            fully_evaluated=len(self._evaluated),
            pass_through=len(self._pass_through),
            sink_nodes_reached=len(self._sinks_reached),
            total_sink_nodes=total_sinks,
            filter_drops=list(self._filter_drops),
        )


# -- Row Tracer (Orchestrator) --------------------------------------------

_EVALUABLE_OPS = frozenset({
    "filter", "where", "select", "drop",
    "withColumn", "withColumnRenamed", "join",
    "distinct", "union", "unionByName",
})

_PASS_THROUGH_OPS = frozenset({
    "groupBy_agg", "agg", "orderBy", "sort", "cache", "persist",
    "dropDuplicates", "fillna", "toDF", "crossJoin", "subtract",
    "replace", "pivot", "tail", "max", "groupBy", "transform",
    "alias", "select_custom",
})


@dataclass
class TraceResult:
    """Result of tracing rows through the ASG transformation graph."""
    expected_outputs: dict[str, list[dict[str, Any]]]
    coverage: CoverageReport
    transformation_chains: dict[str, list[str]]

    def to_dict(self) -> dict:
        return {
            "version": "1.0",
            "tables": {
                name: {
                    "rows": rows,
                    "row_count": len(rows),
                    "transformation_chain": self.transformation_chains.get(name, []),
                }
                for name, rows in self.expected_outputs.items()
            },
            "coverage": self.coverage.to_dict(),
        }


class RowTracer:
    """Traces synthetic rows through the ASG transformation DAG.

    Builds the graph from ``transformations[].inputs``, topologically
    sorts it, and feeds rows through each operation.  Rows that survive
    to sink nodes are collected as expected outputs.
    """

    def __init__(
        self,
        transformations: list[dict],
        data_out: list[dict] | None = None,
        reference_timestamp: datetime | None = None,
    ):
        self._txs = {tx["id"]: tx for tx in transformations}
        self._data_out = data_out or []

        constants = ParameterResolver.scan_constants(transformations)
        resolver = ParameterResolver(
            reference_timestamp=reference_timestamp,
            known_constants=constants,
        )
        self._evaluator = ExpressionEvaluator(resolver=resolver)
        self._mapper = StateMapper(self._evaluator)
        self._tracker = CoverageTracker()

        self._children: dict[str, list[str]] = {}
        self._parents: dict[str, list[str]] = {}
        self._build_graph()

    def _build_graph(self) -> None:
        for tx_id, tx in self._txs.items():
            self._children.setdefault(tx_id, [])
            self._parents.setdefault(tx_id, [])
            for inp in tx.get("inputs", []):
                if inp in self._txs:
                    self._children.setdefault(inp, []).append(tx_id)
                    self._parents[tx_id].append(inp)

    def _find_roots(self) -> list[str]:
        return [
            tx_id for tx_id, parents in self._parents.items()
            if not parents
        ]

    def _find_sinks(self) -> list[str]:
        return [
            tx_id for tx_id, children in self._children.items()
            if not children
        ]

    def _topo_sort(self) -> list[str]:
        in_degree: dict[str, int] = {
            tx_id: len(parents)
            for tx_id, parents in self._parents.items()
        }
        queue = [tx_id for tx_id, deg in in_degree.items() if deg == 0]
        order: list[str] = []

        while queue:
            queue.sort()
            node = queue.pop(0)
            order.append(node)
            for child in self._children.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) < len(self._txs):
            remaining = set(self._txs) - set(order)
            order.extend(sorted(remaining))

        return order

    def trace(
        self,
        input_data: dict[str, list[dict[str, Any]]],
        source_map: dict[str, str] | None = None,
    ) -> TraceResult:
        """Trace *input_data* through the transformation graph.

        Parameters
        ----------
        input_data : dict
            Mapping of table_name -> list of row dicts (from generator).
        source_map : dict
            Mapping of source_id (in_*) -> table_name.
        """
        src_map = source_map or {}
        order = self._topo_sort()
        sinks = set(self._find_sinks())

        node_rows: dict[str, list[dict[str, Any]]] = {}

        for tx_id in order:
            tx = self._txs[tx_id]
            op = tx.get("operation", "unknown")
            params = tx.get("parameters", {})
            inputs = tx.get("inputs", [])

            incoming: list[dict[str, Any]] = []
            for inp in inputs:
                if inp in node_rows:
                    incoming.extend(node_rows[inp])
                elif inp in src_map:
                    table_name = src_map[inp]
                    if table_name in input_data:
                        incoming.extend(input_data[table_name])

            if not incoming and not inputs:
                for table_name, rows in input_data.items():
                    incoming.extend(rows)
                    break

            if not incoming:
                continue

            secondary: list[dict[str, Any]] | None = None
            if op in ("join", "union", "unionByName") and len(inputs) >= 2:
                secondary = node_rows.get(inputs[1], [])

            is_evaluable = op in _EVALUABLE_OPS
            rows_before = len(incoming)

            if is_evaluable:
                result_rows = self._mapper.apply(
                    op, params, incoming, secondary,
                )
                self._tracker.record_visit(tx_id, evaluated=True)
            else:
                result_rows = list(incoming)
                self._tracker.record_visit(tx_id, evaluated=False)

            if op in ("filter", "where"):
                dropped = rows_before - len(result_rows)
                cond = params.get("condition", "")
                self._tracker.record_filter_drop(tx_id, cond, dropped)

            node_rows[tx_id] = result_rows

            if tx_id in sinks and result_rows:
                self._tracker.record_sink_reached(tx_id)

        expected_outputs: dict[str, list[dict[str, Any]]] = {}
        chains: dict[str, list[str]] = {}

        for sink_id in sinks:
            rows = node_rows.get(sink_id, [])
            if not rows:
                continue
            tx = self._txs[sink_id]
            loc = tx.get("location", {})
            name = f"sink_{sink_id}"

            for out in self._data_out:
                out_id = out.get("id", "")
                if out_id in self._children.get(sink_id, [sink_id]):
                    name = out.get("name", name)
                    break

            expected_outputs[name] = rows

            chain = []
            current = sink_id
            visited_chain: set[str] = set()
            while current and current not in visited_chain:
                visited_chain.add(current)
                chain.insert(0, current)
                parents = self._parents.get(current, [])
                current = parents[0] if parents else None
            chains[name] = chain

        coverage = self._tracker.build_report(
            total_txs=len(self._txs),
            total_sinks=len(sinks),
        )

        return TraceResult(
            expected_outputs=expected_outputs,
            coverage=coverage,
            transformation_chains=chains,
        )

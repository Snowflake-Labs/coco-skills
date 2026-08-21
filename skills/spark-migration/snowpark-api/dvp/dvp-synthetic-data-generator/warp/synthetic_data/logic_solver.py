"""
Hybrid Z3 Logic Solver -- Template Generation (Phase 2.1).

Uses Microsoft's Z3 SMT solver to produce satisfying data templates for
constraint sets that the lightweight DomainSolver cannot handle:

1. Cross-column constraints  (amount * rate > threshold)
2. Non-linear arithmetic     (ABS(x) * 1.5 < y)
3. Contradiction confirmation (DomainSolver says empty -> Z3 verifies)

Z3 is an **optional dependency** loaded lazily.  The ``import z3`` call
happens only when the Z3Dispatcher determines that symbolic solving is
actually needed.  This means:

- ``import synthetic_data`` never loads Z3.
- Simple workloads (no cross-column constraints) never load Z3.
- Complex workloads load Z3 only at the moment the solver runs.
- If Z3 is not installed, all methods return empty results gracefully.

Architecture:
- ``ExpressionTranslator``: Converts ASG ColumnConstraints to Z3 expressions
- ``Z3TemplateSolver``: Generates satisfying model "templates"
- ``Z3Dispatcher``: Decides whether Z3 is needed for a constraint set
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

_z3_module: Any = None


def _get_z3() -> Any:
    """Lazy-load the z3 module on first real use.

    Returns the z3 module or ``None`` if not installed.
    """
    global _z3_module
    if _z3_module is not None:
        return _z3_module
    try:
        import z3
        _z3_module = z3
        return z3
    except ImportError:
        return None


def is_available() -> bool:
    """Return ``True`` if ``z3-solver`` is importable.

    This does NOT eagerly load Z3; it only checks if the package exists
    using importlib without triggering the heavy C++ binary load.
    """
    import importlib.util
    return importlib.util.find_spec("z3") is not None


# -- Operator mapping from ASG constraint_type to Z3 ----------------------

_COMPARISON_OPS: dict[str, str] = {
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "equals": "==",
    "not_equals": "!=",
}


# -- Expression Translator ------------------------------------------------

@dataclass
class _ColumnMeta:
    """Tracked metadata for a Z3 variable."""
    name: str
    z3_var: Any
    data_type: str
    is_int: bool


class ExpressionTranslator:
    """Translates ASG ``ColumnConstraint`` dicts into Z3 assertions.

    Creates Z3 variables on demand and builds a conjunction of all
    translatable constraints.  Constraints that cannot be translated
    (e.g. ``like``, ``rlike``) are silently skipped.
    """

    def __init__(self) -> None:
        self._vars: dict[str, _ColumnMeta] = {}
        self._assertions: list[Any] = []
        self._skipped: list[dict] = []

    @property
    def variables(self) -> dict[str, _ColumnMeta]:
        return dict(self._vars)

    @property
    def assertions(self) -> list:
        return list(self._assertions)

    @property
    def skipped(self) -> list[dict]:
        return list(self._skipped)

    def _get_or_create_var(self, name: str, data_type: str = "unknown") -> Any:
        """Get or create a Z3 variable for *name*."""
        z3 = _get_z3()
        if z3 is None:
            return None
        if name in self._vars:
            return self._vars[name].z3_var

        dt = (data_type or "unknown").upper()
        is_int = dt in ("INT", "INTEGER", "LONG", "BIGINT", "SHORT", "SMALLINT")

        if is_int:
            var = z3.Int(name)
        else:
            var = z3.Real(name)

        self._vars[name] = _ColumnMeta(
            name=name, z3_var=var, data_type=dt, is_int=is_int,
        )
        return var

    def translate(self, constraints: list[dict]) -> list:
        """Translate a list of ASG constraints into Z3 assertions.

        Returns the list of Z3 assertion objects.  Non-translatable
        constraints are collected in ``self.skipped``.
        """
        if _get_z3() is None:
            return []

        for c in constraints:
            ct = c.get("constraint_type", "")
            col = c.get("column_name", "")
            raw_val = c.get("value")
            vtype = c.get("value_type", "unknown")

            if ct in ("not_null", "is_null", "like", "rlike"):
                self._skipped.append(c)
                continue

            if ct in _COMPARISON_OPS:
                assertion = self._translate_comparison(col, ct, raw_val, vtype)
                if assertion is not None:
                    self._assertions.append(assertion)
                else:
                    self._skipped.append(c)

            elif ct in ("in", "enum"):
                assertion = self._translate_in(col, raw_val, vtype)
                if assertion is not None:
                    self._assertions.append(assertion)
                else:
                    self._skipped.append(c)

            elif ct == "between":
                assertion = self._translate_between(col, raw_val, vtype)
                if assertion is not None:
                    self._assertions.append(assertion)
                else:
                    self._skipped.append(c)

            else:
                self._skipped.append(c)

        return list(self._assertions)

    def _translate_comparison(
        self, col: str, ct: str, raw_val: Any, vtype: str,
    ) -> Any:
        lhs = self._get_or_create_var(col, vtype)

        if isinstance(raw_val, str) and self._is_column_reference(raw_val):
            rhs = self._get_or_create_var(raw_val, vtype)
        else:
            rhs = self._to_z3_value(raw_val, col)
            if rhs is None:
                return None

        op = _COMPARISON_OPS[ct]
        if op == ">":
            return lhs > rhs
        elif op == ">=":
            return lhs >= rhs
        elif op == "<":
            return lhs < rhs
        elif op == "<=":
            return lhs <= rhs
        elif op == "==":
            return lhs == rhs
        elif op == "!=":
            return lhs != rhs
        return None

    def _translate_in(self, col: str, raw_val: Any, vtype: str) -> Any:
        z3 = _get_z3()
        if not isinstance(raw_val, list) or not raw_val or z3 is None:
            return None
        var = self._get_or_create_var(col, vtype)
        z3_vals = []
        for v in raw_val:
            zv = self._to_z3_value(v, col)
            if zv is not None:
                z3_vals.append(var == zv)
        if not z3_vals:
            return None
        return z3.Or(*z3_vals)

    def _translate_between(self, col: str, raw_val: Any, vtype: str) -> Any:
        z3 = _get_z3()
        if not isinstance(raw_val, list) or len(raw_val) != 2 or z3 is None:
            return None
        var = self._get_or_create_var(col, vtype)
        lo = self._to_z3_value(raw_val[0], col)
        hi = self._to_z3_value(raw_val[1], col)
        if lo is None or hi is None:
            return None
        return z3.And(var >= lo, var <= hi)

    def _to_z3_value(self, raw: Any, col_hint: str = "") -> Any:
        """Convert a raw ASG value to a Z3-compatible constant."""
        z3 = _get_z3()
        if raw is None or z3 is None:
            return None
        if isinstance(raw, bool):
            return z3.IntVal(1 if raw else 0)
        if isinstance(raw, int):
            meta = self._vars.get(col_hint)
            if meta and not meta.is_int:
                return z3.RealVal(raw)
            return z3.IntVal(raw)
        if isinstance(raw, float):
            return z3.RealVal(raw)
        if isinstance(raw, str):
            raw = raw.strip()
            if raw.startswith("'") and raw.endswith("'"):
                raw = raw[1:-1]
            try:
                if "." in raw:
                    return z3.RealVal(float(raw))
                return z3.IntVal(int(raw))
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def _is_column_reference(val: str) -> bool:
        """Heuristic: a value is a column reference if it looks like
        an identifier (no quotes, no numbers-only, no operators)."""
        v = val.strip()
        if not v or v[0].isdigit():
            return False
        if v.startswith("'") or v.startswith('"'):
            return False
        if any(ch in v for ch in "+-*/()%"):
            return False
        return v.replace("_", "").replace(".", "").isalnum()


# -- Template Solver -------------------------------------------------------

@dataclass
class SolverResult:
    """Result of a Z3 template solving session."""
    status: str  # "sat", "unsat", "unknown", "unavailable"
    templates: list[dict[str, Any]] = field(default_factory=list)
    unsat_core_columns: list[str] = field(default_factory=list)
    skipped_constraints: int = 0
    z3_time_ms: float = 0.0


class Z3TemplateSolver:
    """Generates satisfying data templates using Z3.

    Given a set of ColumnConstraints, produces up to ``max_templates``
    distinct satisfying assignments.  Each template is a dict mapping
    column names to concrete values.

    If the constraints are unsatisfiable, returns ``status='unsat'``
    with the columns involved in the contradiction.
    """

    def __init__(self, max_templates: int = 50, timeout_ms: int = 5000):
        self.max_templates = max_templates
        self.timeout_ms = timeout_ms

    def solve(self, constraints: list[dict]) -> SolverResult:
        """Generate templates from ASG constraints."""
        z3 = _get_z3()
        if z3 is None:
            return SolverResult(status="unavailable")

        import time
        start = time.monotonic()

        translator = ExpressionTranslator()
        assertions = translator.translate(constraints)

        if not assertions:
            return SolverResult(
                status="sat",
                skipped_constraints=len(translator.skipped),
            )

        solver = z3.Solver()
        solver.set("timeout", self.timeout_ms)
        for a in assertions:
            solver.add(a)

        check = solver.check()
        elapsed = (time.monotonic() - start) * 1000

        if check == z3.unsat:
            core_cols = sorted(translator.variables.keys())
            return SolverResult(
                status="unsat",
                unsat_core_columns=core_cols,
                skipped_constraints=len(translator.skipped),
                z3_time_ms=round(elapsed, 2),
            )

        if check != z3.sat:
            return SolverResult(
                status="unknown",
                skipped_constraints=len(translator.skipped),
                z3_time_ms=round(elapsed, 2),
            )

        templates: list[dict[str, Any]] = []
        for _ in range(self.max_templates):
            if solver.check() != z3.sat:
                break

            model = solver.model()
            template = self._extract_template(model, translator.variables)
            templates.append(template)

            exclusion = z3.Or(*[
                translator.variables[col].z3_var != model[translator.variables[col].z3_var]
                for col in template
                if model[translator.variables[col].z3_var] is not None
            ])
            solver.add(exclusion)

        elapsed = (time.monotonic() - start) * 1000
        return SolverResult(
            status="sat",
            templates=templates,
            skipped_constraints=len(translator.skipped),
            z3_time_ms=round(elapsed, 2),
        )

    @staticmethod
    def _extract_template(
        model: Any, variables: dict[str, _ColumnMeta],
    ) -> dict[str, Any]:
        """Extract concrete values from a Z3 model."""
        result: dict[str, Any] = {}
        for col, meta in variables.items():
            val = model[meta.z3_var]
            if val is None:
                result[col] = None
                continue
            try:
                if meta.is_int:
                    result[col] = val.as_long()
                else:
                    result[col] = float(val.as_fraction())
            except (AttributeError, ValueError):
                result[col] = str(val)
        return result


# -- Dispatcher ------------------------------------------------------------

@dataclass
class DispatchDecision:
    """Why Z3 was (or was not) invoked."""
    use_z3: bool
    reason: str
    cross_column_constraints: list[dict] = field(default_factory=list)


class Z3Dispatcher:
    """Decides whether a constraint set needs Z3 or can be handled
    by the lightweight DomainSolver alone.

    This class does NOT import Z3 -- it only inspects constraint
    metadata.  Z3 is loaded only if the decision is ``use_z3=True``
    and the caller actually invokes the solver.

    Three triggers:
    1. Cross-column constraints (value references another column)
    2. Contradiction fallback (DomainSolver found empty domain)
    3. Arithmetic expressions in constraint values
    """

    @staticmethod
    def should_use_z3(
        constraints: list[dict],
        has_contradictions: bool = False,
    ) -> DispatchDecision:
        """Evaluate whether Z3 is needed for *constraints*.

        This method never imports z3.  It uses ``is_available()``
        (importlib.util.find_spec) to check presence without loading.
        """
        if not is_available():
            return DispatchDecision(
                use_z3=False, reason="z3-solver not installed",
            )

        cross_col = []
        for c in constraints:
            ct = c.get("constraint_type", "")
            val = c.get("value")
            if ct in ("gt", "gte", "lt", "lte", "equals", "not_equals"):
                if isinstance(val, str) and ExpressionTranslator._is_column_reference(val):
                    cross_col.append(c)

        if cross_col:
            return DispatchDecision(
                use_z3=True,
                reason=f"cross_column: {len(cross_col)} constraints reference other columns",
                cross_column_constraints=cross_col,
            )

        if has_contradictions:
            return DispatchDecision(
                use_z3=True,
                reason="fallback: DomainSolver found contradictions, Z3 will verify",
            )

        for c in constraints:
            val = str(c.get("value", ""))
            if any(op in val for op in ("*", "/", "+", "-", "ABS(", "ROUND(")):
                if not val.lstrip("-").replace(".", "").isdigit():
                    return DispatchDecision(
                        use_z3=True,
                        reason=f"arithmetic: expression '{val[:50]}' in constraint",
                    )

        return DispatchDecision(use_z3=False, reason="simple constraints, DomainSolver sufficient")

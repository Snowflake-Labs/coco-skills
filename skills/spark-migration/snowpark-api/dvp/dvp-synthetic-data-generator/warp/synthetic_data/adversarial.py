"""
Adversarial Noise Injection for Synthetic Data (Phase 2.2).

Injects controlled, semantically-aware "bad data" into generated rows
to test the resilience of migrated code (TRY_CAST, COALESCE, date
validation, etc.).

Five noise strategies, each targeting a different failure mode:

1. Boundary Poisoning  -- values at domain edges (BVA)
2. Format Drift        -- locale-dependent number/date formats
3. Nullability Stress  -- NULLs where NOT NULL is expected
4. Type Poisoning      -- wrong-type values in typed columns
5. Temporal Drift      -- impossible or far-future dates
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from synthetic_data.generator import DomainState


class NoiseType(str, Enum):
    BOUNDARY = "boundary"
    FORMAT_DRIFT = "format_drift"
    NULL_STRESS = "null_stress"
    TYPE_POISON = "type_poison"
    TEMPORAL_DRIFT = "temporal_drift"


_NOISE_WEIGHTS: dict[NoiseType, float] = {
    NoiseType.BOUNDARY: 0.30,
    NoiseType.FORMAT_DRIFT: 0.15,
    NoiseType.NULL_STRESS: 0.20,
    NoiseType.TYPE_POISON: 0.20,
    NoiseType.TEMPORAL_DRIFT: 0.15,
}


@dataclass
class NoiseResult:
    """Tracks what noise was injected for diagnostic reporting."""
    original_value: Any
    noisy_value: Any
    noise_type: NoiseType
    column: str
    table: str


@dataclass
class NoiseInjector:
    """Applies adversarial noise to generated synthetic data.

    Parameters
    ----------
    noise_rate : float
        Fraction of values to corrupt (0.0 = off, 0.1 = 10%).
    seed : int | None
        Random seed for reproducibility.
    enabled_types : set[NoiseType] | None
        Subset of noise types to use.  ``None`` = all five.
    reference_timestamp : datetime | None
        Anchor for temporal drift generation.
    """

    noise_rate: float = 0.0
    seed: int | None = None
    enabled_types: set[NoiseType] | None = None
    reference_timestamp: datetime | None = None
    injections: list[NoiseResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        if self.enabled_types is None:
            self.enabled_types = set(NoiseType)

    @property
    def active(self) -> bool:
        return self.noise_rate > 0.0

    def should_inject(self) -> bool:
        """Roll the dice -- returns True ``noise_rate`` fraction of the time."""
        return self.active and self._rng.random() < self.noise_rate

    def inject(
        self,
        value: Any,
        data_type: str,
        col_name: str,
        table_name: str = "",
        domain: DomainState | None = None,
    ) -> Any:
        """Maybe corrupt *value*.  Returns original if no injection."""
        if not self.should_inject():
            return value

        noise_type = self._pick_noise_type(data_type)
        if noise_type is None:
            return value

        noisy = self._apply(noise_type, value, data_type, domain)

        self.injections.append(NoiseResult(
            original_value=value,
            noisy_value=noisy,
            noise_type=noise_type,
            column=col_name,
            table=table_name,
        ))
        return noisy

    def inject_row(
        self,
        row: dict[str, Any],
        col_types: dict[str, str],
        table_name: str = "",
        domains: dict[str, DomainState] | None = None,
    ) -> dict[str, Any]:
        """Apply noise injection across an entire row."""
        if not self.active:
            return row
        domains = domains or {}
        result = dict(row)
        for col, val in row.items():
            dt = col_types.get(col, "STRING")
            result[col] = self.inject(
                val, dt, col, table_name, domains.get(col),
            )
        return result

    def get_summary(self) -> dict[str, Any]:
        """Return injection statistics."""
        from collections import Counter
        by_type = Counter(r.noise_type.value for r in self.injections)
        by_table = Counter(r.table for r in self.injections)
        return {
            "total_injections": len(self.injections),
            "by_noise_type": dict(by_type),
            "by_table": dict(by_table),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _pick_noise_type(self, data_type: str) -> NoiseType | None:
        dt = (data_type or "").upper()
        applicable = list(self.enabled_types or [])

        if dt in ("DATE", "TIMESTAMP"):
            preferred = [NoiseType.TEMPORAL_DRIFT, NoiseType.NULL_STRESS,
                         NoiseType.TYPE_POISON]
        elif dt in ("INT", "INTEGER", "LONG", "BIGINT", "SHORT", "SMALLINT",
                     "DECIMAL", "DOUBLE", "FLOAT", "NUMBER", "NUMERIC"):
            preferred = [NoiseType.BOUNDARY, NoiseType.FORMAT_DRIFT,
                         NoiseType.NULL_STRESS, NoiseType.TYPE_POISON]
        elif dt in ("BOOLEAN",):
            preferred = [NoiseType.NULL_STRESS, NoiseType.TYPE_POISON]
        else:
            preferred = [NoiseType.NULL_STRESS, NoiseType.TYPE_POISON,
                         NoiseType.FORMAT_DRIFT]

        candidates = [t for t in preferred if t in applicable]
        if not candidates:
            candidates = applicable
        if not candidates:
            return None

        weights = [_NOISE_WEIGHTS.get(t, 0.1) for t in candidates]
        return self._rng.choices(candidates, weights=weights, k=1)[0]

    def _apply(
        self,
        noise_type: NoiseType,
        value: Any,
        data_type: str,
        domain: DomainState | None,
    ) -> Any:
        if noise_type == NoiseType.BOUNDARY:
            return self._boundary_poison(value, data_type, domain)
        if noise_type == NoiseType.FORMAT_DRIFT:
            return self._format_drift(value, data_type)
        if noise_type == NoiseType.NULL_STRESS:
            return None
        if noise_type == NoiseType.TYPE_POISON:
            return self._type_poison(value, data_type)
        if noise_type == NoiseType.TEMPORAL_DRIFT:
            return self._temporal_drift(data_type)
        return value

    # -- Boundary Poisoning (BVA) --

    def _boundary_poison(
        self, value: Any, data_type: str, domain: DomainState | None,
    ) -> Any:
        d = domain or DomainState()
        dt = (data_type or "").upper()

        if dt in ("INT", "INTEGER", "LONG", "BIGINT", "SHORT", "SMALLINT"):
            return self._int_boundary(d)
        if dt in ("DECIMAL", "DOUBLE", "FLOAT", "NUMBER", "NUMERIC"):
            return self._float_boundary(d)
        return value

    def _int_boundary(self, d: DomainState) -> int:
        candidates = []
        lo = int(d.low) if d.low is not None else 0
        hi = int(d.high) if d.high is not None else 10000
        candidates.extend([lo - 1, lo, lo + 1, hi - 1, hi, hi + 1])
        candidates.extend([0, -1, 1, 2**31 - 1, -(2**31)])
        return self._rng.choice(candidates)

    def _float_boundary(self, d: DomainState) -> float:
        candidates = []
        lo = d.low if d.low is not None else 0.0
        hi = d.high if d.high is not None else 9999.99
        epsilon = 0.01
        candidates.extend([
            lo - epsilon, lo, lo + epsilon,
            hi - epsilon, hi, hi + epsilon,
            0.0, -0.01, float("inf"), float("-inf"), float("nan"),
        ])
        return round(self._rng.choice(candidates), 4)

    # -- Format Drift --

    def _format_drift(self, value: Any, data_type: str) -> str:
        dt = (data_type or "").upper()

        if dt in ("INT", "INTEGER", "LONG", "BIGINT", "DECIMAL", "DOUBLE",
                   "FLOAT", "NUMBER", "NUMERIC", "SHORT", "SMALLINT"):
            return self._rng.choice(self._numeric_format_variants(value))

        if dt in ("DATE", "TIMESTAMP"):
            return self._rng.choice(self._date_format_variants(value))

        if isinstance(value, str):
            return self._rng.choice([
                value.upper(),
                value.lower(),
                f" {value} ",
                value + "\t",
            ])
        return str(value)

    @staticmethod
    def _numeric_format_variants(value: Any) -> list[str]:
        s = str(value) if value is not None else "0"
        return [
            s.replace(".", ","),
            f"${s}",
            f"{s}€",
            s.replace(".", ""),
            f"({s})" if not s.startswith("-") else s,
            f" {s} ",
            s + "%",
        ]

    @staticmethod
    def _date_format_variants(value: Any) -> list[str]:
        s = str(value) if value is not None else "2025-01-01"
        return [
            s.replace("-", "/"),
            s.replace("-", "."),
            "/".join(reversed(s.split("-")[:3])) if "-" in s else s,
            s.replace("-", ""),
            f"'{s}'",
        ]

    # -- Type Poisoning --

    def _type_poison(self, value: Any, data_type: str) -> Any:
        dt = (data_type or "").upper()

        if dt in ("INT", "INTEGER", "LONG", "BIGINT", "DECIMAL", "DOUBLE",
                   "FLOAT", "NUMBER", "NUMERIC", "SHORT", "SMALLINT"):
            return self._rng.choice([
                "NOT_A_NUMBER",
                "NaN",
                "null",
                "",
                "1.2.3",
                "12abc",
                True,
                "∞",
            ])

        if dt == "BOOLEAN":
            return self._rng.choice(["yes", "no", "1", "0", "maybe", "", None])

        if dt in ("DATE", "TIMESTAMP"):
            return self._rng.choice([
                "not-a-date",
                "99/99/9999",
                "",
                "null",
                12345,
                True,
            ])

        if dt == "STRING":
            return self._rng.choice([
                42,
                3.14,
                True,
                None,
            ])

        return "POISONED_VALUE"

    # -- Temporal Drift --

    def _temporal_drift(self, data_type: str) -> str:
        base = self.reference_timestamp or datetime.now()
        dt = (data_type or "").upper()

        impossible_dates = [
            "2024-02-30",
            "2023-13-01",
            "2025-00-15",
            "9999-12-31",
            "0000-01-01",
            "2025-04-31",
        ]

        far_future = (base + timedelta(days=365 * 100)).strftime(
            "%Y-%m-%d" if dt == "DATE" else "%Y-%m-%d %H:%M:%S"
        )
        far_past = (base - timedelta(days=365 * 200)).strftime(
            "%Y-%m-%d" if dt == "DATE" else "%Y-%m-%d %H:%M:%S"
        )
        epoch_zero = "1970-01-01" if dt == "DATE" else "1970-01-01 00:00:00"

        candidates = impossible_dates + [far_future, far_past, epoch_zero]
        return self._rng.choice(candidates)

"""Output adapters for the migration-readiness HTML report.

Each adapter consumes a fully-populated :class:`assess_ir.Assessment` and
produces a concrete artifact. Adapters MUST be side-effect-free with respect
to the IR — they read only.

Available adapters:
  * :mod:`prototype_v1` — redesigned sidebar layout with Inter font / Snowflake branding.
"""

from . import prototype_v1  # noqa: F401

__all__ = ["prototype_v1"]

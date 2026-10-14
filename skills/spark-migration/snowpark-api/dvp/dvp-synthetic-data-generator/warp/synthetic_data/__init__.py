"""
WARP Synthetic Data Generator - Generate test data from ASG JSON.

Z3 components (Z3TemplateSolver, Z3Dispatcher, ExpressionTranslator) are
available via ``synthetic_data.logic_solver`` but are NOT imported eagerly
here.  This keeps the package startup fast and avoids loading the heavy
z3-solver binary when it is not needed.

    from synthetic_data.logic_solver import Z3TemplateSolver  # lazy
"""

from synthetic_data.generator import SyntheticDataGenerator, GenerationStrategy
from synthetic_data.adversarial import NoiseInjector, NoiseType
from synthetic_data.validator import (
    ParameterResolver,
    ExpressionEvaluator,
    StateMapper,
    RowTracer,
    CoverageTracker,
)

__all__ = [
    "SyntheticDataGenerator",
    "GenerationStrategy",
    "NoiseInjector",
    "NoiseType",
    "ParameterResolver",
    "ExpressionEvaluator",
    "StateMapper",
    "RowTracer",
    "CoverageTracker",
]
__version__ = "0.4.0"

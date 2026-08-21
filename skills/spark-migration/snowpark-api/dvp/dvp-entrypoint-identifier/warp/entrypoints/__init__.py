"""Entrypoint Detector - Identify execution entry points from ASG."""

from entrypoints.detector import (
    EntrypointDetector,
    Entrypoint,
    IOSummary,
)

__all__ = [
    "EntrypointDetector",
    "Entrypoint",
    "IOSummary",
]

__version__ = "0.1.0"

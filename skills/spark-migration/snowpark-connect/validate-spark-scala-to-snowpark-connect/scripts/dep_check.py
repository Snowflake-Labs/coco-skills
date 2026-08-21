#!/usr/bin/env python3
"""Dependency/classpath validation for Scala → Snowpark-Connect workloads.

Runs every check in a single pass — never aborts on the first problem.
All findings are aggregated and emitted as JSON on stdout.

Usage:
  dep_check.py --conv-root <path> [--jar <path>]

Output JSON:
  {"ok": bool, "problems": ["..."], "warnings": ["..."]}

Exit codes: 0 = all clear, 1 = blocking problems, 2 = warnings only.

Checks performed
----------------
1. Spark version alignment  : Output/ build file vs kit defaults.
2. Delta version alignment  : artifact name + version vs kit defaults.
3. Duplicate class detection: assembly JAR entries (exact filename match).
4. SCOS client jar presence : snowpark-connect-java-client*.jar locatable?
5. Shaded-library collision : _shaded_/relocated./repackaged. entries that
                              overlap known Spark/Hadoop internal packages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Kit defaults (from harness-scala/kit/build.sbt sys.env fallback values)
# ---------------------------------------------------------------------------
_KIT_DEFAULT_SPARK_VERSION = "3.5.1"
_KIT_DEFAULT_DELTA_ARTIFACT = "delta-spark"
_KIT_DEFAULT_DELTA_VERSION = "3.1.0"

VALIDATION_DIRNAME = "Validation"

# Known Spark/Hadoop internal package prefixes that shaded copies collide with.
_SPARK_INTERNAL_PREFIXES = frozenset([
    "org/apache/spark/",
    "org/apache/hadoop/",
    "org/apache/avro/",
    "org/apache/parquet/",
    "com/fasterxml/jackson/",
    "org/slf4j/",
    "com/google/protobuf/",
])

# Markers that indicate a class entry is a shaded/relocated copy.
_SHADED_MARKERS = ("_shaded_", "relocated.", "repackaged.", "shaded/", "shadow/")


# ---------------------------------------------------------------------------
# Build file parsers
# ---------------------------------------------------------------------------

def _parse_sbt(content: str) -> dict:
    """Extract Spark/Delta/Scala version info from a build.sbt."""
    def _extract(pattern: str) -> Optional[str]:
        m = re.search(pattern, content, re.DOTALL)
        if not m:
            return None
        return next((g for g in m.groups() if g), None)

    spark_version = _extract(
        r'sparkVersion\s*[=:]=?\s*(?:sys\.env\.getOrElse\([^,]+,\s*"([^"]+)"\)|"([^"]+)")'
    )
    if not spark_version:
        # Fallback: direct version string next to spark-sql artifactId
        spark_version = _extract(
            r'"org\.apache\.spark"[^"]*?"spark-sql"[^"]*?"([0-9]+\.[0-9]+\.[0-9][^"]*)"'
        )

    delta_version = _extract(
        r'deltaVersion\s*[=:]=?\s*(?:sys\.env\.getOrElse\([^,]+,\s*"([^"]+)"\)|"([^"]+)")'
    )
    delta_artifact = _extract(
        r'deltaArtifact\s*[=:]=?\s*(?:sys\.env\.getOrElse\([^,]+,\s*"([^"]+)"\)|"([^"]+)")'
    )
    if not delta_artifact and delta_version:
        # Infer artifact from library dep line: "io.delta" %% "delta-spark" % ...
        m = re.search(r'"io\.delta"[^"]*?"(delta-(?:core|spark))"', content)
        if m:
            delta_artifact = m.group(1)

    return {
        "spark_version": spark_version,
        "delta_version": delta_version,
        "delta_artifact": delta_artifact,
        "build_tool": "sbt",
    }


def _parse_pom(content: str) -> dict:
    """Extract Spark/Delta versions from a pom.xml."""
    # Try <spark.version> property first
    sv = re.search(r'<spark\.version>([^<]+)</spark\.version>', content)
    if not sv:
        # Fallback: version immediately after spark-sql artifactId
        sv = re.search(
            r'<artifactId>spark-sql(?:_[0-9.]+)?</artifactId>\s*<version>([^<]+)</version>',
            content, re.DOTALL,
        )
    delta_m = re.search(
        r'<artifactId>(delta-(?:core|spark)(?:_[0-9.]+)?)</artifactId>\s*<version>([^<]+)</version>',
        content, re.DOTALL,
    )
    return {
        "spark_version": sv.group(1).strip() if sv else None,
        "delta_version": delta_m.group(2).strip() if delta_m else None,
        "delta_artifact": delta_m.group(1).split("_")[0] if delta_m else None,
        "build_tool": "maven",
    }


def _parse_gradle(content: str) -> dict:
    """Extract Spark/Delta versions from a build.gradle or build.gradle.kts."""
    sv = re.search(
        r'(?:spark[Vv]ersion|sparkVersion)\s*[=:]\s*["\']([0-9]+\.[0-9]+\.[0-9][^"\']*)["\']',
        content,
    )
    if not sv:
        sv = re.search(r'spark-sql[^"\']*["\']([0-9]+\.[0-9]+\.[0-9][^"\']*)["\']', content)
    delta_m = re.search(
        r'(delta-(?:core|spark))["\'][^"\']*["\']([0-9]+\.[0-9]+\.[0-9][^"\']*)["\']',
        content,
    )
    return {
        "spark_version": sv.group(1) if sv else None,
        "delta_version": delta_m.group(2) if delta_m else None,
        "delta_artifact": delta_m.group(1) if delta_m else None,
        "build_tool": "gradle",
    }


def _load_output_build_info(output_dir: Path) -> dict:
    """Parse the first build file found under output_dir."""
    for candidate, parser in [
        ("build.sbt", _parse_sbt),
        ("pom.xml", _parse_pom),
        ("build.gradle.kts", _parse_gradle),
        ("build.gradle", _parse_gradle),
    ]:
        p = output_dir / candidate
        if p.exists():
            try:
                return parser(p.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                return {"error": str(exc)}
    return {}


# ---------------------------------------------------------------------------
# JAR locator
# ---------------------------------------------------------------------------

def _find_assembly_jar(output_dir: Path, analysis_jar_path: str) -> Optional[Path]:
    """Return the assembly/fat JAR path from analysis.json hint or target/ scan."""
    if analysis_jar_path:
        p = Path(analysis_jar_path)
        if not p.is_absolute():
            p = output_dir / analysis_jar_path
        if p.exists():
            return p

    target = output_dir / "target"
    if target.is_dir():
        # Prefer explicitly named assembly/uber/fat/shaded jars
        assembly = sorted(
            (j for j in target.rglob("*.jar")
             if any(k in j.name.lower() for k in ("assembly", "uber", "fat", "shaded"))
             and "sources" not in j.name and "javadoc" not in j.name),
            key=lambda j: j.stat().st_mtime,
            reverse=True,
        )
        if assembly:
            return assembly[0]
        # Fallback: newest non-sources/javadoc jar
        all_jars = sorted(
            (j for j in target.rglob("*.jar")
             if "sources" not in j.name and "javadoc" not in j.name),
            key=lambda j: j.stat().st_mtime,
            reverse=True,
        )
        if all_jars:
            return all_jars[0]
    return None


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _check_version_alignment(output_dir: Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []

    build_info = _load_output_build_info(output_dir)
    if "error" in build_info:
        warnings.append(
            f"dep_check: could not parse Output/ build file: {build_info['error']}"
        )
        return problems, warnings
    if not build_info:
        warnings.append(
            "dep_check: no build.sbt / pom.xml / build.gradle found in Output/ — "
            "skipping version alignment check"
        )
        return problems, warnings

    spark = build_info.get("spark_version")
    delta = build_info.get("delta_version")
    delta_artifact = build_info.get("delta_artifact")

    if spark and spark != _KIT_DEFAULT_SPARK_VERSION:
        user_mm = tuple(spark.split(".")[:2])
        kit_mm = tuple(_KIT_DEFAULT_SPARK_VERSION.split(".")[:2])
        msg = (
            f"dep_check: Spark version mismatch — Output/ uses {spark!r}, "
            f"kit default is {_KIT_DEFAULT_SPARK_VERSION!r}. "
            "Set SCOS_KIT_SPARK_VERSION to match, or Phase A classpath may diverge."
        )
        if user_mm != kit_mm:
            problems.append(msg)
        else:
            warnings.append(msg)

    if delta and delta != _KIT_DEFAULT_DELTA_VERSION:
        problems.append(
            f"dep_check: Delta version mismatch — Output/ uses {delta!r}, "
            f"kit default is {_KIT_DEFAULT_DELTA_VERSION!r}. "
            "Set SCOS_KIT_DELTA_VERSION to match."
        )

    if delta_artifact and delta_artifact != _KIT_DEFAULT_DELTA_ARTIFACT:
        problems.append(
            f"dep_check: Delta artifact mismatch — Output/ uses {delta_artifact!r}, "
            f"kit default is {_KIT_DEFAULT_DELTA_ARTIFACT!r}. "
            "For Spark 3.3/3.4 workloads use delta-core; set SCOS_KIT_DELTA_ARTIFACT."
        )

    return problems, warnings


def _check_duplicate_classes(jar_path: Path) -> tuple[list[str], list[str]]:
    """Detect duplicate class entries in the assembly JAR (exact filename match only).

    Only raw filename duplicates are flagged as problems.  The previous canonical
    (inner-class normalised) approach collapsed ``Foo.class`` and ``Foo$1.class``
    into the same key, producing false positives for any class with anonymous or
    named inner classes — a normal pattern in all JVM code.
    """
    problems: list[str] = []
    warnings: list[str] = []
    seen: dict[str, int] = {}

    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            for name in zf.namelist():
                if not name.endswith(".class"):
                    continue
                seen[name] = seen.get(name, 0) + 1
    except (zipfile.BadZipFile, OSError) as exc:
        warnings.append(
            f"dep_check: could not inspect JAR {jar_path.name} for duplicates: {exc}"
        )
        return problems, warnings

    dupes = [k for k, v in seen.items() if v > 1]
    if dupes:
        sample = dupes[:20]
        problems.append(
            f"dep_check: {len(dupes)} duplicate class entry(ies) in {jar_path.name} "
            f"(sample, up to 20): {', '.join(sample)}"
        )
    return problems, warnings


def _check_scos_jar(output_dir: Path, conv_root: Path) -> tuple[list[str], list[str]]:
    """Check that the SCOS Scala client jar is locatable (mirrors scos_state logic)."""
    tests_lib = conv_root / VALIDATION_DIRNAME / "tests" / "lib"
    if list(tests_lib.glob("snowpark-connect-java-client*.jar")):
        return [], []

    search = [
        output_dir / "lib",
        Path.home() / ".m2" / "repository" / "com" / "snowflake",
        Path.home() / "Library" / "Caches" / "Coursier" / "v1" / "https"
            / "repo1.maven.org" / "maven2" / "com" / "snowflake",
        Path.home() / ".cache" / "coursier" / "v1" / "https" / "repo1.maven.org"
            / "maven2" / "com" / "snowflake",
    ]
    for base in search:
        if base.is_dir():
            for j in base.rglob("snowpark-connect-java-client*.jar"):
                if "sources" not in j.name and "javadoc" not in j.name:
                    return [], []

    return (
        [
            "dep_check: SCOS Scala client jar (snowpark-connect-java-client) not found in "
            "Validation/tests/lib, Output/lib, ~/.m2, or the Coursier cache — "
            "Phase B will fail with ClassNotFoundException. "
            "Place the jar in Output/lib/ before running Phase B."
        ],
        [],
    )


def _check_shaded_collisions(jar_path: Path) -> tuple[list[str], list[str]]:
    """Detect shaded entries that collide with known Spark/Hadoop internal packages."""
    problems: list[str] = []
    warnings: list[str] = []
    collisions: list[str] = []

    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            for name in zf.namelist():
                if not name.endswith(".class"):
                    continue
                low = name.lower()
                if any(m in low for m in _SHADED_MARKERS):
                    if any(low.startswith(pfx) or pfx in low for pfx in _SPARK_INTERNAL_PREFIXES):
                        collisions.append(name)
    except (zipfile.BadZipFile, OSError) as exc:
        warnings.append(
            f"dep_check: could not inspect JAR for shaded collisions: {exc}"
        )
        return problems, warnings

    if collisions:
        sample = collisions[:10]
        problems.append(
            f"dep_check: {len(collisions)} shaded-library collision(s) in {jar_path.name} "
            "— shaded copies of Spark/Hadoop classes may cause NoSuchMethodError or "
            f"ClassCastException at runtime. Sample: {', '.join(sample)}"
        )
    return problems, warnings


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def run_checks(conv_root: Path, jar_path_override: str = "") -> dict:
    """Run all checks and return ``{"ok": bool, "problems": [...], "warnings": [...]}``.

    Safe to import and call directly (e.g. from scos_state.py prevalidate).
    """
    output_dir = conv_root / "Output"
    all_problems: list[str] = []
    all_warnings: list[str] = []

    # Resolve JAR path from analysis.json if not overridden
    analysis_jar = jar_path_override or ""
    if not analysis_jar:
        analysis_json = conv_root / VALIDATION_DIRNAME / "shared" / "analysis.json"
        if analysis_json.exists():
            try:
                data = json.loads(analysis_json.read_text(encoding="utf-8"))
                analysis_jar = data.get("jar_path", "") or ""
            except Exception:
                pass

    # Check 1+2: version alignment
    p, w = _check_version_alignment(output_dir)
    all_problems.extend(p)
    all_warnings.extend(w)

    # Check 4: SCOS client jar (doesn't need assembly JAR)
    p, w = _check_scos_jar(output_dir, conv_root)
    all_problems.extend(p)
    all_warnings.extend(w)

    # Checks 3+5: require the assembly JAR
    jar = _find_assembly_jar(output_dir, analysis_jar)
    if jar:
        p, w = _check_duplicate_classes(jar)
        all_problems.extend(p)
        all_warnings.extend(w)

        p, w = _check_shaded_collisions(jar)
        all_problems.extend(p)
        all_warnings.extend(w)
    else:
        all_warnings.append(
            "dep_check: no assembly JAR found in Output/target/ and no jar_path in "
            "analysis.json — skipping duplicate-class and shaded-collision checks"
        )

    return {
        "ok": len(all_problems) == 0,
        "problems": all_problems,
        "warnings": all_warnings,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--conv-root", required=True,
        help="Conversion root directory (must contain Output/).",
    )
    ap.add_argument(
        "--jar", default="",
        help="Override assembly JAR path (else read from analysis.json / scan target/).",
    )
    args = ap.parse_args(argv)

    conv_root = Path(args.conv_root).expanduser().resolve()
    if not conv_root.is_dir():
        print(json.dumps({
            "ok": False,
            "problems": [f"dep_check: conv_root not found: {conv_root}"],
            "warnings": [],
        }))
        return 1

    result = run_checks(conv_root, jar_path_override=args.jar)
    print(json.dumps(result, indent=2))

    if result["problems"]:
        return 1
    if result["warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

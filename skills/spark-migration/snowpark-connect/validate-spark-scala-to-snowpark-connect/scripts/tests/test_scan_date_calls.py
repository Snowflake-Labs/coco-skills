"""Tests for scan_date_calls.py."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import scan_date_calls as sdc  # noqa: E402


def test_detects_bare_and_qualified_calls(tmp_path):
    source = tmp_path / "Validation" / "source" / "src" / "Job.scala"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "import org.apache.spark.sql.functions._\n"
        "val a = functions.current_date()\n"
        "val b = current_date()\n",
        encoding="utf-8",
    )
    hits = sdc._scan_tree(tmp_path / "Validation" / "source")
    kinds = {h["kind"] for h in hits}
    assert "functions.current_date()" in kinds
    assert "current_date()" in kinds
    batch = sdc.build_patch_batch(hits)
    assert batch["patches"]


def test_clean_scan_exits_zero(tmp_path):
    source = tmp_path / "Validation" / "source" / "src" / "Job.scala"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("object Job { def main(args: Array[String]): Unit = () }", encoding="utf-8")
    report = sdc.run(tmp_path)
    assert report["clean"] is True

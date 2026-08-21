"""Tests for generate_scos_reports input-files inventory (R3 code-vs-data split).

Code (.scala/.py/...) and build files (build.sbt/pom.xml) are conversion units
(`Ignored == "False"`); data/resource files (CSV/TXT/...) are inventoried but
marked `Ignored == "True"` so they don't count as migration work.
"""

from __future__ import annotations

import csv
import pathlib

import generate_scos_reports as g


def test_is_conversion_unit_classification():
    assert g._is_conversion_unit("Main.scala", ".scala", "Scala") is True
    assert g._is_conversion_unit("app.py", ".py", "Python") is True
    assert g._is_conversion_unit("build.sbt", ".sbt", "Other") is True
    assert g._is_conversion_unit("pom.xml", ".xml", "Other") is True
    assert g._is_conversion_unit("data.csv", ".csv", "Other") is False
    assert g._is_conversion_unit("notes.txt", ".txt", "Other") is False
    assert g._is_conversion_unit("sample.dat", ".dat", "Other") is False


def _read_inventory(reports_dir: pathlib.Path) -> dict[str, str]:
    """Return {FileId: Ignored} from the generated inventory."""
    path = reports_dir / "InputFilesInventory.csv"
    with path.open(encoding="utf-8") as f:
        return {r["FileId"]: r["Ignored"] for r in csv.DictReader(f)}


def test_inventory_marks_data_files_ignored(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Main.scala").write_text("object M {}\n", encoding="utf-8")
    (src / "build.sbt").write_text('name := "x"\n', encoding="utf-8")
    (src / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (src / "notes.txt").write_text("hello\n", encoding="utf-8")
    (src / "sample.dat").write_text("raw\n", encoding="utf-8")

    out = tmp_path / "out"
    n = g.generate_input_files_inventory(str(src), str(out), "proj", "exec-1")
    assert n == 5

    ign = _read_inventory(out / "Reports")
    # Conversion units
    assert ign["Main.scala"] == "False"
    assert ign["build.sbt"] == "False"
    # Data / resource files — ignored, not migration work
    assert ign["data.csv"] == "True"
    assert ign["notes.txt"] == "True"
    assert ign["sample.dat"] == "True"

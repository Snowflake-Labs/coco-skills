"""Static guard: validation_report_app.py must stay byte-identical to the PySpark copy.

Both files carry a comment at the top explaining the constraint. If the files
diverge, this test fails with a message telling you to edit both together.
"""
from pathlib import Path


def test_report_app_in_sync_with_pyspark():
    java = (
        Path(__file__).resolve().parents[1]
        / "report"
        / "validation_report_app.py"
    )
    pyspark = (
        Path(__file__).resolve().parents[3]
        / "validate-pyspark-to-snowpark-connect"
        / "scripts"
        / "report"
        / "validation_report_app.py"
    )
    assert java.read_text(encoding="utf-8") == pyspark.read_text(encoding="utf-8"), (
        "validation_report_app.py has diverged from the PySpark validator copy.\n"
        "Edit both files together:\n"
        f"  {java}\n"
        f"  {pyspark}"
    )

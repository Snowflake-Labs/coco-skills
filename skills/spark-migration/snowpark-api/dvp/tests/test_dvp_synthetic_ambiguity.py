"""Tests for SyntheticDataGenerator handling of ambiguous columns.

The updated warp-suite generator includes all columns regardless of confidence
level.  Ambiguous columns are generated with valid data so downstream tests can
still exercise them; the diagnostic_reporter is responsible for flagging them.
"""

import json
import sys
from pathlib import Path

_DVP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_DVP_DIR / "dvp-orchestrator"))
sys.path.insert(0, str(_DVP_DIR / "dvp-synthetic-data-generator" / "warp"))

from synthetic_data.generator import SyntheticDataGenerator


def test_ambiguous_columns_are_included_in_csv(tmp_path: Path):
    data_io = [
        {
            "name": "t_left",
            "role": "input",
            "columns": [
                {"name": "id", "type": "STRING", "confidence": "evidence"},
                {"name": "amount", "type": "DECIMAL", "confidence": "ambiguous", "reason": "multi_origin"},
            ],
        },
        {
            "name": "t_right",
            "role": "input",
            "columns": [
                {"name": "id", "type": "STRING", "confidence": "evidence"},
                {"name": "amount", "type": "DECIMAL", "confidence": "ambiguous", "reason": "multi_origin"},
            ],
        },
    ]

    data_io_path = tmp_path / "data_io_schema.json"
    data_io_path.write_text(json.dumps(data_io))

    asg = {
        "column_relationships": [
            {
                "left_column": "id",
                "left_source": "in_001",
                "right_column": "id",
                "right_source": "in_002",
                "join_type": "left",
                "source_transformation": "tx_001",
            }
        ],
        "data_in": [
            {"id": "in_001", "name": "t_left"},
            {"id": "in_002", "name": "t_right"},
        ],
    }
    asg_path = tmp_path / "asg.json"
    asg_path.write_text(json.dumps(asg))

    gen = SyntheticDataGenerator.from_files(data_io_path=data_io_path, asg_path=asg_path)

    output_dir = tmp_path / "out"
    created = gen.write_csv_files(output_dir=output_dir, rows_per_table=2)

    left_csv = output_dir / "synthetic_data" / "t_left.csv"
    right_csv = output_dir / "synthetic_data" / "t_right.csv"

    assert left_csv.exists()
    assert right_csv.exists()

    left_header = left_csv.read_text().splitlines()[0].split(",")
    right_header = right_csv.read_text().splitlines()[0].split(",")

    assert "id" in left_header
    assert "amount" in left_header
    assert "id" in right_header
    assert "amount" in right_header

    metadata_path = output_dir / "synthetic_data" / "metadata.json"
    assert metadata_path in created
    metadata = json.loads(metadata_path.read_text())
    assert "t_left" in metadata["tables"]
    assert "t_right" in metadata["tables"]

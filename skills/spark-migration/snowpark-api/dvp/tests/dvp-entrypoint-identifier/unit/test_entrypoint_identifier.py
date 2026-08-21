"""Tests for dvp-entrypoint-identifier (ASG-only).

Validates that EntrypointDetector produces expected entrypoints from ASG JSON.
"""

from entrypoints import EntrypointDetector


def test_detects_entrypoints_from_asg() -> None:
    asg = {
        "source_files": [
            {"path": "jobs/a.py", "source_type": "script", "is_entry_point": True},
            {"path": "jobs/b.py", "source_type": "script", "is_entry_point": False},
        ],
        "data_in": [
            {"type": "parquet", "location": {"pathfile": "jobs/a.py"}},
            {"type": "parquet", "location": {"pathfile": "jobs/b.py"}},
        ],
        "data_out": [
            {"type": "delta", "location": {"pathfile": "jobs/b.py"}},
        ],
        "execution_calls": [
            {"caller": {"file": "jobs/a.py"}, "callee": {"file": "jobs/b.py"}},
        ],
    }

    detector = EntrypointDetector()
    eps = detector.detect(asg)

    assert [e.name for e in eps] == ["a"]

    ep = eps[0]
    assert ep.origin == "ASG"
    assert ep.status == "detected"
    assert ep.type == "script"

    d = ep.to_dict()
    assert "origin" in d
    assert "lineno" not in d
    assert "source" in d

    # direct + transitive IO rollup (a depends on b)
    assert ep.inputs.total == 2
    assert ep.outputs.total == 1

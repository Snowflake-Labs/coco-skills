"""Shared fixtures for sma_api unit tests."""

import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import sma_api  # noqa: E402


# ---------------------------------------------------------------------------
# CSV content helpers
# ---------------------------------------------------------------------------

ISSUES_CSV = textwrap.dedent("""\
    Code,Description,Category,FileId,Line,Column,Url
    SPRKPY1001,Unsupported API call,ConversionError,src/etl/pipeline.py,10,5,https://docs.example.com/SPRKPY1001
    SPRKPY1001,Unsupported API call,ConversionError,src/etl/pipeline.py,25,5,https://docs.example.com/SPRKPY1001
    SPRKPY1001,Unsupported API call,ConversionError,src/etl/loader.py,42,10,https://docs.example.com/SPRKPY1001
    SPRKPY1038,Complex UDF not convertible,ConversionError,src/etl/pipeline.py,55,1,https://docs.example.com/SPRKPY1038
    SPRKPY2000,Minor style issue,Warning,src/etl/pipeline.py,60,1,https://docs.example.com/SPRKPY2000
""")

DEPENDENCY_CSV = textwrap.dedent("""\
    ExecutionId,FileId,Dependency,Type,Success,StatusDetail,Arguments,Location,IndirectDependencies,TotalIndirectDependencies,DirectParents,TotalDirectParents,IndirectParents,TotalIndirectParents
    exec1,src/etl/pipeline.py,src/etl/loader.py,UserCodeFile,True,Parsed,,line 3,,,0,,0
    exec1,src/etl/pipeline.py,pandas,ThirdPartyLibraries,True,Supported,,line 1,,,0,,0
    exec1,src/etl/loader.py,spark_utils,UnknownLibraries,False,Unknown,,line 5,,,0,,0
    exec1,src/etl/loader.py,s3://bucket/data,IOSources,True,OK,,line 10,,,0,,0
""")

INPUT_FILES_CSV = textwrap.dedent("""\
    Element,ProjectId,FileId,Count,SessionId,Extension,Technology,Bytes,CharacterLength,LinesOfCode,ParseResult,Ignored,OriginFilePath
    File,proj1,src/etl/pipeline.py,1,sess1,.py,PySpark,2048,2000,80,Success,False,/original/pipeline.py
    File,proj1,src/etl/loader.py,1,sess1,.py,PySpark,1024,1000,40,Success,False,/original/loader.py
    File,proj1,src/etl/config.yaml,1,sess1,.yaml,Config,512,500,20,Skipped,True,/original/config.yaml
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workload_path(tmp_path):
    """Create a bare workload directory (no CSV or DB)."""
    return str(tmp_path)


@pytest.fixture
def workload_with_csv(tmp_path):
    """Create a workload directory with Reports/Issues.csv."""
    reports = tmp_path / "Reports"
    reports.mkdir()
    (reports / "Issues.csv").write_text(ISSUES_CSV, encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def seeded_db(workload_with_csv):
    """Initialize the database from Issues.csv and return the workload path."""
    result = sma_api.initialize_database(workload_with_csv)
    assert result.get("success") is True
    assert result.get("initialized_from_csv") is True
    return workload_with_csv


@pytest.fixture
def dependency_csv(tmp_path):
    """Write ArtifactDependencyInventory.csv and return its path."""
    csv_path = tmp_path / "ArtifactDependencyInventory.csv"
    csv_path.write_text(DEPENDENCY_CSV, encoding="utf-8")
    return str(csv_path)


@pytest.fixture
def input_files_csv(tmp_path):
    """Write InputFilesInventory.csv and return its path."""
    csv_path = tmp_path / "InputFilesInventory.csv"
    csv_path.write_text(INPUT_FILES_CSV, encoding="utf-8")
    return str(csv_path)


@pytest.fixture
def seeded_db_with_deps(seeded_db, dependency_csv):
    """Seeded DB + dependency tables loaded."""
    result = sma_api.create_artifact_dependency_tables(seeded_db, dependency_csv)
    assert result.get("success") is True
    return seeded_db


@pytest.fixture
def seeded_db_with_input_files(seeded_db, input_files_csv):
    """Seeded DB + input_files_inventory table loaded."""
    result = sma_api.create_input_files_table(seeded_db, input_files_csv)
    assert result.get("success") is True
    return seeded_db

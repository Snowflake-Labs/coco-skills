"""Unit tests for the File Information table + migration-scope taxonomy.

Covers:
  * :func:`file_info.is_migration_scope` — Category B library classification.
  * :func:`file_info.detect_eai` — AST-based network-egress detection with
    UDF-context weighting and cloud-SDK service-string checks.
  * :func:`file_info.detect_ar_required` — AR flag derivation with stdlib,
    internal, migration-scope, and Anaconda-snapshot exclusions.
  * :func:`file_info.build_file_info_row` — row assembly from per-file
    scan output.
  * :func:`scan_codebase.scan` — the ``file_info`` field is populated end
    to end.
  * :func:`assess_ir.Assessment.merge` — ``file_info`` survives the merge.
  * :mod:`adapters.prototype_v1` — renders the new section.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adapters import prototype_v1
from assess_ir import Assessment, AssessmentMetadata, FileInfoRow
import file_info as _file_info_module
from file_info import (
    _ANACONDA_SNAPSHOT,
    _MIGRATION_SCOPE,
    _load_anaconda_snapshot,
    _read_cached_anaconda_packages,
    _write_cached_anaconda_packages,
    build_file_info_row,
    detect_ar_required,
    detect_eai,
    is_migration_scope,
    refresh_anaconda_cache,
)
from scan_codebase import scan as scan_codebase


# ---------------------------------------------------------------------------
# is_migration_scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        # PySpark top-level and submodules
        "pyspark",
        "pyspark.sql",
        "pyspark.sql.functions",
        "pyspark.ml",
        "pyspark.mllib",
        "pyspark.streaming",
        "pyspark.rdd",
        "pyspark.pandas",
        # Databricks utilities and SDK
        "dbutils",
        "dbutils.fs",
        "dbutils.secrets",
        "databricks",
        "databricks.sdk",
        "databricks.connect",
        "databricks.sql",
        "databricks.feature_store",
        # Delta Lake
        "delta",
        "delta.tables",
        "deltalake",
        "delta_sharing",
        # Legacy Koalas
        "koalas",
        # Spark extension libs
        "graphframes",
        "sparknlp",
        "petastorm",
        "horovod",
        # Hive / HDFS bridges
        "pyhive",
        "snakebite",
    ],
)
def test_is_migration_scope_covers_ecosystem(name: str) -> None:
    """A comprehensive set of PySpark / Databricks / Delta / Spark-adjacent
    libraries must all be flagged as Category B (migration scope)."""
    assert is_migration_scope(name), f"{name} should be migration-scope"


@pytest.mark.parametrize(
    "name",
    [
        "pandas",
        "numpy",
        "requests",
        "sklearn",
        "boto3",
        "flask",
        "os",              # stdlib
        "collections",     # stdlib
        "my_internal_lib",
        "",                # empty
    ],
)
def test_is_migration_scope_rejects_non_scope(name: str) -> None:
    assert not is_migration_scope(name)


def test_is_migration_scope_submodule_prefix_matches() -> None:
    """A dotted import path matches when any of its prefixes is in scope."""
    assert is_migration_scope("pyspark.sql.window.Window")
    assert is_migration_scope("databricks.feature_engineering.entities.something")


# ---------------------------------------------------------------------------
# detect_eai
# ---------------------------------------------------------------------------


def test_detect_eai_clean_file() -> None:
    """A pure Spark/pandas module makes no network calls."""
    src = (
        "import pandas as pd\n"
        "import numpy as np\n"
        "from pyspark.sql import SparkSession\n"
        "def transform(df):\n"
        "    return df.groupBy('x').count()\n"
    )
    assert detect_eai(src) == "No"


def test_detect_eai_http_client_at_module_level() -> None:
    """``import requests`` at module scope → EAI required."""
    src = (
        "import requests\n"
        "resp = requests.get('https://api.example.com/data')\n"
    )
    assert detect_eai(src) == "Yes"


def test_detect_eai_urllib_stdlib() -> None:
    src = (
        "from urllib.request import urlopen\n"
        "x = urlopen('https://api.example.com')\n"
    )
    assert detect_eai(src) == "Yes"


def test_detect_eai_external_db_driver() -> None:
    """``psycopg2`` connects to an external Postgres — EAI required."""
    src = "import psycopg2\nconn = psycopg2.connect('...')\n"
    assert detect_eai(src) == "Yes"


def test_detect_eai_kafka_producer() -> None:
    src = "from kafka import KafkaProducer\np = KafkaProducer()\n"
    assert detect_eai(src) == "Yes"


def test_detect_eai_smtp_egress() -> None:
    src = "import smtplib\ns = smtplib.SMTP('smtp.example.com')\n"
    assert detect_eai(src) == "Yes"


def test_detect_eai_boto3_storage_is_not_eai() -> None:
    """``boto3.client('s3')`` is native cloud storage — NOT EAI."""
    src = (
        "import boto3\n"
        "s3 = boto3.client('s3')\n"
        "s3.get_object(Bucket='b', Key='k')\n"
    )
    assert detect_eai(src) == "No"


def test_detect_eai_boto3_compute_service_is_eai() -> None:
    """``boto3.client('lambda')`` hits AWS compute — EAI required."""
    src = (
        "import boto3\n"
        "lam = boto3.client('lambda')\n"
        "lam.invoke(FunctionName='foo')\n"
    )
    assert detect_eai(src) == "Yes"


def test_detect_eai_boto3_sagemaker_is_eai() -> None:
    src = "import boto3\nsm = boto3.client('sagemaker-runtime')\n"
    assert detect_eai(src) == "Yes"


def test_detect_eai_google_cloud_storage_is_not_eai() -> None:
    src = "from google.cloud import storage\nc = storage.Client()\n"
    # google.cloud.storage import + no service-name call → we don't flag
    # a bare storage import; only explicit client("<non-storage>") does.
    assert detect_eai(src) == "No"


def test_detect_eai_inside_udf_gets_stronger_signal() -> None:
    """Network egress inside a ``@udf`` is per-row — flag ``Yes (UDF)``."""
    src = (
        "from pyspark.sql.functions import udf\n"
        "import requests\n"
        "@udf('string')\n"
        "def enrich(x):\n"
        "    return requests.get(f'https://api/{x}').text\n"
    )
    # requests import is at module level so BOTH signals fire, but the UDF
    # variant wins for the final label.
    assert detect_eai(src) == "Yes (UDF)"


def test_detect_eai_udf_with_only_call_inside() -> None:
    """The UDF context is detected even when import lives outside the UDF."""
    src = (
        "import boto3\n"
        "from pyspark.sql.functions import pandas_udf\n"
        "@pandas_udf('string')\n"
        "def call_lambda(x):\n"
        "    boto3.client('lambda').invoke(FunctionName='f')\n"
        "    return x\n"
    )
    assert detect_eai(src) == "Yes (UDF)"


def test_detect_eai_syntax_error_returns_no() -> None:
    """Non-parseable Python (or Scala accidentally passed in) → don't false-alarm."""
    assert detect_eai("this is not valid python !!!") == "No"


# ---------------------------------------------------------------------------
# detect_ar_required
# ---------------------------------------------------------------------------


_STDLIB = frozenset({"os", "sys", "json", "collections", "typing"})


def test_ar_required_stdlib_only() -> None:
    """A file that imports only stdlib doesn't need AR."""
    assert detect_ar_required(
        ["os", "json", "collections.abc"],
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    ) == []


def test_ar_required_anaconda_only() -> None:
    """A file that imports only Anaconda-supported packages doesn't need AR."""
    assert detect_ar_required(
        ["pandas", "numpy", "pyarrow"],
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    ) == []


def test_ar_required_internal_only() -> None:
    """A file that imports only intra-workload modules doesn't need AR."""
    assert detect_ar_required(
        ["my_workload_utils", "common"],
        internal_modules={"my_workload_utils", "common"},
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    ) == []


def test_ar_required_pyspark_excluded() -> None:
    """PySpark is migration-scope and must NOT trigger AR."""
    assert detect_ar_required(
        ["pyspark", "pyspark.sql.functions"],
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    ) == []


def test_ar_required_dbutils_excluded() -> None:
    """Databricks utilities are migration-scope and must NOT trigger AR."""
    assert detect_ar_required(
        ["dbutils", "dbutils.fs", "databricks.sdk"],
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    ) == []


def test_ar_required_delta_excluded() -> None:
    assert detect_ar_required(
        ["delta", "delta.tables"],
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    ) == []


def test_ar_required_non_anaconda_package_triggers() -> None:
    """A package not in stdlib / Anaconda / migration-scope / internal → AR."""
    result = detect_ar_required(
        ["some_private_lib"],
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    )
    assert result == ["some_private_lib"]


def test_ar_required_mixed_imports_only_one_needs_triggers() -> None:
    """Even one AR-required import in a mixed file flags AR, and only that package is returned."""
    result = detect_ar_required(
        ["pandas", "numpy", "pyspark", "internal_pkg", "some_private_lib"],
        internal_modules={"internal_pkg"},
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    )
    assert result == ["some_private_lib"]


def test_ar_required_empty_imports() -> None:
    assert detect_ar_required(
        [],
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    ) == []


def test_ar_required_ignores_relative_imports() -> None:
    """Relative imports (leading dots) are always intra-project → not AR."""
    assert detect_ar_required(
        [".", ".utils", "..common"],
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
    ) == []


# ---------------------------------------------------------------------------
# build_file_info_row
# ---------------------------------------------------------------------------


def _row(**overrides) -> dict:
    """Convenience: build a row with sane defaults, override specifics."""
    kwargs = dict(
        name="job.py",
        path="job.py",
        ext=".py",
        lines=42,
        source="",
        imports=[],
        data_urls=[],
        data_formats=[],
        spark_api=0,
        internal_modules=set(),
        stdlib_modules=_STDLIB,
        anaconda_packages=_ANACONDA_SNAPSHOT,
        dag_sink_locations=[],
        dag_source_locations=[],
    )
    kwargs.update(overrides)
    return build_file_info_row(**kwargs)


def test_pure_transformer_row_shows_dataframe_both_sides() -> None:
    """A file that has Spark usage but no external I/O is ``DataFrame → DataFrame``."""
    row = _row(
        source="def f(df):\n    return df.select('a')\n",
        imports=["pyspark.sql"],
        spark_api=3,
    )
    assert row["source_system"] == ["In-Memory"]
    assert row["target_type"] == ["In-Memory"]
    assert row["target_location"] == ""


def test_utility_file_without_spark_shows_na() -> None:
    """__init__.py / config helper with no Spark usage and no I/O → all N/A."""
    row = _row(
        name="__init__.py", path="pkg/__init__.py",
        source="from .utils import helper\n",
        imports=[],
        spark_api=0,
    )
    assert row["source_system"] == ["N/A"]
    assert row["target_type"] == ["N/A"]
    assert row["target_location"] == ""


def test_ingestor_row_shows_platform_not_format() -> None:
    """A file with S3 read shows ``S3`` (platform), not ``Parquet`` (format)."""
    row = _row(
        data_urls=[("S3", "s3://bucket/prefix/file.parquet")],
        data_formats=[("Parquet", "read")],
        imports=["pyspark.sql"],
        spark_api=2,
    )
    assert row["source_system"] == ["S3"]
    assert "Parquet" not in row["source_system"]
    # A reader with no writes returns a DataFrame.
    assert row["target_type"] == ["In-Memory"]


def test_writer_with_snowflake_format_is_table_target() -> None:
    """``.write.format('snowflake')`` → Target Type = Table."""
    src = (
        "df.write.format('snowflake')\\\n"
        "    .option('dbtable', 'PROD_DB.ANALYTICS.ACCOUNTS')\\\n"
        "    .mode('overwrite').save()\n"
    )
    row = _row(
        source=src,
        imports=["pyspark.sql"],
        spark_api=2,
    )
    assert row["target_type"] == ["Snowflake Table"]
    assert row["target_location"] == "PROD_DB.ANALYTICS.ACCOUNTS"


def test_writer_with_save_as_table_extracts_fq_name() -> None:
    src = "df.write.mode('overwrite').saveAsTable('DB.SCHEMA.TBL')\n"
    row = _row(source=src, spark_api=1)
    assert row["target_type"] == ["Snowflake Table"]
    assert row["target_location"] == "DB.SCHEMA.TBL"


def test_writer_to_snowflake_named_stage_is_snowflake_stage() -> None:
    """Writes whose path starts with ``@stage_name`` are Snowflake named
    stages, semantically distinct from Cloud Storage (S3/GCS)."""
    src = "df.write.parquet('@MY_STAGE/path/out')\n"
    row = _row(source=src, spark_api=1)
    assert row["target_type"] == ["Snowflake Stage"]


def test_writer_write_stream_is_streaming_topic() -> None:
    """``.writeStream`` is a Streaming Topic target regardless of destination."""
    src = "df.writeStream.format('kafka').option('topic', 't').start()\n"
    row = _row(source=src, spark_api=1)
    assert row["target_type"] == ["Streaming Topic"]


def test_writer_kafka_producer_is_streaming_topic() -> None:
    src = (
        "from kafka import KafkaProducer\n"
        "p = KafkaProducer()\n"
        "p.send('topic', b'msg')\n"
    )
    row = _row(source=src, imports=["kafka"])
    assert row["target_type"] == ["Streaming Topic"]


def test_writer_kinesis_put_record_is_streaming_topic() -> None:
    src = (
        "import boto3\n"
        "kinesis = boto3.client('kinesis')\n"
        "kinesis.put_record(StreamName='s', Data=b'', PartitionKey='k')\n"
    )
    row = _row(source=src, imports=["boto3"])
    assert row["target_type"] == ["Streaming Topic"]


def test_writer_to_s3_is_stage_target() -> None:
    """A write to an s3://-scheme URL → Target Type = Stage, location = URL."""
    row = _row(
        data_urls=[("S3", "s3://bucket/out/")],
        data_formats=[("Parquet", "write")],
        imports=["pyspark.sql"],
        spark_api=1,
    )
    assert row["target_type"] == ["Cloud Storage"]
    assert row["target_location"] == "s3://bucket/out/"
    # Write-only file → source is DataFrame (no upstream platform).
    assert row["source_system"] == ["In-Memory"]


def test_writer_to_hdfs_is_cloud_storage_target() -> None:
    row = _row(
        data_urls=[("HDFS", "hdfs://nn/data/out")],
        data_formats=[("Parquet", "write")],
        spark_api=1,
    )
    assert row["target_type"] == ["Cloud Storage"]
    assert row["target_location"] == "hdfs://nn/data/out"


def test_smtp_use_flags_email_target() -> None:
    src = "import smtplib\ns = smtplib.SMTP('smtp.example.com')\ns.send_message(msg)\n"
    row = _row(source=src, imports=["smtplib"])
    assert row["target_type"] == ["Email"]


def test_sftp_use_flags_sftp_target() -> None:
    src = "import pysftp\nwith pysftp.Connection('host') as sftp:\n    sftp.put('f')\n"
    row = _row(source=src, imports=["pysftp"])
    assert row["target_type"] == ["SFTP"]


def test_http_post_flags_api_target() -> None:
    src = "import requests\nrequests.post('https://api.example.com/ingest', json={})\n"
    row = _row(source=src, imports=["requests"])
    assert row["target_type"] == ["API"]


def test_kafka_import_sets_source_to_kafka() -> None:
    """``import kafka`` → Source System = Kafka (platform), not a URL scheme."""
    src = "from kafka import KafkaConsumer\nc = KafkaConsumer('topic')\n"
    row = _row(source=src, imports=["kafka"], spark_api=1)
    assert row["source_system"] == ["Kafka"]


def test_postgres_driver_import_sets_source_to_jdbc_flavor() -> None:
    src = "import psycopg2\nconn = psycopg2.connect('...')\n"
    row = _row(source=src, imports=["psycopg2"])
    assert row["source_system"] == ["JDBC (PostgreSQL)"]


def test_requests_import_sets_source_to_rest_api() -> None:
    src = "import requests\nr = requests.get('https://api.example.com')\n"
    row = _row(source=src, imports=["requests"], spark_api=1)
    assert row["source_system"] == ["REST API"]


def test_filename_hint_identifies_platform_when_no_urls() -> None:
    """Class-based ETL frameworks build paths from config at runtime — no
    literal URL in the code. Fall back to filename inference: an
    ``s3_json_reader.py`` in a ``readers/`` directory reads from S3."""
    row = _row(
        name="s3_json_reader.py",
        path="src/readers/s3_json_reader.py",
        source="class S3JsonReader(BaseReader):\n    pass\n",
        imports=[],
        data_formats=[("Json", "read")],
        spark_api=1,
    )
    assert row["source_system"] == ["S3"]


def test_filename_hint_identifies_stage_target_for_s3_writer() -> None:
    row = _row(
        name="s3_parquet_writer.py",
        path="src/writers/s3_parquet_writer.py",
        source="from boto3 import client\n",
        imports=["boto3"],
        data_formats=[],
        spark_api=1,
    )
    assert row["target_type"] == ["Cloud Storage"]


def test_row_flags_jdbc_flavor_when_recognizable() -> None:
    """A JDBC URL to a known DB flavor renders as ``JDBC (PostgreSQL)``."""
    row = _row(
        data_urls=[("JDBC", "jdbc:postgresql://host:5432/db")],
        data_formats=[("Parquet", "read")],
        spark_api=1,
    )
    assert any("PostgreSQL" in s for s in row["source_system"])


def test_row_flags_eai_when_source_uses_requests() -> None:
    row = _row(
        source="import requests\nrequests.get('https://api')\n",
        imports=["requests"],
    )
    assert row["eai_required"] == "Yes"


def test_row_flags_ar_when_third_party_non_anaconda() -> None:
    row = _row(imports=["snowflake_ml_python", "custom_internal_lib"])
    # snowflake_ml_python is in the snapshot, custom_internal_lib is not.
    assert row["ar_required"] == "Yes"


def test_row_ar_na_for_non_python() -> None:
    """Scala/Java files get ``N/A`` for AR (Python-specific concept)."""
    row = _row(ext=".scala", source="", imports=["something"])
    assert row["ar_required"] == "N/A"


def test_row_eai_no_for_non_python() -> None:
    """Non-Python files don't get AST-parsed for EAI (would false-alarm)."""
    row = _row(ext=".scala", source="import requests\n", imports=["requests"])
    assert row["eai_required"] == "No"


# ---------------------------------------------------------------------------
# DAG-derived source / target location (schema_mine's resolved config paths)
# ---------------------------------------------------------------------------


def test_dag_sink_location_populates_target_for_writer_with_no_url() -> None:
    """A writer file whose I/O paths come from config (no URL in code)
    inherits the target_location + target_type from schema_mine's
    ext:sink node on the data DAG."""
    row = _row(
        name="s3_parquet_writer.py",
        path="src/writers/s3_parquet_writer.py",
        source="from boto3 import client\n",
        imports=["boto3"],
        data_formats=[("Parquet", "write")],
        spark_api=5,
        dag_sink_locations=["s3://prod-bucket/output/kipawa/"],
    )
    assert row["target_type"] == ["Cloud Storage"]
    assert row["target_location"] == "s3://prod-bucket/output/kipawa/"


def test_dag_sink_snowflake_table_gives_table_target() -> None:
    """A DAG sink node with a bare ``DB.SCHEMA.TABLE`` label indicates a
    Snowflake table write."""
    row = _row(
        source="",
        imports=[],
        spark_api=3,
        dag_sink_locations=["PROD_DB.ANALYTICS.EVENTS"],
    )
    assert row["target_type"] == ["Snowflake Table"]
    assert row["target_location"] == "PROD_DB.ANALYTICS.EVENTS"


def test_dag_sink_hdfs_gives_cloud_storage_target() -> None:
    row = _row(
        spark_api=1,
        dag_sink_locations=["hdfs://namenode/data/out/"],
    )
    assert row["target_type"] == ["Cloud Storage"]
    assert row["target_location"] == "hdfs://namenode/data/out/"


def test_dag_source_enriches_source_system_when_no_url_in_code() -> None:
    """A reader file with no URL literal but with an ext:source edge
    inherits the platform label from the DAG."""
    row = _row(
        name="s3_json_reader.py",
        path="src/readers/s3_json_reader.py",
        source="class S3JsonReader:\n    pass\n",
        imports=[],
        spark_api=3,
        dag_source_locations=["s3://prod-bucket/input/kipawa/*.json"],
    )
    assert row["source_system"] == ["S3"]


def test_dag_takes_precedence_over_filename_hint_but_agrees() -> None:
    """When both DAG and filename would produce the same platform label,
    we don't emit it twice."""
    row = _row(
        name="s3_json_reader.py",
        path="src/readers/s3_json_reader.py",
        imports=[],
        spark_api=1,
        dag_source_locations=["s3://bucket/prefix/"],
    )
    # S3 should appear once, not twice (dedup within list).
    assert row["source_system"] == ["S3"]


def test_pattern_scan_target_borrows_location_from_dag_when_available() -> None:
    """``saveAsTable(...)`` sets type=Table; if the arg is a variable and
    the DAG knows the resolved table name, use that for the location."""
    src = "df.write.mode('overwrite').saveAsTable(target_name)\n"  # variable, not literal
    row = _row(
        source=src, spark_api=2,
        dag_sink_locations=["DB.SCHEMA.RESOLVED_TABLE"],
    )
    assert row["target_type"] == ["Snowflake Table"]
    assert row["target_location"] == "DB.SCHEMA.RESOLVED_TABLE"


# ---------------------------------------------------------------------------
# scan_codebase — end-to-end
# ---------------------------------------------------------------------------


def test_scan_populates_file_info_field(tmp_path: Path) -> None:
    """The scanner emits one FileInfoRow per code file in ``file_info``."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "import pandas\n"
        "from pyspark.sql import SparkSession\n"
        "df = spark.read.parquet('s3://bucket/data')\n"
        "df.write.parquet('s3://bucket/out')\n"
    )
    (tmp_path / "src" / "transform.py").write_text(
        "from pyspark.sql import DataFrame\n"
        "def flatten(df):\n"
        "    return df.withColumn('c', df.a).filter(df.b > 0)\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    assert len(ir.file_info) == 2
    by_name = {r.name: r for r in ir.file_info}
    # main.py touches S3 → platform label, not format label.
    assert by_name["main.py"].source_system == ["S3"]
    # write to s3 → Stage target
    assert by_name["main.py"].target_type == ["Cloud Storage"]
    # transform.py is pure logic with Spark → In-Memory both sides.
    assert by_name["transform.py"].source_system == ["In-Memory"]
    assert by_name["transform.py"].target_type == ["In-Memory"]


def test_scan_utility_init_file_is_na(tmp_path: Path) -> None:
    """__init__.py files with no Spark usage default to N/A, not DataFrame."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("from .utils import helper\n")
    (tmp_path / "pkg" / "utils.py").write_text("def helper():\n    return 1\n")
    ir = scan_codebase(tmp_path, project="t")
    init_row = next(r for r in ir.file_info if r.name == "__init__.py")
    assert init_row.source_system == ["N/A"]
    assert init_row.target_type == ["N/A"]
    assert init_row.target_location == ""


def test_scan_file_info_ar_flag_pyspark_ignored(tmp_path: Path, _isolated_cache: Path) -> None:
    """A file importing ONLY pyspark + pandas gets ``AR Required = No``.

    Requires a cache with ``pandas`` seeded — pyspark is excluded as
    migration-scope regardless of cache state, but pandas needs to be
    recognized as Anaconda-supported for AR to resolve to No offline."""
    _write_cached_anaconda_packages({"pandas"})
    (tmp_path / "job.py").write_text(
        "import pandas\n"
        "from pyspark.sql import SparkSession\n"
        "x = 1\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    row = ir.file_info[0]
    assert row.ar_required == "No"


def test_scan_file_info_ar_flag_triggers_for_non_anaconda(tmp_path: Path, _isolated_cache: Path) -> None:
    """A file importing a package not in the fallback / cache gets AR=Yes.
    Uses an isolated cache + a fabricated name so the test doesn't accidentally
    hit whatever Snowflake happens to support today."""
    (tmp_path / "job.py").write_text(
        "import totally_fabricated_lib_never_in_anaconda\n"
        "x = 1\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    row = ir.file_info[0]
    assert row.ar_required == "Yes"
    assert "totally_fabricated_lib_never_in_anaconda" in row.ar_packages


def test_scan_file_info_eai_udf_context(tmp_path: Path) -> None:
    """A UDF that calls out to the network flags ``Yes (UDF)``."""
    (tmp_path / "udf_job.py").write_text(
        "from pyspark.sql.functions import udf\n"
        "import requests\n"
        "@udf('string')\n"
        "def enrich(x):\n"
        "    return requests.get(f'https://api/{x}').text\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    row = ir.file_info[0]
    assert row.eai_required == "Yes (UDF)"


def test_scan_third_party_libs_classification_migration_scope(tmp_path: Path) -> None:
    """``pyspark`` and ``dbutils`` render as migration-scope in the third-party
    lib table, distinct from plain ``unsupported``."""
    (tmp_path / "job.py").write_text(
        "import pyspark\n"
        "import dbutils\n"
        "import pandas\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    by_name = {r.name: r for r in ir.third_party_libs}
    assert by_name["pyspark"].classification == "migration-scope"
    assert by_name["dbutils"].classification == "migration-scope"


# ---------------------------------------------------------------------------
# merge preserves file_info
# ---------------------------------------------------------------------------


def test_merge_preserves_file_info_from_codebase_side() -> None:
    """When the codebase IR has file_info and the analyzer IR doesn't, merged
    IR keeps the codebase rows."""
    codebase = Assessment(
        metadata=AssessmentMetadata(project="t", mode="CODEBASE"),
        file_info=[
            FileInfoRow(
                path="a.py", name="a.py",
                source_system="S3", target_type="Cloud Storage",
                target_location="s3://b/x", eai_required="No",
                ar_required="No", lines=10,
            ),
        ],
    )
    analyzer = Assessment(
        metadata=AssessmentMetadata(project="t", mode="ANALYSIS_JSON"),
    )
    merged = codebase.merge(analyzer)
    assert len(merged.file_info) == 1
    assert merged.file_info[0].source_system == ["S3"]


def test_merge_takes_file_info_from_analyzer_if_codebase_empty() -> None:
    """Defensive: if only the analyzer side (unusually) has file_info, keep it."""
    codebase = Assessment(metadata=AssessmentMetadata(project="t", mode="CODEBASE"))
    analyzer = Assessment(
        metadata=AssessmentMetadata(project="t", mode="ANALYSIS_JSON"),
        file_info=[
            FileInfoRow(path="b.py", name="b.py", lines=5),
        ],
    )
    merged = codebase.merge(analyzer)
    assert len(merged.file_info) == 1
    assert merged.file_info[0].name == "b.py"


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def test_v1_template_renders_file_information_section() -> None:
    ir = Assessment(
        file_info=[
            FileInfoRow(
                path="src/ingest.py", name="ingest.py",
                source_system="S3", target_type="Cloud Storage",
                target_location="s3://bucket/data",
                eai_required="No", ar_required="No", lines=42,
            ),
            FileInfoRow(
                path="src/enrich.py", name="enrich.py",
                source_system="In-Memory", target_type="In-Memory",
                target_location="",
                eai_required="Yes (UDF)", ar_required="Yes", lines=88,
            ),
        ],
    )
    html = prototype_v1.render(ir)
    assert "{{ " not in html and "{%" not in html
    assert "<h2>File information</h2>" in html
    # Both rows visible.
    assert "ingest.py" in html
    assert "enrich.py" in html
    # Specific platform label — S3 (now wrapped in <span> inside <td>).
    assert "S3" in html
    # Cloud Storage as target type.
    assert "Cloud Storage" in html
    assert "s3://bucket/data" in html
    # UDF-flagged EAI gets a clickable button (same popover UX as AR Required).
    assert "Yes (UDF)" in html
    # AR Yes flagged for enrich.py — rendered as clickable button.
    assert "ar-pkg-btn" in html


def test_v1_template_file_information_precedes_per_file_compatibility() -> None:
    """File Information must render ABOVE Per-file Compatibility."""
    ir = Assessment(
        file_info=[FileInfoRow(path="a.py", name="a.py", lines=10)],
        files=[],
    )
    html = prototype_v1.render(ir)
    fi = html.index("<h2>File information</h2>")
    pf = html.index("<h2>Per-file compatibility</h2>")
    assert fi < pf, "File Information should render before Per-file Compatibility"


def test_v1_template_hides_insignificant_files_by_default() -> None:
    """Files with N/A source AND N/A target AND No EAI AND (No or N/A) AR
    are tagged as ``data-insignificant`` so the JS toggle can hide them.
    The toggle checkbox defaults to checked (hidden)."""
    ir = Assessment(
        file_info=[
            FileInfoRow(  # insignificant — package marker
                path="pkg/__init__.py", name="__init__.py",
                source_system="N/A", target_type="N/A",
                eai_required="No", ar_required="No", lines=3,
            ),
            FileInfoRow(  # significant — has an S3 read
                path="src/read.py", name="read.py",
                source_system="S3", target_type="In-Memory",
                eai_required="No", ar_required="No", lines=42,
            ),
            FileInfoRow(  # significant — AR Yes
                path="src/ml.py", name="ml.py",
                source_system="N/A", target_type="N/A",
                eai_required="No", ar_required="Yes", lines=10,
            ),
        ],
    )
    html = prototype_v1.render(ir)
    # Toggle checkbox present and checked-by-default.
    assert 'id="hide-insignificant-files"' in html
    assert 'checked' in html
    # The insignificant row is tagged.
    assert 'data-insignificant="true"' in html
    # But NOT the S3 reader, and NOT the AR-required file.
    # (Coarse assertion: only one row carries the attribute.)
    assert html.count('data-insignificant="true"') == 1
    # The counter label shows 1 hidden.
    assert '<span id="insignificant-count">1</span>' in html


def test_v1_template_shows_empty_state_when_file_info_missing() -> None:
    ir = Assessment()
    html = prototype_v1.render(ir)
    assert "<h2>File information</h2>" in html
    assert "No file-information rows available" in html


# ---------------------------------------------------------------------------
# Multiple sources / targets (Issue 1)
# ---------------------------------------------------------------------------


def test_file_reading_from_multiple_sources() -> None:
    """A file with both S3 URLs and a psycopg2 import produces two source systems."""
    row = _row(
        data_urls=[("S3", "s3://bucket/data")],
        data_formats=[("Parquet", "read")],
        imports=["psycopg2", "pyspark.sql"],
        spark_api=2,
    )
    # S3 from URL, JDBC (PostgreSQL) from import — both should appear.
    assert "S3" in row["source_system"]
    assert any("PostgreSQL" in s for s in row["source_system"])
    assert len(row["source_system"]) == 2


def test_file_writing_to_multiple_targets() -> None:
    """A file that writes to both S3 and triggers SMTP gets both targets."""
    src = (
        "import smtplib\n"
        "s = smtplib.SMTP('smtp.example.com')\n"
        "s.send_message(msg)\n"
    )
    row = _row(
        source=src,
        data_urls=[("S3", "s3://bucket/output/")],
        data_formats=[("Parquet", "write")],
        imports=["smtplib"],
        spark_api=1,
    )
    # Cloud Storage from URL + Email from SMTP pattern — both appear.
    assert "Email" in row["target_type"]
    assert "Cloud Storage" in row["target_type"]


def test_source_system_list_no_duplicates() -> None:
    """If DAG and filename hint produce the same label, it appears only once."""
    row = _row(
        name="kafka_consumer.py",
        path="src/readers/kafka_consumer.py",
        source="",
        imports=["kafka"],
        spark_api=1,
        dag_source_locations=["kafka://broker/topic"],
    )
    # kafka from import; dag_source_locations starts with 'kafka://' → same
    assert row["source_system"].count("Kafka") == 1


def test_file_info_row_coerces_legacy_string_to_list() -> None:
    """FileInfoRow built with source_system='S3' (str) auto-coerces to ['S3']."""
    from assess_ir import FileInfoRow
    fi = FileInfoRow(path="a.py", name="a.py", source_system="S3", target_type="Cloud Storage")
    assert fi.source_system == ["S3"]
    assert fi.target_type == ["Cloud Storage"]


# ---------------------------------------------------------------------------
# Third-party library roles and "No" popover (Issue 3)
# ---------------------------------------------------------------------------


def test_pyspark_classified_as_migration_scope(tmp_path: Path) -> None:
    """pyspark should be migration-scope, not unsupported."""
    (tmp_path / "main.py").write_text("from pyspark.sql import SparkSession\n")
    ir = scan_codebase(tmp_path, project="t")
    pyspark_row = next((r for r in ir.third_party_libs if r.name == "pyspark"), None)
    assert pyspark_row is not None
    assert pyspark_row.role == "migration-scope"
    # migration-scope libraries are marked not supported (needs action = rewrite)
    assert not pyspark_row.snowpark_supported
    assert "rewritten" in pyspark_row.not_supported_reason.lower()


def test_pytest_classified_as_test_only(tmp_path: Path) -> None:
    """pytest should be test-only, not unsupported."""
    (tmp_path / "test_it.py").write_text("import pytest\n")
    ir = scan_codebase(tmp_path, project="t")
    pt = next((r for r in ir.third_party_libs if r.name == "pytest"), None)
    assert pt is not None
    assert pt.role == "test-only"


def test_internal_module_not_in_third_party_table(tmp_path: Path) -> None:
    """Modules defined inside the workload (e.g. helper_function.py) should
    NOT appear in third_party_libs."""
    (tmp_path / "helper_function.py").write_text("def do(): pass\n")
    (tmp_path / "main.py").write_text("import helper_function\n")
    ir = scan_codebase(tmp_path, project="t")
    names = [r.name for r in ir.third_party_libs]
    assert "helper_function" not in names


def test_v1_template_lib_no_popover_rendered() -> None:
    """Libraries with role=runtime-third-party and no Anaconda support render
    a clickable 'No ▾' button with the reason."""
    from assess_ir import Assessment
    from assess_ir import ThirdPartyLibRow
    ir = Assessment(
        third_party_libs=[
            ThirdPartyLibRow(
                name="airflow", import_count=10,
                snowpark_supported=False, role="runtime-third-party",
                not_supported_reason="Not in Snowflake's Anaconda channel.",
            ),
            ThirdPartyLibRow(
                name="pyspark", import_count=5,
                snowpark_supported=False, role="migration-scope",
                not_supported_reason="Rewritten by the migration tool.",
            ),
            ThirdPartyLibRow(
                name="pandas", import_count=15,
                snowpark_supported=True, role="runtime-third-party",
            ),
        ]
    )
    html = prototype_v1.render(ir)
    assert "lib-no-btn" in html
    assert "Not in Snowflake" in html
    assert "Rewritten by the migration tool" in html


# ---------------------------------------------------------------------------
# Issue type / EWI codes (Issues 4 + 5)
# ---------------------------------------------------------------------------


def test_transform_analysis_uses_ewi_code_as_bucket_key() -> None:
    """Real EWI codes from analysis.json appear in IssueRow.code, not ANALYZER-H."""
    from transform_analysis import transform as transform_analysis
    findings = [
        {
            "file": "/path/foo.py", "lines": "1-1", "code": "x",
            "final_risk": 0.95, "root_cause": "dbutils not supported",
            "ewi_code": "SPRKCNTPY3100", "status_class": "Error",
        },
    ]
    ir = transform_analysis(findings, project="t")
    assert any(i.code == "SPRKCNTPY3100" for i in ir.issues)
    assert not any(i.code.startswith("ANALYZER-") for i in ir.issues)


def test_transform_analysis_lllm_only_gets_llm_label() -> None:
    """Findings without ewi_code get LLM-H/M/L as the code."""
    from transform_analysis import transform as transform_analysis
    findings = [
        {
            "file": "/path/foo.py", "lines": "1-1", "code": "x",
            "final_risk": 0.95, "root_cause": "some issue",
            "ewi_code": "", "status_class": "",
        },
    ]
    ir = transform_analysis(findings, project="t")
    assert any(i.code.startswith("LLM-") for i in ir.issues)


def test_issue_type_derives_from_status_class() -> None:
    """issue_type is derived from status_class in the EWI bucket."""
    from transform_analysis import transform as transform_analysis
    findings = [
        {"file": "/p/a.py", "lines": "1", "code": "", "final_risk": 0.9,
         "root_cause": "error", "ewi_code": "SPRKCNTPY3100", "status_class": "Error"},
        {"file": "/p/b.py", "lines": "1", "code": "", "final_risk": 0.5,
         "root_cause": "warning", "ewi_code": "SPRKCNTPY5000", "status_class": "Warning"},
        {"file": "/p/c.py", "lines": "1", "code": "", "final_risk": 0.3,
         "root_cause": "fixed", "ewi_code": "SPRKCNTPY5300", "status_class": "Fixed"},
    ]
    ir = transform_analysis(findings, project="t")
    by_code = {i.code: i for i in ir.issues}
    assert by_code["SPRKCNTPY3100"].issue_type == "Conversion"
    assert by_code["SPRKCNTPY5000"].issue_type == "Warning"
    assert by_code["SPRKCNTPY5300"].issue_type == "Fixed"


def test_v1_issue_summary_type_column_rendered() -> None:
    """Issue Summary table has a Type column and conversion rows appear first."""
    from assess_ir import Assessment, IssueRow
    ir = Assessment(
        issues=[
            IssueRow(code="SPRKCNTPY3100", description="dbutils", count=5,
                     issue_type="Conversion"),
            IssueRow(code="SPRKCNTPY5000", description="warning", count=2,
                     issue_type="Warning"),
            IssueRow(code="SPRKCNTPY5300", description="fixed", count=3,
                     issue_type="Fixed"),
        ]
    )
    html = prototype_v1.render(ir)
    # Type column header present
    assert "<th>Type</th>" in html
    # Conversion badge
    assert "badge-conversion" in html
    # Warning row tagged as non-actionable for toggle
    assert 'data-non-actionable="true"' in html
    # Conversion row should appear before warning row
    conv_pos = html.index("badge-conversion")
    warn_pos = html.index("badge-warning")
    assert conv_pos < warn_pos


# ---------------------------------------------------------------------------
# detect_eai_detail — trigger tracking
# ---------------------------------------------------------------------------


def test_detect_eai_detail_returns_triggers() -> None:
    """detect_eai_detail returns the specific package(s) that caused EAI."""
    from file_info import detect_eai_detail
    src = "import requests\nrequests.get('https://api.example.com')\n"
    verdict, triggers = detect_eai_detail(src)
    assert verdict == "Yes"
    assert "requests" in triggers


def test_detect_eai_detail_cloud_sdk_service_in_trigger() -> None:
    """boto3.client(<non-storage-service>) records service in trigger name."""
    from file_info import detect_eai_detail
    src = "import boto3\nclient = boto3.client('lambda')\n"
    verdict, triggers = detect_eai_detail(src)
    assert verdict == "Yes"
    assert any("boto3" in t and "lambda" in t for t in triggers)


def test_detect_eai_detail_no_triggers_when_clean() -> None:
    from file_info import detect_eai_detail
    verdict, triggers = detect_eai_detail("x = 1\n")
    assert verdict == "No"
    assert triggers == []


def test_file_info_row_eai_packages_populated(tmp_path: Path) -> None:
    """eai_packages is populated when EAI is required."""
    (tmp_path / "egress.py").write_text("import smtplib\nsmtplib.SMTP('host')\n")
    ir = scan_codebase(tmp_path, project="t")
    row = next(r for r in ir.file_info if r.name == "egress.py")
    assert row.eai_required in ("Yes", "Yes (UDF)")
    assert "smtplib" in row.eai_packages


# ---------------------------------------------------------------------------
# _derive_issue_type — kind and severity fallback
# ---------------------------------------------------------------------------


def test_derive_issue_type_recipe_validated_is_fixed() -> None:
    """recipe_validated findings are always Fixed regardless of status_class."""
    from transform_analysis import _derive_issue_type
    assert _derive_issue_type("", "", "some issue", "High", "recipe_validated") == "Fixed"
    assert _derive_issue_type("SPRKCNTPY9999", "Error", "x", "High", "recipe_validated") == "Fixed"


def test_derive_issue_type_severity_fallback_for_llm_only() -> None:
    """LLM-only findings (no ewi_code, no status_class) use severity as fallback."""
    from transform_analysis import _derive_issue_type
    assert _derive_issue_type("", "", "some risk", "High") == "Conversion"
    assert _derive_issue_type("", "", "advisory note", "Medium") == "Warning"
    assert _derive_issue_type("", "", "minor hint", "Low") == "Other"


def test_derive_issue_type_status_class_wins_over_severity() -> None:
    """status_class takes priority over severity — a Low-severity KB Error is Conversion."""
    from transform_analysis import _derive_issue_type
    assert _derive_issue_type("SPRKCNTPY3100", "Error", "x", "Low") == "Conversion"
    assert _derive_issue_type("SPRKCNTPY5000", "Warning", "x", "High") == "Warning"


# ---------------------------------------------------------------------------
# Issue summary: Fixed rows removed from table, tiles show row counts
# ---------------------------------------------------------------------------


def test_v1_fixed_rows_not_in_issue_table() -> None:
    """Fixed IssueRows are filtered out of the issue summary table by render().

    CSS defines ``.badge-fixed`` but the attribute ``class="badge badge-fixed"``
    should never appear in a rendered table row when Fixed issues are filtered.
    """
    from assess_ir import Assessment, IssueRow
    ir = Assessment(
        issues=[
            IssueRow(code="SPRKCNTPY3100", description="error", count=2, issue_type="Conversion"),
            IssueRow(code="SPRKCNTPY5300", description="fixed", count=5, issue_type="Fixed"),
        ]
    )
    html = prototype_v1.render(ir)
    # CSS uses .badge-fixed selector, but no table row should carry the attribute.
    assert 'class="badge badge-fixed"' not in html
    assert 'class="badge badge-conversion"' in html


def test_v1_issue_rollup_uses_row_counts_not_occurrences() -> None:
    """v1 _issue_rollup counts unique rows, not summed occurrence counts."""
    from adapters.prototype_v1 import _issue_rollup
    issues = [
        {"issue_type": "Conversion", "code": "X-H", "count": 10},
        {"issue_type": "Conversion", "code": "X-H", "count": 3},
        {"issue_type": "Warning",    "code": "X-M", "count": 7},
    ]
    result = _issue_rollup(issues)
    assert result["conversion"] == 2   # 2 rows, not 13 occurrences
    assert result["warnings"] == 1     # 1 row, not 7 occurrences


def test_v1_issue_rollup_legacy_fallback_uses_row_counts() -> None:
    """Legacy -H/-M code suffix also contributes row counts (not occurrence counts)."""
    from adapters.prototype_v1 import _issue_rollup
    issues = [
        {"issue_type": "Other", "code": "LLM-H", "count": 100},
        {"issue_type": "Other", "code": "LLM-M", "count": 50},
    ]
    result = _issue_rollup(issues)
    assert result["conversion"] == 1   # row count, not 100
    assert result["warnings"] == 1     # row count, not 50


# ---------------------------------------------------------------------------
# _update_data_sources_from_llm_edges — dedup and new row creation
# ---------------------------------------------------------------------------


def test_llm_enrichment_adds_new_read_row() -> None:
    """LLM-resolved read edge for unknown connection creates a new DataSourceRow."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from assess_ir import Assessment, LLMResolvedDataEdges, LLMResolvedEdge
    from render_assessment import _update_data_sources_from_llm_edges

    assessment = Assessment()
    assessment.llm_resolved_data_edges = LLMResolvedDataEdges(
        edges=[
            LLMResolvedEdge(
                file="src/reader.py", line=10, kind="read",
                resolved_signature="s3://my-bucket/data/input.parquet",
                resolution_type="literal_found", source="newly_discovered",
            ),
        ]
    )
    result = _update_data_sources_from_llm_edges(assessment)
    s3_row = next((r for r in result.data_sources if r.connection == "S3"), None)
    assert s3_row is not None
    assert s3_row.reads == 1
    assert "s3://my-bucket/data/input.parquet" in s3_row.read_paths


def test_llm_enrichment_deduplicates_existing_path() -> None:
    """LLM edge for a path already in read_paths does not double-count."""
    from assess_ir import Assessment, DataSourceRow, LLMResolvedDataEdges, LLMResolvedEdge
    from render_assessment import _update_data_sources_from_llm_edges

    existing = DataSourceRow(
        connection="S3", format="Parquet",
        reads=1, read_paths=["s3://bucket/data.parquet"],
    )
    assessment = Assessment(data_sources=[existing])
    assessment.llm_resolved_data_edges = LLMResolvedDataEdges(
        edges=[
            LLMResolvedEdge(
                file="src/reader.py", line=10, kind="read",
                resolved_signature="s3://bucket/data.parquet",  # already known
                resolution_type="literal_found", source="resolved_unresolved",
            ),
        ]
    )
    result = _update_data_sources_from_llm_edges(assessment)
    s3_row = next(r for r in result.data_sources if r.connection == "S3")
    assert s3_row.reads == 1      # not incremented again
    assert len(s3_row.read_paths) == 1   # not duplicated


def test_llm_enrichment_skips_neutral_edge_kinds() -> None:
    """DROP/DELETE/TRUNCATE edges (neutral) do not affect data_sources."""
    from assess_ir import Assessment, LLMResolvedDataEdges, LLMResolvedEdge
    from render_assessment import _update_data_sources_from_llm_edges

    assessment = Assessment()
    assessment.llm_resolved_data_edges = LLMResolvedDataEdges(
        edges=[
            LLMResolvedEdge(
                file="src/drop.py", line=5, kind="drop",
                resolved_signature="PROD.SCHEMA.TABLE",
                resolution_type="literal_found", source="newly_discovered",
            ),
        ]
    )
    result = _update_data_sources_from_llm_edges(assessment)
    assert result.data_sources == []


# ---------------------------------------------------------------------------
# Overview data_sources vs Additional Discovery sources_sinks_inventory
# ---------------------------------------------------------------------------


def test_overview_and_discovery_inventories_reconcile(tmp_path: Path) -> None:
    """The Additional Discovery ``sources_sinks_inventory`` rows must
    reconcile with the Overview ``data_sources`` rows — both aggregate
    over the same underlying (connection, format) buckets so a viewer
    sees numbers that agree between the two tabs.

    Regression: previously ``sources_sinks_inventory`` was 3 hardcoded
    categories with their own aggregation, which drifted from Overview.
    """
    (tmp_path / "reader.py").write_text(
        "df1 = spark.read.parquet('s3://bucket/in1')\n"
        "df2 = spark.read.parquet('s3://bucket/in2')\n"
        "df3 = spark.read.json('s3://bucket/j')\n"
    )
    (tmp_path / "writer.py").write_text(
        "df.write.parquet('s3://bucket/out1')\n"
        "df.write.parquet('s3://bucket/out2')\n"
    )
    ir = scan_codebase(tmp_path, project="t")

    # Overview data_sources: (S3, Parquet) with 2 reads + 2 writes;
    # (S3, Json) with 1 read.
    ds_reads = sum(d.reads for d in ir.data_sources)
    ds_writes = sum(d.writes for d in ir.data_sources)

    # Discovery inventory: Source rows summed == data_sources reads,
    # Sink rows summed == data_sources writes.
    inv_source_occ = sum(
        r.occurrences for r in ir.sources_sinks_inventory if r.direction == "Source"
    )
    inv_sink_occ = sum(
        r.occurrences for r in ir.sources_sinks_inventory if r.direction == "Sink"
    )
    assert inv_source_occ == ds_reads, (
        f"Discovery source occurrences ({inv_source_occ}) must match "
        f"Overview data_sources reads ({ds_reads}); previously these "
        f"drifted because of independent aggregation."
    )
    assert inv_sink_occ == ds_writes


def test_discovery_inventory_uses_platform_plus_format_labels(tmp_path: Path) -> None:
    """Inventory rows carry the same (connection, format) breakdown as
    Overview — e.g. ``"S3 Parquet"`` rather than the old ``"File-based"``
    catch-all."""
    (tmp_path / "job.py").write_text(
        "df = spark.read.parquet('s3://bucket/in')\n"
        "df.write.parquet('s3://bucket/out')\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    categories = {r.category for r in ir.sources_sinks_inventory}
    assert any("S3" in c and "Parquet" in c for c in categories), (
        f"Expected an S3 Parquet inventory row; got {categories}"
    )


def test_sources_sinks_inventory_section_not_rendered(tmp_path: Path) -> None:
    """The 'Sources & sinks inventory' section is intentionally NOT
    rendered — it duplicated the Source/target distribution table on
    the same tab. The underlying ``sources_sinks_inventory`` list is
    still populated in the IR for JSON consumers, but the v1 HTML report
    omits its rendering to keep the Additional Discovery tab focused.
    """
    (tmp_path / "job.py").write_text(
        "df = spark.read.parquet('s3://bucket/in')\n"
        "df.write.parquet('s3://bucket/out')\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    # IR still carries the data.
    assert ir.sources_sinks_inventory, "IR should still populate sources_sinks_inventory"
    # v1 template drops the section.
    assert "Sources &amp; sinks inventory" not in prototype_v1.render(ir)


def test_data_sources_write_files_disjoint_from_read_files(tmp_path: Path) -> None:
    """A reader file only appears in read_files; a writer file only in
    write_files. Previously ``files`` conflated both directions, so the
    Source/target distribution table's ``paths | length`` "Files" column
    over-reported by counting write-only paths on the sources side (and
    read-only paths on the targets side) when a single (connection,
    format) bucket had both.
    """
    (tmp_path / "reader.py").write_text(
        "df = spark.read.parquet('s3://bucket/in')\n"
    )
    (tmp_path / "writer.py").write_text(
        "df.write.parquet('s3://bucket/out')\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    row = next(d for d in ir.data_sources if d.format == "Parquet")
    assert "reader.py" in row.read_files
    assert "writer.py" in row.write_files
    assert "writer.py" not in row.read_files
    assert "reader.py" not in row.write_files
    # Paths are also split: input path is a read, output path is a write.
    assert "s3://bucket/in" in row.read_paths
    assert "s3://bucket/out" in row.write_paths
    assert "s3://bucket/out" not in row.read_paths
    assert "s3://bucket/in" not in row.write_paths


# ---------------------------------------------------------------------------
# Anaconda-package cache resolution order
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the Anaconda-cache path to a per-test tmp file so real
    ``~/.cache/snowpark-migration/`` never gets touched. Yields the cache
    file path (does NOT create it — tests decide whether to seed it)."""
    cache_dir = tmp_path / "anaconda-cache"
    cache_file = cache_dir / "anaconda_packages.json"
    monkeypatch.setattr(_file_info_module, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(_file_info_module, "_CACHE_FILE", cache_file)
    return cache_file


class _FakeRow:
    def __init__(self, name: str) -> None:
        self._name = name

    def __getitem__(self, i: int) -> str:
        assert i == 0
        return self._name


class _FakeSql:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def collect(self) -> list[_FakeRow]:
        return self._rows


class _FakeSession:
    """Minimal Snowpark-shaped session for tests: ``session.sql(...).collect()``."""

    def __init__(self, rows: list[str] | None = None, raise_on_sql: bool = False) -> None:
        self._rows = [_FakeRow(r) for r in (rows or [])]
        self._raise = raise_on_sql
        self.queries: list[str] = []

    def sql(self, q: str) -> _FakeSql:
        self.queries.append(q)
        if self._raise:
            raise RuntimeError("simulated Snowpark failure")
        return _FakeSql(self._rows)


def test_load_anaconda_snapshot_no_session_no_cache_uses_minimal_fallback(_isolated_cache: Path) -> None:
    """With no session AND no on-disk cache, the loader returns the
    minimal in-Python fallback so common packages (pandas, numpy, boto3,
    …) don't get falsely flagged AR=Yes. The fallback is intentionally
    small — anything outside it still gets AR=Yes offline."""
    assert not _isolated_cache.exists()
    result = _load_anaconda_snapshot(session=None)
    # The fallback includes well-known Anaconda staples…
    assert "pandas" in result
    assert "numpy" in result
    assert "boto3" in result
    # …but is intentionally NOT a mirror of Snowflake's whole channel —
    # 5000+ packages would defeat the point of the session-first design.
    assert len(result) < 100, (
        f"Fallback grew to {len(result)} entries — keep it minimal or "
        "callers will drift into using it as production data."
    )


def test_load_anaconda_snapshot_uses_fresh_cache_when_no_session(_isolated_cache: Path) -> None:
    """A fresh on-disk cache is preferred over the hardcoded snapshot when
    no session is supplied."""
    _write_cached_anaconda_packages({"awesome_lib", "coollib"})
    result = _load_anaconda_snapshot(session=None)
    assert "awesome_lib" in result
    assert "coollib" in result
    # The hardcoded snapshot doesn't include our test names.
    assert "awesome_lib" not in _ANACONDA_SNAPSHOT


def test_load_anaconda_snapshot_ignores_stale_cache(_isolated_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache older than ``_CACHE_TTL_DAYS`` is treated as absent."""
    # Simulate an ancient cache by writing one and then rewriting its
    # timestamp to be older than the TTL.
    _write_cached_anaconda_packages({"stale_pkg"})
    stale_payload = json.loads(_isolated_cache.read_text())
    stale_payload["generated_at"] = (
        datetime.now(timezone.utc) - timedelta(days=_file_info_module._CACHE_TTL_DAYS + 1)
    ).isoformat()
    _isolated_cache.write_text(json.dumps(stale_payload))
    result = _load_anaconda_snapshot(session=None)
    # Stale cache falls through to the minimal fallback; stale_pkg is
    # nowhere in it.
    assert "stale_pkg" not in result
    assert "pandas" in result  # confirms we're on the fallback path


def test_load_anaconda_snapshot_uses_session_and_refreshes_cache(_isolated_cache: Path) -> None:
    """A live session is queried, its result is written to the disk cache,
    and the returned set matches the query output."""
    session = _FakeSession(rows=["numpy", "pandas", "custom-lib"])
    result = _load_anaconda_snapshot(session=session)
    assert "numpy" in result
    assert "pandas" in result
    # PyPI-style hyphenated name is normalized to import root form.
    assert "custom_lib" in result
    # SQL query recorded.
    assert any("INFORMATION_SCHEMA.PACKAGES" in q for q in session.queries)
    # Cache written.
    assert _isolated_cache.is_file()
    cached = _read_cached_anaconda_packages()
    assert cached is not None
    assert "custom_lib" in cached


def test_refresh_anaconda_cache_falls_back_when_session_raises(_isolated_cache: Path) -> None:
    """A Snowpark exception during the query never propagates — the loader
    returns an empty frozenset and doesn't corrupt the cache."""
    session = _FakeSession(raise_on_sql=True)
    result = refresh_anaconda_cache(session)
    assert result == frozenset()
    # Cache untouched — no partial write.
    assert not _isolated_cache.exists()


def test_refresh_anaconda_cache_falls_back_on_empty_result(_isolated_cache: Path) -> None:
    """An empty package result returns an empty frozenset rather than
    writing an empty cache — future runs will retry the query instead of
    honoring a bogus zero-package cache."""
    session = _FakeSession(rows=[])
    result = refresh_anaconda_cache(session)
    assert result == frozenset()
    assert not _isolated_cache.exists()


def test_ar_required_uses_freshly_cached_packages(_isolated_cache: Path) -> None:
    """Populating the cache with a novel package name makes that package
    stop triggering AR — proving the cache flows into the AR decision."""
    _write_cached_anaconda_packages({"totally_new_pkg"})
    pkgs = _load_anaconda_snapshot(session=None)
    stdlib_only = frozenset({"os", "sys"})
    assert detect_ar_required(
        ["totally_new_pkg"],
        internal_modules=set(),
        stdlib_modules=stdlib_only,
        anaconda_packages=pkgs,
    ) == []


def test_scan_forwards_session_to_anaconda_loader(tmp_path: Path, _isolated_cache: Path) -> None:
    """``scan_codebase.scan(session=...)`` forwards the session all the way to
    :func:`file_info._load_anaconda_snapshot` so the SQL query fires once
    per scan and the user cache is refreshed with the fresh package set."""
    (tmp_path / "job.py").write_text(
        "import pandas\nimport custom_new_lib\nx = 1\n"
    )
    # Novel package name — NOT in the bundled default — would otherwise flip
    # AR to Yes. Passing it via a session response means the scan should
    # treat it as Anaconda-supported instead.
    session = _FakeSession(rows=["pandas", "numpy", "custom_new_lib"])
    ir = scan_codebase(tmp_path, project="t", session=session)

    # SQL query executed exactly once (per scan, not per file).
    package_queries = [q for q in session.queries if "INFORMATION_SCHEMA.PACKAGES" in q]
    assert len(package_queries) == 1

    # Cache written to the isolated location.
    assert _isolated_cache.is_file()

    # AR flag reflects the fresh set — custom_new_lib is now "Anaconda-supported".
    row = next(r for r in ir.file_info if r.name == "job.py")
    assert row.ar_required == "No"


def test_scan_without_session_uses_minimal_fallback_for_common_pkgs(tmp_path: Path, _isolated_cache: Path) -> None:
    """With no session AND no user cache, well-known packages resolve
    via the minimal in-Python fallback so the offline scan doesn't
    over-report AR=Yes for imports like ``pandas``."""
    (tmp_path / "job.py").write_text("import pandas\nx = 1\n")
    assert not _isolated_cache.exists()

    ir = scan_codebase(tmp_path, project="t")  # no session, no cache
    row = next(r for r in ir.file_info if r.name == "job.py")
    # pandas is in the minimal fallback → AR resolves to No even offline.
    assert row.ar_required == "No"


def test_scan_without_session_still_flags_uncommon_pkgs(tmp_path: Path, _isolated_cache: Path) -> None:
    """The minimal fallback covers common packages only. A truly
    uncommon third-party import still gets AR=Yes offline — the escape
    hatch is a session or ``refresh_anaconda_cache.py``."""
    (tmp_path / "job.py").write_text("import totally_obscure_lib\nx = 1\n")
    assert not _isolated_cache.exists()

    ir = scan_codebase(tmp_path, project="t")  # no session, no cache
    row = next(r for r in ir.file_info if r.name == "job.py")
    assert row.ar_required == "Yes"


def test_scan_without_session_uses_fresh_cache(tmp_path: Path, _isolated_cache: Path) -> None:
    """A pre-existing user cache is honored on an offline scan — this is
    how the normal workflow behaves: seed cache once via the refresh
    CLI, then all subsequent scans read from disk without a session."""
    _write_cached_anaconda_packages({"pandas", "numpy"})
    (tmp_path / "job.py").write_text("import pandas\nx = 1\n")

    ir = scan_codebase(tmp_path, project="t")  # no session; hits cache
    row = next(r for r in ir.file_info if r.name == "job.py")
    assert row.ar_required == "No"

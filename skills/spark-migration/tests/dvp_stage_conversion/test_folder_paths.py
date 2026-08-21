#!/usr/bin/env python3
"""
Test for folder paths containing parquet files.

Tests that the skill correctly detects and transforms folder paths,
including partitioned directories (year=2024/month=01/) and various
cloud storage protocols (S3, HDFS, GCS, Azure).
"""

import tempfile
from pathlib import Path

from embedded_path_replacer import FileScanner, PathTransformer, generate_replacements


def test_folder_path_detection():
    """Test that folder paths (directories with parquet files) are detected"""
    
    # Create test code with folder paths
    test_code = '''
import pyspark.sql as ps

# Reading from a folder containing multiple parquet files
df1 = spark.read.parquet("s3://my-bucket/data/parquet_folder/")

# Writing to a folder
df1.write.parquet("hdfs://cluster/output/results_folder/")

# Folder path in variable
input_folder = "s3://datalake/raw/sales_data/"
output_folder = "s3://datalake/processed/aggregated/"

# Reading folder with wildcard pattern
df2 = spark.read.parquet("gs://analytics/partitioned_data/year=2024/")

# Azure folder path
df3 = spark.read.parquet("abfss://container@account.dfs.core.windows.net/warehouse/tables/")

# Local folder path
df4 = spark.read.parquet("/local/data/parquet_files/")

# Relative folder path
df5 = spark.read.parquet("./data/exports/")

# Folder with Iceberg/Delta format structure
iceberg_table = spark.read.format("iceberg").load("s3://polaris-warehouse/iceberg/sales_table/")
'''
    
    # Create temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_code)
        temp_file = Path(f.name)
    
    try:
        # Scan the file
        scanner = FileScanner()
        occurrences = scanner.scan_python_file(temp_file)
        
        # Expected folder paths
        expected_paths = [
            "s3://my-bucket/data/parquet_folder/",
            "hdfs://cluster/output/results_folder/",
            "s3://datalake/raw/sales_data/",
            "s3://datalake/processed/aggregated/",
            "gs://analytics/partitioned_data/year=2024/",
            "abfss://container@account.dfs.core.windows.net/warehouse/tables/",
            "/local/data/parquet_files/",
            "./data/exports/",
            "s3://polaris-warehouse/iceberg/sales_table/",
        ]
        
        found_paths = [occ.path for occ in occurrences]
        
        print("=" * 80)
        print("Test: Folder Path Detection")
        print("=" * 80)
        print(f"\nTotal paths detected: {len(found_paths)}")
        print("\nExpected folder paths:")
        
        passed = 0
        failed = 0
        
        for expected in expected_paths:
            if expected in found_paths:
                print(f"  ✅ {expected}")
                passed += 1
            else:
                print(f"  ❌ MISSING: {expected}")
                failed += 1
        
        # Check for any unexpected extra paths (which is fine, just informational)
        extra_paths = [p for p in found_paths if p not in expected_paths]
        if extra_paths:
            print(f"\nAdditional paths detected (informational):")
            for extra in extra_paths:
                print(f"  ℹ️  {extra}")
        
        print(f"\nResult: {passed}/{len(expected_paths)} folder paths detected")
        
        return failed == 0
        
    finally:
        # Cleanup
        temp_file.unlink()


def test_folder_path_transformation():
    """Test that folder paths transform correctly to stage format"""
    
    print("\n" + "=" * 80)
    print("Test: Folder Path Transformation")
    print("=" * 80)
    
    test_cases = [
        ("s3://my-bucket/data/parquet_folder/", 
         "@MY_STAGE/s3/my-bucket/data/parquet_folder/"),
        ("hdfs://cluster/output/results_folder/", 
         "@MY_STAGE/hdfs/cluster/output/results_folder/"),
        ("gs://analytics/partitioned_data/year=2024/", 
         "@MY_STAGE/gs/analytics/partitioned_data/year=2024/"),
        ("abfss://container@account.dfs.core.windows.net/warehouse/tables/", 
         "@MY_STAGE/abfss/container@account.dfs.core.windows.net/warehouse/tables/"),
        ("/local/data/parquet_files/", 
         "@MY_STAGE/local/local/data/parquet_files/"),
        ("./data/exports/", 
         "@MY_STAGE/relative/data/exports/"),
    ]
    
    passed = 0
    failed = 0
    
    for original, expected in test_cases:
        transformed, status, reason = PathTransformer.transform_path(original, "MY_STAGE")
        
        if transformed == expected:
            print(f"  ✅ {original}")
            print(f"     → {transformed}")
            passed += 1
        else:
            print(f"  ❌ {original}")
            print(f"     Expected: {expected}")
            print(f"     Got: {transformed}")
            failed += 1
    
    print(f"\nResult: {passed}/{len(test_cases)} transformations correct")
    
    return failed == 0


def test_folder_vs_file_paths():
    """Test that both folder paths (ending with /) and file paths are detected"""
    
    test_code = '''
# Folder paths (end with /)
folder1 = spark.read.parquet("s3://bucket/data/")
folder2 = spark.read.parquet("s3://bucket/partitioned/year=2024/")

# File paths (end with file extension)
file1 = spark.read.parquet("s3://bucket/data/file.parquet")
file2 = spark.read.parquet("s3://bucket/data/sales.snappy.parquet")

# Ambiguous (no trailing slash, no extension - should still detect)
ambiguous = spark.read.parquet("s3://bucket/data")
'''
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_code)
        temp_file = Path(f.name)
    
    try:
        scanner = FileScanner()
        occurrences = scanner.scan_python_file(temp_file)
        found_paths = [occ.path for occ in occurrences]
        
        print("\n" + "=" * 80)
        print("Test: Folder vs File Path Detection")
        print("=" * 80)
        print(f"\nTotal paths detected: {len(found_paths)}")
        
        # Check folder paths
        folder_paths = [p for p in found_paths if p.endswith('/')]
        print(f"\nFolder paths (ending with /): {len(folder_paths)}")
        for fp in folder_paths:
            print(f"  ✅ {fp}")
        
        # Check file paths
        file_paths = [p for p in found_paths if '.parquet' in p and not p.endswith('/')]
        print(f"\nFile paths (with extension): {len(file_paths)}")
        for fp in file_paths:
            print(f"  ✅ {fp}")
        
        # Check ambiguous paths
        ambiguous_paths = [p for p in found_paths if not p.endswith('/') and '.parquet' not in p]
        if ambiguous_paths:
            print(f"\nAmbiguous paths (no slash, no extension): {len(ambiguous_paths)}")
            for ap in ambiguous_paths:
                print(f"  ℹ️  {ap}")
        
        # Success if we found at least the folder and file paths
        success = len(folder_paths) >= 2 and len(file_paths) >= 2
        
        if success:
            print(f"\n✅ Test passed: Both folder and file paths detected")
        else:
            print(f"\n❌ Test failed: Expected at least 2 folder paths and 2 file paths")
        
        return success
        
    finally:
        temp_file.unlink()


def test_partitioned_folder_paths():
    """Test detection of partitioned folder structures (common with parquet)"""
    
    test_code = '''
# Hive-style partitioned folders
df1 = spark.read.parquet("s3://datalake/sales/year=2024/month=01/")
df2 = spark.read.parquet("s3://datalake/events/date=2024-01-15/hour=10/")

# Multi-level partitioned structure
df3 = spark.read.parquet("s3://warehouse/tables/region=us-west/category=electronics/")

# Iceberg table with metadata folder structure
df4 = spark.read.format("iceberg").load("s3://iceberg/db/table/metadata/")

# Delta table folder structure  
df5 = spark.read.format("delta").load("s3://delta/tables/transactions/_delta_log/")
'''
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_code)
        temp_file = Path(f.name)
    
    try:
        scanner = FileScanner()
        occurrences = scanner.scan_python_file(temp_file)
        found_paths = [occ.path for occ in occurrences]
        
        print("\n" + "=" * 80)
        print("Test: Partitioned Folder Paths")
        print("=" * 80)
        print(f"\nTotal paths detected: {len(found_paths)}")
        
        # Expected partitioned paths
        expected_patterns = [
            "year=",     # Hive-style year partition
            "month=",    # Hive-style month partition
            "date=",     # Date partition
            "region=",   # Region partition
            "metadata/", # Iceberg metadata
            "_delta_log/", # Delta log
        ]
        
        detected_patterns = []
        for pattern in expected_patterns:
            matching = [p for p in found_paths if pattern in p]
            if matching:
                detected_patterns.append(pattern)
                print(f"  ✅ Found paths with '{pattern}': {len(matching)}")
                for match in matching:
                    print(f"     - {match}")
        
        success = len(detected_patterns) >= 4
        
        if success:
            print(f"\n✅ Test passed: Partitioned folder paths detected")
        else:
            print(f"\n❌ Test failed: Expected at least 4 partition patterns")
        
        return success
        
    finally:
        temp_file.unlink()


def main():
    """Run all folder path tests"""
    
    print("\n" + "=" * 80)
    print("FOLDER PATH DETECTION TEST SUITE")
    print("=" * 80)
    
    results = []
    
    # Run tests
    results.append(("Folder Path Detection", test_folder_path_detection()))
    results.append(("Folder Path Transformation", test_folder_path_transformation()))
    results.append(("Folder vs File Paths", test_folder_vs_file_paths()))
    results.append(("Partitioned Folder Paths", test_partitioned_folder_paths()))
    
    # Summary
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print("=" * 80)
    
    if all_passed:
        print("\n🎉 All folder path tests passed!")
        return 0
    else:
        print("\n❌ Some folder path tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

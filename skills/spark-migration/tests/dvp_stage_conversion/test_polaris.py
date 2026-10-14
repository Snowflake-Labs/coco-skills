#!/usr/bin/env python3
"""
Test suite for Polaris (Snowflake's open-source Iceberg catalog) path detection.

Tests that paths to Polaris/Iceberg tables are correctly detected and
transformed to Snowflake stage format.
"""

from pathlib import Path

from embedded_path_replacer import FileScanner, DetectionMethod


def test_polaris_paths():
    """Test that Polaris-specific paths are detected correctly"""
    
    test_file = Path(__file__).parent.parent / "examples" / "sample_polaris_code.py"
    
    print("=" * 80)
    print("Testing Polaris Path Detection")
    print("=" * 80)
    
    scanner = FileScanner()
    occurrences = scanner.scan_python_file(test_file)
    
    print(f"\n✅ Found {len(occurrences)} paths in Polaris test file")
    
    # Expected path patterns
    expected_patterns = {
        's3://polaris-': 'S3 Polaris paths',
        'hdfs://polaris-': 'HDFS Polaris paths',
        'gs://polaris-': 'GCS Polaris paths',
        'abfss://polaris-': 'Azure Polaris paths',
        'file:///tmp/polaris-': 'Local Polaris paths',
        './polaris-': 'Relative Polaris config paths',
        '../secrets/polaris-': 'Parent directory Polaris paths',
    }
    
    # Group paths by pattern
    pattern_counts = {pattern: 0 for pattern in expected_patterns.keys()}
    
    for occ in occurrences:
        for pattern in expected_patterns.keys():
            if pattern in occ.path:
                pattern_counts[pattern] += 1
                break
    
    # Report findings
    print("\nPath categories found:")
    for pattern, description in expected_patterns.items():
        count = pattern_counts[pattern]
        status = "✅" if count > 0 else "⚠️"
        print(f"  {status} {description}: {count} paths")
    
    # Check detection methods
    method_counts = {}
    for occ in occurrences:
        method_name = occ.detection_method.value
        method_counts[method_name] = method_counts.get(method_name, 0) + 1
    
    print("\nDetection methods used:")
    for method, count in sorted(method_counts.items()):
        print(f"  - {method}: {count} paths")
    
    # Verify key Polaris paths
    key_paths = [
        "s3://polaris-config/credentials.json",
        "s3://polaris-warehouse/iceberg/catalog/schema/table",
        "hdfs://polaris-cluster/warehouse/iceberg/fact_sales",
        "gs://polaris-bucket/iceberg/warehouse/customers",
        "abfss://polaris-container@account.dfs.core.windows.net/iceberg/tables/sales",
        "./polaris-config/catalog.json",
    ]
    
    found_paths = [occ.path for occ in occurrences]
    
    print("\nKey Polaris paths verification:")
    all_found = True
    for key_path in key_paths:
        if key_path in found_paths:
            print(f"  ✅ {key_path}")
        else:
            print(f"  ❌ MISSING: {key_path}")
            all_found = False
    
    # Check for dynamic path (f-string)
    dynamic_path = "s3://polaris-{environment}/{catalog_name}/tables/data.parquet"
    if dynamic_path in found_paths:
        print(f"\n✅ Dynamic path detected: {dynamic_path}")
    else:
        print(f"\n⚠️ Dynamic path not detected: {dynamic_path}")
    
    # Summary
    print("\n" + "=" * 80)
    if len(occurrences) >= 20 and all_found:
        print("✅ Polaris path detection test PASSED")
        print(f"   Detected {len(occurrences)} paths across multiple storage backends")
        return 0
    else:
        print("❌ Polaris path detection test FAILED")
        print(f"   Expected 20+ paths, found {len(occurrences)}")
        return 1


def test_polaris_transformation():
    """Test that Polaris paths transform correctly"""
    
    from embedded_path_replacer import PathTransformer
    
    print("\n" + "=" * 80)
    print("Testing Polaris Path Transformation")
    print("=" * 80)
    
    test_cases = [
        ("s3://polaris-warehouse/iceberg/data.parquet", 
         "@POLARIS_STAGE/s3/polaris-warehouse/iceberg/data.parquet"),
        ("hdfs://polaris-cluster/warehouse/table", 
         "@POLARIS_STAGE/hdfs/polaris-cluster/warehouse/table"),
        ("gs://polaris-bucket/iceberg/data.json", 
         "@POLARIS_STAGE/gs/polaris-bucket/iceberg/data.json"),
        ("./polaris-config/catalog.json", 
         "@POLARIS_STAGE/relative/polaris-config/catalog.json"),
        ("file:///tmp/polaris-local/test", 
         "@POLARIS_STAGE/local/tmp/polaris-local/test"),
    ]
    
    passed = 0
    failed = 0
    
    for original, expected in test_cases:
        transformed, status, reason = PathTransformer.transform_path(original, "POLARIS_STAGE")
        
        if transformed == expected:
            print(f"  ✅ {original}")
            print(f"     → {transformed}")
            passed += 1
        else:
            print(f"  ❌ {original}")
            print(f"     Expected: {expected}")
            print(f"     Got: {transformed}")
            failed += 1
    
    print(f"\nTransformation results: {passed} passed, {failed} failed")
    
    return 0 if failed == 0 else 1


def main():
    """Run all Polaris tests"""
    
    print("\n" + "=" * 80)
    print("POLARIS PATH DETECTION TEST SUITE")
    print("=" * 80)
    
    results = []
    
    # Run tests
    results.append(("Polaris Path Detection", test_polaris_paths()))
    results.append(("Polaris Transformation", test_polaris_transformation()))
    
    # Summary
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    
    all_passed = True
    for name, result in results:
        status = "✅ PASSED" if result == 0 else "❌ FAILED"
        print(f"{status}: {name}")
        if result != 0:
            all_passed = False
    
    print("=" * 80)
    
    if all_passed:
        print("\n🎉 All Polaris tests passed!")
        return 0
    else:
        print("\n❌ Some Polaris tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

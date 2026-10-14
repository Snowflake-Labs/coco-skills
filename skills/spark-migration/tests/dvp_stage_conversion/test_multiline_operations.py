#!/usr/bin/env python3
"""
Test suite for AST-based path detection including multiline operations.

Tests that the skill correctly handles multiline Spark/PySpark operations
with method chaining, backslash continuations, and complex expressions.
"""

import ast
from pathlib import Path

from embedded_path_replacer import (
    EmbeddedPathDetector, 
    DetectionMethod,
    FileScanner,
    PathOccurrence
)


def test_multiline_spark_read():
    """Test detection of multiline Spark read operations"""
    
    # Test case 1: Multiline with backslash continuation
    code1 = '''# Test file
df_csv = spark.read.format("csv").load("s3a://my-bucket/data/raw_sales.csv")
'''
    
    # Test case 2: Multiline with method chaining (no backslash) - parentheses
    code2 = '''# Test file
df_parquet = (spark.read
    .format("parquet")
    .load("s3://my-bucket/data/customers.parquet"))
'''
    
    # Test case 3: Multiline session.read with options
    code3 = '''# Test file
df_with_options = session.read.option("header", "true").option("inferSchema", "true").csv("hdfs://cluster/warehouse/sales_data.csv")
'''
    
    # Test case 4: Multiline write operation
    code4 = '''# Test file
result_df = None
result_df.write.mode("overwrite").parquet("s3://output-bucket/results/final.parquet")
'''
    
    # Test case 5: Complex multiline with multiple paths
    code5 = '''# Read from multiple sources
input_df = spark.read.format("json").load("gs://my-bucket/input/events.json")

output_df = None
output_df.write.format("parquet").mode("append").save("hdfs://cluster/output/processed.parquet")
'''
    
    test_cases = [
        (code1, "s3a://my-bucket/data/raw_sales.csv", "Test 1: Single line .format().load()"),
        (code2, "s3://my-bucket/data/customers.parquet", "Test 2: Parentheses with .load()"),
        (code3, "hdfs://cluster/warehouse/sales_data.csv", "Test 3: Chained options with .csv()"),
        (code4, "s3://output-bucket/results/final.parquet", "Test 4: Write operation chained"),
        (code5, "gs://my-bucket/input/events.json", "Test 5: Multiple operations (first path)"),
    ]
    
    detector = EmbeddedPathDetector()
    
    print("=" * 80)
    print("Testing Multiline Spark/Snowpark Operations")
    print("=" * 80)
    
    passed = 0
    failed = 0
    
    for i, (code, expected_path, description) in enumerate(test_cases, 1):
        lines = code.split('\n')
        
        try:
            # Use AST-based detection
            results = detector.detect_with_ast(code, lines)
            
            # Check if expected path was found
            found_paths = [path for path, _, _, _, _ in results]
            
            if expected_path in found_paths:
                print(f"\n✅ {description}")
                print(f"   Expected: {expected_path}")
                print(f"   Found: {expected_path}")
                # Find the detection method for this specific path
                detection_methods = [m.value for p, _, _, m, _ in results if p == expected_path]
                if detection_methods:
                    print(f"   Detection method: {detection_methods[0]}")
                passed += 1
            else:
                print(f"\n❌ {description}")
                print(f"   Expected: {expected_path}")
                print(f"   Found: {found_paths if found_paths else 'No paths detected'}")
                failed += 1
                
        except Exception as e:
            print(f"\n❌ {description}")
            print(f"   Error: {e}")
            failed += 1
    
    print("\n" + "=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)
    
    return failed == 0


def test_edge_cases():
    """Test edge cases and complex scenarios"""
    
    # Test case: Method call on result of another method
    code1 = '''
df = spark.read.format("csv").option("header", "true").load("s3://bucket/file.csv")
'''
    
    # Test case: Nested parentheses
    code2 = '''
df = (
    spark.read
        .format("parquet")
        .load("hdfs://cluster/data.parquet")
)
'''
    
    # Test case: Multiple paths in one statement
    code3 = '''
df1 = spark.read.csv("s3://bucket1/data1.csv")
df2 = spark.read.csv("s3://bucket2/data2.csv")
df3 = df1.join(df2).write.parquet("hdfs://output/result.parquet")
'''
    
    test_cases = [
        (code1, "s3://bucket/file.csv", "Edge 1: Single line with chained methods"),
        (code2, "hdfs://cluster/data.parquet", "Edge 2: Nested parentheses"),
        (code3, ["s3://bucket1/data1.csv", "s3://bucket2/data2.csv", "hdfs://output/result.parquet"], "Edge 3: Multiple paths"),
    ]
    
    detector = EmbeddedPathDetector()
    
    print("\n" + "=" * 80)
    print("Testing Edge Cases")
    print("=" * 80)
    
    passed = 0
    failed = 0
    
    for code, expected, description in test_cases:
        lines = code.split('\n')
        
        try:
            results = detector.detect_with_ast(code, lines)
            found_paths = [path for path, _, _, _, _ in results]
            
            # Handle single or multiple expected paths
            if isinstance(expected, str):
                expected = [expected]
            
            # Check if all expected paths were found
            all_found = all(exp in found_paths for exp in expected)
            
            if all_found:
                print(f"\n✅ {description}")
                print(f"   Expected: {expected}")
                print(f"   Found: {found_paths}")
                passed += 1
            else:
                print(f"\n❌ {description}")
                print(f"   Expected: {expected}")
                print(f"   Found: {found_paths}")
                failed += 1
                
        except Exception as e:
            print(f"\n❌ {description}")
            print(f"   Error: {e}")
            failed += 1
    
    print("\n" + "=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)
    
    return failed == 0


def test_file_scanner_multiline():
    """Test FileScanner with a file containing multiline operations"""
    
    sample_code = '''#!/usr/bin/env python3
"""Sample file with multiline operations"""

from snowflake.snowpark import Session

# Multiline read with backslash
sales_df = spark.read \\
    .format("csv") \\
    .option("header", "true") \\
    .load("s3a://my-bucket/data/raw_sales.csv")

# Multiline read with parentheses
customer_df = (
    session.read
        .format("parquet")
        .load("hdfs://cluster/warehouse/customers.parquet")
)

# Multiline write
result_df.write \\
    .mode("overwrite") \\
    .format("parquet") \\
    .save("s3://output/processed/final_results.parquet")

# Single line for comparison
quick_df = spark.read.json("gs://bucket/data/events.json")
'''
    
    print("\n" + "=" * 80)
    print("Testing FileScanner with Multiline Operations")
    print("=" * 80)
    
    # Create a temporary test file
    test_file = Path("test_multiline_temp.py")
    try:
        test_file.write_text(sample_code)
        
        scanner = FileScanner()
        occurrences = scanner.scan_python_file(test_file)
        
        print(f"\n✅ Found {len(occurrences)} paths in file")
        
        expected_paths = [
            "s3a://my-bucket/data/raw_sales.csv",
            "hdfs://cluster/warehouse/customers.parquet",
            "s3://output/processed/final_results.parquet",
            "gs://bucket/data/events.json"
        ]
        
        found_paths = [occ.path for occ in occurrences]
        
        all_found = True
        for expected in expected_paths:
            if expected in found_paths:
                print(f"   ✓ {expected}")
            else:
                print(f"   ✗ MISSING: {expected}")
                all_found = False
        
        if all_found:
            print(f"\n✅ All expected paths detected!")
            return True
        else:
            print(f"\n❌ Some paths were not detected")
            print(f"   Found: {found_paths}")
            return False
            
    finally:
        # Clean up
        if test_file.exists():
            test_file.unlink()


def main():
    """Run all tests"""
    print("\n" + "=" * 80)
    print("AST PARSER MULTILINE TEST SUITE")
    print("=" * 80)
    
    results = []
    
    # Run test suites
    results.append(("Multiline Operations", test_multiline_spark_read()))
    results.append(("Edge Cases", test_edge_cases()))
    results.append(("FileScanner Multiline", test_file_scanner_multiline()))
    
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
        print("\n🎉 All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

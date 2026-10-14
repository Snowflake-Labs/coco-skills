"""
Unit tests for path detection and transformation.

Tests the core functionality of embedded_path_replacer:
- Path detection using regex fallback (detect_in_line)
- Path transformation to Snowflake stage format
"""

from embedded_path_replacer import (
    EmbeddedPathDetector,
    PathTransformer,
    DetectionMethod,
    ReplacementStatus
)

def test_detection():
    """Test path detection using regex fallback (detect_in_line)
    
    Note: detect_in_line() is the regex fallback method and always returns
    REGEX_FALLBACK as the detection method. For AST-based detection with
    specific methods (SNOWPARK_READ, etc.), use detect_with_ast().
    """
    detector = EmbeddedPathDetector()
    
    # Test cases: (line, expected_path)
    # detect_in_line always returns REGEX_FALLBACK as detection method
    test_cases = [
        ('session.read.csv("s3://bucket/file.csv")', 's3://bucket/file.csv'),
        ('df.write.parquet("hdfs://cluster/data/")', 'hdfs://cluster/data/'),
        ('input_path = "gs://mybucket/input.json"', 'gs://mybucket/input.json'),
        ('pd.read_csv("/tmp/local/data.csv")', '/tmp/local/data.csv'),
    ]
    
    print("Testing regex detection (detect_in_line)...")
    for line, expected_path in test_cases:
        results = detector.detect_in_line(line)
        if results:
            path, method, quote = results[0]
            print(f"✓ Detected: {path} via {method.value}")
            assert path == expected_path, f"Expected {expected_path}, got {path}"
            # detect_in_line always returns REGEX_FALLBACK
            assert method == DetectionMethod.REGEX_FALLBACK, f"Expected REGEX_FALLBACK, got {method}"
        else:
            assert False, f"Failed to detect path in: {line}"
    print()

def test_transformation():
    """Test path transformation"""
    test_cases = [
        ('s3://company-data-lake/exports/regional_sales.csv', 'MY_STAGE', 
         '@MY_STAGE/s3/company-data-lake/exports/regional_sales.csv', ReplacementStatus.REPLACED),
        ('hdfs://cluster/warehouse/data.parquet', 'DATA_STAGE',
         '@DATA_STAGE/hdfs/cluster/warehouse/data.parquet', ReplacementStatus.REPLACED),
        ('gs://my-bucket/input/file.json', 'INPUT',
         '@INPUT/gs/my-bucket/input/file.json', ReplacementStatus.REPLACED),
        ('/tmp/local/cache.csv', 'STAGE',
         '@STAGE/local/tmp/local/cache.csv', ReplacementStatus.REPLACED),
        ('file:///data/output.parquet', 'OUTPUT',
         '@OUTPUT/local/data/output.parquet', ReplacementStatus.REPLACED),
        ('./relative/path.csv', 'REL',
         '@REL/relative/relative/path.csv', ReplacementStatus.REPLACED),
        # Dynamic paths
        ('s3://{bucket}/data/file.csv', 'STAGE',
         '@STAGE/s3/{bucket}/data/file.csv', ReplacementStatus.NEEDS_REVISION),
        ('${base_path}/data/file.csv', 'STAGE',
         None, ReplacementStatus.NEEDS_REVISION),
    ]
    
    print("Testing transformation...")
    for original, prefix, expected_path, expected_status in test_cases:
        transformed, status, reason = PathTransformer.transform_path(original, prefix)
        if transformed == expected_path and status == expected_status:
            print(f"✓ {original} → {transformed} ({status.value})")
        else:
            print(f"✗ {original}")
            print(f"  Expected: {expected_path} ({expected_status.value})")
            print(f"  Got:      {transformed} ({status.value})")
            assert False, "Transformation mismatch"
    print()

if __name__ == '__main__':
    print("=" * 60)
    print("Testing Embedded Path Replacer")
    print("=" * 60)
    print()
    
    try:
        test_detection()
        test_transformation()
        print("=" * 60)
        print("All tests passed! ✓")
        print("=" * 60)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

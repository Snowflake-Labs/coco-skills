#!/usr/bin/env python3
"""
End-to-End tests for the embedded path replacer script
Tests the complete workflow including file scanning, transformation, and reporting
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

# Get paths
SCRIPT_DIR = Path(__file__).parent.parent.parent.parent / 'skills' / 'stage-conversion'
SCRIPT_PATH = SCRIPT_DIR / 'scripts' / 'embedded_path_replacer.py'
FIXTURES_DIR = Path(__file__).parent / 'fixtures'

class Colors:
    """ANSI color codes"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_section(title: str):
    """Print a section header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.RESET}\n")

def print_success(message: str):
    """Print success message"""
    print(f"{Colors.GREEN}✓ {message}{Colors.RESET}")

def print_error(message: str):
    """Print error message"""
    print(f"{Colors.RED}✗ {message}{Colors.RESET}")

def print_info(message: str):
    """Print info message"""
    print(f"{Colors.YELLOW}ℹ {message}{Colors.RESET}")

def run_script(args: List[str], cwd: str = None) -> Tuple[int, str, str]:
    """Run the embedded path replacer script"""
    cmd = [str(SCRIPT_PATH)] + args
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True
    )
    return result.returncode, result.stdout, result.stderr

class TestE2E:
    """End-to-end tests for embedded path replacer"""
    
    def __init__(self):
        self.temp_dir = None
        self.test_files: Dict[str, Path] = {}
        self.passed = 0
        self.failed = 0
        
    def setup(self):
        """Setup test environment"""
        print_info("Setting up test environment...")
        self.temp_dir = Path(tempfile.mkdtemp(prefix='test_path_replacer_'))
        
        # Copy fixture files to temp directory
        for fixture_file in FIXTURES_DIR.glob('*'):
            if fixture_file.is_file():
                dest = self.temp_dir / fixture_file.name
                shutil.copy2(fixture_file, dest)
                self.test_files[fixture_file.name] = dest
                print_info(f"  Copied {fixture_file.name} to temp directory")
        
        print_success(f"Test environment ready: {self.temp_dir}")
        
    def teardown(self):
        """Cleanup test environment"""
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
            print_info(f"Cleaned up temp directory: {self.temp_dir}")
    
    def test_scan_only(self):
        """Test --scan-only mode"""
        print_section("Test 1: Scan Only Mode")
        
        test_file = self.test_files['sample_code.py']
        returncode, stdout, stderr = run_script(
            ['--scan-only', str(test_file)]
        )
        
        if returncode == 0:
            # Check output contains expected patterns
            if 's3://my-bucket/raw/data.csv' in stdout:
                print_success("Detected S3 paths")
            else:
                print_error("Failed to detect S3 paths")
                self.failed += 1
                return
                
            if 'hdfs://prod-cluster/warehouse/table_data.parquet' in stdout:
                print_success("Detected HDFS paths")
            else:
                print_error("Failed to detect HDFS paths")
                self.failed += 1
                return
                
            if '/tmp/temp_data.csv' in stdout:
                print_success("Detected local paths")
            else:
                print_error("Failed to detect local paths")
                self.failed += 1
                return
                
            if './config/settings.json' in stdout:
                print_success("Detected relative paths")
            else:
                print_error("Failed to detect relative paths")
                self.failed += 1
                return
            
            if 'dynamic_s3 = f"s3://data-{env}' in stdout or 's3://data-{env}' in stdout:
                print_success("Detected dynamic paths (f-strings)")
            else:
                print_error("Failed to detect dynamic paths")
                self.failed += 1
                return
            
            print_success("Scan-only mode passed")
            self.passed += 1
        else:
            print_error(f"Script failed with return code {returncode}")
            print_error(f"STDERR: {stderr}")
            self.failed += 1
    
    def test_dry_run(self):
        """Test --dry-run mode"""
        print_section("Test 2: Dry Run Mode")
        
        test_file = self.test_files['sample_code.py']
        returncode, stdout, stderr = run_script(
            ['--dry-run', '--prefix', 'TEST_STAGE', str(test_file)]
        )
        
        if returncode == 0:
            # Check transformations are shown
            if '@TEST_STAGE/s3/' in stdout:
                print_success("Shows S3 transformations")
            else:
                print_error("Missing S3 transformations")
                self.failed += 1
                return
                
            if '@TEST_STAGE/hdfs/' in stdout:
                print_success("Shows HDFS transformations")
            else:
                print_error("Missing HDFS transformations")
                self.failed += 1
                return
            
            # Check for needs_revision paths
            if 'needs_revision' in stdout.lower() or 'Paths needing revision' in stdout:
                print_success("Shows paths needing revision (dynamic paths)")
            else:
                print_error("Missing needs_revision section")
                self.failed += 1
                return
            
            # Verify file was NOT modified
            original_content = FIXTURES_DIR / 'sample_code.py'
            test_content = test_file.read_text()
            fixture_content = original_content.read_text()
            
            if test_content == fixture_content:
                print_success("File was not modified (dry-run)")
            else:
                print_error("File was modified in dry-run mode!")
                self.failed += 1
                return
            
            print_success("Dry-run mode passed")
            self.passed += 1
        else:
            print_error(f"Script failed with return code {returncode}")
            print_error(f"STDERR: {stderr}")
            self.failed += 1
    
    def test_apply_replacements(self):
        """Test applying replacements"""
        print_section("Test 3: Apply Replacements")
        
        test_file = self.test_files['sample_code.py']
        returncode, stdout, stderr = run_script(
            ['--prefix', 'PROD_STAGE', str(test_file)]
        )
        
        if returncode == 0:
            # Verify file was modified
            content = test_file.read_text()
            
            # Check static paths were replaced
            if '@PROD_STAGE/s3/my-bucket/raw/data.csv' in content:
                print_success("Replaced S3 static path")
            else:
                print_error("Failed to replace S3 static path")
                print_error(f"File content:\n{content[:500]}")
                self.failed += 1
                return
            
            if '@PROD_STAGE/hdfs/prod-cluster/warehouse' in content:
                print_success("Replaced HDFS static path")
            else:
                print_error("Failed to replace HDFS static path")
                self.failed += 1
                return
            
            if '@PROD_STAGE/local/tmp/temp_data.csv' in content:
                print_success("Replaced local path")
            else:
                print_error("Failed to replace local path")
                self.failed += 1
                return
            
            if '@PROD_STAGE/relative/config/settings.json' in content:
                print_success("Replaced relative path")
            else:
                print_error("Failed to replace relative path")
                self.failed += 1
                return
            
            # Check dynamic paths were transformed
            if '@PROD_STAGE/s3/data-{env}' in content:
                print_success("Transformed dynamic S3 path (f-string)")
            else:
                print_error("Failed to transform dynamic S3 path")
                self.failed += 1
                return
            
            # Check warning comments were added
            if '# WARNING: NEEDS MANUAL REVIEW' in content:
                print_success("Added warning comments for dynamic paths")
            else:
                print_error("Missing warning comments")
                self.failed += 1
                return
            
            if '# Original path:' in content:
                print_success("Added original path reference in warnings")
            else:
                print_error("Missing original path references")
                self.failed += 1
                return
            
            print_success("Apply replacements passed")
            self.passed += 1
        else:
            print_error(f"Script failed with return code {returncode}")
            print_error(f"STDERR: {stderr}")
            self.failed += 1
    
    def test_report_generation(self):
        """Test report generation"""
        print_section("Test 4: Report Generation")
        
        # Create a fresh copy for this test
        test_file = self.temp_dir / 'report_test.py'
        shutil.copy2(FIXTURES_DIR / 'sample_code.py', test_file)
        
        returncode, stdout, stderr = run_script(
            ['--prefix', 'REPORT_STAGE', str(test_file)]
        )
        
        if returncode == 0:
            # Check for CSV report
            csv_files = list(self.temp_dir.glob('sma_path_replacement_report_*.csv'))
            if csv_files:
                print_success(f"Generated CSV report: {csv_files[0].name}")
                
                # Verify CSV content
                csv_content = csv_files[0].read_text()
                if 'needs_revision' in csv_content:
                    print_success("CSV contains needs_revision status")
                else:
                    print_error("CSV missing needs_revision status")
                    self.failed += 1
                    return
                
                if 'Original Path' in csv_content and 'Transformed Path' in csv_content:
                    print_success("CSV has correct headers")
                else:
                    print_error("CSV missing required headers")
                    self.failed += 1
                    return
            else:
                print_error("CSV report not generated")
                self.failed += 1
                return
            
            # Check for JSON report
            json_files = list(self.temp_dir.glob('sma_path_replacement_report_*.json'))
            if json_files:
                print_success(f"Generated JSON report: {json_files[0].name}")
                
                # Verify JSON structure
                with open(json_files[0]) as f:
                    report = json.load(f)
                
                if 'metadata' in report and 'summary' in report:
                    print_success("JSON has correct structure")
                else:
                    print_error("JSON missing required sections")
                    self.failed += 1
                    return
                
                if 'paths_needs_revision' in report['metadata']:
                    print_success("JSON tracks needs_revision count")
                else:
                    print_error("JSON missing needs_revision count")
                    self.failed += 1
                    return
                
                if 'needs_revision_paths' in report:
                    print_success("JSON has needs_revision_paths section")
                else:
                    print_error("JSON missing needs_revision_paths section")
                    self.failed += 1
                    return
            else:
                print_error("JSON report not generated")
                self.failed += 1
                return
            
            print_success("Report generation passed")
            self.passed += 1
        else:
            print_error(f"Script failed with return code {returncode}")
            print_error(f"STDERR: {stderr}")
            self.failed += 1
    
    def test_notebook_processing(self):
        """Test Jupyter notebook processing"""
        print_section("Test 5: Jupyter Notebook Processing")
        
        test_file = self.test_files['sample_notebook.ipynb']
        returncode, stdout, stderr = run_script(
            ['--prefix', 'NB_STAGE', str(test_file)]
        )
        
        if returncode == 0:
            # Verify notebook was modified
            with open(test_file) as f:
                notebook = json.load(f)
            
            # Get all code cell content
            code_cells = [
                ''.join(cell['source'])
                for cell in notebook['cells']
                if cell['cell_type'] == 'code'
            ]
            all_code = '\n'.join(code_cells)
            
            # Check transformations
            if '@NB_STAGE/s3/' in all_code:
                print_success("Transformed paths in notebook")
            else:
                print_error("Failed to transform paths in notebook")
                self.failed += 1
                return
            
            # Check dynamic path handling
            if '@NB_STAGE/hdfs/{env}' in all_code:
                print_success("Transformed dynamic paths in notebook")
            else:
                print_error("Failed to transform dynamic paths in notebook")
                self.failed += 1
                return
            
            # Check warning comments
            if '# WARNING: NEEDS MANUAL REVIEW' in all_code:
                print_success("Added warnings to notebook cells")
            else:
                print_error("Missing warnings in notebook")
                self.failed += 1
                return
            
            print_success("Notebook processing passed")
            self.passed += 1
        else:
            print_error(f"Script failed with return code {returncode}")
            print_error(f"STDERR: {stderr}")
            self.failed += 1
    
    def test_no_warnings_flag(self):
        """Test --no-warnings flag"""
        print_section("Test 6: No Warnings Flag")
        
        # Create a fresh copy
        test_file = self.temp_dir / 'no_warnings_test.py'
        shutil.copy2(FIXTURES_DIR / 'sample_code.py', test_file)
        
        returncode, stdout, stderr = run_script(
            ['--prefix', 'NO_WARN_STAGE', '--no-warnings', str(test_file)]
        )
        
        if returncode == 0:
            content = test_file.read_text()
            
            # Check paths were replaced
            if '@NO_WARN_STAGE/' in content:
                print_success("Paths were replaced")
            else:
                print_error("Paths were not replaced")
                self.failed += 1
                return
            
            # Check NO warnings were added
            if '# WARNING:' not in content:
                print_success("No warning comments added (--no-warnings)")
            else:
                print_error("Warnings were added despite --no-warnings flag")
                self.failed += 1
                return
            
            print_success("No warnings flag passed")
            self.passed += 1
        else:
            print_error(f"Script failed with return code {returncode}")
            print_error(f"STDERR: {stderr}")
            self.failed += 1
    
    def run_all_tests(self):
        """Run all E2E tests"""
        print_section("Starting End-to-End Tests")
        
        try:
            self.setup()
            
            # Run tests
            self.test_scan_only()
            self.test_dry_run()
            self.test_apply_replacements()
            self.test_report_generation()
            self.test_notebook_processing()
            self.test_no_warnings_flag()
            
            # Print summary
            print_section("Test Summary")
            total = self.passed + self.failed
            print(f"Total tests: {total}")
            print_success(f"Passed: {self.passed}")
            if self.failed > 0:
                print_error(f"Failed: {self.failed}")
            else:
                print_success("All tests passed! ✓")
            
            return self.failed == 0
            
        finally:
            self.teardown()

def main():
    """Main entry point"""
    print(f"{Colors.BOLD}Embedded Path Replacer - End-to-End Tests{Colors.RESET}")
    
    # Check script exists
    if not SCRIPT_PATH.exists():
        print_error(f"Script not found: {SCRIPT_PATH}")
        return 1
    
    # Check fixtures exist
    if not FIXTURES_DIR.exists() or not list(FIXTURES_DIR.glob('*')):
        print_error(f"Fixtures not found: {FIXTURES_DIR}")
        return 1
    
    # Run tests
    tester = TestE2E()
    success = tester.run_all_tests()
    
    return 0 if success else 1

if __name__ == '__main__':
    sys.exit(main())

"""
Tests for sma-dashboard-generator EWI extractor.

Covers:
- parse_issues_csv (CSV parsing)
- aggregate_ewis (EWI aggregation by code)
- aggregate_files (file aggregation with EWI details)
- generate_summary (summary statistics)
- extract_ewi_data_from_rows (integration test)
"""

import json
import os
from pathlib import Path

import pytest

from extractors.ewi_extractor import (
    parse_issues_csv,
    aggregate_ewis,
    aggregate_files,
    generate_summary,
    extract_ewi_data_from_rows,
)


# ===========================================================================
# 1. parse_issues_csv
# ===========================================================================

class TestParseIssuesCsv:
    """Tests for CSV parsing."""

    def test_parses_all_records(self, sample_csv_path: Path):
        records = parse_issues_csv(str(sample_csv_path))
        assert len(records) == 9

    def test_extracts_code_field(self, sample_csv_path: Path):
        records = parse_issues_csv(str(sample_csv_path))
        codes = [r['code'] for r in records]
        assert 'SPRKPY-1001' in codes
        assert 'SSC-EWI-0001' in codes

    def test_extracts_file_id_field(self, sample_csv_path: Path):
        records = parse_issues_csv(str(sample_csv_path))
        file_ids = [r['file_id'] for r in records]
        assert 'src/main.py' in file_ids
        assert 'queries/test.sql' in file_ids

    def test_extracts_line_and_column(self, sample_csv_path: Path):
        records = parse_issues_csv(str(sample_csv_path))
        first_record = records[0]
        assert first_record['line'] == '10'
        assert first_record['column'] == '5'

    def test_extracts_category(self, sample_csv_path: Path):
        records = parse_issues_csv(str(sample_csv_path))
        categories = set(r['category'] for r in records)
        assert 'Conversion' in categories
        assert 'SQL' in categories


# ===========================================================================
# 2. aggregate_ewis
# ===========================================================================

class TestAggregateEwis:
    """Tests for EWI aggregation by code."""

    def test_aggregates_by_code(self, sample_ewi_records: list[dict]):
        result = aggregate_ewis(sample_ewi_records)
        codes = [ewi['code'] for ewi in result]
        assert len(codes) == 2  # SPRKPY-1001 and SSC-EWI-0001
        assert 'SPRKPY-1001' in codes
        assert 'SSC-EWI-0001' in codes

    def test_counts_occurrences(self, sample_ewi_records: list[dict]):
        result = aggregate_ewis(sample_ewi_records)
        sprkpy = next(ewi for ewi in result if ewi['code'] == 'SPRKPY-1001')
        assert sprkpy['occurrences'] == 3

    def test_collects_unique_files_affected(self, sample_ewi_records: list[dict]):
        result = aggregate_ewis(sample_ewi_records)
        sprkpy = next(ewi for ewi in result if ewi['code'] == 'SPRKPY-1001')
        # SPRKPY-1001 appears in main.py (twice) and utils.py (once)
        assert len(sprkpy['files_affected']) == 2
        assert 'src/main.py' in sprkpy['files_affected']
        assert 'src/utils.py' in sprkpy['files_affected']

    def test_files_affected_are_sorted(self, sample_ewi_records: list[dict]):
        result = aggregate_ewis(sample_ewi_records)
        sprkpy = next(ewi for ewi in result if ewi['code'] == 'SPRKPY-1001')
        assert sprkpy['files_affected'] == sorted(sprkpy['files_affected'])

    def test_results_sorted_by_code(self, sample_ewi_records: list[dict]):
        result = aggregate_ewis(sample_ewi_records)
        codes = [ewi['code'] for ewi in result]
        assert codes == sorted(codes)

    def test_sets_default_status_pending(self, sample_ewi_records: list[dict]):
        result = aggregate_ewis(sample_ewi_records)
        for ewi in result:
            assert ewi['status'] == 'pending'

    def test_handles_empty_code(self):
        records = [
            {'code': '', 'description': 'Empty', 'category': 'Test', 'file_id': 'test.py', 'line': '1', 'column': '1'},
            {'code': 'SPRKPY-1001', 'description': 'Valid', 'category': 'Test', 'file_id': 'test.py', 'line': '2', 'column': '1'},
        ]
        result = aggregate_ewis(records)
        assert len(result) == 1
        assert result[0]['code'] == 'SPRKPY-1001'


# ===========================================================================
# 3. aggregate_files
# ===========================================================================

class TestAggregateFiles:
    """Tests for file aggregation with EWI details."""

    def test_aggregates_by_file(self, sample_ewi_records: list[dict]):
        result = aggregate_files(sample_ewi_records)
        assert 'src/main.py' in result
        assert 'src/utils.py' in result
        assert 'queries/test.sql' in result

    def test_lists_ewis_per_file(self, sample_ewi_records: list[dict]):
        result = aggregate_files(sample_ewi_records)
        main_py = result['src/main.py']
        ewi_codes = [e['code'] for e in main_py['ewis']]
        assert 'SPRKPY-1001' in ewi_codes

    def test_tracks_line_numbers(self, sample_ewi_records: list[dict]):
        result = aggregate_files(sample_ewi_records)
        main_py = result['src/main.py']
        sprkpy_ewi = next(e for e in main_py['ewis'] if e['code'] == 'SPRKPY-1001')
        line_nums = [ln['line'] for ln in sprkpy_ewi['lines']]
        assert 10 in line_nums
        assert 15 in line_nums

    def test_counts_occurrences_per_file(self, sample_ewi_records: list[dict]):
        result = aggregate_files(sample_ewi_records)
        main_py = result['src/main.py']
        sprkpy_ewi = next(e for e in main_py['ewis'] if e['code'] == 'SPRKPY-1001')
        assert sprkpy_ewi['occurrences'] == 2  # Lines 10 and 15

    def test_each_line_has_status(self, sample_ewi_records: list[dict]):
        """Each line within an EWI should have its own status."""
        result = aggregate_files(sample_ewi_records)
        main_py = result['src/main.py']
        for ewi in main_py['ewis']:
            for line_info in ewi['lines']:
                assert 'line' in line_info
                assert 'status' in line_info
                assert line_info['status'] == 'pending'

    def test_sets_default_file_status_pending(self, sample_ewi_records: list[dict]):
        result = aggregate_files(sample_ewi_records)
        for file_info in result.values():
            assert file_info['file_status'] == 'pending'

    def test_handles_empty_file_id(self):
        records = [
            {'code': 'SPRKPY-1001', 'description': 'Test', 'category': 'Test', 'file_id': '', 'line': '1', 'column': '1'},
        ]
        result = aggregate_files(records)
        assert len(result) == 0


# ===========================================================================
# 4. generate_summary
# ===========================================================================

class TestGenerateSummary:
    """Tests for summary statistics generation."""

    def test_counts_by_status(self, sample_aggregated_ewis: list[dict]):
        summary = generate_summary(sample_aggregated_ewis)
        assert summary['pending'] == 1
        assert summary['manual_resolved'] == 1
        assert summary['in_progress'] == 1
        assert summary['wont_fix'] == 1

    def test_handles_empty_list(self):
        summary = generate_summary([])
        assert summary == {'pending': 0, 'in_progress': 0, 'manual_resolved': 0, 'auto_resolved': 0, 'not_auto_resolved': 0, 'wont_fix': 0}

    def test_handles_missing_status(self):
        ewis = [{'code': 'TEST-001'}]  # No status field
        summary = generate_summary(ewis)
        assert summary['pending'] == 1  # Default to pending


# ===========================================================================
# 5. extract_ewi_data_from_rows (Integration)
# ===========================================================================

class TestExtractEwiDataFromRows:
    """Integration tests for the main extraction function."""

    def test_returns_expected_keys(self, sample_ewi_records: list[dict]):
        result = extract_ewi_data_from_rows(sample_ewi_records, "test_workload")
        assert 'ewi_data' in result
        assert 'file_data' in result
        assert 'workload_name' in result

    def test_ewi_data_contains_expected_structure(self, sample_ewi_records: list[dict]):
        result = extract_ewi_data_from_rows(sample_ewi_records, "test_workload")
        ewi_data = result['ewi_data']

        assert 'generated_at' in ewi_data
        assert 'total_ewis' in ewi_data
        assert 'summary' in ewi_data
        assert 'ewis' in ewi_data
        assert isinstance(ewi_data['ewis'], list)

    def test_file_data_contains_expected_structure(self, sample_ewi_records: list[dict]):
        result = extract_ewi_data_from_rows(sample_ewi_records, "test_workload")
        file_data = result['file_data']

        assert 'generated_at' in file_data
        assert 'total_files' in file_data
        assert 'files' in file_data
        assert isinstance(file_data['files'], list)

    def test_counts_unique_ewis(self, sample_ewi_records: list[dict]):
        result = extract_ewi_data_from_rows(sample_ewi_records, "test")
        assert result['ewi_data']['total_ewis'] == 2  # SPRKPY-1001 and SSC-EWI-0001

    def test_returns_workload_name(self, sample_ewi_records: list[dict]):
        result = extract_ewi_data_from_rows(sample_ewi_records, "my_workload")
        assert result['workload_name'] == 'my_workload'

    def test_defaults_workload_name(self, sample_ewi_records: list[dict]):
        result = extract_ewi_data_from_rows(sample_ewi_records)
        assert result['workload_name'] == 'Unknown Workload'

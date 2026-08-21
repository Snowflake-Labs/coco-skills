"""
Tests for sma-dashboard-generator manager utility functions.

Covers:
- generate_category_options (category filter options)
- find_output_base (output directory detection)
"""

import os
import tempfile
from pathlib import Path

import pytest

from sma_manager import (
    generate_category_options,
    find_output_base,
)


# ===========================================================================
# 1. generate_category_options
# ===========================================================================

class TestGenerateCategoryOptions:
    """Tests for category filter options generation."""

    def test_generates_options_for_unique_categories(self):
        ewis = [
            {'code': 'A', 'category': 'Conversion'},
            {'code': 'B', 'category': 'SQL'},
            {'code': 'C', 'category': 'Conversion'},  # Duplicate
        ]
        html = generate_category_options(ewis)
        assert '<option value="Conversion">Conversion</option>' in html
        assert '<option value="SQL">SQL</option>' in html
        # Should only have 2 options, not 3
        assert html.count('<option') == 2

    def test_options_are_sorted(self):
        ewis = [
            {'code': 'A', 'category': 'Zebra'},
            {'code': 'B', 'category': 'Alpha'},
            {'code': 'C', 'category': 'Middle'},
        ]
        html = generate_category_options(ewis)
        alpha_pos = html.find('Alpha')
        middle_pos = html.find('Middle')
        zebra_pos = html.find('Zebra')
        assert alpha_pos < middle_pos < zebra_pos

    def test_handles_empty_category(self):
        ewis = [
            {'code': 'A', 'category': ''},
            {'code': 'B', 'category': 'Valid'},
        ]
        html = generate_category_options(ewis)
        # Empty category should be filtered out
        assert '<option value="">""</option>' not in html
        assert '<option value="Valid">Valid</option>' in html

    def test_handles_empty_list(self):
        html = generate_category_options([])
        assert html == ""


# ===========================================================================
# 2. find_output_base
# ===========================================================================

class TestFindOutputBase:
    """Tests for output directory detection."""

    def test_returns_working_dir_when_no_ewis(self):
        result = find_output_base([], "/working/dir")
        assert result == "/working/dir"

    def test_returns_working_dir_when_no_files_affected(self):
        ewis = [{'code': 'A', 'files_affected': []}]
        result = find_output_base(ewis, "/working/dir")
        assert result == "/working/dir"

    def test_returns_working_dir_when_file_exists_there(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "src", "main.py")
            os.makedirs(os.path.dirname(test_file), exist_ok=True)
            Path(test_file).touch()

            ewis = [{'code': 'A', 'files_affected': ['src/main.py']}]
            result = find_output_base(ewis, tmpdir)
            assert result == tmpdir

    def test_returns_output_subdir_when_file_exists_there(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "Output")
            test_file = os.path.join(output_dir, "src", "main.py")
            os.makedirs(os.path.dirname(test_file), exist_ok=True)
            Path(test_file).touch()

            ewis = [{'code': 'A', 'files_affected': ['src/main.py']}]
            result = find_output_base(ewis, tmpdir)
            assert result == output_dir

    def test_returns_output_dir_as_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "Output")
            os.makedirs(output_dir, exist_ok=True)

            ewis = [{'code': 'A', 'files_affected': ['nonexistent/file.py']}]
            result = find_output_base(ewis, tmpdir)
            assert result == output_dir

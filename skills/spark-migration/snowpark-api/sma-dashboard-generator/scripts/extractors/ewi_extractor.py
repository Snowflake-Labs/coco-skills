#!/usr/bin/env python3
"""
EWI Extractor - Extract and aggregate EWI data from Issues.csv or SQLite
"""

import csv
import os
from collections import defaultdict
from datetime import datetime


def normalize_path(path: str) -> str:
    """Normalize path to use forward slashes consistently and remove leading slashes."""
    if not path:
        return ''
    # Convert backslashes to forward slashes and remove leading slashes
    return path.replace('\\', '/').lstrip('/')


def parse_issues_csv(csv_path: str) -> list[dict]:
    """Parse the Issues.csv file and extract all EWI records."""
    records = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                'code': row.get('Code', ''),
                'description': row.get('Description', ''),
                'category': row.get('Category', 'None'),
                'file_id': normalize_path(row.get('FileId', '')),
                'line': row.get('Line', ''),
                'column': row.get('Column', ''),
                'url': row.get('Url', ''),
            })
    return records


def normalize_row(row: dict) -> dict:
    """
    Normalize a row dictionary to handle different column naming conventions.
    Supports both CSV format (Code, Description) and SQLite format (Code, code, etc.)
    """
    # Map of possible column names to normalized names
    column_mappings = {
        'code': ['Code', 'code', 'CODE'],
        'description': ['Description', 'description', 'DESCRIPTION'],
        'category': ['Category', 'category', 'CATEGORY'],
        'file_id': ['FileId', 'file_id', 'fileid', 'FILEID', 'File_Id'],
        'line': ['Line', 'line', 'LINE'],
        'column': ['Column', 'column', 'COLUMN'],
        'url': ['Url', 'url', 'URL'],
        'status': ['status', 'Status', 'STATUS'],
        'notes': ['notes', 'Notes', 'NOTES'],
    }
    
    normalized = {}
    for target_key, possible_keys in column_mappings.items():
        value = ''
        for key in possible_keys:
            if key in row and row[key] is not None:
                value = row[key]
                break
        # Ensure all values are strings (SQLite may return integers for some columns)
        normalized[target_key] = str(value) if value is not None else ''
    
    return normalized


def parse_issues_rows(rows: list[dict]) -> list[dict]:
    """Parse a list of row dictionaries (from CSV or SQLite) and normalize them."""
    records = []
    for row in rows:
        normalized = normalize_row(row)
        records.append({
            'code': normalized.get('code', ''),
            'description': normalized.get('description', ''),
            'category': normalized.get('category', 'None') or 'None',
            'file_id': normalize_path(normalized.get('file_id', '')),
            'line': normalized.get('line', ''),
            'column': normalized.get('column', ''),
            'url': normalized.get('url', ''),
            'status': normalized.get('status', 'pending') or 'pending',
            'notes': normalized.get('notes', ''),
        })
    return records


def aggregate_ewis(records: list[dict]) -> list[dict]:
    """Aggregate records by EWI code and compute statistics."""
    ewi_data = defaultdict(lambda: {
        'code': '',
        'description': '',
        'category': '',
        'url': '',
        'occurrences': 0,
        'files_affected': set(),
        'status': 'pending',
        'notes': ''
    })
    
    for record in records:
        code = record['code']
        if not code:
            continue
        ewi = ewi_data[code]
        ewi['code'] = code
        if not ewi['description']:
            ewi['description'] = record['description']
        if not ewi['category']:
            ewi['category'] = record['category'] if record['category'] else 'None'
        if not ewi['url']:
            # Use URL from CSV only
            ewi['url'] = record.get('url', '')
        ewi['occurrences'] += 1
        
        if record['file_id']:
            ewi['files_affected'].add(record['file_id'])
    
    result = []
    for code in sorted(ewi_data.keys()):
        ewi = ewi_data[code]
        ewi['files_affected'] = sorted(ewi['files_affected'])
        result.append(ewi)
    return result


def aggregate_files(records: list[dict]) -> dict:
    """Aggregate records by file and list EWIs with line numbers and status."""
    file_data = defaultdict(lambda: {
        'file_path': '',
        'file_status': 'pending',
        'total_ewis': 0,
        'ewis': []
    })
    
    file_ewi_lines = defaultdict(lambda: defaultdict(list))
    
    for record in records:
        code = record['code']
        file_id = record['file_id']
        if not code or not file_id:
            continue
        
        line = record.get('line', '')
        try:
            line_num = int(line) if line else 0
        except ValueError:
            line_num = 0
        
        # Get status from the record (from SQLite)
        status = record.get('status', 'pending') or 'pending'
        
        file_ewi_lines[file_id][code].append({
            'line': line_num,
            'status': status
        })
    
    for file_path in sorted(file_ewi_lines.keys()):
        file_info = file_data[file_path]
        file_info['file_path'] = file_path
        
        ewis_in_file = []
        all_statuses = []
        
        for code in sorted(file_ewi_lines[file_path].keys()):
            occurrences = file_ewi_lines[file_path][code]
            # Group by line number, keeping status
            line_status_map = {}
            for occ in occurrences:
                ln = occ['line']
                if ln not in line_status_map:
                    line_status_map[ln] = occ['status']
                else:
                    # If same line appears multiple times, keep non-pending status
                    if occ['status'] != 'pending':
                        line_status_map[ln] = occ['status']
            
            lines_with_status = [{'line': ln, 'status': st} for ln, st in sorted(line_status_map.items())]
            all_statuses.extend([l['status'] for l in lines_with_status])
            
            # Calculate EWI-level status based on line statuses
            line_statuses = [l['status'] for l in lines_with_status]
            unique_line_statuses = set(line_statuses)
            if len(unique_line_statuses) == 1:
                ewi_status = line_statuses[0]
            else:
                ewi_status = 'in_progress'
            
            ewis_in_file.append({
                'code': code,
                'lines': lines_with_status,
                'occurrences': len(occurrences),
                'status': ewi_status
            })
        
        file_info['ewis'] = ewis_in_file
        file_info['total_ewis'] = len(ewis_in_file)
        
        # Determine file status based on line statuses
        if all_statuses:
            unique_statuses = set(all_statuses)
            if len(unique_statuses) == 1:
                file_info['file_status'] = all_statuses[0]
            else:
                file_info['file_status'] = 'in_progress'
    
    return dict(file_data)


def generate_summary(ewis: list[dict]) -> dict:
    """Generate summary statistics."""
    summary = {'pending': 0, 'in_progress': 0, 'manual_resolved': 0, 'auto_resolved': 0, 'not_auto_resolved': 0, 'wont_fix': 0}
    for ewi in ewis:
        status = ewi.get('status', 'pending')
        if status in summary:
            summary[status] += 1
    return summary


def extract_ewi_data_from_rows(rows: list[dict], workload_name: str = None) -> dict:
    """
    Extract EWI data from a list of row dictionaries.
    
    Args:
        rows: List of dictionaries with issue data (from CSV or SQLite)
        workload_name: Optional workload name
    
    Returns:
        dict with ewi_data and file_data (no files written)
    """
    # Parse and normalize rows
    print("  Processing issue records...")
    records = parse_issues_rows(rows)
    ewis = aggregate_ewis(records)
    files = aggregate_files(records)
    
    if not ewis:
        print("  Warning: No EWI codes found in the data")
    
    # Calculate EWI status based on line statuses across all files
    ewi_line_statuses = defaultdict(list)
    for file_path, file_info in files.items():
        for ewi_info in file_info.get('ewis', []):
            code = ewi_info['code']
            for line_info in ewi_info.get('lines', []):
                ewi_line_statuses[code].append(line_info.get('status', 'pending'))
    
    # Update EWI statuses
    for ewi in ewis:
        code = ewi['code']
        if code in ewi_line_statuses:
            statuses = ewi_line_statuses[code]
            unique = set(statuses)
            if len(unique) == 1:
                ewi['status'] = statuses[0]
            else:
                ewi['status'] = 'in_progress'
    
    # Build ewi_data structure
    ewi_data = {
        'generated_at': datetime.now().isoformat(),
        'source_file': 'database',
        'workload_name': workload_name or 'Unknown Workload',
        'total_ewis': len(ewis),
        'summary': generate_summary(ewis),
        'ewis': ewis
    }
    
    # Build file_data structure
    file_data = {
        'generated_at': datetime.now().isoformat(),
        'source_file': 'database',
        'total_files': len(files),
        'files': list(files.values())
    }
    
    print(f"  Extracted {len(ewis)} unique EWIs from {len(records)} records")
    
    return {
        'ewi_data': ewi_data,
        'file_data': file_data,
        'workload_name': workload_name or 'Unknown Workload'
    }

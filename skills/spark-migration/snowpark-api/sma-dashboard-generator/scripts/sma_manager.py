#!/usr/bin/env python3
"""
SMA Dashboard Manager - Orchestrates the complete SMA dashboard workflow

Usage:
    python sma_manager.py [--output-base <path>] [--template-dir <dir>]
"""

import argparse
import csv
import json
import os
import re
import shutil
import sys
import platform
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extractors.ewi_extractor import extract_ewi_data_from_rows

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import sma_api

# EWI codes that are considered critical blockers for migration
# Based on SMA documentation - these have Category: ConversionError or ParsingError
# or explicitly state "does not have a recommended fix"
BLOCKER_EWI_CODES = {
    # Category: Conversion Error
    'PNDSPY1001',  # Element is not supported (generic unsupported Pandas)
    'PNDSPY1003',  # Element is not yet recognized
    'SPRKPY1002',  # Element is not supported (generic unsupported Spark)
    'SPRKPY1003',  # Element is not yet recognized
    'SPRKPY1032',  # Element is not defined
    'SPRKPY1038',  # Element is not yet recognized
    
    # Category: Parsing Error
    'SPRKPY1001',  # Parsing errors in code
    'SPRKPY1004',  # Parsing errors in code
    'SPRKPY1074',  # Mixed indentation (spaces and tabs)
    
    # No recommended fix available (from documentation)
    'SPRKPY1054',  # JDBC format not supported - no fix
    'SPRKPY1058',  # RuntimeConfig platform-specific keys - no fix
    'SPRKPY1067',  # Split with regex pattern - no fix for regex case
    'SPRKPY1084',  # ML element not supported - no fix
    'SPRKPY1085',  # VectorAssembler not supported - no fix
    'SPRKPY1086',  # VectorUDT not supported - no fix
    
    # SnowConvert SQL EWIs
    'SSC-EWI-0001',  # Unrecognized token - parsing error
}

# EWI codes that require review but may have workarounds
REVIEW_EWI_CODES = {
    'SPRKPY1001',  # SparkSession creation
    'SPRKPY1002',  # Configuration changes
    'SPRKPY1003',  # Context operations
}


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running. Works on Windows, Linux, and macOS."""
    if platform.system() == 'Windows':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process:
                kernel32.CloseHandle(process)
                return True
            return False
        except Exception:
            # Fallback: try tasklist command
            try:
                import subprocess
                output = subprocess.check_output(
                    ['tasklist', '/FI', f'PID eq {pid}'], 
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
                return str(pid) in output.decode()
            except Exception:
                return False
    else:
        # Unix/Linux/macOS
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we don't have permission
            return True


def check_existing_server(dashboard_dir: str) -> int | None:
    """Check if a server is already running. Returns port if running, None otherwise."""
    pid_file = os.path.join(dashboard_dir, 'server', '.server.pid')
    
    if not os.path.exists(pid_file):
        return None
    
    try:
        with open(pid_file, 'r') as f:
            content = f.read().strip()
            # Format: "pid:port"
            if ':' in content:
                pid, port = content.split(':')
                pid = int(pid)
                port = int(port)
            else:
                # Old format, just pid
                pid = int(content)
                port = 8080
        
        # Check if process is still running
        if is_process_running(pid):
            return port
        
        # Process not running, clean up PID file
        try:
            os.remove(pid_file)
        except FileNotFoundError:
            pass
        return None
        
    except (FileNotFoundError, ValueError):
        return None


def save_server_pid(dashboard_dir: str, port: int) -> None:
    """Save current process PID and port to file."""
    pid_file = os.path.join(dashboard_dir, 'server', '.server.pid')
    with open(pid_file, 'w') as f:
        f.write(f"{os.getpid()}:{port}")


def read_tool_execution_info(working_dir: str) -> dict:
    """
    Read ExecutionId and ToolVersion from tool_execution.csv.
    
    Returns a dict with:
        - 'execution_id': ExecutionId value or 'Not detected'
        - 'tool_version': ToolVersion value or 'Not detected'
    """
    result = {
        'execution_id': 'Not detected',
        'tool_version': 'Not detected'
    }
    
    # Check both possible locations
    sma_output_tool_exec = os.path.join(working_dir, 'sma-output', 'Reports', 'tool_execution.csv')
    root_tool_exec = os.path.join(working_dir, 'Reports', 'tool_execution.csv')
    
    tool_exec_csv = None
    if os.path.exists(sma_output_tool_exec):
        tool_exec_csv = sma_output_tool_exec
    elif os.path.exists(root_tool_exec):
        tool_exec_csv = root_tool_exec
    
    if not tool_exec_csv:
        return result
    
    try:
        with open(tool_exec_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Get ExecutionId if column exists
                if 'ExecutionId' in row and row['ExecutionId']:
                    result['execution_id'] = row['ExecutionId']
                # Get ToolVersion if column exists
                if 'ToolVersion' in row and row['ToolVersion']:
                    result['tool_version'] = row['ToolVersion']
                # Only need first row
                break
    except Exception:
        # Any error - just return defaults
        pass
    
    return result


def read_log_info(working_dir: str) -> dict:
    """
    Read ProjectName, OwnerEmail and OwnerCompany from log file in Logs folder.
    
    The log file has a format like: PythonSnowConvert-Log-20260222.215242.log
    
    Returns a dict with:
        - 'project_name': ProjectName value or 'Not detected'
        - 'owner_email': OwnerEmail value or 'Not detected'
        - 'owner_company': OwnerCompany value or 'Not detected'
    """
    result = {
        'project_name': 'Not detected',
        'owner_email': 'Not detected',
        'owner_company': 'Not detected'
    }
    
    # Check both possible locations for Logs folder
    sma_output_logs = os.path.join(working_dir, 'sma-output', 'Logs')
    root_logs = os.path.join(working_dir, 'Logs')
    
    logs_dir = None
    if os.path.isdir(sma_output_logs):
        logs_dir = sma_output_logs
    elif os.path.isdir(root_logs):
        logs_dir = root_logs
    
    if not logs_dir:
        return result
    
    # Find log file matching pattern PythonSnowConvert-Log-*.log
    log_file = None
    try:
        for filename in os.listdir(logs_dir):
            if filename.startswith('PythonSnowConvert-Log-') and filename.endswith('.log'):
                log_file = os.path.join(logs_dir, filename)
                break  # Use first matching file
    except Exception:
        return result
    
    if not log_file:
        return result
    
    # Read log file and extract fields
    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                # Look for patterns like "ProjectName: value" or "ProjectName=value"
                if 'ProjectName' in line:
                    match = re.search(r'ProjectName[:\s=]+(.+?)(?:\s*[,;]|\s*$)', line, re.IGNORECASE)
                    if match:
                        result['project_name'] = match.group(1).strip()
                elif 'OwnerEmail' in line:
                    match = re.search(r'OwnerEmail[:\s=]+(.+?)(?:\s*[,;]|\s*$)', line, re.IGNORECASE)
                    if match:
                        result['owner_email'] = match.group(1).strip()
                elif 'OwnerCompany' in line:
                    match = re.search(r'OwnerCompany[:\s=]+(.+?)(?:\s*[,;]|\s*$)', line, re.IGNORECASE)
                    if match:
                        result['owner_company'] = match.group(1).strip()
                
                # Stop early if we found all fields
                if (result['project_name'] != 'Not detected' and 
                    result['owner_email'] != 'Not detected' and 
                    result['owner_company'] != 'Not detected'):
                    break
    except Exception:
        # Any error - just return what we have
        pass
    
    return result


def detect_data_source(working_dir: str) -> dict:
    """
    Detect the data source for EWI issues and/or artifact dependencies.
    
    IMPORTANT: This function ONLY works with sma_storage.sqlite3.
    It will NOT create or modify any other storage files.
    
    IMPORTANT: Reports/ folder MUST be in the root of working_dir.
    The script will NOT accept sma-output/Reports/ or other nested structures.
    
    Returns a dict with:
        - 'type': 'reports_sqlite' | 'reports_csv_new' | 'dependency_only' | 'not_found'
        - 'sqlite_path': path to sma_storage.sqlite3 (if applicable)
        - 'csv_path': path to CSV file (if applicable)
        - 'artifact_dep_csv': path to ArtifactDependencyInventory.csv (if found)
        - 'input_files_csv': path to InputFilesInventory.csv (if found)
        - 'has_issues': whether issues data is available (CSV has data rows, not just header)
        - 'has_dependencies': whether dependency data is available
        - 'has_input_files': whether input files inventory is available
        - 'message': description of what was found
    """
    # Reports folder MUST be in root - no sma-output/Reports/ support
    reports_dir = os.path.join(working_dir, 'Reports')
    
    # Check for ArtifactDependencyInventory.csv (only in root Reports/)
    artifact_dep_csv = os.path.join(reports_dir, 'ArtifactDependencyInventory.csv')
    if not os.path.exists(artifact_dep_csv):
        artifact_dep_csv = None
    
    # Validate artifact dependency CSV has data (not just header)
    has_dependencies = False
    if artifact_dep_csv:
        try:
            with open(artifact_dep_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                has_dependencies = any(True for _ in reader)  # Check if at least one row exists
        except Exception:
            has_dependencies = False
            artifact_dep_csv = None
    
    # Check for InputFilesInventory.csv (only in root Reports/)
    input_files_csv = os.path.join(reports_dir, 'InputFilesInventory.csv')
    if not os.path.exists(input_files_csv):
        input_files_csv = None
    
    # Validate input files CSV has data (not just header)
    has_input_files = False
    if input_files_csv:
        try:
            with open(input_files_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                has_input_files = any(True for _ in reader)
        except Exception:
            has_input_files = False
            input_files_csv = None
    
    # sma_storage.sqlite3 path - ONLY this file will be used/created
    sma_storage_sqlite = os.path.join(working_dir, 'sma_storage.sqlite3')
    
    # Check for Issues.csv (only in root Reports/)
    issues_csv = os.path.join(reports_dir, 'Issues.csv')
    
    # Check if Issues.csv exists and has data (not just header)
    has_issues = False
    if os.path.exists(issues_csv):
        try:
            with open(issues_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                has_issues = any(True for _ in reader)  # Check if at least one data row exists
        except Exception:
            has_issues = False
    
    if has_issues:
        # Issues.csv has data
        if os.path.exists(sma_storage_sqlite):
            return {
                'type': 'reports_sqlite',
                'sqlite_path': sma_storage_sqlite,
                'csv_path': issues_csv,
                'artifact_dep_csv': artifact_dep_csv,
                'input_files_csv': input_files_csv,
                'has_issues': True,
                'has_dependencies': has_dependencies,
                'has_input_files': has_input_files,
                'message': f'Found existing database: {sma_storage_sqlite}'
            }
        
        # Issues.csv exists but NO sma_storage.sqlite3 - will create
        return {
            'type': 'reports_csv_new',
            'sqlite_path': sma_storage_sqlite,
            'csv_path': issues_csv,
            'artifact_dep_csv': artifact_dep_csv,
            'input_files_csv': input_files_csv,
            'has_issues': True,
            'has_dependencies': has_dependencies,
            'has_input_files': has_input_files,
            'message': f'Found CSV with data, will create database: {sma_storage_sqlite}'
        }
    
    # No Issues.csv data but ArtifactDependencyInventory.csv has data
    if has_dependencies:
        return {
            'type': 'dependency_only',
            'sqlite_path': sma_storage_sqlite,
            'csv_path': None,
            'artifact_dep_csv': artifact_dep_csv,
            'input_files_csv': input_files_csv,
            'has_issues': False,
            'has_dependencies': True,
            'has_input_files': has_input_files,
            'message': f'Found dependency inventory only (Issues.csv empty or not found): {artifact_dep_csv}'
        }
    
    # Nothing found with valid data
    return {
        'type': 'not_found',
        'sqlite_path': None,
        'csv_path': None,
        'artifact_dep_csv': None,
        'input_files_csv': None,
        'has_issues': False,
        'has_dependencies': False,
        'has_input_files': False,
        'message': 'Could not find valid data source. Expected Reports/ folder in root with:\n'
                   f'  - {issues_csv} (with data rows)\n'
                   f'  - {os.path.join(reports_dir, "ArtifactDependencyInventory.csv")} (with data rows)\n'
                   '  Note: sma-output/Reports/ is NOT supported. Reports/ must be in the root directory.'
    }





def read_issues_from_csv(csv_path: str) -> list[dict]:
    """
    Read issues from CSV file.
    Returns list of dictionaries with issue data.
    """
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        issues = list(reader)
    
    print(f"Loaded {len(issues)} issues from CSV")
    return issues


def find_output_base(ewis: list, working_dir: str) -> str:
    """Find where the output files are located."""
    if not ewis or not ewis[0].get('files_affected'):
        return working_dir
    
    sample_file = ewis[0]['files_affected'][0]
    
    if os.path.exists(os.path.join(working_dir, sample_file)):
        return working_dir
    
    output_path = os.path.join(working_dir, 'Output')
    if os.path.exists(os.path.join(output_path, sample_file)):
        return output_path
    
    if os.path.isdir(output_path):
        return output_path
    return working_dir


def generate_category_options(ewis: list[dict]) -> str:
    """Generate HTML options for category filter."""
    categories = sorted(set(ewi['category'] for ewi in ewis if ewi['category']))
    return '\n'.join(f'<option value="{cat}">{cat}</option>' for cat in categories)


def generate_index_html(manifest: dict, template_path: str, output_path: str, dashboard_dir: str) -> None:
    """Generate the main index.html with side panel."""
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()
    
    html = template.replace('{{MANIFEST_DATA}}', json.dumps(manifest))
    html = html.replace('{{DASHBOARD_PATH}}', dashboard_dir)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


def generate_ewi_tracker_html(ewi_data: dict, template_path: str, output_path: str, workload_name: str = "") -> None:
    """Generate the EWI tracker HTML."""
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()
    
    category_options = generate_category_options(ewi_data['ewis'])
    
    html = template.replace('{{CATEGORY_OPTIONS}}', category_options)
    html = html.replace('{{WORKLOAD_NAME}}', workload_name)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


def generate_file_tracker_html(template_path: str, output_path: str, workload_name: str = "") -> None:
    """Generate the File tracker HTML."""
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()
    
    html = template.replace('{{WORKLOAD_NAME}}', workload_name)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)



def generate_file_ewis_page(file_info: dict, file_index: int, template_path: str, output_path: str, workload_name: str, ewi_data: dict) -> None:
    """Generate a page showing all EWIs for a specific file. Data is loaded dynamically from API."""
    import html as html_module
    import json
    
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()
    
    file_path = file_info.get('file_path', '')
    file_name = os.path.basename(file_path)
    
    # Escape for HTML display
    file_name_escaped = html_module.escape(file_name)
    file_path_escaped = html_module.escape(file_path)
    workload_escaped = html_module.escape(workload_name)
    
    # Escape for JavaScript string (handle quotes and backslashes)
    file_path_js = json.dumps(file_path)[1:-1]  # Remove surrounding quotes from json.dumps
    
    html = template.replace('{{FILE_NAME}}', file_name_escaped)
    html = html.replace('{{FILE_PATH}}', file_path_escaped)
    html = html.replace('{{FILE_PATH_JS}}', file_path_js)
    html = html.replace('{{WORKLOAD_NAME}}', workload_escaped)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


def generate_files_page(ewi: dict, template_path: str, output_path: str, output_base: str, workload_name: str, file_data: dict = None) -> None:
    """Generate a file detail page for an EWI. Files list is loaded dynamically from API."""
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()
    
    # Only replace static template variables - files list is loaded dynamically via API
    html = template.replace('{{EWI_CODE}}', ewi['code'])
    html = html.replace('{{EWI_CATEGORY}}', ewi['category'])
    html = html.replace('{{EWI_DESCRIPTION}}', ewi['description'])
    html = html.replace('{{EWI_URL}}', ewi['url'])
    html = html.replace('{{PROJECT_BASE}}', output_base)
    html = html.replace('{{WORKLOAD_NAME}}', workload_name)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


def generate_dependency_detail_page(file_id: str, file_index: int, template_path: str, output_path: str) -> None:
    """Generate a dependency detail page for a specific file. Data is loaded dynamically from API."""
    import html as html_module
    import json
    
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()
    
    file_name = os.path.basename(file_id)
    
    # Escape for HTML display
    file_name_escaped = html_module.escape(file_name)
    file_path_escaped = html_module.escape(file_id)
    
    # Escape for JavaScript string (handle quotes and backslashes)
    file_path_js = json.dumps(file_id)[1:-1]  # Remove surrounding quotes from json.dumps
    
    html = template.replace('{{FILE_NAME}}', file_name_escaped)
    html = html.replace('{{FILE_PATH}}', file_path_js)
    html = html.replace('{{FILE_INDEX}}', str(file_index))
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


WORKLOAD_GITIGNORE_LINES = [
    "# Virtual environments",
    ".venv/",
    "venv/",
    ".conda-env/",
    "",
    "# Python",
    "__pycache__/",
    "*.pyc",
    "",
    "# Dashboard server",
    ".server.pid",
    "",
    "# OS",
    ".DS_Store",
]


def _ensure_workload_gitignore(working_dir: str) -> None:
    """Ensure a .gitignore exists at the workload root with common entries."""
    gitignore_path = os.path.join(working_dir, ".gitignore")
    if os.path.exists(gitignore_path):
        existing = set(open(gitignore_path).read().splitlines())
        to_add = [line for line in WORKLOAD_GITIGNORE_LINES if line not in existing]
        if to_add:
            with open(gitignore_path, "a") as f:
                f.write("\n" + "\n".join(to_add) + "\n")
    else:
        with open(gitignore_path, "w") as f:
            f.write("\n".join(WORKLOAD_GITIGNORE_LINES) + "\n")


def _ensure_vscode_settings(working_dir: str) -> None:
    """Ensure .vscode/settings.json exists at the workload root."""
    vscode_dir = os.path.join(working_dir, ".vscode")
    settings_path = os.path.join(vscode_dir, "settings.json")

    # Detect migrated suite
    migrated_path = "dvp/02-migrated"
    if os.path.isdir(os.path.join(working_dir, "dvp", "02-migrated_scos")):
        migrated_path = "dvp/02-migrated_scos"

    new_settings = {
        "python.testing.pytestEnabled": True,
        "python.testing.pytestArgs": [
            "dvp/03-tests",
            "-v",
            "--import-mode=importlib"
        ],
        "python.analysis.extraPaths": [
            "dvp/03-tests",
            "dvp/01-source",
            migrated_path
        ]
    }

    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                existing = json.load(f)
            existing.update(new_settings)
            new_settings = existing
        except Exception:
            pass

    os.makedirs(vscode_dir, exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(new_settings, f, indent=4)


def print_summary(manifest: dict, ewi_data: dict, generated_files: list[str], sqlite_path: str) -> None:
    """Print summary of the operation."""
    print("\n" + "=" * 70)
    print("SMA DASHBOARD - COMPLETE")
    print("=" * 70)
    
    print(f"\nWorkload: {manifest['workload_name']}")
    print(f"Database: {sqlite_path}")
    
    print("\nFiles Generated:")
    for f in generated_files[:10]:
        print(f"  - {f}")
    if len(generated_files) > 10:
        print(f"  ... and {len(generated_files) - 10} more files")
    
    print(f"\nEWI Summary:")
    print(f"  Total EWIs: {ewi_data['total_ewis']}")
    total_occurrences = sum(e['occurrences'] for e in ewi_data['ewis'])
    print(f"  Total Occurrences: {total_occurrences}")
    
    print("\nBy Category:")
    from collections import defaultdict
    category_counts = defaultdict(int)
    for ewi in ewi_data['ewis']:
        category_counts[ewi['category']] += 1
    for cat in sorted(category_counts.keys()):
        print(f"  {cat}: {category_counts[cat]}")
    
    print(f"\nStatus: Statuses are stored in SQLite database")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description='SMA Dashboard Manager')
    parser.add_argument('data_path', nargs='?', default=None, help='Path to Issues.csv or SQLite database (auto-detected if not provided)')
    parser.add_argument('--output-base', default=None, help='Base path for output files location')
    parser.add_argument('--template-dir', required=True, help='Directory containing HTML templates')
    parser.add_argument('--no-open', action='store_true', help='Do not open dashboard in browser')
    
    args = parser.parse_args()
    
    working_dir = os.getcwd()
    
    # Detect data source
    print("Detecting data source...")
    
    if args.data_path:
        # User provided explicit path
        if os.path.isdir(args.data_path):
            # Directory provided - use it as working directory and auto-detect
            working_dir = os.path.abspath(args.data_path)
            data_source = detect_data_source(working_dir)
        elif args.data_path.endswith('.sqlite3') or args.data_path.endswith('.db'):
            if not os.path.exists(args.data_path):
                print(f"Error: Database not found: {args.data_path}", file=sys.stderr)
                sys.exit(1)
            # Check if SQLite has issues
            has_issues_in_db = sma_api.has_issues(db_path=args.data_path)
            data_source = {
                'type': 'explicit_sqlite',
                'sqlite_path': args.data_path,
                'csv_path': None,
                'has_issues': has_issues_in_db,
                'message': f'Using specified database: {args.data_path}'
            }
        elif args.data_path.endswith('.csv'):
            if not os.path.exists(args.data_path):
                print(f"Error: CSV not found: {args.data_path}", file=sys.stderr)
                sys.exit(1)
            # For explicit CSV, create SQLite in ROOT directory (parent of Reports)
            csv_dir = os.path.dirname(args.data_path)
            # If CSV is in Reports folder, put sqlite in parent (root) directory
            if os.path.basename(csv_dir) == 'Reports':
                root_dir = os.path.dirname(csv_dir)
            else:
                root_dir = csv_dir
            sqlite_path = os.path.join(root_dir, 'sma_storage.sqlite3')
            
            # Also look for ArtifactDependencyInventory.csv in the same directory as the CSV
            artifact_dep_csv = os.path.join(csv_dir, 'ArtifactDependencyInventory.csv')
            has_dependencies = False
            if os.path.exists(artifact_dep_csv):
                try:
                    with open(artifact_dep_csv, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        has_dependencies = any(True for _ in reader)
                except Exception:
                    has_dependencies = False
                    artifact_dep_csv = None
            else:
                artifact_dep_csv = None
            
            data_source = {
                'type': 'explicit_csv',
                'sqlite_path': sqlite_path,
                'csv_path': args.data_path,
                'artifact_dep_csv': artifact_dep_csv,
                'has_issues': True,  # CSV always has issues data
                'has_dependencies': has_dependencies,
                'message': f'Using specified CSV: {args.data_path}'
            }
        else:
            print(f"Error: Unknown file type: {args.data_path}", file=sys.stderr)
            sys.exit(1)
    else:
        # Auto-detect data source
        data_source = detect_data_source(working_dir)
    
    print(f"  {data_source['message']}")
    
    # Handle data source not found
    if data_source['type'] == 'not_found':
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Data source not found                                       ║
╠══════════════════════════════════════════════════════════════╣
║  Could not find any of the following:                        ║
║                                                              ║
║  Option 1: storage.sqlite3 (with sma-output/ folder)         ║
║  Option 2: Reports/Issues.csv                                ║
║  Option 3: Reports/ArtifactDependencyInventory.csv           ║
║                                                              ║
║  Make sure you are in the correct workload directory.        ║
╚══════════════════════════════════════════════════════════════╝
""", file=sys.stderr)
        sys.exit(1)
    
    # Store the SQLite path for server
    sqlite_path = data_source['sqlite_path']
    has_issues = data_source.get('has_issues', False)
    has_dependencies = data_source.get('has_dependencies', False)
    
    # Load issues based on data source type
    issues_rows = []
    ewi_data = None
    file_data = None
    
    if data_source['type'] == 'reports_csv_new':
        sma_api.initialize_database(working_dir, db_path=sqlite_path)
        issues_rows = sma_api.read_issues_raw(db_path=sqlite_path)
        print(f"Loaded {len(issues_rows)} issues from database")
        
    elif data_source['type'] == 'reports_sqlite':
        issues_rows = sma_api.read_issues_raw(db_path=sqlite_path)
        print(f"Loaded {len(issues_rows)} issues from database")
        
    elif data_source['type'] == 'explicit_sqlite':
        issues_rows = sma_api.read_issues_raw(db_path=sqlite_path)
        print(f"Loaded {len(issues_rows)} issues from database")
        
    elif data_source['type'] == 'explicit_csv':
        if not os.path.exists(sqlite_path):
            sma_api.initialize_database(working_dir, db_path=sqlite_path)
        issues_rows = sma_api.read_issues_raw(db_path=sqlite_path)
        print(f"Loaded {len(issues_rows)} issues from database")
    
    elif data_source['type'] == 'dependency_only':
        sma_api.ensure_db(db_path=sqlite_path)
        issues_rows = []
        has_issues = False
    
    artifact_dep_csv = data_source.get('artifact_dep_csv')
    if artifact_dep_csv:
        sma_api.create_artifact_dependency_tables(working_dir, artifact_dep_csv, db_path=sqlite_path)
    else:
        print(f"  Note: ArtifactDependencyInventory.csv not found, skipping dependency tables")
        has_dependencies = False
    
    input_files_csv = data_source.get('input_files_csv')
    if input_files_csv:
        sma_api.create_input_files_table(working_dir, input_files_csv, db_path=sqlite_path)
    else:
        print(f"  Note: InputFilesInventory.csv not found, skipping input files table")
    
    # Validate templates
    template_index = os.path.join(args.template_dir, 'index.html')
    template_ewi = os.path.join(args.template_dir, 'ewi_tracker.html')
    template_file_tracker = os.path.join(args.template_dir, 'file_tracker.html')
    template_file_ewis = os.path.join(args.template_dir, 'file_ewis_detail.html')
    template_files = os.path.join(args.template_dir, 'files_detail.html')
    template_dependency = os.path.join(args.template_dir, 'dependency_tracker.html')
    template_dependency_detail = os.path.join(args.template_dir, 'dependency_detail.html')
    
    for tmpl in [template_index, template_ewi, template_file_tracker, template_file_ewis, template_files]:
        if not os.path.exists(tmpl):
            print(f"Error: Template not found: {tmpl}", file=sys.stderr)
            sys.exit(1)
    
    # Create output directory structure
    dashboard_dir = os.path.join(working_dir, 'sma-dashboard')
    ewi_tracker_dir = os.path.join(dashboard_dir, 'ewi-tracker')
    content_dir = os.path.join(ewi_tracker_dir, 'content')
    server_dir = os.path.join(dashboard_dir, 'server')
    assets_dir = os.path.join(dashboard_dir, 'assets')
    
    for d in [dashboard_dir, ewi_tracker_dir, content_dir, server_dir, assets_dir]:
        os.makedirs(d, exist_ok=True)
    
    generated_files = []
    
    # Determine workload name from path
    workload_name = os.path.basename(working_dir)
    
    # Extract EWI data if we have issues
    if has_issues and issues_rows:
        print("\n[1/5] Extracting EWI data...")
        result = extract_ewi_data_from_rows(issues_rows, workload_name)
        ewi_data = result['ewi_data']
        file_data = result['file_data']
        workload_name = result['workload_name']
    else:
        print("\n[1/5] No EWI data found, creating empty structure...")
        ewi_data = {
            'generated_at': datetime.now().isoformat(),
            'source_file': '',
            'total_ewis': 0,
            'summary': {'pending': 0, 'in_progress': 0, 'manual_resolved': 0, 'auto_resolved': 0, 'not_auto_resolved': 0, 'wont_fix': 0},
            'ewis': []
        }
        file_data = {
            'generated_at': datetime.now().isoformat(),
            'source_file': '',
            'total_files': 0,
            'files': []
        }
    
    # Find output base
    output_base = args.output_base if args.output_base else working_dir
    if has_issues and ewi_data['ewis']:
        output_base = args.output_base if args.output_base else find_output_base(ewi_data['ewis'], working_dir)
    
    # Read tool execution info and log info
    tool_info = read_tool_execution_info(working_dir)
    log_info = read_log_info(working_dir)
    
    # Create manifest.json
    print("[2/5] Creating manifest...")
    conversion_type = sma_api.get_metadata(working_dir, "conversion_type", db_path=sqlite_path)
    if not conversion_type:
        # Fallback: detect from CSV filename and EWI codes for legacy DBs
        reports_dir = os.path.join(working_dir, 'Reports')
        if os.path.exists(os.path.join(reports_dir, 'IssuesConnect.csv')):
            conversion_type = "snowpark_connect"
        elif os.path.exists(os.path.join(reports_dir, 'Issues.csv')):
            # Check EWI codes: SCOS workloads use SPRKCNTPY*/SPRKCNTSCL*
            try:
                rows = sma_api.read_issues_raw(db_path=sqlite_path)
                has_scos = any(
                    (r.get("Code") or r.get("code") or "").startswith("SPRKCNT")
                    for r in rows[:50]
                )
            except Exception:
                has_scos = False
            conversion_type = "snowpark_connect" if has_scos else "snowpark_api"
        else:
            conversion_type = "unknown"
        # Persist so future runs don't need to re-detect
        if conversion_type != "unknown":
            sma_api.set_metadata(working_dir, "conversion_type", conversion_type, db_path=sqlite_path)
    manifest = {
        'generated_at': datetime.now().isoformat(),
        'workload_name': workload_name,
        'source_path': working_dir,
        'sqlite_path': sqlite_path,
        'conversion_type': conversion_type,
        'tool_info': {
            'execution_id': tool_info['execution_id'],
            'tool_version': tool_info['tool_version']
        },
        'log_info': {
            'project_name': log_info['project_name'],
            'owner_email': log_info['owner_email'],
            'owner_company': log_info['owner_company']
        },
        'modules': {
            'overview': {
                'enabled': True,
                'has_data': True,
                'label': 'Overview',
                'icon': 'dashboard',
                'data_path': 'ewi-tracker'
            },
            'dependency_tracker': {
                'enabled': True,
                'has_data': has_dependencies,
                'label': 'Dependency Tracker',
                'icon': 'link',
                'data_path': 'ewi-tracker'
            },
            'ewi_tracker': {
                'enabled': True,
                'has_data': has_issues,
                'label': 'EWI Tracker',
                'icon': 'warning',
                'data_path': 'ewi-tracker'
            },
            'file_tracker': {
                'enabled': True,
                'has_data': has_issues,
                'label': 'File Tracker',
                'icon': 'folder',
                'data_path': 'ewi-tracker'
            },
            'test_tracker': {
                'enabled': True,
                'has_data': sma_api.has_tests(db_path=sqlite_path),
                'label': 'Test Tracker',
                'icon': 'test',
                'data_path': 'ewi-tracker'
            }
        }
    }
    
    manifest_path = os.path.join(dashboard_dir, 'manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    generated_files.append(manifest_path)
    
    # Generate index.html
    print("[3/5] Generating dashboard...")
    index_path = os.path.join(dashboard_dir, 'index.html')
    generate_index_html(manifest, template_index, index_path, dashboard_dir)
    generated_files.append(index_path)
    
    # Generate Overview HTML
    overview_template_path = os.path.join(args.template_dir, 'overview.html')
    if os.path.exists(overview_template_path):
        overview_path = os.path.join(ewi_tracker_dir, 'overview.html')
        
        # Calculate overview data
        overview_data = {
            'workload_path': working_dir,
            'workload_name': workload_name,
            'total_files': 0,
            'file_types_summary': '',
            'file_types': {},
            'total_ewis': ewi_data['total_ewis'] if ewi_data else 0,
            'ewi_occurrences': sum(e.get('occurrences', 0) for e in ewi_data.get('ewis', [])) if ewi_data else 0,
            'ewi_categories': {},
            'total_islands': 0,
            'island_files': 0,
            'ready_files': 0,
            'modules': manifest['modules'],
            # New: Migration readiness data
            'migration_readiness': {
                'ready': 0,
                'review': 0,
                'blocked': 0
            },
            'blockers': [],  # List of blocker issues found
            'file_complexity': {
                'simple': 0,      # 0-2 issues
                'moderate': 0,    # 3-5 issues
                'complex': 0      # 6+ issues
            },
            'readiness_files': {  # Files grouped by readiness category
                'ready': [],
                'review': [],
                'blocked': []
            }
        }
        
        # Get total files analyzed from input_files_inventory table
        ifs = sma_api.get_input_files_stats(db_path=sqlite_path)
        if ifs:
            overview_data['total_files'] = ifs.get('total_files', 0)
            overview_data['file_types'] = ifs.get('file_types', {})
            overview_data['file_types_summary'] = ifs.get('file_types_summary', '')
        
        # Get EWI categories breakdown
        if ewi_data and ewi_data.get('ewis'):
            categories = {}
            for ewi in ewi_data['ewis']:
                cat = ewi.get('category', 'Unknown')
                if cat not in categories:
                    categories[cat] = {'count': 0, 'occurrences': 0}
                categories[cat]['count'] += 1
                categories[cat]['occurrences'] += ewi.get('occurrences', 0)
            overview_data['ewi_categories'] = categories
            
            # Identify blockers from EWI codes
            blockers_found = []
            for ewi in ewi_data['ewis']:
                code = ewi.get('code', '')
                if code in BLOCKER_EWI_CODES:
                    blockers_found.append({
                        'code': code,
                        'description': ewi.get('description', ''),
                        'files_count': len(ewi.get('files_affected', [])),
                        'occurrences': ewi.get('occurrences', 0)
                    })
            overview_data['blockers'] = blockers_found
            
            # Calculate file complexity and readiness
            # Build file_path to index mapping for linking to file_ewis pages
            file_path_to_index = {}
            for idx, f in enumerate(file_data.get('files', [])):
                file_path_to_index[f.get('file_path', '')] = idx
            
            file_issue_counts = {}  # file_path -> {'total': count, 'has_blocker': bool}
            for ewi in ewi_data['ewis']:
                code = ewi.get('code', '')
                is_blocker = code in BLOCKER_EWI_CODES
                for file_path in ewi.get('files_affected', []):
                    if file_path not in file_issue_counts:
                        file_issue_counts[file_path] = {'total': 0, 'has_blocker': False}
                    file_issue_counts[file_path]['total'] += 1
                    if is_blocker:
                        file_issue_counts[file_path]['has_blocker'] = True
            
            # Classify files by complexity and readiness
            readiness_files = {'ready': [], 'review': [], 'blocked': []}
            
            for file_path, counts in file_issue_counts.items():
                issue_count = counts['total']
                has_blocker = counts['has_blocker']
                
                # Get file index for linking to file_ewis page
                file_index = file_path_to_index.get(file_path, -1)
                
                file_info = {
                    'path': file_path,
                    'issues': issue_count,
                    'has_blocker': has_blocker,
                    'file_index': file_index  # Index for file_ewis_{idx}.html
                }
                
                # Complexity classification
                if issue_count <= 2:
                    overview_data['file_complexity']['simple'] += 1
                elif issue_count <= 5:
                    overview_data['file_complexity']['moderate'] += 1
                else:
                    overview_data['file_complexity']['complex'] += 1
                
                # Readiness classification
                if has_blocker:
                    overview_data['migration_readiness']['blocked'] += 1
                    readiness_files['blocked'].append(file_info)
                elif issue_count > 5:
                    overview_data['migration_readiness']['review'] += 1
                    readiness_files['review'].append(file_info)
                else:
                    overview_data['migration_readiness']['ready'] += 1
                    readiness_files['ready'].append(file_info)
            
            # Sort files by issue count (descending)
            for category in readiness_files:
                readiness_files[category].sort(key=lambda x: -x['issues'])
            
            overview_data['readiness_files'] = readiness_files
        
        # Get file types from issues data (fallback if input_files_inventory not available)
        # Only use this fallback if we didn't get data from input_files_inventory
        if overview_data['total_files'] == 0 and ewi_data and ewi_data.get('ewis'):
            file_extensions = {}
            seen_files = set()
            for ewi in ewi_data['ewis']:
                for file_path in ewi.get('files_affected', []):
                    if file_path and file_path not in seen_files:
                        seen_files.add(file_path)
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext == '.py':
                            file_extensions['Python'] = file_extensions.get('Python', 0) + 1
                        elif ext == '.sql':
                            file_extensions['SQL'] = file_extensions.get('SQL', 0) + 1
                        elif ext == '.ipynb':
                            file_extensions['Jupyter'] = file_extensions.get('Jupyter', 0) + 1
                        elif ext == '.scala':
                            file_extensions['Scala'] = file_extensions.get('Scala', 0) + 1
                        else:
                            file_extensions['Other'] = file_extensions.get('Other', 0) + 1
            
            overview_data['file_types'] = file_extensions
            overview_data['total_files'] = len(seen_files)
            
            # Build file types summary
            type_parts = []
            for ft, count in sorted(file_extensions.items(), key=lambda x: -x[1]):
                type_parts.append(f"{count} {ft}")
            overview_data['file_types_summary'] = ', '.join(type_parts[:3])
            if len(type_parts) > 3:
                overview_data['file_types_summary'] += '...'
        
        # Get dependency stats from database
        if has_dependencies:
            dep_stats = sma_api.get_dependency_stats(db_path=sqlite_path)
            overview_data['total_islands'] = dep_stats['total_islands']
            overview_data['island_files'] = dep_stats['island_files']
            overview_data['ready_files'] = dep_stats['ready_files']
        
        # Save overview stats to database
        sma_api.save_overview_stats(working_dir, overview_data, db_path=sqlite_path)
        
        # Add conversion type for overview badge
        overview_data['conversion_type'] = conversion_type
        
        # Generate overview.html
        with open(overview_template_path, 'r', encoding='utf-8') as f:
            overview_content = f.read()
        
        overview_content = overview_content.replace('{{OVERVIEW_DATA}}', json.dumps(overview_data))
        
        with open(overview_path, 'w', encoding='utf-8') as f:
            f.write(overview_content)
        generated_files.append(overview_path)
    
    # Generate EWI tracker HTML (only if has issues)
    if has_issues:
        ewi_tracker_path = os.path.join(ewi_tracker_dir, 'ewi_tracker.html')
        generate_ewi_tracker_html(ewi_data, template_ewi, ewi_tracker_path, workload_name)
        generated_files.append(ewi_tracker_path)
        
        # Generate File tracker HTML
        file_tracker_path = os.path.join(ewi_tracker_dir, 'file_tracker.html')
        generate_file_tracker_html(template_file_tracker, file_tracker_path, workload_name)
        generated_files.append(file_tracker_path)
    
    # Generate Dependency tracker HTML (only if has dependencies)
    if has_dependencies:
        dependency_tracker_path = os.path.join(ewi_tracker_dir, 'dependency_tracker.html')
        if os.path.exists(template_dependency):
            with open(template_dependency, 'r', encoding='utf-8') as f:
                dep_content = f.read()
            dep_content = dep_content.replace('{{WORKLOAD_NAME}}', workload_name)
            with open(dependency_tracker_path, 'w', encoding='utf-8') as f:
                f.write(dep_content)
            generated_files.append(dependency_tracker_path)
    
    test_tracker_path = os.path.join(ewi_tracker_dir, 'test_tracker.html')
    template_test_tracker = os.path.join(args.template_dir, 'test_tracker.html')
    if os.path.exists(template_test_tracker):
        shutil.copy2(template_test_tracker, test_tracker_path)
        generated_files.append(test_tracker_path)
    
    # Clean up existing content HTML files
    existing_content = [f for f in os.listdir(content_dir) if f.endswith('.html')]
    for html_file in existing_content:
        os.remove(os.path.join(content_dir, html_file))
    
    # Generate file detail pages (only if has issues)
    print("[4/5] Generating detail pages...")
    if has_issues:
        for ewi in ewi_data['ewis']:
            if ewi['files_affected']:
                files_page_path = os.path.join(content_dir, f"files_{ewi['code']}.html")
                generate_files_page(ewi, template_files, files_page_path, output_base, workload_name, file_data)
                generated_files.append(files_page_path)
        
        # Generate file EWI detail pages (for File Tracker)
        for idx, file_info in enumerate(file_data.get('files', [])):
            file_ewis_path = os.path.join(content_dir, f"file_ewis_{idx}.html")
            generate_file_ewis_page(file_info, idx, template_file_ewis, file_ewis_path, workload_name, ewi_data)
            generated_files.append(file_ewis_path)
    
    # Generate dependency detail pages (for Dependency Tracker)
    if has_dependencies and os.path.exists(template_dependency_detail):
        dep_files = sma_api.list_dependency_files(db_path=sqlite_path)
        
        for idx, file_id in enumerate(dep_files):
            dep_detail_path = os.path.join(content_dir, f"dep_{idx}.html")
            generate_dependency_detail_page(file_id, idx, template_dependency_detail, dep_detail_path)
            generated_files.append(dep_detail_path)
    
    # Copy test detail page template (for Test Tracker → detail navigation)
    template_test_detail = os.path.join(args.template_dir, 'test_detail.html')
    if os.path.exists(template_test_detail):
        test_detail_path = os.path.join(content_dir, 'test_detail.html')
        shutil.copy2(template_test_detail, test_detail_path)
        generated_files.append(test_detail_path)
    
    # Copy assets and server
    print("[5/5] Copying assets...")
    
    # Copy CSS
    css_src = os.path.join(args.template_dir, 'assets', 'styles.css')
    css_dst = os.path.join(assets_dir, 'styles.css')
    if os.path.exists(css_src):
        shutil.copy2(css_src, css_dst)
        generated_files.append(css_dst)
    
    # Copy logo
    skill_dir = os.path.dirname(args.template_dir)
    logo_src = os.path.join(skill_dir, 'resources', 'svg', 'SMA_logo_sidebar.svg')
    logo_dst = os.path.join(assets_dir, 'snowflake_logo.svg')
    if os.path.exists(logo_src):
        shutil.copy2(logo_src, logo_dst)
        generated_files.append(logo_dst)
    
    # Copy SF_only logo
    sf_logo_src = os.path.join(skill_dir, 'resources', 'svg', 'SF_only.svg')
    sf_logo_dst = os.path.join(assets_dir, 'SF_only.svg')
    if os.path.exists(sf_logo_src):
        shutil.copy2(sf_logo_src, sf_logo_dst)
        generated_files.append(sf_logo_dst)
    
    # Copy server script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    server_src = os.path.join(script_dir, 'sma_server.py')
    server_dst = os.path.join(server_dir, 'sma_server.py')
    if os.path.exists(server_src):
        shutil.copy2(server_src, server_dst)
        generated_files.append(server_dst)
    
    # Copy sma_api.py so the server works standalone outside the skills repo
    sma_api_src = os.path.join(script_dir, "..", "..", "scripts", "sma_api.py")
    sma_api_dst = os.path.join(server_dir, "sma_api.py")
    if os.path.exists(sma_api_src):
        shutil.copy2(sma_api_src, sma_api_dst)
        generated_files.append(sma_api_dst)
    
    # Copy extractors for server
    extractors_src = os.path.join(script_dir, 'extractors')
    extractors_dst = os.path.join(server_dir, 'extractors')
    if os.path.exists(extractors_src):
        if os.path.exists(extractors_dst):
            shutil.rmtree(extractors_dst)
        shutil.copytree(extractors_src, extractors_dst)
        generated_files.append(extractors_dst)
    
    # Copy start_server.py launcher script to dashboard root
    launcher_src = os.path.join(args.template_dir, 'start_server.py')
    launcher_dst = os.path.join(dashboard_dir, 'start_server.py')
    if os.path.exists(launcher_src):
        shutil.copy2(launcher_src, launcher_dst)
        generated_files.append(launcher_dst)
    
    # Ensure .gitignore at workload root
    _ensure_workload_gitignore(working_dir)
    
    # Ensure .vscode/settings.json at workload root
    _ensure_vscode_settings(working_dir)
    
    # Print summary
    print_summary(manifest, ewi_data, generated_files, sqlite_path)
    
    # Start server
    if not args.no_open:
        import webbrowser
        
        # Check if server is already running
        existing_port = check_existing_server(dashboard_dir)
        
        if existing_port:
            url = f"http://localhost:{existing_port}/index.html"
            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Server already running at: http://localhost:{existing_port:<5}            ║
╠══════════════════════════════════════════════════════════════╣
║  Dashboard files have been regenerated.                      ║
║  Refresh your browser to see the changes.                    ║
╚══════════════════════════════════════════════════════════════╝
""")
            # Don't auto-open browser again - user should just refresh
            return 0
        
        # No existing server, start a new one as background process
        import subprocess
        import socket
        
        # Find an available port (try 8080-8099)
        start_port = 8080
        max_attempts = 20
        port = None
        
        for attempt in range(max_attempts):
            test_port = start_port + attempt
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('localhost', test_port))
                    port = test_port
                    break
            except OSError:
                continue
        
        if port is None:
            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Could not find available port (tried {start_port}-{start_port + max_attempts - 1})       ║
╠══════════════════════════════════════════════════════════════╣
║  Start the server manually with:                             ║
║                                                              ║
║    cd {dashboard_dir}
║    python start_server.py --port 9000                        ║
╚══════════════════════════════════════════════════════════════╝
""")
            return 1
        
        # Launch server as detached background process
        server_script = os.path.join(server_dir, 'sma_server.py')
        cmd = [
            sys.executable, server_script, dashboard_dir,
            '--sqlite', sqlite_path,
            '--workload', workload_name,
            '--port', str(port),
            '--no-open'
        ]
        
        if platform.system() == 'Windows':
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            )
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )
        
        # Save PID and port
        pid_file = os.path.join(dashboard_dir, 'server', '.server.pid')
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, 'w') as f:
            f.write(f"{proc.pid}:{port}")
        
        import time
        
        # Wait for server to start and verify it's running
        url = f"http://localhost:{port}/index.html"
        server_ready = False
        
        for _ in range(10):  # Try for up to 2 seconds
            time.sleep(0.2)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    result = s.connect_ex(('localhost', port))
                    if result == 0:
                        server_ready = True
                        break
            except:
                pass
        
        if server_ready:
            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SMA Dashboard Server v2.0.0 (SQLite)                        ║
╠══════════════════════════════════════════════════════════════╣
║  Server running at: http://localhost:{port:<5}                  ║
║  Dashboard: {url:<47} ║
║  Database: {os.path.basename(sqlite_path):<48} ║
║                                                              ║
║  Server PID: {proc.pid:<6}                                         ║
║  Changes are saved directly to SQLite database.              ║
║                                                              ║
║  To stop: python start_server.py --stop                      ║
╚══════════════════════════════════════════════════════════════╝
""")
            webbrowser.open(url)
        else:
            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Warning: Server may not have started correctly              ║
╠══════════════════════════════════════════════════════════════╣
║  Attempted to start on port: {port:<5}                          ║
║  Server PID: {proc.pid:<6}                                         ║
║                                                              ║
║  Try starting manually:                                      ║
║    cd {dashboard_dir}
║    python3 server/sma_server.py . --port {port}
╚══════════════════════════════════════════════════════════════╝
""")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())

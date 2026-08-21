#!/usr/bin/env python3
"""
Embedded File Path Replacer for SMA-Converted Snowpark Code

Detects embedded file paths in Python files and Jupyter notebooks using AST parsing,
transforms them to Snowflake stage format, and applies replacements.
"""

import re
import json
import argparse
import ast
import subprocess
import sys
import os
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set, Any
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import sma_api  # noqa: E402


class DetectionMethod(Enum):
    """How the path was detected"""
    SNOWPARK_READ = "Snowpark read operation"
    SNOWPARK_WRITE = "Snowpark write operation"
    SNOWPARK_SQL = "SQL statement with path"
    FILE_OPERATION = "File operation"
    STRING_VARIABLE = "String variable assignment"
    SNOWFLAKE_STAGE = "Snowflake stage path (SMA-converted)"
    REGEX_FALLBACK = "Regex pattern match"


class ReplacementStatus(Enum):
    """Status of a path replacement"""
    REPLACED = "replaced"
    NEEDS_REVISION = "needs_revision"
    SKIPPED_RELATIVE = "skipped_relative_path"
    SKIPPED_DYNAMIC = "skipped_dynamic_path"
    SKIPPED_UNSUPPORTED = "skipped_unsupported"
    FAILED = "failed"


@dataclass
class ReplacementResult:
    """Result of a single path replacement attempt"""
    original_path: str
    transformed_path: Optional[str]
    status: ReplacementStatus
    reason: str
    file: str
    line_number: int
    cell_index: Optional[int] = None
    line_content: str = ""


@dataclass
class PathOccurrence:
    """Represents a found file path"""
    path: str
    file: str
    line_number: int
    line_content: str
    detection_method: DetectionMethod
    cell_index: Optional[int] = None  # For Jupyter notebooks
    quote_char: str = '"'  # Track quote style for accurate replacement


@dataclass
class PathReplacement:
    """Represents a path transformation"""
    original: str
    transformed: Optional[str]
    status: ReplacementStatus
    reason: str
    occurrences: List[PathOccurrence] = field(default_factory=list)


class ASTPathVisitor(ast.NodeVisitor):
    """AST visitor to detect file paths in Python code"""
    
    def __init__(self, source_lines: List[str]):
        self.source_lines = source_lines
        self.paths_found: List[Tuple[str, int, str, DetectionMethod, str]] = []
        # Supported protocols
        self.protocols = {
            'hdfs', 's3', 's3a', 's3n', 'abfs', 'abfss', 
            'wasb', 'wasbs', 'adl', 'gs', 'file'
        }
    
    def visit_Call(self, node: ast.Call) -> None:
        """Visit function calls and check ALL arguments for paths"""
        try:
            # Get the function name for context (informational only)
            func_name = self._get_func_name(node.func)
            
            # Determine detection method based on function name patterns
            detection_method = self._determine_detection_method(func_name)
            
            # Extract ALL path-like strings from this call
            paths_in_call = self._extract_all_paths_from_call(node)
            
            for path, quote_char in paths_in_call:
                line_num = node.lineno
                line_content = self._get_line_content(line_num)
                self.paths_found.append((
                    path, line_num, line_content, 
                    detection_method, quote_char
                ))
        
        except Exception:
            pass  # Silently skip unparseable nodes
        
        self.generic_visit(node)
    
    def _determine_detection_method(self, func_name: str) -> DetectionMethod:
        """Determine detection method based on function name (for reporting)"""
        func_lower = func_name.lower()
        
        # Check for Snowpark/Spark read patterns
        if any(pattern in func_lower for pattern in [
            'spark.read', 'session.read', '.read.', '.load'
        ]):
            return DetectionMethod.SNOWPARK_READ
        
        # Check for write patterns
        if any(pattern in func_lower for pattern in [
            '.write.', '.save'
        ]):
            return DetectionMethod.SNOWPARK_WRITE
        
        # Check for file operations
        if any(pattern in func_lower for pattern in [
            'pd.read', 'file.get', 'file.put', 'open'
        ]):
            return DetectionMethod.FILE_OPERATION
        
        # Default to generic pattern match
        return DetectionMethod.REGEX_FALLBACK
    
    def visit_Assign(self, node: ast.Assign) -> None:
        """Visit assignments to detect path string variables"""
        try:
            # Check if any target is a path-related variable name
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_name = target.id.lower()
                    if any(keyword in var_name for keyword in [
                        'path', 'location', 'dir', 'directory', 'file', 'url'
                    ]):
                        # Extract string value (regular string)
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            path = self._get_string_value(node.value)
                            if path and self._is_valid_path(path):
                                line_num = node.lineno
                                line_content = self._get_line_content(line_num)
                                quote_char = self._detect_quote_char(line_content, path)
                                self.paths_found.append((
                                    path, line_num, line_content, 
                                    DetectionMethod.STRING_VARIABLE, quote_char
                                ))
                        # Extract f-string value
                        elif isinstance(node.value, ast.JoinedStr):
                            path = self._reconstruct_fstring(node.value)
                            if path and self._is_valid_path(path):
                                line_num = node.lineno
                                line_content = self._get_line_content(line_num)
                                self.paths_found.append((
                                    path, line_num, line_content, 
                                    DetectionMethod.STRING_VARIABLE, '"'
                                ))
        except Exception:
            pass
        
        self.generic_visit(node)
    
    def _get_func_name(self, node: ast.AST) -> str:
        """Extract function name from AST node"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            # Build dotted name like session.read.csv
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            
            # Check if the base is a Name (e.g., 'spark' in spark.read.csv)
            if isinstance(current, ast.Name):
                parts.append(current.id)
                return '.'.join(reversed(parts))
            
            # Check if the base is a Call (e.g., .load() called on .format() result)
            # In this case, recursively get the name from the call
            elif isinstance(current, ast.Call):
                # Get the name of the chained call
                base_name = self._get_func_name(current.func)
                final_method = parts[0] if parts else ""
                if base_name:
                    return f"{base_name}.{final_method}"
                return final_method
            
            return '.'.join(reversed(parts))
        elif isinstance(node, ast.Call):
            # It's a call node, get its function name
            return self._get_func_name(node.func)
        return ""
    
    def _extract_all_paths_from_call(self, node: ast.Call) -> List[Tuple[str, str]]:
        """
        Extract ALL path-like strings from a function call (unrestricted mode).
        Returns list of (path, quote_char) tuples.
        """
        paths = []
        
        # Check all positional arguments
        if node.args:
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    path = self._get_string_value(arg)
                    if path and self._is_valid_path(path):
                        quote_char = self._detect_quote_char(
                            self._get_line_content(node.lineno), path
                        )
                        paths.append((path, quote_char))
                elif isinstance(arg, ast.JoinedStr):  # f-string
                    path = self._reconstruct_fstring(arg)
                    if path and self._is_valid_path(path):
                        paths.append((path, '"'))
        
        # Check all keyword arguments
        for keyword in node.keywords:
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                path = self._get_string_value(keyword.value)
                if path and self._is_valid_path(path):
                    quote_char = self._detect_quote_char(
                        self._get_line_content(node.lineno), path
                    )
                    paths.append((path, quote_char))
            elif isinstance(keyword.value, ast.JoinedStr):
                path = self._reconstruct_fstring(keyword.value)
                if path and self._is_valid_path(path):
                    paths.append((path, '"'))
        
        return paths
    
    def _get_string_value(self, node: ast.AST) -> Optional[str]:
        """Get string value from AST node"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None
    
    def _reconstruct_fstring(self, node: ast.JoinedStr) -> Optional[str]:
        """Reconstruct f-string with placeholders"""
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(self._get_string_value(value))
            elif isinstance(value, ast.FormattedValue):
                # Preserve the interpolation marker
                if isinstance(value.value, ast.Name):
                    parts.append(f"{{{value.value.id}}}")
                else:
                    parts.append("{...}")
        return ''.join(parts) if parts else None
    
    def _is_valid_path(self, path: str) -> bool:
        """Check if detected string is likely a valid file path"""
        if not path or len(path) < 3:
            return False
        
        # Has protocol
        if any(path.lower().startswith(f"{p}://") for p in self.protocols):
            return True
        
        # Already converted Snowflake stage path
        if path.startswith('@'):
            return True
        
        # Has file extension
        extensions = ['.csv', '.parquet', '.json', '.avro', '.orc', '.txt', '.data', '.tsv']
        if any(path.lower().endswith(ext) for ext in extensions):
            return True
        
        # Looks like a directory path (ends with /)
        if path.endswith('/') and ('/' in path or '\\' in path):
            return True
        
        # Relative paths
        if path.startswith('./') or path.startswith('../'):
            return True
        
        return False
    
    def _get_line_content(self, line_num: int) -> str:
        """Get the content of a line"""
        if 0 < line_num <= len(self.source_lines):
            return self.source_lines[line_num - 1]
        return ""
    
    def _detect_quote_char(self, line_content: str, path: str) -> str:
        """Detect which quote character is used for the path"""
        # Try to find the path in the line with quotes
        if f'"{path}"' in line_content:
            return '"'
        elif f"'{path}'" in line_content:
            return "'"
        return '"'  # Default to double quotes


class EmbeddedPathDetector:
    """Detects embedded file paths in code using AST parsing with regex fallback"""
    
    # Supported protocols
    PROTOCOLS = {
        'hdfs', 's3', 's3a', 's3n', 'abfs', 'abfss', 
        'wasb', 'wasbs', 'adl', 'gs', 'file'
    }
    
    # Snowflake stage path pattern (SMA-converted paths)
    # Matches: @STAGE_NAME/protocol/path or @"STAGE_NAME"/protocol/path
    SNOWFLAKE_STAGE_PATTERN = re.compile(
        r'@(?:"([^"]+)"|([A-Za-z0-9_]+))/([a-z0-9]+)/([^\s"\']+)',
        re.IGNORECASE
    )
    
    # Fallback regex for protocol-based paths
    PROTOCOL_REGEX = re.compile(
        r'(["\'])(' + '|'.join(PROTOCOLS) + r')://([^\s"\']+)\1',
        re.IGNORECASE
    )
    
    # Fallback for local paths
    LOCAL_PATH_REGEX = re.compile(
        r'(["\'])(/[a-zA-Z0-9_/.\-]+\.(?:csv|parquet|json|avro|orc|txt|data|tsv))\1'
    )
    
    # Relative path patterns
    RELATIVE_PATH_REGEX = re.compile(
        r'(["\'])(\.\.?/[^\s"\']+)\1'
    )
    
    def __init__(self):
        pass
    
    def detect_with_ast(self, source_code: str, source_lines: List[str]) -> List[Tuple[str, int, str, DetectionMethod, str]]:
        """
        Detect paths using AST parsing.
        Checks ALL function calls for path-like arguments.
        
        Args:
            source_code: The Python source code to parse
            source_lines: List of source code lines
        
        Returns list of (path, line_num, line_content, detection_method, quote_char) tuples.
        """
        try:
            tree = ast.parse(source_code)
            visitor = ASTPathVisitor(source_lines)
            visitor.visit(tree)
            return visitor.paths_found
        except SyntaxError:
            # If AST parsing fails, return empty list (will fall back to regex)
            return []
    
    def detect_in_line(self, line: str) -> List[Tuple[str, DetectionMethod, str]]:
        """
        Detect paths in a single line of code using regex (fallback method).
        Returns list of (path, detection_method, quote_char) tuples.
        """
        results = []
        
        # FIRST: Check for Snowflake stage paths (SMA-converted format: @STAGE/protocol/path)
        for match in self.SNOWFLAKE_STAGE_PATTERN.finditer(line):
            # Extract stage name (from either quoted or unquoted group)
            stage_name = match.group(1) if match.group(1) else match.group(2)
            protocol = match.group(3)
            path = match.group(4)
            
            # Reconstruct full stage path
            if match.group(1):  # Quoted stage name
                full_path = f'@"{stage_name}"/{protocol}/{path}'
            else:
                full_path = f'@{stage_name}/{protocol}/{path}'
            
            # Detect quote character
            quote_char = '"' if '"' in match.group(0) else "'"
            results.append((full_path, DetectionMethod.SNOWFLAKE_STAGE, quote_char))
        
        # If we found stage paths, return them (they take precedence)
        if results:
            return results
        
        # Protocol-based paths
        for match in self.PROTOCOL_REGEX.finditer(line):
            quote_char = match.group(1)
            protocol = match.group(2)
            rest = match.group(3)
            path = f"{protocol}://{rest}"
            if self._is_valid_path(path):
                results.append((path, DetectionMethod.REGEX_FALLBACK, quote_char))
        
        # Local absolute paths
        for match in self.LOCAL_PATH_REGEX.finditer(line):
            quote_char = match.group(1)
            path = match.group(2)
            if self._is_valid_path(path):
                results.append((path, DetectionMethod.REGEX_FALLBACK, quote_char))
        
        # Relative paths
        for match in self.RELATIVE_PATH_REGEX.finditer(line):
            quote_char = match.group(1)
            path = match.group(2)
            if self._is_valid_path(path):
                results.append((path, DetectionMethod.REGEX_FALLBACK, quote_char))
        
        return results
    
    def _is_valid_path(self, path: str) -> bool:
        """Check if detected string is likely a valid file path"""
        if not path or len(path) < 3:
            return False
        
        # Has protocol
        if any(path.lower().startswith(f"{p}://") for p in self.PROTOCOLS):
            return True
        
        # Already converted stage path
        if path.startswith('@'):
            return True
        
        # Has file extension
        extensions = ['.csv', '.parquet', '.json', '.avro', '.orc', '.txt', '.data', '.tsv']
        if any(path.lower().endswith(ext) for ext in extensions):
            return True
        
        # Looks like a directory path (ends with /)
        if path.endswith('/') and ('/' in path or '\\' in path):
            return True
        
        # Relative paths
        if path.startswith('./') or path.startswith('../'):
            return True
        
        return False


class FileScanner:
    """Scans Python files and Jupyter notebooks for embedded paths using AST parsing"""
    
    def __init__(self):
        self.detector = EmbeddedPathDetector()
    
    def scan_python_file(self, file_path: Path) -> List[PathOccurrence]:
        """Scan a Python file for embedded paths using AST parsing with regex fallback"""
        occurrences = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
                lines = source_code.splitlines(keepends=False)
            
            # Track found paths to avoid duplicates
            found_paths: Set[Tuple[str, int]] = set()
            
            # PRIMARY: Try AST-based detection first (checks ALL calls)
            try:
                ast_results = self.detector.detect_with_ast(source_code, lines)
                for path, line_num, line_content, method, quote_char in ast_results:
                    key = (path, line_num)
                    if key not in found_paths:
                        found_paths.add(key)
                        occurrences.append(PathOccurrence(
                            path=path,
                            file=str(file_path),
                            line_number=line_num,
                            line_content=line_content,
                            detection_method=method,
                            quote_char=quote_char
                        ))
            except Exception as e:
                print(f"  Warning: AST parsing failed for {file_path.name}, falling back to regex: {e}")
            
            # FALLBACK: Use regex-based detection for paths AST might have missed
            # (e.g., paths in SQL strings, multiline strings, comments)
            for line_num, line in enumerate(lines, start=1):
                # Skip pure comment lines
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue
                
                detected = self.detector.detect_in_line(line)
                for path, method, quote_char in detected:
                    key = (path, line_num)
                    if key not in found_paths:
                        found_paths.add(key)
                        occurrences.append(PathOccurrence(
                            path=path,
                            file=str(file_path),
                            line_number=line_num,
                            line_content=line.rstrip(),
                            detection_method=method,
                            quote_char=quote_char
                        ))
        
        except Exception as e:
            print(f"Error scanning {file_path}: {e}")
        
        return occurrences
    
    def scan_jupyter_notebook(self, file_path: Path) -> List[PathOccurrence]:
        """Scan a Jupyter notebook for embedded paths (Python cells only) using AST parsing"""
        occurrences = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                notebook = json.load(f)
            
            cells = notebook.get('cells', [])
            
            for cell_idx, cell in enumerate(cells):
                # Only process Python code cells
                if cell.get('cell_type') != 'code':
                    continue
                
                # Some notebooks have language metadata
                language = cell.get('metadata', {}).get('language', 'python')
                if language.lower() != 'python':
                    continue
                
                # Get source lines
                source = cell.get('source', [])
                if isinstance(source, str):
                    cell_code = source
                    lines = source.split('\n')
                elif isinstance(source, list):
                    # Handle both list of lines and list with single multiline string
                    if len(source) == 1 and '\n' in source[0]:
                        cell_code = source[0]
                        lines = source[0].split('\n')
                    else:
                        # Join to get full code
                        cell_code = ''.join(source)
                        lines = cell_code.split('\n')
                else:
                    lines = []
                    cell_code = ""
                
                if not cell_code.strip():
                    continue
                
                # Track found paths to avoid duplicates
                found_paths: Set[Tuple[str, int]] = set()
                
                # PRIMARY: Try AST-based detection first (checks ALL calls)
                try:
                    ast_results = self.detector.detect_with_ast(cell_code, lines)
                    for path, line_num, line_content, method, quote_char in ast_results:
                        key = (path, line_num)
                        if key not in found_paths:
                            found_paths.add(key)
                            occurrences.append(PathOccurrence(
                                path=path,
                                file=str(file_path),
                                line_number=line_num,
                                line_content=line_content,
                                detection_method=method,
                                cell_index=cell_idx,
                                quote_char=quote_char
                            ))
                except Exception:
                    # Silently fall back to regex for this cell
                    pass
                
                # FALLBACK: Use regex-based detection
                for line_num, line in enumerate(lines, start=1):
                    # Skip comments
                    stripped = line.strip()
                    if stripped.startswith('#'):
                        continue
                    
                    detected = self.detector.detect_in_line(line)
                    for path, method, quote_char in detected:
                        key = (path, line_num)
                        if key not in found_paths:
                            found_paths.add(key)
                            occurrences.append(PathOccurrence(
                                path=path,
                                file=str(file_path),
                                line_number=line_num,
                                line_content=line.rstrip(),
                                detection_method=method,
                                cell_index=cell_idx,
                                quote_char=quote_char
                            ))
        
        except Exception as e:
            print(f"Error scanning {file_path}: {e}")
        
        return occurrences
    
    def scan_file(self, file_path: Path) -> List[PathOccurrence]:
        """Scan a file (auto-detect type)"""
        if file_path.suffix == '.ipynb':
            return self.scan_jupyter_notebook(file_path)
        elif file_path.suffix == '.py':
            return self.scan_python_file(file_path)
        else:
            return []


class PathTransformer:
    """Transforms embedded paths to Snowflake stage format"""
    
    # Pattern to detect already-converted Snowflake stage paths
    STAGE_PATH_PATTERN = re.compile(
        r'^@(?:"([^"]+)"|([A-Za-z0-9_]+))/([a-z0-9]+)/(.+)$',
        re.IGNORECASE
    )
    
    # Dynamic path patterns (f-strings, concatenation) - still process but flag
    DYNAMIC_PATTERNS = [
        (r'\{[^}]+\}', 'Contains f-string interpolation'),
        (r'\$\{[^}]+\}', 'Contains variable interpolation'),
    ]
    
    @classmethod
    def should_skip_path(cls, path: str) -> Tuple[bool, Optional[ReplacementStatus], Optional[str]]:
        """
        Check if a path should be skipped.
        Returns (should_skip, status, reason)
        """
        # Check for dynamic paths (but don't skip - just flag for review)
        for pattern, reason in cls.DYNAMIC_PATTERNS:
            if re.search(pattern, path):
                # We still transform these but flag them for review
                return False, None, None
        
        return False, None, None
    
    @classmethod
    def is_dynamic_path(cls, path: str) -> bool:
        """Check if path contains dynamic elements like f-string interpolation"""
        for pattern, _ in cls.DYNAMIC_PATTERNS:
            if re.search(pattern, path):
                return True
        return False
    
    @staticmethod
    def transform_path(original_path: str, prefix: str) -> Tuple[Optional[str], ReplacementStatus, str]:
        """
        Transform a path to @prefix/protocol/path format.
        
        Handles two scenarios:
        1. Already converted stage paths (@OLD_STAGE/protocol/path) → @prefix/protocol/path
        2. Original protocol paths (s3://bucket/path) → @prefix/s3/bucket/path
        
        Returns: (transformed_path, status, reason)
        
        Examples:
            # Scenario 1: Update stage prefix
            @OLD_STAGE/s3/bucket/file.csv → @NEW_STAGE/s3/bucket/file.csv
            
            # Scenario 2: Convert original paths
            s3://bucket/file.csv → @prefix/s3/bucket/file.csv
            hdfs://cluster/data → @prefix/hdfs/cluster/data
            /local/file.csv → @prefix/local/local/file.csv
        """
        # Check if path should be skipped
        should_skip, skip_status, skip_reason = PathTransformer.should_skip_path(original_path)
        if should_skip:
            return None, skip_status, skip_reason
        
        # Check if path contains dynamic elements (f-strings, variable interpolation)
        # These paths will be transformed BUT flagged as needs_revision
        is_dynamic = PathTransformer.is_dynamic_path(original_path)
        
        # If path is dynamic but doesn't match any known pattern, flag it as needs manual review
        if is_dynamic and original_path.startswith('${'):
            return None, ReplacementStatus.NEEDS_REVISION, "Dynamic path with variable interpolation - cannot auto-transform, needs manual review"
        
        # Ensure prefix doesn't have @ already
        prefix = prefix.lstrip('@')
        
        # Check if this is already a Snowflake stage path (SMA-converted)
        # Pattern: @STAGE_NAME/protocol/path or @"STAGE_NAME"/protocol/path
        stage_match = re.match(r'^@(?:"([^"]+)"|([A-Za-z0-9_]+))/([a-z0-9]+)/(.+)$', original_path, re.IGNORECASE)
        if stage_match:
            # Extract components
            old_stage = stage_match.group(1) if stage_match.group(1) else stage_match.group(2)
            protocol = stage_match.group(3)
            rest = stage_match.group(4)
            
            # Return with new prefix (keep protocol and path)
            transformed = f"@{prefix}/{protocol}/{rest}"
            if is_dynamic:
                return transformed, ReplacementStatus.NEEDS_REVISION, f"Dynamic path - contains variable interpolation. Updated stage prefix from @{old_stage} to @{prefix}"
            return transformed, ReplacementStatus.REPLACED, f"Updated stage prefix from @{old_stage} to @{prefix}"
        
        # Handle protocol-based paths (original, unconverted)
        protocol_match = re.match(r'^([a-z0-9]+)://(.+)$', original_path, re.IGNORECASE)
        if protocol_match:
            protocol = protocol_match.group(1).lower()
            rest = protocol_match.group(2)
            
            # Special handling for file:// protocol
            if protocol == 'file':
                # Remove leading slashes and treat as local
                rest = rest.lstrip('/')
                transformed = f"@{prefix}/local/{rest}"
                if is_dynamic:
                    return transformed, ReplacementStatus.NEEDS_REVISION, "Dynamic path - contains variable interpolation. Transformed file:// to local"
                return transformed, ReplacementStatus.REPLACED, "Transformed file:// to local"
            
            transformed = f"@{prefix}/{protocol}/{rest}"
            if is_dynamic:
                return transformed, ReplacementStatus.NEEDS_REVISION, f"Dynamic path - contains variable interpolation. Transformed {protocol}:// path"
            return transformed, ReplacementStatus.REPLACED, f"Transformed {protocol}:// path"
        
        # Handle absolute local paths
        if original_path.startswith('/'):
            rest = original_path.lstrip('/')
            transformed = f"@{prefix}/local/{rest}"
            if is_dynamic:
                return transformed, ReplacementStatus.NEEDS_REVISION, "Dynamic path - contains variable interpolation. Transformed absolute local path"
            return transformed, ReplacementStatus.REPLACED, "Transformed absolute local path"
        
        # Handle relative paths - convert to stage format
        if original_path.startswith('./'):
            # Remove ./ and add to relative path
            rest = original_path[2:]  # Remove ./
            transformed = f"@{prefix}/relative/{rest}"
            if is_dynamic:
                return transformed, ReplacementStatus.NEEDS_REVISION, "Dynamic path - contains variable interpolation. Transformed relative path (./) to stage format"
            return transformed, ReplacementStatus.REPLACED, "Transformed relative path (./) to stage format"
        
        if original_path.startswith('../'):
            # Count parent directory traversals
            parts = original_path.split('/')
            parent_count = 0
            remaining_parts = []
            
            for part in parts:
                if part == '..':
                    parent_count += 1
                elif part and part != '.':
                    remaining_parts.append(part)
            
            # Build path with parent indicator
            if parent_count == 1:
                rest = '/'.join(remaining_parts)
                transformed = f"@{prefix}/relative/parent/{rest}"
                if is_dynamic:
                    return transformed, ReplacementStatus.NEEDS_REVISION, "Dynamic path - contains variable interpolation. Transformed relative path (../) to stage format"
                return transformed, ReplacementStatus.REPLACED, "Transformed relative path (../) to stage format"
            else:
                rest = '/'.join(remaining_parts)
                transformed = f"@{prefix}/relative/parent{parent_count}/{rest}"
                if is_dynamic:
                    return transformed, ReplacementStatus.NEEDS_REVISION, f"Dynamic path - contains variable interpolation. Transformed relative path (../ x{parent_count}) to stage format"
                return transformed, ReplacementStatus.REPLACED, f"Transformed relative path (../ x{parent_count}) to stage format"
        
        # Default: unsupported path format
        return None, ReplacementStatus.SKIPPED_UNSUPPORTED, f"Unsupported path format: {original_path}"


class ReplacementApplier:
    """Applies path replacements to files"""
    
    def apply_to_python_file(self, file_path: Path, replacements: Dict[str, str], dry_run: bool = False) -> int:
        """Apply replacements to a Python file"""
        if dry_run:
            return 0
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            replacement_count = 0
            
            for old_path, new_path in replacements.items():
                # Try both quote styles
                for quote in ['"', "'"]:
                    old_quoted = f"{quote}{old_path}{quote}"
                    new_quoted = f"{quote}{new_path}{quote}"
                    if old_quoted in content:
                        content = content.replace(old_quoted, new_quoted)
                        replacement_count += content.count(new_quoted) - original_content.count(new_quoted)
            
            if content != original_content:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            return replacement_count
        
        except Exception as e:
            print(f"Error applying replacements to {file_path}: {e}")
            return 0
    
    def apply_to_notebook(self, file_path: Path, replacements: Dict[str, str], dry_run: bool = False) -> int:
        """Apply replacements to a Jupyter notebook"""
        if dry_run:
            return 0
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                notebook = json.load(f)
            
            replacement_count = 0
            
            for cell in notebook.get('cells', []):
                if cell.get('cell_type') != 'code':
                    continue
                
                source = cell.get('source', [])
                if isinstance(source, str):
                    source = [source]
                
                modified = False
                new_source = []
                
                for line in source:
                    new_line = line
                    for old_path, new_path in replacements.items():
                        for quote in ['"', "'"]:
                            old_quoted = f"{quote}{old_path}{quote}"
                            new_quoted = f"{quote}{new_path}{quote}"
                            if old_quoted in new_line:
                                new_line = new_line.replace(old_quoted, new_quoted)
                                replacement_count += 1
                                modified = True
                    new_source.append(new_line)
                
                if modified:
                    cell['source'] = new_source
            
            if replacement_count > 0:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(notebook, f, indent=1, ensure_ascii=False)
            
            return replacement_count
        
        except Exception as e:
            print(f"Error applying replacements to {file_path}: {e}")
            return 0
    
    def apply_to_file(self, file_path: Path, replacements: Dict[str, str], dry_run: bool = False) -> int:
        """Apply replacements to a file (auto-detect type)"""
        if file_path.suffix == '.ipynb':
            return self.apply_to_notebook(file_path, replacements, dry_run)
        elif file_path.suffix == '.py':
            return self.apply_to_python_file(file_path, replacements, dry_run)
        else:
            return 0


def scan_files(file_paths: List[Path]) -> List[PathOccurrence]:
    """Scan multiple files for embedded paths"""
    scanner = FileScanner()
    all_occurrences = []
    
    for file_path in file_paths:
        occurrences = scanner.scan_file(file_path)
        all_occurrences.extend(occurrences)
    
    return all_occurrences


def generate_replacements(occurrences: List[PathOccurrence], prefix: str) -> List[PathReplacement]:
    """Generate path replacements from occurrences"""
    # Group by unique path
    path_map: Dict[str, List[PathOccurrence]] = {}
    for occ in occurrences:
        if occ.path not in path_map:
            path_map[occ.path] = []
        path_map[occ.path].append(occ)
    
    # Generate transformations
    replacements = []
    
    for original_path, occs in path_map.items():
        transformed, status, reason = PathTransformer.transform_path(original_path, prefix)
        replacements.append(PathReplacement(
            original=original_path,
            transformed=transformed,
            status=status,
            reason=reason,
            occurrences=occs
        ))
    
    return replacements


def format_occurrence_location(occ: PathOccurrence) -> str:
    """Format occurrence location for display"""
    if occ.cell_index is not None:
        return f"cell[{occ.cell_index}]:line {occ.line_number}"
    else:
        return f"line {occ.line_number}"


def print_findings(occurrences: List[PathOccurrence]):
    """Print found paths in a readable format"""
    if not occurrences:
        print("No embedded file paths found.")
        return
    
    # Group by file
    by_file: Dict[str, List[PathOccurrence]] = {}
    for occ in occurrences:
        if occ.file not in by_file:
            by_file[occ.file] = []
        by_file[occ.file].append(occ)
    
    print(f"\nFound {len(occurrences)} embedded file paths in {len(by_file)} files:\n")
    
    for file_path, occs in by_file.items():
        file_type = "Jupyter notebook" if file_path.endswith('.ipynb') else "Python file"
        print(f"File: {Path(file_path).name} ({file_type})")
        
        # Group by unique path within file
        by_path: Dict[str, List[PathOccurrence]] = {}
        for occ in occs:
            if occ.path not in by_path:
                by_path[occ.path] = []
            by_path[occ.path].append(occ)
        
        for path, path_occs in by_path.items():
            print(f"  Path: {path}")
            for occ in path_occs:
                location = format_occurrence_location(occ)
                print(f"    - {location}: {occ.line_content.strip()}")
            print(f"    Detection: {path_occs[0].detection_method.value}")
        print()


def print_replacements(replacements: List[PathReplacement], prefix: str):
    """Print planned replacements, paths needing revision, and skipped paths"""
    # Separate by status
    to_replace = [r for r in replacements if r.status == ReplacementStatus.REPLACED]
    needs_revision = [r for r in replacements if r.status == ReplacementStatus.NEEDS_REVISION]
    skipped = [r for r in replacements if r.status not in (ReplacementStatus.REPLACED, ReplacementStatus.NEEDS_REVISION)]
    
    if to_replace:
        print(f"\nPlanned replacements with prefix '@{prefix}':\n")
        for repl in to_replace:
            print(f"  {repl.original}")
            print(f"  → {repl.transformed}")
            print(f"  Occurrences: {len(repl.occurrences)}")
            print()
    
    if needs_revision:
        print(f"\n⚠️  Paths needing revision (will be replaced with warning comments):\n")
        for repl in needs_revision:
            print(f"  {repl.original}")
            print(f"  → {repl.transformed}")
            print(f"  Reason: {repl.reason}")
            print(f"  Occurrences: {len(repl.occurrences)}")
            print()
    
    if skipped:
        print(f"\n❌ Skipped paths (will add warning comments, no transformation):\n")
        for repl in skipped:
            print(f"  {repl.original}")
            print(f"  Status: {repl.status.value}")
            print(f"  Reason: {repl.reason}")
            print(f"  Occurrences: {len(repl.occurrences)}")
            print()


def apply_replacements(replacements: List[PathReplacement], dry_run: bool = False) -> Dict[str, int]:
    """Apply all replacements to files (both REPLACED and NEEDS_REVISION status paths)"""
    applier = ReplacementApplier()
    results = {}
    
    # Filter to paths that should be replaced (both REPLACED and NEEDS_REVISION)
    to_replace = [r for r in replacements if r.status in (ReplacementStatus.REPLACED, ReplacementStatus.NEEDS_REVISION)]
    
    # Group by file
    by_file: Dict[str, Dict[str, str]] = {}
    for repl in to_replace:
        for occ in repl.occurrences:
            if occ.file not in by_file:
                by_file[occ.file] = {}
            by_file[occ.file][repl.original] = repl.transformed
    
    # Apply to each file
    for file_path, file_replacements in by_file.items():
        count = applier.apply_to_file(Path(file_path), file_replacements, dry_run)
        results[file_path] = count
    
    return results


def inject_warnings_python(file_path: Path, skipped: List[PathReplacement]) -> int:
    """Inject warning comments for skipped paths in a Python file"""
    # Build a map of line numbers to warnings needed
    warnings_by_line: Dict[int, List[Tuple[str, str, str]]] = {}
    
    for repl in skipped:
        for occ in repl.occurrences:
            if occ.file == str(file_path):
                if occ.line_number not in warnings_by_line:
                    warnings_by_line[occ.line_number] = []
                warnings_by_line[occ.line_number].append((repl.original, repl.status.value, repl.reason))
    
    if not warnings_by_line:
        return 0
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Insert warnings from bottom to top to preserve line numbers
        warning_count = 0
        for line_num in sorted(warnings_by_line.keys(), reverse=True):
            warnings = warnings_by_line[line_num]
            # Get indentation of the target line
            target_line = lines[line_num - 1] if line_num <= len(lines) else ""
            indent = len(target_line) - len(target_line.lstrip())
            indent_str = " " * indent
            
            # Create warning comments
            warning_lines = []
            for path, status, reason in warnings:
                if status == ReplacementStatus.NEEDS_REVISION.value:
                    warning_lines.append(f"{indent_str}# WARNING: NEEDS MANUAL REVIEW - {reason}\n")
                    warning_lines.append(f"{indent_str}# Original path: {path}\n")
                else:
                    warning_lines.append(f"{indent_str}# WARNING: SMA-PATH-NOT-REPLACED - {reason}\n")
                    warning_lines.append(f"{indent_str}# Original path: {path}\n")
                warning_count += 1
            
            # Insert before the target line
            for warning_line in reversed(warning_lines):
                lines.insert(line_num - 1, warning_line)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        return warning_count
    
    except Exception as e:
        print(f"Error injecting warnings to {file_path}: {e}")
        return 0


def inject_warnings_notebook(file_path: Path, skipped: List[PathReplacement]) -> int:
    """Inject warning comments for skipped paths in a Jupyter notebook"""
    # Build a map of (cell_index, line_number) to warnings needed
    warnings_by_location: Dict[Tuple[int, int], List[Tuple[str, str, str]]] = {}
    
    for repl in skipped:
        for occ in repl.occurrences:
            if occ.file == str(file_path) and occ.cell_index is not None:
                key = (occ.cell_index, occ.line_number)
                if key not in warnings_by_location:
                    warnings_by_location[key] = []
                warnings_by_location[key].append((repl.original, repl.status.value, repl.reason))
    
    if not warnings_by_location:
        return 0
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            notebook = json.load(f)
        
        warning_count = 0
        
        # Group by cell
        by_cell: Dict[int, Dict[int, List[Tuple[str, str, str]]]] = {}
        for (cell_idx, line_num), warnings in warnings_by_location.items():
            if cell_idx not in by_cell:
                by_cell[cell_idx] = {}
            by_cell[cell_idx][line_num] = warnings
        
        for cell_idx, line_warnings in by_cell.items():
            cell = notebook['cells'][cell_idx]
            source = cell.get('source', [])
            
            # Normalize source to list of lines
            if isinstance(source, str):
                lines = source.split('\n')
                was_string = True
            elif isinstance(source, list) and len(source) == 1 and '\n' in source[0]:
                lines = source[0].split('\n')
                was_string = False
            else:
                lines = list(''.join(source).split('\n'))
                was_string = False
            
            # Insert warnings from bottom to top
            for line_num in sorted(line_warnings.keys(), reverse=True):
                warnings = line_warnings[line_num]
                # Get indentation
                target_line = lines[line_num - 1] if line_num <= len(lines) else ""
                indent = len(target_line) - len(target_line.lstrip())
                indent_str = " " * indent
                
                for path, status, reason in reversed(warnings):
                    if status == ReplacementStatus.NEEDS_REVISION.value:
                        warning_line1 = f"{indent_str}# WARNING: NEEDS MANUAL REVIEW - {reason}"
                        warning_line2 = f"{indent_str}# Original path: {path}"
                        lines.insert(line_num - 1, warning_line1)
                        lines.insert(line_num, warning_line2)
                    else:
                        warning_line1 = f"{indent_str}# WARNING: SMA-PATH-NOT-REPLACED - {reason}"
                        warning_line2 = f"{indent_str}# Original path: {path}"
                        lines.insert(line_num - 1, warning_line1)
                        lines.insert(line_num, warning_line2)
                    warning_count += 1
            
            # Restore source format
            cell['source'] = [line + '\n' for line in lines[:-1]] + [lines[-1]] if lines else []
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(notebook, f, indent=1, ensure_ascii=False)
        
        return warning_count
    
    except Exception as e:
        print(f"Error injecting warnings to {file_path}: {e}")
        return 0


def inject_warnings(replacements: List[PathReplacement], dry_run: bool = False) -> Dict[str, int]:
    """Inject warning comments for skipped paths and paths that need revision"""
    if dry_run:
        return {}
    
    # Include both skipped paths AND paths that need revision
    needs_warnings = [r for r in replacements if r.status != ReplacementStatus.REPLACED]
    if not needs_warnings:
        return {}
    
    # Get unique files
    files = set()
    for repl in needs_warnings:
        for occ in repl.occurrences:
            files.add(occ.file)
    
    results = {}
    for file_path in files:
        path = Path(file_path)
        if path.suffix == '.py':
            count = inject_warnings_python(path, needs_warnings)
        elif path.suffix == '.ipynb':
            count = inject_warnings_notebook(path, needs_warnings)
        else:
            count = 0
        results[file_path] = count
    
    return results


def generate_report(
    replacements: List[PathReplacement],
    replacement_results: Dict[str, int],
    warning_results: Dict[str, int],
    prefix: str,
    output_path: Path
) -> None:
    """Generate a JSON report of all path replacements and skipped paths"""
    from datetime import datetime
    
    replaced = [r for r in replacements if r.status == ReplacementStatus.REPLACED]
    needs_revision = [r for r in replacements if r.status == ReplacementStatus.NEEDS_REVISION]
    skipped = [r for r in replacements if r.status not in (ReplacementStatus.REPLACED, ReplacementStatus.NEEDS_REVISION)]
    
    # Build report structure
    report = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "prefix": prefix,
            "total_paths_found": len(replacements),
            "paths_replaced": len(replaced),
            "paths_needs_revision": len(needs_revision),
            "paths_skipped": len(skipped),
        },
        "summary": {
            "total_replacements_applied": sum(replacement_results.values()),
            "total_warnings_injected": sum(warning_results.values()),
            "files_modified": list(set(list(replacement_results.keys()) + list(warning_results.keys()))),
        },
        "replaced_paths": [],
        "needs_revision_paths": [],
        "skipped_paths": [],
        "by_file": {},
    }
    
    # Add replaced paths details
    for repl in replaced:
        report["replaced_paths"].append({
            "original": repl.original,
            "transformed": repl.transformed,
            "reason": repl.reason,
            "occurrences": [
                {
                    "file": occ.file,
                    "line": occ.line_number,
                    "cell_index": occ.cell_index,
                    "detection_method": occ.detection_method.value,
                }
                for occ in repl.occurrences
            ]
        })
    
    # Add needs_revision paths details
    for repl in needs_revision:
        report["needs_revision_paths"].append({
            "original": repl.original,
            "transformed": repl.transformed,
            "reason": repl.reason,
            "occurrences": [
                {
                    "file": occ.file,
                    "line": occ.line_number,
                    "cell_index": occ.cell_index,
                    "detection_method": occ.detection_method.value,
                }
                for occ in repl.occurrences
            ]
        })
    
    # Add skipped paths details
    for repl in skipped:
        report["skipped_paths"].append({
            "original": repl.original,
            "status": repl.status.value,
            "reason": repl.reason,
            "occurrences": [
                {
                    "file": occ.file,
                    "line": occ.line_number,
                    "cell_index": occ.cell_index,
                    "detection_method": occ.detection_method.value,
                }
                for occ in repl.occurrences
            ]
        })
    
    # Build by-file breakdown
    all_files = set()
    for repl in replacements:
        for occ in repl.occurrences:
            all_files.add(occ.file)
    
    for file_path in all_files:
        file_replaced = []
        file_needs_revision = []
        file_skipped = []
        
        for repl in replaced:
            file_occs = [o for o in repl.occurrences if o.file == file_path]
            if file_occs:
                file_replaced.append({
                    "original": repl.original,
                    "transformed": repl.transformed,
                    "lines": [o.line_number for o in file_occs],
                })
        
        for repl in needs_revision:
            file_occs = [o for o in repl.occurrences if o.file == file_path]
            if file_occs:
                file_needs_revision.append({
                    "original": repl.original,
                    "transformed": repl.transformed,
                    "reason": repl.reason,
                    "lines": [o.line_number for o in file_occs],
                })
        
        for repl in skipped:
            file_occs = [o for o in repl.occurrences if o.file == file_path]
            if file_occs:
                file_skipped.append({
                    "original": repl.original,
                    "status": repl.status.value,
                    "reason": repl.reason,
                    "lines": [o.line_number for o in file_occs],
                })
        
        report["by_file"][file_path] = {
            "replacements_applied": replacement_results.get(file_path, 0),
            "warnings_injected": warning_results.get(file_path, 0),
            "replaced_paths": file_replaced,
            "needs_revision_paths": file_needs_revision,
            "skipped_paths": file_skipped,
        }
    
    # Write JSON report
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ JSON Report generated: {output_path}")
    
    # Generate CSV report as well
    csv_path = output_path.with_suffix('.csv')
    generate_csv_report(replacements, prefix, csv_path)


def generate_csv_report(
    replacements: List[PathReplacement],
    prefix: str,
    output_path: Path
) -> None:
    """Generate a CSV report of all path replacements and skipped paths"""
    import csv
    from datetime import datetime
    
    # Create CSV with all path information
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        # Header
        writer.writerow([
            'Original Path',
            'Transformed Path',
            'Status',
            'Reason',
            'Detection Method',
            'File',
            'Location',
            'Line Content (excerpt)'
        ])
        
        # Write each path occurrence
        for repl in replacements:
            for occ in repl.occurrences:
                # Format location
                if occ.cell_index is not None:
                    location = f"cell[{occ.cell_index}]:line {occ.line_number}"
                else:
                    location = f"line {occ.line_number}"
                
                # Get line content excerpt (first 100 chars)
                line_excerpt = occ.line_content.strip()[:100]
                if len(occ.line_content.strip()) > 100:
                    line_excerpt += "..."
                
                writer.writerow([
                    repl.original,
                    repl.transformed if repl.transformed else "N/A",
                    repl.status.value,
                    repl.reason,
                    occ.detection_method.value,
                    Path(occ.file).name,
                    location,
                    line_excerpt
                ])
    
    print(f"✅ CSV Report generated: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Replace embedded file paths in SMA-converted Snowpark code'
    )
    parser.add_argument('files', nargs='+', help='Files to scan')
    parser.add_argument('--prefix', help='Prefix for transformed paths (required unless --scan-only)')
    parser.add_argument('--dry-run', action='store_true', help='Show changes without applying')
    parser.add_argument('--scan-only', action='store_true', help='Only scan and show findings')
    parser.add_argument('--report', type=str, metavar='PATH', 
                        help='Custom path for JSON report file (default: auto-generated in target directory)')
    parser.add_argument('--no-report', action='store_true',
                        help='Do not generate a report file')
    parser.add_argument('--no-warnings', action='store_true',
                        help='Do not inject warning comments for skipped paths')
    parser.add_argument('--skip-git-check', action='store_true',
                        help='Skip git repository check and proceed without prompting')
    
    args = parser.parse_args()
    
    # Validate: prefix required unless scan-only
    if not args.scan_only and not args.prefix:
        parser.error('--prefix is required unless --scan-only is used')
    
    # Convert to Path objects
    file_paths = [Path(f) for f in args.files]
    
    # Scan files (now always checks ALL function calls)
    print("Scanning files...")
    occurrences = scan_files(file_paths)
    
    # Print findings
    print_findings(occurrences)
    
    if args.scan_only or not occurrences:
        return
    
    # Generate replacements
    replacements = generate_replacements(occurrences, args.prefix)
    
    # Print planned replacements
    print_replacements(replacements, args.prefix)
    
    # Track results
    replacement_results: Dict[str, int] = {}
    warning_results: Dict[str, int] = {}
    
    # Apply replacements
    if not args.dry_run:
        # Check if target directory is a git repository (unless --skip-git-check)
        if not args.skip_git_check:
            target_dir = file_paths[0].parent
            is_git_repo = sma_api.git_is_repo(str(target_dir))
            
            if not is_git_repo:
                # Not a git repo - ask user for confirmation
                print("\n" + "=" * 80)
                print("⚠️  GIT REPOSITORY CHECK")
                print("=" * 80)
                print(f"\nThe target directory is NOT a git repository:")
                print(f"  {target_dir.absolute()}")
                print("\nWithout version control, changes cannot be easily reverted.")
                
                # Get unique list of files that will be modified
                files_to_modify = set()
                for repl in replacements:
                    if repl.status == ReplacementStatus.REPLACED or repl.status == ReplacementStatus.NEEDS_REVISION:
                        for occ in repl.occurrences:
                            files_to_modify.add(Path(occ.file))
                
                if files_to_modify:
                    print(f"\n{len(files_to_modify)} file(s) will be modified:")
                    for f in sorted(files_to_modify):
                        print(f"  - {f.name}")
                    
                    print("\n" + "=" * 80)
                    
                    # Prompt user for confirmation
                    while True:
                        response = input("\nDo you want to proceed with changes? (yes/no): ").strip().lower()
                        if response in ['yes', 'y', 'si']:
                            print("\n✓ Proceeding with changes...")
                            break
                        elif response in ['no', 'n']:
                            print("\n✗ Changes cancelled by user.")
                            return
                        else:
                            print("Please answer 'yes' or 'no'")
            else:
                print(f"\n✓ Git repository detected: {target_dir.absolute()}")
        
        print("\nApplying replacements...")
        replacement_results = apply_replacements(replacements, dry_run=False)
        
        print("\nReplacement Results:")
        total = 0
        for file_path, count in replacement_results.items():
            print(f"  {Path(file_path).name}: {count} replacements")
            total += count
        print(f"\nTotal: {total} replacements applied")
        
        # Inject warnings for skipped paths
        if not args.no_warnings:
            warning_results = inject_warnings(replacements, dry_run=False)
            if warning_results:
                print("\nWarning Injection Results:")
                total_warnings = 0
                for file_path, count in warning_results.items():
                    print(f"  {Path(file_path).name}: {count} warnings injected")
                    total_warnings += count
                print(f"\nTotal: {total_warnings} warnings injected for skipped paths")
        
        # Generate report automatically (unless --no-report)
        if not args.no_report:
            # Determine report path
            if args.report:
                report_path = Path(args.report)
            else:
                # Auto-generate report path in the same directory as the first file
                from datetime import datetime
                target_dir = file_paths[0].parent
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                report_path = target_dir / f"sma_path_replacement_report_{timestamp}.json"
            
            generate_report(
                replacements=replacements,
                replacement_results=replacement_results,
                warning_results=warning_results,
                prefix=args.prefix,
                output_path=report_path
            )
    else:
        print("\n[DRY RUN] No changes applied.")
        # Show what would have been done
        skipped = [r for r in replacements if r.status != ReplacementStatus.REPLACED]
        if skipped and not args.no_warnings:
            print(f"  Would inject {len(skipped)} warning comments for skipped paths")


if __name__ == '__main__':
    main()

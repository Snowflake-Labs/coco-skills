"""Parse-only code analysis: Python AST SQL detection, config syntax, language coverage.

Candidate code is parsed, never compiled to bytecode, imported or executed.
ast.parse builds a syntax tree only. Every function returns (code, line)
pairs with fixed codes; parser messages and candidate text are never reported.
"""
import ast
import json
from pathlib import PurePosixPath
import re
import textwrap

try:
    import tomllib
except ImportError:  # Python < 3.11 hosts never run container checks.
    tomllib = None
import yaml

from risk_checks import INTERPOLATION, KEYWORDS, MAX_PER_CODE

MAX_AST_NODES = 50_000
MAX_YAML_NODES, MAX_YAML_DEPTH = 20_000, 64
START = re.compile(r'^\s*' + KEYWORDS + r'\b')
CLAUSE = re.compile(r'^\s*(?:FROM|WHERE|VALUES|SET|INTO|JOIN|AND|OR|ORDER|GROUP|LIMIT|ON|UNION)\b')
COMMENTS = re.compile(r'^(?:\s*--[^\n]*\n)+')
SINKS = {'sql', 'execute', 'executemany', 'execute_string', 'execute_async',
         'read_sql', 'read_sql_query', 'text'}
SQL_KEYWORDS = ('query', 'sql', 'command', 'statement')
PYTHON_FENCES = ('python', 'py', 'python3')
CONFIG_SUFFIXES = {'.json', '.yaml', '.yml', '.toml'}
TEMPLATE_MARKERS = re.compile(r'\{\{|\{%|<%')
# Code the reviewer decodes and regex-scans but does not parse. Python, JSON,
# YAML and TOML are parsed; prose, data and markup are not code.
UNPARSED_SUFFIXES = {
    '.sh', '.bash', '.zsh', '.ps1', '.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.sql',
    '.r', '.scala', '.sbt', '.java', '.kt', '.kts', '.go', '.rs', '.rb', '.php', '.c', '.h',
    '.cc', '.cpp', '.hpp', '.cs', '.lua', '.pl', '.swift', '.groovy',
}
UNPARSED_NAMES = {'Dockerfile', 'Containerfile', 'Makefile', 'Justfile', 'Jenkinsfile'}


def _limited(found):
    counts, kept = {}, []
    for code, line in found:
        if counts.get(code, 0) < MAX_PER_CODE and (code, line) not in kept:
            counts[code] = counts.get(code, 0) + 1
            kept.append((code, line))
    return kept


def parse_python(text):
    """Return (tree, None), or (None, ('syntax', line)) / (None, ('unchecked', None))."""
    if '\x00' in text:
        # Some Python versions raise SyntaxError for NUL bytes; that is a
        # decoding problem, not a syntax error in the code.
        return None, ('unchecked', None)
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return None, ('syntax', error.lineno if isinstance(error.lineno, int) and error.lineno > 0 else None)
    except (ValueError, RecursionError, MemoryError):
        # ValueError covers null bytes; the others are pathological nesting.
        return None, ('unchecked', None)
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        return None, ('unchecked', None)
    return tree, None


# --- Python SQL construction ------------------------------------------------

def _is_sql_text(value):
    """True when text starts like SQL: a statement keyword, or a clause fragment.

    Leading SQL comment lines are skipped so generated scripts that open with a
    banner still count. Prose that merely mentions SQL words mid-sentence (log
    and error messages) does not.
    """
    if not isinstance(value, str):
        return False
    value = COMMENTS.sub('', value)
    return bool(START.search(value) or CLAUSE.search(value))


def _constant_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _operands(node):
    """Flatten a chain of + into its operands."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _operands(node.left) + _operands(node.right)
    return [node]


def _built(node, tainted):
    """True when node builds SQL text from non-constant values."""
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.JoinedStr):
        literal = ''.join(part.value for part in node.values if _constant_str(part))
        dynamic = [part for part in node.values if isinstance(part, ast.FormattedValue)
                   and not isinstance(part.value, ast.Constant)]
        return bool(dynamic) and _is_sql_text(literal)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = _operands(node)
        sql = any(_constant_str(part) and _is_sql_text(part.value) for part in parts)
        sql = sql or any(_built(part, tainted) for part in parts)
        dynamic = any(not _constant_str(part) for part in parts)
        return sql and dynamic
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return (_constant_str(node.left) and _is_sql_text(node.left.value)
                and not isinstance(node.right, ast.Constant))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'format' and _constant_str(node.func.value)
            and _is_sql_text(node.func.value.value) and (node.args or node.keywords)):
        return True
    return False


def _scope_nodes(scope):
    """Yield nodes in a scope without descending into nested functions or classes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _tainted(scope):
    """Names assigned built SQL in this scope (flow-insensitive, to a fixpoint)."""
    tainted = set()
    for _ in range(4):
        before = len(tainted)
        for node in _scope_nodes(scope):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if _built(node.value, tainted):
                    tainted.update(target.id for target in targets if isinstance(target, ast.Name))
            elif (isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add)
                  and isinstance(node.target, ast.Name)):
                value = node.value
                if (node.target.id in tainted and not _constant_str(value)) or _built(value, tainted):
                    tainted.add(node.target.id)
        if len(tainted) == before:
            break
    return tainted


def _sink_argument(call):
    name = call.func.attr if isinstance(call.func, ast.Attribute) else (
        call.func.id if isinstance(call.func, ast.Name) else None)
    if name not in SINKS:
        return None
    if call.args:
        return call.args[0]
    return next((keyword.value for keyword in call.keywords if keyword.arg in SQL_KEYWORDS), None)


def python_sql_findings(tree, offset=0):
    """Return security.sql_interpolation (code, line) pairs for a parsed module."""
    found = []
    scopes = [tree] + [node for node in ast.walk(tree)
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
    for scope in scopes:
        tainted = _tainted(scope)
        reported = set()
        nodes = list(_scope_nodes(scope))
        for node in nodes:
            if isinstance(node, ast.Call):
                argument = _sink_argument(node)
                if argument is not None and _built(argument, tainted):
                    found.append(('security.sql_interpolation', node.lineno + offset))
                    reported.update(id(child) for child in ast.walk(argument))
        for node in nodes:
            if id(node) in reported or isinstance(node, ast.Name) or not _built(node, tainted):
                continue
            # Report the outermost built expression only.
            reported.update(id(child) for child in ast.walk(node))
            found.append(('security.sql_interpolation', node.lineno + offset))
    # Code stored as data (docstring examples, fixtures, generated stored
    # procedure bodies) is opaque to the AST, so scan multi-line string
    # literals with the regex patterns. Line numbers are approximate when the
    # literal contains escape sequences.
    for node in ast.walk(tree):
        if _constant_str(node) and '\n' in node.value:
            for pattern in INTERPOLATION:
                for match in pattern.finditer(node.value):
                    found.append(('security.sql_interpolation',
                                  node.lineno + offset + node.value.count('\n', 0, match.start())))
    found.sort(key=lambda item: item[1])
    return _limited(found)


# --- Markdown Python fences -------------------------------------------------

def python_fences(text):
    """Yield (first_body_line, body, start_offset, end_offset) for Python fences."""
    lines = text.split('\n')
    offset, inside, start, first, marker = 0, False, 0, 0, None
    for number, line in enumerate(lines, 1):
        fence = re.match(r'^ {0,3}(`{3,}|~{3,})\s*([\w+-]*)', line)
        if fence and inside and fence[1][0] == marker[0] and len(fence[1]) >= len(marker):
            yield first, text[start:offset], start, offset
            inside = False
        elif fence and not inside and fence[2].lower() in PYTHON_FENCES:
            inside, marker, start, first = True, fence[1], offset + len(line) + 1, number + 1
        offset += len(line) + 1


def mask(text, ranges):
    """Blank character ranges, keeping newlines so line numbers stay stable."""
    chars = list(text)
    for start, end in ranges:
        for index in range(start, min(end, len(chars))):
            if chars[index] != '\n':
                chars[index] = ' '
    return ''.join(chars)


# --- Config syntax ----------------------------------------------------------

class _ConfigLoader(yaml.SafeLoader):
    """Compose-only YAML with node/depth budgets. Aliases and tags are legal YAML."""

    def __init__(self, stream):
        super().__init__(stream)
        self.depth = self.nodes = 0

    def compose_node(self, parent, index):
        self.depth += 1
        self.nodes += 1
        if self.depth > MAX_YAML_DEPTH or self.nodes > MAX_YAML_NODES:
            raise RecursionError()
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1


def _duplicate_key(node):
    """True when any YAML mapping repeats a scalar key."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, yaml.MappingNode):
            keys = [key.value for key, _ in current.value if isinstance(key, yaml.ScalarNode)]
            if len(keys) != len(set(keys)):
                return True
            stack.extend(value for pair in current.value for value in pair)
        elif isinstance(current, yaml.SequenceNode):
            stack.extend(current.value)
    return False


def _json_no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate')
    return dict(pairs)


def config_findings(path, text):
    """Return syntax.config / syntax.unchecked findings for JSON, YAML and TOML."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in CONFIG_SUFFIXES:
        return []
    if TEMPLATE_MARKERS.search(text):
        return [('syntax.unchecked', None)]
    try:
        if suffix == '.json':
            try:
                json.loads(text, object_pairs_hook=_json_no_duplicates)
            except json.JSONDecodeError as error:
                return [('syntax.config', error.lineno)]
        elif suffix in ('.yaml', '.yml'):
            try:
                if any(_duplicate_key(node) for node in yaml.compose_all(text, Loader=_ConfigLoader)):
                    return [('syntax.config', None)]
            except yaml.MarkedYAMLError as error:
                mark = error.problem_mark or error.context_mark
                return [('syntax.config', mark.line + 1 if mark else None)]
            except yaml.YAMLError:
                return [('syntax.config', None)]
        elif tomllib is not None:
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as error:
                match = re.search(r'line (\d+)', str(error))
                return [('syntax.config', int(match[1]) if match else None)]
    except ValueError:
        return [('syntax.config', None)]
    except (RecursionError, MemoryError):
        return [('syntax.unchecked', None)]
    return []


# --- Language coverage ------------------------------------------------------

def unparsed_language(path, text):
    """True for decoded code files in languages the reviewer does not parse."""
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in UNPARSED_SUFFIXES or name in UNPARSED_NAMES:
        return True
    if not suffix and text.startswith('#!'):
        return 'python' not in text.split('\n', 1)[0]
    return False


# --- Entry point ------------------------------------------------------------

def code_findings(path, text):
    """Return (findings, masked_text_or_None) for one decoded file.

    masked_text is the text with Python regions the AST already covered blanked
    out, for the regex SQL checks to run on; None means regex interpolation
    should be skipped entirely because the whole file was analysed by AST.
    """
    suffix = PurePosixPath(path).suffix.lower()
    found = []
    masked = text
    if suffix == '.py' or (not suffix and text.startswith('#!') and 'python' in text.split('\n', 1)[0]):
        tree, failure = parse_python(text)
        if tree is not None:
            return python_sql_findings(tree), None
        found.append(('syntax.python', failure[1]) if failure[0] == 'syntax'
                     else ('syntax.unchecked', None))
        return _limited(found), text
    if suffix in ('.md', '.markdown', '.rst', '.txt'):
        covered = []
        for first, body, start, end in python_fences(text):
            # Fences nested in list items keep the list indentation; strip it.
            tree, failure = parse_python(textwrap.dedent(body))
            if tree is not None:
                found += python_sql_findings(tree, offset=first - 1)
                covered.append((start, end))
            elif failure[0] == 'syntax':
                found.append(('syntax.python_example', first - 1 + failure[1] if failure[1] else first))
        masked = mask(text, covered)
    found += config_findings(path, text)
    if unparsed_language(path, text):
        found.append(('syntax.unparsed_language', None))
    found.sort(key=lambda item: (item[1] or 0, item[0]))
    return _limited(found), masked

"""Deterministic SQL-escaping and dependency-pinning checks over decoded text.

Pure functions over (path, text) pairs; no filesystem, network or execution.
Each check yields (code, line) tuples. Codes map to fixed protocol messages,
so matched candidate text is never reported. Patterns are deliberately
conservative: SQL rules match uppercase statement keywords, and dependency
rules read only well-known manifest formats and install commands.
"""
from functools import lru_cache
import json
from pathlib import Path, PurePosixPath
import re

try:
    import tomllib
except ImportError:  # Python < 3.11 hosts never run container checks.
    tomllib = None

from ingest import lf_line_count, lf_lines

MAX_PER_CODE = 5

KEYWORDS = (r'(?:SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|GRANT|REVOKE|CALL|COPY'
            r'|PUT|GET|TRUNCATE|USE|SHOW|DESCRIBE|DESC|EXECUTE|UNDROP|REMOVE|LIST|WITH)')

# Python/JS strings that build SQL from variables. Uppercase keywords only, so
# prose such as "select a file" does not match.
INTERPOLATION = tuple(re.compile(pattern) for pattern in (
    # f-strings: f"SELECT ... {value}" (not {{ literal braces }}).
    r'\b[fF][rR]?(?:"""|\'\'\'|"|\')\s*' + KEYWORDS + r'\b[^\n]*?\{(?!\{)[^}\n]*\}',
    # str.format: "SELECT ... {}".format(value)
    r'(?:"|\')\s*' + KEYWORDS + r'\b[^"\'\n]*\{[^}\n]*\}[^"\'\n]*(?:"|\')\s*\.format\(',
    # %-formatting: "SELECT ... %s" % value (bind parameters pass a tuple instead)
    r'(?:"|\')\s*' + KEYWORDS + r'\b[^"\'\n]*%[sdr(][^"\'\n]*(?:"|\')\s*%\s*[A-Za-z_(]',
    # Concatenation: "SELECT ... " + value, or value + " FROM ..."
    r'(?:"|\')\s*' + KEYWORDS + r'\b[^"\'\n]*(?:"|\')\s*\+\s*[A-Za-z_]',
    r'[A-Za-z_\])]\s*\+\s*(?:"|\')\s*(?:FROM|WHERE|AND|OR|VALUES|SET|INTO|TABLE|JOIN)\b',
    # JavaScript template literals: `SELECT ... ${value}`
    r'`\s*' + KEYWORDS + r'\b[^`]*\$\{',
    # Snowflake JavaScript procedures: snowflake.execute({sqlText: "..." + value})
    r'sqlText\s*:\s*(?:"[^"\n]*"|\'[^\'\n]*\')\s*\+',
    # Snowpark / connector calls given an f-string or concatenation directly.
    r'\.(?:sql|execute|execute_string|execute_async)\(\s*[fF](?:"|\')',
    r'\.(?:sql|execute|execute_string|execute_async)\(\s*(?:"[^"\n]*"|\'[^\'\n]*\')\s*\+',
))
# Snowflake Scripting dynamic SQL built from variables or concatenation.
# EXECUTE IMMEDIATE FROM @stage/file is a static command and is allowed.
DYNAMIC = (re.compile(r'EXECUTE\s+IMMEDIATE\s+(?!FROM\b)(?![\'$])[:A-Za-z_]'),
           re.compile(r'EXECUTE\s+IMMEDIATE\s+[^;\n]*\|\|'))
# SnowSQL &var / &{var} and Snowflake CLI <% var %> client-side substitution,
# checked only in .sql files and ```sql fences where they are SQL text.
SUBSTITUTION = re.compile(r'&\{\s*[A-Za-z_][\w.]*\s*\}|(?<![&\w])&[A-Za-z_]\w*\b|<%\s*[A-Za-z_][\w.]*\s*%>')
# Classic injection payloads on a single line.
PAYLOADS = re.compile(
    r"(?i)'[ \t]*or[ \t]+'?1'?[ \t]*=[ \t]*'?1"
    r"|[A-Za-z0-9_]'[ \t]*;[ \t]*(?:drop|delete|truncate|alter|grant)[ \t]+\w[^\n]*--"
    r"|union[ \t]+(?:all[ \t]+)?select[ \t]+null[ \t]*,")
# Prose telling the agent to splice values into SQL. A sentence that also says
# to use IDENTIFIER(), bind variables or parameters is treated as safe guidance.
SPLICING = re.compile(
    r'(?i)\b(?:substitute|interpolate|splice|concatenate|paste|inject|insert|plug|embed|append)\b'
    r'[^.\n]{0,60}\b(?:value|name|input|argument|variable|identifier|string|text)s?\b'
    r'[^.\n]{0,40}\b(?:into|in(?:to)?|inside)\b[^.\n]{0,30}\b(?:the\s+)?(?:sql|query|statement)\b')
SAFE_SPLICING = re.compile(r'(?i)IDENTIFIER\s*\(|bind(?:ing)?\s+(?:variable|parameter)|parameteri[sz]ed|placeholder')

MANIFESTS = ('requirements', 'pyproject.toml', 'package.json', 'environment.yml', 'environment.yaml')
INSTALL = re.compile(
    r'(?:^|[\s`$;&|(])((?:python3?\s+-m\s+)?pip3?\s+install|uv\s+pip\s+install|uv\s+add|poetry\s+add'
    r'|pipx\s+(?:install|run)|uvx|npm\s+(?:install|i|add)|yarn\s+add|pnpm\s+(?:add|install|i)'
    r'|npx|conda\s+install|mamba\s+install|brew\s+install)\b([^\n`|;&]*)')
OPTIONS_WITH_VALUE = {'-r', '--requirement', '-c', '--constraint', '-e', '--editable', '-i',
                      '--index-url', '--extra-index-url', '-f', '--find-links', '--target', '-t',
                      '-p', '--package', '--python', '--prefix', '--registry', '-n', '--name',
                      '--channel', '--spec'}


def _limited(found):
    """Keep at most MAX_PER_CODE (code, line) pairs per code, preserving order."""
    counts, kept = {}, []
    for code, line in found:
        if counts.get(code, 0) < MAX_PER_CODE and (code, line) not in kept:
            counts[code] = counts.get(code, 0) + 1
            kept.append((code, line))
    return kept


def _sql_regions(path, text):
    """Yield (offset, body) for SQL text: whole .sql files or ```sql fences."""
    if path.lower().endswith('.sql'):
        yield 0, text
        return
    offset, inside, start = 0, False, 0
    for line in text.split('\n'):
        fence = re.match(r'^ {0,3}(`{3,}|~{3,})\s*(\w*)', line)
        if fence:
            if inside:
                yield start, text[start:offset]
                inside = False
            elif fence[2].lower() in ('sql', 'snowsql', 'snowflake'):
                inside, start = True, offset + len(line) + 1
        offset += len(line) + 1


def sql_findings(path, text, interpolation=True):
    """Return blocking SQL-escaping findings as (code, line) pairs.

    interpolation=False skips the regex interpolation patterns, for Python
    sources whose SQL construction the AST analysis in code_checks covers.
    """
    found = []
    for pattern in INTERPOLATION if interpolation else ():
        found += [('security.sql_interpolation', lf_line_count(text, m.start()))
                  for m in pattern.finditer(text)]
    for pattern in DYNAMIC:
        found += [('security.sql_dynamic', lf_line_count(text, m.start())) for m in pattern.finditer(text)]
    for start, body in _sql_regions(path, text):
        found += [('security.sql_substitution', lf_line_count(text, start + m.start()))
                  for m in SUBSTITUTION.finditer(body)]
    found += [('security.sql_payload', lf_line_count(text, m.start())) for m in PAYLOADS.finditer(text)]
    if PurePosixPath(path).suffix.lower() in ('.md', '.txt', '.rst'):
        for number, line in enumerate(lf_lines(text), 1):
            for sentence in re.split(r'(?<=[.!?])\s+', line):
                if SPLICING.search(sentence) and not SAFE_SPLICING.search(sentence):
                    found.append(('security.sql_splicing', number))
    found.sort(key=lambda item: (item[1], item[0]))
    return _limited(found)


# --- Dependencies -----------------------------------------------------------

def _python_name(spec):
    match = re.match(r'\s*([A-Za-z0-9][A-Za-z0-9._-]*)', spec)
    return re.sub(r'[-_.]+', '-', match[1]).lower() if match else None


def _python_pin(spec):
    """Return the exact version for 'name==1.2.3' (markers/extras allowed), else None."""
    spec = spec.split(';', 1)[0]
    match = re.fullmatch(r'\s*[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?\s*===?\s*([0-9][A-Za-z0-9.+!-]*)\s*', spec)
    return match[1] if match and '*' not in match[1] else None


def _npm_pin(version):
    if not isinstance(version, str):
        return None
    return version if re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', version) else None


def _line_of(text, needle):
    index = text.find(needle)
    return lf_line_count(text, index) if index >= 0 else None


def manifest_dependencies(path, text):
    """Return (ecosystem, name, pinned_version_or_None, line) for a known manifest."""
    name = PurePosixPath(path).name
    deps = []
    if name.startswith('requirements') and name.endswith('.txt'):
        for number, raw in enumerate(lf_lines(text), 1):
            line = raw.split(' #', 1)[0].strip()
            if not line or line.startswith(('#', '-', 'git+', 'http:', 'https:', '.', '/')):
                continue
            package = _python_name(line)
            if package:
                deps.append(('PyPI', package, _python_pin(line), number))
    elif name == 'pyproject.toml' and tomllib is not None:
        try:
            data = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError):
            return deps
        project = data.get('project', {}) if isinstance(data.get('project'), dict) else {}
        specs = list(project.get('dependencies') or [])
        for group in (project.get('optional-dependencies') or {}).values():
            specs += list(group or [])
        for group in ((data.get('dependency-groups') or {}).values()):
            specs += [item for item in (group or []) if isinstance(item, str)]
        for spec in specs:
            if isinstance(spec, str) and _python_name(spec):
                deps.append(('PyPI', _python_name(spec), _python_pin(spec), _line_of(text, spec)))
        poetry = ((data.get('tool') or {}).get('poetry') or {})
        for table in ('dependencies', 'dev-dependencies'):
            for package, version in (poetry.get(table) or {}).items():
                if package.lower() == 'python':
                    continue
                exact = version if isinstance(version, str) and re.fullmatch(r'=?=?\s*\d[\w.+!-]*', version) else None
                deps.append(('PyPI', _python_name(package), exact.lstrip('= ') if exact else None,
                             _line_of(text, package)))
    elif name == 'package.json':
        try:
            data = json.loads(text)
        except ValueError:
            return deps
        if isinstance(data, dict):
            for table in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'):
                for package, version in (data.get(table) or {}).items():
                    deps.append(('npm', package, _npm_pin(version), _line_of(text, f'"{package}"')))
    elif name in ('environment.yml', 'environment.yaml'):
        for number, raw in enumerate(lf_lines(text), 1):
            match = re.match(r'^\s*-\s+([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:(==?)\s*([0-9][\w.+!-]*))?\s*$', raw)
            if match and match[1] not in ('pip', 'python'):
                deps.append(('PyPI', _python_name(match[1]), match[3], number))
    return deps


def install_commands(text):
    """Return (ecosystem, name, pinned_version_or_None, line) for install commands in text."""
    deps = []
    for match in INSTALL.finditer(text):
        tool, rest = re.sub(r'\s+', ' ', match[1]), match[2]
        line = lf_line_count(text, match.start(1))
        ecosystem = 'npm' if tool.split()[0] in ('npm', 'yarn', 'pnpm', 'npx') else (
            'other' if tool.split()[0] in ('brew', 'conda', 'mamba') else 'PyPI')
        tokens, skip = rest.split(), False
        for token in tokens:
            token = token.strip('"\'')
            if skip:
                skip = False
                continue
            if token in OPTIONS_WITH_VALUE:
                skip = True
                continue
            if token.startswith('-') or not token or token in ('.', '..') or '/' in token[:1]:
                continue
            if token.endswith(('.whl', '.txt', '.tar.gz', '.zip')) or token.startswith(('git+', 'http')):
                continue
            if not re.match(r'@?[A-Za-z0-9]', token):
                break
            if ecosystem == 'npm':
                package, _, version = token.rpartition('@') if token.count('@') > (1 if token.startswith('@') else 0) else (token, '', '')
                deps.append(('npm', package, _npm_pin(version), line))
            elif ecosystem == 'PyPI':
                if tool.startswith(('uvx', 'pipx run')) and '@' in token:
                    package, _, version = token.partition('@')
                    deps.append(('PyPI', _python_name(package), version or None, line))
                else:
                    deps.append(('PyPI', _python_name(token), _python_pin(token), line))
            else:
                deps.append(('other', token.split('=')[0], token.split('=', 1)[1] if '=' in token else None, line))
            if tool in ('npx', 'uvx', 'pipx run'):
                break  # Remaining tokens are the tool's own arguments.
    return deps


@lru_cache(maxsize=1)
def advisories():
    """Load the pinned offline advisory snapshot shipped with the reviewer."""
    resource = Path(__file__).resolve().parent / 'resources' / 'advisories.json'
    value = json.loads(resource.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('format') != 'osv-ranges/1':
        raise ValueError('advisory_resource_format')
    return value


def _version_key(version):
    parts = re.findall(r'\d+|[A-Za-z]+', version)
    # Pre-release letters sort before the release; numbers compare numerically.
    return tuple((1, int(part)) if part.isdigit() else (0, part) for part in parts)


def vulnerable(ecosystem, name, version):
    """Return True when the snapshot lists a range containing this exact version."""
    for item in (advisories().get('packages', {}).get(ecosystem, {}).get(name, [])):
        introduced = item.get('introduced', '0')
        fixed, last = item.get('fixed'), item.get('last_affected')
        key = _version_key(version)
        if key < _version_key(introduced):
            continue
        if fixed is not None and key >= _version_key(fixed):
            continue
        if last is not None and key > _version_key(last):
            continue
        return True
    return False


def dependency_findings(path, text):
    """Return orange dependency findings as (code, line) pairs."""
    name = PurePosixPath(path).name
    deps = []
    if name.startswith('requirements') and name.endswith('.txt') or name in MANIFESTS:
        deps += manifest_dependencies(path, text)
    if PurePosixPath(path).suffix.lower() in ('.md', '.txt', '.rst', '.sh', '.bash', '.py', '.yml',
                                               '.yaml', '.toml', '.ps1') or name in ('Dockerfile',):
        deps += install_commands(text)
    found = []
    snapshot_empty = not advisories().get('packages')
    for ecosystem, package, version, line in deps:
        if version is None:
            found.append(('dependency.unpinned', line))
        elif ecosystem in ('PyPI', 'npm') and vulnerable(ecosystem, package, version):
            found.append(('dependency.vulnerable', line))
        elif snapshot_empty and ecosystem in ('PyPI', 'npm'):
            found.append(('dependency.unchecked', line))
    return _limited(found)

"""Pure deterministic checks over an in-memory snapshot; no filesystem or network."""
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

import yaml

from ingest import Entry, canonical, lf_line_count, lf_lines
from markdown_checks import check_documents
from catalog import related
from protocol import base_report, bind_report, finding
from policy import LICENSE_POLICY, METADATA, author_claims, match_license, valid_shape

TEXT_SUFFIXES = {'.md', '.txt', '.py', '.sh', '.bash', '.json', '.yaml', '.yml',
                 '.toml', '.sql', '.csv', '.js', '.ts', '.html', '.css', '.rst',
                 '.xml', '.ini', '.cfg', '.r', '.ps1', '.dockerignore'}
TEXT_NAMES = {'SKILL.md', 'LICENSE', 'Dockerfile', '.dockerignore', '.gitignore'}
SECRET_PATTERNS = [
    re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----'),
    re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{40,}\b'),
    re.compile(r'(?i)\b(?:password|api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*[\"\']?([A-Za-z0-9/+_=.-]{12,})'),
]


def has_secret(text):
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


class StrictLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self.depth = 0
        self.nodes = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError()
        self.depth += 1
        self.nodes += 1
        if self.depth > 20 or self.nodes > 2048:
            raise ValueError()
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError()
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_frontmatter(text):
    lines = lf_lines(text)
    if not lines or lines[0] != '---':
        raise ValueError()
    closing = next((index for index in range(1, len(lines)) if lines[index] == '---'), None)
    if closing is None:
        raise ValueError()
    raw = '\n'.join(lines[1:closing])
    if len(raw.encode()) > 16384:
        raise ValueError()
    value = yaml.load(raw, Loader=StrictLoader)
    if not isinstance(value, dict):
        raise ValueError()
    return value


def text_contents(entries):
    texts = {}
    unsupported = []
    for index, entry in enumerate(entries, 1):
        file_id = f'F{index:04d}'
        path = PurePosixPath(entry.path)
        try:
            text = entry.data.decode('utf-8')
            if '\x00' in text or (path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES):
                raise ValueError()
            texts[entry.path] = (file_id, text)
        except (UnicodeError, ValueError):
            unsupported.append(file_id)
    return texts, unsupported


@dataclass(frozen=True)
class _Analysis:
    # Only built inside this module. No public API accepts an analysis or report
    # as authority; immutable values cannot drift between review and packet use.
    entries: tuple
    texts: object
    unsupported: tuple
    report_bytes: bytes
    records: tuple


def review(name: str, entries: tuple[Entry, ...], catalog=None):
    return _review(name, tuple(sorted(entries, key=lambda entry: entry.path)), catalog)[0]


def _analyze(name, entries, catalog):
    entries = tuple(sorted(entries, key=lambda entry: entry.path))
    report, texts, unsupported, records = _review(name, entries, catalog)
    return _Analysis(entries, MappingProxyType(texts), tuple(unsupported), canonical(report),
                     tuple(MappingProxyType(record) for record in records))


def _review(name, entries, catalog):
    report = base_report()
    records = bind_report(report, name, entries, catalog)
    source_ids = {item['file_id']: item['source_sha256'] for item in report['sources']}
    texts, unsupported = text_contents(entries)
    report['coverage'] = {'files': len(entries), 'text_files': len(texts)}
    findings = report['findings']
    seen_findings = set()

    def add(code, severity='warning', file_id=None, line=None, field=None):
        item = finding(code, severity, file_id, line, field, source_ids.get(file_id))
        identity = (code, severity, file_id, line, item['field'])
        if identity not in seen_findings:
            if len(findings) >= 4096:
                raise ValueError('finding_limit')
            seen_findings.add(identity)
            findings.append(item)

    for file_id in unsupported:
        add('coverage.unsupported', 'error', file_id)
    # Scan decodable bytes even for unsupported formats; never print the match.
    for index, entry in enumerate(entries, 1):
        file_id = f'F{index:04d}'
        text = texts[entry.path][1] if entry.path in texts else entry.data.decode('utf-8', errors='replace')
        if has_secret(entry.path) or has_secret(text):
            add('security.secret', 'error', file_id)
        if 'skill_metadata' in PurePosixPath(entry.path).parts:
            add('format.internal_directory', 'error', file_id)
    for path, (file_id, text) in texts.items():
        for code, pattern in [
            ('policy.tone', r'(?i)\b(critical|failure|violation|danger|urgent|high-risk)\b|\U0001f534'),
            ('policy.identity', r'\b(USER_NAME|LOGIN_NAME|EMAIL|DISPLAY_NAME|CLIENT_IP|SOURCE_IP)\b'),
            ('policy.hardcoded', r'\b[A-Z][A-Z0-9_]*_(PROD|DEV|FINANCE)(?:_[A-Z0-9]+)*\b'),
        ]:
            match = re.search(pattern, text)
            if match:
                add(code, file_id=file_id, line=lf_line_count(text, match.start()))
    check_documents(texts, {entry.path for entry in entries}, add)
    meta = {}
    skill = texts.get('SKILL.md')
    if not skill:
        add('format.skill_missing', 'error')
    else:
        file_id, text = skill
        try:
            meta = parse_frontmatter(text)
        except (ValueError, yaml.YAMLError, TypeError, RecursionError):
            add('format.yaml', 'error', file_id)
        if meta:
            for field, rule in METADATA.items():
                value = meta.get(field)
                if rule.get('required') and (not isinstance(value, str) or not value.strip()):
                    add('format.required', 'error', file_id, field=field)
                if rule.get('recommended') and not value:
                    add('format.recommended', file_id=file_id, field=field)
                if field in meta and not valid_shape(value, rule['shape']):
                    add('format.field_type', 'error', file_id, field=field)
                if rule.get('values') and value and value not in rule['values']:
                    add('format.' + field, rule['severity'], file_id)
                if 'limit' in rule and isinstance(value, str) and len(value) > rule['limit']:
                    add('format.length', file_id=file_id, field=field)
            claims, conflict = author_claims(meta)
            if not claims:
                add('format.recommended', file_id=file_id, field='authors')
            if conflict:
                add('format.author_conflict', file_id=file_id)
            if 'id' in meta and (meta['id'] != name or not isinstance(meta['id'], str)
                                 or not re.fullmatch(METADATA['id']['pattern'], meta['id'])):
                add('format.id', 'error', file_id)
            candidate_name = meta.get('name')
            if isinstance(candidate_name, str) and (candidate_name != name or
                    not re.fullmatch(METADATA['name']['pattern'], candidate_name)):
                add('format.name', file_id=file_id)
            description = meta.get('description', '')
            description_rule = METADATA['description']
            if isinstance(description, str) and (not description.startswith(description_rule['prefix'])
                    or description_rule['trigger'] not in description.lower()
                    or re.search(description_rule['first_person'], description)):
                add('format.description', file_id=file_id)
            if re.search(r'(?i)\b(report|assessment|audit|score|rating)\b', text) and not re.search(r'(?i)not a (?:comprehensive |definitive )?audit|advisory', text):
                add('policy.disclaimer', file_id=file_id)
        elif not any(item['code'] == 'format.yaml' for item in findings):
            for field in ('name', 'description'):
                add('format.required', 'error', file_id, field=field)
    license_match = match_license(texts.get('LICENSE', ('', ''))[1])
    claims, _ = author_claims(meta)
    employee_claim = any(LICENSE_POLICY['employee_marker'] in claim for claim in claims)
    if license_match is None:
        add(LICENSE_POLICY['error_code'], 'error')
    elif employee_claim and license_match[0] != LICENSE_POLICY['employee_license']:
        # Preserve the existing email-marker rule for both author spellings.
        add(LICENSE_POLICY['error_code'], 'error')
    if license_match and license_match[1] == 'notice_only':
        add('license.notice')
    # Names, contributor type and email strings cannot establish identity, rights,
    # or the applicable agreement. Even an exact content match needs human review.
    add(LICENSE_POLICY['advisory_code'])
    if catalog and catalog.get('entries'):
        candidate_name = meta.get('name')
        if isinstance(candidate_name, str):
            variants = {candidate_name, re.sub(r'^(manage|create|build|deploy|setup|configure)-', '', candidate_name)}
            if any(item['name'] in variants and item['name'] != name for item in catalog['entries']):
                add('catalog.collision')
    report['related'] = related(name, meta, catalog)
    if any(item['severity'] == 'error' and item['code'] != 'coverage.unsupported' for item in findings):
        report['status'] = 'failed'
    elif unsupported:
        report['status'] = 'incomplete'
    return report, texts, unsupported, records

"""Fixed diagnostic vocabulary shared by the host and container."""
import hashlib
import json
import re

from ingest import canonical, manifest
from policy import METADATA, RULES_VERSION

from catalog import QUESTION

VERSION = '2.1'
VALIDATOR = 'contained-reviewer/2.1'
MESSAGES = {
    'input.timeout': 'Collection deadline exceeded; no review was performed.',
    'input.unsafe': 'Input rejected: unsafe path, file type, change, or size limit.',
    'input.symlink': 'Input rejected: a symbolic link was found in the candidate or reviewer path.',
    'runtime.unavailable': 'Restricted validator unavailable; no host fallback was used.',
    'runtime.invalid_output': 'Validator returned invalid or oversized output.',
    'format.skill_missing': 'Required entry document is missing or not readable as text.',
    'format.yaml': 'Frontmatter is invalid, duplicated, too complex, or unsupported.',
    'format.required': 'A required metadata field is missing or invalid.',
    'format.recommended': 'Recommended metadata is missing.',
    'format.field_type': 'Metadata has an unsupported field type.',
    'format.id': 'Optional identifier does not match the directory or naming rule.',
    'format.type': 'Contributor type is not an accepted CI value.',
    'format.status': 'Status is not a recommended CI value.',
    'format.length': 'Title or summary exceeds the advisory length limit.',
    'format.description': 'Description wording or trigger guidance needs human review.',
    'format.name': 'Skill name differs from its directory or advisory naming convention.',
    'format.license': 'License does not match a complete pinned source template or conflicts with an employee email claim.',
    'format.author_conflict': 'Author aliases differ; reconcile both claims with a human reviewer.',
    'license.notice': 'Complete legacy notice matched, not full Apache terms; maintainer review is required.',
    'license.review': 'Confirm license applicability, attribution and rights with maintainers; content matching proves neither identity nor legal approval.',
    'license.inherited': 'Plugin skill: the plugin root LICENSE was checked; skills inherit it.',
    'format.reference': 'A local support-file reference is missing or escapes the bundle.',
    'format.internal_directory': 'An internal-only metadata directory was detected.',
    'coverage.unsupported': 'Unsupported file format or encoding; review coverage is incomplete.',
    'security.secret': 'Potential secret detected; value withheld and model packet blocked.',
    'policy.tone': 'Tone or status-marker pattern needs contextual human review.',
    'policy.identity': 'Identity-field reference needs contextual human review.',
    'policy.hardcoded': 'Environment-specific identifier pattern needs human review.',
    'policy.disclaimer': 'Assessment language found without a recognized disclaimer.',
    'markdown.anchor': 'A local heading anchor was not found in the supported Markdown subset.',
    'markdown.fence': 'A fenced code example has no matching closing fence.',
    'markdown.reference': 'An explicit Markdown reference link has no local definition.',
    'markdown.limited': 'Markdown syntax exceeds the supported structural subset or bounds.',
    'catalog.collision': 'Name matches a different skill in the supplied catalog snapshot.',
}
STATUSES = {'passed', 'failed', 'incomplete', 'blocked', 'unavailable'}
GROUPS = ('required', 'advisory', 'coverage')
FAILURES = {'input.timeout': 'blocked', 'input.unsafe': 'blocked', 'input.symlink': 'blocked',
            'runtime.unavailable': 'unavailable', 'runtime.invalid_output': 'unavailable'}
ERROR_CODES = set(FAILURES) | {'format.skill_missing', 'format.yaml', 'format.required',
    'format.field_type', 'format.id', 'format.type', 'format.license',
    'format.internal_directory', 'coverage.unsupported', 'security.secret'}
FIELDS = {
    'format.required': {'name', 'description'},
    'format.recommended': {'id', 'authors', 'type', 'status', 'categories'},
    'format.field_type': set(METADATA), 'format.length': {'title', 'summary'},
    'format.id': {'id'}, 'format.type': {'type'}, 'format.status': {'status'},
    'format.name': {'name'}, 'format.description': {'description'},
    'format.author_conflict': {'authors'},
}
ACTIONS = {
    'input.timeout': 'Retry after reducing the bundle or resolving collection stalls.',
    'input.unsafe': 'Use a stable bounded directory containing only regular unlinked files.',
    'input.symlink': 'Replace every symlinked path component with its real path (on macOS use /private/tmp, not /tmp) and remove symlinks from the bundle.',
    'runtime.unavailable': 'Ask the operator to provision a compatible approved local validator.',
    'runtime.invalid_output': 'Use a compatible reviewed launcher and validator image; do not trust this output.',
    'format.skill_missing': 'Add a readable UTF-8 entry document.',
    'format.yaml': 'Correct frontmatter syntax, duplicate keys, aliases and complexity.',
    'format.required': 'Supply a nonblank string for {field}.',
    'format.recommended': 'Should {field} be supplied for this submission?',
    'format.field_type': 'Use the documented metadata type for {field}.',
    'format.id': 'Make id match the directory and the documented identifier rule.',
    'format.type': 'Use community or snowflake as the contributor type.',
    'format.status': 'Should status use stable, beta or draft?',
    'format.length': 'Can {field} fit the documented advisory length limit?',
    'format.description': 'Does description state Use when, triggers and third-person guidance?',
    'format.name': 'Should name match the directory and the advisory naming convention?',
    'format.license': 'Ask maintainers to select the applicable complete pinned license text.',
    'format.author_conflict': 'Which author and authors claims should be reconciled?',
    'license.notice': 'Should full license terms replace this legacy notice?',
    'license.review': 'Have maintainers confirmed applicability, attribution and distribution rights?',
    'license.inherited': 'Does the plugin root LICENSE cover every file in this skill?',
    'format.reference': 'Does the referenced support document exist inside this bundle?',
    'format.internal_directory': 'Remove internal-only metadata from the submission.',
    'coverage.unsupported': 'Provide supported UTF-8 text or arrange separate human review.',
    'security.secret': 'Remove exposed values and ask the owner whether rotation is needed.',
    'policy.tone': 'Is the tone appropriate for an advisory result?',
    'policy.identity': 'Are identity fields necessary and appropriately minimized?',
    'policy.hardcoded': 'Should environment-specific identifiers be parameterized?',
    'policy.disclaimer': 'Should this assessment include an explicit advisory disclaimer?',
    'markdown.anchor': 'Does the link fragment match the intended heading in the target document?',
    'markdown.fence': 'Should the example have a matching closing fence?',
    'markdown.reference': 'Should a definition be added for this reference link?',
    'markdown.limited': 'Arrange human review of syntax outside the bounded Markdown subset.',
    'catalog.collision': 'How is this submission distinct from the matching catalog entry?',
}


def is_hex(value, length=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError()
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)


def catalog_identity(catalog):
    if catalog is None:
        return {'sha256': None, 'revision': None}
    if not isinstance(catalog, dict):
        raise ValueError()
    revision = catalog.get('revision')
    if revision is not None and not is_hex(revision, 40):
        raise ValueError()
    return {'sha256': hashlib.sha256(canonical(catalog)).hexdigest(), 'revision': revision}


def snapshot_identity(name, entries):
    records, digest = manifest(entries, name)
    return digest, [{'file_id': item['id'], 'source_sha256': item['path_sha256']}
                    for item in records]


def finding(code, severity='warning', file_id=None, line=None, field=None, source_sha256=None):
    if field is None and len(FIELDS.get(code, ())) == 1:
        field = next(iter(FIELDS[code]))
    group = ('coverage' if code in FAILURES or code.startswith('coverage.') else
             'required' if severity == 'error' else 'advisory')
    return {'code': code, 'severity': severity, 'group': group, 'field': field,
            'file_id': file_id, 'source_sha256': source_sha256, 'line': line,
            'action': ACTIONS[code].format(field=field)}


def base_report(status='passed'):
    return {'schema_version': VERSION, 'rules_version': RULES_VERSION,
            'status': status, 'coverage': {'files': 0, 'text_files': 0},
            'catalog': 'unavailable', 'catalog_revision': None, 'model': 'disabled',
            'public_duplicates': 'not_run', 'provenance': None, 'sources': [],
            'findings': [], 'related': []}


def bind_report(report, name, entries, catalog):
    records, digest = manifest(entries, name)
    report['sources'] = [{'file_id': item['id'], 'source_sha256': item['path_sha256']}
                         for item in records]
    identity = catalog_identity(catalog)
    report['catalog'] = 'partial_snapshot' if catalog is not None else 'unavailable'
    report['catalog_revision'] = identity['revision']
    report['provenance'] = {'snapshot_sha256': digest, 'validator': VALIDATOR,
                            'image_id': None, 'catalog': identity,
                            'base_revision': None, 'head_revision': None}
    return records


def error_report(code, status):
    result = base_report(status)
    result['findings'] = [finding(code, 'error')]
    return result


def validate_report(value):
    """Strict bounded data contract; never accept runtime-authored prose."""
    try:
        _validate_report(value)
    except (TypeError, KeyError, ValueError, RecursionError):
        raise ValueError() from None
    return value


REPORT_KEYS = frozenset(base_report())
FINDING_KEYS = frozenset(finding('license.review'))
RELATED_KEYS = frozenset(('catalog_id', 'question'))
SOURCE_KEYS = frozenset(('file_id', 'source_sha256'))
NO_FIELDS = frozenset((None,))


def _validate_report(value):
    if not isinstance(value, dict) or value.keys() != REPORT_KEYS:
        raise ValueError()
    if value['schema_version'] != VERSION or value['rules_version'] != RULES_VERSION:
        raise ValueError()
    if value['status'] not in STATUSES:
        raise ValueError()
    if value['model'] != 'disabled' or value['public_duplicates'] != 'not_run':
        raise ValueError()
    coverage = value['coverage']
    if not isinstance(coverage, dict) or set(coverage) != {'files', 'text_files'}:
        raise ValueError()
    if any(type(number) is not int or not 0 <= number <= 512 for number in coverage.values()):
        raise ValueError()
    if coverage['text_files'] > coverage['files']:
        raise ValueError()
    if not isinstance(value['findings'], list) or len(value['findings']) > 4096:
        raise ValueError()
    if (not isinstance(value['related'], list) or len(value['related']) > 3):
        raise ValueError()
    related_ids = set()
    for item in value['related']:
        if (not isinstance(item, dict) or item.keys() != RELATED_KEYS
                or not is_hex(item['catalog_id']) or item['question'] != QUESTION
                or item['catalog_id'] in related_ids):
            raise ValueError()
        related_ids.add(item['catalog_id'])
    if value['catalog'] == 'unavailable' and value['related']:
        raise ValueError()
    if value['status'] in ('blocked', 'unavailable'):
        if (value['provenance'] is not None or value['sources'] != [] or
                coverage != {'files': 0, 'text_files': 0} or value['catalog'] != 'unavailable'
                or value['catalog_revision'] is not None or len(value['findings']) != 1):
            raise ValueError()
        code = value['findings'][0]['code']
        if FAILURES.get(code) != value['status'] or value['findings'] != [finding(code, 'error')]:
            raise ValueError()
        return
    provenance = value['provenance']
    if not isinstance(provenance, dict) or set(provenance) != {
            'snapshot_sha256', 'validator', 'image_id', 'catalog', 'base_revision', 'head_revision'}:
        raise ValueError()
    if not is_hex(provenance['snapshot_sha256']) or provenance['validator'] != VALIDATOR:
        raise ValueError()
    image = provenance['image_id']
    if image is not None and (not isinstance(image, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image)):
        raise ValueError()
    revisions = [provenance[key] for key in ('base_revision', 'head_revision')]
    if revisions != [None, None] and not all(is_hex(revision, 40) for revision in revisions):
        raise ValueError()
    catalog = provenance['catalog']
    if not isinstance(catalog, dict) or set(catalog) != {'sha256', 'revision'}:
        raise ValueError()
    if catalog['sha256'] is None:
        if catalog['revision'] is not None or value['catalog'] != 'unavailable':
            raise ValueError()
    elif not is_hex(catalog['sha256']) or value['catalog'] != 'partial_snapshot':
        raise ValueError()
    if catalog['revision'] is not None and not is_hex(catalog['revision'], 40):
        raise ValueError()
    if value['catalog_revision'] != catalog['revision']:
        raise ValueError()
    sources = value['sources']
    if not isinstance(sources, list) or len(sources) != coverage['files']:
        raise ValueError()
    source_ids = {}
    for index, source in enumerate(sources, 1):
        if (not isinstance(source, dict) or source.keys() != SOURCE_KEYS or
                source['file_id'] != f'F{index:04d}' or not is_hex(source['source_sha256'])):
            raise ValueError()
        source_ids[source['file_id']] = source['source_sha256']
    if len(set(source_ids.values())) != len(source_ids):
        raise ValueError()
    unsupported_ids = set()
    for item in value['findings']:
        if not isinstance(item, dict) or item.keys() != FINDING_KEYS:
            raise ValueError()
        code, field, file_id = item['code'], item['field'], item['file_id']
        if code not in MESSAGES or code in FAILURES or field not in FIELDS.get(code, NO_FIELDS):
            raise ValueError()
        severity = 'error' if code in ERROR_CODES else 'warning'
        if file_id is None:
            if item['source_sha256'] is not None or item['line'] is not None:
                raise ValueError()
        elif file_id not in source_ids or item['source_sha256'] != source_ids[file_id]:
            raise ValueError()
        if item['line'] is not None and (type(item['line']) is not int or not 1 <= item['line'] <= 262145):
            raise ValueError()
        if item != finding(code, severity, file_id, item['line'], field, item['source_sha256']):
            raise ValueError()
        if code == 'coverage.unsupported':
            if file_id is None or item['line'] is not None or file_id in unsupported_ids:
                raise ValueError()
            unsupported_ids.add(file_id)
    # Every unreviewed file needs its own source-bound coverage diagnostic, even
    # when a required error takes precedence over the incomplete status.
    if len(unsupported_ids) != coverage['files'] - coverage['text_files']:
        raise ValueError()
    expected = ('failed' if any(item['group'] == 'required' for item in value['findings']) else
                'incomplete' if any(item['group'] == 'coverage' for item in value['findings']) else 'passed')
    if value['status'] != expected:
        raise ValueError()


def render(report, output_format='text'):
    validate_report(report)
    if output_format == 'json':
        return json.dumps(report, sort_keys=True)
    if output_format != 'text':
        raise ValueError()
    lines = ['Skill review: ' + report['status'],
             'Mechanical checks only; not a security certification or acceptance decision.',
             'Schema version: ' + VERSION, 'Rules version: ' + report['rules_version'],
             f"Coverage: {report['coverage']['text_files']} text files / {report['coverage']['files']} files",
             'Catalog: ' + report['catalog'],
             'Catalog revision: ' + (report['catalog_revision'] or 'unavailable'),
             'Model assessment: disabled; public duplicate search: not run']
    provenance = report['provenance']
    if provenance is None:
        lines.append('Provenance: unavailable; no validated review input or image identity.')
    else:
        lines.extend(['Snapshot SHA-256: ' + provenance['snapshot_sha256'],
                      'Validator: ' + provenance['validator'],
                      'Validated image: ' + (provenance['image_id'] or 'unavailable'),
                      'Catalog SHA-256: ' + (provenance['catalog']['sha256'] or 'unavailable'),
                      'Base revision: ' + (provenance['base_revision'] or 'unavailable'),
                      'Head revision: ' + (provenance['head_revision'] or 'unavailable')])
    grouped = {group: [] for group in GROUPS}
    for item in report['findings']:
        grouped[item['group']].append(item)
    for group, title in zip(GROUPS, ('Required corrections', 'Advisory human decisions', 'Coverage limitations')):
        lines.append(title + ':')
        items = grouped[group]
        if not items:
            lines.append('  None reported.')
        for item in items:
            location = item['file_id'] or 'bundle'
            if item['line'] is not None:
                location += ':' + str(item['line'])
            field = ' [' + item['field'] + ']' if item['field'] else ''
            lines.append(f"  {item['severity']} {item['code']} {location}{field}: {MESSAGES[item['code']]}")
            lines.append('  Next: ' + item['action'])
    lines.append('Related catalog entries (partial pinned corpus; lexical candidates, not acceptance):')
    for item in report['related']:
        lines.append('  ' + item['catalog_id'] + ': ' + item['question'])
    lines.append('Markdown: bounded subset only; no external fetching or workflow termination proof.')
    return '\n'.join(lines)


def exit_code(report):
    return {'passed': 0, 'failed': 1, 'incomplete': 2, 'blocked': 2, 'unavailable': 3}[report['status']]

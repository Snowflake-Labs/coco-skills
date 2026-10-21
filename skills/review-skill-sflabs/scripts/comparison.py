"""Compare validated reports, never candidate prose or ordinal/line locations."""
import hashlib

from ingest import canonical
from protocol import is_hex, validate_report

STATES = ('introduced', 'resolved', 'unchanged', 'not-comparable')


def stable_id(path):
    return hashlib.sha256(path.encode('utf-8')).hexdigest()


def complete(report):
    return (report is not None and report['provenance'] is not None
            and report['coverage']['files'] == report['coverage']['text_files']
            and not any(item['group'] == 'coverage' or item['code'] in
                        ('format.yaml', 'format.skill_missing', 'markdown.limited', 'markdown.fence') for item in report['findings']))


def compare(before, after, *, kind='updated', source_map=None):
    """Absence is trusted selection data, never inferred from a missing report.

    Match rule/field/source buckets, not individual occurrences. A moved line or
    duplicate occurrence is not evidence of a new or resolved issue. source_map
    is old->new path hashes validated by trusted Git selection, not similarity.
    """
    if kind not in ('added', 'deleted', 'updated', 'renamed'):
        raise ValueError()
    for report in (before, after):
        if report is not None:
            validate_report(report)
    source_map = {} if source_map is None else source_map
    if (not isinstance(source_map, dict) or len(source_map) > 512
            or not all(is_hex(key) and is_hex(value) for key, value in source_map.items())
            or len(set(source_map.values())) != len(source_map)):
        raise ValueError()
    if source_map:
        old_sources = {item['source_sha256'] for item in before['sources']} if before else set()
        new_sources = {item['source_sha256'] for item in after['sources']} if after else set()
        if not (set(source_map) <= old_sources and set(source_map.values()) <= new_sources):
            raise ValueError()
        if any(key in new_sources or value in old_sources for key, value in source_map.items()):
            raise ValueError()
    if (kind == 'added' and before is not None) or (kind == 'deleted' and after is not None):
        raise ValueError()

    def buckets(report, remap):
        return {(item['code'], item['field'], remap.get(item['source_sha256'], item['source_sha256']))
                for item in report['findings']} if report else set()

    old, new = buckets(before, source_map), buckets(after, {})
    comparable = ((kind == 'added' and complete(after)) or
                  (kind == 'deleted' and complete(before)) or
                  (kind in ('updated', 'renamed') and complete(before) and complete(after)))
    if comparable:
        reports = [item for item in (before, after) if item is not None]
        # Offline reports have no validated image; never treat them as CI evidence.
        identities = [canonical({key: report['provenance'][key] for key in
                                 ('validator', 'image_id', 'catalog', 'base_revision', 'head_revision')})
                      for report in reports]
        comparable = (all(report['provenance']['image_id'] is not None
                          and report['provenance']['base_revision'] is not None
                          and report['provenance']['head_revision'] is not None for report in reports)
                      and len(set(identities)) == 1)
    counts = dict.fromkeys(STATES, 0)
    if comparable:
        counts.update(introduced=len(new - old), resolved=len(old - new), unchanged=len(old & new))
    else:
        counts['not-comparable'] = len(old | new)
    findings = []
    shared = old & new
    for bucket in sorted(old | new, key=lambda item: canonical(item)):
        state = ('not-comparable' if not comparable else 'unchanged' if bucket in shared
                 else 'resolved' if bucket in old else 'introduced')
        findings.append({'finding_id': hashlib.sha256(canonical(bucket)).hexdigest(),
                         'code': bucket[0], 'field': bucket[1], 'source_sha256': bucket[2],
                         'classification': state})
    return {'kind': kind, 'comparison': 'complete' if comparable else 'not-comparable',
            'counts': counts, 'findings': findings}


def render_delta(delta):
    # Only produced locally from validated reports; no source names or excerpts.
    return ('Comparison: ' + delta['comparison'] + '; selection: ' + delta['kind'] + '\n'
            + 'Finding buckets: ' + ' '.join(key + '=' + str(delta['counts'][key]) for key in STATES))

"""Trusted policy contract. Candidate metadata cannot select or override these rules."""
import json
from functools import lru_cache
from pathlib import Path
import re

RULES_VERSION = '1.2'
SOURCE_REVISION = '89c57c211105bb5a870ce2f6e6e495a20c962fd8'

# This matrix also renders the contributor table; checks must consume its rules.
METADATA = {
    'name': {'shape': 'string', 'required': True, 'pattern': r'[a-z0-9]+(?:-[a-z0-9]+){0,3}',
             'description': 'Required nonblank string; directory match and 1-4 lowercase alphanumeric words separated by hyphens are advisory.'},
    'description': {'shape': 'string', 'required': True, 'prefix': 'Use when',
                    'trigger': 'triggers:', 'first_person': r'\b(I|My|my|We|we|Our|our|Us|us)\b',
                    'description': 'Required nonblank string; Use when prefix, Triggers: guidance and third-person wording are advisory.'},
    'id': {'shape': 'string', 'recommended': True, 'pattern': r'[a-z0-9-]+',
           'description': 'Optional; recommended. If present, must match the directory and [a-z0-9-]+ (error otherwise).'},
    'author': {'shape': 'string', 'author_alias': True,
               'description': 'Optional string, comma-separated names supported. Either author or authors satisfies the authorship recommendation; differing aliases require human review.'},
    'authors': {'shape': 'string_or_list', 'author_alias': True,
                'description': 'Optional string or list of strings. Same authorship and license-claim rules as author; neither spelling proves identity.'},
    'type': {'shape': 'string', 'recommended': True, 'values': ('community', 'snowflake'), 'severity': 'error',
             'description': 'Optional; recommended. Nonempty values: community or snowflake only (case-sensitive). Partner and all other values are unsupported errors pending maintainer approval; empty string warns, no default is inserted.'},
    'status': {'shape': 'string', 'recommended': True, 'values': ('stable', 'beta', 'draft'), 'severity': 'warning',
               'legacy_values': ('Published', 'Archived', 'Hidden'),
               'description': 'Optional; recommended values: stable, beta, draft (case-sensitive). Legacy Published, Archived, Hidden and any other nonempty string warn, never fail on value alone; empty string warns.'},
    'categories': {'shape': 'string_or_list', 'recommended': True,
                   'description': 'Optional string or list of strings; missing or empty is advisory. No tag enum is enforced.'},
    'title': {'shape': 'string', 'limit': 30,
              'description': 'Optional string; more than 30 characters is advisory, not a required catalog field.'},
    'summary': {'shape': 'string', 'limit': 140,
                'description': 'Optional string; more than 140 characters is advisory, not a required catalog field.'},
    'tools': {'shape': 'string_or_list',
              'description': 'Optional string or list of strings; no tool-name enum is enforced. This field grants no execution authority.'},
    'prompt': {'shape': 'string', 'description': 'Optional sample prompt string.'},
    'language': {'shape': 'string',
                 'description': 'Optional string; no language enum is enforced. en, es, it, fr, ja, ko and pt_br are examples, not an allowlist.'},
}

LICENSE_POLICY = {
    'normalization': 'ascii-whitespace-and-quotes-v1',
    'employee_marker': '@snowflake.com',
    'employee_license': 'snowflake',
    'error_code': 'format.license',
    'advisory_code': 'license.review',
}


def metadata_table():
    rows = ['| Field | Reviewer contract |', '|---|---|']
    rows.extend(f'| `{field}` | {rule["description"]} |' for field, rule in METADATA.items())
    return '\n'.join(rows)


def valid_shape(value, shape):
    return isinstance(value, str) or (shape == 'string_or_list' and isinstance(value, list)
                                     and all(isinstance(item, str) for item in value))


def author_claims(meta):
    """Union both aliases; a conflicting alias must never hide an employee claim."""
    groups = []
    for field, rule in METADATA.items():
        if not rule.get('author_alias') or field not in meta:
            continue
        value = meta[field]
        if not valid_shape(value, rule['shape']):
            continue
        values = [value] if isinstance(value, str) else value
        groups.append({part.strip().casefold() for item in values for part in item.split(',') if part.strip()})
    return set().union(*groups), len(groups) == 2 and groups[0] != groups[1]


def normalize_license(text):
    # Only observed typography and ASCII layout differences; no case folding,
    # Unicode compatibility folding, HTML stripping, clauses or wildcard notices.
    text = text.translate(str.maketrans({'\u201c': '"', '\u201d': '"', '\u2019': "'"}))
    return re.sub(r'[ \t\r\n\f\v]+', ' ', text).strip(' ')


@lru_cache(maxsize=1)
def license_templates():
    # Fixed installed-resource path, never cwd, candidate, or caller configuration.
    resource = Path(__file__).resolve().parent / 'resources' / 'licenses.json'
    value = json.loads(resource.read_text(encoding='utf-8'))
    if (value['source_revision'] != SOURCE_REVISION or value['rules_version'] != RULES_VERSION
            or value['normalization'] != LICENSE_POLICY['normalization']):
        raise ValueError('license_resource_version')
    return tuple((item['family'], item['kind'], normalize_license(item['text']))
                 for item in value['templates'])


def match_license(text):
    normalized = normalize_license(text)
    for family, kind, template in license_templates():
        if normalized == template:
            return family, kind
    return None

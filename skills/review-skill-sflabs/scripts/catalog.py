"""Small lexical shortlist from the supplied trusted public snapshot only."""
import hashlib
import re

QUESTION = 'What distinct scope or behavior does this submission add beyond this catalog entry?'
STOP = frozenset('use when triggers the a an and or for to of in on with this skill snowflake skills'.split())


def identity(name):
    return hashlib.sha256(('Snowflake-Labs/coco-skills/skills/' + name).encode('utf-8')).hexdigest()


def tokens(text):
    return set(re.findall(r'[a-z][a-z0-9]{2,}', text.lower())) - STOP


def related(name, metadata, catalog):
    if not catalog or not catalog.get('entries'):
        return []
    query = tokens(' '.join([name] + [metadata[field] for field in ('name', 'description', 'summary')
                                     if isinstance(metadata.get(field), str)]))
    ranked = []
    for entry in catalog['entries']:
        if entry['name'] == name:
            continue
        terms = tokens(' '.join([entry['name'], entry.get('summary', ''), entry.get('triggers', '')]))
        overlap = len(query & terms)
        if overlap:
            ranked.append((-overlap, identity(entry['name'])))
    return [{'catalog_id': entry_id, 'question': QUESTION} for _, entry_id in sorted(ranked)[:3]]

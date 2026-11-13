#!/usr/bin/env python3
"""Maintainer-only refresh of the reviewer's offline OSV advisory snapshot.

Collects PyPI and npm package names from dependency manifests and install
commands in the given repository checkout, queries the public OSV API
(https://api.osv.dev) for each name, and writes affected version ranges to
skills/review-skill-sflabs/scripts/resources/advisories.json. The container
never runs this or touches the network; review the resulting diff like any
other policy change before committing it. Requires network access.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'skills' / 'review-skill-sflabs' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from risk_checks import MANIFESTS, install_commands, manifest_dependencies  # noqa: E402

OSV_QUERY = 'https://api.osv.dev/v1/query'
TEXT_SUFFIXES = ('.md', '.txt', '.rst', '.sh', '.bash', '.py', '.yml', '.yaml', '.toml', '.ps1', '.json')


def collect(checkout):
    names = set()
    for base in ('skills', 'plugins'):
        for path in (checkout / base).rglob('*') if (checkout / base).is_dir() else ():
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
                continue
            name = path.name
            if not (name.startswith('requirements') or name in MANIFESTS or path.suffix in TEXT_SUFFIXES):
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            relative = str(path.relative_to(checkout))
            for ecosystem, package, _, _ in manifest_dependencies(relative, text) + install_commands(text):
                if ecosystem in ('PyPI', 'npm') and package:
                    names.add((ecosystem, package))
    return sorted(names)


def ranges(vulnerability, ecosystem, package):
    found = []
    for affected in vulnerability.get('affected', []):
        target = affected.get('package', {})
        if target.get('ecosystem') != ecosystem or target.get('name', '').lower() != package.lower():
            continue
        for item in affected.get('ranges', []):
            if item.get('type') != 'ECOSYSTEM':
                continue
            current = None
            for event in item.get('events', []):
                if 'introduced' in event:
                    current = {'id': vulnerability['id'], 'introduced': event['introduced']}
                elif current is not None and ('fixed' in event or 'last_affected' in event):
                    current.update({key: event[key] for key in ('fixed', 'last_affected') if key in event})
                    found.append(current)
                    current = None
            if current is not None:
                found.append(current)
    return found


def query(ecosystem, package):
    body = json.dumps({'package': {'name': package, 'ecosystem': ecosystem}}).encode()
    request = urllib.request.Request(OSV_QUERY, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get('vulns', [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, default=ROOT, help='Repository checkout to scan')
    parser.add_argument('--output', type=Path, default=SCRIPTS / 'resources' / 'advisories.json')
    args = parser.parse_args()
    packages = {}
    for ecosystem, package in collect(args.checkout.resolve()):
        entries = [entry for vulnerability in query(ecosystem, package)
                   for entry in ranges(vulnerability, ecosystem, package)]
        if entries:
            packages.setdefault(ecosystem, {})[package] = sorted(entries, key=lambda item: item['id'])
    snapshot = {
        'format': 'osv-ranges/1',
        'source': 'https://osv.dev (public OSV database), refreshed offline by .github/scripts/refresh_advisories.py',
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'approval': 'Maintainer-refreshed snapshot; review the diff before committing.',
        'packages': packages,
    }
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temporary, args.output)
    print(f'{sum(len(items) for items in packages.values())} packages with advisories written to {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

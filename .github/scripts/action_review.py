"""Thin trusted Action adapter. Candidate metadata never establishes authority."""
import argparse
import os
from pathlib import Path
import re
import stat
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skills/review-skill-sflabs/scripts'))
sys.path.insert(0, str(ROOT / '.github/scripts'))
from ingest import canonical, open_root
from packaging import snapshot, trusted_root
from private_output import Destination, write_outputs
from protocol import RULES_VERSION, VERSION, is_hex, strict_json, validate_report
from comparison import STATES
from launch import container_command, trusted_catalog
from reviewer_test_support import complete, summary, validate_counts
from verify_reviewer_runtime import verification_evidence
from validate_skill import scan

MAX_REPORT = 32 * 1024 * 1024


def event_revisions(path, event_name, repository):
    if event_name != 'pull_request' or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError()
    parent = open_root(str(Path(path).parent))
    try:
        descriptor = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
                raise ValueError()
            event = strict_json(stream.read(1024 * 1024 + 1))
    finally:
        os.close(parent)
    # A fork may control title/body/branch/labels and its repository metadata.
    # Read none of those. GitHub-delivered exact SHAs are context, not authority.
    pr = event['pull_request']
    base, head = pr['base']['sha'], pr['head']['sha']
    if (not all(is_hex(value, 40) for value in (base, head))
            or pr['base']['repo']['full_name'] != repository
            or event['repository']['full_name'] != repository):
        raise ValueError()
    return base, head


def safe_comparison(raw, base, head, image, code):
    """Revalidate before persistence; omit unneeded delta detail, never raw output."""
    if len(raw.encode('utf-8')) > MAX_REPORT or type(code) is not int or code not in (0, 1):
        raise ValueError()
    value = strict_json(raw)
    if (set(value) != {'comparison_schema', 'base_revision', 'head_revision', 'submissions'}
            or value['comparison_schema'] != '1.0' or value['base_revision'] != base
            or value['head_revision'] != head or not isinstance(value['submissions'], list)
            or len(value['submissions']) > 100):
        raise ValueError()
    submissions = []
    seen = set()
    for item in value['submissions']:
        if set(item) != {'submission_id', 'baseline', 'head', 'delta'}:
            raise ValueError()
        identifier = item['submission_id']
        if not is_hex(identifier) or identifier in seen:
            raise ValueError()
        seen.add(identifier)
        for report in (item['baseline'], item['head']):
            if report is not None:
                validate_report(report)
                provenance = report['provenance']
                if provenance is not None and (
                        provenance['image_id'] != image or provenance['base_revision'] != base
                        or provenance['head_revision'] != head or provenance['catalog'] != trusted_catalog()):
                    raise ValueError()
        delta = item['delta']
        if (set(delta) != {'kind', 'comparison', 'counts', 'findings'}
                or delta['kind'] not in ('added', 'deleted', 'updated', 'renamed')
                or delta['comparison'] not in ('complete', 'not-comparable')
                or set(delta['counts']) != set(STATES)
                or any(type(count) is not int or not 0 <= count <= 8192 for count in delta['counts'].values())):
            raise ValueError()
        # Delta finding detail is deliberately not a second report contract.
        submissions.append({'submission_id': identifier, 'baseline': item['baseline'], 'head': item['head'],
                            'delta': {key: delta[key] for key in ('kind', 'comparison', 'counts')}})
    from protocol import exit_code
    expected = int(any(item['head'] is not None and exit_code(item['head']) != 0 for item in submissions))
    if code != expected:
        raise ValueError()
    return submissions


def run(args):
    trusted_root(args.trusted_root, args.candidate_root, args.base_root)
    container_command(args.image, 'preflight')
    if not is_hex(args.tooling_sha256):
        raise ValueError()
    _, _, digest = snapshot(args.trusted_root, 'ci')
    if digest != args.tooling_sha256:
        raise ValueError()
    base, head = event_revisions(args.event_path, args.event_name, args.repository)
    report = {'action_schema': '1.0', 'schema_version': VERSION, 'rules_version': RULES_VERSION,
              'authority': 'not-established-by-this-report', 'status': 'blocked',
              'tooling_sha256': digest, 'image_id': args.image, 'base_revision': base,
              'head_revision': head, 'catalog': trusted_catalog(), 'verification': None,
              'submissions': []}
    verification_code, counts = verification_evidence(args.image)
    counts = validate_counts(canonical(counts))
    report['verification'] = counts
    if verification_code != 0 or not complete(counts):
        return 3, report
    options = SimpleNamespace(candidate_root=args.candidate_root, base_root=args.base_root,
                              base=base, head=head, image=args.image, format='json',
                              renames=args.renames, file_renames=args.file_renames)
    code, raw = scan(options)
    report['submissions'] = safe_comparison(raw, base, head, args.image, code)
    # Detect changes to the installed snapshot during verification/scanning.
    if snapshot(args.trusted_root, 'ci')[2] != digest:
        raise ValueError()
    report['status'] = 'passed' if code == 0 else 'failed'
    return code, report


def render_summary(report):
    counts = report['verification']
    return ('## Contained reviewer\n\n'
            + 'Mechanical result: ' + report['status'] + '.\n\n'
            + 'Submissions: ' + str(len(report['submissions'])) + '.\n\n'
            + 'Trusted verification: ' + (summary(counts) if counts is not None else 'unavailable') + '.\n\n'
            + 'Not security certification, release approval, or proof of required-workflow authority.\n'
            + 'Model disabled. No raw candidate content, private locators or artifacts uploaded.\n')


def publish_summary(path, text, roots):
    """Append only fixed generated text to the runner-owned summary command file."""
    parent = str(Path(path).parent)
    from packaging import disjoint
    for root in roots:
        disjoint(parent, root)
    directory = open_root(parent)
    try:
        descriptor = os.open(Path(path).name, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW |
                             os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'ab') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise ValueError()
            stream.write(text.encode('ascii'))
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('trusted-root', 'candidate-root', 'base-root', 'image', 'tooling-sha256',
                 'event-path', 'event-name', 'repository', 'report'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--renames', default='[]')
    parser.add_argument('--file-renames', default='[]')
    args = parser.parse_args()
    destinations = []
    try:
        # Reject unsafe output even on a failing review; no candidate writes.
        for root in (args.candidate_root, args.base_root, args.trusted_root):
            destinations.append(Destination(args.report, root, {'.json'}))
        code, report = run(args)
        write_outputs([(destinations[0], canonical(report))])
        text = render_summary(report)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            publish_summary(os.environ['GITHUB_STEP_SUMMARY'], text,
                            (args.candidate_root, args.base_root, args.trusted_root))
        print(text)
        return code
    except Exception:
        print('Contained review blocked: trusted configuration, verification or output unavailable.')
        return 3
    finally:
        for destination in destinations:
            destination.close()


if __name__ == '__main__':
    sys.exit(main())

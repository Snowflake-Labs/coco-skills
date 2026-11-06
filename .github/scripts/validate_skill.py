#!/usr/bin/env python3
"""Run trusted tooling against exact base/head bundles; never execute PR tooling."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
TRUSTED_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TRUSTED_ROOT / 'skills/review-skill-sflabs/scripts'))
from comparison import compare, render_delta, stable_id
from git_context import Checkout, selection, source_renames, validate_renames
from ingest import UnsafeInput, absent
from launch import review
from protocol import error_report, exit_code, render, strict_json

# Kept as a public test/adapter seam; exact base..head, not implicit merge-base.
changed_skills = selection


def scan(args):
    candidate = Checkout(args.candidate_root, args.head)
    names = changed_skills(args.candidate_root, args.base, args.head)
    # Selection comes from immutable Git trees, not missing files in a checkout.
    old_files = {name: candidate.files(name, args.base) for name in names}
    new_files = {name: candidate.files(name) for name in names}
    pairs = validate_renames(strict_json(args.renames), old_files, new_files)
    used = {name for pair in pairs for name in pair}
    pairs += [(name, name) for name in names if name not in used]
    mappings = source_renames(strict_json(args.file_renames), pairs, old_files, new_files)
    baseline = None
    if args.base_root:
        try:
            baseline = Checkout(args.base_root, args.base)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
    failed = False
    results = []
    output = []
    for old, new in pairs:
        old_expected, new_expected = old_files[old], new_files[new]
        kind = ('renamed' if old != new else 'added' if not old_expected else
                'deleted' if not new_expected else 'updated')
        before = after = None
        target = os.path.join(args.candidate_root, 'skills', new)
        if kind == 'renamed' and not candidate.missing(old):
            raise UnsafeInput()
        if new_expected:
            after = review(target, args.image, base_revision=args.base, head_revision=args.head,
                           expected_files=new_expected)
            failed = failed or exit_code(after) != 0
        elif not candidate.missing(new):
            # Deleted Git entries must also be absent from the actual checkout.
            raise UnsafeInput()
        if old_expected and baseline is not None:
            try:
                baseline.check()
                if baseline.files(old) != old_expected or (old != new and not baseline.missing(new)):
                    raise UnsafeInput()
                before = review(os.path.join(args.base_root, 'skills', old), args.image,
                                base_revision=args.base, head_revision=args.head,
                                expected_files=old_expected)
                baseline.check()
            except (ValueError, OSError, subprocess.SubprocessError):
                before = error_report('input.unsafe', 'blocked')
        elif not old_expected and baseline is not None:
            try:
                if not baseline.missing(old):
                    raise UnsafeInput()
            except (ValueError, OSError):
                # Conflicting checkout data cannot prove a true new skill.
                before = error_report('input.unsafe', 'blocked')
                kind = 'updated'
        # Unusable reports cannot validate source remapping; they remain not-comparable.
        source_map = mappings[(old, new)] if (before and after and before['provenance']
                                              and after['provenance']) else {}
        delta = compare(before, after, kind=kind, source_map=source_map)
        results.append({'submission_id': stable_id('skills/' + new), 'delta': delta,
                        'baseline': before, 'head': after})
        output.append('Submission SHA-256: ' + stable_id('skills/' + new))
        output.append(render_delta(delta))
        if before is not None:
            output.append('Baseline review:\n' + render(before))
        elif old_expected:
            output.append('Baseline review: unavailable; no resolution claim.')
        if after is not None:
            output.append('Head review:\n' + render(after))
        else:
            output.append('Head selection: absent in the requested tree and checkout.')
    candidate.check()
    # No output is published until checkout consistency checks have finished.
    output.append('PR context: base=' + args.base + ' head=' + args.head)
    if not names:
        output.append('No skill changes to scan.')
    if args.format == 'json':
        return int(failed), json.dumps({'comparison_schema': '1.0', 'base_revision': args.base,
                                       'head_revision': args.head, 'submissions': results}, sort_keys=True)
    return int(failed), '\n'.join(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-root', required=True)
    parser.add_argument('--base-root', help='Separate no-follow checkout at the exact base revision')
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    parser.add_argument('--renames', default='[]',
                        help='Trusted operator JSON list of {base,head} skill names; never candidate metadata')
    parser.add_argument('--file-renames', default='[]',
                        help='Trusted operator JSON {base,head} paths under skills/; no similarity inference')
    args = parser.parse_args()
    try:
        code, output = scan(args)
        print(output)
        return code
    except (ValueError, OSError, subprocess.SubprocessError):
        print('Unable to determine or scan candidate changes safely.')
        return 3


if __name__ == '__main__':
    sys.exit(main())

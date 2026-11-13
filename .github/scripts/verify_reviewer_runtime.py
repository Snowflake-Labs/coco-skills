"""Verify reviewed base tests and real runtime boundaries, never candidate tests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
from reviewer_test_support import (MAX_EVIDENCE_BYTES, REQUIRED_TESTS, complete,
                                   run_suite, summary, test_ids, validate_counts)

ROOT = Path(__file__).resolve().parents[2]


def required_present(suite):
    identifiers = list(test_ids(suite))
    return all(identifiers.count(identifier) == 1 for identifier in REQUIRED_TESTS)


def worker(image):
    # The worker only ever discovers tests relative to this trusted script.
    os.environ['REVIEWER_TEST_IMAGE'] = image
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / 'skills/review-skill-sflabs/tests'))
    if loader.errors or not required_present(suite):
        return 3
    counts = run_suite(suite)
    print(json.dumps(counts), flush=True)
    return 0 if complete(counts) else 1


def verification_evidence(image):
    """Same bounded worker for CLI and Action; no parsing of human output."""
    with tempfile.TemporaryFile() as output:
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()),
                                 '--image', image, '--worker'], stdout=output,
                                stderr=subprocess.DEVNULL, timeout=240)
        output.seek(0)
        counts = validate_counts(output.read(MAX_EVIDENCE_BYTES + 1))
    return result.returncode, counts


def verify(image):
    try:
        code, counts = verification_evidence(image)
        print('Trusted-base verification: ' + summary(counts))
        if code != 0 or not complete(counts):
            print('NOT VERIFIED: required tests failed, skipped, or did not execute.')
            return 1
        print('Trusted-base required tests and runtime checks verified; not candidate admission or release approval.')
        return 0
    except Exception:
        print('NOT VERIFIED: trusted verification unavailable or required coverage missing.')
        return 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.image)
    return verify(args.image)


if __name__ == '__main__':
    sys.exit(main())

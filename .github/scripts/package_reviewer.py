"""Create an offline snapshot, never build, install, publish or approve tooling."""
import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).absolute().parents[2] / 'skills/review-skill-sflabs/scripts'))
from packaging import save_snapshot, snapshot, trusted_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-root', required=True)
    parser.add_argument('--candidate-root', required=True)
    parser.add_argument('--profile', choices=('local', 'ci'), required=True)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        trusted_root(args.trusted_root, args.candidate_root)
        if args.output:
            digest = save_snapshot(args.trusted_root, args.candidate_root, args.profile,
                                   args.expected_sha256, args.output)
        else:
            _, _, digest = snapshot(args.trusted_root, args.profile)
        print('Snapshot SHA-256: ' + digest)
        print('Snapshot only; installation, build, publication and distribution approval not performed.')
        return 0
    except Exception:
        print('Trusted snapshot unavailable or rejected; no approval established.')
        return 3


if __name__ == '__main__':
    sys.exit(main())

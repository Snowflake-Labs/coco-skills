"""Run proposed tests offline; validated counts remain untrusted, not admission evidence."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/review-skill-sflabs/scripts'))
from launch import collect, container_command, local_docker
from reviewer_test_support import MAX_EVIDENCE_BYTES, complete, summary, validate_counts

# These bytes come from the trusted checkout, never from the proposed snapshot.
SUPPORT = Path(__file__).with_name('reviewer_test_support.py').read_text()
BOOTSTRAP = '''import sys,json,base64,pathlib,subprocess,os
root=pathlib.Path('/tmp/proposed'); root.mkdir()
value=json.loads(sys.stdin.buffer.read(4194305))
for item in value['files']:
 path=root/item['path']
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_bytes(base64.b64decode(item['data'],validate=True))
# Raw proposed output is discarded, including discovery/import-time output.
# A separate bounded evidence file is the only proposed output channel.
worker = SUPPORT + """
import unittest
loader = unittest.TestLoader()
suite = loader.discover('/tmp/proposed/skills/review-skill-sflabs/tests')
counts = run_suite(suite)
with open('/tmp/counts.json', 'x') as stream:
    json.dump(counts, stream)
"""
result=subprocess.run([sys.executable,'-I','-c',worker],
                      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
if result.returncode != 0:
 sys.exit(3)
with open('/tmp/counts.json','rb') as stream:
 data=stream.read(513)
namespace={}
exec(SUPPORT,namespace)
counts=namespace['validate_counts'](data)
sys.stdout.write(json.dumps(counts))
'''
BOOTSTRAP = 'SUPPORT = ' + repr(SUPPORT) + '\n' + BOOTSTRAP


def run_proposed(candidate_root, image):
    name = 'reviewer-tests-' + uuid.uuid4().hex
    started = False
    try:
        local_docker()
        command = container_command(image, name)
        wire = collect(candidate_root, repository_tests=True)
        command[-1:-1] = ['--entrypoint', 'python']
        with tempfile.TemporaryFile() as output:
            started = True
            result = subprocess.run(command + ['-I', '-c', BOOTSTRAP], input=wire,
                                    stdout=output, stderr=subprocess.DEVNULL, timeout=120)
            output.seek(0)
            counts = validate_counts(output.read(MAX_EVIDENCE_BYTES + 1))
        if result.returncode != 0:
            raise ValueError()
        print('Untrusted proposed-test counts: ' + summary(counts))
        print('Not authoritative verification or admission evidence; runtime isolation is verified separately.')
        # Runtime smoke tests intentionally cannot run inside a socket-free container.
        # Skips are reported as incomplete, never as a complete verified pass.
        print('Proposed coverage: ' + ('complete (untrusted).' if complete(counts) else 'incomplete or failed (untrusted).'))
        return int(counts['expected'] == 0 or counts['executed'] != counts['expected'] or counts['failed'] != 0)
    except Exception:
        print('Isolated proposed tests unavailable or blocked; no counts verified.')
        return 3
    finally:
        if started:
            try:
                subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass


def tooling_changed(candidate_root, base, head):
    from collect_snapshot import REPOSITORY_INPUTS
    from git_context import Checkout, git
    from protocol import is_hex
    if not all(is_hex(revision, 40) for revision in (base, head)):
        raise ValueError()
    checkout = Checkout(candidate_root, head)
    data = git(candidate_root, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
               '--name-only', '-z', base, head, '--', *REPOSITORY_INPUTS)
    checkout.check()
    return bool(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-root', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--base', help='Exact base revision for optional proposed-tooling test selection')
    parser.add_argument('--head', help='Exact head revision; both revisions must be provided')
    args = parser.parse_args()
    if args.base is not None or args.head is not None:
        try:
            if not tooling_changed(args.candidate_root, args.base, args.head):
                print('Proposed-tooling tests not selected: no relevant tree changes. Admission scans are unchanged.')
                return 0
        except (OSError, ValueError, subprocess.SubprocessError):
            print('Proposed-tooling selection unavailable; no tests or verification claimed.')
            return 3
    return run_proposed(args.candidate_root, args.image)


if __name__ == '__main__':
    sys.exit(main())

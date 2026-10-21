"""Stdlib-only snapshots of separately trusted tooling; never an installer."""
import hashlib
import io
import os
from pathlib import Path
import tarfile

from ingest import canonical, collect, open_root
from private_output import Destination, write_outputs

REVIEWER = 'skills/review-skill-sflabs/'
LOCAL_FILES = tuple(REVIEWER + path for path in (
    'scripts/run.py', 'scripts/launch.py', 'scripts/ingest.py',
    'scripts/collect_snapshot.py', 'scripts/private_output.py', 'scripts/protocol.py',
    'scripts/policy.py', 'scripts/checks.py', 'scripts/entrypoint.py',
    'scripts/catalog.py', 'scripts/comparison.py', 'scripts/git_context.py',
    'scripts/markdown_checks.py', 'scripts/model_contract.py', 'scripts/packaging.py',
    'scripts/report_cache.py', 'scripts/editorial_broker.py', 'scripts/advisory_cache.py',
    'scripts/resources/licenses.json', 'references/catalog-snapshot.json',
    'Dockerfile', '.dockerignore', 'requirements.txt', 'pyproject.toml', 'LICENSE',
))
LOCAL_FILES += ('.github/scripts/package_reviewer.py',)
CI_FILES = (
    '.github/scripts/action_review.py',
    '.github/scripts/validate_skill.py', '.github/scripts/verify_reviewer_runtime.py',
    '.github/scripts/reviewer_test_support.py', '.github/scripts/test_reviewer_isolated.py',
    '.github/actions/contained-review/action.yml', '.github/workflows/validate-skill.yml',
    REVIEWER + 'tests', 'CONTRIBUTING.md', REVIEWER + 'workflows/format-check.md',
    REVIEWER + 'references/report-template.md',
    'skills/dbt-model-generator/LICENSE', 'skills/check-agent-access/LICENSE',
    'skills/persist-memory/LICENSE', 'skills/snowpro-study/LICENSE',
    'skills/swap-databases/LICENSE',
)
ROOT = Path(__file__).absolute().parents[3]


def disjoint(*paths):
    """Reject links, lexical overlap and inode aliases before any execution."""
    normalized = []
    identities = []
    for value in paths:
        descriptor = open_root(str(value))
        try:
            info = os.fstat(descriptor)
            identities.append((info.st_dev, info.st_ino))
        finally:
            os.close(descriptor)
        normalized.append(Path(value).resolve(strict=True))
    for index, left in enumerate(normalized):
        for offset, right in enumerate(normalized[index + 1:], index + 1):
            if (left == right or left in right.parents or right in left.parents
                    or identities[index] == identities[offset]):
                raise ValueError()


def trusted_root(root, candidate, baseline=None):
    # A CLI flag is not a trust assertion. Only this script's installed root is
    # eligible, and its bytes still require an independently approved digest.
    disjoint(root, candidate, *([baseline] if baseline is not None else []))
    descriptor = open_root(str(ROOT))
    os.close(descriptor)
    if Path(root).resolve(strict=True) != ROOT.resolve(strict=True):
        raise ValueError()


def snapshot(root, profile):
    if profile not in ('local', 'ci'):
        raise ValueError()
    entries = collect(str(root), selected=LOCAL_FILES + (CI_FILES if profile == 'ci' else ()))
    records = [{'path': entry.path, 'size': len(entry.data),
                'sha256': hashlib.sha256(entry.data).hexdigest()} for entry in entries]
    manifest = canonical({'format': 'contained-reviewer-snapshot/1', 'profile': profile,
                          'distribution': 'blocked-pending-rights-and-owner-approval',
                          'files': records})
    return entries, manifest, hashlib.sha256(manifest).hexdigest()


def archive(entries, manifest):
    """Stable plain tar: captured bytes only, no source rereads, hooks or builds."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as bundle:
        for name, data in [('MANIFEST.json', manifest)] + [(item.path, item.data) for item in entries]:
            record = tarfile.TarInfo(name)
            record.size = len(data)
            record.mode = 0o600
            record.mtime = record.uid = record.gid = 0
            record.uname = record.gname = ''
            bundle.addfile(record, io.BytesIO(data))
    return output.getvalue()


def save_snapshot(root, candidate, profile, expected, output):
    trusted_root(root, candidate)
    entries, manifest, digest = snapshot(root, profile)
    if expected != digest:
        raise ValueError()
    destination = Destination(output, str(root), {'.tar'})
    candidate_guard = None
    try:
        candidate_guard = Destination(output, candidate, {'.tar'})
        write_outputs([(destination, archive(entries, manifest))])
    finally:
        destination.close()
        if candidate_guard is not None:
            candidate_guard.close()
    return digest

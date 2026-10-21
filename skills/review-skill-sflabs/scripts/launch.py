#!/usr/bin/env python3
"""Trusted host launcher. Only collects bytes; never parses candidate instructions."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from ingest import MAX_WIRE_BYTES, UnsafeInput, decode, lf_line_count
from private_output import Destination, OutputError, locator_payload, write_outputs
from protocol import (catalog_identity, error_report, exit_code, is_hex, render,
                      snapshot_identity, strict_json, validate_report)

MAX_OUTPUT = 1024 * 1024


def container_command(image, name):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError()
    return ['docker', 'run', '--rm', '--pull=never', '--name', name, '-i',
            '--network=none', '--read-only', '--user=65532:65532',
            '--cap-drop=ALL', '--security-opt=no-new-privileges=true',
            '--memory=256m', '--memory-swap=256m', '--cpus=1', '--pids-limit=32',
            '--ulimit=nofile=128:128', '--ulimit=fsize=1048576:1048576',
            '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m',
            '--log-driver=none', image]


def local_docker():
    """Never stream confidential bytes to a remotely configured Docker daemon."""
    if os.environ.get('DOCKER_HOST'):
        endpoint = os.environ['DOCKER_HOST']
    else:
        result = subprocess.run(['docker', 'context', 'inspect', '--format',
                                 '{{.Endpoints.docker.Host}}'],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=10, check=True)
        endpoint = result.stdout.decode('ascii').strip()
    if not endpoint.startswith('unix:///'):
        raise ValueError()
    # Do not let a context environment override a checked DOCKER_HOST.
    if os.environ.get('DOCKER_HOST') and os.environ.get('DOCKER_CONTEXT'):
        raise ValueError()


def collect(path, timeout=15, repository_tests=False):
    """Supervise trusted collection separately from the container deadline."""
    command = [sys.executable, '-I', str(Path(__file__).with_name('collect_snapshot.py')), path]
    if repository_tests:
        command.append('--repository-tests')
    with tempfile.TemporaryFile() as output:
        process = subprocess.run(command, stdout=output, stderr=subprocess.DEVNULL,
                                 timeout=timeout)
        output.seek(0)
        wire = output.read(MAX_WIRE_BYTES + 1)
    if process.returncode != 0 or len(wire) > MAX_WIRE_BYTES:
        raise UnsafeInput()
    return wire


def review(path, image, *, private_locator=None, output=None, output_format='text',
           base_revision=None, head_revision=None, expected_files=None):
    """Return only safe diagnostics; locator bytes never enter the returned report."""
    destinations = []
    try:
        if [base_revision, head_revision] != [None, None] and not all(
                is_hex(revision, 40) for revision in (base_revision, head_revision)):
            return error_report('runtime.invalid_output', 'unavailable')
        if output is not None:
            destinations.append(Destination(output, path, {'.txt', '.json'}))
        if private_locator is not None:
            destinations.append(Destination(private_locator, path, {'.json'}))
        if len({item.path for item in destinations}) != len(destinations):
            raise OutputError()
        report, snapshot = _review(path, image, expected_files=expected_files)
        if report['provenance'] is not None:
            report['provenance']['base_revision'] = base_revision
            report['provenance']['head_revision'] = head_revision
        pending = []
        if output is not None:
            pending.append((destinations[0], (render(report, output_format) + '\n').encode('ascii')))
        if private_locator is not None and snapshot is not None:
            pending.append((destinations[-1], locator_payload(*snapshot)))
        write_outputs(pending)
        return report
    finally:
        for destination in destinations:
            destination.close()


def trusted_catalog_data():
    # Installed alongside the reviewed launcher, never resolved from the candidate.
    path = Path(__file__).resolve().parents[1] / 'references' / 'catalog-snapshot.json'
    with path.open('rb') as stream:
        data = stream.read(MAX_OUTPUT + 1)
    if len(data) > MAX_OUTPUT:
        raise ValueError()
    return strict_json(data)


def trusted_catalog():
    return catalog_identity(trusted_catalog_data())


def _review(path, image, *, expected_files=None):
    try:
        local_docker()
        container_command(image, 'check')
    except (OSError, ValueError, subprocess.SubprocessError):
        return error_report('runtime.unavailable', 'unavailable'), None
    try:
        wire = collect(path)
        if expected_files is not None:
            from git_context import verify_entries
            _, entries = decode(wire)
            verify_entries(entries, expected_files)
    except subprocess.TimeoutExpired:
        return error_report('input.timeout', 'blocked'), None
    except (UnsafeInput, OSError, ValueError):
        return error_report('input.unsafe', 'blocked'), None
    return _run_review(wire, image)


def validate_snapshot_report(report, name, entries, image=None):
    """Independent host binding, also mandatory on every non-authoritative hit."""
    validate_report(report)
    digest, sources = snapshot_identity(name, entries)
    provenance = report['provenance']
    if (provenance is None or provenance['snapshot_sha256'] != digest or report['sources'] != sources
            or report['coverage']['files'] != len(entries)
            or provenance['catalog'] != trusted_catalog()
            or provenance['image_id'] != image
            or provenance['base_revision'] is not None or provenance['head_revision'] is not None):
        raise ValueError()
    from catalog import identity
    allowed = {identity(item['name']) for item in trusted_catalog_data()['entries']}
    if any(item['catalog_id'] not in allowed for item in report['related']):
        raise ValueError()
    line_limits = {f'F{index:04d}': lf_line_count(entry.data)
                   for index, entry in enumerate(entries, 1)}
    if any(item['line'] is not None and item['line'] > line_limits[item['file_id']]
           for item in report['findings']):
        raise ValueError()


def _run_review(wire, image):
    name = 'skill-review-' + uuid.uuid4().hex
    try:
        command = container_command(image, name)
        # Output is bounded by the trusted validator. Spool privately rather than
        # printing Docker errors, which can contain candidate-controlled names.
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.run(command, input=wire, stdout=output,
                                         stderr=subprocess.DEVNULL, timeout=60)
            finally:
                subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)
            output.seek(0)
            data = output.read(MAX_OUTPUT + 1)
        if process.returncode not in (0, 1, 2) or len(data) > MAX_OUTPUT:
            return error_report('runtime.unavailable', 'unavailable'), None
        report = validate_report(strict_json(data))
        if exit_code(report) != process.returncode:
            raise ValueError()
        if report['provenance'] is None:
            return report, None
        candidate_name, entries = decode(wire)
        validate_snapshot_report(report, candidate_name, entries)
        provenance = report['provenance']
        # Docker is invoked with the exact immutable image ID, never a tag. The
        # runtime cannot self-assert an image or supply trusted PR context.
        provenance['image_id'] = image
        return validate_report(report), (candidate_name, entries)
    except (OSError, subprocess.SubprocessError):
        return error_report('runtime.unavailable', 'unavailable'), None
    except (ValueError, TypeError, KeyError, RecursionError):
        return error_report('runtime.invalid_output', 'unavailable'), None


def cache_context(name, entries, image):
    from packaging import ROOT, snapshot
    from protocol import RULES_VERSION, VALIDATOR, VERSION
    from report_cache import FORMAT, MAX_BYTES, SLOTS, TTL_SECONDS
    import ingest
    return {'snapshot': snapshot_identity(name, entries)[0], 'image': image,
            'cache_policy': {'format': FORMAT, 'max_bytes': MAX_BYTES, 'slots': SLOTS, 'ttl': TTL_SECONDS},
            'tooling': snapshot(str(ROOT), 'local')[2], 'validator': VALIDATOR,
            'rules': RULES_VERSION, 'schema': VERSION, 'catalog': trusted_catalog(),
            'limits': {key: getattr(ingest, key) for key in (
                'MAX_FILES', 'MAX_ENTRIES', 'MAX_DEPTH', 'MAX_FILE_BYTES', 'MAX_TOTAL_BYTES', 'MAX_WIRE_BYTES')},
            'config': {'max_output': MAX_OUTPUT, 'collection_seconds': 15,
                       'container_seconds': 60, 'command': container_command(image, 'fixed'),
                       'mode': 'non-authoritative-preview'}}


def preview(path, image, cache_directory):
    """Private-owner report acceleration only; never called by admission or model APIs."""
    from packaging import ROOT, trusted_root
    from report_cache import PrivateCache, key_for
    cache = None
    state = 'bypassed'
    try:
        trusted_root(str(ROOT), path)
        container_command(image, 'check')
        # No timestamp shortcut, even when Docker is unavailable on a hit.
        wire = collect(path)
        name, entries = decode(wire)
    except subprocess.TimeoutExpired:
        return error_report('input.timeout', 'blocked'), state
    except (OSError, ValueError):
        return error_report('input.unsafe', 'blocked'), state
    validate = lambda report: validate_snapshot_report(report, name, entries, image)
    try:
        try:
            context = cache_context(name, entries, image)
            key = key_for(context)
            cache = PrivateCache(cache_directory, path, str(ROOT))
            report = cache.get(key, validate)
            if report is not None and cache_context(name, entries, image) == context:
                return report, 'hit'
            state = 'miss'
        except (OSError, ValueError, TypeError, KeyError):
            state = 'bypassed'
        try:
            local_docker()
        except (OSError, ValueError, subprocess.SubprocessError):
            return error_report('runtime.unavailable', 'unavailable'), state
        report, _ = _run_review(wire, image)
        if cache is not None and report['provenance'] is not None:
            try:
                if cache_context(name, entries, image) == context:
                    cache.put(key, report, validate)
            except (OSError, ValueError, TypeError, KeyError):
                pass
        return report, state
    finally:
        if cache is not None:
            cache.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', help='Absolute directory path; no symlink components')
    parser.add_argument('--image', required=True, help='Reviewed local image ID: sha256:...')
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    parser.add_argument('--output', help='New safe .txt/.json file in an existing owner-only 0700 directory')
    parser.add_argument('--private-locator', help='Opt-in PRIVATE human-only .json locator; never read back into the agent')
    parser.add_argument('--preview-cache', help='Opt-in existing private cache directory; NON-AUTHORITATIVE preview only, exit 3')
    args = parser.parse_args()
    if args.preview_cache:
        if args.output or args.private_locator:
            print('Preview cache cannot publish authoritative reports or private locators.')
            return 3
        report, state = preview(args.candidate, args.image, args.preview_cache)
        print('NON-AUTHORITATIVE private preview; cache=' + state + '; fresh admission review required.')
        print(render(report, args.format))
        return 3
    try:
        from packaging import ROOT, trusted_root
        trusted_root(str(ROOT), args.candidate)
    except (OSError, ValueError):
        print('Separate trusted installation required; no review performed.')
        return 3
    try:
        report = review(args.candidate, args.image, output=args.output,
                        output_format=args.format, private_locator=args.private_locator)
    except (OutputError, OSError, UnicodeError):
        print('Output destination rejected; no report was published.')
        return 3
    print(render(report, args.format))
    return exit_code(report)


if __name__ == '__main__':
    sys.exit(main())

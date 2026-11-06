"""Offline workstream 6 rehearsal: synthetic inputs, no builds or credentials."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).absolute().parents[3]
sys.path.insert(0, str(ROOT / 'skills/review-skill-sflabs/scripts'))
sys.path.insert(0, str(ROOT / '.github/scripts'))
import action_review as action
import packaging as package
import package_reviewer
from ingest import Entry, canonical
from protocol import error_report
import test_ci

IMAGE = 'sha256:' + 'a' * 64
DIGEST = 'd' * 64
COUNTS = {'expected': 4, 'executed': 4, 'failed': 0, 'skipped': 0}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.trusted = self.root / 'trusted'
        self.trusted.mkdir()
        self.candidate = self.root / 'candidate'
        self.candidate.mkdir()
        self.output = self.root / 'private'
        self.output.mkdir(mode=0o700)
        for relative in package.LOCAL_FILES:
            target = self.trusted / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'# synthetic nonexecuted fixture\n')

    def test_snapshot_allowlist_reproducible_and_no_source_reread(self):
        (self.trusted / 'CANARY').write_text('DO_NOT_PACKAGE')
        entries, manifest, digest = package.snapshot(str(self.trusted), 'local')
        first = package.archive(entries, manifest)
        (self.trusted / package.LOCAL_FILES[0]).write_text('CHANGED_CANARY')
        self.assertEqual(first, package.archive(entries, manifest))
        self.assertEqual(digest, hashlib.sha256(manifest).hexdigest())
        self.assertNotIn(b'CANARY', first)
        with tarfile.open(fileobj=io.BytesIO(first)) as bundle:
            self.assertEqual(bundle.getnames(), ['MANIFEST.json'] + sorted(package.LOCAL_FILES))
            for item in bundle:
                self.assertEqual(item.mode, 0o600)
                self.assertEqual((item.uid, item.gid, item.mtime), (0, 0, 0))
                self.assertTrue(item.isfile())
        self.assertNotEqual(digest, package.snapshot(str(self.trusted), 'local')[2])

    def test_private_archive_runtime_copy_ownership_contract(self):
        # Static source integration only: no Docker build or runtime claim.
        entries, manifest, _ = package.snapshot(str(ROOT), 'local')
        with tarfile.open(fileobj=io.BytesIO(package.archive(entries, manifest))) as bundle:
            dockerfile = bundle.extractfile(package.REVIEWER + 'Dockerfile').read().decode()
            copies = [line.split() for line in dockerfile.splitlines() if line.startswith('COPY ')]
            self.assertEqual(copies, [
                ['COPY', 'requirements.txt', './'],
                ['COPY', '--chown=65532:65532', 'scripts/', './'],
                ['COPY', '--chown=65532:65532', 'references/catalog-snapshot.json', './catalog-snapshot.json'],
            ])
            self.assertIn('USER 65532:65532', dockerfile.splitlines())
            runtime = [item for item in bundle if item.name.startswith(package.REVIEWER + 'scripts/')
                       or item.name == package.REVIEWER + 'references/catalog-snapshot.json']
            self.assertIn(package.REVIEWER + 'scripts/entrypoint.py', {item.name for item in runtime})
            self.assertIn(package.REVIEWER + 'scripts/resources/licenses.json', {item.name for item in runtime})
            for item in runtime:
                self.assertEqual((item.mode, item.uid, item.gid), (0o600, 0, 0))
                relative = item.name.removeprefix(package.REVIEWER)
                matches = [copy for copy in copies if len(copy) == 4 and
                           (relative == copy[2] or copy[2].endswith('/') and relative.startswith(copy[2]))]
                self.assertEqual(len(matches), 1, relative)
                self.assertEqual(matches[0][1], '--chown=65532:65532')

    def test_root_candidate_overlap_symlink_and_wrong_installation_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.trusted, target_is_directory=True)
        with patch.object(package, 'ROOT', self.trusted):
            package.trusted_root(str(self.trusted), str(self.candidate))
            for trusted, candidate in [(self.candidate, self.trusted), (self.trusted, self.trusted),
                                       (self.trusted, self.root), (link, self.candidate)]:
                with self.assertRaises((ValueError, OSError)):
                    package.trusted_root(str(trusted), str(candidate))

    def test_missing_link_hardlink_and_extra_scripts_not_packaged(self):
        target = self.trusted / package.LOCAL_FILES[0]
        target.unlink()
        with self.assertRaises(ValueError):
            package.snapshot(str(self.trusted), 'local')
        target.symlink_to(self.candidate)
        with self.assertRaises(ValueError):
            package.snapshot(str(self.trusted), 'local')
        target.unlink()
        source = self.root / 'source'
        source.write_text('synthetic')
        os.link(source, target)
        with self.assertRaises(ValueError):
            package.snapshot(str(self.trusted), 'local')

    def test_save_requires_exact_digest_and_new_private_destination(self):
        digest = package.snapshot(str(self.trusted), 'local')[2]
        output = self.output / 'reviewer.tar'
        with patch.object(package, 'ROOT', self.trusted):
            with self.assertRaises(ValueError):
                package.save_snapshot(str(self.trusted), str(self.candidate), 'local', '0' * 64, str(output))
            self.assertFalse(output.exists())
            package.save_snapshot(str(self.trusted), str(self.candidate), 'local', digest, str(output))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                package.save_snapshot(str(self.trusted), str(self.candidate), 'local', digest, str(output))
            self.candidate.chmod(0o700)
            with self.assertRaises(ValueError):
                package.save_snapshot(str(self.trusted), str(self.candidate), 'local', digest,
                                      str(self.candidate / 'reviewer.tar'))

    def test_actual_profiles_capture_resources_wrapper_and_test_inputs(self):
        with patch('subprocess.run') as execute:
            local = package.snapshot(str(ROOT), 'local')
            ci = package.snapshot(str(ROOT), 'ci')
        execute.assert_not_called()
        local_paths = {item.path for item in local[0]}
        ci_paths = {item.path for item in ci[0]}
        self.assertLess(local_paths, ci_paths)
        self.assertIn('.github/scripts/package_reviewer.py', local_paths)
        self.assertNotIn('.github/scripts/action_review.py', local_paths)
        self.assertIn('.github/actions/contained-review/action.yml', ci_paths)
        self.assertIn(package.REVIEWER + 'scripts/resources/licenses.json', local_paths)

    def test_package_cli_has_no_exception_content(self):
        with patch.object(sys, 'argv', ['package', '--trusted-root', str(self.trusted),
                                       '--candidate-root', str(self.candidate), '--profile', 'local']), \
                patch.object(package_reviewer, 'trusted_root', side_effect=ValueError('CANARY')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(package_reviewer.main(), 3)
        self.assertNotIn('CANARY', output.getvalue())


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ('candidate', 'baseline', 'trusted', 'private', 'runner'):
            (self.root / name).mkdir(mode=0o700)
        self.event = {'repository': {'full_name': 'owner/repo'}, 'pull_request': {
            'base': {'sha': 'a' * 40, 'repo': {'full_name': 'owner/repo'}},
            'head': {'sha': 'b' * 40, 'repo': {'full_name': 'fork/CANARY'}, 'ref': '$(CANARY)'},
            'title': '::error::CANARY\n<script>', 'body': 'CANARY',
            'authority': True, 'image': 'CANARY'}}
        self.path = self.root / 'event.json'
        self.path.write_text(json.dumps(self.event))
        self.args = SimpleNamespace(trusted_root=str(self.root / 'trusted'),
                                    candidate_root=str(self.root / 'candidate'),
                                    base_root=str(self.root / 'baseline'), image=IMAGE,
                                    tooling_sha256=DIGEST, event_path=str(self.path),
                                    event_name='pull_request', repository='owner/repo',
                                    renames='[]', file_renames='[]')

    def aggregate(self):
        return {'comparison_schema': '1.0', 'base_revision': 'a' * 40,
                'head_revision': 'b' * 40, 'submissions': []}

    def run_action(self, counts=None, code=0, aggregate=None):
        with patch.object(action, 'trusted_root'), patch.object(action, 'snapshot', return_value=((), b'', DIGEST)), \
                patch.object(action, 'verification_evidence', return_value=(code, counts or COUNTS)), \
                patch.object(action, 'scan', return_value=(0, json.dumps(aggregate or self.aggregate()))) as scan:
            result = action.run(self.args)
        return result, scan

    def test_fork_event_uses_only_exact_shas_not_candidate_assertions(self):
        self.assertEqual(action.event_revisions(str(self.path), 'pull_request', 'owner/repo'), ('a' * 40, 'b' * 40))
        (code, report), scanner = self.run_action()
        self.assertEqual(code, 0)
        self.assertEqual(report['authority'], 'not-established-by-this-report')
        self.assertEqual(report['tooling_sha256'], DIGEST)
        options = scanner.call_args.args[0]
        self.assertEqual((options.base, options.head, options.image), ('a' * 40, 'b' * 40, IMAGE))
        self.assertEqual(options.base_root, self.args.base_root)
        self.assertEqual(options.format, 'json')
        self.assertNotIn('CANARY', json.dumps(report) + action.render_summary(report))

    def test_invalid_event_revision_repository_and_event_type_fail_closed(self):
        for event_name in ('push', 'pull_request_target', 'workflow_run'):
            with self.assertRaises(ValueError):
                action.event_revisions(str(self.path), event_name, 'owner/repo')
        for revision in ('HEAD', '--upload', 'b' * 40 + '\n', 'B' * 40):
            event = copy.deepcopy(self.event)
            event['pull_request']['head']['sha'] = revision
            self.path.write_text(json.dumps(event))
            with self.assertRaises(ValueError):
                action.event_revisions(str(self.path), 'pull_request', 'owner/repo')
        self.path.write_text(json.dumps(self.event))
        with self.assertRaises(ValueError):
            action.event_revisions(str(self.path), 'pull_request', 'other/repo')

    def test_event_link_duplicate_oversize_rejected(self):
        link = self.root / 'link.json'
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            action.event_revisions(str(link), 'pull_request', 'owner/repo')
        for raw in ('{"pull_request":{},"pull_request":{}}', 'x' * (1024 * 1024 + 1)):
            self.path.write_text(raw)
            with self.assertRaises((ValueError, KeyError)):
                action.event_revisions(str(self.path), 'pull_request', 'owner/repo')

    def test_missing_failed_skipped_coverage_prevents_scan(self):
        for counts, code in [(COUNTS | {'skipped': 1}, 0), (COUNTS | {'failed': 1}, 0),
                             (COUNTS | {'executed': 3}, 0), (COUNTS, 1)]:
            (result, report), scanner = self.run_action(counts, code)
            self.assertEqual(result, 3)
            self.assertEqual(report['status'], 'blocked')
            scanner.assert_not_called()
            self.assertEqual(report['verification'], counts)

    def test_digest_mismatch_blocks_before_verification(self):
        with patch.object(action, 'trusted_root'), patch.object(action, 'snapshot', return_value=((), b'', 'f' * 64)), \
                patch.object(action, 'verification_evidence') as verification, self.assertRaises(ValueError):
            action.run(self.args)
        verification.assert_not_called()

    def test_tooling_change_during_run_rejected(self):
        with patch.object(action, 'trusted_root'), \
                patch.object(action, 'snapshot', side_effect=[((), b'', DIGEST), ((), b'', 'f' * 64)]), \
                patch.object(action, 'verification_evidence', return_value=(0, COUNTS)), \
                patch.object(action, 'scan', return_value=(0, json.dumps(self.aggregate()))), self.assertRaises(ValueError):
            action.run(self.args)

    def test_raw_machine_output_extra_fields_and_canaries_rejected(self):
        for value in [self.aggregate() | {'raw': 'CANARY'}, self.aggregate() | {'head_revision': 'c' * 40},
                      self.aggregate() | {'submissions': [{'submission_id': 'CANARY'}]}]:
            with self.assertRaises(ValueError):
                action.safe_comparison(json.dumps(value), 'a' * 40, 'b' * 40, IMAGE, 0)
        with self.assertRaises(ValueError):
            action.safe_comparison('CANARY', 'a' * 40, 'b' * 40, IMAGE, 0)

    def test_blocked_head_report_and_wrong_exit_code(self):
        value = self.aggregate()
        value['submissions'] = [{'submission_id': 'e' * 64, 'baseline': None,
                                 'head': error_report('input.unsafe', 'blocked'),
                                 'delta': {'kind': 'added', 'comparison': 'not-comparable',
                                           'counts': dict.fromkeys(action.STATES, 0), 'findings': []}}]
        self.assertEqual(len(action.safe_comparison(json.dumps(value), 'a' * 40, 'b' * 40, IMAGE, 1)), 1)
        with self.assertRaises(ValueError):
            action.safe_comparison(json.dumps(value), 'a' * 40, 'b' * 40, IMAGE, 0)
        value['submissions'][0]['head']['findings'][0]['action'] = 'CANARY'
        with self.assertRaises(ValueError):
            action.safe_comparison(json.dumps(value), 'a' * 40, 'b' * 40, IMAGE, 1)

    def test_summary_no_links_or_raw_content_and_rejects_candidate_destination(self):
        (_, report), _ = self.run_action()
        summary = self.root / 'runner/summary'
        summary.write_text('')
        action.publish_summary(str(summary), action.render_summary(report),
                               (self.args.candidate_root, self.args.trusted_root, self.args.base_root))
        self.assertIn('expected=4 executed=4 failed=0 skipped=0', summary.read_text())
        for token in ('CANARY', '::error::', '<script', 'https:', '\x1b'):
            self.assertNotIn(token, summary.read_text())
        candidate_summary = self.root / 'candidate/summary'
        candidate_summary.write_text('untouched')
        with self.assertRaises(ValueError):
            action.publish_summary(str(candidate_summary), 'fixed', (self.args.candidate_root,))
        self.assertEqual(candidate_summary.read_text(), 'untouched')
        link = self.root / 'runner/link'
        link.symlink_to(summary)
        with self.assertRaises(OSError):
            action.publish_summary(str(link), 'fixed', (self.args.candidate_root,))

    def test_main_saves_only_validated_report_no_github_outputs_or_uploads(self):
        (_, report), _ = self.run_action()
        target = self.root / 'private/report.json'
        summary = self.root / 'runner/summary'
        summary.write_text('')
        argv = ['action']
        for name, value in vars(self.args).items():
            argv += ['--' + name.replace('_', '-'), value]
        argv += ['--report', str(target)]
        with patch.object(sys, 'argv', argv), patch.object(action, 'run', return_value=(0, report)), \
                patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary), 'GITHUB_OUTPUT': str(self.root / 'forbidden')}), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(action.main(), 0)
        self.assertEqual(json.loads(target.read_bytes()), report)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / 'forbidden').exists())
        self.assertNotIn('CANARY', stdout.getvalue() + summary.read_text())

    def test_failure_never_echoes_exception_or_candidate_output(self):
        argv = ['action']
        for name, value in vars(self.args).items():
            argv += ['--' + name.replace('_', '-'), value]
        argv += ['--report', str(self.root / 'private/report.json')]
        with patch.object(sys, 'argv', argv), patch.object(action, 'run', side_effect=ValueError('CANARY\x1b')), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(action.main(), 3)
        self.assertNotIn('CANARY', stdout.getvalue())
        self.assertFalse((self.root / 'private/report.json').exists())

    def test_action_quoted_environment_not_expressions_in_shell(self):
        wrapper = yaml.safe_load((ROOT / '.github/actions/contained-review/action.yml').read_text())
        step = wrapper['runs']['steps'][0]
        self.assertNotIn('${{', step['run'])
        self.assertNotIn('sudo', step['run'])
        self.assertNotIn('pip', step['run'])
        self.assertIn('--image "$REVIEW_IMAGE"', step['run'])
        self.assertIn('--base-root "$BASE_ROOT"', step['run'])

    def test_machine_provenance_image_catalog_and_revision_binding(self):
        from test_workstream5 import report as synthetic_report
        from comparison import compare
        value = self.aggregate()
        head = synthetic_report()
        value['submissions'] = [{'submission_id': 'e' * 64, 'baseline': None,
                                 'head': head, 'delta': compare(None, head, kind='added')}]
        safe = action.safe_comparison(json.dumps(value), 'a' * 40, 'b' * 40, IMAGE, 0)
        self.assertEqual(safe[0]['head']['provenance']['image_id'], IMAGE)
        for key, replacement in [('image_id', 'sha256:' + 'f' * 64), ('base_revision', 'c' * 40),
                                 ('catalog', {'sha256': 'f' * 64, 'revision': head['catalog_revision']})]:
            tampered = copy.deepcopy(value)
            tampered['submissions'][0]['head']['provenance'][key] = replacement
            with self.assertRaises(ValueError):
                action.safe_comparison(json.dumps(tampered), 'a' * 40, 'b' * 40, IMAGE, 0)

    def test_action_script_path_with_dotdot_can_load_without_candidate_imports(self):
        script = ROOT / '.github/actions/contained-review/../../scripts/action_review.py'
        result = subprocess.run([sys.executable, '-I', '-B', str(script), '--help'],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'--tooling-sha256', result.stdout)

    def test_verification_uses_argument_array_and_never_shell(self):
        from verify_reviewer_runtime import verification_evidence
        def fake(command, **kwargs):
            self.assertIsInstance(command, list)
            self.assertNotIn('shell', kwargs)
            self.assertEqual(command[-3:], ['--image', IMAGE, '--worker'])
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            kwargs['stdout'].write(canonical(COUNTS))
            return subprocess.CompletedProcess(command, 0)
        with patch('verify_reviewer_runtime.subprocess.run', side_effect=fake):
            self.assertEqual(verification_evidence(IMAGE), (0, COUNTS))


class ForkGitRehearsal(unittest.TestCase):
    def test_synthetic_fork_exact_git_selection_with_unavailable_runtime(self):
        fixture = test_ci.GitSelectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        target = fixture.root / 'skills/CANARY $(not-a-command)'
        target.mkdir(parents=True)
        (target / 'SKILL.md').write_text('Synthetic fixture only')
        head = fixture.commit()
        event = {'repository': {'full_name': 'owner/repo'}, 'pull_request': {
            'base': {'sha': fixture.base, 'repo': {'full_name': 'owner/repo'}},
            'head': {'sha': head, 'repo': {'full_name': 'fork/CANARY'}}}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            baseline = root / 'baseline'
            subprocess.run(['git', 'clone', '--no-hardlinks', '--quiet', str(fixture.root), str(baseline)],
                           check=True, capture_output=True, timeout=10)
            subprocess.run(['git', '-C', str(baseline), 'checkout', '--quiet', fixture.base],
                           check=True, capture_output=True, timeout=10)
            event_path = root / 'event.json'
            event_path.write_text(json.dumps(event))
            args = SimpleNamespace(trusted_root=str(ROOT), candidate_root=str(fixture.root),
                                   base_root=str(baseline), image=IMAGE, tooling_sha256=package.snapshot(str(ROOT), 'ci')[2],
                                   event_path=str(event_path), event_name='pull_request', repository='owner/repo',
                                   renames='[]', file_renames='[]')
            with patch.object(action, 'verification_evidence', return_value=(0, COUNTS)), \
                    patch('validate_skill.review', return_value=error_report('runtime.unavailable', 'unavailable')) as reviewer:
                code, report = action.run(args)
            self.assertEqual(code, 1)
            self.assertEqual(len(report['submissions']), 1)
            self.assertNotIn('CANARY', json.dumps(report))
            self.assertEqual(reviewer.call_args.args[1], IMAGE)
            self.assertEqual(reviewer.call_args.kwargs['head_revision'], head)
            from test_workstream5 import VALID, APACHE, CATALOG
            from checks import review as deterministic_review
            from ingest import collect
            from git_context import verify_entries
            (target / 'SKILL.md').write_bytes(VALID)
            (target / 'LICENSE').write_bytes(APACHE)
            head = fixture.commit()
            event['pull_request']['head']['sha'] = head
            event_path.write_text(json.dumps(event))

            def offline_runtime(path, image, **kwargs):
                # Explicit synthetic runtime double; exercise blob verification
                # and deterministic core without claiming Docker isolation.
                entries = collect(path)
                verify_entries(entries, kwargs['expected_files'])
                value = deterministic_review(Path(path).name, entries, CATALOG)
                value['provenance'].update(image_id=image, base_revision=kwargs['base_revision'],
                                           head_revision=kwargs['head_revision'])
                return value

            with patch.object(action, 'verification_evidence', return_value=(0, COUNTS)), \
                    patch('validate_skill.review', side_effect=offline_runtime):
                code, report = action.run(args)
            self.assertEqual(code, 0)
            self.assertEqual(report['status'], 'passed')
            self.assertNotIn('CANARY', json.dumps(report) + action.render_summary(report))


if __name__ == '__main__':
    unittest.main()

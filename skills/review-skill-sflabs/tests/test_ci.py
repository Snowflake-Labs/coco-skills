import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'skills/review-skill-sflabs/scripts'))
sys.path.insert(0, str(ROOT / '.github/scripts'))
# Missing integration files are errors, not silently skipped test classes.
import validate_skill as ci
import test_reviewer_isolated as isolated
import verify_reviewer_runtime as trusted
import reviewer_test_support as evidence
from collect_snapshot import REPOSITORY_INPUTS
from ingest import UnsafeInput, absent, collect, decode, encode
import launch
IMAGE = 'sha256:' + 'a' * 64


class CITests(unittest.TestCase):
    def test_paths_nul_delimited_no_shell_and_deleted_skill(self):
        output = b'skills/example/SKILL.md\x00skills/example/LICENSE\x00skills/another/file.md\x00skills/placeholder\x00'
        def fake_run(command, **kwargs):
            kwargs['stdout'].write(output)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(ci.subprocess, 'run', side_effect=fake_run) as run:
            self.assertEqual(ci.changed_skills('/candidate', 'a' * 40, 'b' * 40), ['another', 'example', 'placeholder'])
            self.assertNotIn('shell', run.call_args.kwargs)
            self.assertIn('-z', run.call_args.args[0])
            self.assertIn('--no-renames', run.call_args.args[0])

    def test_ci_forwards_validated_revision_context(self):
        from protocol import error_report
        with patch.object(sys, 'argv', ['validate', '--candidate-root', '/candidate',
                                       '--base', 'a' * 40, '--head', 'b' * 40, '--image', IMAGE]), \
                patch.object(ci, 'Checkout') as checkout, \
                patch.object(ci, 'changed_skills', return_value=['example']), \
                patch.object(ci, 'absent', return_value=False), \
                patch.object(ci, 'review', return_value=error_report('runtime.unavailable', 'unavailable')) as reviewer, \
                contextlib.redirect_stdout(io.StringIO()):
            checkout.return_value.files.return_value = {'SKILL.md': ('100644', 'blob', 'c' * 40)}
            self.assertEqual(ci.main(), 1)
        reviewer.assert_called_once_with('/candidate/skills/example', IMAGE,
                                         base_revision='a' * 40, head_revision='b' * 40,
                                         expected_files={'SKILL.md': ('100644', 'blob', 'c' * 40)})

    def test_bad_revision_rejected_before_git(self):
        for revision in ['--bad', 'a' * 39, 'A' * 40, 'a' * 40 + '\n', 'HEAD']:
            with patch.object(ci.subprocess, 'run') as run, self.assertRaises(ValueError):
                ci.changed_skills('/candidate', revision, 'b' * 40)
            run.assert_not_called()

    def test_changed_control_filename_rejected(self):
        for name in [b'bad\nname', b'bad\x7fname', b'..', b'.', b'', b'\xff']:
            with patch('git_context.git', return_value=b'skills/' + name + b'/a.md\x00'):
                with self.assertRaises(ValueError):
                    ci.changed_skills('/candidate', 'a' * 40, 'b' * 40)

    def test_changed_skill_limit(self):
        output = b'\x00'.join(('skills/item' + str(index)).encode() for index in range(101))
        with patch('git_context.git', return_value=output):
            with self.assertRaises(ValueError):
                ci.changed_skills('/candidate', 'a' * 40, 'b' * 40)

    def test_workflow_builds_and_invokes_only_trusted_tooling(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/validate-skill.yml').read_text())
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        for job in workflow['jobs'].values():
            for step in job['steps']:
                if step.get('uses', '').startswith('actions/checkout'):
                    self.assertIs(step['with']['persist-credentials'], False)
                command = step.get('run', '')
                self.assertNotIn('--mount', command)
                self.assertNotIn('pull_request_target', command)
                self.assertNotIn('docker build', command)
                self.assertNotIn('docker pull', command)
                self.assertNotIn('pip install', command)
                if step.get('uses', '').startswith('actions/'):
                    self.assertRegex(step['uses'], r'@[0-9a-f]{40}$')
                if 'python -I' in command:
                    self.assertIn('/trusted/.github/scripts/', command)

    def test_workflow_triggers_and_trusted_verification(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/validate-skill.yml').read_text())
        triggers = workflow.get('on', workflow.get(True))['pull_request']['paths']
        for path in ['CONTRIBUTING.md', '.github/scripts/test_reviewer_isolated.py', '.github/scripts/verify_reviewer_runtime.py',
                     '.github/scripts/reviewer_test_support.py', '.github/scripts/validate_skill.py']:
            self.assertIn(path, triggers)
        steps = workflow['jobs']['validate']['steps']
        admission = next(index for index, step in enumerate(steps) if step.get('uses') == './trusted/.github/actions/contained-review')
        inputs = steps[admission]['with']
        self.assertEqual(inputs['base-root'], '${{ github.workspace }}/baseline')
        self.assertEqual(inputs['candidate-root'], '${{ github.workspace }}/candidate')
        self.assertNotIn('continue-on-error', steps[admission])
        self.assertEqual(workflow['jobs']['validate']['if'], '${{ false }}')
        proposed = steps[-1]['run']
        self.assertIn('--candidate-root "$GITHUB_WORKSPACE/candidate"', proposed)
        self.assertIn('--image "$REVIEW_IMAGE"', proposed)
        self.assertEqual(inputs['image'], '${{ env.REVIEW_IMAGE }}')
        checkouts = [step['with'] for step in steps if step.get('uses', '').startswith('actions/checkout@')]
        self.assertEqual([item['path'] for item in checkouts], ['trusted', 'baseline', 'candidate'])
        self.assertEqual(checkouts[0]['ref'], '${{ env.TOOLING_REVISION }}')
        self.assertEqual(checkouts[1]['ref'], '${{ github.event.pull_request.base.sha }}')
        self.assertEqual(checkouts[2]['ref'], '${{ github.event.pull_request.head.sha }}')

    def test_catalog_contains_no_unreviewed_descriptions(self):
        catalog = json.loads((ROOT / 'skills/review-skill-sflabs/references/catalog-snapshot.json').read_text())
        self.assertEqual(len(catalog['revision']), 40)
        self.assertEqual(len(catalog['entries']), 26)
        enriched = [item for item in catalog['entries'] if 'summary' in item]
        self.assertEqual({item['name'] for item in enriched}, {'rbac', 'snowpipe-bcdr', 'semantic-view-patterns'})
        for item in enriched:
            self.assertEqual(item['source_revision'], catalog['revision'])
            self.assertEqual(item['source_path'], 'skills/' + item['name'] + '/SKILL.md')


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_absent_skill_and_absent_skills_parent(self):
        self.assertTrue(absent(str(self.root / 'skills/example')))
        (self.root / 'skills').mkdir()
        self.assertTrue(absent(str(self.root / 'skills/example')))

    def test_parent_symlinks_never_count_as_removed(self):
        for destination in [self.root / 'missing', self.root]:
            parent = self.root / 'skills'
            parent.symlink_to(destination, target_is_directory=True)
            with self.assertRaises((UnsafeInput, OSError)):
                absent(str(parent / 'example'))
            with patch.object(sys, 'argv', ['validate', '--candidate-root', str(self.root), '--base', 'a' * 40,
                                          '--head', 'b' * 40, '--image', IMAGE]), \
                    patch.object(ci, 'changed_skills', return_value=['example']), \
                    patch.object(ci, 'review') as review, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(ci.main(), 3)
                self.assertNotIn('removed', output.getvalue())
                review.assert_not_called()
            parent.unlink()

    def test_existing_symlink_file_fifo_are_not_absent(self):
        target = self.root / 'example'
        for kind in ('symlink', 'file', 'fifo'):
            if kind == 'symlink':
                target.symlink_to(self.root / 'missing')
            elif kind == 'file':
                target.write_text('synthetic')
            else:
                os.mkfifo(target)
            self.assertFalse(absent(str(target)))
            with self.assertRaises(UnsafeInput):
                collect(str(target))
            target.unlink()

    def test_parent_regular_file_and_invalid_paths_fail_closed(self):
        (self.root / 'skills').write_text('synthetic')
        for path in [str(self.root / 'skills/example'), 'relative', str(self.root / '../outside')]:
            with self.assertRaises((UnsafeInput, OSError)):
                absent(path)


class GitSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.git('init', '-q')
        (self.root / 'seed').write_text('synthetic fixture')
        self.base = self.commit()

    def git(self, *arguments):
        return subprocess.run(['git', '-C', str(self.root), *arguments], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10).stdout.decode().strip()

    def commit(self):
        self.git('add', '--all')
        # Identity applies only to synthetic fixture commits, not repository config.
        self.git('-c', 'user.name=Synthetic Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'synthetic')
        return self.git('rev-parse', 'HEAD')

    def test_two_component_symlinks_selected_and_blocked(self):
        skills = self.root / 'skills'
        skills.mkdir()
        (skills / 'dangling').symlink_to(self.root / 'missing')
        (skills / 'directory').symlink_to(self.root, target_is_directory=True)
        (skills / 'placeholder').write_text('synthetic')
        head = self.commit()
        self.assertEqual(ci.changed_skills(str(self.root), self.base, head), ['dangling', 'directory', 'placeholder'])
        with patch.object(sys, 'argv', ['validate', '--candidate-root', str(self.root), '--base', self.base,
                                      '--head', head, '--image', IMAGE]), patch('launch.local_docker'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(ci.main(), 1)
        self.assertEqual(output.getvalue().count('input.unsafe'), 3)
        self.assertNotIn('removed', output.getvalue())

    def test_rename_and_complete_deletion(self):
        old = self.root / 'skills/old'
        old.mkdir(parents=True)
        (old / 'SKILL.md').write_text('synthetic')
        base = self.commit()
        old.rename(self.root / 'skills/new')
        head = self.commit()
        self.assertEqual(ci.changed_skills(str(self.root), base, head), ['new', 'old'])
        (self.root / 'skills/new/SKILL.md').unlink()
        (self.root / 'skills/new').rmdir()
        (self.root / 'skills').rmdir()
        deleted = self.commit()
        self.assertEqual(ci.changed_skills(str(self.root), head, deleted), ['new'])
        with patch.object(sys, 'argv', ['validate', '--candidate-root', str(self.root), '--base', head,
                                      '--head', deleted, '--image', IMAGE]), patch.object(ci, 'review') as review, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(ci.main(), 0)
            self.assertIn('Comparison: not-comparable; selection: deleted', output.getvalue())
            self.assertIn('resolved=0', output.getvalue())
            review.assert_not_called()

    def test_regular_addition_nested_changes_and_rename_outside_skills(self):
        skill = self.root / 'skills/space name'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('synthetic')
        added = self.commit()
        self.assertEqual(ci.changed_skills(str(self.root), self.base, added), ['space name'])
        (skill / 'references').mkdir()
        (skill / 'references/notes.md').write_text('synthetic nested change')
        nested = self.commit()
        self.assertEqual(ci.changed_skills(str(self.root), added, nested), ['space name'])
        skill.rename(self.root / 'outside-skills')
        moved = self.commit()
        self.assertEqual(ci.changed_skills(str(self.root), nested, moved), ['space name'])
        self.assertTrue(absent(str(skill)))

    def test_git_control_names_rejected_without_echo(self):
        for index, name in enumerate(['control\nname', 'control\x7fname']):
            target = self.root / 'skills' / name
            target.mkdir(parents=True)
            (target / 'SKILL.md').write_text('synthetic')
        head = self.commit()
        with self.assertRaises(ValueError):
            ci.changed_skills(str(self.root), self.base, head)

    def test_nonexistent_valid_hex_revision_fails_safely(self):
        with self.assertRaises(subprocess.CalledProcessError):
            ci.changed_skills(str(self.root), '0' * 40, self.base)


class RepositorySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for relative in REPOSITORY_INPUTS:
            path = self.root / relative
            if relative == 'skills/review-skill-sflabs':
                path = path / 'tests/test_synthetic.py'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic fixture only\n')
        (self.root / 'private-unrelated').write_text('UNRELATED_CANARY')

    def test_selected_inputs_preserve_repository_layout(self):
        wire = launch.collect(str(self.root), repository_tests=True)
        name, entries = decode(wire)
        self.assertEqual(name, 'proposed-reviewer')
        self.assertEqual(len(entries), len(REPOSITORY_INPUTS))
        self.assertNotIn(b'UNRELATED_CANARY', b''.join(entry.data for entry in entries))
        self.assertIn('.github/scripts/validate_skill.py', [entry.path for entry in entries])
        self.assertIn('skills/review-skill-sflabs/tests/test_synthetic.py', [entry.path for entry in entries])

    def test_missing_and_symlinked_integration_inputs_rejected(self):
        target = self.root / '.github/scripts/validate_skill.py'
        target.unlink()
        with self.assertRaises(UnsafeInput):
            launch.collect(str(self.root), repository_tests=True)
        target.symlink_to(self.root / 'private-unrelated')
        with self.assertRaises(UnsafeInput):
            launch.collect(str(self.root), repository_tests=True)

    def test_integration_parent_symlink_rejected(self):
        scripts = self.root / '.github/scripts'
        scripts.rename(self.root / 'other-scripts')
        scripts.symlink_to(self.root / 'other-scripts', target_is_directory=True)
        with self.assertRaises(UnsafeInput):
            launch.collect(str(self.root), repository_tests=True)


class TestEvidenceTests(unittest.TestCase):
    def counts(self, **updates):
        return dict(expected=4, executed=4, failed=0, skipped=0) | updates

    def test_count_schema_bounds_and_consistency(self):
        self.assertEqual(evidence.validate_counts(json.dumps(self.counts()).encode()), self.counts())
        for field in evidence.FIELDS:
            for value in [True, -1, 10001, 'CANARY\x1b[31m', 1.5, None]:
                with self.assertRaises(ValueError):
                    evidence.validate_counts(json.dumps(self.counts(**{field: value})).encode())
        for value in [self.counts(expected=3), self.counts(skipped=5), self.counts(failed=4, skipped=1),
                      self.counts(extra='CANARY'), {}, []]:
            with self.assertRaises(ValueError):
                evidence.validate_counts(json.dumps(value).encode())
        for raw in [b'x' * 513, b'{', b'{"expected":1,"expected":2}']:
            with self.assertRaises(ValueError):
                evidence.validate_counts(raw)

    def test_skipped_missing_and_failed_are_not_verified(self):
        for counts in [self.counts(skipped=1), self.counts(failed=1), self.counts(executed=3),
                       self.counts(expected=0, executed=0)]:
            self.assertFalse(evidence.complete(counts))
        self.assertTrue(evidence.complete(self.counts()))
        self.assertFalse(trusted.required_present(unittest.TestSuite()))

    def test_required_inventory_matches_actual_tests(self):
        suite = unittest.TestLoader().discover(str(ROOT / 'skills/review-skill-sflabs/tests'))
        self.assertTrue(trusted.required_present(suite))

    def test_counts_include_subtests_skips_and_expected_failures(self):
        class Synthetic(unittest.TestCase):
            def test_pass(self):
                pass

            @unittest.skip('synthetic skip')
            def test_skip(self):
                pass

            def test_failures(self):
                for value in range(2):
                    with self.subTest(value=value):
                        self.fail('SYNTHETIC_CANARY')

            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail('SYNTHETIC_CANARY')

        counts = evidence.run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(Synthetic))
        self.assertEqual(counts, self.counts(failed=2, skipped=1))

    def test_untrusted_output_never_reaches_host_summary(self):
        marker = b'CANDIDATE_CANARY\x1b[31m\x7f'
        for raw in [marker, json.dumps(self.counts(extra=marker.decode())).encode(), b'x' * 513]:
            def fake_run(command, **kwargs):
                if command[1] == 'run':
                    self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
                    self.assertEqual(kwargs['timeout'], 120)
                    self.assertNotIn('--mount', command)
                    kwargs['stdout'].write(raw)
                return subprocess.CompletedProcess(command, 0)
            with patch.object(isolated, 'local_docker'), patch.object(isolated, 'collect', return_value=b'{}'), \
                    patch.object(isolated.subprocess, 'run', side_effect=fake_run) as run, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(isolated.run_proposed('/unused', IMAGE), 3)
                self.assertNotIn('CANARY', output.getvalue())
                self.assertNotIn('\x1b', output.getvalue())
                self.assertEqual(run.call_args.args[0][1:3], ['rm', '-f'])

    def test_bootstrap_runs_only_synthetic_tests_and_discards_raw_output(self):
        # Exercise the exact bootstrap with reviewed synthetic bytes, not a skill.
        # Redirect its fixed container paths into a private temporary fixture.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            code = isolated.BOOTSTRAP.replace('/tmp/proposed', str(root / 'proposed')).replace(
                '/tmp/counts.json', str(root / 'counts.json'))
            synthetic = b'''import os, unittest
os.write(1, b"IMPORT_CANARY\\x1b[31m")
os.write(2, b"STDERR_CANARY")
class Synthetic(unittest.TestCase):
    def test_integration_file(self):
        from pathlib import Path
        root = Path(__file__).parents[3]
        self.assertEqual((root / '.github/scripts/fixture.py').read_text(), '# synthetic')
        os.write(1, b"TEST_CANARY")
    @unittest.skip('SKIP_CANARY')
    def test_skip(self):
        pass
    def test_fail(self):
        self.fail('FAILURE_CANARY')
'''
            from ingest import Entry
            wire = encode((Entry('skills/review-skill-sflabs/tests/test_synthetic.py', synthetic),
                           Entry('.github/scripts/fixture.py', b'# synthetic')), 'synthetic')
            result = subprocess.run([sys.executable, '-I', '-c', code], input=wire,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b'')
            self.assertNotIn(b'CANARY', result.stdout)
            self.assertEqual(evidence.validate_counts(result.stdout),
                             {'expected': 3, 'executed': 3, 'failed': 1, 'skipped': 1})

    def test_proposed_collection_failure_never_starts_container(self):
        with patch.object(isolated, 'local_docker'), \
                patch.object(isolated, 'collect', side_effect=subprocess.TimeoutExpired('CANARY', 15)), \
                patch.object(isolated.subprocess, 'run') as run, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(isolated.run_proposed('/unused', IMAGE), 3)
            run.assert_not_called()
            self.assertNotIn('CANARY', output.getvalue())

    def test_proposed_skips_explicitly_untrusted_and_incomplete(self):
        def fake_run(command, **kwargs):
            if command[1] == 'run':
                kwargs['stdout'].write(json.dumps(self.counts(skipped=2)).encode())
            return subprocess.CompletedProcess(command, 0)
        with patch.object(isolated, 'local_docker'), patch.object(isolated, 'collect', return_value=b'{}'), \
                patch.object(isolated.subprocess, 'run', side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(isolated.run_proposed('/unused', IMAGE), 0)
        self.assertIn('Untrusted proposed-test counts: expected=4 executed=4 failed=0 skipped=2', output.getvalue())
        self.assertIn('incomplete', output.getvalue())
        self.assertIn('Not authoritative', output.getvalue())

    def test_proposed_timeout_cleans_up(self):
        def fake_run(command, **kwargs):
            if command[1] == 'run':
                raise subprocess.TimeoutExpired(command, 120, output=b'CANARY')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(isolated, 'local_docker'), patch.object(isolated, 'collect', return_value=b'{}'), \
                patch.object(isolated.subprocess, 'run', side_effect=fake_run) as run, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(isolated.run_proposed('/unused', IMAGE), 3)
        self.assertEqual(run.call_args.args[0][1:3], ['rm', '-f'])
        self.assertNotIn('CANARY', output.getvalue())

    def test_trusted_worker_missing_security_tests_fails_closed(self):
        with patch.object(trusted.unittest.TestLoader, 'discover', return_value=unittest.TestSuite()), \
                patch.object(trusted, 'run_suite') as run, patch.dict(os.environ):
            self.assertEqual(trusted.worker(IMAGE), 3)
            run.assert_not_called()

    def test_trusted_verification_requires_complete_successful_counts(self):
        for counts, code, expected in [(self.counts(), 0, 0), (self.counts(skipped=1), 1, 1),
                                       (self.counts(executed=3), 0, 1), (self.counts(), 3, 1)]:
            def fake_run(command, **kwargs):
                self.assertIn('--worker', command)
                self.assertNotIn('/candidate', ' '.join(command))
                self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
                kwargs['stdout'].write(json.dumps(counts).encode())
                return subprocess.CompletedProcess(command, code)
            with patch.object(trusted.subprocess, 'run', side_effect=fake_run), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(trusted.verify(IMAGE), expected)
            if expected:
                self.assertIn('NOT VERIFIED', output.getvalue())

    def test_trusted_invalid_or_timeout_evidence_is_not_verified(self):
        for timeout in [False, True]:
            def fake_run(command, **kwargs):
                if timeout:
                    raise subprocess.TimeoutExpired(command, 240, output=b'CANARY')
                kwargs['stdout'].write(b'CANARY\x1b[31m')
                return subprocess.CompletedProcess(command, 0)
            with patch.object(trusted.subprocess, 'run', side_effect=fake_run), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(trusted.verify(IMAGE), 3)
            self.assertIn('NOT VERIFIED', output.getvalue())
            self.assertNotIn('CANARY', output.getvalue())


if __name__ == '__main__':
    unittest.main()

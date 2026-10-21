"""Synthetic-only optimization and non-authoritative private cache boundaries."""
import contextlib
import copy
from dataclasses import FrozenInstanceError
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import checks
import ingest
import launch
import model_contract
import packaging
import report_cache
from protocol import canonical, render, strict_json
from license_fixtures import APACHE

IMAGE = 'sha256:' + 'a' * 64
VALID = b'---\nname: example\ndescription: "Use when reviewing. Triggers: review"\n---\nAdvisory report.\n'
ENTRIES = (ingest.Entry('LICENSE', APACHE), ingest.Entry('SKILL.md', VALID))


class AnalysisTests(unittest.TestCase):
    def test_one_review_decode_and_manifest_for_pair(self):
        with patch.object(checks, '_review', wraps=checks._review) as review, \
                patch.object(checks, 'text_contents', wraps=checks.text_contents) as texts, \
                patch('protocol.manifest', wraps=ingest.manifest) as manifest:
            result, packet = model_contract.review_and_packet('example', ENTRIES, enabled=True)
        self.assertEqual(review.call_count, 1)
        self.assertEqual(texts.call_count, 1)
        self.assertEqual(manifest.call_count, 1)
        self.assertEqual(result, checks.review('example', ENTRIES))
        self.assertEqual(packet, model_contract.make_packet('example', ENTRIES, enabled=True))
        result['provenance']['snapshot_sha256'] = '0' * 64
        self.assertNotEqual(result['provenance'], packet['provenance'])

    def test_analysis_is_deeply_immutable_and_not_accepted_as_authority(self):
        analysis = checks._analyze('example', ENTRIES, None)
        with self.assertRaises(FrozenInstanceError):
            analysis.entries = ()
        with self.assertRaises(TypeError):
            analysis.texts['SKILL.md'] = ('F0002', 'changed')
        with self.assertRaises(TypeError):
            analysis.records[0]['sha256'] = '0' * 64
        for function in (checks.review, model_contract.make_packet, model_contract.review_and_packet):
            for argument in ('analysis', 'report'):
                with self.assertRaises(TypeError):
                    function('example', ENTRIES, **{argument: analysis})

    def test_arbitrary_packet_inputs_still_require_fresh_eligibility(self):
        model_contract.make_packet('example', ENTRIES, enabled=True)
        for entries in (ENTRIES + (ingest.Entry('notes.txt', b'password="synthetic-marker-only"'),),
                        ENTRIES + (ingest.Entry('password=synthetic-marker-only.txt', b'example'),),
                        ENTRIES + (ingest.Entry('binary.zip', b'PK'),),
                        ENTRIES + (ingest.Entry('bad.txt', b'\xff'),),
                        ENTRIES + (ingest.Entry('big.txt', b'x' * 140000),),
                        (ingest.Entry('SKILL.md', VALID),)):
            with self.subTest(entries=len(entries)), self.assertRaises(model_contract.ContractError):
                model_contract.make_packet('example', entries, enabled=True)
        with patch.object(checks, '_review') as reviewer, self.assertRaises(model_contract.ContractError):
            model_contract.make_packet('example', ENTRIES)
        reviewer.assert_not_called()

    def test_supported_text_is_decoded_once_in_checks(self):
        class CountedBytes(bytes):
            calls = 0
            def decode(self, *args, **kwargs):
                self.calls += 1
                return super().decode(*args, **kwargs)
        content = CountedBytes(VALID)
        model_contract.make_packet('example', (ENTRIES[0], ingest.Entry('SKILL.md', content)), enabled=True)
        self.assertEqual(content.calls, 1)

    def test_baseline_corpus_report_and_packet_equivalence(self):
        from benchmark_reviewer import corpus, paired
        report, packet = paired(corpus())
        self.assertEqual(ingest.manifest(corpus(), 'example')[1],
                         '6c28bb3293e0a61301fecad12f76b259caf20a53c9136656df84ed7e6c45edfe')
        self.assertEqual(hashlib.sha256(canonical(report)).hexdigest(),
                         '7eaed312a419f97f63931ee465c242a327f925ea4a8b592bb6c683060de58ef0')
        self.assertEqual(packet['packet_sha256'],
                         '396e3a8ccb833f8a3bb9b2f795b04772b7b4c92d00213f861b5a4aedb8e03a5b')
        # The only intentional packet change is the corrected LF coordinate count.
        legacy = copy.deepcopy(packet)
        del legacy['packet_sha256']
        for item in legacy['files']:
            item['line_count'] = len(item['text'].splitlines())
        self.assertEqual(hashlib.sha256(canonical(legacy)).hexdigest(),
                         '2e97fcd963c7309c7e838c4cad5153bd9c8d0560c0ab74447d829543e8f14b2f')

    def test_finding_limit_retained_and_dedup_stable(self):
        def too_many(texts, paths, add):
            for line in range(1, 4098):
                add('markdown.limited', file_id='F0002', line=line)
        with patch.object(checks, 'check_documents', side_effect=too_many), self.assertRaises(ValueError):
            checks.review('example', ENTRIES)
        def duplicate(texts, paths, add):
            for _ in range(5000):
                add('markdown.limited', file_id='F0002', line=1)
        with patch.object(checks, 'check_documents', side_effect=duplicate):
            report = checks.review('example', ENTRIES)
        self.assertEqual(sum(item['code'] == 'markdown.limited' for item in report['findings']), 1)


class CacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.candidate = self.root / 'example'
        self.directory = self.root / 'private'
        self.trusted = self.root / 'trusted'
        for path in (self.candidate, self.directory, self.trusted):
            path.mkdir(mode=0o700)
        for entry in ENTRIES:
            (self.candidate / entry.path).write_bytes(entry.data)
        self.cache = report_cache.PrivateCache(str(self.directory), str(self.candidate), str(self.trusted))
        self.addCleanup(self.cache.close)
        self.key = report_cache.key_for({'synthetic': 'fixture'})
        self.path = self.directory / self.cache.slot(self.key)
        self.report = checks.review('example', ENTRIES, launch.trusted_catalog_data())
        self.report['provenance']['image_id'] = IMAGE

    def validate(self, report):
        launch.validate_snapshot_report(report, 'example', ENTRIES, IMAGE)

    def put(self):
        self.assertTrue(self.cache.put(self.key, self.report, self.validate))

    def test_timestamp_integer_bound_and_validation_time_race(self):
        self.put()
        original = self.path.read_bytes()
        for timestamp in (10 ** 400, -1, True):
            value = strict_json(original)
            value['created'] = timestamp
            self.path.write_bytes(canonical(value))
            self.assertIsNone(self.cache.get(self.key, self.validate))
        self.path.write_bytes(original)
        def replaced(report):
            self.validate(report)
            self.path.unlink()
            self.path.write_bytes(b'replacement during validation')
        self.assertIsNone(self.cache.get(self.key, replaced))
        self.assertEqual(self.path.read_bytes(), b'replacement during validation')

    def test_post_read_permissions_and_directory_replacement_rejected(self):
        self.put()
        original_read = os.read
        def chmod(descriptor, amount):
            value = original_read(descriptor, amount)
            self.directory.chmod(0o755)
            return value
        with patch.object(report_cache.os, 'read', side_effect=chmod):
            self.assertIsNone(self.cache.get(self.key, self.validate))
        self.directory.chmod(0o700)
        self.directory.rename(self.root / 'old')
        self.directory.mkdir(mode=0o700)
        self.assertIsNone(self.cache.get(self.key, self.validate))
        self.assertFalse(self.cache.put(self.key, self.report, self.validate))

    def test_valid_schema_wrong_lines_related_and_status_rejected(self):
        from protocol import finding
        self.put()
        original = self.path.read_bytes()
        mutations = []
        value = strict_json(original)
        value['report']['findings'].append(finding('policy.tone', file_id='F0002', line=100,
                                                 source_sha256=value['report']['sources'][1]['source_sha256']))
        mutations.append(value)
        value = strict_json(original)
        from catalog import QUESTION
        value['report']['related'] = [{'catalog_id': 'f' * 64, 'question': QUESTION}]
        mutations.append(value)
        value = strict_json(original)
        value['report']['status'] = 'failed'
        mutations.append(value)
        for value in mutations:
            self.path.write_bytes(canonical(value))
            self.assertIsNone(self.cache.get(self.key, self.validate))

    def test_oversized_put_and_preexisting_race_do_not_modify_files(self):
        with patch.object(report_cache, 'MAX_BYTES', 100):
            self.assertFalse(self.cache.put(self.key, self.report, self.validate))
        self.assertFalse(self.path.exists())
        original_open = os.open
        def create_first(name, flags, *args, **kwargs):
            if name == self.path.name and flags & os.O_CREAT:
                self.path.write_bytes(b'other writer')
            return original_open(name, flags, *args, **kwargs)
        with patch.object(report_cache.os, 'open', side_effect=create_first):
            self.assertFalse(self.cache.put(self.key, self.report, self.validate))
        self.assertEqual(self.path.read_bytes(), b'other writer')

    def test_round_trip_private_no_raw_content_or_mapping(self):
        self.put()
        self.assertEqual(self.cache.get(self.key, self.validate), self.report)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        raw = self.path.read_bytes()
        for content in (b'LICENSE', b'SKILL.md', VALID, b'example', b'Apache License'):
            self.assertNotIn(content, raw)
        self.assertEqual(set(strict_json(raw)), {'format', 'key', 'created', 'report'})

    def test_context_invalidation_every_component(self):
        context = launch.cache_context('example', ENTRIES, IMAGE)
        original = report_cache.key_for(context)
        for field in context:
            altered = copy.deepcopy(context)
            altered[field] = {'changed': 'synthetic'}
            self.assertNotEqual(original, report_cache.key_for(altered), field)
        for group in ('limits', 'config'):
            for field in context[group]:
                altered = copy.deepcopy(context)
                altered[group][field] = 'changed'
                self.assertNotEqual(original, report_cache.key_for(altered), field)
        for name, entries in [('other', ENTRIES), ('example', ENTRIES + (ingest.Entry('x.txt', b'x'),)),
                              ('example', (ENTRIES[0], ingest.Entry('SKILL.md', VALID + b'\n'))),
                              ('example', (ENTRIES[0], ingest.Entry('RENAMED.md', VALID)))]:
            self.assertNotEqual(original, report_cache.key_for(launch.cache_context(name, entries, IMAGE)))
        self.assertEqual(original, report_cache.key_for(launch.cache_context('example', ENTRIES[::-1], IMAGE)))
        with patch.object(launch, 'trusted_catalog_data', return_value={'revision': 'a' * 40, 'entries': []}):
            self.assertNotEqual(original, report_cache.key_for(launch.cache_context('example', ENTRIES, IMAGE)))
        with patch.object(packaging, 'snapshot', return_value=((), b'', 'f' * 64)):
            self.assertNotEqual(original, report_cache.key_for(launch.cache_context('example', ENTRIES, IMAGE)))
        self.assertNotEqual(original, report_cache.key_for(launch.cache_context('example', ENTRIES, 'sha256:' + 'b' * 64)))

    def test_corrupt_duplicate_oversize_and_provenance_fail_safe(self):
        self.put()
        valid = self.path.read_bytes()
        for raw in (b'{', b'{"key":1,"key":2}', b'x' * (report_cache.MAX_BYTES + 1)):
            self.path.write_bytes(raw)
            self.assertIsNone(self.cache.get(self.key, self.validate))
        for field in ('format', 'key', 'created'):
            value = strict_json(valid)
            value[field] = 'bad'
            self.path.write_bytes(canonical(value))
            self.assertIsNone(self.cache.get(self.key, self.validate))
        for field in ('snapshot_sha256', 'validator', 'image_id', 'catalog', 'base_revision', 'head_revision'):
            value = strict_json(valid)
            value['report']['provenance'][field] = 'f' * 64
            self.path.write_bytes(canonical(value))
            self.assertIsNone(self.cache.get(self.key, self.validate), field)
        value = strict_json(valid)
        value['report']['sources'][0]['source_sha256'] = 'f' * 64
        self.path.write_bytes(canonical(value))
        self.assertIsNone(self.cache.get(self.key, self.validate))
        value = strict_json(valid)
        value['report']['raw'] = 'CANARY'
        self.path.write_bytes(canonical(value))
        self.assertIsNone(self.cache.get(self.key, self.validate))

    def test_expiration_future_and_collision_never_overwrite_or_prune(self):
        self.put()
        before = self.path.read_bytes()
        created = strict_json(before)['created']
        for now in (created - 1, created + report_cache.TTL_SECONDS + 1):
            with patch.object(report_cache.time, 'time', return_value=now):
                self.assertIsNone(self.cache.get(self.key, self.validate))
        other = f'{(int(self.key, 16) + report_cache.SLOTS) % (1 << 256):064x}'
        self.assertIsNone(self.cache.get(other, self.validate))
        with patch('os.unlink', side_effect=AssertionError('no deletion')), \
                patch('os.scandir', side_effect=AssertionError('no scan')):
            self.assertFalse(self.cache.put(other, self.report, self.validate))
            self.assertEqual(self.cache.get(self.key, self.validate), self.report)
        self.assertEqual(self.path.read_bytes(), before)

    def test_file_modes_owner_links_and_special_files(self):
        self.put()
        self.path.chmod(0o644)
        self.assertIsNone(self.cache.get(self.key, self.validate))
        self.path.chmod(0o600)
        with patch.object(report_cache.os, 'geteuid', return_value=os.geteuid() + 1):
            self.assertIsNone(self.cache.get(self.key, self.validate))
        other = self.root / 'other'
        os.link(self.path, other)
        self.assertIsNone(self.cache.get(self.key, self.validate))
        self.path.unlink()
        self.path.symlink_to(other)
        self.assertIsNone(self.cache.get(self.key, self.validate))
        self.assertFalse(self.cache.put(self.key, self.report, self.validate))
        self.path.unlink()
        os.mkfifo(self.path)
        self.assertIsNone(self.cache.get(self.key, self.validate))
        self.path.unlink()
        self.path.mkdir()
        self.assertIsNone(self.cache.get(self.key, self.validate))

    def test_candidate_tooling_overlap_and_unsafe_directory(self):
        for directory in (self.candidate, self.trusted, self.root):
            with self.assertRaises(ValueError):
                report_cache.PrivateCache(str(directory), str(self.candidate), str(self.trusted))
        self.directory.chmod(0o755)
        self.assertIsNone(self.cache.get(self.key, self.validate))
        self.assertFalse(self.cache.put(self.key, self.report, self.validate))
        self.directory.chmod(0o700)
        link = self.root / 'link'
        link.symlink_to(self.directory)
        with self.assertRaises(ValueError):
            report_cache.PrivateCache(str(link), str(self.candidate), str(self.trusted))
        self.root.chmod(0o777)
        with self.assertRaises(ValueError):
            report_cache.PrivateCache(str(self.directory), str(self.candidate), str(self.trusted))
        self.root.chmod(0o700)

    def test_parent_replacement_and_symlink_race(self):
        self.put()
        self.directory.rename(self.root / 'old')
        self.directory.symlink_to(self.root / 'old')
        self.assertIsNone(self.cache.get(self.key, self.validate))
        self.assertFalse(self.cache.put(self.key, self.report, self.validate))
        self.assertTrue((self.root / 'old' / self.path.name).exists())

    def test_file_replacement_between_stat_and_open_and_during_read(self):
        self.put()
        original_open = os.open
        victim = self.root / 'victim'
        victim.write_bytes(b'DO_NOT_TOUCH')
        def replace(name, flags, *args, **kwargs):
            if name == self.path.name:
                self.path.unlink()
                self.path.symlink_to(victim)
            return original_open(name, flags, *args, **kwargs)
        with patch.object(report_cache.os, 'open', side_effect=replace):
            self.assertIsNone(self.cache.get(self.key, self.validate))
        self.assertEqual(victim.read_bytes(), b'DO_NOT_TOUCH')
        self.path.unlink()
        self.put()
        original_read = os.read
        changed = False
        def change(descriptor, amount):
            nonlocal changed
            data = original_read(descriptor, amount)
            if not changed:
                changed = True
                self.path.write_bytes(b'changed')
            return data
        with patch.object(report_cache.os, 'read', side_effect=change):
            self.assertIsNone(self.cache.get(self.key, self.validate))

    def test_permission_io_write_races_and_storage_bounds(self):
        with patch.object(report_cache.os, 'write', side_effect=PermissionError('CANARY')):
            self.assertFalse(self.cache.put(self.key, self.report, self.validate))
        self.assertIsNone(self.cache.get(self.key, self.validate))
        self.assertTrue(self.path.exists())  # Never deletes a partial slot.
        self.path.unlink()
        with patch.object(report_cache.os, 'write', return_value=0):
            self.assertFalse(self.cache.put(self.key, self.report, self.validate))
        self.path.unlink()
        original_write = os.write
        def replace(descriptor, raw):
            self.path.unlink()
            self.path.write_bytes(b'replacement')
            return original_write(descriptor, raw)
        with patch.object(report_cache.os, 'write', side_effect=replace):
            self.assertFalse(self.cache.put(self.key, self.report, self.validate))
        self.assertEqual(self.path.read_bytes(), b'replacement')
        self.path.unlink()
        for index in range(report_cache.SLOTS * 2):
            self.cache.put(f'{index:064x}', self.report, self.validate)
        self.assertEqual(len(list(self.directory.iterdir())), report_cache.SLOTS)
        self.assertLessEqual(sum(path.stat().st_size for path in self.directory.iterdir()),
                             report_cache.SLOTS * report_cache.MAX_BYTES)


class PreviewTests(unittest.TestCase):
    setUp = CacheTests.setUp

    def runtime(self, wire, image):
        name, entries = ingest.decode(wire)
        report = checks.review(name, entries, launch.trusted_catalog_data())
        report['provenance']['image_id'] = image
        return report, (name, entries)

    def preview(self):
        return launch.preview(str(self.candidate), IMAGE, str(self.directory))

    def test_preview_recollects_and_does_not_require_runtime_on_hit(self):
        with patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=self.runtime) as runtime:
            first, state = self.preview()
            self.assertEqual(state, 'miss')
            second, state = self.preview()
            self.assertEqual(state, 'hit')
        self.assertEqual(first, second)
        self.assertEqual(runtime.call_count, 1)
        with patch.object(launch, 'collect', wraps=launch.collect) as collect, \
                patch.object(launch, 'local_docker', side_effect=AssertionError('not invoked')):
            self.assertEqual(self.preview()[1], 'hit')
        collect.assert_called_once()
        (self.candidate / 'SKILL.md').write_bytes(VALID + b'\nChanged bytes')
        with patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=self.runtime) as runtime:
            changed, state = self.preview()
        self.assertEqual(state, 'miss')
        runtime.assert_called_once()
        self.assertNotEqual(first['provenance']['snapshot_sha256'], changed['provenance']['snapshot_sha256'])

    def test_unsafe_recollection_blocks_hit_and_corrupt_falls_back(self):
        with patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=self.runtime):
            self.preview()
        slot = next(self.directory.iterdir())
        slot.write_bytes(b'corrupt')
        with patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=self.runtime) as runtime:
            report, state = self.preview()
        self.assertEqual(state, 'miss')
        self.assertEqual(report['status'], 'passed')
        runtime.assert_called_once()
        self.assertEqual(slot.read_bytes(), b'corrupt')
        (self.candidate / 'bad').symlink_to(self.root / 'missing')
        with patch.object(launch, '_run_review') as runtime:
            report, _ = self.preview()
        self.assertEqual(report['status'], 'blocked')
        runtime.assert_not_called()

    def test_cache_permission_fallback_and_tooling_race(self):
        self.directory.chmod(0o755)
        with patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=self.runtime):
            report, state = self.preview()
        self.assertEqual((report['status'], state), ('passed', 'bypassed'))
        self.directory.chmod(0o700)
        with patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=self.runtime):
            self.preview()
        context = launch.cache_context('example', ENTRIES, IMAGE)
        with patch.object(launch, 'cache_context', side_effect=[context, context | {'tooling': 'f' * 64}, context]), \
                patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=self.runtime) as runtime:
            self.preview()
        runtime.assert_called_once()

    def test_cli_never_returns_admission_success_or_publishes_cache_report(self):
        args = ['run', str(self.candidate), '--image', IMAGE, '--preview-cache', str(self.directory)]
        with patch.object(sys, 'argv', args), patch.object(launch, 'preview', return_value=(self.report, 'hit')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(launch.main(), 3)
        self.assertIn('NON-AUTHORITATIVE', output.getvalue())
        with patch.object(sys, 'argv', args + ['--output', str(self.directory / 'report.json')]), \
                patch.object(launch, 'preview') as preview, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launch.main(), 3)
        preview.assert_not_called()
        self.assertFalse((self.directory / 'report.json').exists())

    def test_authoritative_api_never_uses_cache_and_same_user_limit(self):
        # A same-owner structurally valid forged report can pass cache validation.
        # The mitigation is separation, NOT a claim that hashes prove authenticity.
        forged = copy.deepcopy(self.report)
        forged['findings'] = []
        context = launch.cache_context('example', ENTRIES, IMAGE)
        key = report_cache.key_for(context)
        validate = lambda report: launch.validate_snapshot_report(report, 'example', ENTRIES, IMAGE)
        self.assertTrue(self.cache.put(key, forged, validate))
        with patch.object(launch, 'local_docker', side_effect=AssertionError('hit')):
            self.assertEqual(self.preview(), (forged, 'hit'))
        with patch.object(report_cache.PrivateCache, 'get', side_effect=AssertionError('no admission cache')), \
                patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=self.runtime) as runtime:
            report = launch.review(str(self.candidate), IMAGE)
        runtime.assert_called_once()
        self.assertTrue(report['findings'])
        self.assertTrue(model_contract.make_packet('example', ENTRIES, enabled=True))


class SelectionTests(unittest.TestCase):
    def test_proposed_scope_exact_revisions_and_all_tooling_inputs(self):
        root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root / '.github/scripts'))
        import test_reviewer_isolated as isolated
        from collect_snapshot import REPOSITORY_INPUTS
        import test_ci
        fixture = test_ci.GitSelectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        unrelated = fixture.root / 'skills/other/SKILL.md'
        unrelated.parent.mkdir(parents=True)
        unrelated.write_bytes(VALID)
        head = fixture.commit()
        self.assertFalse(isolated.tooling_changed(str(fixture.root), fixture.base, head))
        for index, path in enumerate(REPOSITORY_INPUTS):
            with self.subTest(path=path):
                if path == 'skills/review-skill-sflabs':
                    path += '/scripts/checks.py'
                target = fixture.root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('synthetic tooling fixture ' + str(index))
                base, head = head, fixture.commit()
                self.assertTrue(isolated.tooling_changed(str(fixture.root), base, head))
                target.unlink()
                base, head = head, fixture.commit()
                self.assertTrue(isolated.tooling_changed(str(fixture.root), base, head))
        with patch('git_context.git') as git, self.assertRaises(ValueError):
            isolated.tooling_changed(str(fixture.root), '--bad', head)
        git.assert_not_called()

    def test_unselected_tests_explicit_not_verified_and_selection_failure_closed(self):
        root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root / '.github/scripts'))
        import test_reviewer_isolated as isolated
        args = ['tests', '--candidate-root', '/synthetic', '--image', IMAGE,
                '--base', 'a' * 40, '--head', 'b' * 40]
        with patch.object(sys, 'argv', args), patch.object(isolated, 'tooling_changed', return_value=False), \
                patch.object(isolated, 'run_proposed') as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(isolated.main(), 0)
        run.assert_not_called()
        self.assertIn('not selected', output.getvalue())
        with patch.object(sys, 'argv', args), patch.object(isolated, 'tooling_changed', side_effect=ValueError('CANARY')), \
                patch.object(isolated, 'run_proposed') as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(isolated.main(), 3)
        run.assert_not_called()
        self.assertNotIn('CANARY', output.getvalue())


if __name__ == '__main__':
    unittest.main()

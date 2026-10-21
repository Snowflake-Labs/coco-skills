"""Synthetic report/output boundary fixtures; no Docker, network or inference."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import launch
import private_output
from checks import review
from ingest import Entry, collect, encode, manifest
from license_fixtures import APACHE
from model_contract import ContractError, make_packet, validate_response
from policy import METADATA
from private_output import Destination, OutputError, locator_payload, passive_name, write_outputs
from protocol import (ACTIONS, MESSAGES, VERSION, catalog_identity, error_report,
                      exit_code, finding, render, strict_json, validate_report)

IMAGE = 'sha256:' + 'a' * 64
SKILL = b'---\nname: example\ndescription: "Use when checking. Triggers: check"\n---\nAdvisory report.\n'
ENTRIES = (Entry('LICENSE', APACHE), Entry('SKILL.md', SKILL))
CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'references/catalog-snapshot.json').read_text())


class ReportTests(unittest.TestCase):
    def test_fields_remain_distinct_and_actionable(self):
        report = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', b'---\n{}\n---\n')))
        required = [item for item in report['findings'] if item['code'] == 'format.required']
        self.assertEqual({item['field'] for item in required}, {'name', 'description'})
        self.assertEqual(len({item['action'] for item in required}), 2)
        report = review('example', ENTRIES)
        recommended = [item for item in report['findings'] if item['code'] == 'format.recommended']
        self.assertEqual({item['field'] for item in recommended}, {'id', 'type', 'status', 'categories', 'authors'})
        self.assertEqual(len(recommended), 5)
        validate_report(report)

    def test_all_field_types_and_lengths_identified(self):
        for field in METADATA:
            with self.subTest(field=field):
                # String replacement avoids creating duplicate YAML keys.
                text = SKILL.decode().replace('name: example\n', '').replace('description: "Use when checking. Triggers: check"\n', '')
                text = text.replace('---\n', '---\n' + field + ': {}\n', 1)
                report = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', text.encode())))
                self.assertIn(field, [item['field'] for item in report['findings'] if item['code'] == 'format.field_type'])
                validate_report(report)
        text = SKILL.replace(b'name: example', b'name: example\ntitle: ' + b'x' * 31 + b'\nsummary: ' + b'x' * 141)
        report = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', text)))
        self.assertEqual({item['field'] for item in report['findings'] if item['code'] == 'format.length'}, {'title', 'summary'})

    def test_every_code_has_fixed_next_action(self):
        self.assertEqual(set(ACTIONS), set(MESSAGES))
        self.assertTrue(all(ACTIONS.values()))

    def test_grouping_and_status_cannot_be_forged(self):
        entries = ENTRIES + (Entry('x.zip', b'PK'), Entry('secret.txt', b'password="synthetic-secret-canary"'))
        report = review('example', entries)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual({item['group'] for item in report['findings']}, {'required', 'advisory', 'coverage'})
        rendered = render(report)
        for label in ('Required corrections:', 'Advisory human decisions:', 'Coverage limitations:'):
            self.assertIn(label, rendered)
        report['status'] = 'passed'
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_coverage_requires_unique_source_bound_unsupported_findings(self):
        original = review('example', ENTRIES + (Entry('x.zip', b'PK'), Entry('y.zip', b'PK')))
        unsupported = [item for item in original['findings'] if item['code'] == 'coverage.unsupported']
        self.assertEqual(len(unsupported), 2)
        mutations = []
        missing = copy.deepcopy(original)
        missing['findings'].remove(unsupported[0])
        mutations.append(missing)
        duplicate = copy.deepcopy(original)
        duplicate['findings'].remove(unsupported[1])
        duplicate['findings'].append(copy.deepcopy(unsupported[0]))
        mutations.append(duplicate)
        anonymous = copy.deepcopy(original)
        anonymous['findings'].remove(unsupported[0])
        anonymous['findings'].append(finding('coverage.unsupported', 'error'))
        mutations.append(anonymous)
        with_line = copy.deepcopy(original)
        next(item for item in with_line['findings'] if item['code'] == 'coverage.unsupported')['line'] = 1
        mutations.append(with_line)
        full = copy.deepcopy(original)
        full['coverage']['text_files'] = full['coverage']['files']
        mutations.append(full)
        for report in mutations:
            with self.subTest(report=report), self.assertRaises(ValueError):
                validate_report(report)

    def test_legitimate_coverage_and_error_statuses_remain_valid(self):
        for entries, status in [(ENTRIES, 'passed'),
                (ENTRIES + (Entry('x.zip', b'PK'),), 'incomplete'),
                ((Entry('x.zip', b'PK'),), 'failed'), ((), 'failed')]:
            report = review('example', entries)
            self.assertEqual(report['status'], status)
            validate_report(report)
        for code, status in [('input.unsafe', 'blocked'), ('input.timeout', 'blocked'),
                ('runtime.unavailable', 'unavailable'), ('runtime.invalid_output', 'unavailable')]:
            validate_report(error_report(code, status))

    def test_snapshot_binds_name_path_content_and_is_order_independent(self):
        original = manifest(ENTRIES, 'example')[1]
        self.assertEqual(original, manifest(tuple(reversed(ENTRIES)), 'example')[1])
        for name, entries in [('other', ENTRIES), ('example', ENTRIES + (Entry('a.txt', b'x'),)),
                              ('example', (Entry('LICENSE', APACHE + b' '), ENTRIES[1])),
                              ('example', (Entry('OTHER', APACHE), ENTRIES[1]))]:
            self.assertNotEqual(original, manifest(entries, name)[1])
        self.assertEqual(review('example', ENTRIES), review('example', tuple(reversed(ENTRIES))))

    def test_stable_source_survives_ordinal_and_line_shifts(self):
        original = review('example', ENTRIES + (Entry('notes.txt', b'urgent'),))
        shifted = review('example', (Entry('000.txt', b'x'),) + ENTRIES + (Entry('notes.txt', b'\n\nurgent'),))
        before = next(item for item in original['findings'] if item['source_sha256'] == hashlib.sha256(b'notes.txt').hexdigest())
        after = next(item for item in shifted['findings'] if item['source_sha256'] == hashlib.sha256(b'notes.txt').hexdigest())
        self.assertNotEqual(before['file_id'], after['file_id'])
        self.assertNotEqual(before['line'], after['line'])
        self.assertEqual(before['source_sha256'], after['source_sha256'])
        self.assertEqual(before['source_sha256'], hashlib.sha256(b'notes.txt').hexdigest())
        self.assertNotEqual(original['provenance']['snapshot_sha256'], shifted['provenance']['snapshot_sha256'])

    def test_canaries_paths_values_controls_and_links_not_in_safe_reports(self):
        marker = 'SYNTHETIC_CANARY'
        entries = ENTRIES + (Entry(marker + '\u202e.txt', ('password="' + marker + '_secret"\n\x1b]8;;https://example.invalid\x07').encode()),)
        report = review(marker, entries)
        for rendered in (render(report), render(report, 'json')):
            for unsafe in (marker, 'https://', '\x1b', '\u202e', 'example.invalid'):
                self.assertNotIn(unsafe, rendered)

    def test_malformed_nested_fields_and_bounded_ids(self):
        original = review('example', ENTRIES, CATALOG)
        mutations = [(['schema_version'], '1.0'), (['rules_version'], '1.0'),
            (['sources', 0, 'file_id'], 'F9999'), (['sources', 0, 'source_sha256'], 'CANARY'),
            (['coverage', 'files'], True), (['coverage', 'text_files'], 513),
            (['provenance', 'snapshot_sha256'], 'a' * 65), (['provenance', 'validator'], 'CANARY'),
            (['provenance', 'image_id'], 'latest'), (['provenance', 'catalog', 'revision'], 'CANARY'),
            (['provenance', 'base_revision'], 'a' * 40),
            (['findings', 0, 'field'], 'CANARY'), (['findings', 0, 'action'], 'CANARY'),
            (['findings', 0, 'file_id'], 'F0000'), (['findings', 0, 'source_sha256'], 'b' * 64),
            (['findings', 0, 'line'], True), (['findings', 0, 'line'], 262146),
            (['findings', 0, 'severity'], 'error'), (['findings', 0, 'group'], 'required'),
            (['findings', 0, 'code'], []), (['findings', 0, 'field'], {}),
            (['catalog_revision'], 'c' * 40), (['sources'], []), (['provenance'], None)]
        for path, value in mutations:
            with self.subTest(path=path):
                report = copy.deepcopy(original)
                target = report
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ValueError):
                    validate_report(report)
        for path in ([], ['provenance'], ['provenance', 'catalog'], ['sources', 0], ['findings', 0]):
            report = copy.deepcopy(original)
            target = report
            for key in path:
                target = target[key]
            target['unexpected'] = 'CANARY'
            with self.assertRaises(ValueError):
                validate_report(report)

    def test_failures_explicitly_lack_provenance(self):
        for code, status in [('input.unsafe', 'blocked'), ('input.timeout', 'blocked'),
                             ('runtime.unavailable', 'unavailable'), ('runtime.invalid_output', 'unavailable')]:
            report = error_report(code, status)
            validate_report(report)
            self.assertIsNone(report['provenance'])
            self.assertIn('Provenance: unavailable', render(report))
            report['provenance'] = review('example', ENTRIES)['provenance']
            with self.assertRaises(ValueError):
                validate_report(report)
        self.assertIsNotNone(review('example', ())['provenance'])

    def test_catalog_full_digest_and_revision(self):
        original = catalog_identity(CATALOG)
        for field, value in [('scope', 'different'), ('entries', []), ('revision', 'b' * 40), ('extra', 'data')]:
            self.assertNotEqual(original['sha256'], catalog_identity(CATALOG | {field: value})['sha256'])
        self.assertEqual(original, catalog_identity(dict(reversed(list(CATALOG.items())))))

    def test_duplicate_keys_and_nonfinite_json_rejected(self):
        for raw in (b'{"status":"passed","status":"failed"}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.assertRaises(ValueError):
                strict_json(raw)

    def test_offline_packet_contract_binds_same_snapshot_and_identity(self):
        packet = make_packet('example', tuple(reversed(ENTRIES)), CATALOG, enabled=True)
        report = review('example', ENTRIES, CATALOG)
        self.assertEqual(packet['schema_version'], VERSION)
        self.assertEqual(packet['snapshot_sha256'], report['provenance']['snapshot_sha256'])
        self.assertEqual(packet['provenance'], report['provenance'])
        self.assertEqual([item['source_sha256'] for item in packet['files']],
                         [item['source_sha256'] for item in report['sources']])
        other = make_packet('other', ENTRIES, CATALOG, enabled=True)
        self.assertNotEqual(packet['snapshot_sha256'], other['snapshot_sha256'])
        for version in ('1.0', 'future'):
            raw = json.dumps({'schema_version': version, 'packet_sha256': packet['packet_sha256'], 'findings': []}).encode()
            with self.assertRaises(ContractError):
                validate_response(raw, packet)


class HostProvenanceTests(unittest.TestCase):
    def run_host(self, report=None, wire=None, code=None, candidate='/example', **kwargs):
        report = report if report is not None else review('example', ENTRIES, CATALOG)
        def fake_run(command, **options):
            if command[1] == 'run':
                options['stdout'].write(json.dumps(report).encode())
                return subprocess.CompletedProcess(command, exit_code(report) if code is None else code)
            return subprocess.CompletedProcess(command, 0)
        with patch('launch.local_docker'), patch('launch.collect', return_value=wire or encode(ENTRIES, 'example')), \
                patch('launch.subprocess.run', side_effect=fake_run):
            return launch.review(candidate, IMAGE, **kwargs)

    def test_image_set_by_host_and_ci_context_bound(self):
        report = self.run_host(base_revision='a' * 40, head_revision='b' * 40)
        self.assertEqual(report['provenance']['image_id'], IMAGE)
        self.assertEqual(report['provenance']['base_revision'], 'a' * 40)
        self.assertEqual(report['provenance']['head_revision'], 'b' * 40)
        validate_report(report)

    def test_provenance_tampering_fails_closed(self):
        for path, value in [(['snapshot_sha256'], 'b' * 64), (['image_id'], IMAGE),
                            (['catalog', 'sha256'], 'b' * 64), (['catalog', 'revision'], 'b' * 40),
                            (['base_revision'], 'b' * 40)]:
            report = review('example', ENTRIES, CATALOG)
            target = report['provenance']
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            rejected = self.run_host(report)
            self.assertEqual(rejected['findings'][0]['code'], 'runtime.invalid_output')
            self.assertIsNone(rejected['provenance'])

    def test_correctly_bound_partial_coverage_fails_closed(self):
        for entries in (ENTRIES, ENTRIES + (Entry('x.zip', b'PK'),)):
            report = review('example', entries, CATALOG)
            if report['coverage']['text_files'] == report['coverage']['files']:
                report['coverage']['text_files'] -= 1
            report['findings'] = [item for item in report['findings'] if item['code'] != 'coverage.unsupported']
            report['status'] = 'passed'
            digest, sources = launch.snapshot_identity('example', entries)
            self.assertEqual(report['provenance']['snapshot_sha256'], digest)
            self.assertEqual(report['sources'], sources)
            self.assertEqual(report['provenance']['catalog'], launch.trusted_catalog())
            with self.assertRaises(ValueError):
                launch.validate_snapshot_report(report, 'example', entries)
            rejected = self.run_host(report, wire=encode(entries, 'example'), code=0)
            self.assertEqual(rejected, error_report('runtime.invalid_output', 'unavailable'))
            self.assertEqual(exit_code(rejected), 3)

    def test_same_directory_bytes_different_name_rejected(self):
        self.assertEqual(self.run_host(wire=encode(ENTRIES, 'different'))['status'], 'unavailable')

    def test_forged_source_mapping_and_wrong_return_code_rejected(self):
        report = review('example', ENTRIES, CATALOG)
        report['sources'][0]['source_sha256'] = 'c' * 64
        self.assertEqual(self.run_host(report)['status'], 'unavailable')
        self.assertEqual(self.run_host(code=1)['status'], 'unavailable')

    def test_line_claim_outside_actual_file_rejected(self):
        report = review('example', ENTRIES, CATALOG)
        report['findings'][0]['line'] = 10000
        self.assertEqual(self.run_host(report)['findings'][0]['code'], 'runtime.invalid_output')

    def test_duplicate_json_and_oversized_runtime_output(self):
        for data in (b'{"status":"passed","status":"failed"}', b'x' * (launch.MAX_OUTPUT + 1)):
            def fake_run(command, **options):
                if command[1] == 'run':
                    options['stdout'].write(data)
                return subprocess.CompletedProcess(command, 0)
            with patch('launch.local_docker'), patch('launch.collect', return_value=encode(ENTRIES, 'example')), \
                    patch('launch.subprocess.run', side_effect=fake_run):
                result = launch.review('/example', IMAGE)
            self.assertEqual(result['status'], 'unavailable')
            self.assertIsNone(result['provenance'])

    def test_shortlist_outside_trusted_catalog_rejected(self):
        from catalog import QUESTION
        report = review('example', ENTRIES, CATALOG)
        report['related'] = [{'catalog_id': 'b' * 64, 'question': QUESTION}]
        self.assertEqual(self.run_host(report)['findings'][0]['code'], 'runtime.invalid_output')

    def test_missing_or_modified_host_catalog_no_verified_identity(self):
        with patch('launch.trusted_catalog', side_effect=OSError('CANARY')):
            self.assertIsNone(self.run_host()['provenance'])
        with patch('launch.trusted_catalog', return_value=catalog_identity(CATALOG | {'scope': 'modified'})):
            self.assertIsNone(self.run_host()['provenance'])

    def test_runtime_failure_no_locator(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / 'locator.json'
            (target.parent / 'candidate').mkdir()
            report = self.run_host(error_report('input.unsafe', 'blocked'), candidate=str(target.parent / 'candidate'), private_locator=str(target))
            self.assertIsNone(report['provenance'])
            self.assertFalse(target.exists())


class PrivateOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.candidate = self.root / 'example'
        self.candidate.mkdir(mode=0o700)
        for entry in ENTRIES:
            (self.candidate / entry.path).write_bytes(entry.data)
        self.private = self.root / 'private'
        self.private.mkdir(mode=0o700)

    def destination(self, path):
        destination = Destination(str(path), str(self.candidate), {'.json'})
        self.addCleanup(destination.close)
        return destination

    def test_exclusive_private_mode_and_no_automatic_locator(self):
        destination = self.destination(self.private / 'mapping.json')
        write_outputs([(destination, locator_payload('example', ENTRIES))])
        self.assertEqual(stat.S_IMODE((self.private / 'mapping.json').stat().st_mode), 0o600)
        payload = json.loads((self.private / 'mapping.json').read_text())
        self.assertEqual(payload['classification'], 'PRIVATE')
        self.assertEqual(payload['snapshot_sha256'], manifest(ENTRIES, 'example')[1])
        self.assertEqual(payload['files'][1]['display_name'], 'SKILL[U+002E]md')
        with patch('launch.local_docker', side_effect=OSError()), patch('launch.locator_payload') as locator:
            launch.review(str(self.candidate), IMAGE)
            locator.assert_not_called()

    def test_same_captured_snapshot_despite_candidate_changes(self):
        real_collect = launch.collect
        captured = []
        def capture(path):
            wire = real_collect(path)
            captured.append(wire)
            (self.candidate / 'SKILL.md').rename(self.candidate / 'CHANGED.md')
            (self.candidate / '000.txt').write_text('later')
            return wire
        def fake_run(command, **options):
            if command[1] == 'run':
                self.assertEqual(options['input'], captured[0])
                options['stdout'].write(render(review('example', ENTRIES, CATALOG), 'json').encode())
            return subprocess.CompletedProcess(command, 0)
        target = self.private / 'mapping.json'
        safe = self.private / 'report.json'
        with patch('launch.local_docker'), patch('launch.collect', side_effect=capture) as collector:
            # The collector subprocess remains real; only Docker execution is doubled.
            real_run = subprocess.run
            def route(command, **options):
                return fake_run(command, **options) if command[0] == 'docker' else real_run(command, **options)
            with patch('launch.subprocess.run', side_effect=route):
                report = launch.review(str(self.candidate), IMAGE, private_locator=str(target),
                                       output=str(safe), output_format='json')
        collector.assert_called_once()
        payload = json.loads(target.read_text())
        self.assertEqual(payload['snapshot_sha256'], report['provenance']['snapshot_sha256'])
        self.assertEqual([item['display_name'] for item in payload['files']], ['LICENSE', 'SKILL[U+002E]md'])
        self.assertNotIn('CHANGED', target.read_text())
        self.assertNotIn('display_name', render(report, 'json'))
        self.assertEqual(json.loads(safe.read_text()), report)

    def test_sanitized_names_passive_bounded_and_disambiguated(self):
        for name in ('\x1b]8;;https://example.invalid\x07click\x1b]8;;\x07', '\u202ebidi\u2066',
                     '<script>alert(1)</script>', '[click](https://example.invalid)',
                     'file:///secret', 'www.example.invalid', 'name@example.invalid',
                     '\x00\n\r\x7f\x9b', 'x' * 4096):
            value = passive_name(name)
            self.assertLessEqual(len(value), 251)
            self.assertTrue(value.isascii())
            for prohibited in ('\x1b', '\n', '\r', '\x00', '\x7f', '<', '>', ':', '.', '@', '\u202e'):
                self.assertNotIn(prohibited, value)
        entries = (Entry('x' * 300 + 'a.txt', b'a'), Entry('x' * 300 + 'b.txt', b'b'))
        files = json.loads(locator_payload('example', entries))['files']
        self.assertEqual(files[0]['display_name'], files[1]['display_name'])
        self.assertNotEqual(files[0]['source_sha256'], files[1]['source_sha256'])

    def test_prohibited_destinations_rejected_before_collection(self):
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        nested = self.candidate / 'nested'
        nested.mkdir(mode=0o700)
        paths = [self.candidate / 'mapping.json', nested / 'mapping.json', self.candidate,
                 self.private / '../mapping.json', public / 'mapping.json', self.private / 'mapping.html',
                 self.private / 'bad\nname.json', self.private / 'bad\u202ename.json',
                 self.private / ('x' * 100 + '.json'), self.root / 'missing/mapping.json',
                 Path('relative.json'), Path('/dev/stdout'), self.private / '-']
        for path in paths:
            with self.subTest(path=path), patch('launch.collect') as collector, patch('launch.local_docker') as docker:
                with self.assertRaises(OutputError):
                    launch.review(str(self.candidate), IMAGE, private_locator=str(path))
                collector.assert_not_called()
                docker.assert_not_called()

    def test_existing_regular_hardlink_directory_fifo_and_symlink_rejected(self):
        target = self.private / 'mapping.json'
        for kind in ('regular', 'hardlink', 'directory', 'fifo', 'symlink', 'dangling'):
            if kind == 'regular':
                target.write_text('KEEP_CANARY')
            elif kind == 'hardlink':
                os.link(self.candidate / 'SKILL.md', target)
            elif kind == 'directory':
                target.mkdir()
            elif kind == 'fifo':
                os.mkfifo(target)
            else:
                target.symlink_to(self.candidate / ('SKILL.md' if kind == 'symlink' else 'missing'))
            with self.subTest(kind=kind), self.assertRaises(OutputError):
                self.destination(target)
            if kind == 'regular':
                self.assertEqual(target.read_text(), 'KEEP_CANARY')
            target.rmdir() if kind == 'directory' else target.unlink()

    def test_symlinked_parent_or_ancestor_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.private, target_is_directory=True)
        for path in (link / 'mapping.json', link / 'nested/mapping.json'):
            with self.assertRaises(OutputError):
                self.destination(path)

    def test_parent_mode_and_owner_checked_again_at_write(self):
        destination = self.destination(self.private / 'mapping.json')
        self.private.chmod(0o755)
        with self.assertRaises(OutputError):
            write_outputs([(destination, b'PRIVATE')])
        self.assertFalse((self.private / 'mapping.json').exists())
        self.private.chmod(0o700)
        with patch('private_output.os.geteuid', return_value=os.geteuid() + 1):
            with self.assertRaises(OutputError):
                self.destination(self.private / 'mapping.json')

    def test_parent_replaced_by_symlink_between_check_and_create(self):
        destination = self.destination(self.private / 'mapping.json')
        real_open = os.open
        moved = self.root / 'moved'
        def race(path, flags, *args, **kwargs):
            if path == 'mapping.json':
                self.private.rename(moved)
                self.private.symlink_to(self.candidate, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)
        with patch('private_output.os.open', side_effect=race), self.assertRaises(OutputError):
            write_outputs([(destination, b'PRIVATE')])
        self.assertFalse((self.candidate / 'mapping.json').exists())
        self.assertFalse((moved / 'mapping.json').exists())

    def test_regular_directory_replacement_is_rejected(self):
        destination = self.destination(self.private / 'mapping.json')
        self.private.rename(self.root / 'moved')
        self.private.mkdir(mode=0o700)
        with self.assertRaises(OutputError):
            write_outputs([(destination, b'PRIVATE')])
        self.assertFalse((self.private / 'mapping.json').exists())

    def test_file_created_between_preflight_and_write_is_not_overwritten(self):
        destination = self.destination(self.private / 'mapping.json')
        (self.private / 'mapping.json').write_text('KEEP_CANARY')
        with self.assertRaises(OutputError):
            write_outputs([(destination, b'PRIVATE')])
        self.assertEqual((self.private / 'mapping.json').read_text(), 'KEEP_CANARY')

    def test_symlink_race_does_not_overwrite_target(self):
        destination = self.destination(self.private / 'mapping.json')
        (self.private / 'mapping.json').symlink_to(self.candidate / 'SKILL.md')
        with self.assertRaises(OutputError):
            write_outputs([(destination, b'PRIVATE')])
        self.assertEqual((self.candidate / 'SKILL.md').read_bytes(), SKILL)

    def test_write_failure_removes_only_new_files(self):
        first = self.destination(self.private / 'first.json')
        second = self.destination(self.private / 'second.json')
        with patch('private_output.os.write', side_effect=OSError('CANARY')), self.assertRaises(OutputError):
            write_outputs([(first, b'safe'), (second, b'PRIVATE')])
        self.assertEqual(list(self.private.iterdir()), [])

    def test_ascii_case_alias_inside_candidate_rejected(self):
        # On case-sensitive hosts exercise the equivalent alias using the pinned
        # directory FD, without weakening the production no-follow open.
        nested = self.candidate / 'nested'
        nested.mkdir(mode=0o700)
        original = private_output.open_root
        def alias(path):
            return original(str(nested) if path == str(self.root / 'EXAMPLE/nested') else path)
        with patch('private_output.open_root', side_effect=alias), self.assertRaises(OutputError):
            self.destination(self.root / 'EXAMPLE/nested/mapping.json')

    def test_file_replaced_after_create_preserves_replacement(self):
        destination = self.destination(self.private / 'mapping.json')
        original = destination.write
        def replace(data):
            (self.private / 'mapping.json').unlink()
            (self.private / 'mapping.json').write_text('KEEP_REPLACEMENT')
            original(data)
        with patch.object(destination, 'write', side_effect=replace), self.assertRaises(OutputError):
            write_outputs([(destination, b'PRIVATE')])
        self.assertEqual((self.private / 'mapping.json').read_text(), 'KEEP_REPLACEMENT')

    def test_second_destination_failure_rolls_back_first(self):
        first = self.destination(self.private / 'first.json')
        second = self.destination(self.private / 'second.json')
        (self.private / 'second.json').write_text('KEEP_EXISTING')
        with self.assertRaises(OutputError):
            write_outputs([(first, b'safe'), (second, b'PRIVATE')])
        self.assertFalse((self.private / 'first.json').exists())
        self.assertEqual((self.private / 'second.json').read_text(), 'KEEP_EXISTING')

    def test_short_writes_complete_and_zero_write_rolls_back(self):
        destination = self.destination(self.private / 'short.json')
        real_write = os.write
        with patch('private_output.os.write', side_effect=lambda fd, data: real_write(fd, data[:2])):
            write_outputs([(destination, b'PRIVATE')])
        self.assertEqual((self.private / 'short.json').read_bytes(), b'PRIVATE')
        zero = self.destination(self.private / 'zero.json')
        with patch('private_output.os.write', return_value=0), self.assertRaises(OutputError):
            write_outputs([(zero, b'PRIVATE')])
        self.assertFalse((self.private / 'zero.json').exists())

    def test_safe_output_protections_and_ancestor_overlap(self):
        for path in (self.candidate / 'report.json', self.private / 'report.html'):
            with self.assertRaises(OutputError):
                launch.review(str(self.candidate), IMAGE, output=str(path))
        with self.assertRaises(OutputError):
            Destination(str(self.root), str(self.candidate), {'.json'})

    def test_duplicate_safe_and_private_destination_rejected(self):
        target = str(self.private / 'same.json')
        with patch('launch.collect') as collector, self.assertRaises(OutputError):
            launch.review(str(self.candidate), IMAGE, private_locator=target, output=target)
        collector.assert_not_called()
        self.assertFalse(Path(target).exists())

    def test_cli_fixed_destination_error_no_path_or_canary(self):
        target = str(self.private / 'CANARY\x1b.json')
        with patch.object(sys, 'argv', ['run', str(self.candidate), '--image', IMAGE, '--private-locator', target]), \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(launch.main(), 3)
        self.assertEqual(output.getvalue(), 'Output destination rejected; no report was published.\n')
        self.assertEqual(error.getvalue(), '')

    def test_failed_publication_cli_hides_os_errors(self):
        with patch.object(sys, 'argv', ['run', str(self.candidate), '--image', IMAGE]), \
                patch('launch.review', side_effect=OSError('SECRET_PATH_CANARY')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(launch.main(), 3)
        self.assertNotIn('CANARY', output.getvalue())


if __name__ == '__main__':
    unittest.main()

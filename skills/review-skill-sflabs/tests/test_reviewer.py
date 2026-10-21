import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ingest
from ingest import Entry, UnsafeInput, collect, decode, encode, manifest
import launch
from checks import parse_frontmatter, review
from launch import container_command, local_docker, review as launch_review
from protocol import base_report, render, validate_report
from license_fixtures import APACHE, SNOWFLAKE

VALID = b'---\nname: example\ndescription: "Use when checking. Triggers: check"\n---\nAdvisory report.\n'


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.candidate = self.root / 'example'
        self.candidate.mkdir()
        (self.candidate / 'SKILL.md').write_bytes(VALID)
        (self.candidate / 'LICENSE').write_bytes(APACHE)

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_wire_round_trip(self):
        entries = collect(str(self.candidate))
        self.assertEqual(decode(encode(entries, 'example')), ('example', entries))
        self.assertEqual(manifest(entries, 'example'), manifest(collect(str(self.candidate)), 'example'))

    def test_symlinks_root_intermediate_file_directory(self):
        canary = self.root / 'private'
        canary.write_text('synthetic-external-canary')
        for target, directory in [(canary, False), (self.root, True)]:
            link = self.candidate / 'link'
            link.symlink_to(target, target_is_directory=directory)
            with self.assertRaises(UnsafeInput):
                collect(str(self.candidate))
            link.unlink()
        link = self.root / 'root-link'
        link.symlink_to(self.candidate, target_is_directory=True)
        with self.assertRaises(UnsafeInput):
            collect(str(link))
        parent_link = self.root / 'parent-link'
        parent_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(UnsafeInput):
            collect(str(parent_link / 'example'))

    def test_hardlink_rejected(self):
        os.link(self.candidate / 'SKILL.md', self.candidate / 'second.md')
        with self.assertRaises(UnsafeInput):
            collect(str(self.candidate))

    def test_fifo_and_socket_rejected(self):
        fifo = self.candidate / 'pipe'
        os.mkfifo(fifo)
        with self.assertRaises(UnsafeInput):
            collect(str(self.candidate))
        fifo.unlink()
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(self.candidate / 'sock'))
            with self.assertRaises(UnsafeInput):
                collect(str(self.candidate))

    def test_limits(self):
        for limit, value in [('MAX_FILES', 1), ('MAX_ENTRIES', 1),
                             ('MAX_FILE_BYTES', 4), ('MAX_TOTAL_BYTES', 4)]:
            with patch.object(ingest, limit, value), self.assertRaises(UnsafeInput):
                collect(str(self.candidate))
        (self.candidate / 'nested').mkdir()
        with patch.object(ingest, 'MAX_DEPTH', 0), self.assertRaises(UnsafeInput):
            collect(str(self.candidate))

    def test_control_filename_and_path_escape(self):
        (self.candidate / 'bad\nname').write_text('x')
        with self.assertRaises(UnsafeInput):
            collect(str(self.candidate))
        for path in [str(self.candidate / '..'), 'relative/path']:
            with self.assertRaises(UnsafeInput):
                collect(path)

    def test_replacement_between_stat_and_open(self):
        original = os.open
        changed = False

        def replace(path, flags, *args, **kwargs):
            nonlocal changed
            if path == 'SKILL.md' and not changed:
                changed = True
                (self.candidate / 'SKILL.md').unlink()
                (self.candidate / 'SKILL.md').write_text('replacement')
            return original(path, flags, *args, **kwargs)

        with patch.object(ingest.os, 'open', side_effect=replace), self.assertRaises(UnsafeInput):
            collect(str(self.candidate))

    def test_wire_rejects_unsafe_paths(self):
        for path in ['/outside', '../outside', 'a/../b', 'a//b', 'bad\nname']:
            wire = json.dumps({'name': 'example', 'files': [{'path': path, 'data': 'eA=='}]}).encode()
            with self.assertRaises((UnsafeInput, ValueError)):
                decode(wire)


class RuleTests(unittest.TestCase):
    def run_review(self, skill=VALID, extras=(), license=APACHE):
        return review('example', tuple(sorted((Entry('SKILL.md', skill), Entry('LICENSE', license)) + extras,
                                               key=lambda entry: entry.path)))

    def test_valid_is_reproducible(self):
        self.assertEqual(self.run_review()['status'], 'passed')
        self.assertEqual(self.run_review(), self.run_review())
        validate_report(self.run_review())

    def test_real_yaml_multiline_and_lists(self):
        text = '---\nname: example\ndescription: >-\n  Use when checking.\n  Triggers: check\nauthors:\n  - Example Author\ntools: [Read, Grep]\n---\n'
        self.assertEqual(parse_frontmatter(text)['authors'], ['Example Author'])
        self.assertEqual(self.run_review(text.encode())['status'], 'passed')

    def test_malformed_duplicate_alias_and_wrong_types(self):
        for text in ['---\nname: one\nname: two\n---',
                     '---\nx: &anchor [1]\ny: *anchor\n---',
                     '---\nname: [one]\ndescription: x\n---',
                     '---\nname: one\ndescription: x\ntype: {}\n---',
                     '---\n', '---\n{}\n---', '---\nname: [\n---']:
            with self.subTest(text=text):
                self.assertEqual(self.run_review(text.encode())['status'], 'failed')

    def test_missing_skill_and_license(self):
        self.assertEqual(review('example', ())['status'], 'failed')
        self.assertEqual(self.run_review(license=b'not a license')['status'], 'failed')

    def test_author_license(self):
        text = VALID.replace(b'name: example', b'name: example\nauthors: [example@snowflake.com]')
        self.assertEqual(self.run_review(text)['status'], 'failed')
        self.assertEqual(self.run_review(text, license=SNOWFLAKE)['status'], 'passed')

    def test_legacy_status_advisory(self):
        text = VALID.replace(b'name: example', b'name: example\nstatus: Published')
        result = self.run_review(text)
        self.assertEqual(result['status'], 'passed')
        self.assertIn('format.status', [item['code'] for item in result['findings']])

    def test_secrets_any_support_folder_no_leak(self):
        for folder in ['scripts', 'references', 'workflows']:
            marker = 'synthetic-marker-only'
            result = self.run_review(extras=(Entry(folder + '/sample.txt', ('password="' + marker + '"').encode()),))
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn(marker, render(result))
            self.assertNotIn(marker, render(result, 'json'))
            self.assertNotIn(folder, render(result))

    def test_unknown_formats_no_partial_pass(self):
        for entry in [Entry('archive.zip', b'PK'), Entry('bad.txt', b'\xff'), Entry('x.txt', b'\x00')]:
            self.assertEqual(self.run_review(extras=(entry,))['status'], 'incomplete')

    def test_candidate_prose_not_returned(self):
        marker = 'UNTRUSTED_SENTENCE_DO_NOT_REPEAT'
        result = self.run_review(VALID + marker.encode())
        self.assertNotIn(marker, render(result))
        self.assertNotIn(marker, render(result, 'json'))

    def test_local_reference_and_catalog_warning(self):
        result = self.run_review(VALID + b'\nRead `references/../../outside.md`')
        self.assertIn('format.reference', [item['code'] for item in result['findings']])
        result = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', VALID.replace(b'name: example', b'name: rbac'))),
                        {'entries': [{'name': 'rbac'}]})
        self.assertIn('catalog.collision', [item['code'] for item in result['findings']])


class LauncherTests(unittest.TestCase):
    def test_command_has_no_mounts_and_has_restrictions(self):
        command = container_command('sha256:' + 'a' * 64, 'test')
        for flag in ['--network=none', '--read-only', '--cap-drop=ALL',
                     '--security-opt=no-new-privileges=true', '--user=65532:65532', '--pull=never']:
            self.assertIn(flag, command)
        self.assertFalse(any('mount' in item or item == '-v' for item in command))

    def test_remote_daemon_denied(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://example.invalid:2376'}, clear=True):
            with self.assertRaises(ValueError):
                local_docker()

    def test_missing_docker_does_not_collect(self):
        with patch('launch.local_docker', side_effect=OSError()), patch('launch.collect') as collector:
            self.assertEqual(launch_review('/unused', 'sha256:' + 'a' * 64)['status'], 'unavailable')
            collector.assert_not_called()

    def test_timeout_cleans_container_and_never_falls_back(self):
        def fake_run(command, **kwargs):
            if command[1] == 'run':
                raise subprocess.TimeoutExpired(command, 60)
            return subprocess.CompletedProcess(command, 0)
        with patch('launch.local_docker'), patch('launch.collect', return_value=b'{}'), \
                patch('launch.subprocess.run', side_effect=fake_run) as run:
            result = launch_review('/example', 'sha256:' + 'a' * 64)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(run.call_args.args[0][1:3], ['rm', '-f'])

    def test_untrusted_container_output_never_echoed(self):
        marker = 'UNTRUSTED_OUTPUT_SENTENCE'
        def fake_run(command, **kwargs):
            if command[1] == 'run':
                kwargs['stdout'].write(marker.encode())
            return subprocess.CompletedProcess(command, 0)
        with patch('launch.local_docker'), patch('launch.collect', return_value=b'{}'), \
                patch('launch.subprocess.run', side_effect=fake_run):
            result = launch_review('/example', 'sha256:' + 'a' * 64)
            self.assertEqual(result['status'], 'unavailable')
            self.assertNotIn(marker, render(result))

    def test_image_tag_rejected(self):
        with self.assertRaises(ValueError):
            container_command('untrusted:latest', 'test')

    def test_unknown_report_strings_rejected(self):
        value = base_report()
        value['status'] = 'UNTRUSTED_INSTRUCTION'
        with self.assertRaises(ValueError):
            validate_report(value)
        value = base_report()
        value['extra'] = 'UNTRUSTED_INSTRUCTION'
        with self.assertRaises(ValueError):
            validate_report(value)


class CollectionDeadlineTests(unittest.TestCase):
    def test_supervised_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary).resolve() / 'example'
            candidate.mkdir()
            (candidate / 'SKILL.md').write_bytes(VALID)
            wire = launch.collect(str(candidate))
            self.assertIsInstance(wire, bytes)
            self.assertEqual(decode(wire), ('example', collect(str(candidate))))

    def test_real_stalled_child_killed_and_reaped(self):
        real_run = subprocess.run
        real_popen = subprocess.Popen
        processes = []
        streams = []

        def track(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        def stall(command, **kwargs):
            streams.append(kwargs['stdout'])
            self.assertEqual(command[1], '-I')
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            return real_run([sys.executable, '-I', '-c', 'import time; time.sleep(30)'], **kwargs)

        with patch('launch.subprocess.run', side_effect=stall), \
                patch('subprocess.Popen', side_effect=track):
            with self.assertRaises(subprocess.TimeoutExpired):
                launch.collect('/unused', timeout=0.2)
        self.assertEqual(len(processes), 1)
        self.assertLess(processes[0].returncode, 0)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(streams[0].closed)

    def test_worker_errors_and_output_are_private(self):
        marker = 'CANDIDATE_CANARY_\x1b[31m\x7f'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            link = root / marker
            link.symlink_to(root / 'missing')
            child = subprocess.run([sys.executable, '-I', str(Path(launch.__file__).with_name('collect_snapshot.py')),
                                    str(link)], capture_output=True, timeout=5)
            self.assertEqual(child.returncode, 2)
            self.assertEqual((child.stdout, child.stderr), (b'', b''))
            with self.assertRaises(UnsafeInput) as error:
                launch.collect(str(link))
            self.assertEqual(str(error.exception), '')
        for error in [UnsafeInput(marker), OSError(marker),
                      subprocess.TimeoutExpired(marker, 15, output=marker.encode())]:
            with patch('launch.local_docker'), patch('launch.collect', side_effect=error), \
                    patch('launch.subprocess.run') as run:
                report = launch_review('/unused', 'sha256:' + 'a' * 64)
                self.assertEqual(report['status'], 'blocked')
                self.assertNotIn(marker, render(report))
                self.assertNotIn(marker, render(report, 'json'))
                expected = 'input.timeout' if isinstance(error, subprocess.TimeoutExpired) else 'input.unsafe'
                self.assertEqual(report['findings'][0]['code'], expected)
                run.assert_not_called()

    def test_collection_output_limits_and_exit_code(self):
        for code, data in [(2, b'CANARY'), (0, b'x' * 9)]:
            def fake_run(command, **kwargs):
                kwargs['stdout'].write(data)
                return subprocess.CompletedProcess(command, code)
            with patch('launch.MAX_WIRE_BYTES', 8), patch('launch.subprocess.run', side_effect=fake_run):
                with self.assertRaises(UnsafeInput):
                    launch.collect('/unused')

    def test_repository_collection_uses_same_deadline(self):
        def fake_run(command, **kwargs):
            self.assertIn('--repository-tests', command)
            self.assertEqual(kwargs['timeout'], 15)
            self.assertNotIn('shell', kwargs)
            kwargs['stdout'].write(b'{}')
            return subprocess.CompletedProcess(command, 0)
        with patch('launch.subprocess.run', side_effect=fake_run):
            self.assertEqual(launch.collect('/unused', repository_tests=True), b'{}')


# The same assertion runs in the actual container and in synthetic controls.
# Read the effective namespace's interfaces; failed external probes prove nothing.
NETWORK_ISOLATION_CHECK = "import os\nassert set(os.listdir('/sys/class/net')) == {'lo'}, 'network enabled'\n"


class NetworkIsolationTests(unittest.TestCase):
    def test_loopback_only_namespace_accepted(self):
        with patch('os.listdir', return_value=['lo']) as interfaces:
            exec(NETWORK_ISOLATION_CHECK, {})
        interfaces.assert_called_once_with('/sys/class/net')

    def test_network_enabled_namespace_rejected(self):
        for names in (['lo', 'eth0'], ['eth0'], ['lo', 'wlan0'], []):
            with self.subTest(interfaces=names), patch('os.listdir', return_value=names):
                with self.assertRaises(AssertionError):
                    exec(NETWORK_ISOLATION_CHECK, {})
        with patch('os.listdir', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):
                exec(NETWORK_ISOLATION_CHECK, {})


@unittest.skipUnless(os.environ.get('REVIEWER_TEST_IMAGE'), 'Approved Docker image not provided')
class ContainerSmokeTests(unittest.TestCase):
    def test_valid_review_inside_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary).resolve() / 'example'
            candidate.mkdir()
            (candidate / 'SKILL.md').write_bytes(VALID)
            (candidate / 'LICENSE').write_bytes(APACHE)
            result = launch_review(str(candidate), os.environ['REVIEWER_TEST_IMAGE'])
            self.assertEqual(result['status'], 'passed')

    def test_runtime_write_network_and_privilege_boundaries(self):
        image = os.environ['REVIEWER_TEST_IMAGE']
        import uuid
        local_docker()
        name = 'reviewer-smoke-' + uuid.uuid4().hex
        command = container_command(image, name)
        command.remove('-i')
        command[-1:-1] = ['--entrypoint', 'python']
        code = '''import os
assert os.getuid() == 65532
try:
 open('/validator/forbidden','w')
 raise AssertionError('writable root')
except OSError: pass
assert not os.path.exists('/var/run/docker.sock')
with open('/proc/self/status') as stream:
 status=stream.read()
assert 'NoNewPrivs:\\t1' in status
''' + NETWORK_ISOLATION_CHECK
        try:
            result = subprocess.run(command + ['-I', '-c', code], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=20)
            self.assertEqual(result.returncode, 0)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)


if __name__ == '__main__':
    unittest.main()

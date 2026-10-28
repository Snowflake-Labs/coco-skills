import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ingest import (PLUGIN_LICENSE, Entry, SymlinkInput, UnsafeInput, collect, decode,
                    open_root, plugin_license)
import launch
from checks import review
from markdown_checks import parse
from protocol import error_report, render, validate_report
from license_fixtures import APACHE

VALID = b'---\nname: example\ndescription: "Use when checking. Triggers: check"\n---\nAdvisory report.\n'
COLLECTOR = Path(launch.__file__).with_name('collect_snapshot.py')


def codes(report):
    return [item['code'] for item in report['findings']]


class PluginLicenseTests(unittest.TestCase):
    """Plugin skills are checked against the plugin root LICENSE, no exceptions."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.plugin = self.root / 'plugins' / 'example-plugin'
        self.skill = self.plugin / 'skills' / 'example'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_bytes(VALID)

    def wire(self, path=None):
        child = subprocess.run([sys.executable, '-I', str(COLLECTOR), str(path or self.skill)],
                               capture_output=True, timeout=10)
        return child.returncode, child.stdout

    def test_plugin_root_license_is_captured_and_passes(self):
        (self.plugin / 'LICENSE').write_bytes(APACHE)
        status, wire = self.wire()
        self.assertEqual(status, 0)
        _, entries = decode(wire)
        self.assertIn(Entry(PLUGIN_LICENSE, APACHE), entries)
        report = review('example', entries)
        self.assertNotIn('format.license', codes(report))
        self.assertIn('license.inherited', codes(report))
        validate_report(report)

    def test_missing_plugin_license_fails_even_with_skill_license(self):
        (self.skill / 'LICENSE').write_bytes(APACHE)
        status, wire = self.wire()
        self.assertEqual(status, 0)
        _, entries = decode(wire)
        self.assertIn(Entry(PLUGIN_LICENSE, b''), entries)
        report = review('example', entries)
        self.assertIn('format.license', codes(report))
        self.assertEqual(report['status'], 'failed')

    def test_cortex_plugin_marker_outside_plugins_folder(self):
        plugin = self.root / 'elsewhere'
        skill = plugin / 'skills' / 'example'
        skill.mkdir(parents=True)
        (plugin / '.cortex-plugin').mkdir()
        (plugin / 'LICENSE').write_bytes(APACHE)
        self.assertEqual(plugin_license(str(skill)), Entry(PLUGIN_LICENSE, APACHE))

    def test_top_level_skills_are_unchanged(self):
        skill = self.root / 'skills' / 'example'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_bytes(VALID)
        (skill / 'LICENSE').write_bytes(APACHE)
        (self.root / 'LICENSE').write_bytes(b'not read')
        self.assertIsNone(plugin_license(str(skill)))
        _, entries = decode(self.wire(skill)[1])
        self.assertNotIn(PLUGIN_LICENSE, [entry.path for entry in entries])
        self.assertNotIn('format.license', codes(review('example', entries)))

    def test_candidate_cannot_supply_reserved_name(self):
        (self.plugin / 'LICENSE').write_bytes(APACHE)
        (self.skill / PLUGIN_LICENSE).write_bytes(APACHE)
        self.assertEqual(self.wire(), (2, b''))

    def test_plugin_license_symlink_and_hardlink_rejected(self):
        target = self.root / 'real-license'
        target.write_bytes(APACHE)
        (self.plugin / 'LICENSE').symlink_to(target)
        with self.assertRaises(SymlinkInput):
            plugin_license(str(self.skill))
        self.assertEqual(self.wire(), (3, b''))
        (self.plugin / 'LICENSE').unlink()
        os.link(target, self.plugin / 'LICENSE')
        with self.assertRaises(UnsafeInput):
            plugin_license(str(self.skill))


class SymlinkReportingTests(unittest.TestCase):
    """Symlinks produce a distinct fixed error that never echoes the path."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_link_component_in_candidate_path(self):
        real = self.root / 'real' / 'example'
        real.mkdir(parents=True)
        (real / 'SKILL.md').write_bytes(VALID)
        (self.root / 'alias').symlink_to(self.root / 'real', target_is_directory=True)
        with self.assertRaises(SymlinkInput):
            open_root(str(self.root / 'alias' / 'example'))
        with self.assertRaises(SymlinkInput):
            collect(str(self.root / 'alias' / 'example'))

    def test_link_inside_bundle(self):
        candidate = self.root / 'example'
        candidate.mkdir()
        (candidate / 'SKILL.md').write_bytes(VALID)
        (candidate / 'CANARY_LINK').symlink_to(candidate / 'SKILL.md')
        with self.assertRaises(SymlinkInput) as error:
            launch.collect(str(candidate))
        self.assertEqual(str(error.exception), '')

    def test_symlink_report_is_fixed_and_blocked(self):
        report = validate_report(error_report('input.symlink', 'blocked'))
        text = render(report, 'text')
        self.assertIn('symbolic link', text)
        self.assertIn('/private/tmp', text)
        self.assertEqual(report['status'], 'blocked')

    def test_symlink_is_still_an_oserror_for_existing_callers(self):
        self.assertIsInstance(SymlinkInput(), OSError)
        self.assertIsInstance(SymlinkInput(), UnsafeInput)


class HeadingAnchorTests(unittest.TestCase):
    """Plain ASCII punctuation uses GitHub slugs; markup stays conservative."""

    def test_punctuation_headings_are_complete(self):
        document = parse('# Optional: grant access (v2.0)\n\n## Q&A? no\n')
        self.assertFalse(document.anchors_complete)
        document = parse('# Optional: grant access (v2.0)\n## Step 1. Setup, then run\n')
        self.assertTrue(document.anchors_complete)
        self.assertIn('optional-grant-access-v20', document.anchors)
        self.assertIn('step-1-setup-then-run', document.anchors)
        self.assertEqual(document.issues, [])

    def test_markup_unicode_and_empty_slugs_remain_partial(self):
        for heading in ('Use `code`', 'A *bold* idea', 'See [link](x)', 'Caf\u00e9', '!!!'):
            with self.subTest(heading=heading):
                document = parse('# ' + heading + '\n')
                self.assertFalse(document.anchors_complete)
                self.assertIn(('markdown.limited', 1), document.issues)

    def test_local_fragment_to_punctuated_heading_resolves(self):
        entries = (Entry('LICENSE', APACHE),
                   Entry('SKILL.md', VALID + b'\n## Optional: extra step\n\nSee [it](#optional-extra-step).\n'))
        report = review('example', entries)
        self.assertNotIn('markdown.limited', codes(report))
        self.assertNotIn('markdown.anchor', codes(report))


if __name__ == '__main__':
    unittest.main()

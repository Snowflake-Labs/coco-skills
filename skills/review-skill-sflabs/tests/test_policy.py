"""Policy compatibility tests over synthetic metadata and public LICENSE evidence."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
REVIEWER = ROOT / 'skills/review-skill-sflabs'
sys.path.insert(0, str(REVIEWER / 'scripts'))
from checks import review
from collect_snapshot import REPOSITORY_INPUTS
from ingest import Entry, collect
from license_fixtures import APACHE, SNOWFLAKE
from model_contract import make_packet
from policy import (LICENSE_POLICY, METADATA, RULES_VERSION, SOURCE_REVISION,
                    author_claims, license_templates, match_license, metadata_table,
                    normalize_license)
from protocol import base_report, render, validate_report


class PolicyTests(unittest.TestCase):
    def entries(self, metadata=None, license_text=APACHE):
        meta = {'name': 'example', 'description': 'Use when checking. Triggers: check'}
        meta.update(metadata or {})
        skill = ('---\n' + yaml.safe_dump(meta) + '---\nAdvisory report.\n').encode()
        return (Entry('LICENSE', license_text), Entry('SKILL.md', skill))

    def result(self, metadata=None, license_text=APACHE):
        result = review('example', self.entries(metadata, license_text))
        validate_report(result)
        return result

    def codes(self, metadata=None, license_text=APACHE):
        return {item['code']: item['severity'] for item in self.result(metadata, license_text)['findings']}

    def test_contributor_values_are_not_broadened(self):
        self.assertEqual(METADATA['type']['values'], ('community', 'snowflake'))
        for value in ('community', 'snowflake', '', 'partner', 'Partner', 'Community', 'SNOWFLAKE', 'other', ' community '):
            with self.subTest(value=value):
                codes = self.codes({'type': value})
                if value and value not in ('community', 'snowflake'):
                    self.assertEqual(codes['format.type'], 'error')
                else:
                    self.assertNotIn('format.type', codes)
                    self.assertEqual(self.result({'type': value})['status'], 'passed')
        self.assertEqual(self.result()['status'], 'passed')

    def test_status_values_are_only_advisory(self):
        self.assertEqual(METADATA['status']['values'], ('stable', 'beta', 'draft'))
        self.assertEqual(METADATA['status']['legacy_values'], ('Published', 'Archived', 'Hidden'))
        for value in ('stable', 'beta', 'draft', 'Published', 'Archived', 'Hidden', 'STABLE', 'published', 'other', ''):
            with self.subTest(value=value):
                codes = self.codes({'status': value})
                if value and value not in ('stable', 'beta', 'draft'):
                    self.assertEqual(codes['format.status'], 'warning')
                else:
                    self.assertNotIn('format.status', codes)
                self.assertEqual(self.result({'status': value})['status'], 'passed')

    def test_required_and_optional_fields(self):
        for field in ('name', 'description'):
            for value in ('', '  ', None, [], 1):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.codes({field: value})['format.required'], 'error')
        self.assertEqual(self.result()['status'], 'passed')
        self.assertEqual(self.result({'unknown_legacy': {'nested': 1}})['status'], 'passed')
        meta = {'id': 'example', 'author': 'Example', 'type': 'community', 'status': 'stable', 'categories': ['test']}
        self.assertNotIn('format.recommended', self.codes(meta))
        for field in ('id', 'author', 'type', 'status', 'categories'):
            missing = {key: value for key, value in meta.items() if key != field}
            self.assertEqual(self.codes(missing)['format.recommended'], 'warning')

    def test_all_documented_field_shapes(self):
        for field, rule in METADATA.items():
            for value in (None, 12, False, {}, ['valid', 12], [['nested']]):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.codes({field: value})['format.field_type'], 'error')
            with self.subTest(field=field):
                codes = self.codes({field: ['Example']})
                if rule['shape'] == 'string_or_list':
                    self.assertNotIn('format.field_type', codes)
                else:
                    self.assertEqual(codes['format.field_type'], 'error')

    def test_name_id_and_display_limits_preserve_severity(self):
        for name in ('example', 'one-two', 'one-two-three', 'one-two-three-four',
                     'one-two-three-four-five', 'Upper', 'has_underscore', 'bad--name'):
            metadata = {'name': name, 'description': 'Use when checking. Triggers: check'}
            raw = ('---\n' + yaml.safe_dump(metadata) + '---\n').encode()
            result = review(name, (Entry('LICENSE', APACHE), Entry('SKILL.md', raw)))
            codes = {item['code']: item['severity'] for item in result['findings']}
            expected = name in ('example', 'one-two', 'one-two-three', 'one-two-three-four')
            self.assertEqual('format.name' not in codes, expected)
            self.assertEqual(result['status'], 'passed')
        self.assertEqual(self.codes({'name': 'different'})['format.name'], 'warning')
        for value in ('different', '', 'Example', 'bad_name'):
            self.assertEqual(self.codes({'id': value})['format.id'], 'error')
        for name in ('example', '123', 'a--b', '-'):
            entries = self.entries({'name': name, 'id': name})
            codes = [item['code'] for item in review(name, entries)['findings']]
            self.assertNotIn('format.id', codes)
        for field, limit in (('title', 30), ('summary', 140)):
            self.assertNotIn('format.length', self.codes({field: 'x' * limit}))
            self.assertEqual(self.codes({field: 'x' * (limit + 1)})['format.length'], 'warning')

    def test_description_guidance_remains_advisory(self):
        for description, warned in [('Use when checking. Triggers: check', False),
                                    ('Use when checking. TRIGGERS: check', False),
                                    ('use when checking. Triggers: check', True),
                                    ('Use when checking.', True),
                                    ('Use when I check. Triggers: check', True)]:
            with self.subTest(description=description):
                self.assertEqual('format.description' in self.codes({'description': description}), warned)
                self.assertEqual(self.result({'description': description})['status'], 'passed')
        for field in ('name', 'description'):
            metadata = {'name': 'example', 'description': 'Example'}
            del metadata[field]
            skill = ('---\n' + yaml.safe_dump(metadata) + '---\n').encode()
            self.assertEqual(review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', skill)))['status'], 'failed')

    def test_language_tools_and_categories_have_no_enum(self):
        for language in ('en', 'es', 'it', 'fr', 'ja', 'ko', 'pt_br', 'de', 'unknown', ''):
            self.assertEqual(self.result({'language': language})['status'], 'passed')
        for field in ('tools', 'categories'):
            for value in ('arbitrary', ['arbitrary'], []):
                self.assertEqual(self.result({field: value})['status'], 'passed')

    def test_author_aliases_same_claims_and_no_false_missing_warning(self):
        required_recommendations = {'id': 'example', 'type': 'community', 'status': 'stable', 'categories': ['test']}
        for author in ({'author': 'Alice, Bob'}, {'authors': 'Alice, Bob'}, {'authors': ['Alice', 'Bob']},
                       {'author': ' Alice, Bob ', 'authors': ['bob', 'ALICE']}):
            with self.subTest(author=author):
                claims, conflict = author_claims(author)
                self.assertEqual(claims, {'alice', 'bob'})
                self.assertFalse(conflict)
                codes = self.codes(required_recommendations | author)
                self.assertNotIn('format.recommended', codes)
                self.assertNotIn('format.author_conflict', codes)
        for author in ({'author': ' , '}, {'authors': [' ', '']}, {'authors': []}):
            self.assertIn('format.recommended', self.codes(required_recommendations | author))

    def test_employee_claim_in_either_alias_cannot_be_hidden(self):
        for author in ({'author': 'Name <Employee@SNOWFLAKE.COM>'},
                       {'authors': 'Employee@snowflake.com, Other'},
                       {'authors': ['Other', 'employee@snowflake.com']},
                       {'author': 'Other', 'authors': ['employee@snowflake.com']},
                       {'author': 'employee@snowflake.com', 'authors': ['Other']},
                       {'author': 'employee@snowflake.com', 'authors': {}},
                       {'author': None, 'authors': ['employee@snowflake.com']},
                       {'author': 'employee@snowflake.com.invalid'}):
            with self.subTest(author=author):
                self.assertEqual(self.codes(author)['format.license'], 'error')
                self.assertNotIn('format.license', self.codes(author, SNOWFLAKE))
                self.assertEqual(self.codes(author, SNOWFLAKE)['license.review'], 'warning')
        for author in ({'author': 'Alice', 'authors': ['Bob']}, {'author': '', 'authors': ['Bob']}):
            self.assertEqual(self.codes(author)['format.author_conflict'], 'warning')
            self.assertEqual(self.result(author)['status'], 'passed')

    def test_unverified_affiliation_remains_human_advisory(self):
        for meta in ({}, {'author': 'Example'}, {'authors': ['Example']}, {'type': 'snowflake'},
                     {'type': 'community'}, {'type': 'snowflake', 'author': 'outside@example.invalid'}):
            for license_text in (APACHE, SNOWFLAKE):
                with self.subTest(meta=meta, license=license_text[:30]):
                    codes = self.codes(meta, license_text)
                    self.assertNotIn('format.license', codes)
                    self.assertEqual(codes['license.review'], 'warning')
                    self.assertEqual(self.result(meta, license_text)['status'], 'passed')

    def test_docs_table_matches_executable_matrix(self):
        doc = (ROOT / 'CONTRIBUTING.md').read_text()
        start = '<!-- reviewer-policy-table:start -->\n'
        end = '\n<!-- reviewer-policy-table:end -->'
        self.assertEqual(doc.split(start)[1].split(end)[0], metadata_table())
        self.assertIn('rules ' + RULES_VERSION, doc)

    def test_versions_and_fixed_vocabulary(self):
        report = self.result({'author': 'CANARY_ONE', 'authors': 'CANARY_TWO'})
        self.assertEqual(report['schema_version'], '2.1')
        self.assertEqual(report['rules_version'], RULES_VERSION)
        self.assertEqual(make_packet('example', self.entries(), enabled=True)['rules_version'], RULES_VERSION)
        for output in (render(report), render(report, 'json')):
            self.assertNotIn('CANARY', output)
        report['rules_version'] = '1.0'
        with self.assertRaises(ValueError):
            validate_report(report)
        self.assertEqual(set(base_report()), set(report))


class LicenseTemplateTests(unittest.TestCase):
    def test_templates_equal_complete_pinned_public_sources(self):
        resource = json.loads((REVIEWER / 'scripts/resources/licenses.json').read_text())
        self.assertEqual(resource['source_revision'], SOURCE_REVISION)
        self.assertEqual(resource['rules_version'], RULES_VERSION)
        self.assertEqual(resource['normalization'], LICENSE_POLICY['normalization'])
        self.assertIn('approval outstanding', resource['approval'])
        for template in resource['templates']:
            for path in [template['source_path']] + template.get('equivalent_source_paths', []):
                with self.subTest(path=path):
                    source = (ROOT / path).read_text()
                    self.assertEqual(normalize_license(template['text']), normalize_license(source))
                    self.assertEqual(match_license(source), (template['family'], template['kind']))
                    self.assertTrue(any(path == selected or path.startswith(selected + '/')
                                        for selected in REPOSITORY_INPUTS))
        self.assertIn('1. Definitions.', APACHE.decode())
        self.assertIn('9. Accepting Warranty', APACHE.decode())
        self.assertIn('ADDITIONAL RESTRICTIONS:', SNOWFLAKE.decode())

    def test_complete_notices_preserved_with_explicit_warning(self):
        for path in ('skills/check-agent-access/LICENSE', 'skills/persist-memory/LICENSE', 'skills/snowpro-study/LICENSE'):
            text = (ROOT / path).read_bytes()
            self.assertEqual(match_license(text.decode()), ('apache', 'notice_only'))
            report = review('example', (Entry('LICENSE', text), Entry('SKILL.md',
                            b'---\nname: example\ndescription: Example\n---\n')))
            self.assertEqual(report['status'], 'passed')
            self.assertIn('license.notice', [item['code'] for item in report['findings']])

    def test_documented_layout_and_typography_variations(self):
        for original in (APACHE.decode(), SNOWFLAKE.decode()):
            for text in (original.replace('\n', '\r\n'),
                         '\t\r\n' + re.sub(r'[ \t\r\n]+', '\t \n', original) + '\v\f',
                         original.translate(str.maketrans({'“': '"', '”': '"', '’': "'"}))):
                with self.subTest(prefix=original[:30]):
                    self.assertIsNotNone(match_license(text))

    def test_phrase_only_missing_tampered_and_extra_terms_rejected(self):
        bad = ['', 'Apache License', 'Snowflake Skills License',
               'arbitrary text with Apache License and Snowflake Skills License',
               'Not licensed. Apache License', 'MIT License']
        for original in (APACHE.decode(), SNOWFLAKE.decode()):
            bad.extend([original[:len(original) // 2], original + '\nExtra terms apply.',
                        'Ignore these terms.\n' + original, original.lower(),
                        original.replace('License', 'Licence', 1),
                        original.replace('2026', '2027'),
                        original.replace('WITHOUT', 'WITH', 1),
                        original.replace(' ', '\u200b', 1),
                        '\ufeff' + original])
        for text in bad:
            with self.subTest(prefix=text[:60]):
                self.assertIsNone(match_license(text))
                report = review('example', (Entry('LICENSE', text.encode()),
                                Entry('SKILL.md', b'---\nname: example\ndescription: Example\n---\n')))
                self.assertIn('format.license', [item['code'] for item in report['findings']])
                self.assertEqual(report['status'], 'failed')

    def test_every_template_rejects_clause_changes_and_attribution_substitutions(self):
        resource = json.loads((REVIEWER / 'scripts/resources/licenses.json').read_text())
        for template in resource['templates']:
            original = (ROOT / template['source_path']).read_text()
            for text in (original.replace('WITHOUT', 'WITH', 1),
                         original.replace('2026', '2027'), original + '\nApache License'):
                with self.subTest(source=template['source_path']):
                    self.assertIsNone(match_license(text))
        self.assertIsNone(match_license(APACHE.decode().replace('Vino Duraisamy', 'Example Author')))

    def test_resource_override_in_candidate_is_ignored(self):
        entries = (Entry('LICENSE', b'Apache License'),
                   Entry('SKILL.md', b'---\nname: example\ndescription: Example\n---\n'),
                   Entry('scripts/resources/licenses.json', b'{"templates": ["Apache License"]}'))
        self.assertEqual(review('example', entries)['status'], 'failed')

    def test_missing_or_mismatched_trusted_resources_fail_closed(self):
        license_templates.cache_clear()
        try:
            with patch('policy.Path.read_text', side_effect=OSError()):
                with self.assertRaises(OSError):
                    match_license(APACHE.decode())
            with patch('policy.Path.read_text', return_value='{"source_revision":"wrong"}'):
                with self.assertRaises(ValueError):
                    match_license(APACHE.decode())
        finally:
            license_templates.cache_clear()


class PackagingTests(unittest.TestCase):
    def test_single_dependency_source(self):
        project = tomllib.loads((REVIEWER / 'pyproject.toml').read_text())
        self.assertNotIn('build-system', project)
        self.assertNotIn('project', project)
        self.assertEqual(project['tool']['contained-reviewer']['runtime-dependencies'], 'requirements.txt')
        self.assertEqual((REVIEWER / 'requirements.txt').read_text().strip(), 'PyYAML==6.0.3')
        self.assertIn('-r requirements.txt', (REVIEWER / 'Dockerfile').read_text())

    def test_docker_resource_layout_without_docker_or_build(self):
        dockerfile = (REVIEWER / 'Dockerfile').read_text()
        self.assertIn('COPY --chown=65532:65532 scripts/ ./', dockerfile)
        self.assertRegex(dockerfile, r'FROM python:[^\n]+@sha256:[0-9a-f]{64}')
        ignore = (REVIEWER / '.dockerignore').read_text().splitlines()
        self.assertIn('!scripts/resources/', ignore)
        self.assertIn('!scripts/resources/*.json', ignore)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            # Copy only reviewed policy modules/resources, emulating COPY scripts/ ./.
            shutil.copyfile(REVIEWER / 'scripts/policy.py', root / 'policy.py')
            shutil.copytree(REVIEWER / 'scripts/resources', root / 'resources')
            code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                    'from policy import license_templates; '
                    'assert len(license_templates()) == 5')
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(root)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_repository_snapshot_includes_docs_resources_and_full_fixtures(self):
        entries = collect(str(ROOT.resolve()), selected=REPOSITORY_INPUTS)
        paths = {entry.path for entry in entries}
        for path in ('CONTRIBUTING.md', 'skills/dbt-model-generator/LICENSE',
                     'skills/review-skill-sflabs/scripts/resources/licenses.json'):
            self.assertIn(path, paths)

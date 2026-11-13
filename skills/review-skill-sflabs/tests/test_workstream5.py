"""Synthetic offline WS5 fixtures; Git commits exist only in temporary test repos."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'skills/review-skill-sflabs/scripts'))
sys.path.insert(0, str(ROOT / '.github/scripts'))
import validate_skill as ci
import launch
from catalog import QUESTION, identity, related
from checks import review
from comparison import compare, stable_id
from git_context import Checkout, git, selection, source_renames, validate_renames, verify_entries
from ingest import Entry, UnsafeInput, collect, encode
from license_fixtures import APACHE
from markdown_checks import MAX_LINE, parse
from protocol import error_report, render, validate_report

IMAGE = 'sha256:' + 'a' * 64
VALID = b'---\nname: example\ndescription: "Use when checking. Triggers: check"\n---\nAdvisory report.\n'
CATALOG = json.loads((ROOT / 'skills/review-skill-sflabs/references/catalog-snapshot.json').read_text())


def report(extras=(), skill=VALID, image=IMAGE):
    value = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', skill)) + extras, CATALOG)
    value['provenance'].update(image_id=image, base_revision='a' * 40, head_revision='b' * 40)
    return validate_report(value)


class ComparisonTests(unittest.TestCase):
    def test_line_ordinal_shift_and_distinct_metadata_fields(self):
        old = report((Entry('notes.txt', b'urgent'),))
        new = report((Entry('000.txt', b'hello'), Entry('notes.txt', b'\n\nurgent')))
        delta = compare(old, new)
        self.assertEqual(delta['counts']['introduced'], 0)
        self.assertEqual(delta['counts']['resolved'], 0)
        self.assertEqual(delta['counts']['unchanged'], len(old['findings']))
        self.assertEqual(compare(old, old)['findings'], delta['findings'])
        self.assertEqual(len({item['finding_id'] for item in delta['findings']}), len(delta['findings']))

    def test_introduced_resolved_unchanged_without_head_grandfathering(self):
        old = report((Entry('a.txt', b'urgent'), Entry('b.txt', b'hello')))
        new = report((Entry('a.txt', b'hello'), Entry('b.txt', b'urgent')))
        delta = compare(old, new)
        self.assertEqual(delta['counts']['introduced'], 1)
        self.assertEqual(delta['counts']['resolved'], 1)
        bad = report(skill=VALID.replace(b'name: example', b'name: example\ntype: partner'))
        self.assertEqual(compare(bad, bad)['counts']['introduced'], 0)
        self.assertEqual(bad['status'], 'failed')

    def test_incomplete_unsafe_missing_baseline_never_resolved(self):
        for baseline in (None, error_report('input.unsafe', 'blocked'),
                         error_report('runtime.unavailable', 'unavailable'),
                         report((Entry('x.zip', b'PK'),)), report(skill=b'---\n'),
                         report(skill=VALID + b'\n```\nexample'),
                         report(skill=VALID + b'x' * (MAX_LINE + 1))):
            for old, new in ((baseline, report()), (report(), baseline)):
                with self.subTest(old=old and old['status'], new=new and new['status']):
                    delta = compare(old, new)
                    self.assertEqual(delta['comparison'], 'not-comparable')
                    self.assertEqual(delta['counts']['resolved'], 0)
                    self.assertEqual(delta['counts']['introduced'], 0)

    def test_image_catalog_revision_mismatch_no_comparison(self):
        old = report()
        for key, value in [('image_id', 'sha256:' + 'b' * 64), ('image_id', None),
                           ('head_revision', 'c' * 40), ('catalog', {'sha256': 'c' * 64, 'revision': CATALOG['revision']})]:
            new = copy.deepcopy(old)
            new['provenance'][key] = value
            self.assertEqual(compare(old, new)['comparison'], 'not-comparable')

    def test_added_deleted_require_explicit_absence(self):
        self.assertGreater(compare(None, report(), kind='added')['counts']['introduced'], 0)
        self.assertGreater(compare(report(), None, kind='deleted')['counts']['resolved'], 0)
        self.assertEqual(compare(None, report())['comparison'], 'not-comparable')
        with self.assertRaises(ValueError):
            compare(report(), report(), kind='added')
        self.assertEqual(compare(error_report('input.unsafe', 'blocked'), None, kind='deleted')['counts']['resolved'], 0)

    def test_explicit_file_rename_hash_mapping_not_guessed(self):
        old = report((Entry('old.txt', b'urgent'),))
        new = report((Entry('new.txt', b'\nurgent'),))
        self.assertEqual(compare(old, new)['counts']['introduced'], 1)
        mapped = compare(old, new, source_map={stable_id('old.txt'): stable_id('new.txt')})
        self.assertEqual(mapped['counts']['introduced'], 0)
        self.assertEqual(mapped['counts']['resolved'], 0)
        for mapping in ({'CANARY': stable_id('new.txt')}, {stable_id('absent'): stable_id('new.txt')},
                        {stable_id('old.txt'): stable_id('SKILL.md')}):
            with self.assertRaises(ValueError):
                compare(old, new, source_map=mapping)

    def test_duplicate_occurrences_are_rule_source_buckets_not_line_claims(self):
        old = report(skill=VALID + b'\n[a](absent.md)\n')
        new = report(skill=VALID + b'\n\n[a](absent.md)\n[b](also-absent.md)\n')
        self.assertEqual(compare(old, new)['counts']['introduced'], 0)


class MarkdownTests(unittest.TestCase):
    def codes(self, body, extras=()):
        result = report(extras, VALID + body.encode())
        self.assertEqual(result['status'], 'passed')
        return {item['code'] for item in result['findings'] if item['code'].startswith('markdown.') or item['code'] == 'format.reference'}

    def test_examples_fences_inline_and_prose_not_dependencies(self):
        body = '''
Example references/missing.md in prose.
`[sample](references/missing.md)`
````markdown
```nested
[link](missing.md)
Read `workflows/missing.md`
```
````
~~~text
[link](missing.md)
~~~
    [indented](missing.md)
> ```md
> [quoted](missing.md)
> ```
- ```md
  [listed](missing.md)
  ```
Example: Read `references/missing.md`
'''
        self.assertEqual(self.codes(body), set())

    def test_unclosed_and_mismatched_fences(self):
        for body in ('\n```md\n[example](missing.md)', '\n~~~~\n~~~\n', '\n```\n~~~\n'):
            self.assertEqual(self.codes(body), {'markdown.fence'})
        self.assertEqual(self.codes('\n~~~\ntext\n~~~~\n'), set())

    def test_balanced_nested_labels_destinations_titles_and_images(self):
        extras = (Entry('references/a(b).md', b'# Good heading\n'), Entry('data.txt', b'hello'))
        self.assertEqual(self.codes('\n[nested [label]](references/a(b).md#good-heading "title")\n'
                                    '![asset](data.txt)\n', extras), set())
        self.assertEqual(self.codes('\n[nested [label]](references/missing(x).md)\n'), {'format.reference'})
        self.assertEqual(self.codes('\n[escaped](references/a\\(b\\).md)\n', extras), {'markdown.limited'})

    def test_local_anchor_duplicates_setext_relative_and_encoded(self):
        extras = (Entry('references/a.md', b'# Good heading\n# Good heading\nOther\n=====\n[back](../SKILL.md)\n'),)
        self.assertEqual(self.codes('\n[a](references/a.md#good-heading-1)\n[b](references/a.md#other)\n'
                                    '[c](references/a.md#good%2Dheading)\n', extras), set())
        self.assertEqual(self.codes('\n[x](references/a.md#not-here)\n', extras), {'markdown.anchor'})
        self.assertEqual(self.codes('\n# Local\n[x](#local)\n'), set())

    def test_complex_anchor_renderers_abstain(self):
        for heading in ('# **Bold**', '# Unicode \u00e9', '<a id="custom"></a>', '# [Link](https://example.invalid)'):
            self.assertNotIn('markdown.anchor', self.codes('\n[x](notes.md#custom)', (Entry('notes.md', heading.encode()),)))

    def test_reference_links_and_shortcut_prose(self):
        self.assertEqual(self.codes('\n[a][dest]\n\n[dest]: SKILL.md "title"\n'), set())
        self.assertEqual(self.codes('\n[a][missing]\n'), {'markdown.reference'})
        self.assertEqual(self.codes('\n[a][]\n[a]: missing.md\n'), {'format.reference'})
        self.assertEqual(self.codes('\n[plain prose]\n'), set())
        self.assertEqual(self.codes('\n[dest]\n[dest]: SKILL.md\n'), set())

    def test_routing_is_explicit_local_and_not_commands(self):
        for body in ('\nRead `references/missing.md`', '\n1. Follow `workflows/missing.md`', '\n- Load `scripts/missing.py`'):
            self.assertEqual(self.codes(body), {'format.reference'})
        self.assertEqual(self.codes('\nRun `python scripts/missing.py`\n'), set())
        self.assertEqual(self.codes('\nRead `references/<name>.md`\n'), set())

    def test_external_links_and_examples_never_fetched(self):
        self.assertEqual(self.codes('\n[a](https://example.invalid/never) [b](mailto:example@example.invalid) '
                                    '[c](//example.invalid/a)\n'), set())
        self.assertEqual(self.codes('\n[x](../../private.md)\n'), {'format.reference'})
        self.assertEqual(self.codes('\n[x](%2e%2e/private.md)\n'), {'format.reference'})
        self.assertEqual(self.codes('\n[x](/private.md)\n'), {'format.reference'})

    def test_limits_do_not_hang_or_silently_certify(self):
        self.assertEqual(self.codes('\n' + 'x' * (MAX_LINE + 1)), {'markdown.limited'})
        document = parse('\n'.join('[x](a)' for _ in range(2050)))
        self.assertLessEqual(len(document.links), 2048)
        self.assertIn('markdown.limited', [code for code, _ in document.issues])

    def test_indented_fence_literal_and_ascii_line_accounting(self):
        self.assertEqual(self.codes('\n    ```\n    literal example\n'), set())
        value = report(skill=VALID + b'\n\x0b\x0b[x](missing.md)\n')
        item = next(item for item in value['findings'] if item['code'] == 'format.reference')
        self.assertLessEqual(item['line'], (VALID + b'\n\x0b\x0b[x](missing.md)\n').count(b'\n') + 1)
        self.assertEqual(self.codes('\n[x](./)\n'), set())
        self.assertEqual(self.codes('\n' + '[' * 17 + 'x' + ']' * 17 + '(a)\n'), {'markdown.limited'})

    def test_canary_paths_and_anchors_not_in_report(self):
        value = report(skill=VALID + b'\n[CANARY_LABEL](CANARY_PATH.md#CANARY_ANCHOR)\n')
        self.assertNotIn('CANARY', render(value, 'json'))


class CatalogTests(unittest.TestCase):
    def test_expected_neighbors_and_deterministic_order(self):
        for query, expected in [('failover failback catchup disaster recovery', 'snowpipe-bcdr'),
                                ('functional roles role hierarchy secondary roles', 'rbac'),
                                ('ASOF semi-additive time intelligence fan trap', 'semantic-view-patterns')]:
            result = related('example', {'description': query}, CATALOG)
            self.assertEqual(result[0]['catalog_id'], identity(expected))
            self.assertEqual(result, related('example', {'description': query}, CATALOG))
            self.assertLessEqual(len(result), 3)
            self.assertEqual(set(result[0]), {'catalog_id', 'question'})
            self.assertEqual(result[0]['question'], QUESTION)

    def test_no_self_match_unknown_match_or_candidate_catalog_injection(self):
        self.assertEqual(related('rbac', {'description': 'role hierarchy'}, {'entries': [{'name': 'rbac'}]}), [])
        self.assertEqual(related('example', {'description': 'zxqvzy'}, CATALOG), [])
        self.assertEqual(related('example', {}, None), [])
        value = report((Entry('catalog-snapshot.json', b'{"entries":[{"name":"CANARY"}]}'),),
                       VALID.replace(b'Triggers: check', b'Triggers: role hierarchy CANARY'))
        self.assertTrue(value['related'])
        self.assertNotIn('CANARY', render(value, 'json'))

    def test_ties_use_public_identity_not_candidate_order(self):
        catalog = {'entries': [{'name': 'alpha-test'}, {'name': 'beta-test'}, {'name': 'gamma-test'}, {'name': 'delta-test'}]}
        expected = sorted(identity(item['name']) for item in catalog['entries'])[:3]
        self.assertEqual([item['catalog_id'] for item in related('example', {'description': 'test'}, catalog)], expected)
        self.assertEqual(related('example', {'description': 'test'}, catalog),
                         related('example', {'description': 'test'}, {'entries': list(reversed(catalog['entries']))}))

    def test_strict_shortlist_contract_and_legacy_version_rejection(self):
        original = report()
        for related_items in ([{'catalog_id': 'CANARY', 'question': QUESTION}],
                              [{'catalog_id': 'b' * 64, 'question': 'CANARY'}],
                              [{'catalog_id': 'b' * 64, 'question': QUESTION}] * 2,
                              [{'catalog_id': 'b' * 64, 'question': QUESTION, 'name': 'CANARY'}]):
            value = copy.deepcopy(original)
            value['related'] = related_items
            with self.assertRaises(ValueError):
                validate_report(value)
        for version in ('1.0', '2.0'):
            value = copy.deepcopy(original)
            value['schema_version'] = version
            with self.assertRaises(ValueError):
                validate_report(value)


class GitComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.head = self.root / 'head'
        self.base = self.root / 'base'
        self.head.mkdir()
        self.run_git('init', '-q')
        (self.head / 'seed').write_text('synthetic')
        self.initial = self.commit()

    def run_git(self, *args, root=None):
        return subprocess.run(['git', '-C', str(root or self.head), *args], check=True,
                              capture_output=True, timeout=10).stdout.decode().strip()

    def commit(self):
        self.run_git('add', '--all')
        self.run_git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                     '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'synthetic')
        return self.run_git('rev-parse', 'HEAD')

    def skill(self, name='example', body=VALID):
        path = self.head / 'skills' / name
        path.mkdir(parents=True, exist_ok=True)
        (path / 'SKILL.md').write_bytes(body)
        (path / 'LICENSE').write_bytes(APACHE)
        return path

    def base_checkout(self, revision):
        self.run_git('-c', 'core.hooksPath=/dev/null', 'clone', '--no-hardlinks', '-q', str(self.head), str(self.base))
        self.run_git('-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', '-q', revision, root=self.base)

    def args(self, base, head, **updates):
        return argparse.Namespace(**(dict(candidate_root=str(self.head), base_root=str(self.base), base=base,
                                         head=head, image=IMAGE, renames='[]', file_renames='[]', format='json') | updates))

    def fake_review(self, path, image, **kwargs):
        entries = collect(path)
        verify_entries(entries, kwargs['expected_files'])
        result = review(Path(path).name, entries, CATALOG)
        result['provenance'].update(image_id=image, base_revision=kwargs['base_revision'], head_revision=kwargs['head_revision'])
        return validate_report(result)

    def scan(self, args):
        with patch.object(ci, 'review', side_effect=self.fake_review) as reviewer:
            code, output = ci.scan(args)
        return code, json.loads(output), reviewer

    def test_whole_bundle_scan_same_image_and_unchanged_head_error(self):
        path = self.skill(body=VALID.replace(b'name: example', b'name: example\ntype: partner'))
        (path / 'notes.txt').write_text('urgent')
        base = self.commit()
        (path / 'notes.txt').write_text('\nurgent')
        head = self.commit()
        self.base_checkout(base)
        code, output, reviewer = self.scan(self.args(base, head))
        self.assertEqual(code, 1)
        value = output['submissions'][0]
        self.assertEqual(value['delta']['counts']['introduced'], 0)
        self.assertEqual(value['head']['status'], 'failed')
        self.assertEqual(reviewer.call_count, 2)
        self.assertTrue(all(call.args[1] == IMAGE for call in reviewer.call_args_list))
        self.assertTrue(all(len(call.kwargs['expected_files']) == 3 for call in reviewer.call_args_list))

    def test_addition_deletion_and_missing_baseline(self):
        path = self.skill()
        added = self.commit()
        self.base_checkout(self.initial)
        code, output, _ = self.scan(self.args(self.initial, added))
        self.assertEqual(code, 0)
        self.assertEqual(output['submissions'][0]['delta']['kind'], 'added')
        self.assertGreater(output['submissions'][0]['delta']['counts']['introduced'], 0)
        self.run_git('-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', '-q', added, root=self.base)
        (path / 'SKILL.md').unlink()
        (path / 'LICENSE').unlink()
        path.rmdir()
        deleted = self.commit()
        code, output, _ = self.scan(self.args(added, deleted))
        self.assertEqual(code, 0)
        self.assertGreater(output['submissions'][0]['delta']['counts']['resolved'], 0)
        _, output, _ = self.scan(self.args(added, deleted, base_root=None))
        self.assertEqual(output['submissions'][0]['delta']['comparison'], 'not-comparable')
        self.assertEqual(output['submissions'][0]['delta']['counts']['resolved'], 0)

    def test_whole_skill_rename_explicit_mapping_no_heuristic(self):
        path = self.skill('old', VALID.replace(b'example', b'old'))
        base = self.commit()
        path.rename(self.head / 'skills/new')
        head = self.commit()
        self.base_checkout(base)
        _, output, _ = self.scan(self.args(base, head))
        self.assertEqual({item['delta']['kind'] for item in output['submissions']}, {'added', 'deleted'})
        _, output, _ = self.scan(self.args(base, head, renames='[{"base":"old","head":"new"}]'))
        self.assertEqual(len(output['submissions']), 1)
        self.assertEqual(output['submissions'][0]['delta']['kind'], 'renamed')
        self.assertEqual(output['submissions'][0]['delta']['counts']['resolved'], 0)
        self.assertEqual(output['submissions'][0]['head']['status'], 'passed')

    def test_file_rename_mapping_and_line_shift(self):
        path = self.skill()
        (path / 'old.txt').write_text('urgent')
        base = self.commit()
        (path / 'old.txt').rename(path / 'new.txt')
        (path / 'new.txt').write_text('\nurgent')
        head = self.commit()
        self.base_checkout(base)
        _, output, _ = self.scan(self.args(base, head, file_renames='[{"base":"skills/example/old.txt","head":"skills/example/new.txt"}]'))
        self.assertEqual(output['submissions'][0]['delta']['counts']['introduced'], 0)
        self.assertEqual(output['submissions'][0]['delta']['counts']['resolved'], 0)

    def test_wrong_revisions_nofollow_checkouts_and_dirty_snapshots(self):
        path = self.skill()
        base = self.commit()
        (path / 'notes.txt').write_text('urgent')
        head = self.commit()
        self.base_checkout(base)
        with self.assertRaises(UnsafeInput):
            Checkout(str(self.head), base)
        link = self.root / 'link'
        link.symlink_to(self.head, target_is_directory=True)
        with self.assertRaises(OSError):
            Checkout(str(link), head)
        expected = Checkout(str(self.head), head).files('example')
        for change in ('edit', 'extra', 'missing'):
            if change == 'edit':
                (path / 'notes.txt').write_text('changed')
            elif change == 'extra':
                (path / 'notes.txt').write_text('urgent')
                (path / 'extra.txt').write_text('ignored')
            else:
                (path / 'extra.txt').unlink()
                (path / 'notes.txt').unlink()
            with self.assertRaises(UnsafeInput):
                verify_entries(collect(str(path)), expected)
        with patch('launch.local_docker'), patch('launch.subprocess.run') as run, \
                patch('launch.collect', return_value=encode(collect(str(path)), 'example')):
            result = launch.review(str(path), IMAGE, expected_files=expected)
        self.assertEqual(result['status'], 'blocked')
        run.assert_not_called()

    def test_baseline_mismatch_unsafe_incomplete_and_unavailable_no_resolution(self):
        path = self.skill()
        (path / 'notes.txt').write_text('urgent')
        base = self.commit()
        (path / 'notes.txt').write_text('fine')
        head = self.commit()
        self.base_checkout(base)
        self.run_git('-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', '-q', head, root=self.base)
        _, output, _ = self.scan(self.args(base, head))
        self.assertEqual(output['submissions'][0]['delta']['counts']['resolved'], 0)
        self.run_git('-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', '-q', base, root=self.base)
        (self.base / 'skills/example/notes.txt').unlink()
        (self.base / 'skills/example/notes.txt').symlink_to(self.root / 'absent')
        _, output, _ = self.scan(self.args(base, head))
        self.assertEqual(output['submissions'][0]['delta']['comparison'], 'not-comparable')
        self.assertEqual(output['submissions'][0]['delta']['counts']['resolved'], 0)

    def test_deleted_entry_left_in_checkout_is_not_removed(self):
        path = self.skill()
        base = self.commit()
        self.run_git('rm', '-r', 'skills/example')
        head = self.commit()
        self.skill()
        with self.assertRaises(UnsafeInput):
            self.scan(self.args(base, head, base_root=None))

    def test_different_base_from_mergebase_uses_exact_trees(self):
        self.skill()
        base = self.commit()
        # Compare reversed revisions: exact tree difference still includes example.
        self.assertEqual(selection(str(self.head), base, self.initial), ['example'])

    def test_git_disables_hooks_filters_lazy_fetch_and_environment_overrides(self):
        def fake_run(command, **kwargs):
            self.assertIn('core.hooksPath=/dev/null', command)
            self.assertIn('protocol.allow=never', command)
            self.assertIn('core.fsmonitor=false', command)
            self.assertNotIn('GIT_DIR', kwargs['env'])
            self.assertEqual(kwargs['env']['GIT_NO_LAZY_FETCH'], '1')
            self.assertEqual(kwargs['env']['GIT_NO_REPLACE_OBJECTS'], '1')
            kwargs['stdout'].write(b'safe')
            return subprocess.CompletedProcess(command, 0)
        with patch.dict(os.environ, GIT_DIR='/CANARY'), patch('git_context.subprocess.run', side_effect=fake_run):
            self.assertEqual(git(str(self.head), 'rev-parse', 'HEAD'), b'safe')

    def test_real_launch_rejects_dirty_bytes_before_container_and_hides_names(self):
        path = self.skill()
        base = self.commit()
        (path / 'notes.txt').write_text('urgent')
        head = self.commit()
        expected = Checkout(str(self.head), head).files('example')
        (path / 'notes.txt').write_text('CANARY_DIRTY')
        with patch('launch.local_docker'), patch('launch.collect', return_value=encode(collect(str(path)), 'example')):
            result = launch.review(str(path), IMAGE, expected_files=expected)
        self.assertEqual(result['findings'][0]['code'], 'input.unsafe')
        self.assertNotIn('CANARY', render(result, 'json'))
        self.assertEqual(Checkout(str(self.head), head).files('example')['notes.txt'], expected['notes.txt'])

    def test_changed_checkout_identity_and_git_symlink_rejected(self):
        checked = Checkout(str(self.head), self.initial)
        self.head.rename(self.root / 'moved')
        self.head.mkdir()
        with self.assertRaises(OSError):
            checked.check()
        self.head.rmdir()
        (self.root / 'moved').rename(self.head)
        (self.head / '.git').rename(self.root / 'git-store')
        (self.head / '.git').symlink_to(self.root / 'git-store', target_is_directory=True)
        with self.assertRaises(OSError):
            Checkout(str(self.head), self.initial)

    def test_aggregate_never_emits_candidate_names_or_untrusted_git_errors(self):
        self.skill('CANARY_NAME')
        head = self.commit()
        _, output, _ = self.scan(self.args(self.initial, head, base_root=None))
        self.assertNotIn('CANARY_NAME', json.dumps(output))
        for item in output['submissions'][0]['delta']['findings']:
            self.assertRegex(item['finding_id'], r'^[0-9a-f]{64}$')

    def test_checkout_revision_changes_before_publication_fail_closed(self):
        path = self.skill()
        base = self.commit()
        (path / 'notes.txt').write_text('urgent')
        head = self.commit()
        original = Checkout.check
        calls = []
        def changed(checkout):
            calls.append(checkout.root)
            if len(calls) > 1:
                raise UnsafeInput()
            original(checkout)
        with patch.object(Checkout, 'check', side_effect=changed, autospec=True), \
                patch.object(ci, 'review', side_effect=self.fake_review):
            with self.assertRaises(UnsafeInput):
                ci.scan(self.args(base, head, base_root=None))

    def test_prohibited_git_modes_cannot_be_regular_snapshot_files(self):
        entry = Entry('notes.txt', b'synthetic')
        oid = hashlib.sha1(b'blob 9\0synthetic').hexdigest()
        for mode, kind in (('120000', 'blob'), ('160000', 'commit'), ('040000', 'tree')):
            with self.assertRaises(UnsafeInput):
                verify_entries((entry,), {'notes.txt': (mode, kind, oid)})

    def test_rename_mapping_rejects_ambiguous_or_candidate_paths(self):
        old = {'old': {'a': ('100644', 'blob', 'a' * 40)}, 'new': {}}
        new = {'old': {}, 'new': {'a': ('100644', 'blob', 'a' * 40)}}
        self.assertEqual(validate_renames([{'base': 'old', 'head': 'new'}], old, new), [('old', 'new')])
        for mapping in ([{'base': 'old', 'head': '../new'}], [{'base': 'old', 'head': 'new'}] * 2,
                        [{'base': 'missing', 'head': 'new'}], [{'base': 'old', 'head': 'new', 'extra': True}]):
            with self.assertRaises(UnsafeInput):
                validate_renames(mapping, old, new)
        with self.assertRaises(UnsafeInput):
            source_renames([{'base': 'skills/old/../a', 'head': 'skills/new/a'}], [('old', 'new')], old, new)


if __name__ == '__main__':
    unittest.main()

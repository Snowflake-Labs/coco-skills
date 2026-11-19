import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import risk_checks
from risk_checks import dependency_findings, sql_findings
from ingest import PLUGIN_LICENSE, Entry
from checks import review
from protocol import TIERS, exit_code, render, validate_report
from license_fixtures import APACHE

VALID = b'---\nname: example\ndescription: "Use when checking. Triggers: check"\n---\nAdvisory report.\n'


def codes(findings):
    return [code for code, _ in findings]


class SqlEscapingTests(unittest.TestCase):
    """Every SQL-escaping pattern is a blocking RED error."""

    def test_interpolation_forms(self):
        for source in ('q = f"SELECT * FROM {table}"',
                       "q = f'''\n  DELETE FROM {t}'''",
                       'q = "SELECT * FROM {}".format(table)',
                       'q = "SELECT * FROM t WHERE id = %s" % value',
                       'q = "SELECT * FROM " + table',
                       'q = base + " WHERE id = 1"',
                       'const q = `SELECT * FROM ${table}`;',
                       'snowflake.execute({sqlText: "DROP TABLE " + name});',
                       'session.sql(f"USE ROLE {role}")',
                       'cursor.execute("GRANT ROLE x TO USER " + user)'):
            with self.subTest(source=source):
                self.assertIn('security.sql_interpolation', codes(sql_findings('x.py', source)))

    def test_safe_sql_is_not_flagged(self):
        for source in ('cursor.execute("SELECT * FROM t WHERE id = %s", (value,))',
                       'session.sql("SELECT * FROM IDENTIFIER(?)", params=[name])',
                       'q = f"{{SELECT}} literal braces"',
                       'q = "SELECT 1"',
                       'Select a file from the list and {open} it.',
                       "EXECUTE IMMEDIATE FROM @stage/setup.sql;",
                       "EXECUTE IMMEDIATE 'SELECT 1';",
                       "EXECUTE IMMEDIATE $$ SELECT 1 $$;"):
            with self.subTest(source=source):
                self.assertEqual(sql_findings('x.py', source), [])

    def test_dynamic_sql(self):
        for source in ("EXECUTE IMMEDIATE :stmt;",
                       "EXECUTE IMMEDIATE stmt;",
                       "EXECUTE IMMEDIATE 'ALTER USER ' || name || ' SET X';"):
            with self.subTest(source=source):
                self.assertIn('security.sql_dynamic', codes(sql_findings('x.sql', source)))

    def test_client_substitution_only_in_sql_regions(self):
        self.assertIn('security.sql_substitution', codes(sql_findings('x.sql', 'SELECT * FROM &table;')))
        markdown = 'Use &amp; here.\n\n```sql\nSELECT * FROM &{db}.PUBLIC.T;\nSELECT <% ctx.env.x %>;\n```\n'
        found = sql_findings('SKILL.md', markdown)
        self.assertEqual(found, [('security.sql_substitution', 4), ('security.sql_substitution', 5)])
        self.assertEqual(sql_findings('SKILL.md', 'Tom &Jerry outside SQL\n'), [])

    def test_payloads_and_multi_statement_sql(self):
        for source in ("WHERE name = '' OR '1'='1'",
                       "x'; DROP TABLE users; --",
                       "' UNION SELECT NULL, NULL"):
            with self.subTest(source=source):
                self.assertIn('security.sql_payload', codes(sql_findings('x.md', source)))
        # Ordinary multi-statement SQL across lines is not a payload.
        self.assertEqual(sql_findings('x.md', "ALTER USER x SET NAME = 'a';\nGRANT ROLE r TO USER x;"), [])

    def test_prose_splicing(self):
        self.assertIn('security.sql_splicing',
                      codes(sql_findings('SKILL.md', 'Substitute the table name into the SQL query.')))
        self.assertEqual(sql_findings('SKILL.md', 'Insert the table name into the SQL query using IDENTIFIER().'), [])
        self.assertEqual(sql_findings('SKILL.md', 'Pass the value into the query as a bind variable.'), [])
        self.assertEqual(sql_findings('script.py', '# substitute the name into the query'), [])

    def test_findings_are_capped_and_line_numbered(self):
        source = '\n'.join(f'q{i} = f"SELECT {{x{i}}}"' for i in range(20))
        found = sql_findings('x.py', source)
        self.assertEqual(len(found), risk_checks.MAX_PER_CODE)
        self.assertEqual([line for _, line in found], [1, 2, 3, 4, 5])

    def test_red_errors_fail_and_render_tier(self):
        report = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', VALID),
                                    Entry('scripts/run.py', b'q = f"DROP TABLE {name}"\n')))
        validate_report(report)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(exit_code(report), 1)
        finding = next(item for item in report['findings'] if item['code'] == 'security.sql_interpolation')
        self.assertEqual((finding['severity'], finding['group'], finding['line']), ('error', 'required', 1))
        text = render(report, 'text')
        self.assertIn('[RED] error security.sql_interpolation F0003:1', text)
        self.assertNotIn('DROP TABLE', text)

    def test_license_text_is_not_scanned(self):
        report = review('example', (Entry('LICENSE', APACHE + b"\n' OR '1'='1\n"), Entry('SKILL.md', VALID)))
        self.assertNotIn('security.sql_payload', [item['code'] for item in report['findings']])


class DependencyTests(unittest.TestCase):
    """Dependency findings are ORANGE advisories."""

    def test_unpinned_manifests(self):
        cases = {
            'requirements.txt': 'requests>=2\nflask\nnumpy==1.26.4\n-r other.txt\n',
            'pyproject.toml': '[project]\nname = "x"\ndependencies = ["requests>=2", "pyyaml==6.0.3"]\n',
            'package.json': json.dumps({'dependencies': {'left-pad': '^1.3.0', 'lodash': '4.17.21'}}),
        }
        expected = {'requirements.txt': 2, 'pyproject.toml': 1, 'package.json': 1}
        for name, text in cases.items():
            with self.subTest(name=name):
                found = dependency_findings(name, text)
                self.assertEqual(codes(found).count('dependency.unpinned'), expected[name])

    def test_install_commands(self):
        text = ('Run `pip install requests` then `pip install pyyaml==6.0.3`.\n'
                'npm install left-pad@1.3.0 lodash\n'
                'uvx ruff check .\n'
                'pip install -r requirements.txt\n')
        found = dependency_findings('SKILL.md', text)
        self.assertEqual([item for item in found if item[0] == 'dependency.unpinned'],
                         [('dependency.unpinned', 1), ('dependency.unpinned', 2), ('dependency.unpinned', 3)])

    def test_pinned_versions_checked_against_snapshot(self):
        snapshot = {'format': 'osv-ranges/1', 'packages': {
            'PyPI': {'requests': [{'id': 'GHSA-test', 'introduced': '0', 'fixed': '2.32.0'}]},
            'npm': {'lodash': [{'id': 'GHSA-npm', 'introduced': '4.0.0', 'last_affected': '4.17.20'}]}}}
        with patch.object(risk_checks, 'advisories', return_value=snapshot):
            self.assertEqual(dependency_findings('requirements.txt', 'requests==2.31.0\n'),
                             [('dependency.vulnerable', 1)])
            self.assertEqual(dependency_findings('requirements.txt', 'requests==2.32.0\n'), [])
            self.assertEqual(dependency_findings('SKILL.md', 'npm install lodash@4.17.20\n'),
                             [('dependency.vulnerable', 1)])
            self.assertEqual(dependency_findings('SKILL.md', 'npm install lodash@4.17.21\n'), [])

    def test_empty_snapshot_reports_unchecked(self):
        risk_checks.advisories.cache_clear()
        self.assertEqual(risk_checks.advisories()['packages'], {})
        self.assertEqual(dependency_findings('requirements.txt', 'pyyaml==6.0.3\n'),
                         [('dependency.unchecked', 1)])

    def test_orange_is_advisory_and_rendered(self):
        report = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', VALID),
                                    Entry('requirements.txt', b'requests\n')))
        self.assertEqual(report['status'], 'passed')
        self.assertIn('[ORANGE] warning dependency.unpinned', render(report, 'text'))
        self.assertEqual({TIERS[code] for code in TIERS if code.startswith('dependency.')}, {'ORANGE'})


class PluginNameTests(unittest.TestCase):
    """Plugin skills are not exempt from the community naming rule."""

    def entries(self, name, plugin=True):
        skill = VALID.replace(b'name: example', b'name: ' + name)
        license = Entry(PLUGIN_LICENSE, APACHE) if plugin else Entry('LICENSE', APACHE)
        return (license, Entry('SKILL.md', skill))

    def test_plugin_name_mismatch_is_error(self):
        for name in (b'other', b'Example', b'one-two-three-four-five'):
            with self.subTest(name=name):
                report = review('example', self.entries(name))
                self.assertIn('format.plugin_name', [item['code'] for item in report['findings']])
                self.assertEqual(report['status'], 'failed')

    def test_standalone_mismatch_stays_advisory(self):
        report = review('example', self.entries(b'other', plugin=False))
        found = [item['code'] for item in report['findings']]
        self.assertIn('format.name', found)
        self.assertNotIn('format.plugin_name', found)

    def test_matching_plugin_name_passes(self):
        report = review('example', self.entries(b'example'))
        self.assertNotIn('format.plugin_name', [item['code'] for item in report['findings']])


if __name__ == '__main__':
    unittest.main()

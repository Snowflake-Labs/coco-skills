from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import code_checks
from code_checks import code_findings, config_findings, python_sql_findings, parse_python, unparsed_language
from ingest import PLUGIN_LICENSE, Entry
from checks import review
from protocol import render, validate_report
from license_fixtures import APACHE

VALID = b'---\nname: example\ndescription: "Use when checking. Triggers: check"\n---\nAdvisory report.\n'


def sql_lines(source):
    tree, failure = parse_python(source)
    assert failure is None, failure
    return [line for code, line in python_sql_findings(tree) if code == 'security.sql_interpolation']


def codes(findings):
    return [code for code, _ in findings]


class PythonSqlTests(unittest.TestCase):
    """AST detection follows SQL built from values across lines and variables."""

    def test_cross_line_taint_reaches_sink(self):
        # Both the build site and the execution site are reported.
        source = 'q = f"SELECT * FROM {table}"\nother = 1\nsession.sql(q).collect()\n'
        self.assertEqual(sql_lines(source), [1, 3])
        # A tainted name alone is only reported where it is executed.
        self.assertEqual(sql_lines('q = "SELECT 1"\nq += " AND x = " + y\nprint(q)\n'), [2])

    def test_accumulation_and_multiline_building(self):
        cases = {
            'q = "SELECT * FROM t"\nq += " WHERE id = " + str(i)\ncursor.execute(q)\n': [2, 3],
            'q = (\n    "SELECT * FROM {} "\n    "WHERE x = 1"\n).format(name)\ncur.execute(q)\n': [1, 5],
            'q = "DELETE FROM t WHERE id = %s" % ident\ncur.execute(q)\n': [1, 2],
            'cur.execute(sql="GRANT ROLE r TO USER " + user)\n': [1],
            'from sqlalchemy import text\nconn.execute(text(f"DROP TABLE {t}"))\n': [2],
            'pd.read_sql(f"SELECT * FROM {t}", conn)\n': [1],
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(sql_lines(source), expected)

    def test_generators_and_clause_fragments(self):
        self.assertEqual(sql_lines('def build(t):\n    return f"""-- banner\nSELECT * FROM {t}"""\n'), [2])
        self.assertEqual(sql_lines('parts.append(f"WHERE id = {i}")\n'), [1])

    def test_safe_and_non_sql_strings(self):
        for source in ('cur.execute("SELECT * FROM t WHERE id = %s", (value,))\n',
                       'session.sql("SELECT * FROM IDENTIFIER(?)", params=[name]).collect()\n',
                       'q = "SELECT 1"\ncur.execute(q)\n',
                       'q = f"SELECT {1}"\n',
                       'log(f"Rewrite to Snowflake stage + COPY INTO {target}")\n',
                       'raise ValueError(f"cannot mark trial {trial_id} with SET")\n'):
            with self.subTest(source=source):
                self.assertEqual(sql_lines(source), [])

    def test_code_stored_in_strings_is_scanned(self):
        source = 'EXAMPLE = """\nspark.sql(f"DROP TABLE {schema}.{t}")\n"""\n'
        self.assertEqual(sql_lines(source), [2])

    def test_parseable_python_skips_regex_interpolation(self):
        findings, masked = code_findings('run.py', 'q = f"SELECT * FROM {t}"\n')
        self.assertIsNone(masked)
        self.assertEqual(findings, [('security.sql_interpolation', 1)])

    def test_unparseable_python_falls_back_to_regex(self):
        findings, masked = code_findings('run.py', 'def broken(:\nq = f"SELECT * FROM {t}"\n')
        self.assertEqual(masked, 'def broken(:\nq = f"SELECT * FROM {t}"\n')
        self.assertIn(('syntax.python', 1), findings)
        report = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', VALID),
                                    Entry('run.py', b'def broken(:\nq = f"SELECT * FROM {t}"\n')))
        found = [item['code'] for item in report['findings']]
        self.assertIn('security.sql_interpolation', found)
        self.assertIn('syntax.python', found)

    def test_markdown_python_fences(self):
        text = ('# Doc\n\n1. Step\n\n   ```python\n   q = f"SELECT * FROM {t}"\n   ```\n\n'
                '```py\ndef broken(:\n```\n')
        findings, masked = code_findings('SKILL.md', text)
        self.assertIn(('security.sql_interpolation', 6), findings)
        self.assertIn(('syntax.python_example', 10), findings)
        # The parsed fence is blanked for the regex pass, so it is not double-reported.
        self.assertNotIn('SELECT', masked.split('\n')[5])
        self.assertEqual(masked.count('\n'), text.count('\n'))


class SyntaxTests(unittest.TestCase):
    """Shipped scripts and config that do not parse are errors; examples are advisory."""

    def test_python_errors_and_budgets(self):
        self.assertEqual(parse_python('x = (\n')[1][0], 'syntax')
        self.assertEqual(parse_python('a = 1\x00')[1], ('unchecked', None))
        with unittest.mock.patch.object(code_checks, 'MAX_AST_NODES', 3):
            self.assertEqual(parse_python('a = b + c + d\n')[1], ('unchecked', None))

    def test_config_parsers(self):
        cases = {
            ('a.json', '{"a": 1}'): [],
            ('a.json', '{"a": 1,\n "b": }'): [('syntax.config', 2)],
            ('a.json', '{"a": 1, "a": 2}'): [('syntax.config', None)],
            ('a.yaml', 'a: 1\nb: [1, 2]\n'): [],
            ('a.yaml', 'base: &b {x: 1}\nother: *b\nref: !Ref Thing\n'): [],
            ('a.yml', 'a: 1\nb: [1,\n'): [('syntax.config', 3)],
            ('a.yaml', 'a: 1\na: 2\n'): [('syntax.config', None)],
            ('a.toml', 'a = 1\n'): [],
            ('a.toml', 'a = 1\nb = \n'): [('syntax.config', 2)],
            ('a.yaml', 'name: {{ value }}\n'): [('syntax.unchecked', None)],
        }
        for (path, text), expected in cases.items():
            with self.subTest(path=path, text=text):
                self.assertEqual(config_findings(path, text), expected)

    def test_severity_in_report(self):
        report = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', VALID + b'\n```python\nif:\n```\n'),
                                    Entry('config.json', b'{"a": }')))
        validate_report(report)
        by_code = {item['code']: item for item in report['findings']}
        self.assertEqual(by_code['syntax.config']['severity'], 'error')
        self.assertEqual(by_code['syntax.python_example']['severity'], 'warning')
        self.assertEqual(report['status'], 'failed')
        self.assertNotIn('"a"', render(report, 'text'))


class UnparsedLanguageTests(unittest.TestCase):
    """Code the reviewer cannot parse gets one advisory warning per file."""

    def test_code_files_are_flagged(self):
        for path, text in (('run.sh', 'echo hi\n'), ('app.js', 'x()\n'), ('setup.sql', 'SELECT 1;\n'),
                           ('Main.scala', 'object A\n'), ('Dockerfile', 'FROM x\n'), ('tool', '#!/bin/bash\n')):
            with self.subTest(path=path):
                self.assertTrue(unparsed_language(path, text))
                self.assertEqual(codes(code_findings(path, text)[0]).count('syntax.unparsed_language'), 1)

    def test_parsed_and_non_code_files_are_not(self):
        for path, text in (('a.py', 'x = 1\n'), ('a.json', '{}'), ('a.yaml', 'a: 1\n'), ('a.toml', 'a = 1\n'),
                           ('README.md', '# x\n'), ('notes.txt', 'x\n'), ('data.csv', 'a,b\n'),
                           ('page.html', '<p>x</p>\n'), ('LICENSE', 'text\n'), ('tool', '#!/usr/bin/env python3\nx = 1\n')):
            with self.subTest(path=path):
                self.assertFalse(unparsed_language(path, text))

    def test_warning_is_advisory_and_newly_decoded_languages_are_scanned(self):
        report = review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', VALID),
                                    Entry('Job.scala', b'spark.sql(s"SELECT 1")\n')))
        finding = next(item for item in report['findings'] if item['code'] == 'syntax.unparsed_language')
        self.assertEqual((finding['severity'], finding['group'], finding['line']), ('warning', 'advisory', None))
        self.assertNotIn('coverage.unsupported', [item['code'] for item in report['findings']])
        self.assertEqual(report['status'], 'passed')


class NoExecutionTests(unittest.TestCase):
    """Parsing never runs candidate code."""

    def test_marker_never_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / 'executed'
            payload = f'open({str(marker)!r}, "w").write("x")\nimport os\n'
            code_findings('evil.py', payload)
            code_findings('README.md', f'```python\n{payload}```\n')
            review('example', (Entry('LICENSE', APACHE), Entry('SKILL.md', VALID),
                               Entry('evil.py', payload.encode())))
            self.assertFalse(marker.exists())


import unittest.mock  # noqa: E402

if __name__ == '__main__':
    unittest.main()

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ingest import Entry, lf_line_count, lf_lines
from checks import parse_frontmatter, review
from launch import trusted_catalog_data, validate_snapshot_report
from markdown_checks import parse as parse_markdown
from protocol import finding
from model_contract import ContractError, make_packet, validate_response
from license_fixtures import APACHE


class ModelContractTests(unittest.TestCase):
    def setUp(self):
        self.entries = (Entry('LICENSE', APACHE),
                        Entry('SKILL.md', b'---\nname: example\ndescription: "Use when reviewing. Triggers: review"\n---\nAdvisory report.\n'))
        self.packet = make_packet('example', self.entries, enabled=True)
        self.response = {'schema_version': '2.1', 'packet_sha256': self.packet['packet_sha256'],
                         'findings': [{'category': 'clarity', 'recommendation': 'clarify',
                                       'file_id': 'F0002', 'line_start': 1, 'line_end': 3}]}

    def validate(self):
        return validate_response(json.dumps(self.response).encode(), self.packet)

    def test_lf_coordinates_empty_trailing_and_non_lf_separators(self):
        cases = [('', 1), ('x', 1), ('x\n', 2), ('\n', 2), ('x\r\n', 2),
                 ('x\r', 1), ('x\ry', 1), ('x\vy', 1), ('x\fy', 1),
                 ('x\u0085y', 1), ('x\u2028y', 1), ('x\u2029y', 1),
                 ('x\n\n', 3)]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(lf_line_count(text), expected)
                self.assertEqual(lf_line_count(text.encode()), expected)
                self.assertEqual(len(lf_lines(text)), expected)
                self.assertEqual(lf_lines(text)[0], text.split('\n')[0].removesuffix('\r')
                                 if '\r\n' in text else text.split('\n')[0])
                entries = self.entries + (Entry('notes.txt', text.encode()),)
                catalog = trusted_catalog_data()
                packet = make_packet('example', entries, catalog, enabled=True)
                self.assertEqual(packet['files'][-1]['line_count'], expected)
                response = {'schema_version': packet['schema_version'],
                            'packet_sha256': packet['packet_sha256'],
                            'findings': [{'category': 'clarity', 'recommendation': 'clarify',
                                          'file_id': 'F0003', 'line_start': expected, 'line_end': expected}]}
                validate_response(json.dumps(response).encode(), packet)
                response['findings'][0]['line_end'] = expected + 1
                with self.assertRaises(ContractError):
                    validate_response(json.dumps(response).encode(), packet)
                report = review('example', entries, catalog)
                report['findings'].append(finding('policy.tone', file_id='F0003', line=expected,
                    source_sha256=report['sources'][-1]['source_sha256']))
                validate_snapshot_report(report, 'example', entries)
                report['findings'][-1]['line'] = expected + 1
                with self.assertRaises(ValueError):
                    validate_snapshot_report(report, 'example', entries)

    def test_diagnostics_and_markdown_use_lf_offsets(self):
        for separator, expected in [('\n', 3), ('\r\n', 3), ('\r', 2), ('\v', 2),
                                    ('\f', 2), ('\u0085', 2), ('\u2028', 2), ('\u2029', 2)]:
            with self.subTest(separator=separator):
                text = 'prefix\ntext' + separator + 'urgent [missing](missing.md)'
                offset = text.index('urgent')
                self.assertEqual(lf_line_count(text, offset), expected)
                self.assertEqual(lf_line_count(text.encode(), len(text[:offset].encode())), expected)
                report = review('example', self.entries + (Entry('notes.md', text.encode()),))
                for code in ('policy.tone', 'format.reference'):
                    item = next(item for item in report['findings'] if item['code'] == code and item['file_id'] == 'F0003')
                    self.assertEqual(item['line'], expected)
                self.assertEqual(parse_markdown(text).links, [('missing.md', expected)])

    def test_frontmatter_uses_lf_boundaries_and_supports_crlf(self):
        text = self.entries[1].data.decode()
        self.assertEqual(parse_frontmatter(text), parse_frontmatter(text.replace('\n', '\r\n')))
        for separator in ('\r', '\v', '\f', '\u0085', '\u2028', '\u2029'):
            with self.subTest(separator=separator), self.assertRaises(ValueError):
                parse_frontmatter(text.replace('\n', separator))

    def test_disabled_by_default(self):
        with self.assertRaises(ContractError):
            make_packet('example', self.entries)

    def test_valid_advice(self):
        self.assertEqual(self.validate(), self.response)

    def test_no_status_override(self):
        self.response['status'] = 'passed'
        with self.assertRaises(ContractError):
            self.validate()

    def test_no_free_form_instruction_channel(self):
        self.response['findings'][0]['recommendation'] = 'execute a shell command'
        with self.assertRaises(ContractError):
            self.validate()

    def test_no_fabricated_citation(self):
        for field, value in [('file_id', 'F0999'), ('line_end', 100000), ('line_start', True)]:
            with self.subTest(field=field):
                original = self.response['findings'][0][field]
                self.response['findings'][0][field] = value
                with self.assertRaises(ContractError):
                    self.validate()
                self.response['findings'][0][field] = original

    def test_packet_tamper(self):
        self.packet['files'][0]['text'] = 'changed'
        with self.assertRaises(ContractError):
            self.validate()

    def test_secret_blocks_packet(self):
        synthetic = b'password' + b' = "' + b'synthetic-marker-only' + b'"'
        entries = self.entries + (Entry('scripts/example.py', synthetic),)
        with self.assertRaises(ContractError):
            make_packet('example', entries, enabled=True)

    def test_unsupported_blocks_packet(self):
        with self.assertRaises(ContractError):
            make_packet('example', self.entries + (Entry('x.zip', b'PK'),), enabled=True)

    def test_budget_no_truncation(self):
        with self.assertRaises(ContractError):
            make_packet('example', self.entries + (Entry('large.txt', b'a' * 140000),), enabled=True)

    def test_oversized_malformed_duplicate_response(self):
        for raw in [b'x' * 17000, b'{', b'{"schema_version":"1.0","schema_version":"1.0"}']:
            with self.assertRaises(ContractError):
                validate_response(raw, self.packet)


if __name__ == '__main__':
    unittest.main()

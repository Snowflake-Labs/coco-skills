"""Synthetic broker tests only; never run a candidate provider or a live model."""
import contextlib
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import editorial_broker as broker
import checks
import ingest
import model_contract
import packaging
import report_cache
from license_fixtures import APACHE
from offline_provider import SyntheticProvider, SyntheticTokenizer, TARGET

VALID = b'---\nname: example\ndescription: "Use when reviewing. Triggers: review"\n---\nAdvisory report.\n'
ENTRIES = (ingest.Entry('LICENSE', APACHE), ingest.Entry('SKILL.md', VALID))
WIRE = ingest.encode(ENTRIES, 'example')
# Pure test units; not actual prices or credit rates.
RATES = broker.Rates('fixture-unit', 2, 3, 7, True)
CONFIG = broker.Config(enabled=True, target=TARGET, allowed_targets=(TARGET,),
                       tokenizer_revision=SyntheticTokenizer.revision, rates=RATES,
                       budget=broker.Budget(spend_units=100000))


def consent(config=CONFIG, wire=WIRE, catalog=None):
    return broker.Consent(True, hashlib.sha256(wire).hexdigest(), broker.config_digest(config, catalog))


def assess(mode='valid', *, config=CONFIG, wire=WIRE, **kwargs):
    approval = consent(config, wire) if broker._configured(config) else None
    return broker.assess_in_isolation(wire, config=config, consent=approval,
                                      provider=SyntheticProvider(mode), **kwargs)


class BrokerTests(unittest.TestCase):
    def test_disabled_by_default_before_any_provider_or_worker(self):
        with patch.object(broker, '_supervise') as supervise:
            self.assertEqual(broker.assess_in_isolation(WIRE)['state'], 'disabled')
        supervise.assert_not_called()
        self.assertEqual(broker.Budget().input_tokens, 8000)
        self.assertEqual(broker.Budget().output_tokens, 1000)
        self.assertIsNone(broker.Budget().spend_units)

    def test_trusted_interface_only_and_http_unavailable_even_with_consent(self):
        class Duck:
            def assess(self, *args, **kwargs):
                raise AssertionError('Never call unregistered duck')
        for provider in (None, Duck(), object()):
            with patch.object(broker, '_supervise') as worker:
                result = broker.assess_in_isolation(WIRE, config=CONFIG, consent=consent(), provider=provider)
            self.assertEqual(result['state'], 'unavailable')
            worker.assert_not_called()
        target = replace(TARGET, endpoint='https://unapproved.invalid')
        config = replace(CONFIG, target=target, allowed_targets=(target,))
        with patch.object(broker, '_supervise') as worker:
            self.assertEqual(assess(config=config)['state'], 'unavailable')
        worker.assert_not_called()

    def test_exact_target_revision_allowlist_and_tokenizer_required(self):
        changes = [dict(allowed_targets=()), dict(allowed_targets=(replace(TARGET, revision='c' * 64),)),
                   dict(target=replace(TARGET, revision='latest')), dict(tokenizer_revision=None),
                   dict(tokenizer_revision='alias'), dict(target=None)]
        for change in changes:
            config = replace(CONFIG, **change)
            with self.subTest(change=change), patch.object(broker, '_supervise') as worker:
                self.assertEqual(assess(config=config)['state'], 'unavailable')
                worker.assert_not_called()
        for attribute, value in [('target', replace(TARGET, revision='c' * 64)), ('tokenizer', None)]:
            provider = SyntheticProvider()
            setattr(provider, attribute, value)
            self.assertEqual(broker._work(WIRE, CONFIG, None, provider)['state'], 'unavailable')
            self.assertEqual(provider.calls, 0)
        provider = SyntheticProvider()
        provider.tokenizer.revision = 'c' * 64
        self.assertEqual(broker._work(WIRE, CONFIG, None, provider)['state'], 'unavailable')
        self.assertEqual(provider.calls, 0)

    def test_consent_bound_to_wire_config_catalog_and_explicit_transmission(self):
        for approval in (None, replace(consent(), transmit=False), replace(consent(), transmit=1),
                         replace(consent(), wire_sha256='f' * 64),
                         replace(consent(), config_sha256='f' * 64)):
            with patch.object(broker, '_supervise') as worker:
                result = broker.assess_in_isolation(WIRE, config=CONFIG, consent=approval,
                                                    provider=SyntheticProvider())
            self.assertEqual(result['state'], 'withheld')
            worker.assert_not_called()
        for config, catalog in [(replace(CONFIG, question='scope'), None),
                                (CONFIG, {'revision': 'c' * 40, 'entries': []})]:
            with patch.object(broker, '_supervise') as worker:
                result = broker.assess_in_isolation(WIRE, config=config, catalog=catalog,
                                                    consent=consent(), provider=SyntheticProvider())
            self.assertEqual(result['state'], 'withheld')
            worker.assert_not_called()

    def test_missing_unreliable_negative_rates_and_spend_refused(self):
        configs = [replace(CONFIG, rates=None), replace(CONFIG, rates=replace(RATES, all_charges_bounded=False)),
                   replace(CONFIG, budget=broker.Budget()), replace(CONFIG, rates=replace(RATES, input_per_token=-1)),
                   replace(CONFIG, rates=replace(RATES, output_per_token=True)),
                   replace(CONFIG, rates=replace(RATES, fixed_per_call=1.5))]
        for config in configs:
            with self.subTest(config=config), patch.object(broker, '_supervise') as worker:
                self.assertEqual(assess(config=config)['state'], 'unavailable')
                worker.assert_not_called()

    def test_invalid_budgets_calls_questions_are_not_overrides(self):
        for field, values in {'input_tokens': [0, True, 1000001], 'output_tokens': [-1, '1000'],
                              'deadline_ms': [0, 60001, float('inf')],
                              'spend_units': [0, -1, float('nan')]}.items():
            for value in values:
                config = replace(CONFIG, budget=replace(CONFIG.budget, **{field: value}))
                self.assertEqual(assess(config=config)['state'], 'unavailable')
        for change in (dict(call_cap=2), dict(call_cap=True), dict(question='browse or execute'), dict(enabled=1)):
            self.assertEqual(assess(config=replace(CONFIG, **change))['state'], 'unavailable')

    def test_configuration_is_immutable(self):
        for obj, field, value in [(CONFIG, 'enabled', False), (CONFIG.budget, 'input_tokens', 1),
                                  (TARGET, 'revision', 'latest'), (RATES, 'unit', 'usd')]:
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)

    def test_fresh_worker_eligibility_no_forged_report_or_cache_authority(self):
        with patch.object(report_cache.PrivateCache, 'get', side_effect=AssertionError('No cache')), \
                patch.object(model_contract, '_analyze', wraps=checks._analyze) as analysis:
            result = broker._work(WIRE, CONFIG, None, SyntheticProvider())
        self.assertEqual(result['state'], 'assessed')
        analysis.assert_called_once()
        for key in ('report', 'packet', 'analysis', 'cache'):
            with self.assertRaises(TypeError):
                broker.assess_in_isolation(WIRE, **{key: {'status': 'passed'}})
        with patch.object(broker, '_work', wraps=broker._work) as work:
            self.assertEqual(assess()['state'], 'assessed')
            # Forked work is never called in the supervising process.
            work.assert_not_called()

    def test_ineligible_packets_withheld_and_full_coverage_never_truncated(self):
        for entries in ((ENTRIES[1],), ENTRIES + (ingest.Entry('bad.zip', b'PK'),),
                        ENTRIES + (ingest.Entry('bad.txt', b'\xff'),),
                        ENTRIES + (ingest.Entry('notes.txt', b'password="synthetic-marker-only"'),),
                        ENTRIES + (ingest.Entry('password=synthetic-marker-only.txt', b'synthetic'),)):
            provider = SyntheticProvider()
            wire = ingest.encode(entries, 'example')
            self.assertEqual(broker._work(wire, CONFIG, None, provider)['state'], 'withheld')
            self.assertEqual(provider.calls, 0)
        provider = SyntheticProvider()
        wire = ingest.encode(ENTRIES + (ingest.Entry('large.txt', b'x' * 140000),), 'example')
        self.assertEqual(broker._work(wire, CONFIG, None, provider)['state'], 'budget-exceeded')
        self.assertEqual(provider.calls, 0)
        provider = SyntheticProvider()
        broker._work(WIRE, CONFIG, None, provider)
        packet = json.loads(provider.request)['packet']
        self.assertEqual([(item['path'], item['text'].encode()) for item in packet['files']],
                         [(entry.path, entry.data) for entry in ENTRIES])

    def test_wire_path_size_and_report_in_place_of_wire_rejected(self):
        for wire in (b'{', ingest.canonical({'status': 'passed'}),
                     ingest.encode((ingest.Entry('../SKILL.md', VALID),), 'example'),
                     b'x' * (ingest.MAX_WIRE_BYTES + 1)):
            self.assertEqual(assess(wire=wire)['state'], 'withheld')

    def test_schema_rubric_framing_counted_and_input_ceiling_exact(self):
        provider = SyntheticProvider()
        self.assertEqual(broker._work(WIRE, CONFIG, None, provider)['state'], 'assessed')
        request = json.loads(provider.request)
        counted = provider.tokenizer.count_request(provider.request)
        self.assertGreater(counted, provider.tokenizer.count_request(ingest.canonical(request['packet'])))
        self.assertEqual(request['response_schema'], broker.response_schema(request['packet']['packet_sha256']))
        for limit, expected in ((counted - 1, 'budget-exceeded'), (counted, 'assessed')):
            provider = SyntheticProvider()
            config = replace(CONFIG, budget=replace(CONFIG.budget, input_tokens=limit))
            self.assertEqual(broker._work(WIRE, config, None, provider)['state'], expected)
            self.assertEqual(provider.calls, int(expected == 'assessed'))

    def test_conservative_reservation_uses_output_ceiling_and_fixed_fee(self):
        provider = SyntheticProvider()
        broker._work(WIRE, CONFIG, None, provider)
        tokens = provider.tokenizer.count_request(provider.request)
        reserve = tokens * 2 + CONFIG.budget.output_tokens * 3 + 7
        for cap, state in ((reserve - 1, 'budget-exceeded'), (reserve, 'assessed')):
            provider = SyntheticProvider()
            config = replace(CONFIG, budget=replace(CONFIG.budget, spend_units=cap))
            self.assertEqual(broker._work(WIRE, config, None, provider)['state'], state)
            self.assertEqual(provider.calls, int(state == 'assessed'))

    def test_bad_tokenizer_counts_fail_closed_before_call(self):
        for value in (None, True, -1, 0, float('nan'), '8000'):
            provider = SyntheticProvider()
            with patch.object(provider.tokenizer, 'count_request', return_value=value):
                self.assertEqual(broker._work(WIRE, CONFIG, None, provider)['state'], 'unavailable')
            self.assertEqual(provider.calls, 0)

    def test_visible_output_and_reported_usage_budgets(self):
        for mode in ('excess-input', 'excess-output'):
            self.assertEqual(assess(mode)['state'], 'budget-exceeded')
        for mode in ('bad-usage', 'understated-output'):
            self.assertEqual(assess(mode)['state'], 'invalid-response')
        config = replace(CONFIG, budget=replace(CONFIG.budget, output_tokens=1))
        self.assertEqual(assess(config=config)['state'], 'budget-exceeded')

    def test_invalid_response_no_prose_citation_override_or_custom_objects(self):
        for mode in ('override', 'instruction', 'citation', 'wrong-file', 'wrong-hash', 'mutate-packet',
                     'oversize', 'malformed', 'duplicate', 'non-bytes', 'custom-object'):
            with self.subTest(mode=mode):
                result = assess(mode)
                self.assertEqual(result['state'], 'invalid-response')
                self.assertIsNone(result['advice'])
                self.assertNotIn('CANARY', broker.render(result))

    def test_one_call_no_retry_even_on_failure(self):
        for mode in ('valid', 'malformed', 'citation'):
            provider = SyntheticProvider(mode)
            broker._work(WIRE, CONFIG, None, provider)
            self.assertEqual(provider.calls, 1)
        provider = SyntheticProvider('error')
        with self.assertRaises(RuntimeError):
            broker._work(WIRE, CONFIG, None, provider)
        self.assertEqual(provider.calls, 1)
        result = assess('error')
        self.assertEqual(result['state'], 'unavailable')
        self.assertNotIn('CANARY', broker.render(result))

    def test_output_fixed_and_deterministic_report_unchanged(self):
        before = checks.review('example', ENTRIES)
        result = assess()
        self.assertEqual(result['state'], 'assessed')
        self.assertEqual(result['advice'], model_contract.validate_response(
            ingest.canonical(result['advice']), model_contract.make_packet('example', ENTRIES, enabled=True)))
        self.assertIn('Consider clarifying', broker.render(result))
        self.assertEqual(checks.review('example', ENTRIES), before)
        self.assertEqual(before['model'], 'disabled')
        for text in ('SKILL.md', 'Apache License', 'Use when reviewing'):
            self.assertNotIn(text, broker.render(result))

    def test_all_states_and_renderer_rejects_forged_text(self):
        self.assertEqual(set(broker.MESSAGES), {'disabled', 'withheld', 'cache-hit', 'assessed',
                                              'invalid-response', 'unavailable', 'budget-exceeded'})
        valid = assess()
        for state in broker.MESSAGES:
            value = broker._result(state, valid['advice'] if state in ('cache-hit', 'assessed') else None)
            self.assertTrue(broker.render(value).startswith(broker.MESSAGES[state]))
        for mutation in ({'message': 'CANARY'}, {'state': 'CANARY'}, {'advice': {'text': 'CANARY'}},
                         {'extra': 'CANARY'}):
            self.assertEqual(broker.render(valid | mutation), broker.MESSAGES['invalid-response'])

    def test_consent_fingerprint_changes_for_every_configuration_field(self):
        original = broker.config_digest(CONFIG)
        changes = dict(enabled=False, target=replace(TARGET, revision='c' * 64), allowed_targets=(),
                       tokenizer_revision='c' * 64, rates=replace(RATES, fixed_per_call=8),
                       budget=replace(CONFIG.budget, spend_units=99999), question='scope', call_cap=2)
        for field, value in changes.items():
            self.assertNotEqual(original, broker.config_digest(replace(CONFIG, **{field: value})), field)
        self.assertNotEqual(original, broker.config_digest(CONFIG, {'entries': []}))

    def test_provider_and_tokenizer_stalls_both_supervised_and_reaped(self):
        config = replace(CONFIG, budget=replace(CONFIG.budget, deadline_ms=150))
        original_wait = os.waitpid
        reaped = []
        def wait(process, options):
            result = original_wait(process, options)
            reaped.append(result)
            return result
        for phase in ('provider', 'tokenizer', 'eligibility'):
            provider = SyntheticProvider('stall' if phase == 'provider' else 'valid')
            target = (patch.object(provider.tokenizer, 'count_request', side_effect=lambda raw: time.sleep(5))
                      if phase == 'tokenizer' else
                      patch.object(broker, 'make_packet', side_effect=lambda *args, **kwargs: time.sleep(5))
                      if phase == 'eligibility' else contextlib.nullcontext())
            started = time.monotonic()
            with target, patch.object(broker.os, 'waitpid', side_effect=wait):
                result = broker.assess_in_isolation(WIRE, config=config, consent=consent(config), provider=provider)
            self.assertEqual(result['state'], 'unavailable')
            self.assertLess(time.monotonic() - started, 2)
            self.assertTrue(os.WIFSIGNALED(reaped[-1][1]))
            self.assertEqual(os.WTERMSIG(reaped[-1][1]), signal.SIGKILL)
            with self.assertRaises(ProcessLookupError):
                os.kill(reaped[-1][0], 0)

    def test_worker_stdout_stderr_environment_and_no_pickle(self):
        with patch.dict(os.environ, {'SYNTHETIC_MARKER': 'NOT-A-SECRET'}):
            self.assertEqual(assess('no-environment')['state'], 'assessed')
        with patch('pickle.loads', side_effect=AssertionError('No pickle')), \
                patch('pickle.dumps', side_effect=AssertionError('No pickle')):
            self.assertEqual(assess('noisy')['state'], 'assessed')
        # OS pipes, not Python redirect_stdout, verify child descriptor egress.
        read_fd, write_fd = os.pipe()
        saved = [os.dup(1), os.dup(2)]
        try:
            os.dup2(write_fd, 1)
            os.dup2(write_fd, 2)
            result = assess('noisy')
        finally:
            for descriptor, original in zip((1, 2), saved):
                os.dup2(original, descriptor)
                os.close(original)
            os.close(write_fd)
        try:
            self.assertEqual(os.read(read_fd, 4096), b'')
        finally:
            os.close(read_fd)
        self.assertEqual(result['state'], 'assessed')

    def test_bounded_ipc_rejects_oversize_and_malformed_without_deserialization(self):
        def bad_child(write_fd, *args):
            try:
                os.setsid()
                os.write(write_fd, b'x' * (broker.MAX_IPC_BYTES + 1))
            finally:
                os._exit(0)
        with patch.object(broker, '_child', side_effect=bad_child):
            self.assertEqual(assess()['state'], 'invalid-response')
        def malformed(write_fd, *args):
            try:
                os.setsid()
                os.write(write_fd, b'{"state":"CANARY"}')
            finally:
                os._exit(0)
        with patch.object(broker, '_child', side_effect=malformed):
            self.assertEqual(assess()['state'], 'unavailable')

    def test_fork_failure_fixed_no_fallback(self):
        with patch.object(broker.os, 'fork', side_effect=OSError('CANARY')), \
                patch.object(broker, '_work') as work:
            self.assertEqual(assess()['state'], 'unavailable')
        work.assert_not_called()

    def test_tokenizer_errors_and_response_counting_deadline_are_fixed(self):
        for method in ('count_request', 'count_output'):
            provider = SyntheticProvider()
            with patch.object(provider.tokenizer, method, side_effect=RuntimeError('CANARY')):
                result = broker.assess_in_isolation(WIRE, config=CONFIG, consent=consent(), provider=provider)
            self.assertEqual(result['state'], 'unavailable')
            self.assertNotIn('CANARY', broker.render(result))
        provider = SyntheticProvider()
        config = replace(CONFIG, budget=replace(CONFIG.budget, deadline_ms=150))
        # Delay only the broker's second count, after the double constructs usage.
        calls = 0
        original = provider.tokenizer.count_output
        def count(raw):
            nonlocal calls
            calls += 1
            if calls > 1:
                time.sleep(5)
            return original(raw)
        started = time.monotonic()
        with patch.object(provider.tokenizer, 'count_output', side_effect=count):
            result = broker.assess_in_isolation(WIRE, config=config, consent=consent(config), provider=provider)
        self.assertEqual(result['state'], 'unavailable')
        self.assertLess(time.monotonic() - started, 2)

    def test_request_and_catalog_bounds_prevent_provider_call(self):
        provider = SyntheticProvider()
        with patch.object(broker, 'MAX_REQUEST_BYTES', 100):
            self.assertEqual(broker._work(WIRE, CONFIG, None, provider)['state'], 'budget-exceeded')
        self.assertEqual(provider.calls, 0)
        catalog = {'synthetic': 'x' * (broker.MAX_REQUEST_BYTES + 1)}
        with patch.object(broker, '_supervise') as worker:
            result = broker.assess_in_isolation(WIRE, config=CONFIG, consent=consent(CONFIG, WIRE, catalog),
                                                catalog=catalog, provider=provider)
        self.assertEqual(result['state'], 'budget-exceeded')
        worker.assert_not_called()

    def test_worker_closes_inherited_nonstandard_descriptors(self):
        read_fd, write_fd = os.pipe()
        try:
            # Reviewed synthetic closure assertion, not candidate code.
            class DescriptorProvider(SyntheticProvider):
                def assess(self, request, **kwargs):
                    for descriptor in (read_fd, write_fd):
                        try:
                            os.fstat(descriptor)
                        except OSError:
                            continue
                        raise AssertionError('Inherited descriptor remains open')
                    return super().assess(request, **kwargs)
            result = broker.assess_in_isolation(WIRE, config=CONFIG, consent=consent(), provider=DescriptorProvider())
            self.assertEqual(result['state'], 'assessed')
            os.fstat(read_fd)
            os.fstat(write_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_broker_is_packaged_but_no_launcher_or_runtime_entrypoint_hook(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn(packaging.REVIEWER + 'scripts/editorial_broker.py', packaging.LOCAL_FILES)
        for path in ('launch.py', 'entrypoint.py', 'run.py'):
            self.assertNotIn('editorial_broker', (root / 'scripts' / path).read_text())
        source = (root / 'scripts/editorial_broker.py').read_text()
        for forbidden in ('import pickle', 'import requests', 'import http', 'import socket',
                          'import subprocess', 'eval(', 'exec('):
            self.assertNotIn(forbidden, source)


class EditorialBenchmarkTests(unittest.TestCase):
    def setUp(self):
        import benchmark_editorial
        self.harness = benchmark_editorial
        self.corpus = self.harness.corpus()
        # Arithmetic fixtures ONLY, not actual human judgments or evidence.
        self.ratings = {'format': self.harness.FORMAT,
                        'corpus_sha256': self.harness.corpus_digest(self.corpus),
                        'ratings': [{'case_id': case['id'], 'human_reviewed': True,
                                     'accepted': index % 2 == 0, 'usefulness': 5 if index % 2 == 0 else 1,
                                     'false_positive': index % 2 != 0, 'review_seconds': 10}
                                    for index, case in enumerate(self.corpus['cases'])]}

    def test_no_human_judgments_means_unmeasured_not_fabricated_quality(self):
        result = self.harness.score(self.corpus)
        self.assertEqual(result['status'], 'unmeasured')
        self.assertEqual(result['expected'], 8)
        self.assertEqual(result['rated'], 0)
        self.assertIsNone(result['metrics'])
        self.assertIsNone(result['by_dimension'])
        self.assertEqual(result['live_model_calls'], 0)
        self.assertFalse(result['human_judgments_verified'])
        self.assertEqual({case['dimension'] for case in self.corpus['cases']},
                         {'scope', 'clarity', 'usefulness', 'differentiation'})

    def test_deterministic_scoring_uses_only_supplied_ratings(self):
        result = self.harness.score(self.corpus, self.ratings)
        self.assertEqual(result['status'], 'human-rated-unverified')
        self.assertFalse(result['human_judgments_verified'])
        self.assertEqual(result['metrics'], {'suggestions': 8, 'accepted': 4,
                         'acceptance_rate': '1/2', 'mean_usefulness': '3',
                         'mean_usefulness_per_accepted': '5', 'false_positive_rate': '1/2',
                         'review_seconds': 80})
        self.ratings['ratings'].reverse()
        self.assertEqual(self.harness.score(self.corpus, self.ratings), result)
        self.assertEqual(set(result['by_dimension']), set(broker.QUESTIONS))

    def test_zero_accepted_no_invented_denominator(self):
        for rating in self.ratings['ratings']:
            rating['accepted'] = False
        result = self.harness.score(self.corpus, self.ratings)
        self.assertIsNone(result['metrics']['mean_usefulness_per_accepted'])

    def test_missing_duplicate_unknown_and_nonhuman_ratings_refused(self):
        import copy
        for mutation in ('missing', 'duplicate', 'unknown', 'nonhuman', 'wrong-corpus', 'extra'):
            value = copy.deepcopy(self.ratings)
            if mutation == 'missing':
                value['ratings'].pop()
            elif mutation == 'duplicate':
                value['ratings'][0] = value['ratings'][1]
            elif mutation == 'unknown':
                value['ratings'][0]['case_id'] = 'unknown'
            elif mutation == 'nonhuman':
                value['ratings'][0]['human_reviewed'] = False
            elif mutation == 'wrong-corpus':
                value['corpus_sha256'] = 'f' * 64
            else:
                value['raw_model_prose'] = 'CANARY'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.harness.score(self.corpus, value)

    def test_invalid_boolean_bounds_and_contradictory_judgments_refused(self):
        import copy
        for field, value in [('usefulness', True), ('usefulness', 6), ('accepted', 1),
                             ('false_positive', True), ('review_seconds', -1), ('review_seconds', 3601)]:
            ratings = copy.deepcopy(self.ratings)
            ratings['ratings'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.harness.score(self.corpus, ratings)

    def test_cli_unmeasured_exit3_and_fixed_error_no_input_echo(self):
        import io
        with patch.object(sys, 'argv', ['benchmark']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.harness.main(), 3)
        self.assertEqual(json.loads(output.getvalue())['status'], 'unmeasured')
        with patch.object(sys, 'argv', ['benchmark', '--judgments', '/synthetic/CANARY']), \
                patch.object(self.harness, 'read_json', side_effect=ValueError('CANARY')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.harness.main(), 3)
        self.assertNotIn('CANARY', output.getvalue())


if __name__ == '__main__':
    unittest.main()

"""Synthetic-only service-local cache tests; no provider network or Docker."""
import copy
from dataclasses import asdict, replace
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import advisory_cache as cache_module
import checks
import editorial_broker as broker
import ingest
import model_contract
import packaging
import protocol
import report_cache
from offline_provider import SyntheticProvider
from test_workstream8 import CONFIG, ENTRIES, VALID, WIRE, consent


class AdvisoryCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.directory, self.trusted = [self.root / name for name in ('source', 'private', 'trusted')]
        for path in (self.source, self.directory, self.trusted):
            path.mkdir(mode=0o700)
        self.settings = cache_module.CacheConfig(str(self.directory), str(self.source), str(self.trusted),
                                                hashlib.sha256(WIRE).hexdigest(), 'c' * 64)
        self.config = replace(CONFIG, advisory_cache=self.settings)
        self.packet = model_contract.make_packet('example', ENTRIES, enabled=True)
        self.advice = {'schema_version': protocol.VERSION, 'packet_sha256': self.packet['packet_sha256'],
                       'findings': [{'category': 'clarity', 'recommendation': 'clarify',
                                     'file_id': 'F0002', 'line_start': 1, 'line_end': 1}]}
        self.cache = cache_module.AdvisoryCache(self.settings)
        self.addCleanup(self.cache.close)
        self.provider = self.provider_for_cache()
        # Capture the exact request using the existing no-cache offline double.
        broker._work(WIRE, CONFIG, None, self.provider)
        self.request = self.provider.request
        self.provider = self.provider_for_cache()
        self.key = cache_module.key_for(broker.cache_context(WIRE, self.config, None, self.packet, self.request))
        self.path = self.directory / self.cache.slot(self.key)

    def provider_for_cache(self, mode='valid'):
        provider = SyntheticProvider(mode)
        provider.cache_identity = 'c' * 64
        return provider

    def seed(self):
        self.assertTrue(self.cache.put(self.key, self.advice, self.packet))

    def work(self, config=None, wire=WIRE, approval=None, provider=None):
        config = config or self.config
        return broker._work(wire, config, None, provider or self.provider,
                            consent(config, wire) if approval is None else approval)

    def test_disabled_and_no_cache_default_never_touch_files(self):
        with patch.object(cache_module, 'AdvisoryCache', side_effect=AssertionError('No cache')):
            self.assertEqual(broker.assess_in_isolation(WIRE)['state'], 'disabled')
            self.assertEqual(broker._work(WIRE, CONFIG, None, self.provider)['state'], 'assessed')
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_miss_defaults_to_no_call_and_explicit_one_call_populates(self):
        self.assertEqual(self.work()['state'], 'unavailable')
        self.assertEqual(self.provider.calls, 0)
        self.assertEqual(list(self.directory.iterdir()), [])
        config = replace(self.config, advisory_cache=replace(self.settings, allow_call_on_miss=True))
        self.assertEqual(self.work(config)['state'], 'assessed')
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(len(list(self.directory.iterdir())), 1)
        second = self.provider_for_cache('error')
        self.assertEqual(self.work(config, provider=second)['state'], 'cache-hit')
        self.assertEqual(second.calls, 0)

    def test_hit_fresh_eligibility_no_provider_and_report_unchanged(self):
        self.seed()
        before = checks.review('example', ENTRIES)
        with patch.object(model_contract, '_analyze', wraps=checks._analyze) as analyze, \
                patch.object(self.provider, 'assess', side_effect=AssertionError('No call')):
            result = self.work()
        analyze.assert_called_once()
        self.assertEqual(result['state'], 'cache-hit')
        self.assertEqual(result['advice'], self.advice)
        self.assertIn('NON-AUTHORITATIVE', broker.render(result))
        self.assertIn('not a fresh', broker.render(result))
        self.assertEqual(checks.review('example', ENTRIES), before)
        self.assertEqual(before['model'], 'disabled')
        with self.assertRaises(ValueError):
            protocol.validate_report(result)

    def test_real_supervised_api_cache_hit_and_fill(self):
        config = replace(self.config, advisory_cache=replace(self.settings, allow_call_on_miss=True))
        result = broker.assess_in_isolation(WIRE, config=config, consent=consent(config),
                                            provider=self.provider_for_cache())
        self.assertEqual(result['state'], 'assessed')
        with patch.object(SyntheticProvider, 'assess', side_effect=AssertionError('No call')):
            result = broker.assess_in_isolation(WIRE, config=config, consent=consent(config),
                                                provider=self.provider_for_cache())
        self.assertEqual(result['state'], 'cache-hit')

    def test_consent_withdrawn_or_config_changed_precedes_cache(self):
        self.seed()
        for approval in (replace(consent(self.config), transmit=False),
                         replace(consent(self.config), wire_sha256='f' * 64), consent(CONFIG)):
            with patch.object(cache_module, 'AdvisoryCache', side_effect=AssertionError('No lookup')):
                self.assertEqual(self.work(approval=approval)['state'], 'withheld')
                result = broker.assess_in_isolation(WIRE, config=self.config, consent=approval,
                                                    provider=self.provider)
                self.assertEqual(result['state'], 'withheld')
        with patch.object(cache_module, 'AdvisoryCache', side_effect=AssertionError('No lookup')):
            self.assertEqual(broker._work(WIRE, self.config, None, self.provider)['state'], 'withheld')
        self.assertEqual(self.provider.calls, 0)

    def test_changed_eligibility_secrets_coverage_and_wire_precede_cache(self):
        self.seed()
        cases = [(ENTRIES[1],), ENTRIES + (ingest.Entry('bad.zip', b'PK'),),
                 ENTRIES + (ingest.Entry('bad.txt', b'\xff'),),
                 ENTRIES + (ingest.Entry('notes.txt', b'password="synthetic-marker-only"'),),
                 ENTRIES + (ingest.Entry('password=synthetic-marker-only.txt', b'synthetic'),)]
        wires = [ingest.encode(entries, 'example') for entries in cases]
        wires += [b'{', ingest.encode((ingest.Entry('../SKILL.md', VALID),), 'example')]
        for wire in wires:
            config = replace(self.config, advisory_cache=replace(self.settings,
                             source_wire_sha256=hashlib.sha256(wire).hexdigest()))
            with self.subTest(wire_hash=hashlib.sha256(wire).hexdigest()), \
                    patch.object(cache_module, 'AdvisoryCache', side_effect=AssertionError('No lookup')):
                self.assertEqual(self.work(config, wire)['state'], 'withheld')
        with patch.object(broker, 'make_packet', side_effect=model_contract.ContractError('ineligible')), \
                patch.object(cache_module, 'AdvisoryCache', side_effect=AssertionError('No lookup')):
            self.assertEqual(self.work()['state'], 'withheld')
        self.assertEqual(self.provider.calls, 0)

    def test_source_and_provider_binding_required_before_cache(self):
        for settings in (replace(self.settings, source_wire_sha256='f' * 64),
                         replace(self.settings, provider_config_sha256='latest'),
                         replace(self.settings, allow_call_on_miss=1),
                         replace(self.settings, directory=None)):
            with patch.object(cache_module, 'AdvisoryCache', side_effect=AssertionError('No lookup')):
                self.assertEqual(self.work(replace(self.config, advisory_cache=settings))['state'], 'unavailable')
        for identity in (None, 'd' * 64):
            self.provider.cache_identity = identity
            with patch.object(cache_module, 'AdvisoryCache', side_effect=AssertionError('No lookup')):
                self.assertEqual(self.work()['state'], 'unavailable')
        self.assertEqual(self.provider.calls, 0)

    def test_token_and_spending_checks_precede_lookup(self):
        self.seed()
        for budget in (replace(CONFIG.budget, input_tokens=1), replace(CONFIG.budget, spend_units=1)):
            with patch.object(cache_module, 'AdvisoryCache', side_effect=AssertionError('No lookup')):
                self.assertEqual(self.work(replace(self.config, budget=budget))['state'], 'budget-exceeded')
        with patch.object(self.provider.tokenizer, 'count_output', return_value=1001):
            self.assertEqual(self.work()['state'], 'budget-exceeded')
        self.assertEqual(self.provider.calls, 0)

    def test_context_invalidation_every_leaf(self):
        context = broker.cache_context(WIRE, self.config, None, self.packet, self.request)
        original = cache_module.key_for(context)
        def paths(value, prefix=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield from paths(child, prefix + (key,))
            elif isinstance(value, (tuple, list)):
                for index, child in enumerate(value):
                    yield from paths(child, prefix + (index,))
            else:
                yield prefix
        for path in paths(context):
            changed = copy.deepcopy(context)
            # asdict keeps allowed_targets a tuple; its elements are mutable dicts.
            current = changed
            for field in path[:-1]:
                current = current[field]
            current[path[-1]] = 'changed-synthetic-dimension'
            with self.subTest(path=path):
                self.assertNotEqual(original, cache_module.key_for(changed))
        self.assertEqual(set(context['config']), set(asdict(self.config)))
        self.assertEqual(set(context['limits']), {'wire', 'packet', 'request', 'response', 'ipc', 'files',
                                                'entries', 'depth', 'file_bytes', 'total',
                                                'cache_bytes', 'cache_slots', 'cache_ttl'})

    def test_real_packet_content_paths_rubric_rules_catalog_schema_invalidate(self):
        original = self.key
        cases = [('other', ENTRIES), ('example', ENTRIES + (ingest.Entry('notes.txt', b'example'),)),
                 ('example', (ENTRIES[0], ingest.Entry('SKILL.md', VALID + b'Changed'))),
                 ('example', ENTRIES + (ingest.Entry('renamed.txt', b'example'),))]
        for name, entries in cases:
            packet = model_contract.make_packet(name, entries, enabled=True)
            wire = ingest.encode(entries, name)
            context = broker.cache_context(wire, self.config, None, packet, self.request)
            self.assertNotEqual(original, cache_module.key_for(context))
        for module, field, value in ((model_contract, 'RUBRIC', 'Changed rubric'),
                                      (protocol, 'RULES_VERSION', 'changed')):
            with patch.object(module, field, value):
                packet = model_contract.make_packet('example', ENTRIES, enabled=True)
                self.assertNotEqual(original, cache_module.key_for(
                    broker.cache_context(WIRE, self.config, None, packet, self.request)))
        # Same revision, different complete catalog content still invalidates.
        for catalog in ({'revision': 'a' * 40, 'entries': []},
                        {'revision': 'a' * 40, 'entries': [], 'coverage': 'different'}):
            self.assertNotEqual(original, cache_module.key_for(
                broker.cache_context(WIRE, self.config, catalog, self.packet, self.request)))
        for field, value in (('VERSION', 'next-schema'), ('BROKER_VERSION', 'next-broker'),
                             ('GENERATION', {'seed': 1}), ('DATA_HANDLING', {'raw_retention': 'changed'})):
            with patch.object(broker, field, value):
                self.assertNotEqual(original, cache_module.key_for(
                    broker.cache_context(WIRE, self.config, None, self.packet, self.request)))

    def test_key_mismatch_valid_advice_and_same_revision_catalog_miss(self):
        self.seed()
        changed = replace(self.config, question='scope')
        self.assertEqual(self.work(changed)['state'], 'unavailable')
        first = {'revision': 'a' * 40, 'entries': []}
        second = first | {'synthetic': 'different public metadata'}
        keys = [cache_module.key_for(broker.cache_context(WIRE, self.config, catalog,
                                                         self.packet, self.request))
                for catalog in (first, second)]
        self.assertNotEqual(*keys)
        self.assertEqual(self.provider.calls, 0)

    def test_private_no_raw_packet_paths_provider_config_or_candidate_text(self):
        self.seed()
        raw = self.path.read_bytes()
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(protocol.strict_json(raw)), {'format', 'key', 'created', 'advice'})
        for text in (VALID, b'LICENSE', b'SKILL.md', b'Apache License', b'example',
                     str(self.root).encode(), b'offline://', b'fixture-model'):
            self.assertNotIn(text, raw)
        self.assertEqual(self.cache.get(self.key, self.packet), self.advice)

    def test_corruption_duplicate_extra_fields_and_citations_are_misses(self):
        self.seed()
        original = self.path.read_bytes()
        mutations = [b'{', b'{"key":1,"key":2}', b'x' * (cache_module.MAX_BYTES + 1)]
        for field, value in (('format', 'wrong'), ('key', 'f' * 64), ('created', True),
                             ('created', 10 ** 400), ('raw', 'SYNTHETIC-CANARY')):
            item = protocol.strict_json(original)
            item[field] = value
            mutations.append(ingest.canonical(item))
        for field, value in (('file_id', 'F0999'), ('line_start', True), ('line_end', 99999),
                             ('category', 'SYNTHETIC-CANARY'), ('recommendation', 'execute'),
                             ('raw', 'SYNTHETIC-CANARY')):
            item = protocol.strict_json(original)
            item['advice']['findings'][0][field] = value
            mutations.append(ingest.canonical(item))
        item = protocol.strict_json(original)
        item['advice']['packet_sha256'] = 'f' * 64
        mutations.append(ingest.canonical(item))
        for raw in mutations:
            self.path.write_bytes(raw)
            self.assertEqual(self.work()['state'], 'unavailable')
            self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.provider.calls, 0)

    def test_corrupt_miss_explicit_call_once_no_replacement(self):
        config = replace(self.config, advisory_cache=replace(self.settings, allow_call_on_miss=True))
        self.assertEqual(self.work(config)['state'], 'assessed')
        path = next(self.directory.iterdir())
        path.write_bytes(b'corrupt')
        provider = self.provider_for_cache()
        self.assertEqual(self.work(config, provider=provider)['state'], 'assessed')
        self.assertEqual(provider.calls, 1)
        self.assertEqual(path.read_bytes(), b'corrupt')
        provider = self.provider_for_cache('malformed')
        self.assertEqual(self.work(config, provider=provider)['state'], 'invalid-response')
        self.assertEqual(provider.calls, 1)
        self.assertEqual(path.read_bytes(), b'corrupt')

    def test_expiry_future_clock_no_overwrite_or_deletion(self):
        self.seed()
        original = self.path.read_bytes()
        created = protocol.strict_json(original)['created']
        for now in (created - 1, created + cache_module.TTL_SECONDS + 1):
            with patch.object(cache_module.time, 'time', return_value=now), \
                    patch('os.unlink', side_effect=AssertionError('No removal')), \
                    patch('os.scandir', side_effect=AssertionError('No scan')):
                self.assertEqual(self.work()['state'], 'unavailable')
                self.assertFalse(self.cache.put(self.key, self.advice, self.packet))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.provider.calls, 0)

    def test_slots_exhaustion_collision_partial_no_pruning(self):
        with patch('os.unlink', side_effect=AssertionError('No removal')), \
                patch('os.scandir', side_effect=AssertionError('No scan')):
            for index in range(cache_module.SLOTS):
                self.assertTrue(self.cache.put(f'{index:064x}', self.advice, self.packet))
            for index in range(cache_module.SLOTS, cache_module.SLOTS * 2):
                self.assertFalse(self.cache.put(f'{index:064x}', self.advice, self.packet))
                self.assertIsNone(self.cache.get(f'{index:064x}', self.packet))
        self.assertEqual(len(list(self.directory.iterdir())), cache_module.SLOTS)
        self.assertLessEqual(sum(path.stat().st_size for path in self.directory.iterdir()),
                             cache_module.SLOTS * cache_module.MAX_BYTES)
        self.assertEqual(self.work()['state'], 'unavailable')
        self.assertEqual(self.provider.calls, 0)

    def test_directory_overlap_missing_symlink_mode_and_ancestry(self):
        link = self.root / 'link'
        link.symlink_to(self.directory)
        nested = self.source / 'cache'
        nested.mkdir(mode=0o700)
        for directory in (self.source, self.trusted, self.root, nested, link, self.root / 'absent'):
            config = replace(self.config, advisory_cache=replace(self.settings, directory=str(directory)))
            self.assertEqual(self.work(config)['state'], 'unavailable')
        self.directory.chmod(0o755)
        self.assertEqual(self.work()['state'], 'unavailable')
        self.directory.chmod(0o700)
        self.root.chmod(0o777)
        self.assertEqual(self.work()['state'], 'unavailable')
        self.root.chmod(0o700)
        self.assertEqual(self.provider.calls, 0)
        self.assertFalse((self.root / 'absent').exists())

    def test_file_privacy_links_owner_fifo_and_directory_rejected(self):
        self.seed()
        self.path.chmod(0o644)
        self.assertIsNone(self.cache.get(self.key, self.packet))
        self.path.chmod(0o600)
        with patch('os.geteuid', return_value=os.geteuid() + 1):
            self.assertIsNone(self.cache.get(self.key, self.packet))
        victim = self.root / 'victim'
        os.link(self.path, victim)
        self.assertIsNone(self.cache.get(self.key, self.packet))
        original = victim.read_bytes()
        self.path.unlink()
        self.path.symlink_to(victim)
        self.assertIsNone(self.cache.get(self.key, self.packet))
        self.assertFalse(self.cache.put(self.key, self.advice, self.packet))
        self.assertEqual(victim.read_bytes(), original)
        self.path.unlink()
        os.mkfifo(self.path)
        self.assertIsNone(self.cache.get(self.key, self.packet))
        self.path.unlink()
        self.path.mkdir()
        self.assertIsNone(self.cache.get(self.key, self.packet))

    def test_read_replacement_and_validation_race_fail_closed(self):
        self.seed()
        original_read = os.read
        changed = False
        def read(descriptor, amount):
            nonlocal changed
            raw = original_read(descriptor, amount)
            if not changed:
                changed = True
                self.path.write_bytes(b'changed')
            return raw
        with patch.object(cache_module.os, 'read', side_effect=read):
            self.assertIsNone(self.cache.get(self.key, self.packet))
        self.path.unlink()
        self.seed()
        original_validate = cache_module.validate_response
        def validate(raw, packet):
            advice = original_validate(raw, packet)
            self.path.unlink()
            self.path.write_bytes(b'replacement')
            return advice
        with patch.object(cache_module, 'validate_response', side_effect=validate):
            self.assertIsNone(self.cache.get(self.key, self.packet))
        self.assertEqual(self.path.read_bytes(), b'replacement')

    def test_relocated_source_inside_cache_rejected_after_open(self):
        self.seed()
        self.source.rename(self.directory / 'moved-source')
        settings = replace(self.settings, source_root=str(self.directory / 'moved-source'))
        self.cache.config = settings
        self.cache.guard.candidate = settings.source_root
        self.assertIsNone(self.cache.get(self.key, self.packet))
        self.assertFalse(self.cache.put(self.key, self.advice, self.packet))

    def test_invalid_provider_advice_never_persisted(self):
        config = replace(self.config, advisory_cache=replace(self.settings, allow_call_on_miss=True))
        for mode in ('instruction', 'citation', 'wrong-file', 'wrong-hash', 'oversize', 'malformed'):
            provider = self.provider_for_cache(mode)
            self.assertEqual(self.work(config, provider=provider)['state'], 'invalid-response')
            self.assertEqual(provider.calls, 1)
            self.assertEqual(list(self.directory.iterdir()), [])

    def test_stat_open_symlink_swap_rejected(self):
        self.seed()
        victim = self.root / 'victim'
        victim.write_bytes(b'PRIVATE-SYNTHETIC-CANARY')
        original_open = os.open
        def swap(name, flags, *args, **kwargs):
            if name == self.path.name:
                self.path.unlink()
                self.path.symlink_to(victim)
            return original_open(name, flags, *args, **kwargs)
        with patch.object(cache_module.os, 'open', side_effect=swap):
            self.assertIsNone(self.cache.get(self.key, self.packet))
        self.assertEqual(victim.read_bytes(), b'PRIVATE-SYNTHETIC-CANARY')

    def test_write_namespace_races_never_replace_other_files(self):
        original_write = os.write
        def replace_file(descriptor, raw):
            self.path.unlink()
            self.path.write_bytes(b'other writer')
            return original_write(descriptor, raw)
        with patch.object(cache_module.os, 'write', side_effect=replace_file):
            self.assertFalse(self.cache.put(self.key, self.advice, self.packet))
        self.assertEqual(self.path.read_bytes(), b'other writer')
        self.path.unlink()
        original_destination = cache_module.Destination
        def replace_directory(*args, **kwargs):
            self.directory.rename(self.root / 'old')
            self.directory.mkdir(mode=0o700)
            return original_destination(*args, **kwargs)
        with patch.object(cache_module, 'Destination', side_effect=replace_directory):
            self.assertFalse(self.cache.put(self.key, self.advice, self.packet))
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(list((self.root / 'old').iterdir()), [])

    def test_key_invalidation_changes_reject_existing_entry(self):
        self.seed()
        for config in (replace(self.config, budget=replace(CONFIG.budget, deadline_ms=4999)),
                       replace(self.config, tokenizer_revision='d' * 64),
                       replace(self.config, rates=replace(CONFIG.rates, fixed_per_call=8))):
            provider = self.provider_for_cache()
            provider.tokenizer.revision = config.tokenizer_revision
            self.assertEqual(self.work(config, provider=provider)['state'], 'unavailable')
            self.assertEqual(provider.calls, 0)
        for field in ('GENERATION', 'DATA_HANDLING'):
            changed = dict(getattr(broker, field)) | {'synthetic-setting': 1}
            with patch.object(broker, field, changed):
                self.assertEqual(self.work()['state'], 'unavailable')
        self.assertEqual(self.provider.calls, 0)

    def test_required_cache_inventory_is_discoverable(self):
        scripts = Path(__file__).resolve().parents[3] / '.github/scripts'
        sys.path.insert(0, str(scripts))
        import reviewer_test_support
        loader = unittest.TestLoader()
        suite = loader.discover(str(Path(__file__).resolve().parent))
        ids = list(reviewer_test_support.test_ids(suite))
        self.assertEqual(loader.errors, [])
        self.assertGreaterEqual(len([item for item in reviewer_test_support.REQUIRED_TESTS
                                     if item.startswith('test_advisory_cache.')]), 12)
        for required in reviewer_test_support.REQUIRED_TESTS:
            self.assertEqual(ids.count(required), 1, required)

    def test_pinned_directory_replacement_rejected(self):
        self.seed()
        self.directory.rename(self.root / 'old')
        self.directory.mkdir(mode=0o700)
        self.assertIsNone(self.cache.get(self.key, self.packet))
        self.assertFalse(self.cache.put(self.key, self.advice, self.packet))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_write_failure_leaves_private_partial_slot_no_retry(self):
        with patch.object(cache_module.os, 'write', side_effect=PermissionError('SYNTHETIC-CANARY')):
            self.assertFalse(self.cache.put(self.key, self.advice, self.packet))
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.cache.put(self.key, self.advice, self.packet))
        self.assertEqual(self.work()['state'], 'unavailable')
        self.assertEqual(self.provider.calls, 0)

    def test_unavailable_cache_explicit_one_call_without_persistence(self):
        self.directory.chmod(0o755)
        config = replace(self.config, advisory_cache=replace(self.settings, allow_call_on_miss=True))
        self.assertEqual(self.work(config)['state'], 'assessed')
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_same_user_forgery_can_be_syntactically_valid_not_truth(self):
        self.seed()
        forged = protocol.strict_json(self.path.read_bytes())
        forged['advice']['findings'] = []
        self.path.write_bytes(ingest.canonical(forged))
        result = self.work()
        self.assertEqual(result['state'], 'cache-hit')
        self.assertEqual(result['advice']['findings'], [])
        self.assertIn('NON-AUTHORITATIVE', result['message'])
        self.assertEqual(self.provider.calls, 0)
        self.assertTrue(checks.review('example', ENTRIES)['findings'])

    def test_cache_stall_in_worker_is_supervised(self):
        config = replace(self.config, budget=replace(CONFIG.budget, deadline_ms=150))
        started = time.monotonic()
        with patch.object(cache_module.AdvisoryCache, 'get', side_effect=lambda *args: time.sleep(5)):
            result = broker.assess_in_isolation(WIRE, config=config, consent=consent(config),
                                                provider=self.provider)
        self.assertEqual(result['state'], 'unavailable')
        self.assertLess(time.monotonic() - started, 2)

    def test_report_cache_schema_and_admission_remain_separate(self):
        self.assertIn(packaging.REVIEWER + 'scripts/advisory_cache.py', packaging.LOCAL_FILES)
        preview = report_cache.PrivateCache(str(self.directory), str(self.source), str(self.trusted))
        self.addCleanup(preview.close)
        self.assertFalse(preview.put(self.key, self.advice, lambda value: None))
        self.assertFalse(self.cache.put(self.key, checks.review('example', ENTRIES), self.packet))
        for filename in ('launch.py', 'entrypoint.py', 'run.py', 'checks.py', 'model_contract.py'):
            source = (Path(__file__).resolve().parents[1] / 'scripts' / filename).read_text()
            self.assertNotIn('advisory_cache', source)


if __name__ == '__main__':
    unittest.main()

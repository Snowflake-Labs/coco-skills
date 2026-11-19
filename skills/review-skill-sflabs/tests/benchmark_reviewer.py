"""Offline synthetic benchmark. Never accepts a candidate path or invokes Docker."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import tracemalloc

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import checks
import ingest
import model_contract
from protocol import render
from license_fixtures import APACHE


def corpus():
    skill = b'---\nname: example\ndescription: "Use when reviewing. Triggers: review"\n---\nAdvisory report.\n'
    entries = [ingest.Entry('LICENSE', APACHE), ingest.Entry('SKILL.md', skill)]
    for index in range(96):
        text = '# Synthetic guide\n' + ('[Guide](../docs/guide000.md#synthetic-guide)\n' * 12)
        entries.append(ingest.Entry(f'docs/guide{index:03d}.md', text.encode()))
    return tuple(sorted(entries, key=lambda entry: entry.path))


def paired(entries):
    if hasattr(model_contract, 'review_and_packet'):
        return model_contract.review_and_packet('example', entries, enabled=True)
    return checks.review('example', entries), model_contract.make_packet('example', entries, enabled=True)


def measure(operation, iterations):
    started = time.perf_counter_ns()
    result = operation()
    first = (time.perf_counter_ns() - started) / 1e6
    samples = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - started) / 1e6)
    # Separate instrumented memory pass: tracing overhead is not in wall timings.
    tracemalloc.start()
    try:
        operation()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return {'first_call_ms': round(first, 3), 'repeat_median_ms': round(statistics.median(samples), 3),
            'repeat_min_ms': round(min(samples), 3), 'repeat_max_ms': round(max(samples), 3),
            'python_peak_bytes': peak}, result


def run(iterations=15):
    entries = corpus()
    wire = ingest.encode(entries, 'example')
    report, packet = paired(entries)
    output = {'benchmark': 'synthetic-reviewer-v1', 'iterations': iterations,
              'python': sys.version.split()[0], 'changed_skills': 1, 'files': len(entries),
              'content_bytes': sum(len(entry.data) for entry in entries),
              'corpus_sha256': ingest.manifest(entries, 'example')[1],
              'report_sha256': hashlib.sha256(ingest.canonical(report)).hexdigest(),
              'packet_sha256': packet['packet_sha256'],
              'cold_provisioning': 'unmeasured: no Docker provisioning authorized',
              'warm_provisioning': 'unmeasured: no approved running runtime',
              'container_startup': 'unmeasured: Docker unavailable; not invoked',
              'process_peak_rss': 'unmeasured: tracemalloc measures Python allocations only',
              'cache': 'unmeasured: implementation unavailable', 'stages': {}}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        candidate = root / 'example'
        candidate.mkdir()
        for entry in entries:
            target = candidate / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.data)
        operations = {'collection': lambda: ingest.collect(str(candidate)),
                      'serialization': lambda: ingest.encode(entries, 'example'),
                      'wire_validation': lambda: ingest.decode(wire),
                      'checks': lambda: checks.review('example', entries),
                      'render': lambda: render(report),
                      'model_packet': lambda: model_contract.make_packet('example', entries, enabled=True),
                      'review_and_packet': lambda: paired(entries)}
        for name, operation in operations.items():
            output['stages'][name] = measure(operation, iterations)[0]
        # This seam measures cache overhead, not Docker latency or isolation.
        # A synthetic pure-core runtime double is never a production fallback.
        import launch
        from packaging import ROOT
        from report_cache import PrivateCache, key_for
        from unittest.mock import patch
        image = 'sha256:' + 'a' * 64
        directory = root / 'private'
        directory.mkdir(mode=0o700)
        context = launch.cache_context('example', entries, image)
        key = key_for(context)
        cached_report = checks.review('example', entries, launch.trusted_catalog_data())
        cached_report['provenance']['image_id'] = image
        validate = lambda value: launch.validate_snapshot_report(value, 'example', entries, image)
        cache = PrivateCache(str(directory), str(candidate), str(ROOT))
        try:
            cache.put(key, cached_report, validate)
            output['stages']['cache_lookup_validation'] = measure(lambda: cache.get(key, validate), iterations)[0]
            output['stages']['cache_key_context'] = measure(
                lambda: key_for(launch.cache_context('example', entries, image)), iterations)[0]
            output['stages']['supervised_collection_serialization'] = measure(
                lambda: launch.collect(str(candidate)), iterations)[0]
            with patch.object(launch, 'local_docker', side_effect=AssertionError('Docker must not run')):
                output['stages']['preview_cache_hit'] = measure(
                    lambda: launch.preview(str(candidate), image, str(directory)), iterations)[0]
            def synthetic_runtime(wire, image):
                name, records = ingest.decode(wire)
                result = checks.review(name, records, launch.trusted_catalog_data())
                result['provenance']['image_id'] = image
                return result, (name, records)
            def miss():
                with tempfile.TemporaryDirectory(dir=root) as empty:
                    with patch.object(launch, 'local_docker'), patch.object(launch, '_run_review', side_effect=synthetic_runtime):
                        return launch.preview(str(candidate), image, empty)
            output['stages']['preview_cache_miss_synthetic_runtime'] = measure(miss, iterations)[0]
            output['cache'] = 'measured private preview hit/miss; miss uses synthetic runtime double, not Docker'
        finally:
            cache.close()
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=15)
    args = parser.parse_args()
    if not 3 <= args.iterations <= 100:
        parser.error('iterations must be between 3 and 100')
    print(json.dumps(run(args.iterations), sort_keys=True, indent=2))

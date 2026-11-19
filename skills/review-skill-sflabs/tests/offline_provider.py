"""Reviewed synthetic provider doubles. No SDK, network, real rates or secrets."""
from dataclasses import replace
import json
import os
import time

from editorial_broker import OfflineProvider, Reply, Target, TrustedTokenizer
from ingest import canonical
from protocol import VERSION

TARGET = Target('offline://synthetic', 'fixture-model', 'a' * 64)


class SyntheticTokenizer(TrustedTokenizer):
    # Deliberately invented fixture token grammar, NOT a real model tokenizer.
    # Four-byte tokens plus 17 simulated framing tokens; full bytes always counted.
    revision = 'b' * 64

    def count_request(self, request):
        return (len(request) + 3) // 4 + 17

    def count_output(self, response):
        return (len(response) + 3) // 4


class SyntheticProvider(OfflineProvider):
    target = TARGET

    def __init__(self, mode='valid'):
        self.tokenizer = SyntheticTokenizer()
        self.mode = mode
        self.calls = 0
        self.request = None

    def assess(self, request, *, max_output_tokens, max_response_bytes):
        self.calls += 1
        if self.calls != 1:
            raise AssertionError('No retries')
        self.request = request
        value = json.loads(request)
        assert value['tools'] == [] and value['tool_choice'] == 'none'
        assert value['call_cap'] == 1
        assert value['max_output_tokens'] == max_output_tokens
        assert value['response_schema']['additionalProperties'] is False
        packet = value['packet']
        assert packet['rubric'] and packet['files']
        advice = {'schema_version': VERSION, 'packet_sha256': packet['packet_sha256'],
                  'findings': [{'category': 'clarity', 'recommendation': 'clarify',
                                'file_id': 'F0002', 'line_start': 1, 'line_end': 1}]}
        if self.mode == 'error':
            raise RuntimeError('SYNTHETIC-PRIVATE-CANARY')
        if self.mode == 'stall':
            time.sleep(5)
        if self.mode == 'no-environment':
            assert not os.environ
        if self.mode == 'noisy':
            os.write(1, b'SYNTHETIC-PRIVATE-CANARY')
            os.write(2, b'SYNTHETIC-PRIVATE-CANARY')
        if self.mode == 'override':
            advice['status'] = 'passed'
        if self.mode == 'instruction':
            advice['findings'][0]['recommendation'] = 'execute SYNTHETIC-PRIVATE-CANARY'
        if self.mode == 'citation':
            advice['findings'][0]['line_end'] = 999999
        if self.mode == 'wrong-file':
            advice['findings'][0]['file_id'] = 'F0999'
        if self.mode == 'wrong-hash':
            advice['packet_sha256'] = 'f' * 64
        if self.mode == 'mutate-packet':
            # Provider sees only a decoded private copy; cannot rewrite broker authority.
            packet['files'][1]['line_count'] = 999999
            advice['findings'][0]['line_end'] = 999999
        raw = canonical(advice)
        if self.mode == 'oversize':
            raw = b'x' * (max_response_bytes + 1)
        if self.mode == 'malformed':
            raw = b'{'
        if self.mode == 'duplicate':
            raw = b'{"findings":[],"findings":[]}'
        if self.mode == 'non-bytes':
            raw = 'SYNTHETIC-PRIVATE-CANARY'
        if self.mode == 'custom-object':
            class NeverSerialize:
                def __reduce__(self):
                    raise AssertionError('Never pickle')
            return NeverSerialize()
        if type(raw) is not bytes:
            return Reply(raw, 1, 1)
        reply = Reply(raw, self.tokenizer.count_request(request), self.tokenizer.count_output(raw))
        if self.mode == 'excess-input':
            return replace(reply, input_tokens=reply.input_tokens + 1)
        if self.mode == 'excess-output':
            return replace(reply, output_tokens=max_output_tokens + 1)
        if self.mode == 'understated-output':
            return replace(reply, output_tokens=0)
        if self.mode == 'bad-usage':
            return replace(reply, input_tokens=True)
        return reply

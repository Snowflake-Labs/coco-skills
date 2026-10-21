"""Service-internal OFFLINE broker contract, not a host runner or a sandbox.

Only separately reviewed Python provider/tokenizer objects may be injected by
trusted orchestration INSIDE an existing isolated, secret-free service boundary.
Never import candidate code or resolve a provider from candidate input. This
module does not establish that boundary, launch a service, or implement HTTP.
"""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import hashlib
import os
import re
import select
import signal
import time
from types import MappingProxyType

import advisory_cache
from advisory_cache import CacheConfig
from ingest import MAX_WIRE_BYTES, canonical, decode
from model_contract import (CATEGORIES, MAX_RESPONSE_BYTES, RECOMMENDATIONS,
                            ContractError, make_packet, validate_response)
from protocol import VERSION, is_hex, strict_json

BROKER_VERSION = 'offline-editorial-broker/1'
MAX_IPC_BYTES = 24 * 1024
MAX_REQUEST_BYTES = 160 * 1024
# Fixed offline request settings, explicitly fingerprinted for advice reuse.
GENERATION = MappingProxyType({'temperature_milli': 0, 'top_p_milli': 1000, 'seed': 0})
DATA_HANDLING = MappingProxyType({'network': 'none', 'logging': 'none',
                                 'raw_retention': 'none', 'advice': 'private-enums-only'})
MESSAGES = MappingProxyType({
    'disabled': 'Editorial assessment disabled; no provider call.',
    'withheld': 'Editorial assessment withheld; consent or complete eligibility is missing.',
    'cache-hit': 'NON-AUTHORITATIVE cached advice; not a fresh assessment or admission decision.',
    'assessed': 'Offline editorial assessment completed; advisory only, not admission evidence.',
    'invalid-response': 'Editorial response rejected; no advice retained.',
    'unavailable': 'Editorial assessment unavailable; no live adapter or host fallback.',
    'budget-exceeded': 'Editorial budget exceeded; no truncation, retry or escalation.',
})
QUESTIONS = MappingProxyType({
    'scope': 'Is the scope bounded, with clear inputs, outputs and stopping conditions?',
    'clarity': 'Can a reader follow the instructions without guessing missing decisions?',
    'usefulness': 'Does the workflow produce a useful, actionable result for its stated audience?',
    'differentiation': 'Is the distinct behavior clear relative to the supplied partial catalog?',
})
ACTIONS = MappingProxyType({
    'human_review': 'Arrange contextual human review.',
    'parameterize': 'Consider parameterizing environment-specific values.',
    'clarify': 'Consider clarifying the instructions or stopping conditions.',
    'differentiate': 'Explain the distinct scope relative to the partial catalog.',
    'minimize_output': 'Consider reducing unnecessary output.',
})


def response_schema(packet_hash):
    """Provider guidance only; validate_response remains the admission boundary for advice."""
    return {
        'type': 'object', 'additionalProperties': False,
        'required': ['schema_version', 'packet_sha256', 'findings'],
        'properties': {
            'schema_version': {'const': VERSION},
            'packet_sha256': {'const': packet_hash},
            'findings': {'type': 'array', 'maxItems': 32, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['category', 'recommendation', 'file_id', 'line_start', 'line_end'],
                'properties': {
                    'category': {'enum': sorted(CATEGORIES)},
                    'recommendation': {'enum': sorted(RECOMMENDATIONS)},
                    'file_id': {'type': 'string', 'pattern': '^F[0-9]{4}$'},
                    'line_start': {'type': 'integer', 'minimum': 1},
                    'line_end': {'type': 'integer', 'minimum': 1},
                },
            }},
        },
    }


@dataclass(frozen=True)
class Target:
    endpoint: str
    model: str
    revision: str  # Independently approved immutable digest, never a model alias.


@dataclass(frozen=True)
class Budget:
    # Proposed defaults, not calibrated model-specific optima.
    input_tokens: int = 8000
    output_tokens: int = 1000
    deadline_ms: int = 5000
    spend_units: int | None = None


@dataclass(frozen=True)
class Rates:
    # Trusted upper bounds in one operator-defined integer unit, not credit rates.
    # Cover ALL billed tokens (including hidden/reasoning tokens) and fixed fees.
    unit: str
    input_per_token: int
    output_per_token: int
    fixed_per_call: int
    all_charges_bounded: bool


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    target: Target | None = None
    allowed_targets: tuple[Target, ...] = ()
    tokenizer_revision: str | None = None
    rates: Rates | None = None
    budget: Budget = Budget()
    question: str = 'clarity'
    call_cap: int = 1
    advisory_cache: CacheConfig | None = None


@dataclass(frozen=True)
class Consent:
    # Explicit operator decision for exact bytes AND all disclosure/budget settings.
    transmit: bool
    wire_sha256: str
    config_sha256: str


def config_digest(config, catalog=None):
    return hashlib.sha256(canonical({'broker': BROKER_VERSION, 'config': asdict(config),
                                     'catalog': catalog, 'generation': dict(GENERATION),
                                     'data_handling': dict(DATA_HANDLING)})).hexdigest()


class TrustedTokenizer(ABC):
    """Reviewed exact/upper-bound accounting, not len/4 or a caller-supplied count.

    count_request covers the ENTIRE serialized request, schema/rubric, model chat
    framing and other input overhead. count_output covers all visible output.
    Hidden billed output must additionally be bounded/enforced by the provider.
    revision is an immutable digest bound to the exact target's tokenizer.
    """
    revision: str

    @abstractmethod
    def count_request(self, request: bytes) -> int:
        raise NotImplementedError

    @abstractmethod
    def count_output(self, response: bytes) -> int:
        raise NotImplementedError


@dataclass(frozen=True)
class Reply:
    raw: bytes
    input_tokens: int
    output_tokens: int  # All billed output, including any non-visible tokens.


class OfflineProvider(ABC):
    """Trusted code, NOT a plugin safety boundary. Only reviewed synthetic doubles.

    Implement exactly one tool-less call; honor the output and response-byte caps;
    no network, credentials, SDK, subprocess, tools, callbacks, browsing or retry.
    Do not return custom objects or log requests/responses. Python cannot enforce
    this against malicious trusted code: service isolation remains mandatory.
    """
    target: Target
    tokenizer: TrustedTokenizer
    # Required only for optional caching: independently approved identity of ALL
    # provider implementation/configuration, not just its model or class name.
    cache_identity: str | None = None

    @abstractmethod
    def assess(self, request: bytes, *, max_output_tokens: int,
               max_response_bytes: int) -> Reply:
        raise NotImplementedError


def _result(state, advice=None):
    return {'broker_version': BROKER_VERSION, 'state': state,
            'message': MESSAGES[state], 'advice': advice}


def _positive(value, maximum):
    return type(value) is int and 0 < value <= maximum


def _configured(config):
    if type(config) is not Config or type(config.enabled) is not bool:
        return False
    target, budget, rates = config.target, config.budget, config.rates
    return (type(target) is Target and type(config.allowed_targets) is tuple
            and 0 < len(config.allowed_targets) <= 16
            and all(type(item) is Target for item in config.allowed_targets)
            and target in config.allowed_targets
            # Concrete HTTP adapters intentionally unavailable, even with consent.
            and isinstance(target.endpoint, str)
            and re.fullmatch(r'offline://[a-z0-9-]{1,64}', target.endpoint) is not None
            and isinstance(target.model, str)
            and re.fullmatch(r'[a-z0-9-]{1,64}', target.model) is not None
            and is_hex(target.revision) and is_hex(config.tokenizer_revision)
            and type(config.question) is str and config.question in QUESTIONS
            and type(config.call_cap) is int and config.call_cap == 1
            and type(budget) is Budget
            and _positive(budget.input_tokens, 1000000)
            and _positive(budget.output_tokens, 1000000)
            and _positive(budget.deadline_ms, 60000)
            and _positive(budget.spend_units, 10 ** 15)
            and type(rates) is Rates and rates.all_charges_bounded is True
            and isinstance(rates.unit, str)
            and re.fullmatch(r'[a-z0-9-]{1,32}', rates.unit) is not None
            and all(type(rate) is int and 0 <= rate <= 10 ** 12 for rate in
                    (rates.input_per_token, rates.output_per_token, rates.fixed_per_call)))


def cache_context(wire, config, catalog, packet, request):
    """Full content/config identities only; this context is hashed, never stored."""
    from ingest import MAX_DEPTH, MAX_ENTRIES, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES
    from model_contract import MAX_PACKET_BYTES
    return {'broker': BROKER_VERSION, 'config': asdict(config),
            'wire_sha256': hashlib.sha256(wire).hexdigest(),
            'packet_sha256': packet['packet_sha256'],
            'request_sha256': hashlib.sha256(request).hexdigest(),
            'rubric': packet['rubric'], 'rules': packet['rules_version'], 'schema': VERSION,
            'catalog_sha256': hashlib.sha256(canonical(catalog)).hexdigest(),
            'generation': dict(GENERATION), 'data_handling': dict(DATA_HANDLING),
            'limits': {'wire': MAX_WIRE_BYTES, 'packet': MAX_PACKET_BYTES,
                       'request': MAX_REQUEST_BYTES, 'response': MAX_RESPONSE_BYTES,
                       'ipc': MAX_IPC_BYTES, 'files': MAX_FILES, 'entries': MAX_ENTRIES,
                       'depth': MAX_DEPTH, 'file_bytes': MAX_FILE_BYTES, 'total': MAX_TOTAL_BYTES,
                       'cache_bytes': advisory_cache.MAX_BYTES, 'cache_slots': advisory_cache.SLOTS,
                       'cache_ttl': advisory_cache.TTL_SECONDS}}


def _consented(wire, config, catalog, consent):
    return (type(consent) is Consent and consent.transmit is True
            and consent.wire_sha256 == hashlib.sha256(wire).hexdigest()
            and consent.config_sha256 == config_digest(config, catalog))


def _work(wire, config, catalog, provider, consent=None):
    if not config.enabled:
        return _result('disabled')
    cache_config = config.advisory_cache
    if cache_config is not None:
        # Repeat exact operator consent/config checks inside the supervised worker
        # before any cache filesystem access, including direct internal callers.
        if not _configured(config) or not advisory_cache.configured(cache_config, wire):
            return _result('unavailable')
        if not _consented(wire, config, catalog, consent):
            return _result('withheld')
        if provider.cache_identity != cache_config.provider_config_sha256:
            return _result('unavailable')
    try:
        # Revalidate bounded wire and recompute eligibility INSIDE the worker.
        # No packet, prior report, caller analysis or cache is accepted as authority.
        name, entries = decode(wire)
        packet = make_packet(name, entries, catalog, enabled=True)
    except ContractError as error:
        return _result('budget-exceeded' if str(error) == 'budget_exceeded' else 'withheld')
    except Exception:
        return _result('withheld')
    if (type(provider.target) is not Target or provider.target != config.target
            or not isinstance(provider.tokenizer, TrustedTokenizer)
            or provider.tokenizer.revision != config.tokenizer_revision):
        return _result('unavailable')
    request = canonical({'packet': packet, 'question': QUESTIONS[config.question],
                         'response_schema': response_schema(packet['packet_sha256']),
                         'tools': [], 'tool_choice': 'none', 'call_cap': 1,
                         'max_output_tokens': config.budget.output_tokens,
                         'generation': dict(GENERATION), 'data_handling': dict(DATA_HANDLING)})
    if len(request) > MAX_REQUEST_BYTES:
        return _result('budget-exceeded')
    input_tokens = provider.tokenizer.count_request(request)
    if not _positive(input_tokens, 1000000):
        return _result('unavailable')
    budget, rates = config.budget, config.rates
    reservation = (input_tokens * rates.input_per_token
                   + budget.output_tokens * rates.output_per_token + rates.fixed_per_call)
    if input_tokens > budget.input_tokens or reservation > budget.spend_units:
        return _result('budget-exceeded')
    cache = None
    key = None
    try:
        if cache_config is not None:
            key = advisory_cache.key_for(cache_context(wire, config, catalog, packet, request))
            try:
                cache = advisory_cache.AdvisoryCache(cache_config)
            except (OSError, ValueError):
                pass
            if cache is not None:
                advice = cache.get(key, packet)
                if advice is not None:
                    # Syntax/citations were checked against this fresh packet;
                    # tokenizer limits still apply to same-owner forged entries.
                    tokens = provider.tokenizer.count_output(canonical(advice))
                    if not _positive(tokens, 1000000):
                        return _result('unavailable')
                    if tokens > budget.output_tokens:
                        return _result('budget-exceeded')
                    return _result('cache-hit', advice)
            if not cache_config.allow_call_on_miss:
                return _result('unavailable')
        result = _assess_request(provider, request, packet, input_tokens, budget)
        if cache is not None and result['state'] == 'assessed':
            cache.put(key, result['advice'], packet)
        return result
    finally:
        if cache is not None:
            cache.close()


def _assess_request(provider, request, packet, input_tokens, budget):
    # Single syntactic call site; no retry on any failure. Immutable bytes are
    # supplied instead of the mutable packet later used for citation validation.
    reply = provider.assess(request, max_output_tokens=budget.output_tokens,
                            max_response_bytes=MAX_RESPONSE_BYTES)
    if (type(reply) is not Reply or type(reply.raw) is not bytes
            or len(reply.raw) > MAX_RESPONSE_BYTES):
        return _result('invalid-response')
    if (type(reply.input_tokens) is not int or type(reply.output_tokens) is not int
            or reply.input_tokens < 0 or reply.output_tokens < 0):
        return _result('invalid-response')
    visible_tokens = provider.tokenizer.count_output(reply.raw)
    if not _positive(visible_tokens, 1000000):
        return _result('unavailable')
    if (reply.input_tokens > input_tokens or reply.output_tokens > budget.output_tokens
            or visible_tokens > budget.output_tokens):
        return _result('budget-exceeded')
    if visible_tokens > reply.output_tokens:
        return _result('invalid-response')
    try:
        return _result('assessed', validate_response(reply.raw, packet))
    except ContractError:
        return _result('invalid-response')


def _validate_result(value):
    """Fixed parent-side egress grammar; full packet/citation checks stay inside."""
    if (type(value) is not dict or set(value) != {'broker_version', 'state', 'message', 'advice'}
            or value['broker_version'] != BROKER_VERSION or type(value['state']) is not str
            or value['state'] not in MESSAGES or value['message'] != MESSAGES[value['state']]):
        raise ValueError()
    advice = value['advice']
    if value['state'] not in ('assessed', 'cache-hit'):
        if advice is not None:
            raise ValueError()
        return value
    if (type(advice) is not dict or set(advice) != {'schema_version', 'packet_sha256', 'findings'}
            or advice['schema_version'] != VERSION or not is_hex(advice['packet_sha256'])
            or type(advice['findings']) is not list or len(advice['findings']) > 32):
        raise ValueError()
    for item in advice['findings']:
        if (type(item) is not dict or set(item) != {
                'category', 'recommendation', 'file_id', 'line_start', 'line_end'}
                or type(item['category']) is not str or item['category'] not in CATEGORIES
                or type(item['recommendation']) is not str or item['recommendation'] not in ACTIONS
                or type(item['file_id']) is not str
                or re.fullmatch(r'F[0-9]{4}', item['file_id']) is None
                or not 1 <= int(item['file_id'][1:]) <= 512
                or type(item['line_start']) is not int or type(item['line_end']) is not int
                or not 1 <= item['line_start'] <= item['line_end'] <= 262145):
            raise ValueError()
    return value


def render(value):
    """Only fixed messages, enum labels and bounded numeric citations leave the API."""
    try:
        _validate_result(value)
        lines = [value['message']]
        if value['advice'] is not None:
            for item in value['advice']['findings']:
                lines.append(f"{item['category']} {item['file_id']}:{item['line_start']}-{item['line_end']}: "
                             + ACTIONS[item['recommendation']])
        return '\n'.join(lines)
    except (ValueError, TypeError, KeyError, RecursionError):
        return MESSAGES['invalid-response']


def _child(write_fd, wire, config, catalog, provider, consent=None):
    try:
        os.setsid()
        # No inherited environment or ordinary descriptors available to doubles.
        # Fork memory is NOT scrubbed: this must already be a secret-free service.
        os.environ.clear()
        with open(os.devnull, 'rb+') as sink:
            for descriptor in (0, 1, 2):
                os.dup2(sink.fileno(), descriptor)
        import resource
        maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        if maximum == resource.RLIM_INFINITY:
            maximum = int(os.sysconf('SC_OPEN_MAX'))
        os.closerange(3, write_fd)
        os.closerange(write_fd + 1, maximum)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        result = _work(wire, config, catalog, provider, consent)
        raw = canonical(_validate_result(result))
        if len(raw) > MAX_IPC_BYTES:
            raw = canonical(_result('invalid-response'))
    except BaseException:
        raw = canonical(_result('unavailable'))
    try:
        pending = memoryview(raw)
        while pending:
            count = os.write(write_fd, pending)
            if count <= 0:
                break
            pending = pending[count:]
    except BaseException:
        pass
    finally:
        os._exit(0)


def _supervise(wire, config, catalog, provider, consent=None):
    # POSIX fork avoids any pickle deserialization or dynamic module selection.
    # This is single-threaded service-internal supervision, NOT host isolation.
    deadline = time.monotonic() + config.budget.deadline_ms / 1000
    read_fd, write_fd = os.pipe()
    process = None
    try:
        process = os.fork()
        if process == 0:
            os.close(read_fd)
            _child(write_fd, wire, config, catalog, provider, consent)
        os.close(write_fd)
        write_fd = None
        os.set_blocking(read_fd, False)
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _result('unavailable')
            ready, _, _ = select.select([read_fd], [], [], remaining)
            if not ready:
                return _result('unavailable')
            chunk = os.read(read_fd, min(4096, MAX_IPC_BYTES + 1 - size))
            if not chunk:
                if time.monotonic() > deadline:
                    return _result('unavailable')
                return _validate_result(strict_json(b''.join(chunks)))
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_IPC_BYTES:
                return _result('invalid-response')
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if process:
            # Reap before signaling to avoid zombie races. This contract allows
            # no descendants; future service process limits must enforce that.
            reaped, _ = os.waitpid(process, os.WNOHANG)
            if not reaped:
                try:
                    os.kill(process, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                finally:
                    os.waitpid(process, 0)


def assess_in_isolation(wire, *, config=Config(), consent=None, catalog=None, provider=None):
    """Offline-only service API. NOT for use by the host agent on candidate text.

    Caller supplies captured wire bytes and separately trusted configuration,
    catalog and provider objects. A consent/config flag cannot attest isolation.
    Optional private advice caching is service-local and separately configured.
    No service launch, real endpoint or credentials are provided.
    Each invocation has a fresh one-call allowance; orchestration must separately
    limit aggregate invocations/spend. Timeouts cannot undo an already begun call.
    """
    try:
        if type(config) is not Config or type(config.enabled) is not bool:
            return _result('unavailable')
        if not config.enabled:
            return _result('disabled')
        if not _configured(config) or not isinstance(provider, OfflineProvider):
            return _result('unavailable')
        if type(wire) is not bytes or len(wire) > MAX_WIRE_BYTES:
            return _result('withheld')
        # Bound and detach operator catalog data before fork and consent comparison.
        catalog_raw = canonical(catalog)
        if len(catalog_raw) > MAX_REQUEST_BYTES:
            return _result('budget-exceeded')
        catalog = strict_json(catalog_raw)
        if not _consented(wire, config, catalog, consent):
            return _result('withheld')
        if not hasattr(os, 'fork'):
            return _result('unavailable')
        return _supervise(wire, config, catalog, provider, consent)
    except (Exception, KeyboardInterrupt):
        # Provider messages, tracebacks and candidate bytes never cross egress.
        return _result('unavailable')

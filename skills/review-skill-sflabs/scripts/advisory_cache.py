"""Private service-local enum/citation advice only; never eligibility or admission.

Same-owner forgery is possible. Keys bind context, not authenticity or truth.
No directory creation, slot replacement, pruning, raw packets or provider prose.
"""
from dataclasses import dataclass
import hashlib
import os
import time

from ingest import canonical, stamp
from model_contract import MAX_RESPONSE_BYTES, validate_response
from private_output import Destination
from protocol import is_hex, strict_json
from report_cache import PrivateCache

FORMAT = 'private-advisory-cache/1'
SLOTS = 32
MAX_BYTES = 24 * 1024
TTL_SECONDS = 3600


@dataclass(frozen=True)
class CacheConfig:
    # Operator-supplied service-local paths, NEVER taken from the packet. The
    # trusted collector/orchestrator binds the actual source root to exact wire.
    directory: str
    source_root: str
    trusted_root: str
    source_wire_sha256: str
    provider_config_sha256: str
    allow_call_on_miss: bool = False


def configured(value, wire):
    return (type(value) is CacheConfig
            and all(type(path) is str and len(path) <= 4096 for path in
                    (value.directory, value.source_root, value.trusted_root))
            and is_hex(value.source_wire_sha256)
            and value.source_wire_sha256 == hashlib.sha256(wire).hexdigest()
            and is_hex(value.provider_config_sha256)
            and type(value.allow_call_on_miss) is bool)


def key_for(context):
    return hashlib.sha256(canonical({'format': FORMAT, 'context': context})).hexdigest()


class AdvisoryCache:
    """Reuse only preview cache filesystem guards, not its report codec/policy."""
    def __init__(self, config):
        self.config = config
        self.guard = PrivateCache(config.directory, config.source_root, config.trusted_root)

    def check(self):
        from packaging import disjoint
        disjoint(self.config.directory, self.config.source_root, self.config.trusted_root)
        self.guard.check()

    def close(self):
        self.guard.close()

    @staticmethod
    def slot(key):
        if not is_hex(key):
            raise ValueError()
        return f'advice-slot-{int(key, 16) % SLOTS:02d}.json'

    def get(self, key, packet):
        descriptor = None
        try:
            self.check()
            name = self.slot(key)
            before = os.stat(name, dir_fd=self.guard.directory, follow_symlinks=False)
            self.guard.file_ok(before)
            if before.st_size > MAX_BYTES:
                raise ValueError()
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=self.guard.directory)
            if stamp(before) != stamp(os.fstat(descriptor)):
                raise ValueError()
            chunks, size = [], 0
            while size <= MAX_BYTES:
                chunk = os.read(descriptor, min(4096, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size != before.st_size:
                raise ValueError()
            value = strict_json(b''.join(chunks))
            if (type(value) is not dict or set(value) != {'format', 'key', 'created', 'advice'}
                    or value['format'] != FORMAT or value['key'] != key
                    or type(value['created']) is not int or not 0 <= value['created'] <= 2 ** 53
                    or not 0 <= time.time() - value['created'] <= TTL_SECONDS):
                raise ValueError()
            advice = validate_response(canonical(value['advice']), packet)
            self.check()
            if (stamp(before) != stamp(os.fstat(descriptor)) or stamp(before) != stamp(
                    os.stat(name, dir_fd=self.guard.directory, follow_symlinks=False))):
                raise ValueError()
            return advice
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def put(self, key, advice, packet):
        destination = None
        try:
            advice = validate_response(canonical(advice), packet)
            raw = canonical({'format': FORMAT, 'key': key, 'created': int(time.time()), 'advice': advice})
            if len(raw) > MAX_BYTES or len(canonical(advice)) > MAX_RESPONSE_BYTES:
                return False
            self.check()
            destination = Destination(self.config.directory + '/' + self.slot(key),
                                      self.config.source_root, {'.json'})
            self.check()
            pinned = os.fstat(self.guard.directory)
            opened = os.fstat(destination.directory)
            if (pinned.st_dev, pinned.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError()
            # Use exclusive creation and checked writes, but never cleanup: even
            # a partial slot stays occupied until explicit owner retirement.
            destination.create()
            self.check()
            destination.write(raw)
            self.check()
            return True
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return False
        finally:
            if destination is not None:
                destination.close()

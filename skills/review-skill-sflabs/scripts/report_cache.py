"""Opt-in private preview cache. Never admission, tooling or packet authority.

No HMAC or hash is claimed to authenticate against the same OS user. The trusted
private owner is the boundary; independent authoritative reviews always bypass
this module. Fixed write-once slots bound our storage without scans or deletion.
"""
import hashlib
import os
import stat
import time

from ingest import canonical, open_root, stamp
from private_output import Destination
from protocol import is_hex, strict_json, validate_report

SLOTS = 32
MAX_BYTES = 1024 * 1024
TTL_SECONDS = 3600
FORMAT = 'private-preview-cache/1'


def key_for(context):
    return hashlib.sha256(canonical({'format': FORMAT, 'context': context})).hexdigest()


def safe_ancestors(path):
    """Owner/root ancestry; shared sticky temporary parents cannot rename children.

    Reject group/other-writable non-sticky ancestors. Owner-only cache leaf is
    checked separately, and all path components are opened without following links.
    """
    descriptor = open_root(path)
    try:
        for _ in range(256):
            info = os.fstat(descriptor)
            if (info.st_uid not in (0, os.geteuid()) or
                    (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
                raise ValueError()
            parent = os.open('..', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=descriptor)
            parent_info = os.fstat(parent)
            same = (info.st_dev, info.st_ino) == (parent_info.st_dev, parent_info.st_ino)
            os.close(descriptor)
            descriptor = parent
            if same:
                return
        raise ValueError()
    finally:
        os.close(descriptor)


class PrivateCache:
    def __init__(self, directory, candidate, trusted):
        self.directory = None
        try:
            from packaging import disjoint
            disjoint(directory, candidate, trusted)
            self.parent = directory
            self.candidate = candidate
            self.trusted = trusted
            self.directory = open_root(directory)
            self.check()
        except (OSError, ValueError):
            self.close()
            raise ValueError() from None

    def close(self):
        if self.directory is not None:
            os.close(self.directory)
            self.directory = None

    def check(self):
        safe_ancestors(self.parent)
        # Reuse the no-follow pathname identity and inode-ancestry guard used by
        # private outputs. Neither candidate nor installed tooling may contain us.
        for root in (self.candidate, self.trusted):
            guard = object.__new__(Destination)
            guard.directory, guard.parent, guard.candidate = self.directory, self.parent, root
            guard.check_directory()

    @staticmethod
    def slot(key):
        if not is_hex(key):
            raise ValueError()
        return f'slot-{int(key, 16) % SLOTS:02d}.json'

    @staticmethod
    def file_ok(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
                not 0 < info.st_size <= MAX_BYTES):
            raise ValueError()

    def get(self, key, validate):
        descriptor = None
        try:
            self.check()
            name = self.slot(key)
            before = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            self.file_ok(before)
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=self.directory)
            if stamp(before) != stamp(os.fstat(descriptor)):
                raise ValueError()
            chunks, size = [], 0
            while size <= MAX_BYTES:
                chunk = os.read(descriptor, min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            after = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            if (size != before.st_size or stamp(before) != stamp(after) or
                    stamp(before) != stamp(os.fstat(descriptor))):
                raise ValueError()
            self.check()
            value = strict_json(b''.join(chunks))
            if (not isinstance(value, dict) or set(value) != {'format', 'key', 'created', 'report'} or
                    value['format'] != FORMAT or value['key'] != key or
                    type(value['created']) is not int or not 0 <= value['created'] <= 2 ** 53 or
                    not 0 <= time.time() - value['created'] <= TTL_SECONDS):
                raise ValueError()
            report = validate_report(value['report'])
            validate(report)
            self.check()
            if (stamp(before) != stamp(os.fstat(descriptor)) or stamp(before) != stamp(
                    os.stat(name, dir_fd=self.directory, follow_symlinks=False))):
                raise ValueError()
            return report
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def put(self, key, report, validate):
        descriptor = None
        try:
            validate_report(report)
            validate(report)
            raw = canonical({'format': FORMAT, 'key': key, 'created': int(time.time()), 'report': report})
            if len(raw) > MAX_BYTES:
                return False
            self.check()
            name = self.slot(key)
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                                 os.O_CLOEXEC, 0o600, dir_fd=self.directory)
            os.fchmod(descriptor, 0o600)
            identity = os.fstat(descriptor)
            pending = memoryview(raw)
            while pending:
                count = os.write(descriptor, pending)
                if count <= 0:
                    raise ValueError()
                pending = pending[count:]
            os.fsync(descriptor)
            info = os.fstat(descriptor)
            self.file_ok(info)
            current = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            if ((identity.st_dev, identity.st_ino) != (info.st_dev, info.st_ino) or
                    stamp(current) != stamp(info) or info.st_size != len(raw)):
                raise ValueError()
            self.check()
            return True
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            # A partial/expired/colliding slot is a miss, never pruned or replaced.
            # No unlink check/use race can delete someone else's replacement.
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

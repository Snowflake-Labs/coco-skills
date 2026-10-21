"""Explicit local outputs only. No raw snapshot persistence or automatic exposure."""
import json
import os
from pathlib import PurePosixPath
import re
import stat

from ingest import open_root
from protocol import VERSION, snapshot_identity


class OutputError(ValueError):
    """Never carry a destination name or OS error into the safe CLI."""


def passive_name(path):
    """Bounded ASCII display, not a reversible path or executable/link syntax.

    Escape punctuation, Unicode and controls as [U+XXXX]. Keep separators for
    human navigation, but never emit a raw period/colon/@/escape/HTML delimiter.
    The source hash disambiguates sanitized or truncated labels.
    """
    parts = []
    size = 0
    for character in path:
        token = character if character.isascii() and (character.isalnum() or character in '/_- ') else f'[U+{ord(character):04X}]'
        if size + len(token) > 240:
            parts.append('[truncated]')
            break
        parts.append(token)
        size += len(token)
    return ''.join(parts)


def locator_payload(name, entries):
    digest, sources = snapshot_identity(name, entries)
    return json.dumps({'classification': 'PRIVATE', 'schema_version': VERSION,
                       'snapshot_sha256': digest,
                       'files': [source | {'display_name': passive_name(entry.path)}
                                 for source, entry in zip(sources, sorted(entries, key=lambda entry: entry.path))]},
                      sort_keys=True, ensure_ascii=True).encode('ascii')


class Destination:
    """Pin an existing owner-only directory; exclusively create a 0600 file.

    No mkdir, symlink following, overwrites, stdout destinations, or automatic
    retention cleanup. Directory FDs prevent replacement redirecting writes.
    Recheck the pathname before/after creation to reject namespace races.
    """
    def __init__(self, path, candidate, suffixes):
        self.directory = None
        self.file = None
        self.identity = None
        try:
            for value in (path, candidate):
                if (not isinstance(value, str) or not value.startswith('/') or '\x00' in value
                        or any(part in ('.', '..') for part in value.split('/'))):
                    raise OutputError()
            target, source = PurePosixPath(path), PurePosixPath(candidate)
            if target == source or source in target.parents or target in source.parents:
                raise OutputError()
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', target.name)
                    or target.suffix not in suffixes):
                raise OutputError()
            self.path = str(target)
            self.candidate = str(source)
            self.parent = str(target.parent)
            self.name = target.name
            self.directory = open_root(self.parent)
            self.check_directory()
            try:
                os.stat(self.name, dir_fd=self.directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OutputError()
        except (OSError, ValueError, UnicodeError):
            self.close()
            raise OutputError() from None

    def check_directory(self):
        info = os.fstat(self.directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise OutputError()
        check = open_root(self.parent)
        try:
            current = os.fstat(check)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise OutputError()
        finally:
            os.close(check)
        # Check inode ancestry too (case-insensitive aliases and directory moves
        # can bypass lexical containment). This inspects directories, not files.
        candidate = open_root(self.candidate)
        ancestor = os.dup(self.directory)
        try:
            candidate_info = os.fstat(candidate)
            candidate_id = (candidate_info.st_dev, candidate_info.st_ino)
            for _ in range(256):
                ancestor_info = os.fstat(ancestor)
                ancestor_id = (ancestor_info.st_dev, ancestor_info.st_ino)
                if ancestor_id == candidate_id:
                    raise OutputError()
                parent = os.open('..', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                                 os.O_CLOEXEC, dir_fd=ancestor)
                parent_info = os.fstat(parent)
                if (parent_info.st_dev, parent_info.st_ino) == ancestor_id:
                    os.close(parent)
                    break
                os.close(ancestor)
                ancestor = parent
            else:
                raise OutputError()
        finally:
            os.close(candidate)
            os.close(ancestor)

    def create(self):
        self.check_directory()
        self.file = os.open(self.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                            os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.directory)
        info = os.fstat(self.file)
        self.identity = (info.st_dev, info.st_ino)
        os.fchmod(self.file, 0o600)
        self.check_file()

    def check_file(self):
        self.check_directory()
        info = os.stat(self.name, dir_fd=self.directory, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid() or
                (info.st_dev, info.st_ino) != self.identity):
            raise OutputError()

    def write(self, data):
        self.check_file()
        remaining = memoryview(data)
        while remaining:
            count = os.write(self.file, remaining)
            if count <= 0:
                raise OutputError()
            remaining = remaining[count:]
        os.fsync(self.file)
        self.check_file()

    def discard(self):
        # Only remove the inode we created, never an attacker's replacement.
        if self.identity is not None:
            try:
                info = os.stat(self.name, dir_fd=self.directory, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == self.identity:
                    os.unlink(self.name, dir_fd=self.directory)
            except OSError:
                pass

    def close(self):
        for descriptor in (self.file, self.directory):
            if descriptor is not None:
                os.close(descriptor)
        self.file = self.directory = None


def write_outputs(outputs):
    """Reserve every destination before writing; roll back only our own inodes."""
    try:
        for destination, _ in outputs:
            destination.create()
        for destination, data in outputs:
            destination.write(data)
    except (OSError, ValueError):
        for destination, _ in outputs:
            destination.discard()
        raise OutputError() from None

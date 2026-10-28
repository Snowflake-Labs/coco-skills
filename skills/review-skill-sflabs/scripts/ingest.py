"""Bounded no-follow byte collection. No parsing, execution, or diagnostic content."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import PurePosixPath
import stat

MAX_FILES = 512
MAX_ENTRIES = 1024
MAX_DEPTH = 12
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_WIRE_BYTES = 4 * 1024 * 1024


class UnsafeInput(ValueError):
    """Intentionally carries no candidate-controlled error text."""


class SymlinkInput(UnsafeInput, OSError):
    """A path component or bundle entry is a symbolic link; reported distinctly.

    Also an OSError so existing open_root callers that fail closed on OSError
    (for example git_context checkouts) keep rejecting links unchanged.
    """


def _raise_if_link(name, directory):
    """Re-raise a failed no-follow open as SymlinkInput when name is a link."""
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise SymlinkInput() from None


@dataclass(frozen=True)
class Entry:
    path: str
    data: bytes


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_root(path: str) -> int:
    """Open every absolute component without following a link (POSIX only)."""
    if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
        raise UnsafeInput()
    parts = path.split('/')[1:]
    if any(part in ('.', '..') for part in parts):
        raise UnsafeInput()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        for part in filter(None, parts):
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError:
                # macOS /tmp -> /private/tmp is the common accidental case.
                _raise_if_link(part, descriptor)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def absent(path: str) -> bool:
    """Prove absence without following even a dangling parent symlink."""
    if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
        raise UnsafeInput()
    parts = [part for part in path.split('/') if part]
    if not parts or any(part in ('.', '..') for part in parts):
        raise UnsafeInput()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                return True
            os.close(descriptor)
            descriptor = child
        try:
            os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    except OSError:
        raise UnsafeInput() from None
    finally:
        os.close(descriptor)


def collect(path: str, selected: tuple[str, ...] | None = None) -> tuple[Entry, ...]:
    """Copy bounded regular files into memory; reject links and concurrent changes."""
    entries = []
    total_bytes = 0
    entry_count = 0
    if selected is not None and (not selected or any(
            not path or any(part in ('', '.', '..') for part in path.split('/'))
            for path in selected)):
        raise UnsafeInput()

    def included(path):
        return selected is None or any(path == target or path.startswith(target + '/')
                                       or target.startswith(path + '/') for target in selected)

    def walk(directory, prefix, depth):
        nonlocal total_bytes, entry_count
        if depth > MAX_DEPTH:
            raise UnsafeInput()
        before = os.fstat(directory)
        names = []
        with os.scandir(directory) as iterator:
            for item in iterator:
                entry_count += 1
                if entry_count > MAX_ENTRIES:
                    raise UnsafeInput()
                names.append(item.name)
        for name in sorted(names):
            if not included(prefix + name):
                continue
            if name == 'skill_metadata':
                raise UnsafeInput()
            # Surrogate filenames and control characters must not reach artifacts.
            if any(ord(char) < 32 or ord(char) == 127 for char in name):
                raise UnsafeInput()
            name.encode('utf-8', errors='strict')
            relative = prefix + name
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                try:
                    if stamp(info) != stamp(os.fstat(child)):
                        raise UnsafeInput()
                    walk(child, relative + '/', depth + 1)
                    if stamp(info) != stamp(os.stat(name, dir_fd=directory, follow_symlinks=False)):
                        raise UnsafeInput()
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if len(entries) >= MAX_FILES or info.st_size > MAX_FILE_BYTES:
                    raise UnsafeInput()
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=directory)
                try:
                    if stamp(info) != stamp(os.fstat(descriptor)):
                        raise UnsafeInput()
                    chunks = []
                    size = 0
                    while True:
                        chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        total_bytes += len(chunk)
                        if size > MAX_FILE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                            raise UnsafeInput()
                        chunks.append(chunk)
                    if stamp(info) != stamp(os.fstat(descriptor)) or size != info.st_size:
                        raise UnsafeInput()
                    if stamp(info) != stamp(os.stat(name, dir_fd=directory, follow_symlinks=False)):
                        raise UnsafeInput()
                    entries.append(Entry(relative, b''.join(chunks)))
                finally:
                    os.close(descriptor)
            elif stat.S_ISLNK(info.st_mode):
                raise SymlinkInput()
            else:
                raise UnsafeInput()
        if stamp(before) != stamp(os.fstat(directory)):
            raise UnsafeInput()

    root = None
    try:
        root = open_root(path)
        identity = stamp(os.fstat(root))
        walk(root, '', 0)
        check = open_root(path)
        try:
            if identity != stamp(os.fstat(check)):
                raise UnsafeInput()
        finally:
            os.close(check)
        if selected is not None and any(not any(
                entry.path == target or entry.path.startswith(target + '/') for entry in entries)
                for target in selected):
            raise UnsafeInput()
        return tuple(sorted(entries, key=lambda entry: entry.path))
    except SymlinkInput:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise UnsafeInput() from None
    finally:
        if root is not None:
            os.close(root)


PLUGIN_LICENSE = 'PLUGIN_LICENSE'


def plugin_root(path: str) -> str | None:
    """Return <plugin> for a .../<plugin>/skills/<skill> candidate, else None.

    A plugin is either under a plugins/ folder or has a .cortex-plugin/ folder.
    Only the plugin directory itself is probed; nothing above it is read.
    """
    parts = [part for part in path.split('/') if part]
    if len(parts) < 3 or parts[-2] != 'skills':
        return None
    root = '/' + '/'.join(parts[:-2])
    if len(parts) >= 4 and parts[-4] == 'plugins':
        return root
    return None if absent(root + '/.cortex-plugin') else root


def plugin_license(path: str) -> Entry | None:
    """Capture a plugin skill's inherited root LICENSE as a reserved entry.

    Every plugin must have a root LICENSE and its skills inherit it. A missing
    file yields empty bytes so the license check fails rather than falling back
    to any skill-level file. Links and special files are rejected; the read uses
    the same no-follow, single-link, size and concurrent-change rules as collect.
    """
    root = plugin_root(path)
    if root is None:
        return None
    directory = None
    try:
        directory = open_root(root)
        try:
            info = os.stat('LICENSE', dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return Entry(PLUGIN_LICENSE, b'')
        if stat.S_ISLNK(info.st_mode):
            raise SymlinkInput()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
            raise UnsafeInput()
        descriptor = os.open('LICENSE', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        try:
            chunks, size = [], 0
            while chunk := os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise UnsafeInput()
            data = b''.join(chunks)
            if len(data) != info.st_size or stamp(info) != stamp(os.fstat(descriptor)):
                raise UnsafeInput()
        finally:
            os.close(descriptor)
        return Entry(PLUGIN_LICENSE, data)
    except SymlinkInput:
        raise
    except (OSError, ValueError):
        raise UnsafeInput() from None
    finally:
        if directory is not None:
            os.close(directory)


def encode(entries: tuple[Entry, ...], name: str) -> bytes:
    value = {'name': name, 'files': [
        {'path': entry.path, 'data': base64.b64encode(entry.data).decode('ascii')}
        for entry in entries]}
    wire = json.dumps(value, ensure_ascii=True).encode('ascii')
    if len(wire) > MAX_WIRE_BYTES:
        raise UnsafeInput()
    return wire


def decode(wire: bytes) -> tuple[str, tuple[Entry, ...]]:
    """Revalidate the wire format in the container; never materialize paths."""
    if len(wire) > MAX_WIRE_BYTES:
        raise UnsafeInput()
    value = json.loads(wire)
    if not isinstance(value, dict) or set(value) != {'name', 'files'}:
        raise UnsafeInput()
    if not isinstance(value['name'], str) or len(value['name']) > 255:
        raise UnsafeInput()
    if not isinstance(value['files'], list) or len(value['files']) > MAX_FILES:
        raise UnsafeInput()
    entries = []
    seen = set()
    total = 0
    for item in value['files']:
        if not isinstance(item, dict) or set(item) != {'path', 'data'}:
            raise UnsafeInput()
        path, data = item['path'], item['data']
        if not isinstance(path, str) or not path or len(path) > 4096:
            raise UnsafeInput()
        parts = path.split('/')
        if (PurePosixPath(path).is_absolute() or len(parts) > MAX_DEPTH + 1
                or any(part in ('', '.', '..') for part in parts)
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or path in seen):
            raise UnsafeInput()
        if not isinstance(data, str):
            raise UnsafeInput()
        content = base64.b64decode(data, validate=True)
        total += len(content)
        if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise UnsafeInput()
        seen.add(path)
        entries.append(Entry(path, content))
    if any('/'.join(path.split('/')[:index]) in seen
           for path in seen for index in range(1, len(path.split('/')))):
        raise UnsafeInput()
    return value['name'], tuple(sorted(entries, key=lambda entry: entry.path))


def lf_line_count(content: str | bytes, end: int | None = None) -> int:
    """One-based LF coordinates: empty input is line 1; final LF starts a new line.

    CRLF counts once. Bare CR, VT, FF and Unicode separators never add lines.
    An optional exclusive end maps an offset to the same source coordinates.
    """
    separator = b'\n' if isinstance(content, bytes) else '\n'
    return content.count(separator, 0, end) + 1


def lf_lines(text: str) -> list[str]:
    """Split only at LF, removing CR only when paired with LF; retain final empty line."""
    return text.replace('\r\n', '\n').split('\n')


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def manifest(entries: tuple[Entry, ...], name: str) -> tuple[list[dict], str]:
    """Bind ordered paths, bytes and directory name; source identity survives edits.

    Path hashes are identifiers, not anonymization or proof of authorship. Renames
    intentionally change identity; ordinal IDs are only local snapshot locators.
    """
    records = [{'id': f'F{index:04d}',
                'path_sha256': hashlib.sha256(entry.path.encode('utf-8')).hexdigest(),
                'sha256': hashlib.sha256(entry.data).hexdigest(),
                'bytes': len(entry.data)}
               for index, entry in enumerate(sorted(entries, key=lambda entry: entry.path), 1)]
    digest = hashlib.sha256(canonical({'domain': 'skill-snapshot-v2', 'name': name,
                                       'files': records})).hexdigest()
    return records, digest

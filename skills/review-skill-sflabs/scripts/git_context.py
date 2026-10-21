"""Local Git metadata only; no checkout, archive extraction, filters or hooks."""
import hashlib
import os
import subprocess
import tempfile

from ingest import MAX_FILES, MAX_WIRE_BYTES, UnsafeInput, absent, open_root
from protocol import is_hex


def git(repository, *arguments):
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1',
                       GIT_TERMINAL_PROMPT='0', GIT_LITERAL_PATHSPECS='1')
    command = ['git', '--no-pager', '--no-optional-locks', '-c', 'core.hooksPath=/dev/null',
               '-c', 'core.fsmonitor=false', '-c', 'protocol.allow=never',
               '-c', 'submodule.recurse=false', '-C', repository, *arguments]
    with tempfile.TemporaryFile() as output:
        subprocess.run(command, env=environment, check=True, stdout=output,
                       stderr=subprocess.DEVNULL, timeout=30)
        output.seek(0)
        data = output.read(MAX_WIRE_BYTES + 1)
    if len(data) > MAX_WIRE_BYTES:
        raise UnsafeInput()
    return data


def valid_name(name):
    if (not isinstance(name, str) or not name or len(name.encode('utf-8')) > 255
            or name in ('.', '..') or '/' in name
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise UnsafeInput()
    return name


class Checkout:
    """Pin no-follow checkout directories and require HEAD at the requested commit.

    Separate ordinary checkouts only (.git directories); linked worktrees are not
    supported. The reviewed wire is compared to raw blob IDs, not Git's index or
    stat cache, so dirty/ignored files and clean/smudge filters cannot hide bytes.
    """
    def __init__(self, root, revision):
        if not is_hex(revision, 40):
            raise UnsafeInput()
        self.root, self.revision = root, revision
        self.identity = None
        self.check()

    def check(self):
        identities = []
        for path in (self.root, self.root + '/.git'):
            descriptor = open_root(path)
            try:
                info = os.fstat(descriptor)
                identities.append((info.st_dev, info.st_ino))
            finally:
                os.close(descriptor)
        if self.identity is not None and self.identity != identities:
            raise UnsafeInput()
        self.identity = identities
        if git(self.root, 'rev-parse', '--verify', 'HEAD').strip() != self.revision.encode('ascii'):
            raise UnsafeInput()
        # Ensure -C did not select a parent repository.
        if git(self.root, 'rev-parse', '--show-prefix').strip():
            raise UnsafeInput()

    def files(self, name, revision=None):
        revision = revision or self.revision
        if not is_hex(revision, 40):
            raise UnsafeInput()
        valid_name(name)
        prefix = 'skills/' + name
        data = git(self.root, 'ls-tree', '-r', '-z', revision, '--', prefix)
        records = {}
        for record in data.split(b'\0'):
            if not record:
                continue
            metadata, raw_path = record.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split(' ')
            path = raw_path.decode('utf-8')
            if not (path == prefix or path.startswith(prefix + '/')):
                raise UnsafeInput()
            relative = path[len(prefix) + 1:] if path.startswith(prefix + '/') else ''
            if not is_hex(oid, 40) or relative in records:
                raise UnsafeInput()
            records[relative] = (mode, kind, oid)
            if len(records) > MAX_FILES:
                raise UnsafeInput()
        return records

    def missing(self, name):
        return absent(self.root + '/skills/' + valid_name(name))


def verify_entries(entries, expected):
    """Compare raw Git blobs without invoking filters, candidate code, or the index."""
    if set(expected) != {entry.path for entry in entries}:
        raise UnsafeInput()
    for entry in entries:
        mode, kind, oid = expected[entry.path]
        raw = b'blob ' + str(len(entry.data)).encode('ascii') + b'\0' + entry.data
        if (mode not in ('100644', '100755') or kind != 'blob'
                or hashlib.sha1(raw).hexdigest() != oid):
            raise UnsafeInput()


def selection(repository, base, head):
    if not all(is_hex(revision, 40) for revision in (base, head)):
        raise ValueError()
    # Compare the exact supplied trees, not an implicit merge base.
    data = git(repository, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
               '--name-only', '-z', base, head, '--', 'skills/')
    names = set()
    for raw in data.split(b'\0'):
        if raw:
            parts = raw.decode('utf-8').split('/')
            if len(parts) >= 2 and parts[0] == 'skills':
                names.add(valid_name(parts[1]))
    if len(names) > 100:
        raise UnsafeInput()
    return sorted(names)


def validate_renames(mapping, old_files, new_files):
    """Explicit operator mappings only; no Git similarity or candidate claims.

    Accept only disjoint complete deletion/addition pairs. Partial moves, merges,
    splits and ambiguous pairs must be reviewed separately without rename claims.
    """
    if not isinstance(mapping, list) or len(mapping) > 100:
        raise UnsafeInput()
    pairs = []
    used = set()
    for pair in mapping:
        if not isinstance(pair, dict) or set(pair) != {'base', 'head'}:
            raise UnsafeInput()
        old, new = valid_name(pair['base']), valid_name(pair['head'])
        if (old == new or old in used or new in used or not old_files.get(old)
                or new_files.get(old) or old_files.get(new) or not new_files.get(new)):
            raise UnsafeInput()
        pairs.append((old, new))
        used.update((old, new))
    return pairs


def source_renames(mapping, pairs, old_files, new_files):
    """Validate explicit full-path moves against selected trees, without guessing."""
    if not isinstance(mapping, list) or len(mapping) > MAX_FILES:
        raise UnsafeInput()
    result = {pair: {} for pair in pairs}
    destinations = set()
    for item in mapping:
        if not isinstance(item, dict) or set(item) != {'base', 'head'}:
            raise UnsafeInput()
        parsed = []
        for key in ('base', 'head'):
            path = item[key]
            if not isinstance(path, str) or len(path) > 4096:
                raise UnsafeInput()
            parts = path.split('/')
            if len(parts) < 3 or parts[0] != 'skills':
                raise UnsafeInput()
            for part in parts:
                valid_name(part)
            parsed.append((parts[1], '/'.join(parts[2:])))
        (old, old_path), (new, new_path) = parsed
        pair = (old, new)
        if (pair not in result or old_path not in old_files[old] or new_path not in new_files[new]
                or old_path in new_files[new] or new_path in old_files[old]
                or any(files[path][0] not in ('100644', '100755') for files, path in
                       ((old_files[old], old_path), (new_files[new], new_path)))):
            raise UnsafeInput()
        source = hashlib.sha256(old_path.encode('utf-8')).hexdigest()
        target = hashlib.sha256(new_path.encode('utf-8')).hexdigest()
        if source in result[pair] or (pair, target) in destinations:
            raise UnsafeInput()
        result[pair][source] = target
        destinations.add((pair, target))
    return result

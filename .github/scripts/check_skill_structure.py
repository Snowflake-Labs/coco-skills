#!/usr/bin/env python3
"""Inspect changed skill structures as data, without executing candidate code.

The trusted entry point accepts exact base/head commit IDs from the CLI or a
validated PR event. Offline mode reads an existing trusted Git database; public
fetch mode creates a temporary bare database and fetches from a constructed
GitHub URL. Neither mode checks out the candidate tree. A NUL-delimited diff
and direct-child tree comparison select changed skill roots, then the checker
walks each retained root in full. Only SKILL.md and LICENSE blobs are decoded;
support-file contents, including executable blobs, are not read or run.

The checker, interpreter, PyYAML installation, and local Git configuration are
trusted inputs. In particular, --repository is not a sandbox for a hostile Git
configuration. Candidate object contents, paths, and event fields are untrusted.
Run this trusted script with Python -I -B and the workflow's hash-locked PyYAML:
-I excludes script/current-directory imports and Python environment overrides;
-B avoids bytecode writes. These flags do not make an untrusted script safe.

Inspection is bounded by object/output, tree, YAML, and diagnostic budgets.
Diagnostics use fixed messages and percent-encoded paths, not candidate prose.
An optional summary is written only to an explicitly trusted CLI path; event
contents cannot select local paths. Network access requires --fetch-public.
Exit 0 means valid/no relevant changes, 1 means structural violations, and 2
means incomplete/unavailable inspection, including a budget or I/O failure.
Passing does not establish execution safety, semantic correctness, or legal
approval. This module docstring also supplies argparse's CLI help description.
"""

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.parse import quote_from_bytes

try:
    import yaml
except ImportError:
    print("[incomplete] trusted PyYAML dependency is unavailable")
    sys.exit(2)

# These conservative inspection ceilings bound work, not acceptable project size.
# Exceeding one reports incomplete rather than declaring uninspected data valid.
# Tree entry/depth limits apply per skill; MAX_DEPTH also bounds YAML composition.
MAX_SKILLS, MAX_ENTRIES, MAX_DEPTH = 20, 500, 12
# Required text files have separate caps; ordinary support blobs need no decoding.
MAX_SKILL, MAX_LICENSE = 64 * 1024, 256 * 1024
# Plugin manifests are small JSON documents; plugins are bounded separately
# from skills because one plugin can contribute several skills.
MAX_MANIFEST, MAX_PLUGINS = 64 * 1024, 20
# Git stdout AND stderr consume per-command and per-Git-instance byte budgets.
# MAX_OUTPUT also caps the event document; diagnostic count caps report growth.
MAX_OUTPUT, MAX_TOTAL, MAX_DIAGNOSTICS = 1024 * 1024, 16 * 1024 * 1024, 100
# Node count bounds wide YAML as depth bounds nested YAML. Fetch gets a longer
# wall-clock allowance than local Git; neither timeout is a CPU/memory sandbox.
MAX_NODES, GIT_TIMEOUT, FETCH_TIMEOUT = 4096, 15, 90
# Exact identifiers avoid symbolic revision interpretation and free-form URLs.
SHA = re.compile(r"[0-9a-f]{40}\Z")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")


class Incomplete(Exception):
    """Signal that inspection could not finish, yielding exit status 2.

    Callers supply only fixed, trusted messages: main renders this exception's
    text directly. Use for exhausted budgets, unavailable objects, or malformed
    transport/event data, not a known structural violation in a required file.
    """


class Invalid(Exception):
    """Signal a known content violation, normally recorded as exit status 1.

    Supply only fixed, trusted messages because required-file inspection renders
    them directly. Parser exception details and candidate values must not leak
    into these messages. Budget exhaustion instead uses Incomplete.
    """


class LimitedLoader(yaml.SafeLoader):
    """Compose bounded YAML and construct only SafeLoader-supported values.

    Reject aliases, unsupported tags, duplicate keys, and non-string keys with
    Invalid; raise Incomplete when composition exceeds depth/node budgets.
    SafeLoader avoids Python-object constructors, but is not a process sandbox.
    Blob-size checks outside this class provide the input-byte bound.
    """

    def __init__(self, stream):
        """Accept YAML text/stream and initialize per-document composition counts.

        No value is returned; stream/scanner setup errors propagate to the
        frontmatter wrapper. Callers must supply already size-bounded input.
        """
        super().__init__(stream)
        self.depth = self.nodes = 0

    def compose_node(self, parent, index):
        """Return one YAML node using PyYAML's parent/index traversal context.

        Reject aliases/tags with Invalid before constructing values; depth or
        cumulative node excess raises Incomplete. Parser errors propagate.
        """
        # Alias rejection avoids shared/cyclic object graphs; counting each node
        # also bounds wide documents that would fit beneath the nesting limit.
        if self.check_event(yaml.AliasEvent):
            raise Invalid("YAML aliases are not allowed")
        self.depth += 1
        self.nodes += 1
        if self.depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise Incomplete("YAML depth or node budget exceeded")
        try:
            node = super().compose_node(parent, index)
            if node.tag not in self.yaml_constructors or node.tag is None:
                raise Invalid("unsupported YAML tag")
            return node
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        """Return a dict from a mapping node, honoring PyYAML's deep flag.

        Invalid rejects non-string or duplicate keys at any mapping level.
        Nested construction errors propagate. Do not flatten YAML merge keys:
        the supported-tag check rejects them instead of silently merging data.
        """
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise Invalid("YAML mapping keys must be strings")
            if key in result:
                raise Invalid("duplicate YAML mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def frontmatter(text):
    """Return the leading YAML mapping from decoded SKILL.md text.

    The first line and a later closing line must be exactly '---'; the body
    after that closing delimiter is ignored. Invalid covers missing delimiters,
    non-mappings, and parser/content errors; Incomplete budget errors propagate.
    The caller, not this helper, enforces the byte size and UTF-8 requirements.
    """
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise Invalid("missing opening frontmatter delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise Invalid("missing closing frontmatter delimiter") from None
    try:
        data = yaml.load("\n".join(lines[1:end]), Loader=LimitedLoader)
    except (yaml.YAMLError, ValueError, TypeError, OverflowError, RecursionError):
        # YAML errors can quote candidate text; replace them with a fixed message.
        raise Invalid("invalid YAML frontmatter") from None
    if not isinstance(data, dict):
        raise Invalid("frontmatter must be a mapping")
    return data


class Report:
    """Accumulate bounded, render-safe diagnostics and the highest exit severity.

    Messages and levels come from trusted callers; only path bytes may be
    candidate-controlled. Exceeding the line budget raises Incomplete instead
    of silently producing an apparently complete report.
    """

    def __init__(self):
        """Initialize an empty report with success status; accept no inputs."""
        self.lines = []
        self.status = 0

    def add(self, level, message, path=b""):
        """Append a trusted level/message with an optional untrusted byte path.

        Return None and update status monotonically (fail=1, incomplete=2).
        Unknown levels remain informational. Raise Incomplete if the diagnostic
        budget is full; never interpolate candidate content into message itself.
        """
        if len(self.lines) >= MAX_DIAGNOSTICS:
            raise Incomplete("diagnostic budget exceeded; results omitted")
        self.status = max(self.status, {"fail": 1, "incomplete": 2}.get(level, 0))
        # Encode bytes rather than decoding a possibly malformed Git filename.
        # Only slash and URL-unreserved characters stay literal, so terminal
        # controls, Markdown syntax, and workflow-command punctuation stay inert.
        # Truncation precedes encoding to bound expansion; '...' marks omission.
        suffix = " path=" + quote_from_bytes(path[:160], safe="/") if path else ""
        if len(path) > 160:
            suffix += "..."
        self.lines.append(f"[{level}] {message}{suffix}")


def metadata(data, directory, report, path):
    """Record metadata findings from a parsed mapping; return None.

    directory is the decoded skill folder name and path is its diagnostic byte
    path. Required names/descriptions and known field types are hard failures;
    optional id must match the folder slug. Legacy enums/lengths are advisory,
    and unknown fields are allowed. Report budget exhaustion propagates as
    Incomplete; candidate values themselves are never printed.
    """
    for field in ("name", "description"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            report.add("fail", f"{field} must be a nonblank string", path)
    for field in ("id", "title", "summary", "author", "prompt", "language", "type", "status"):
        if field in data and not isinstance(data[field], str):
            report.add("fail", f"{field} must be a string", path)
    for field in ("tools", "authors", "categories", "tags", "allowed-tools"):
        if field not in data:
            continue
        value = data[field]
        valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
        if field != "tools":
            valid = valid or isinstance(value, str)
        if not valid:
            report.add("fail", f"{field} has an invalid metadata type", path)
    if "id" in data and (not isinstance(data["id"], str) or
                         not SLUG.fullmatch(data["id"]) or data["id"] != directory):
        report.add("fail", "id must be a lowercase hyphenated slug matching the directory", path)
    for field, choices in (("type", ("community", "snowflake", "partner")),
                           ("status", ("stable", "beta", "draft", "Published", "Archived", "Hidden")),
                           ("language", ("en", "es", "it", "fr", "ja", "ko", "pt_br"))):
        if isinstance(data.get(field), str) and data[field] not in choices:
            report.add("advisory", f"{field} is outside published legacy values", path)
    for field, limit in (("title", 30), ("summary", 140)):
        if isinstance(data.get(field), str) and len(data[field]) > limit:
            report.add("advisory", f"{field} exceeds the recommended length", path)


def check_sha(value):
    """Return an exact lowercase 40-hex revision string, or raise Incomplete.

    This checks syntax only; Git.commit must separately verify object existence,
    type, and identity. Symbolic refs, abbreviated IDs, and options are rejected.
    """
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise Incomplete("revision must be an exact lowercase 40-character SHA")
    return value


def check_path(path):
    """Return UTF-8 text for a Git path supplied as bytes, or raise Incomplete.

    Reject overlong paths, empty/dot components, and Unicode category-C chars
    (including controls and formatting characters). Shell/Markdown punctuation
    remains valid data: argv-based Git calls and report encoding handle it.
    This validates object names, not filesystem containment or symlink targets.
    """
    try:
        text = path.decode("utf-8")
    except UnicodeDecodeError:
        raise Incomplete("path is not UTF-8") from None
    if (len(path) > 1024 or any(part in ("", ".", "..") for part in text.split("/")) or
            any(unicodedata.category(char).startswith("C") for char in text)):
        raise Incomplete("malformed path or path budget exceeded")
    return text


class Git:
    """Run bounded Git plumbing against a trusted local object database/config.

    Candidate objects stay data: inspection uses neither a working-tree checkout
    nor content filters. Process settings reduce ambient hooks, credentials,
    replacement objects, and network access, but do not sanitize an arbitrary
    hostile local Git config. Each instance tracks cumulative output bytes.
    """

    def __init__(self, repository):
        """Resolve a trusted repository path and reset its output-byte budget.

        No Git command runs and no value is returned. Filesystem resolution
        errors propagate; existence and Git validity are checked by later calls.
        """
        self.repository = str(Path(repository).resolve())
        self.total = 0

    def run(self, *args, limit=MAX_OUTPUT, network=False):
        """Return stdout bytes for trusted Git argv, with no shell interpretation.

        limit bounds combined stdout/stderr; self.total bounds all calls on this
        instance. network enables HTTPS and the longer fetch deadline, not other
        protocols. stderr is counted but never rendered. Nonzero exit, timeout,
        excessive output, or OS launch/I/O failure raises a fixed Incomplete.
        Other exceptions propagate after terminating/reaping the child group.
        """
        # Start from a minimal environment rather than inheriting Git overrides.
        # The trusted repository's own config still exists; these are targeted
        # command-line restrictions, not a general untrusted-config validator.
        env = {"PATH": os.defpath, "HOME": os.devnull, "LC_ALL": "C",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1",
               "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}
        command = ["git", "--no-replace-objects", "-c", "core.hooksPath=" + os.devnull,
                   "-c", "core.fsmonitor=false", "-c", "credential.helper=",
                   "-c", "protocol.allow=never", "-c", "protocol.https.allow=" +
                   ("always" if network else "never"), "-c", "submodule.recurse=false",
                   "-c", "http.followRedirects=false", "-C", self.repository, *args]
        deadline = time.monotonic() + (FETCH_TIMEOUT if network else GIT_TIMEOUT)
        output, received = bytearray(), 0
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  stdin=subprocess.DEVNULL, env=env, start_new_session=True) as process:
                try:
                    # Drain both pipes concurrently: a child blocked on stderr
                    # must not evade the deadline while we wait for stdout. EOF
                    # unregisters a pipe; the loop continues while either is open.
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        selector.register(process.stderr, selectors.EVENT_READ)
                        while selector.get_map():
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise Incomplete("Git operation timed out")
                            for key, _ in selector.select(min(remaining, 0.1)):
                                chunk = os.read(key.fileobj.fileno(), 8192)
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                    continue
                                # Count before retaining stdout. Discarding stderr
                                # is not permission to emit unlimited stderr.
                                received += len(chunk)
                                self.total += len(chunk)
                                if received > limit or self.total > MAX_TOTAL:
                                    raise Incomplete("Git output budget exceeded")
                                if key.fileobj is process.stdout:
                                    output.extend(chunk)
                    # Closed pipes do not imply an exited child. Reuse the same
                    # deadline rather than granting a second full timeout.
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    if process.returncode:
                        raise Incomplete("Git operation failed or object unavailable")
                except BaseException:
                    # start_new_session gives this command its own process group.
                    # Fetch helpers may hold pipes open after the main process
                    # exits; kill the group on any aborted read/wait, then reap
                    # the direct child before leaving Popen's context manager.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    raise
        except subprocess.TimeoutExpired:
            raise Incomplete("Git operation timed out") from None
        except OSError:
            raise Incomplete("Git executable or repository unavailable") from None
        return bytes(output)

    def commit(self, revision):
        """Verify that an exact revision names that same commit; return None.

        Incomplete covers bad syntax, unavailable/non-commit objects, mismatched
        resolution, and Git limits/errors. Reject a tag/blob rather than letting
        revision peeling silently change the requested inspection identity.
        """
        check_sha(revision)
        if self.run("cat-file", "-t", revision, limit=128) != b"commit\n":
            raise Incomplete("revision does not identify a commit")
        actual = self.run("rev-parse", "--verify", revision + "^{commit}", limit=128)
        if actual != (revision + "\n").encode("ascii"):
            raise Incomplete("resolved revision does not match requested SHA")

    def tree(self, object_id):
        """Return immediate byte-name -> (mode bytes, kind bytes, SHA text) entries.

        object_id comes from a verified commit or a previously parsed tree entry.
        Incomplete covers malformed/truncated output, duplicate or invalid child
        names, invalid IDs, and Git failures. This helper preserves modes/kinds;
        callers decide which combinations are allowed and validate full paths.
        """
        # -z uses NUL record separators, not whitespace or quoted filenames.
        # Split only the first tab: a filename can itself contain tabs/newlines.
        raw = self.run("ls-tree", "-z", object_id)
        if raw and not raw.endswith(b"\0"):
            raise Incomplete("incomplete Git tree output")
        entries = {}
        for record in raw.split(b"\0")[:-1]:
            try:
                header, name = record.split(b"\t", 1)
                mode, kind, oid = header.split(b" ")
                check_sha(oid.decode("ascii"))
                if name in entries or b"/" in name or not name:
                    raise ValueError
                entries[name] = (mode, kind, oid.decode("ascii"))
            except (ValueError, UnicodeError):
                raise Incomplete("malformed Git tree output") from None
        return entries

    def text(self, oid, limit):
        """Return nonblank UTF-8 text from a required blob ID within byte limit.

        Check size before requesting contents and verify the returned byte count.
        Size/protocol/Git failures raise Incomplete; non-UTF-8 or blank content
        raises Invalid. Only callers with an allowed regular-file mode use this
        helper; support blobs are never decoded merely because they are present.
        """
        size = self.run("cat-file", "-s", oid, limit=128)
        if not re.fullmatch(rb"[0-9]+\n", size):
            raise Incomplete("invalid blob size output")
        size = int(size)
        if size > limit:
            raise Incomplete("required file size budget exceeded")
        raw = self.run("cat-file", "blob", oid, limit=limit + 128)
        if len(raw) != size:
            raise Incomplete("blob size does not match inspected size")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Invalid("required file must be UTF-8") from None
        if not text.strip():
            raise Invalid("required file must be nonempty")
        return text


def inspect_skill(git, root, entry, report, inherited=False):
    """Inspect one retained skill tree and add findings; return None.

    git reads objects, root is a validated byte path, and entry is its tree-entry
    tuple. Walk every descendant mode/name, but read only root SKILL.md/LICENSE.
    inherited means a plugin skill whose LICENSE requirement is satisfied by the
    plugin root (checked by inspect_plugin); a skill-level LICENSE is then
    optional but still validated when present. Record structural/content
    violations in report; propagate Incomplete on traversal, object, or report
    budgets so partial inspection cannot pass.
    """
    if entry[:2] != (b"040000", b"tree"):
        report.add("fail", "skill root must be a directory, not a link or blob", root)
        return
    count, required = 0, {}

    def walk(oid, prefix, depth):
        """Visit tree oid at byte prefix/depth; update outer count and required IDs.

        Return None. Invalid modes become report failures; path, tree, depth,
        entry-count, or diagnostic-budget failures propagate as Incomplete.
        Descendants contribute to one per-skill count, not one count per folder.
        """
        nonlocal count
        if depth > MAX_DEPTH:
            raise Incomplete("skill depth budget exceeded")
        for name, (mode, kind, child) in git.tree(oid).items():
            path = prefix + b"/" + name
            check_path(path)
            count += 1
            if count > MAX_ENTRIES:
                raise Incomplete("skill entry or depth budget exceeded")
            # Git modes describe stored objects, not the local filesystem.
            # 040000 is a directory; 100644/100755 are regular blobs. Executable
            # mode is allowed as data, never as permission to run a candidate.
            # Reject symlinks (120000), gitlinks (160000), and other combinations.
            if mode == b"040000" and kind == b"tree":
                walk(child, path, depth + 1)
            elif mode in (b"100644", b"100755") and kind == b"blob":
                if prefix == root and name in (b"SKILL.md", b"LICENSE"):
                    required[name] = child
            else:
                report.add("fail", "only regular blobs and directories are allowed", path)

    walk(entry[2], root, 1)
    for name, limit in ((b"SKILL.md", MAX_SKILL), (b"LICENSE", MAX_LICENSE)):
        path = root + b"/" + name
        if name not in required:
            if not (inherited and name == b"LICENSE"):
                report.add("fail", "missing required regular file", path)
            continue
        try:
            text = git.text(required[name], limit)
            if name == b"SKILL.md":
                metadata(frontmatter(text), root.rsplit(b"/", 1)[1].decode("utf-8"), report, path)
        except Invalid as error:
            report.add("fail", str(error), path)


def inspect_plugin(git, root, entry, report):
    """Check one retained plugin's required files; return None.

    Follows plugins/README.md: .cortex-plugin/plugin.json is a JSON object with
    nonblank string name/version/description, and name matches the slug folder.
    A root LICENSE is required because plugin skills inherit it. Only these two
    blobs are decoded; hooks, agents, MCP configs, and assets remain unread data.
    """
    name = root.split(b"/")[1]
    if not SLUG.fullmatch(name.decode("utf-8")):
        report.add("fail", "plugin directory must be a lowercase hyphenated slug", root)
    if entry[:2] != (b"040000", b"tree"):
        report.add("fail", "plugin root must be a directory, not a link or blob", root)
        return
    children = git.tree(entry[2])
    path = root + b"/LICENSE"
    license = children.get(b"LICENSE")
    if not license or license[:2] not in ((b"100644", b"blob"), (b"100755", b"blob")):
        report.add("fail", "missing required regular file", path)
    else:
        try:
            git.text(license[2], MAX_LICENSE)
        except Invalid as error:
            report.add("fail", str(error), path)
    path = root + b"/.cortex-plugin/plugin.json"
    folder = children.get(b".cortex-plugin")
    manifest = git.tree(folder[2]).get(b"plugin.json") if folder and folder[:2] == (b"040000", b"tree") else None
    if not manifest or manifest[:2] not in ((b"100644", b"blob"), (b"100755", b"blob")):
        report.add("fail", "missing required regular file", path)
        return
    try:
        data = json.loads(git.text(manifest[2], MAX_MANIFEST))
    except Invalid as error:
        report.add("fail", str(error), path)
        return
    except (ValueError, RecursionError):
        report.add("fail", "plugin manifest must be valid JSON", path)
        return
    if not isinstance(data, dict):
        report.add("fail", "plugin manifest must be a JSON object", path)
        return
    for field in ("name", "version", "description"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            report.add("fail", f"{field} must be a nonblank string", path)
    if isinstance(data.get("name"), str) and data["name"] != name.decode("utf-8"):
        report.add("fail", "plugin name must match the directory", path)


def subtree(git, entry, name):
    """Return a directory child's entry tuple, or None when absent or not a tree."""
    if not entry or entry[:2] != (b"040000", b"tree"):
        return None
    return git.tree(entry[2]).get(name)


def changed_children(git, before, after):
    """Return direct-child names whose entry tuples differ between two trees.

    Git diff omits empty trees, so comparing entries keeps even a synthetic
    empty skill tree visible. Non-tree sides contribute no children.
    """
    old, new = entries(git, before), entries(git, after)
    return {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)}, new


def entries(git, entry):
    """Return a directory entry's children, or {} when absent or not a tree."""
    return git.tree(entry[2]) if entry and entry[:2] == (b"040000", b"tree") else {}


def inspect(git, base, head, report):
    """Compare exact commits and report changed skills and plugins; return None.

    Skill roots are skills/<name> and plugins/<plugin>/skills/<name>. Affected
    roots include additions, deletions, mode changes, and both sides of a
    rename. Inspect retained roots completely; unchanged skills are outside
    scope and full deletions are informational. Any change inside a retained
    plugin rechecks its manifest/LICENSE, and a plugin LICENSE change rechecks
    every skill that inherits it. Incomplete propagates for bad revisions,
    malformed Git output/paths, or exhausted budgets.
    """
    git.commit(base)
    git.commit(head)
    inspect_skills(git, base, head, report)
    inspect_plugins(git, base, head, report)
    if not report.lines:
        report.add("info", "no relevant skill changes")


def inspect_plugins(git, base, head, report):
    """Select changed plugins and their changed or license-dependent skills."""
    before = git.tree(base).get(b"plugins")
    parent = git.tree(head).get(b"plugins")
    if before == parent:
        return
    if parent and parent[:2] != (b"040000", b"tree"):
        report.add("fail", "plugins must be a directory", b"plugins")
        return
    names, children = changed_children(git, before, parent)
    old_children = entries(git, before)
    # plugins/README.md is the folder's documentation, not a plugin root.
    names.discard(b"README.md")
    if len(names) > MAX_PLUGINS:
        raise Incomplete("affected plugin budget exceeded")
    roots = 0
    for name in sorted(names):
        root = b"plugins/" + name
        check_path(root)
        entry = children.get(name)
        if entry is None:
            report.add("info", "plugin deleted", root)
            continue
        report.add("info", "inspecting plugin manifest and license", root)
        inspect_plugin(git, root, entry, report)
        old = old_children.get(name)
        container = subtree(git, entry, b"skills")
        if container and container[:2] != (b"040000", b"tree"):
            report.add("fail", "skills must be a directory", root + b"/skills")
            continue
        selected, skills = changed_children(git, subtree(git, old, b"skills"), container)
        old_skills = entries(git, subtree(git, old, b"skills"))
        if subtree(git, old, b"LICENSE") != subtree(git, entry, b"LICENSE"):
            selected |= skills.keys()
        roots += len(selected)
        if roots > MAX_SKILLS:
            raise Incomplete("affected skill budget exceeded")
        for skill in sorted(selected):
            path = root + b"/skills/" + skill
            check_path(path)
            if skill not in skills:
                if skill in old_skills:
                    report.add("info", "skill deleted", path)
                continue
            report.add("info", "inspecting entire retained skill", path)
            inspect_skill(git, path, skills[skill], report, inherited=True)


def inspect_skills(git, base, head, report):
    """Select and inspect changed top-level skills/<name> roots."""
    # Compare the two supplied commits directly (not a merge base). Disable
    # external diff/text conversions and rename pairing; -z preserves raw names.
    raw = git.run("diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                  "--ignore-submodules=none", "--name-only", "-z", base, head, "--")
    if raw and not raw.endswith(b"\0"):
        raise Incomplete("incomplete Git diff output")
    roots, touched = set(), False
    for path in raw.split(b"\0")[:-1]:
        if path == b"skills" or path.startswith(b"skills/"):
            check_path(path)
            touched = True
            parts = path.split(b"/")
            if len(parts) >= 2:
                roots.add(b"skills/" + parts[1])
    before = git.tree(base).get(b"skills")
    parent = git.tree(head).get(b"skills")
    if before == parent and not touched:
        return
    if parent and parent[:2] != (b"040000", b"tree"):
        report.add("fail", "skills must be a directory", b"skills")
        return
    children = git.tree(parent[2]) if parent else {}
    old_children = git.tree(before[2]) if before and before[:2] == (b"040000", b"tree") else {}
    # Git diff omits empty trees. Compare direct-child entry tuples as well so
    # even a synthetic empty skill tree must meet the required-file rules.
    # The set union deduplicates roots already selected by the path diff.
    for name in children.keys() | old_children.keys():
        if children.get(name) != old_children.get(name):
            root = b"skills/" + name
            check_path(root)
            roots.add(root)
    if len(roots) > MAX_SKILLS:
        raise Incomplete("affected skill budget exceeded")
    for root in sorted(roots):
        entry = children.get(root.split(b"/")[1])
        if entry is None:
            report.add("info", "skill deleted", root)
        else:
            report.add("info", "inspecting entire retained skill", root)
            inspect_skill(git, root, entry, report)


def event_revisions(path, expected):
    """Return (base SHA, head SHA, PR number) from a bounded local event file.

    path and expected repository come from trusted CLI/workflow wiring. Match
    expected against GITHUB_REPOSITORY, event repository, and PR base repository;
    require a positive bounded integer PR number and exact commit-ID syntax.
    Head repository URLs/ref names are deliberately unused. Incomplete covers
    malformed, oversized, inconsistent, or unreadable events. These consistency
    checks do not authenticate a workflow that a contributor can itself edit.
    """
    if (not expected or not REPOSITORY.fullmatch(expected) or
            expected.split("/")[1] in (".", "..") or os.environ.get("GITHUB_REPOSITORY") != expected):
        raise Incomplete("expected repository must match trusted GITHUB_REPOSITORY")
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_OUTPUT + 1)
        if len(raw) > MAX_OUTPUT:
            raise Incomplete("event size budget exceeded")
        event = json.loads(raw)
        pr = event["pull_request"]
        number = event["number"]
        # bool subclasses int in Python; exact type checks reject true/false
        # rather than treating them as PR 1/0. Bound the number before building
        # a refspec, and require agreement if the nested PR number is present.
        if (type(number) is not int or not 1 <= number <= 2147483647 or
                type(pr.get("number", number)) is not int or
                pr.get("number", number) != number or
                event["repository"]["full_name"] != expected or
                pr["base"]["repo"]["full_name"] != expected):
            raise Incomplete("event repository or PR number mismatch")
        return check_sha(pr["base"]["sha"]), check_sha(pr["head"]["sha"]), number
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise Incomplete("invalid or unavailable PR event") from None


def fetch_public(git, repository, number, base, head):
    """Populate a fresh trusted directory with public PR objects; return None.

    repository/number/base/head must already pass event_revisions; this helper
    is not an independent input validator. Initialize a bare database without
    templates, fetch the base SHA and constructed PR ref, then require the ref
    to equal the event head SHA. Fetch failures, limits, or a raced head raise
    Incomplete; the caller owns temporary-directory cleanup. No checkout occurs.
    """
    # Ignore event-supplied URLs and branch names. One shallow HTTPS fetch uses
    # the expected repository and numeric PR ref, without tags or submodules.
    git.run("init", "--bare", "--template=")
    git.run("fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--depth=1",
            "https://github.com/" + repository + ".git", base,
            f"refs/pull/{number}/head:refs/skill-check/head", network=True)
    if git.run("rev-parse", "--verify", "refs/skill-check/head", limit=128) != (head + "\n").encode():
        raise Incomplete("fetched PR head changed; retry with a fresh event")


def main(argv=None):
    """Run CLI inspection and return status 0 (valid), 1 (fail), or 2 (incomplete).

    argv is an optional argument list; None lets argparse read the process CLI.
    Print a bounded report and optionally write it to --summary-trusted. Event
    mode can fetch into an automatically cleaned temporary bare repository;
    offline mode requires an explicitly trusted existing repository. Incomplete
    and OS errors become fixed diagnostics, including summary-write failures.
    argparse retains its normal SystemExit behavior for help/invalid options;
    unexpected programming errors are not converted into successful results.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--event", type=Path)
    parser.add_argument("--expected-repository")
    parser.add_argument("--fetch-public", action="store_true")
    parser.add_argument("--summary-trusted", type=Path,
                        help="explicit trusted output file; never taken from event/candidate")
    args = parser.parse_args(argv)
    report, base, head = Report(), None, None
    try:
        if args.event:
            base, head, number = event_revisions(args.event, args.expected_repository)
            if (args.base and args.base != base) or (args.head and args.head != head):
                raise Incomplete("explicit revisions do not match event")
        else:
            base, head = check_sha(args.base), check_sha(args.head)
        if args.fetch_public:
            if not args.event or args.repository:
                raise Incomplete("public fetch requires event and forbids a local repository")
            with tempfile.TemporaryDirectory(prefix="skill-structure-") as directory:
                git = Git(directory)
                fetch_public(git, args.expected_repository, number, base, head)
                inspect(git, base, head, report)
        else:
            if not args.repository:
                raise Incomplete("offline inspection requires a trusted local repository")
            inspect(Git(args.repository), base, head, report)
    except (Incomplete, OSError) as error:
        # Do not call Report.add when the failure may be its own line budget.
        # One final fixed diagnostic explains that earlier results are partial.
        report.status = 2
        message = str(error) if isinstance(error, Incomplete) else "trusted local I/O unavailable"
        report.lines.append("[incomplete] " + message)
    revisions = f"Requested base: {base if base and SHA.fullmatch(base) else 'unavailable'}\n"
    revisions += f"Requested head: {head if head and SHA.fullmatch(head) else 'unavailable'}\n"
    result = ("structure valid (advisory)", "structural violations", "inspection incomplete")[report.status]
    text = revisions + "\n".join(report.lines) + "\nResult: " + result + "\n"
    # Do not implicitly trust a summary environment variable or candidate path.
    # The workflow passes the runner-owned output path explicitly at the CLI.
    if args.summary_trusted:
        try:
            args.summary_trusted.write_text("```text\n" + text + "```\n", encoding="utf-8")
        except OSError:
            report.status = 2
            text = text.replace("Result: " + result, "Result: inspection incomplete")
            text += "[incomplete] trusted summary could not be written\n"
    print(text, end="")
    return report.status


if __name__ == "__main__":
    sys.exit(main())

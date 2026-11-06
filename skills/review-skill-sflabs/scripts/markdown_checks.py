"""Bounded Markdown subset, not CommonMark or a workflow interpreter.

No extensions, filesystem access or URL fetching. Recognize single-line inline
and reference links, simple ASCII ATX/setext headings, fences and explicit
Read/Load/Follow/Open/Run `local/path` routing. Unknown anchor syntax abstains.
"""
from dataclasses import dataclass, field
import posixpath
import re
from urllib.parse import unquote, urlsplit

from ingest import lf_lines

MAX_LINE = 4096
MAX_LINKS = 2048
MAX_NESTING = 16


@dataclass
class Document:
    links: list = field(default_factory=list)
    anchors: set = field(default_factory=set)
    issues: list = field(default_factory=list)
    anchors_complete: bool = True


def balanced(text, start, opening, closing):
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if char == opening:
            depth += 1
            if depth > MAX_NESTING:
                return None
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def destination(raw):
    raw = raw.strip()
    if raw.startswith('<'):
        end = raw.find('>')
        return raw[1:end] if end > 0 else None
    # Parenthesized destinations may include a trailing quoted title.
    match = re.fullmatch(r'''(\S+?)(?:\s+(?:"[^"]*"|'[^']*'))?''', raw)
    return match[1] if match else None


def inline_links(text):
    """Balanced labels/destinations and code spans; no backtracking regex nesting."""
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if char == '`':
            end = index
            while end < len(text) and text[end] == '`':
                end += 1
            close = text.find(text[index:end], end)
            index = close + end - index if close >= 0 else end
            continue
        if char != '[':
            index += 1
            continue
        end = balanced(text, index, '[', ']')
        if end is None:
            yield ('limited', '')
            return
        label = text[index + 1:end]
        if end + 1 < len(text) and text[end + 1] == '(':
            close = balanced(text, end + 1, '(', ')')
            if close is not None:
                target = destination(text[end + 2:close])
                if target is not None:
                    yield ('link', target)
                else:
                    yield ('limited', '')
                index = close + 1
                continue
            yield ('limited', '')
            return
        elif end + 1 < len(text) and text[end + 1] == '[':
            close = text.find(']', end + 2)
            if close >= 0:
                yield ('reference', text[end + 2:close] or label)
                index = close + 1
                continue
        else:
            yield ('shortcut', label)
        index = end + 1


def parse(text):
    document = Document()
    definitions = {}
    pending = []
    fence = None
    frontmatter = text.startswith('---\n') or text.startswith('---\r\n')
    html_block = False
    previous = None

    def heading(value):
        # Explicit IDs, HTML, Unicode, links and inline formatting vary by renderer.
        # Plain ASCII punctuation follows GitHub's slug rule: lowercase, drop
        # anything other than letters, digits, spaces, '_' and '-', then map
        # spaces to '-'. Formatting/markup characters still mark anchors partial.
        if (not re.fullmatch(r'[\x20-\x7e]+', value) or re.search(r'[`*\[\]<>!\\~&{}]', value)
                or not re.search(r'[A-Za-z0-9]', value)):
            document.anchors_complete = False
            return
        slug = re.sub(r'[^a-z0-9 _-]', '', value.lower()).replace(' ', '-')
        candidate = slug
        number = 0
        while candidate in document.anchors:
            number += 1
            candidate = slug + '-' + str(number)
        document.anchors.add(candidate)

    for number, raw in enumerate(lf_lines(text), 1):
        if frontmatter:
            if number > 1 and raw == '---':
                frontmatter = False
            continue
        if len(raw) > MAX_LINE:
            document.issues.append(('markdown.limited', number))
            document.anchors_complete = False
            previous = None
            continue
        # Recognize quote/list container fences conservatively; never scan their examples.
        line = re.sub(r'^(?: {0,3}> ?)+', '', raw)
        line = re.sub(r'^ {0,3}(?:[-+*]|\d+[.)]) +', '', line)
        if not fence and raw.startswith(('    ', '\t')):
            previous = None
            continue
        marker = re.match(r'^ *(`{3,}|~{3,})(.*)$', line)
        if fence:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                    and not marker[2].strip()):
                fence = None
            previous = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]), number)
            previous = None
            continue
        if raw.startswith(('    ', '\t')):
            previous = None
            continue
        if html_block:
            if not raw.strip() or '-->' in raw:
                html_block = False
            previous = None
            continue
        if re.match(r'^\s*<(?:!--|/?[A-Za-z])', raw):
            document.anchors_complete = False
            html_block = '-->' not in raw
            previous = None
            continue
        title = re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$', line)
        if title:
            heading(title[1])
        elif re.fullmatch(r' {0,3}(?:=+|-+)\s*', line) and previous:
            heading(previous)
        definition = re.match(r'^ {0,3}\[([^\]]+)\]:\s*(.+)$', line)
        if definition:
            target = destination(definition[2])
            if target is not None:
                definitions.setdefault(definition[1].casefold().strip(), target)
        else:
            for kind, target in inline_links(line):
                pending.append((kind, target, number))
            route = re.match(r'(?i)^\s*(?:read|load|follow|open|run)\s+`([^`]+)`', line)
            if route and '/' in route[1] and not any(char in route[1] for char in '<>{}$* '):
                pending.append(('link', route[1], number))
        previous = line.strip() or None
        if len(pending) + len(definitions) > MAX_LINKS:
            document.issues.append(('markdown.limited', number))
            document.anchors_complete = False
            break
    if fence:
        document.issues.append(('markdown.fence', fence[2]))
        document.anchors_complete = False
    if frontmatter:
        document.anchors_complete = False
    for kind, target, number in pending[:MAX_LINKS]:
        if kind == 'link':
            document.links.append((target, number))
        else:
            resolved = definitions.get(target.casefold().strip())
            if kind == 'limited':
                document.issues.append(('markdown.limited', number))
                document.anchors_complete = False
            elif resolved is not None:
                document.links.append((resolved, number))
            elif kind == 'reference':
                document.issues.append(('markdown.reference', number))
    if not document.anchors_complete and not any(code in ('markdown.limited', 'markdown.fence') for code, _ in document.issues):
        document.issues.append(('markdown.limited', 1))
    return document


def check_documents(texts, paths, add):
    directories = {'.'}
    for path in paths:
        parts = path.split('/')
        directories.update('/'.join(parts[:index]) for index in range(1, len(parts)))
    documents = {path: parse(text) for path, (_, text) in texts.items() if path.lower().endswith('.md')}
    for path, document in documents.items():
        file_id = texts[path][0]
        for code, number in document.issues:
            add(code, file_id=file_id, line=number)
        for raw, number in document.links:
            try:
                target = urlsplit(raw)
                if target.scheme or target.netloc:
                    continue
                local = unquote(target.path)
                fragment = unquote(target.fragment)
            except ValueError:
                add('markdown.limited', file_id=file_id, line=number)
                continue
            if '\\' in local or any(char in local for char in '<>{}$*'):
                add('markdown.limited', file_id=file_id, line=number)
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), local)) if local else path
            escapes = resolved == '..' or resolved.startswith(('../', '/'))
            directory = resolved in directories
            if escapes or (resolved not in paths and not directory):
                add('format.reference', file_id=file_id, line=number)
            elif fragment and resolved in documents:
                linked = documents[resolved]
                if fragment not in linked.anchors and linked.anchors_complete:
                    add('markdown.anchor', file_id=file_id, line=number)

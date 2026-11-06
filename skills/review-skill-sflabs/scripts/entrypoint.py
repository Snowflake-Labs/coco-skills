"""Container-only CLI. Never execute this directly against a local candidate."""
from pathlib import Path
import sys

# -I excludes the current directory; only add the trusted image's script directory.
sys.path.insert(0, str(Path(__file__).parent))
from checks import review
from ingest import MAX_WIRE_BYTES, decode
from protocol import error_report, exit_code, render, strict_json


def main():
    try:
        wire = sys.stdin.buffer.read(MAX_WIRE_BYTES + 1)
        name, entries = decode(wire)
        catalog = strict_json(Path('/validator/catalog-snapshot.json').read_text())
        result = review(name, entries, catalog)
    except Exception:
        # Do not expose exception text, paths, parser context, or candidate bytes.
        result = error_report('input.unsafe', 'blocked')
    print(render(result, 'json'))
    return exit_code(result)


if __name__ == '__main__':
    sys.exit(main())

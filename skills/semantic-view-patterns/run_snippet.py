"""
Run a semantic view snippet interactively against your Snowflake account.

Usage:
    python run_snippet.py <snippet_name> [options]

Options:
    --step schema|seed|sv|queries|all   Which step to run (default: all)
    --db DATABASE                        Target database (default: CORTEX_SNIPPETS)
    --schema SCHEMA                      Target schema (default: PUBLIC)
    --connection CONNECTION_NAME         Snowflake connection name (default: active connection)
    --quiet                              Suppress query result rows

Examples:
    python run_snippet.py time_intelligence
    python run_snippet.py range_join --step sv
    python run_snippet.py window_metrics --db MY_DB --schema MY_SCHEMA
    python run_snippet.py asof_join --connection my_connection
"""

import os
import sys
import re
import argparse
import snowflake.connector

SNIPPETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snippets")

STEP_FILES = {
    "schema":  "schema.sql",
    "seed":    "seed_data.sql",
    "sv":      "semantic_view.sql",
    "queries": "queries.sql",
}


def adapt_sql(sql: str, target_db: str, target_schema: str) -> str:
    """Rewrite SNIPPETS.PUBLIC references to the user's target database and schema."""
    sql = re.sub(r'USE DATABASE SNIPPETS\s*;', f'USE DATABASE {target_db};', sql, flags=re.IGNORECASE)
    sql = re.sub(r'USE SCHEMA PUBLIC\s*;', f'USE SCHEMA {target_schema};', sql, flags=re.IGNORECASE)
    sql = re.sub(r'CREATE DATABASE IF NOT EXISTS SNIPPETS\s*;', f'CREATE DATABASE IF NOT EXISTS {target_db};', sql, flags=re.IGNORECASE)
    sql = re.sub(r'CREATE SCHEMA IF NOT EXISTS SNIPPETS\.PUBLIC\s*;', f'CREATE SCHEMA IF NOT EXISTS {target_db}.{target_schema};', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bSNIPPETS\.PUBLIC\b', f'{target_db}.{target_schema}', sql, flags=re.IGNORECASE)
    return sql


def split_statements(sql: str) -> list[str]:
    """Split SQL into individual statements, skipping blank and comment-only blocks."""
    statements = []
    current = []
    for line in sql.splitlines():
        stripped = line.strip()
        current.append(line)
        if stripped.endswith(';') and not stripped.startswith('--'):
            stmt = '\n'.join(current).strip()
            if stmt and not all(l.strip().startswith('--') or l.strip() == '' for l in stmt.splitlines()):
                statements.append(stmt)
            current = []
    if current:
        stmt = '\n'.join(current).strip()
        if stmt and not all(l.strip().startswith('--') or l.strip() == '' for l in stmt.splitlines()):
            statements.append(stmt)
    return statements


def run_step(cur, snippet_dir: str, step: str, target_db: str, target_schema: str, verbose: bool = True):
    filename = STEP_FILES[step]
    filepath = os.path.join(snippet_dir, filename)

    if not os.path.exists(filepath):
        print(f"  ⚠️  {filename} not found — skipping")
        return

    with open(filepath) as f:
        raw = f.read()

    sql = adapt_sql(raw, target_db, target_schema)
    statements = split_statements(sql)

    print(f"\n{'='*60}")
    print(f"  {step.upper()}: {filename}  ({len(statements)} statements)")
    print(f"{'='*60}")

    for stmt in statements:
        first_line = stmt.splitlines()[0].strip()
        if first_line.startswith('--'):
            first_line = next((l.strip() for l in stmt.splitlines() if l.strip() and not l.strip().startswith('--')), first_line)
        label = first_line[:80]

        if not stmt.strip() or all(l.strip().startswith('--') or not l.strip() for l in stmt.splitlines()):
            continue

        try:
            cur.execute(stmt)
            rows = cur.fetchall() if cur.description else []
            if rows and verbose:
                cols = [d[0] for d in cur.description]
                col_widths = [max(len(str(c)), max((len(str(r[i])) for r in rows), default=0)) for i, c in enumerate(cols)]
                header = '  ' + '  '.join(str(c).ljust(col_widths[i]) for i, c in enumerate(cols))
                print(f"\n  ✓ {label}")
                print(header)
                print('  ' + '  '.join('-' * w for w in col_widths))
                for row in rows[:30]:
                    print('  ' + '  '.join(str(row[i]).ljust(col_widths[i]) for i in range(len(cols))))
                if len(rows) > 30:
                    print(f"  ... ({len(rows)} rows total)")
            else:
                status = f"{cur.rowcount} row(s)" if cur.rowcount and cur.rowcount > 0 else "ok"
                print(f"  ✓ {label}  [{status}]")
        except Exception as e:
            print(f"  ✗ {label}")
            print(f"    ERROR: {e}")


def list_snippets() -> list[str]:
    if not os.path.isdir(SNIPPETS_DIR):
        return []
    return sorted(d for d in os.listdir(SNIPPETS_DIR) if os.path.isdir(os.path.join(SNIPPETS_DIR, d)))


def main():
    parser = argparse.ArgumentParser(description="Run a semantic view snippet against your Snowflake account")
    parser.add_argument("snippet", nargs="?", help="Snippet name (e.g. time_intelligence). Omit to list available snippets.")
    parser.add_argument("--step", choices=["schema", "seed", "sv", "queries", "all"], default="all")
    parser.add_argument("--db", default="CORTEX_SNIPPETS", help="Target database (default: CORTEX_SNIPPETS)")
    parser.add_argument("--schema", default="PUBLIC", help="Target schema (default: PUBLIC)")
    parser.add_argument("--connection", default=None, help="Snowflake connection name (default: active session connection)")
    parser.add_argument("--quiet", action="store_true", help="Suppress query result rows")
    args = parser.parse_args()

    available = list_snippets()

    if not args.snippet:
        print("\nAvailable snippets:")
        for s in available:
            print(f"  {s}")
        print(f"\nUsage: python run_snippet.py <snippet_name> [--db DB] [--schema SCHEMA]")
        sys.exit(0)

    snippet_dir = os.path.join(SNIPPETS_DIR, args.snippet)
    if not os.path.isdir(snippet_dir):
        print(f"ERROR: snippet '{args.snippet}' not found.")
        print(f"Available: {', '.join(available)}")
        sys.exit(1)

    print(f"\nTarget: {args.db}.{args.schema}")
    print(f"Connecting to Snowflake...")

    conn_kwargs = {}
    if args.connection:
        conn_kwargs["connection_name"] = args.connection
    conn = snowflake.connector.connect(**conn_kwargs)
    cur = conn.cursor()
    cur.execute(f"USE DATABASE {args.db}")
    cur.execute(f"USE SCHEMA {args.schema}")
    print(f"Connected: {conn.account}")

    steps = ["schema", "seed", "sv", "queries"] if args.step == "all" else [args.step]
    for step in steps:
        run_step(cur, snippet_dir, step, args.db, args.schema, verbose=not args.quiet)

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()

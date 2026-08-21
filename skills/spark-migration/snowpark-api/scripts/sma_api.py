#!/usr/bin/env python3
"""
SMA API – Unified database and git access module

Single source of truth for all SMA database interactions and git operations.
Replaces the former sma_db.py and sma_database.py modules.

Usage:
    Import: from sma_api import initialize_database, get_pending_ewi_codes, ...
    Git:    from sma_api import git_status, git_init_if_needed, git_ensure_branch, ...

All tools receive `workload_path` as their first parameter.
The database is always at `{workload_path}/sma_storage.sqlite3`.
"""

import csv
import json
import os
import sqlite3
import subprocess
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = {
    "pending",
    "in_progress",
    "manual_resolved",
    "auto_resolved",
    "not_auto_resolved",
    "wont_fix",
}

BLOCKER_EWI_CODES = {
    "PNDSPY1001", "PNDSPY1003",
    "SPRKPY1001", "SPRKPY1002", "SPRKPY1003", "SPRKPY1004",
    "SPRKPY1032", "SPRKPY1038", "SPRKPY1054", "SPRKPY1058",
    "SPRKPY1067", "SPRKPY1074", "SPRKPY1084", "SPRKPY1085",
    "SPRKPY1086",
    "SSC-EWI-0001",
}

MIGRATION_BRANCH = "sma/migration-process"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_fix_id: str | None = None


def _db_from_workload(workload_path: str) -> str:
    """Return the canonical DB path for a workload root directory."""
    return os.path.join(workload_path, "sma_storage.sqlite3")


def resolve_db(workload_path: str = None, db_path: str = None) -> str:
    """Return a concrete database file path.

    Accepts either *workload_path* (directory containing sma_storage.sqlite3)
    or an explicit *db_path*.  At least one must be provided.
    """
    if db_path:
        return db_path
    if workload_path:
        return _db_from_workload(workload_path)
    raise ValueError("Either workload_path or db_path must be provided")


@contextmanager
def _connect(db_path: str):
    """Open a connection to the given SQLite database.

    Creates the database file automatically if it does not exist yet,
    so skills that need the DB can run in any order.
    """
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _column_name(cursor: sqlite3.Cursor, canonical: str) -> str | None:
    """Find actual column name in the issues table (handles case variants)."""
    cursor.execute("PRAGMA table_info(issues)")
    columns = [col[1] for col in cursor.fetchall()]
    for v in (canonical, canonical.lower(), canonical.upper(), canonical.capitalize()):
        if v in columns:
            return v
    return None


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    return path.replace("\\", "/").lstrip("/")


def _validate_status(status: str) -> str | None:
    """Return error message if status is invalid, None otherwise."""
    if status not in VALID_STATUSES:
        return f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
    return None


# ---------------------------------------------------------------------------
# Inline EWI aggregation (replaces ewi_extractor dependency)
# ---------------------------------------------------------------------------

def _normalize_row(row: dict) -> dict:
    mappings = {
        "code": ["Code", "code", "CODE"],
        "description": ["Description", "description", "DESCRIPTION"],
        "category": ["Category", "category", "CATEGORY"],
        "file_id": ["FileId", "file_id", "fileid", "FILEID", "File_Id"],
        "line": ["Line", "line", "LINE"],
        "column": ["Column", "column", "COLUMN"],
        "url": ["Url", "url", "URL"],
        "status": ["status", "Status", "STATUS"],
        "notes": ["notes", "Notes", "NOTES"],
    }
    normalized = {}
    for target, keys in mappings.items():
        value = ""
        for k in keys:
            if k in row and row[k] is not None:
                value = row[k]
                break
        normalized[target] = str(value) if value is not None else ""
    return normalized


def _parse_rows(rows: list[dict]) -> list[dict]:
    records = []
    for row in rows:
        n = _normalize_row(row)
        records.append({
            "code": n.get("code", ""),
            "description": n.get("description", ""),
            "category": n.get("category", "None") or "None",
            "file_id": _normalize_path(n.get("file_id", "")),
            "line": n.get("line", ""),
            "column": n.get("column", ""),
            "url": n.get("url", ""),
            "status": n.get("status", "pending") or "pending",
            "notes": n.get("notes", ""),
        })
    return records


def _aggregate_ewis(records: list[dict]) -> list[dict]:
    data = defaultdict(lambda: {
        "code": "", "description": "", "category": "", "url": "",
        "occurrences": 0, "files_affected": set(), "status": "pending", "notes": "",
    })
    for r in records:
        code = r["code"]
        if not code:
            continue
        e = data[code]
        e["code"] = code
        if not e["description"]:
            e["description"] = r["description"]
        if not e["category"]:
            e["category"] = r["category"] if r["category"] else "None"
        if not e["url"]:
            e["url"] = r.get("url", "")
        e["occurrences"] += 1
        if r["file_id"]:
            e["files_affected"].add(r["file_id"])

    result = []
    for code in sorted(data.keys()):
        ewi = data[code]
        ewi["files_affected"] = sorted(ewi["files_affected"])
        result.append(ewi)
    return result


def _aggregate_files(records: list[dict]) -> dict:
    file_ewi_lines: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        code, file_id = r["code"], r["file_id"]
        if not code or not file_id:
            continue
        try:
            line_num = int(r.get("line", "")) if r.get("line", "") else 0
        except ValueError:
            line_num = 0
        status = r.get("status", "pending") or "pending"
        file_ewi_lines[file_id][code].append({"line": line_num, "status": status})

    file_data = {}
    for file_path in sorted(file_ewi_lines.keys()):
        ewis_in_file = []
        all_statuses = []
        for code in sorted(file_ewi_lines[file_path].keys()):
            occs = file_ewi_lines[file_path][code]
            line_status_map: dict[int, str] = {}
            for occ in occs:
                ln = occ["line"]
                if ln not in line_status_map:
                    line_status_map[ln] = occ["status"]
                elif occ["status"] != "pending":
                    line_status_map[ln] = occ["status"]
            lines_with_status = [{"line": ln, "status": st} for ln, st in sorted(line_status_map.items())]
            all_statuses.extend(l["status"] for l in lines_with_status)
            line_statuses = [l["status"] for l in lines_with_status]
            unique = set(line_statuses)
            ewi_status = line_statuses[0] if len(unique) == 1 else "in_progress"
            ewis_in_file.append({
                "code": code,
                "lines": lines_with_status,
                "occurrences": len(occs),
                "status": ewi_status,
            })

        unique_all = set(all_statuses)
        file_status = all_statuses[0] if len(unique_all) == 1 else "in_progress" if all_statuses else "pending"
        file_data[file_path] = {
            "file_path": file_path,
            "file_status": file_status,
            "total_ewis": len(ewis_in_file),
            "ewis": ewis_in_file,
        }
    return file_data


def _extract_ewi_data(rows: list[dict], workload_name: str = "") -> dict:
    """Full aggregation pipeline – replaces extract_ewi_data_from_rows."""
    records = _parse_rows(rows)
    ewis = _aggregate_ewis(records)
    files = _aggregate_files(records)

    ewi_line_statuses: dict[str, list] = defaultdict(list)
    for fi in files.values():
        for ei in fi.get("ewis", []):
            for li in ei.get("lines", []):
                ewi_line_statuses[ei["code"]].append(li.get("status", "pending"))
    for ewi in ewis:
        code = ewi["code"]
        if code in ewi_line_statuses:
            statuses = ewi_line_statuses[code]
            unique = set(statuses)
            ewi["status"] = statuses[0] if len(unique) == 1 else "in_progress"

    summary = {s: 0 for s in VALID_STATUSES}
    for ewi in ewis:
        st = ewi.get("status", "pending")
        if st in summary:
            summary[st] += 1

    ewi_data = {
        "generated_at": datetime.now().isoformat(),
        "source_file": "database",
        "workload_name": workload_name or "Unknown Workload",
        "total_ewis": len(ewis),
        "summary": summary,
        "ewis": ewis,
    }
    file_data = {
        "generated_at": datetime.now().isoformat(),
        "source_file": "database",
        "total_files": len(files),
        "files": list(files.values()),
    }
    return {"ewi_data": ewi_data, "file_data": file_data}


def extract_ewi_data(rows: list[dict], workload_name: str = "") -> dict:
    """Public wrapper for the EWI aggregation pipeline.

    Args:
        rows: Raw issue rows (list of dicts from the issues table).
        workload_name: Name of the workload for the generated report.

    Returns:
        dict with "ewi_data" and "file_data" keys.
    """
    return _extract_ewi_data(rows, workload_name)


# ===== Git tools =============================================================

def _git(workload_path: str, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=workload_path,
        capture_output=True,
        text=True,
        timeout=30,
    )


def git_is_repo(workload_path: str) -> bool:
    try:
        r = _git(workload_path, "rev-parse", "--is-inside-work-tree")
        return r.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def git_current_branch(workload_path: str) -> Optional[str]:
    r = _git(workload_path, "rev-parse", "--abbrev-ref", "HEAD")
    if r.returncode == 0:
        return r.stdout.strip()
    return None


def git_branch_exists(workload_path: str, branch: str = MIGRATION_BRANCH) -> bool:
    r = _git(workload_path, "rev-parse", "--verify", branch)
    return r.returncode == 0


def git_is_clean(workload_path: str) -> bool:
    r = _git(workload_path, "status", "--porcelain")
    return r.returncode == 0 and r.stdout.strip() == ""


def git_status(workload_path: str) -> dict:
    is_repo = git_is_repo(workload_path)
    if not is_repo:
        return {
            "is_repo": False,
            "is_clean": False,
            "current_branch": None,
            "migration_branch_exists": False,
        }
    return {
        "is_repo": True,
        "is_clean": git_is_clean(workload_path),
        "current_branch": git_current_branch(workload_path),
        "migration_branch_exists": git_branch_exists(workload_path),
    }


def git_init_if_needed(workload_path: str) -> dict:
    if git_is_repo(workload_path):
        return {"success": True, "action": "already_initialized"}
    r = _git(workload_path, "init")
    if r.returncode != 0:
        return {"success": False, "error": r.stderr.strip()}
    _git(workload_path, "add", ".")
    r = _git(workload_path, "commit", "-m", "Initial commit: SMA converted output before EWI fixes")
    if r.returncode != 0:
        return {"success": False, "error": r.stderr.strip()}
    _git(workload_path, "branch", "-M", "main")
    return {"success": True, "action": "initialized"}


def git_stash(workload_path: str, message: str = "Pre-EWI-fixer stash") -> dict:
    r = _git(workload_path, "stash", "push", "--include-untracked", "-m", message)
    if r.returncode != 0:
        return {"success": False, "error": r.stderr.strip()}
    stashed = "No local changes" not in r.stdout
    return {"success": True, "stashed": stashed}


def git_ensure_branch(workload_path: str, branch: str = MIGRATION_BRANCH) -> dict:
    if git_branch_exists(workload_path, branch):
        current = git_current_branch(workload_path)
        if current == branch:
            return {"success": True, "action": "already_on_branch"}
        r = _git(workload_path, "checkout", branch)
        if r.returncode != 0:
            return {"success": False, "error": r.stderr.strip()}
        return {"success": True, "action": "switched"}
    r = _git(workload_path, "checkout", "-b", branch)
    if r.returncode != 0:
        return {"success": False, "error": r.stderr.strip()}
    return {"success": True, "action": "created"}


def git_commit(workload_path: str, message: str) -> dict:
    _git(workload_path, "add", ".")
    if git_is_clean(workload_path):
        return {"success": True, "action": "nothing_to_commit"}
    r = _git(workload_path, "commit", "-m", message)
    if r.returncode != 0:
        return {"success": False, "error": r.stderr.strip()}
    h = _git(workload_path, "rev-parse", "--short", "HEAD")
    return {
        "success": True,
        "action": "committed",
        "commit_hash": h.stdout.strip() if h.returncode == 0 else None,
    }


def git_verify_branches(workload_path: str) -> dict:
    r = _git(workload_path, "branch", "--list")
    if r.returncode != 0:
        return {"success": False, "error": r.stderr.strip()}
    branches = [b.strip().lstrip("* ") for b in r.stdout.splitlines() if b.strip()]
    has_main = "main" in branches
    has_migration = MIGRATION_BRANCH in branches
    return {
        "success": has_main and has_migration,
        "branches": branches,
        "has_main": has_main,
        "has_migration": has_migration,
    }


def git_ensure_ready(workload_path: str) -> dict:
    if not git_is_repo(workload_path):
        init = git_init_if_needed(workload_path)
        if not init["success"]:
            return init
    elif not git_is_clean(workload_path):
        stash = git_stash(workload_path)
        if not stash["success"]:
            return stash
    branch = git_ensure_branch(workload_path)
    if not branch["success"]:
        return branch
    return {"success": True, "init": "done", "branch": branch["action"]}


# ===== Initialization tools =================================================

def initialize_database(workload_path: str, *, db_path: str = None) -> dict:
    """
    Initialize or load the SMA tracking database from an SMA output directory.

    Creates sma_storage.sqlite3 from Issues.csv if it doesn't exist yet.
    Ensures all required tables (issues, ewi_fixer_results, ewi_fixer_summary) exist.
    Generates a new fix session ID.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        db_path: Optional explicit database path override.
    """
    global _fix_id

    db_path = resolve_db(workload_path, db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Issues'")
    needs_init = cursor.fetchone() is None

    if needs_init:
        reports = os.path.join(workload_path, "Reports")
        csv_path = None
        csv_name = None
        for name in ("Issues.csv", "IssuesConnect.csv"):
            candidate = os.path.join(reports, name)
            if os.path.exists(candidate):
                csv_path = candidate
                csv_name = name
                break
        if not csv_path:
            conn.close()
            return {"error": f"No Issues.csv or IssuesConnect.csv found in {reports}"}

        _ensure_metadata_table(cursor)


        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            cols_def = ", ".join(f'"{col}" TEXT' for col in fieldnames)
            cursor.execute(
                f'CREATE TABLE IF NOT EXISTS Issues ('
                f'id INTEGER PRIMARY KEY AUTOINCREMENT, {cols_def}, '
                f'status TEXT DEFAULT "pending", notes TEXT DEFAULT "")'
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_code ON Issues(code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON Issues(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_code_status ON Issues(code, status)")
            f.seek(0)
            reader = csv.DictReader(f)
            for row in reader:
                placeholders = ", ".join(["?"] * len(fieldnames))
                col_names = ", ".join(f'"{c}"' for c in fieldnames)
                values = [_normalize_path(row[c]) if c == "FileId" else row.get(c, "") for c in fieldnames]
                cursor.execute(
                    f'INSERT INTO Issues ({col_names}, status, notes) VALUES ({placeholders}, "pending", "")',
                    values,
                )
        # Detect conversion type from EWI codes in the just-inserted rows.
        # SCOS workloads use SPRKCNTPY*/SPRKCNTSCL* codes even though they
        # also produce Issues.csv (not IssuesConnect.csv).
        if csv_name == "IssuesConnect.csv":
            flow = "snowpark_connect"
        else:
            row = cursor.execute(
                "SELECT code FROM Issues WHERE code LIKE 'SPRKCNT%' LIMIT 1"
            ).fetchone()
            flow = "snowpark_connect" if row else "snowpark_api"
        cursor.execute("""
            INSERT INTO workload_metadata (key, value, updated_at)
            VALUES ('conversion_type', ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
        """, (flow,))
        conn.commit()
    else:
        cursor.execute("PRAGMA table_info(Issues)")
        columns = {r[1] for r in cursor.fetchall()}
        if "status" not in columns:
            cursor.execute("ALTER TABLE Issues ADD COLUMN status TEXT DEFAULT 'pending'")
        if "notes" not in columns:
            cursor.execute("ALTER TABLE Issues ADD COLUMN notes TEXT DEFAULT ''")
        conn.commit()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ewi_fixer_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fix_id TEXT, ewi_code TEXT, fix_description TEXT,
            affected_file TEXT, affected_lines TEXT, status TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fix_id ON ewi_fixer_results(fix_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_results_status ON ewi_fixer_results(status)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ewi_fixer_summary (
            fix_id TEXT PRIMARY KEY,
            total_ewis INTEGER, auto_resolved_ewis INTEGER,
            not_auto_resolved_ewis INTEGER, total_files_fixed INTEGER,
            total_not_auto_resolved_files INTEGER, compilation_errors_fixed INTEGER,
            start_time DATETIME, end_time DATETIME
        )
    """)
    cursor.execute("PRAGMA table_info(ewi_fixer_summary)")
    if "compilation_errors_fixed" not in {r[1] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE ewi_fixer_summary ADD COLUMN compilation_errors_fixed INTEGER")

    conn.commit()
    conn.close()

    # Close orphaned summary rows from previous cancelled/failed runs
    conn_cleanup = sqlite3.connect(db_path)
    try:
        orphaned = conn_cleanup.execute("""
            UPDATE ewi_fixer_summary
            SET end_time = CURRENT_TIMESTAMP, total_ewis = 0, auto_resolved_ewis = 0,
                not_auto_resolved_ewis = 0, total_files_fixed = 0,
                total_not_auto_resolved_files = 0, compilation_errors_fixed = 0
            WHERE end_time IS NULL
        """).rowcount
        conn_cleanup.commit()
        if orphaned > 0:
            print(f"Closed {orphaned} orphaned summary record(s) from previous runs")
    finally:
        conn_cleanup.close()

    _fix_id = str(uuid.uuid4())

    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    try:
        conn2.execute(
            "INSERT INTO ewi_fixer_summary (fix_id, start_time) VALUES (?, CURRENT_TIMESTAMP)",
            (_fix_id,),
        )
        conn2.commit()
    finally:
        conn2.close()

    # Ensure test tracking tables exist (preserves data if already present)
    create_tests_table(db_path=db_path)

    return {
        "success": True,
        "db_path": db_path,
        "fix_id": _fix_id,
        "initialized_from_csv": needs_init,
    }


def create_artifact_dependency_tables(workload_path: str, csv_path: str, *, db_path: str = None) -> dict:
    """
    Create artifact_dependency_inventory, artifact_dependency_summary, and
    artifact_dependency_graph tables from ArtifactDependencyInventory.csv.
    Calculates dependency islands using Union-Find.
    Skips import if inventory table already exists but always refreshes summary.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        csv_path: Path to ArtifactDependencyInventory.csv.
        db_path: Optional explicit database path override.
    """
    with _connect(resolve_db(workload_path, db_path)) as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_dependency_inventory'"
        )
        inventory_exists = cursor.fetchone() is not None

        file_to_island: dict[str, int] = {}

        if inventory_exists:
            cursor.execute("SELECT DISTINCT file_id, island FROM artifact_dependency_summary")
            for row in cursor.fetchall():
                file_to_island[row[0]] = row[1]
        else:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if not rows:
                return {"error": "ArtifactDependencyInventory.csv is empty"}

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artifact_dependency_inventory (
                    execution_id TEXT, file_id TEXT, dependency TEXT, type TEXT,
                    success TEXT, status_detail TEXT, arguments TEXT, location TEXT,
                    indirect_dependencies TEXT, total_indirect_dependencies INTEGER,
                    direct_parents TEXT, total_direct_parents INTEGER,
                    indirect_parents TEXT, total_indirect_parents INTEGER,
                    status TEXT DEFAULT 'pending'
                )
            """)
            for row in rows:
                cursor.execute(
                    """INSERT INTO artifact_dependency_inventory (
                        execution_id, file_id, dependency, type, success, status_detail,
                        arguments, location, indirect_dependencies, total_indirect_dependencies,
                        direct_parents, total_direct_parents, indirect_parents,
                        total_indirect_parents, status
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')""",
                    (
                        row.get("ExecutionId", ""),
                        _normalize_path(row.get("FileId", "")),
                        _normalize_path(row.get("Dependency", "")),
                        row.get("Type", ""),
                        row.get("Success", ""),
                        row.get("StatusDetail", ""),
                        row.get("Arguments", ""),
                        row.get("Location", ""),
                        _normalize_path(row.get("IndirectDependencies", "")),
                        int(row.get("TotalIndirectDependencies", 0) or 0),
                        _normalize_path(row.get("DirectParents", "")),
                        int(row.get("TotalDirectParents", 0) or 0),
                        _normalize_path(row.get("IndirectParents", "")),
                        int(row.get("TotalIndirectParents", 0) or 0),
                    ),
                )
            conn.commit()

            cursor.execute("SELECT DISTINCT file_id FROM artifact_dependency_inventory")
            all_files = set(r[0] for r in cursor.fetchall())
            cursor.execute(
                "SELECT file_id, dependency FROM artifact_dependency_inventory "
                "WHERE type='UserCodeFile' AND dependency IS NOT NULL AND dependency != ''"
            )
            edges = cursor.fetchall()
            for _, dep in edges:
                all_files.add(dep)

            parent = {f: f for f in all_files}
            rank = {f: 0 for f in all_files}

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(x, y):
                px, py = find(x), find(y)
                if px == py:
                    return
                if rank[px] < rank[py]:
                    px, py = py, px
                parent[py] = px
                if rank[px] == rank[py]:
                    rank[px] += 1

            for fid, dep in edges:
                if fid in parent and dep in parent:
                    union(fid, dep)

            root_to_island: dict[str, int] = {}
            counter = 1
            for f in all_files:
                root = find(f)
                if root not in root_to_island:
                    root_to_island[root] = counter
                    counter += 1
                file_to_island[f] = root_to_island[root]

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artifact_dependency_graph (
                    source TEXT, target TEXT, island INTEGER
                )
            """)
            for fid, dep in edges:
                cursor.execute(
                    "INSERT INTO artifact_dependency_graph (source, target, island) VALUES (?,?,?)",
                    (fid, dep, file_to_island.get(fid, 0)),
                )
            conn.commit()

            direct_parents_map: dict[str, list] = {}
            for fid, dep in edges:
                direct_parents_map.setdefault(dep, [])
                if fid not in direct_parents_map[dep]:
                    direct_parents_map[dep].append(fid)

            indirect_parents_map: dict[str, list] = {}
            for fid in all_files:
                dp = direct_parents_map.get(fid, [])
                indirect = []
                visited = set([fid] + dp)
                level = list(dp)
                while level:
                    nxt = []
                    for p in level:
                        for gp in direct_parents_map.get(p, []):
                            if gp not in visited:
                                visited.add(gp)
                                indirect.append(gp)
                                nxt.append(gp)
                    level = nxt
                indirect_parents_map[fid] = indirect

            cursor.execute("SELECT DISTINCT file_id FROM artifact_dependency_inventory")
            for (fid,) in cursor.fetchall():
                dp = direct_parents_map.get(fid, [])
                ip = indirect_parents_map.get(fid, [])
                cursor.execute(
                    "UPDATE artifact_dependency_inventory "
                    "SET direct_parents=?, total_direct_parents=?, indirect_parents=?, total_indirect_parents=? "
                    "WHERE file_id=?",
                    (",".join(dp), len(dp), ",".join(ip), len(ip), fid),
                )
            conn.commit()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS artifact_dependency_summary (
                execution_id TEXT, file_id TEXT, island INTEGER DEFAULT 0,
                total_user_code_file INTEGER DEFAULT 0, total_io_sources INTEGER DEFAULT 0,
                total_third_party_libraries INTEGER DEFAULT 0, total_unknown_libraries INTEGER DEFAULT 0,
                total_sql_object INTEGER DEFAULT 0, total_dependencies INTEGER DEFAULT 0,
                total_issues INTEGER DEFAULT 0, validated INTEGER DEFAULT 0,
                recommended_actions TEXT DEFAULT ''
            )
        """)

        cursor.execute("DROP TABLE IF EXISTS temp_user_data")
        cursor.execute("""
            CREATE TEMP TABLE temp_user_data AS
            SELECT file_id, validated, recommended_actions
            FROM artifact_dependency_summary
            WHERE validated != 0 OR (recommended_actions IS NOT NULL AND recommended_actions != '')
        """)
        cursor.execute("DELETE FROM artifact_dependency_summary")

        cursor.execute("""
            INSERT INTO artifact_dependency_summary (
                execution_id, file_id, total_user_code_file, total_io_sources,
                total_third_party_libraries, total_unknown_libraries, total_sql_object,
                total_dependencies, total_issues
            )
            SELECT
                d.execution_id, d.file_id,
                IFNULL(d.ucf,0), IFNULL(d.ios,0), IFNULL(d.tpl,0),
                IFNULL(d.ul,0), IFNULL(d.sqlo,0), IFNULL(d.td,0),
                IFNULL(i.ti,0)
            FROM (
                SELECT execution_id, file_id,
                    SUM(CASE WHEN type='UserCodeFile' THEN 1 ELSE 0 END) ucf,
                    SUM(CASE WHEN type='IOSources' THEN 1 ELSE 0 END) ios,
                    SUM(CASE WHEN type='ThirdPartyLibraries' THEN 1 ELSE 0 END) tpl,
                    SUM(CASE WHEN type='UnknownLibraries' THEN 1 ELSE 0 END) ul,
                    SUM(CASE WHEN type='SqlObject' THEN 1 ELSE 0 END) sqlo,
                    COUNT(1) td
                FROM artifact_dependency_inventory GROUP BY execution_id, file_id
            ) d LEFT JOIN (
                SELECT execution_id, file_id, COUNT(1) ti
                FROM artifact_dependency_inventory
                WHERE LOWER(status_detail) IN ('notparsed','notsupported','doesnotexists','unknown')
                GROUP BY execution_id, file_id
            ) i ON d.execution_id=i.execution_id AND d.file_id=i.file_id
        """)
        conn.commit()

        cursor.execute("""
            UPDATE artifact_dependency_summary SET
                validated = COALESCE((SELECT validated FROM temp_user_data WHERE temp_user_data.file_id = artifact_dependency_summary.file_id), 0),
                recommended_actions = COALESCE((SELECT recommended_actions FROM temp_user_data WHERE temp_user_data.file_id = artifact_dependency_summary.file_id), '')
        """)
        cursor.execute("DROP TABLE IF EXISTS temp_user_data")

        for fid, island in file_to_island.items():
            cursor.execute(
                "UPDATE artifact_dependency_summary SET island=? WHERE file_id=?",
                (island, fid),
            )
        conn.commit()

        cursor.execute(
            "SELECT file_id FROM artifact_dependency_summary "
            "WHERE recommended_actions IS NULL OR recommended_actions = ''"
        )
        for (fid,) in cursor.fetchall():
            cursor.execute(
                "SELECT type, dependency, success FROM artifact_dependency_inventory WHERE file_id=?",
                (fid,),
            )
            deps = cursor.fetchall()
            unknown = [d[1] for d in deps if d[0] == "UnknownLibraries"]
            tp_issues = [d[1] for d in deps if d[0] == "ThirdPartyLibraries" and d[2] != "True"]
            uc_issues = [d[1] for d in deps if d[0] == "UserCodeFile" and d[2] != "True"]
            io_src = [d[1] for d in deps if d[0] == "IOSources"]
            sql_obj = [d[1] for d in deps if d[0] == "SqlObject"]

            actions = []
            if unknown:
                names = ", ".join(unknown[:3]) + ("..." if len(unknown) > 3 else "")
                actions.append(f"Review {len(unknown)} unknown library reference(s): {names}.")
            if tp_issues:
                names = ", ".join(tp_issues[:3]) + ("..." if len(tp_issues) > 3 else "")
                actions.append(f"Address {len(tp_issues)} third-party library issue(s): {names}.")
            if uc_issues:
                names = ", ".join(uc_issues[:3]) + ("..." if len(uc_issues) > 3 else "")
                actions.append(f"Resolve {len(uc_issues)} user code file(s) not parsed: {names}.")
            if io_src:
                names = ", ".join(io_src[:3]) + ("..." if len(io_src) > 3 else "")
                actions.append(f"Review {len(io_src)} I/O source(s): {names}.")
            if sql_obj:
                names = ", ".join(sql_obj[:3]) + ("..." if len(sql_obj) > 3 else "")
                actions.append(f"Verify {len(sql_obj)} SQL object reference(s): {names}.")
            if not actions:
                actions.append("No immediate issues detected. File appears ready for migration.")

            cursor.execute(
                "UPDATE artifact_dependency_summary SET recommended_actions=? WHERE file_id=?",
                ("\n".join(actions), fid),
            )
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM artifact_dependency_summary")
        total = cursor.fetchone()[0]

    return {"success": True, "summary_rows": total, "islands": len(set(file_to_island.values()))}


def create_input_files_table(workload_path: str, csv_path: str, *, db_path: str = None) -> dict:
    """
    Create input_files_inventory table from InputFilesInventory.csv.
    Skips if table already exists.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        csv_path: Path to InputFilesInventory.csv.
        db_path: Optional explicit database path override.
    """
    with _connect(resolve_db(workload_path, db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='input_files_inventory'")
        if cursor.fetchone():
            cursor.execute("SELECT COUNT(*) FROM input_files_inventory")
            return {"skipped": True, "existing_rows": cursor.fetchone()[0]}

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return {"error": "InputFilesInventory.csv is empty"}

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS input_files_inventory (
                element TEXT, project_id TEXT, file_id TEXT, count INTEGER,
                session_id TEXT, extension TEXT, technology TEXT, bytes INTEGER,
                character_length INTEGER, lines_of_code INTEGER, parse_result TEXT,
                ignored INTEGER, origin_file_path TEXT
            )
        """)
        for row in rows:
            ignored = 1 if row.get("Ignored", "False").lower() == "true" else 0
            cursor.execute(
                "INSERT INTO input_files_inventory VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.get("Element", ""), row.get("ProjectId", ""),
                    row.get("FileId", ""), int(row.get("Count", 0) or 0),
                    row.get("SessionId", ""), row.get("Extension", ""),
                    row.get("Technology", ""), int(row.get("Bytes", 0) or 0),
                    int(row.get("CharacterLength", 0) or 0),
                    int(row.get("LinesOfCode", 0) or 0),
                    row.get("ParseResult", ""), ignored,
                    row.get("OriginFilePath", ""),
                ),
            )
        conn.commit()
    return {"success": True, "rows_imported": len(rows)}


# ===== Read tools – EWI =====================================================

def get_migration_summary(workload_path: str) -> dict:
    """
    High-level migration readiness summary.

    Returns total files, EWIs, occurrences, status breakdown,
    blocker count, and migration readiness categories.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM issues")
        rows = [dict(r) for r in cursor.fetchall()]

    if not rows:
        return {
            "total_files": 0, "total_ewis": 0, "total_occurrences": 0,
            "status_summary": {s: 0 for s in VALID_STATUSES},
            "blockers": [], "migration_readiness": {"ready": 0, "review": 0, "blocked": 0},
        }

    result = _extract_ewi_data(rows)
    ewis = result["ewi_data"].get("ewis", [])

    blockers = []
    file_counts: dict[str, dict] = {}
    for ewi in ewis:
        code = ewi["code"]
        is_blocker = code in BLOCKER_EWI_CODES
        if is_blocker:
            blockers.append({
                "code": code, "description": ewi.get("description", ""),
                "files_affected": len(ewi.get("files_affected", [])),
                "occurrences": ewi.get("occurrences", 0),
            })
        for fp in ewi.get("files_affected", []):
            entry = file_counts.setdefault(fp, {"total": 0, "has_blocker": False})
            entry["total"] += 1
            if is_blocker:
                entry["has_blocker"] = True

    readiness = {"ready": 0, "review": 0, "blocked": 0}
    for counts in file_counts.values():
        if counts["has_blocker"]:
            readiness["blocked"] += 1
        elif counts["total"] > 5:
            readiness["review"] += 1
        else:
            readiness["ready"] += 1

    return {
        "total_files": len(file_counts),
        "total_ewis": result["ewi_data"]["total_ewis"],
        "total_occurrences": sum(e.get("occurrences", 0) for e in ewis),
        "status_summary": result["ewi_data"].get("summary", {}),
        "blockers": blockers,
        "migration_readiness": readiness,
    }


def list_ewis(
    workload_path: str,
    category: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    List EWIs with optional filters.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        category: Filter by category (e.g. "ConversionError").
        status: Filter by status.
        limit: Max results (default 50).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM issues")
        rows = [dict(r) for r in cursor.fetchall()]

    result = _extract_ewi_data(rows)
    ewis = result["ewi_data"].get("ewis", [])

    if category:
        ewis = [e for e in ewis if e.get("category", "").lower() == category.lower()]
    if status:
        ewis = [e for e in ewis if e.get("status", "pending") == status]

    output = []
    for ewi in ewis[:limit]:
        output.append({
            "code": ewi["code"],
            "description": ewi.get("description", ""),
            "category": ewi.get("category", ""),
            "occurrences": ewi.get("occurrences", 0),
            "files_affected": len(ewi.get("files_affected", [])),
            "status": ewi.get("status", "pending"),
            "is_blocker": ewi["code"] in BLOCKER_EWI_CODES,
        })
    return output


def get_blockers(workload_path: str) -> list[dict]:
    """
    List critical blocker EWIs that prevent migration.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    return [e for e in list_ewis(workload_path, limit=999) if e.get("is_blocker")]


def get_pending_ewi_codes(workload_path: str) -> list[dict]:
    """
    Get distinct EWI codes that are still pending.

    Returns list of dicts with Code, Description, Category.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        code_col = _column_name(cursor, "Code")
        desc_col = _column_name(cursor, "Description")
        cat_col = _column_name(cursor, "Category")
        if not code_col:
            return [{"error": "Cannot find Code column"}]
        cursor.execute(
            f'SELECT DISTINCT "{code_col}", "{desc_col}", "{cat_col}" '
            f"FROM issues WHERE status='pending' ORDER BY \"{code_col}\""
        )
        return [dict(r) for r in cursor.fetchall()]


def get_ewis_by_code(workload_path: str, code: str, status: str = "pending") -> list[dict]:
    """
    Get all EWI occurrences for a specific code.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        code: EWI code (e.g. "SPRKPY1002").
        status: Filter by status (default "pending").
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        code_col = _column_name(cursor, "Code")
        file_col = _column_name(cursor, "FileId")
        line_col = _column_name(cursor, "Line")
        if not code_col:
            return [{"error": "Cannot find Code column"}]
        cursor.execute(
            f'SELECT * FROM issues WHERE "{code_col}"=? AND status=? ORDER BY "{file_col}", "{line_col}"',
            (code, status),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_ewis_by_file(workload_path: str, file_id: str, status: str = "pending") -> list[dict]:
    """
    Get all EWIs for a specific file.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_id: File path (e.g. "sample.py").
        status: Filter by status (default "pending").
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        file_col = _column_name(cursor, "FileId")
        line_col = _column_name(cursor, "Line")
        if not file_col:
            return [{"error": "Cannot find FileId column"}]
        cursor.execute(
            f'SELECT * FROM issues WHERE "{file_col}"=? AND status=? ORDER BY "{line_col}"',
            (file_id, status),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_summary_stats(workload_path: str) -> dict:
    """
    Get summary statistics of EWI statuses.

    Returns dict with counts per status plus total.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, COUNT(*) as count FROM issues GROUP BY status")
        stats = {r["status"]: r["count"] for r in cursor.fetchall()}
    result = {
        "pending": stats.get("pending", 0),
        "in_progress": stats.get("in_progress", 0),
        "auto_resolved": stats.get("auto_resolved", 0),
        "not_auto_resolved": stats.get("not_auto_resolved", 0),
        "manual_resolved": stats.get("manual_resolved", 0),
        "wont_fix": stats.get("wont_fix", 0),
    }
    result["total"] = sum(result.values())
    return result


def get_ewi_code_stats(workload_path: str) -> list[dict]:
    """
    Get statistics per EWI code.

    Returns list of dicts with Code, pending_count, auto_resolved_count, total_count.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        code_col = _column_name(cursor, "Code")
        if not code_col:
            return [{"error": "Cannot find Code column"}]
        cursor.execute(f"""
            SELECT "{code_col}",
                SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN status='auto_resolved' THEN 1 ELSE 0 END) as auto_resolved_count,
                COUNT(*) as total_count
            FROM issues GROUP BY "{code_col}" ORDER BY "{code_col}"
        """)
        return [dict(r) for r in cursor.fetchall()]


# ===== Read tools – Files ===================================================

def list_files(workload_path: str, status: str | None = None, limit: int = 50) -> list[dict]:
    """
    List files with their EWI summary.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        status: Filter by file status.
        limit: Max results (default 50).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM issues")
        rows = [dict(r) for r in cursor.fetchall()]

    result = _extract_ewi_data(rows)
    files = result["file_data"].get("files", [])

    if status:
        files = [f for f in files if f.get("file_status", "pending") == status]

    output = []
    for f in files[:limit]:
        ewi_codes = [e["code"] for e in f.get("ewis", [])]
        total_lines = sum(len(e.get("lines", [])) for e in f.get("ewis", []))
        output.append({
            "file_path": f.get("file_path", ""),
            "file_status": f.get("file_status", "pending"),
            "total_ewis": f.get("total_ewis", 0),
            "total_lines_affected": total_lines,
            "ewi_codes": ewi_codes,
        })
    return output


def get_file_details(workload_path: str, file_path: str) -> dict:
    """
    Get detailed EWI information for a specific file.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_path: The file path as shown in list_files.
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM issues")
        rows = [dict(r) for r in cursor.fetchall()]

    result = _extract_ewi_data(rows)
    normalized = _normalize_path(file_path)
    for f in result["file_data"].get("files", []):
        if _normalize_path(f.get("file_path", "")) == normalized:
            return f
    return {"error": f"File not found: {file_path}"}


def get_ewi_descriptions(workload_path: str) -> dict:
    """
    Get EWI code -> description mapping from issues table.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        code_col = _column_name(cursor, "Code")
        desc_col = _column_name(cursor, "Description")
        if not code_col or not desc_col:
            return {"error": "Cannot find Code/Description columns"}
        cursor.execute(
            f'SELECT DISTINCT "{code_col}", "{desc_col}" FROM issues '
            f'WHERE "{code_col}" IS NOT NULL AND "{desc_col}" IS NOT NULL'
        )
        return {r[code_col]: r[desc_col] for r in cursor.fetchall()}


# ===== Read tools – Dependencies =============================================

def get_dependency_summary(workload_path: str) -> dict:
    """
    Get dependency islands summary.

    Returns island count, file count, and per-island breakdown.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_dependency_summary'"
        )
        if not cursor.fetchone():
            return {"error": "No dependency data available."}

        cursor.execute("""
            SELECT island, COUNT(*) as file_count, SUM(total_issues) as total_issues,
                   SUM(total_dependencies) as total_deps
            FROM artifact_dependency_summary WHERE island > 0
            GROUP BY island ORDER BY file_count DESC
        """)
        islands = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT COUNT(DISTINCT island) FROM artifact_dependency_summary WHERE island > 0")
        total_islands = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM artifact_dependency_summary")
        total_files = cursor.fetchone()[0] or 0

    return {"total_islands": total_islands, "total_files_with_dependencies": total_files, "islands": islands}


def get_dependency_summary_by_file(workload_path: str) -> list[dict]:
    """Get per-file dependency summary rows (used by the SMA dashboard).

    Unlike *get_dependency_summary* which aggregates by island, this returns
    one row per file with all columns from artifact_dependency_summary.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_dependency_summary'"
        )
        if not cursor.fetchone():
            return []
        cursor.execute("""
            SELECT
                file_id,
                island,
                total_user_code_file,
                total_io_sources,
                total_third_party_libraries,
                total_unknown_libraries,
                total_sql_object,
                total_dependencies,
                total_issues,
                COALESCE(validated, 0) as validated,
                COALESCE(recommended_actions, '') as recommended_actions
            FROM artifact_dependency_summary
            ORDER BY island, total_issues DESC, total_dependencies DESC
        """)
        return [dict(r) for r in cursor.fetchall()]


def get_file_dependencies(workload_path: str, file_path: str) -> list[dict]:
    """
    Get all dependencies for a specific file.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_path: The file to look up.
    """
    normalized = _normalize_path(file_path)
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_dependency_inventory'"
        )
        if not cursor.fetchone():
            return [{"error": "No dependency data available."}]
        cursor.execute(
            "SELECT type, dependency, success, status_detail, status "
            "FROM artifact_dependency_inventory WHERE file_id=? ORDER BY type, dependency",
            (normalized,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_dependency_inventory(workload_path: str) -> list[dict]:
    """
    Get full dependency inventory data.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_dependency_inventory'"
        )
        if not cursor.fetchone():
            return [{"error": "No dependency data available."}]
        cursor.execute("""
            SELECT file_id, type, dependency, success, status_detail, arguments,
                   location, indirect_dependencies, direct_parents, total_direct_parents,
                   indirect_parents, total_indirect_parents, COALESCE(status,'pending') as status
            FROM artifact_dependency_inventory ORDER BY file_id, type, dependency
        """)
        return [dict(r) for r in cursor.fetchall()]


def get_dependency_graph(workload_path: str) -> dict:
    """
    Get dependency graph data for visualization (nodes, edges, islands).

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT source, target, island FROM artifact_dependency_graph")
        edges = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT file_id, island FROM artifact_dependency_summary WHERE island > 0")
        nodes = [dict(r) for r in cursor.fetchall()]
        cursor.execute("""
            SELECT island, COUNT(*) as file_count, SUM(total_issues) as total_issues
            FROM artifact_dependency_summary WHERE island > 0
            GROUP BY island ORDER BY island
        """)
        islands = [dict(r) for r in cursor.fetchall()]
    return {"nodes": nodes, "edges": edges, "islands": islands}


# ===== Write tools – Status updates ==========================================

def update_ewi_status(workload_path: str, code: str, status: str, notes: str | None = None) -> dict:
    """
    Update status for ALL occurrences of an EWI code (cascading).

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        code: EWI code (e.g. "SPRKPY1001").
        status: New status.
        notes: Optional notes.
    """
    err = _validate_status(status)
    if err:
        return {"error": err}

    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        code_col = _column_name(cursor, "Code")
        if not code_col:
            return {"error": "Cannot find Code column."}
        cursor.execute(f'UPDATE issues SET status=? WHERE "{code_col}"=?', (status, code))
        affected = cursor.rowcount
        if notes is not None:
            cursor.execute(f'UPDATE issues SET notes=? WHERE "{code_col}"=?', (notes, code))
        conn.commit()

    if affected == 0:
        return {"error": f"EWI code '{code}' not found."}
    result = {"success": True, "code": code, "new_status": status, "rows_updated": affected}
    if notes is not None:
        result["notes"] = notes
    return result


def update_file_status(workload_path: str, file_path: str, status: str) -> dict:
    """
    Update status for ALL EWIs in a specific file (cascading).

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_path: File path.
        status: New status.
    """
    err = _validate_status(status)
    if err:
        return {"error": err}

    normalized = _normalize_path(file_path)
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        file_col = _column_name(cursor, "FileId")
        if not file_col:
            return {"error": "Cannot find FileId column."}
        cursor.execute(f'UPDATE issues SET status=? WHERE "{file_col}"=?', (status, normalized))
        affected = cursor.rowcount
        conn.commit()

    if affected == 0:
        return {"error": f"File '{file_path}' not found."}
    return {"success": True, "file_path": file_path, "new_status": status, "rows_updated": affected}


def update_file_ewi_status(workload_path: str, file_path: str, code: str, status: str) -> dict:
    """Update status for all lines of a specific EWI code within a specific file.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_path: File path.
        code: EWI code.
        status: New status.
    """
    err = _validate_status(status)
    if err:
        return {"error": err}

    normalized = _normalize_path(file_path)
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        file_col = _column_name(cursor, "FileId")
        code_col = _column_name(cursor, "Code")
        if not file_col or not code_col:
            return {"error": "Cannot find FileId/Code columns."}
        cursor.execute(
            f'UPDATE issues SET status=? WHERE "{file_col}"=? AND "{code_col}"=?',
            (status, normalized, code),
        )
        affected = cursor.rowcount
        conn.commit()

    if affected == 0:
        return {"error": f"No matching rows for file='{file_path}', code='{code}'."}
    return {"success": True, "file_path": file_path, "code": code, "new_status": status, "rows_updated": affected}


def update_line_status(workload_path: str, file_path: str, code: str, line: int, status: str) -> dict:
    """
    Update status for a specific EWI occurrence at a specific line.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_path: File path.
        code: EWI code.
        line: Line number.
        status: New status.
    """
    err = _validate_status(status)
    if err:
        return {"error": err}

    normalized = _normalize_path(file_path)
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        file_col = _column_name(cursor, "FileId")
        code_col = _column_name(cursor, "Code")
        line_col = _column_name(cursor, "Line")
        if not all([file_col, code_col, line_col]):
            return {"error": "Cannot find required columns."}
        cursor.execute(
            f'UPDATE issues SET status=? WHERE "{file_col}"=? AND "{code_col}"=? AND "{line_col}"=?',
            (status, normalized, code, str(line)),
        )
        affected = cursor.rowcount
        conn.commit()

    if affected == 0:
        return {"error": f"No matching row for file='{file_path}', code='{code}', line={line}."}
    return {"success": True, "rows_updated": affected}


def bulk_update_ewi_status(workload_path: str, codes: list[str], status: str) -> dict:
    """
    Update status for multiple EWI codes at once.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        codes: List of EWI codes.
        status: New status.
    """
    err = _validate_status(status)
    if err:
        return {"error": err}

    total = 0
    details = []
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        code_col = _column_name(cursor, "Code")
        if not code_col:
            return {"error": "Cannot find Code column."}
        for code in codes:
            cursor.execute(f'UPDATE issues SET status=? WHERE "{code_col}"=?', (status, code))
            affected = cursor.rowcount
            total += affected
            details.append({"code": code, "rows_updated": affected})
        conn.commit()
    return {"success": True, "new_status": status, "total_rows_updated": total, "details": details}


def update_ewi_notes(workload_path: str, code: str, notes: str) -> dict:
    """
    Update notes for all occurrences of an EWI code.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        code: EWI code.
        notes: Notes text.
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        code_col = _column_name(cursor, "Code")
        if not code_col:
            return {"error": "Cannot find Code column."}
        cursor.execute(f'UPDATE issues SET notes=? WHERE "{code_col}"=?', (notes, code))
        affected = cursor.rowcount
        conn.commit()
    if affected == 0:
        return {"error": f"EWI code '{code}' not found."}
    return {"success": True, "code": code, "rows_updated": affected}


def update_ewi_status_single(
    workload_path: str, code: str, file_id: str, line: int, status: str, notes: str,
) -> dict:
    """
    Update status and notes for a single specific EWI occurrence (used by ewi-fixer).

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        code: EWI code.
        file_id: File path.
        line: Line number.
        status: New status.
        notes: Fix description / reason.
    """
    err = _validate_status(status)
    if err:
        return {"error": err}

    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        code_col = _column_name(cursor, "Code")
        file_col = _column_name(cursor, "FileId")
        line_col = _column_name(cursor, "Line")
        if not all([code_col, file_col, line_col]):
            return {"error": "Cannot find required columns."}
        cursor.execute(
            f'UPDATE issues SET status=?, notes=? WHERE "{code_col}"=? AND "{file_col}"=? AND "{line_col}"=?',
            (status, notes, code, file_id, str(line)),
        )
        affected = cursor.rowcount
        conn.commit()
    if affected == 0:
        return {"error": f"No match for code={code}, file={file_id}, line={line}."}
    return {"success": True, "rows_updated": affected}


def update_dependency_status(workload_path: str, file_id: str, dependency: str, status: str) -> dict:
    """
    Update status for a specific dependency and recalculate file validation.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_id: File ID.
        dependency: Dependency name.
        status: New status.
    """
    err = _validate_status(status)
    if err:
        return {"error": err}

    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE artifact_dependency_inventory SET status=? WHERE file_id=? AND dependency=?",
            (status, file_id, dependency),
        )
        cursor.execute(
            "SELECT status FROM artifact_dependency_inventory WHERE file_id=?", (file_id,)
        )
        all_statuses = [r[0] or "pending" for r in cursor.fetchall()]
        file_validated = 0
        if all_statuses:
            unique = set(all_statuses)
            resolved = {"manual_resolved", "auto_resolved", "wont_fix"}
            if unique <= resolved:
                file_validated = 2
            elif unique == {"pending"}:
                file_validated = 0
            else:
                file_validated = 1
            cursor.execute(
                "UPDATE artifact_dependency_summary SET validated=? WHERE file_id=?",
                (file_validated, file_id),
            )
        conn.commit()
    return {"success": True, "file_validated": file_validated}


def update_file_validation(workload_path: str, file_id: str, validated: int) -> dict:
    """
    Update file validation status directly.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_id: File ID.
        validated: 0=pending, 1=in_progress, 2=validated.
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE artifact_dependency_summary SET validated=? WHERE file_id=?",
            (int(validated), file_id),
        )
        conn.commit()
    return {"success": True}


def update_recommended_actions(workload_path: str, file_id: str, recommended_actions: str) -> dict:
    """
    Update recommended actions for a file.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        file_id: File ID.
        recommended_actions: Actions text.
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE artifact_dependency_summary SET recommended_actions=? WHERE file_id=?",
            (recommended_actions, file_id),
        )
        conn.commit()
    return {"success": True}


# ===== Write tools – EWI Fixer session =======================================

def generate_fix_id(workload_path: str) -> dict:
    """
    Generate a new fix session UUID.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    global _fix_id
    _fix_id = str(uuid.uuid4())
    return {"fix_id": _fix_id}


def insert_fix_result(
    workload_path: str, ewi_code: str, fix_description: str, affected_file: str,
    affected_lines: str, status: str,
) -> dict:
    """
    Insert a single fix result record.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        ewi_code: EWI code.
        fix_description: Description of the fix or failure reason.
        affected_file: File path.
        affected_lines: Comma-separated line numbers.
        status: "success" or "failed".
    """
    fix_id = _fix_id or str(uuid.uuid4())
    with _connect(_db_from_workload(workload_path)) as conn:
        conn.execute(
            "INSERT INTO ewi_fixer_results (fix_id, ewi_code, fix_description, affected_file, affected_lines, status) "
            "VALUES (?,?,?,?,?,?)",
            (fix_id, ewi_code, fix_description, affected_file, affected_lines, status),
        )
        conn.commit()
    return {"success": True, "fix_id": fix_id}


def batch_insert_fix_results(workload_path: str, results: list[dict]) -> dict:
    """
    Insert multiple fix results in a single transaction.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        results: List of dicts with keys: ewi_code, fix_description, affected_file, affected_lines, status.
    """
    if not results:
        return {"success": True, "inserted": 0}
    fix_id = _fix_id or str(uuid.uuid4())
    data = [
        (fix_id, r["ewi_code"], r["fix_description"], r["affected_file"], r["affected_lines"], r["status"])
        for r in results
    ]
    with _connect(_db_from_workload(workload_path)) as conn:
        conn.executemany(
            "INSERT INTO ewi_fixer_results (fix_id, ewi_code, fix_description, affected_file, affected_lines, status) "
            "VALUES (?,?,?,?,?,?)",
            data,
        )
        conn.commit()
    return {"success": True, "fix_id": fix_id, "inserted": len(data)}


def get_fix_results(workload_path: str, fix_id: str | None = None) -> list[dict]:
    """
    Get fix results, optionally filtered by fix_id.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        fix_id: Optional fix_id (defaults to current session).
    """
    fid = fix_id or _fix_id
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        if fid:
            cursor.execute("SELECT * FROM ewi_fixer_results WHERE fix_id=? ORDER BY timestamp", (fid,))
        else:
            cursor.execute("SELECT * FROM ewi_fixer_results ORDER BY timestamp DESC")
        return [dict(r) for r in cursor.fetchall()]


def get_fix_results_stats(workload_path: str, fix_id: str | None = None) -> dict:
    """
    Get statistics for fix results.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        fix_id: Optional fix_id (defaults to current session).
    """
    fid = fix_id or _fix_id
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        if fid:
            cursor.execute(
                "SELECT status, COUNT(*) as count FROM ewi_fixer_results WHERE fix_id=? GROUP BY status",
                (fid,),
            )
        else:
            cursor.execute("SELECT status, COUNT(*) as count FROM ewi_fixer_results GROUP BY status")
        stats = {r["status"]: r["count"] for r in cursor.fetchall()}
    result = {"success": stats.get("success", 0), "failed": stats.get("failed", 0)}
    result["total"] = sum(result.values())
    return result


def insert_summary_start(workload_path: str) -> dict:
    """
    Insert initial summary record with start_time for current fix session.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    global _fix_id
    if not _fix_id:
        _fix_id = str(uuid.uuid4())
    with _connect(_db_from_workload(workload_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ewi_fixer_summary (fix_id, start_time) VALUES (?, CURRENT_TIMESTAMP)",
            (_fix_id,),
        )
        conn.commit()
    return {"success": True, "fix_id": _fix_id}


def update_summary_end(
    workload_path: str,
    total_ewis: int, auto_resolved_ewis: int, not_auto_resolved_ewis: int,
    total_files_fixed: int, total_not_auto_resolved_files: int,
    compilation_errors_fixed: int = 0,
) -> dict:
    """
    Update the summary record with final results at end of fix session.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        total_ewis: Total EWIs processed.
        auto_resolved_ewis: Successfully resolved.
        not_auto_resolved_ewis: Could not be fixed.
        total_files_fixed: Files with at least one fix.
        total_not_auto_resolved_files: Files with no fixes.
        compilation_errors_fixed: Compilation errors fixed.
    """
    fid = _fix_id
    if not fid:
        return {"error": "No active fix session. Call generate_fix_id first."}
    with _connect(_db_from_workload(workload_path)) as conn:
        conn.execute(
            "UPDATE ewi_fixer_summary SET total_ewis=?, auto_resolved_ewis=?, "
            "not_auto_resolved_ewis=?, total_files_fixed=?, total_not_auto_resolved_files=?, "
            "compilation_errors_fixed=?, end_time=CURRENT_TIMESTAMP WHERE fix_id=?",
            (total_ewis, auto_resolved_ewis, not_auto_resolved_ewis,
             total_files_fixed, total_not_auto_resolved_files, compilation_errors_fixed, fid),
        )
        conn.commit()
    return {"success": True, "fix_id": fid}


def get_fix_summary(workload_path: str, fix_id: str | None = None) -> dict:
    """
    Get summary record for a fix session.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        fix_id: Optional (defaults to current session).
    """
    fid = fix_id or _fix_id
    if not fid:
        return {"error": "No fix_id provided and no active session."}
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ewi_fixer_summary WHERE fix_id=?", (fid,))
        row = cursor.fetchone()
    return dict(row) if row else {"error": f"No summary found for fix_id={fid}"}


# ===== Write tools – Reset ==================================================

def reset_not_resolved_to_pending(workload_path: str) -> dict:
    """
    Reset all 'not_auto_resolved' EWIs back to 'pending'.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE issues SET status='pending', notes=NULL WHERE status='not_auto_resolved'")
        count = cursor.rowcount
        conn.commit()
    return {"success": True, "reset_count": count}


def reset_all_to_pending(workload_path: str) -> dict:
    """
    Reset ALL non-pending EWIs back to 'pending'.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
    """
    with _connect(_db_from_workload(workload_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE issues SET status='pending', notes=NULL WHERE status != 'pending'")
        count = cursor.rowcount
        conn.commit()
    return {"success": True, "reset_count": count}


# ===== Overview tools ========================================================

def save_overview_stats(workload_path: str, overview_data: dict, *, db_path: str = None) -> dict:
    """
    Save overview statistics to the database for persistence.

    Args:
        workload_path: Root path of the workload (sma_storage.sqlite3 lives here).
        overview_data: Dict with total_files, total_ewis, migration_readiness, blockers, etc.
        db_path: Optional explicit database path override.
    """
    with _connect(resolve_db(workload_path, db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS overview_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                calculated_at TEXT NOT NULL,
                total_files INTEGER DEFAULT 0, total_ewis INTEGER DEFAULT 0,
                ewi_occurrences INTEGER DEFAULT 0, total_islands INTEGER DEFAULT 0,
                island_files INTEGER DEFAULT 0, ready_files INTEGER DEFAULT 0,
                readiness_ready INTEGER DEFAULT 0, readiness_review INTEGER DEFAULT 0,
                readiness_blocked INTEGER DEFAULT 0,
                complexity_simple INTEGER DEFAULT 0, complexity_moderate INTEGER DEFAULT 0,
                complexity_complex INTEGER DEFAULT 0,
                blockers_json TEXT, ewi_categories_json TEXT,
                file_types_json TEXT, readiness_files_json TEXT
            )
        """)
        cursor.execute("DELETE FROM overview_stats")

        mr = overview_data.get("migration_readiness", {})
        fc = overview_data.get("file_complexity", {})
        cursor.execute("""
            INSERT INTO overview_stats (
                calculated_at, total_files, total_ewis, ewi_occurrences,
                total_islands, island_files, ready_files,
                readiness_ready, readiness_review, readiness_blocked,
                complexity_simple, complexity_moderate, complexity_complex,
                blockers_json, ewi_categories_json, file_types_json, readiness_files_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now().isoformat(),
            overview_data.get("total_files", 0),
            overview_data.get("total_ewis", 0),
            overview_data.get("ewi_occurrences", 0),
            overview_data.get("total_islands", 0),
            overview_data.get("island_files", 0),
            overview_data.get("ready_files", 0),
            mr.get("ready", 0), mr.get("review", 0), mr.get("blocked", 0),
            fc.get("simple", 0), fc.get("moderate", 0), fc.get("complex", 0),
            json.dumps(overview_data.get("blockers", [])),
            json.dumps(overview_data.get("ewi_categories", {})),
            json.dumps(overview_data.get("file_types", {})),
            json.dumps(overview_data.get("readiness_files", {})),
        ))
        conn.commit()
    return {"success": True}


# ===== Workload metadata =====================================================


def _ensure_metadata_table(cursor):
    """Create the workload_metadata table if it doesn't exist."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workload_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)


def set_metadata(workload_path: str = None, key: str = "", value: str = "",
                 *, db_path: str = None) -> dict:
    """Upsert a key/value pair into the workload_metadata table."""
    if not key:
        return {"error": "key is required"}
    p = resolve_db(workload_path, db_path)
    with _connect(p) as conn:
        cursor = conn.cursor()
        _ensure_metadata_table(cursor)
        cursor.execute("""
            INSERT INTO workload_metadata (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
        """, (key, value))
        conn.commit()
    return {"success": True, "key": key, "value": value}


def get_metadata(workload_path: str = None, key: str = "",
                 *, db_path: str = None) -> str | None:
    """Get a single metadata value by key.  Returns None if not found."""
    if not key:
        return None
    p = resolve_db(workload_path, db_path)
    with _connect(p) as conn:
        cursor = conn.cursor()
        _ensure_metadata_table(cursor)
        cursor.execute("SELECT value FROM workload_metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else None


def get_all_metadata(workload_path: str = None, *, db_path: str = None) -> dict:
    """Return all metadata as a {key: value} dict."""
    p = resolve_db(workload_path, db_path)
    with _connect(p) as conn:
        cursor = conn.cursor()
        _ensure_metadata_table(cursor)
        cursor.execute("SELECT key, value FROM workload_metadata")
        return {row["key"]: row["value"] for row in cursor.fetchall()}


# ===== Test tracking tables ===================================================

VALID_TEST_STATUSES = {"pending", "passed", "failed", "skipped", "error"}


def create_tests_table(workload_path: str = None, *, db_path: str = None) -> dict:
    p = resolve_db(workload_path, db_path)
    with _connect(p) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entrypoint_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entrypoint_name TEXT NOT NULL,
                entrypoint_source TEXT,
                test_file TEXT NOT NULL,
                test_type TEXT NOT NULL DEFAULT 'source',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entrypoint_test_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                test_method TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                duration_seconds REAL,
                executed_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (test_id) REFERENCES entrypoint_tests(id)
            )
        """)
        # Migration: add test_method column to existing databases
        try:
            cursor.execute("ALTER TABLE entrypoint_test_runs ADD COLUMN test_method TEXT")
        except Exception:
            pass  # column already exists
        conn.commit()
    return {"success": True}


def register_tests(workload_path: str, tests: list[dict]) -> dict:
    p = _db_from_workload(workload_path)
    create_tests_table(db_path=p)
    with _connect(p) as conn:
        cursor = conn.cursor()
        inserted = 0
        for t in tests:
            cursor.execute(
                "SELECT id FROM entrypoint_tests WHERE entrypoint_name = ? AND test_type = ?",
                (t["entrypoint_name"], t.get("test_type", "source")),
            )
            if cursor.fetchone():
                continue
            cursor.execute(
                "INSERT INTO entrypoint_tests (entrypoint_name, entrypoint_source, test_file, test_type) VALUES (?, ?, ?, ?)",
                (
                    t["entrypoint_name"],
                    t.get("entrypoint_source", ""),
                    t["test_file"],
                    t.get("test_type", "source"),
                ),
            )
            inserted += 1
        conn.commit()
    return {"success": True, "inserted": inserted}


def get_tests(workload_path: str = None, *, db_path: str = None) -> dict:
    p = resolve_db(workload_path, db_path)
    create_tests_table(db_path=p)
    with _connect(p) as conn:
        cursor = conn.cursor()
        # Check if dependency summary table exists for island lookup
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_dependency_summary'")
        has_deps = cursor.fetchone() is not None
        if has_deps:
            cursor.execute("""
                SELECT t.*, d.island
                FROM entrypoint_tests t
                LEFT JOIN artifact_dependency_summary d
                    ON d.file_id = CASE
                        WHEN INSTR(t.entrypoint_source, ':') > 0
                        THEN SUBSTR(t.entrypoint_source, 1, INSTR(t.entrypoint_source, ':') - 1)
                        ELSE t.entrypoint_source
                    END
                ORDER BY t.id
            """)
        else:
            cursor.execute("SELECT *, NULL AS island FROM entrypoint_tests ORDER BY id")
        tests = [dict(r) for r in cursor.fetchall()]
    return {"success": True, "tests": tests}


def get_test_runs(workload_path: str = None, *, db_path: str = None, test_id: int = None) -> dict:
    p = resolve_db(workload_path, db_path)
    create_tests_table(db_path=p)
    with _connect(p) as conn:
        cursor = conn.cursor()
        if test_id:
            cursor.execute("SELECT * FROM entrypoint_test_runs WHERE test_id = ? ORDER BY executed_at DESC", (test_id,))
        else:
            cursor.execute("SELECT * FROM entrypoint_test_runs ORDER BY executed_at DESC")
        runs = [dict(r) for r in cursor.fetchall()]
    return {"success": True, "runs": runs}


def insert_test_run(workload_path: str, test_id: int, status: str, error_message: str = None, duration_seconds: float = None, test_method: str = None) -> dict:
    if status not in VALID_TEST_STATUSES:
        return {"error": f"Invalid status '{status}'. Must be one of {VALID_TEST_STATUSES}"}
    p = _db_from_workload(workload_path)
    with _connect(p) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO entrypoint_test_runs (test_id, test_method, status, error_message, duration_seconds) VALUES (?, ?, ?, ?, ?)",
            (test_id, test_method, status, error_message, duration_seconds),
        )
        cursor.execute("UPDATE entrypoint_tests SET status = ? WHERE id = ?", (status, test_id))
        conn.commit()
        return {"success": True, "run_id": cursor.lastrowid}


def update_test_status(workload_path: str, test_id: int, status: str) -> dict:
    if status not in VALID_TEST_STATUSES:
        return {"error": f"Invalid status '{status}'. Must be one of {VALID_TEST_STATUSES}"}
    p = _db_from_workload(workload_path)
    with _connect(p) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE entrypoint_tests SET status = ? WHERE id = ?", (status, test_id))
        conn.commit()
    return {"success": True}


def has_tests(workload_path: str = None, *, db_path: str = None) -> bool:
    p = resolve_db(workload_path, db_path)
    if not os.path.exists(p):
        return False
    try:
        with _connect(p) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entrypoint_tests'")
            if not cursor.fetchone():
                return False
            cursor.execute("SELECT COUNT(*) FROM entrypoint_tests")
            return cursor.fetchone()[0] > 0
    except Exception:
        return False


def export_test_results(workload_path: str) -> dict:
    """Export test results to a single CSV in dvp/04-results/testing-results/."""
    from datetime import datetime

    p = _db_from_workload(workload_path)
    create_tests_table(db_path=p)

    output_dir = os.path.join(workload_path, "dvp", "04-results", "testing-results")
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"test_results_{ts}.csv"
    filepath = os.path.join(output_dir, filename)

    with _connect(p) as conn:
        cursor = conn.cursor()
        # Check if dependency summary table exists for island lookup
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_dependency_summary'")
        has_deps = cursor.fetchone() is not None
        if has_deps:
            cursor.execute("""
                SELECT
                    t.entrypoint_name,
                    t.entrypoint_source,
                    d.island,
                    t.test_file,
                    t.test_type,
                    t.status       AS test_status,
                    t.created_at,
                    r.test_method,
                    r.status       AS run_status,
                    r.error_message,
                    r.duration_seconds,
                    r.executed_at
                FROM entrypoint_tests t
                LEFT JOIN artifact_dependency_summary d
                    ON d.file_id = CASE
                        WHEN INSTR(t.entrypoint_source, ':') > 0
                        THEN SUBSTR(t.entrypoint_source, 1, INSTR(t.entrypoint_source, ':') - 1)
                        ELSE t.entrypoint_source
                    END
                LEFT JOIN entrypoint_test_runs r ON r.test_id = t.id
                ORDER BY t.id, r.executed_at DESC
            """)
        else:
            cursor.execute("""
                SELECT
                    t.entrypoint_name,
                    t.entrypoint_source,
                    NULL AS island,
                    t.test_file,
                    t.test_type,
                    t.status       AS test_status,
                    t.created_at,
                    r.test_method,
                    r.status       AS run_status,
                    r.error_message,
                    r.duration_seconds,
                    r.executed_at
                FROM entrypoint_tests t
                LEFT JOIN entrypoint_test_runs r ON r.test_id = t.id
                ORDER BY t.id, r.executed_at DESC
            """)
        rows = cursor.fetchall()

    headers = [
        "entrypoint_name", "entrypoint_source", "island", "test_file", "test_type",
        "test_status", "created_at", "test_method", "run_status",
        "error_message", "duration_seconds", "executed_at",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row[h] for h in headers])

    return {"success": True, "path": output_dir, "file": filename, "rows": len(rows)}


# ===== Convenience helpers (used by sma_manager.py) ==========================

def has_issues(workload_path: str = None, *, db_path: str = None) -> bool:
    """Return True if the issues table exists and has at least one row."""
    p = resolve_db(workload_path, db_path)
    if not os.path.exists(p):
        return False
    try:
        with _connect(p) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM issues")
            return cursor.fetchone()[0] > 0
    except Exception:
        return False


def read_issues_raw(workload_path: str = None, *, db_path: str = None) -> list[dict]:
    """Return every row from the issues table as a plain dict.

    This is the low-level accessor needed by *extract_ewi_data_from_rows()*.
    """
    with _connect(resolve_db(workload_path, db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM issues")
        return [dict(r) for r in cursor.fetchall()]


def get_input_files_stats(workload_path: str = None, *, db_path: str = None) -> dict:
    """Return aggregate stats from input_files_inventory.

    Returns dict with keys: total_files, file_types (dict of key→{count, lines_of_code}),
    file_types_summary (str).  Returns empty dict if table does not exist.
    """
    p = resolve_db(workload_path, db_path)
    result: dict = {}
    try:
        with _connect(p) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='input_files_inventory'"
            )
            if not cursor.fetchone():
                return result

            cursor.execute("SELECT COUNT(*) FROM input_files_inventory WHERE ignored = 0")
            result["total_files"] = cursor.fetchone()[0]

            cursor.execute("""
                SELECT technology, extension, COUNT(*) as count, SUM(lines_of_code) as total_loc
                FROM input_files_inventory
                WHERE ignored = 0
                GROUP BY technology, extension
                ORDER BY count DESC
            """)
            file_types: dict = {}
            for row in cursor.fetchall():
                tech = row[0] or "Unknown"
                ext = row[1] or ""
                key = f"{tech} ({ext})" if ext else tech
                file_types[key] = {"count": row[2], "lines_of_code": row[3] or 0}
            result["file_types"] = file_types

            type_strs = [f"{k}: {v['count']}" for k, v in file_types.items()]
            result["file_types_summary"] = ", ".join(type_strs[:5])
            if len(type_strs) > 5:
                result["file_types_summary"] += f", +{len(type_strs) - 5} more"
    except Exception:
        pass
    return result


def get_dependency_stats(workload_path: str = None, *, db_path: str = None) -> dict:
    """Return dependency overview stats from artifact_dependency_summary.

    Keys: total_islands, island_files, ready_files.
    """
    p = resolve_db(workload_path, db_path)
    result = {"total_islands": 0, "island_files": 0, "ready_files": 0}
    try:
        with _connect(p) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(DISTINCT island) FROM artifact_dependency_summary WHERE island > 0"
            )
            result["total_islands"] = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(*) FROM artifact_dependency_summary")
            result["island_files"] = cursor.fetchone()[0] or 0
            cursor.execute("""
                SELECT COUNT(*) FROM artifact_dependency_summary
                WHERE recommended_actions LIKE '%No immediate issues%'
            """)
            result["ready_files"] = cursor.fetchone()[0] or 0
    except Exception:
        pass
    return result


def list_dependency_files(workload_path: str = None, *, db_path: str = None) -> list[str]:
    """Return ordered list of file_ids from artifact_dependency_summary."""
    with _connect(resolve_db(workload_path, db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_id FROM artifact_dependency_summary ORDER BY file_id")
        return [row[0] for row in cursor.fetchall()]


def ensure_db(workload_path: str = None, *, db_path: str = None) -> str:
    """Create an empty sqlite database if it does not already exist. Returns the path."""
    p = resolve_db(workload_path, db_path)
    if not os.path.exists(p):
        conn = sqlite3.connect(p)
        conn.close()
    return p

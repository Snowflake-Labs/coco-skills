"""sf.py: the one place lab-video talks to Snowflake (data.py, captions.py, deliver.py share it).

A connection is a name in ~/.snowflake/connections.toml, the file the Snowflake CLI and Cortex Code already
use, so no credential passes through a project file, a command line or this code. Every session is tagged
QUERY_TAG='lab-video', so what a video ran can be found in QUERY_HISTORY afterwards.

Identifiers cannot be bound as parameters, so stage names are validated against Snowflake's identifier
grammar before they reach SQL; file paths and expiry times go in as bound values.
"""
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "gauntlet"))
from _boot import ensure  # noqa: E402

ensure("snowflake.connector")
import snowflake.connector  # noqa: E402

# A quoted identifier may hold almost anything, but not a single quote or a backslash: stage names are also placed
# inside SQL string literals (TO_FILE('@db.schema."stage"', ...)), where either would end the literal.
IDENT = r'(?:[A-Za-z_][A-Za-z0-9_$]*|"(?:[^"\'\\]|"")+")'
STAGE_RE = re.compile(r"^@?(%s)\.(%s)\.(%s)$" % (IDENT, IDENT, IDENT))
QUERY_TAG = "lab-video"


class Refusal(Exception):
    """Something the caller must fix; printed as one line, never as a traceback."""


def connection_name(block=None, cli=None):
    name = cli or (block or {}).get("connection") or os.environ.get("SNOWFLAKE_DEFAULT_CONNECTION_NAME")
    if not name:
        raise Refusal("no Snowflake connection named: pass --connection, or set \"connection\" in video.json "
                      "(a name from ~/.snowflake/connections.toml; `snow connection list` shows them)")
    return name


def connect(name):
    try:
        return snowflake.connector.connect(connection_name=name, session_parameters={"QUERY_TAG": QUERY_TAG})
    except Exception as e:  # the connector's messages are good; keep them, drop the traceback
        raise Refusal("could not connect with %r: %s" % (name, str(e).splitlines()[0][:300]))


def context(cur):
    """Where a result came from. The user name is deliberately not recorded."""
    cur.execute("SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME(), CURRENT_REGION(), CURRENT_ROLE(), "
                "CURRENT_WAREHOUSE()")
    org, account, region, role, wh = cur.fetchone()
    return {"account": "%s-%s" % (org, account) if org else account, "region": region, "role": role, "warehouse": wh}


def stage(fqn):
    """'@DB.SCHEMA.STAGE' or 'DB.SCHEMA.STAGE' -> '@DB.SCHEMA.STAGE', or a Refusal."""
    m = STAGE_RE.match(str(fqn or "").strip())
    if not m:
        raise Refusal("%r is not a stage name: use DB.SCHEMA.STAGE (internal stage)" % fqn)
    return "@" + ".".join(m.groups())


def _unquote(part):
    return part[1:-1].replace('""', '"') if part.startswith('"') else part.upper()


def stage_info(cur, at):
    """SHOW STAGES for one stage. DESC STAGE never names the encryption; SHOW STAGES does, in its type column:
    'INTERNAL NO CSE' is server-side only (SNOWFLAKE_SSE), plain 'INTERNAL' is client-side encrypted."""
    db, schema, name = STAGE_RE.match(at).groups()
    cur.execute("SHOW STAGES LIKE %s IN SCHEMA " + db + "." + schema, (_unquote(name),))
    cols = [c[0].lower() for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    row = next((r for r in rows if r["name"] == _unquote(name)), None)
    if not row:
        raise Refusal("%s does not exist (or this role cannot see it). Create it with:\n%s" % (at, stage_ddl(at)))
    kind = str(row.get("type") or "").upper()
    return {"internal": kind.startswith("INTERNAL"), "encryption": "SNOWFLAKE_SSE" if "NO CSE" in kind else "SNOWFLAKE_FULL"
            if kind.startswith("INTERNAL") else "external", "type": kind, "owner": row.get("owner")}


def stage_ddl(at):
    return ("CREATE STAGE IF NOT EXISTS %s\n  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')\n  DIRECTORY = (ENABLE = TRUE)\n"
            "  COMMENT = 'lab-video renders and narration';" % at[1:])


def require_sse(cur, at):
    """Presigned downloads and AI_TRANSCRIBE both need a server-side encrypted internal stage."""
    info = stage_info(cur, at)
    if not info["internal"]:
        raise Refusal("%s is an external stage; lab-video needs an internal one:\n%s" % (at, stage_ddl(at)))
    if info["encryption"] != "SNOWFLAKE_SSE":
        raise Refusal("%s is %s-encrypted; files downloaded from it through a link arrive corrupted. Use a stage "
                      "created with SNOWFLAKE_SSE:\n%s" % (at, info["encryption"] or "client-side", stage_ddl(at)))
    return info


def put(cur, local, at, folder):
    """Upload one file unchanged (AUTO_COMPRESS defaults to TRUE and would gzip an MP4). Returns the stage path."""
    p = pathlib.Path(local).resolve()
    folder = folder.strip("/")
    uri = ("file://" + str(p)).replace("\\", "/").replace("'", "\\'")
    cur.execute("PUT '%s' %s/%s/ AUTO_COMPRESS = FALSE OVERWRITE = TRUE" % (uri, at, folder))
    row = cur.fetchone()
    if not row or str(row[6]).upper() not in ("UPLOADED", "SKIPPED"):
        raise Refusal("upload of %s did not report UPLOADED: %r" % (p.name, row))
    return "%s/%s" % (folder, p.name)


def remove(cur, at, rel):
    cur.execute("REMOVE %s/%s" % (at, rel.replace("'", "")))


def presign(cur, at, rel, seconds):
    cur.execute("SELECT GET_PRESIGNED_URL(%s, %%s, %%s)" % at, (rel, int(seconds)))
    return cur.fetchone()[0]


def fail(e):
    """Print a Refusal as one line and exit 1; let anything else raise."""
    sys.stderr.write("Refused: %s\n" % e)
    sys.exit(1)

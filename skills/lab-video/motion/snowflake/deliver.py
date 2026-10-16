#!/usr/bin/env python3
"""deliver.py: put a finished video in Snowflake, and prove what arrived is what was made.

    python3 deliver.py setup-stage --stage DB.SCHEMA.STAGE --connection NAME [--confirm]      print (then run) the DDL
    python3 deliver.py stage --project P --stage DB.SCHEMA.STAGE [--file out/x.mp4] [--expires 24h] --confirm
    python3 deliver.py workspace --project P --to DB.SCHEMA.WS:/videos/ [--file ...] [--connection NAME] [--publish] --confirm

Nothing is uploaded without --confirm: a video leaving this machine is an outward action, and the person sending it
decides. Before anything moves, the file must pass probe.py, and the project must pass gate.py --final; a TEST_ONLY_
file is refused unless --allow-test (and its name still says TEST_ONLY).

stage      PUTs the file unchanged (AUTO_COMPRESS = FALSE: the default would gzip an MP4) under a name with its own
           hash, makes a presigned link (GET_PRESIGNED_URL, at most 7 days on an internal stage), downloads it back
           through that link, and compares SHA-256 and duration with the original. The link is a bearer link: anyone
           holding it can download the file until it expires, signed in or not, and Snowflake serves it as
           application/octet-stream, so it downloads rather than plays in the browser. The link is printed once and
           never written to a file; the record (out/<name>.delivery.json) keeps where, when, the hash and the expiry.
           Revoke early with REMOVE @stage/path. Inside Snowflake, who can read the stage is the stage's grants.
workspace  copies the file and its captions into a Snowflake Workspace folder (cortex ws cp), then copies the video
           back down and compares SHA-256 (the listing's size and MD5 describe the stored, encrypted copy, so they
           cannot vouch for the bytes). --publish commits a shared workspace so others see the upload. The place to
           keep a video next to the notebooks and apps it shows.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
MOTION = HERE.parent
GAUNTLET = MOTION.parent / "gauntlet"
MAX_SECONDS = 7 * 24 * 3600


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def seconds(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", str(text))
    if not m:
        raise SystemExit("--expires takes a number with s, m, h or d (e.g. 24h, 7d)")
    v = float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    if not 60 <= v <= MAX_SECONDS:
        raise SystemExit("--expires must be between 1 minute and 7 days (GET_PRESIGNED_URL's limit on an internal stage)")
    return int(v)


def pick_file(root, given):
    if given:
        f = pathlib.Path(given).expanduser().resolve()
    else:
        outs = sorted((root / "out").glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        outs = [p for p in outs if not p.stem.endswith("_clean")]
        if not outs:
            raise SystemExit("no out/*.mp4 in the project: run assemble.py --final first")
        f = outs[0]
    if not f.exists():
        raise SystemExit("no such file: %s" % f)
    return f


def duration(path):
    from ffmpeg_tools import tool
    r = subprocess.run(tool("FFMPEG", "ffmpeg") + ["-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2) if m else None


def preconditions(root, f, allow_test):
    """The file must be one probe.py passes and come from a project gate.py --final passes."""
    if f.name.startswith("TEST_ONLY_") and not allow_test:
        raise SystemExit("Refused: %s is a TEST_ONLY file (synthetic narration). Record the narration, or pass --allow-test "
                         "to deliver it as a labelled test" % f.name)
    spec = json.loads((root / "video.json").read_text())
    cmd = [sys.executable, str(GAUNTLET / "probe.py"), str(f), "--expect-sec", "%.3f" % totals(spec)]
    try:   # the loudness the mix was mastered to sits next to the file it made
        cmd += ["--target-lufs", "%.1f" % json.loads(f.with_suffix(".audio.json").read_text())["targetLufs"]]
    except (FileNotFoundError, KeyError, ValueError):
        cmd += ["--allow-silent"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("Refused: probe.py fails %s:\n%s" % (f.name, "\n".join(ln for ln in p.stdout.splitlines() if ln.startswith("FAIL"))))
    g = subprocess.run([sys.executable, str(MOTION / "gate.py"), "--project", str(root), "--final"], capture_output=True, text=True)
    fails = [ln for ln in g.stdout.splitlines() if ln.startswith("FAIL")]
    if g.returncode != 0 and not (allow_test and fails and all("TEST_ONLY" in ln for ln in fails)):
        raise SystemExit("Refused: gate.py --final fails the project:\n" + "\n".join(fails))
    return spec


def totals(spec):
    sys.path.insert(0, str(MOTION / "audio"))
    import project as pj
    return pj.timing(spec)["seconds"]


def to_stage(root, f, stage_name, expires, cli_conn, allow_test):
    sys.path.insert(0, str(HERE))
    import sf
    spec = preconditions(root, f, allow_test)
    want = sha256(f)
    name = sf.connection_name({"connection": ((spec.get("deliver") or {}).get("connection") or (spec.get("data") or {}).get("connection"))}, cli_conn)
    at = sf.stage(stage_name or (spec.get("deliver") or {}).get("stage") or "")
    folder = "lab-video/videos/%s" % re.sub(r"[^A-Za-z0-9_-]+", "_", root.name)[:60]
    with tempfile.TemporaryDirectory() as tmp:
        # The staged name carries the file's own hash: a new cut can never overwrite a link someone already has.
        named = pathlib.Path(tmp) / ("%s-%s%s" % (f.stem, want[:10], f.suffix))
        shutil.copyfile(f, named)
        con = sf.connect(name)
        try:
            cur = con.cursor()
            where = sf.context(cur)
            info = sf.require_sse(cur, at)
            rel = sf.put(cur, named, at, folder)
            url = sf.presign(cur, at, rel, expires)
            cur.execute("SHOW GRANTS ON STAGE " + at[1:])
            cols = [c[0].lower() for c in cur.description]
            readers = sorted({r["grantee_name"] for r in (dict(zip(cols, x)) for x in cur.fetchall())
                              if r.get("privilege") in ("READ", "OWNERSHIP", "USAGE")})
        finally:
            con.close()
        back = pathlib.Path(tmp) / "back.mp4"
        with urllib.request.urlopen(url, timeout=120) as r, open(back, "wb") as out:
            ctype = r.headers.get("Content-Type", "")
            shutil.copyfileobj(r, out)
        got, d0, d1 = sha256(back), duration(f), duration(back)
    if got != want or d0 != d1:
        raise SystemExit("Refused: the file downloaded through the link is not the file uploaded (sha %s vs %s, %ss vs %ss). "
                         "Remove it: REMOVE %s/%s" % (got[:12], want[:12], d1, d0, at, rel))
    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires)
    rec = {"file": f.name, "sha256": want, "bytes": f.stat().st_size, "seconds": d0, "stage": at, "path": rel,
           "account": where["account"], "role": where["role"], "encryption": info["encryption"],
           "linkExpiresAt": until.isoformat(timespec="seconds"), "linkSeconds": expires, "contentType": ctype,
           "verified": {"sha256": True, "duration": True}, "deliveredAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "readersInSnowflake": readers, "revoke": "REMOVE %s/%s" % (at, rel)}
    f.with_suffix(".delivery.json").write_text(json.dumps(rec, indent=1))
    local = until.astimezone()
    print("STAGED    %s/%s  (%s, %.1f MB, SHA-256 and duration match after download)" % (at, rel, info["encryption"], rec["bytes"] / 1e6))
    print("LINK      %s" % url)
    print("          anyone with this link can download the file until %s %s %d %s (%s), signed in or not;" % (
        local.strftime("%a"), local.strftime("%b"), local.day, local.strftime("%H:%M %Z"),
        "%dh" % (expires // 3600) if expires >= 3600 else "%dm" % (expires // 60)))
    print("          it downloads (%s), it does not play in the browser. Revoke early: %s" % (ctype or "octet-stream", rec["revoke"]))
    print("          in Snowflake, the stage is readable by: %s" % (", ".join(readers) or "its owner only"))
    print("record    %s (the link itself is not saved)" % f.with_suffix(".delivery.json").name)
    return rec


def to_workspace(root, f, dest, cli_conn, allow_test, publish=False):
    if not re.fullmatch(r"[^\s:]+\.[^\s:]+\.[^\s:]+:/(?:[^\s]*/)?", dest or ""):
        raise SystemExit("--to must be a Workspace folder, DB.SCHEMA.WS:/folder/ (ending in /: files keep their names)")
    preconditions(root, f, allow_test)
    ws = dest.split(":/", 1)[0]
    personal = ws.upper().startswith("USER$")
    files = [f] + [p for p in (f.with_suffix(".vtt"),) if p.exists()]
    conn = ["--connection", cli_conn] if cli_conn else []
    for p in files:
        r = subprocess.run(["cortex", "ws", "cp", str(p), dest, "--json", *conn], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("cortex ws cp failed for %s: %s" % (p.name, (r.stdout + r.stderr).strip()[-400:]))
    r = subprocess.run(["cortex", "ws", "ls", dest, "--json", *conn], capture_output=True, text=True)
    try:
        listing = json.loads(r.stdout)
    except ValueError:
        raise SystemExit("could not read cortex ws ls output: %s" % (r.stdout + r.stderr).strip()[-300:])
    rows = listing if isinstance(listing, list) else listing.get("rows") or listing.get("files") or []
    names = {pathlib.PurePosixPath(str(x.get("name") or x.get("path") or "")).name: x for x in rows if isinstance(x, dict)}
    missing = [p.name for p in files if p.name not in names]
    if missing:
        raise SystemExit("copied, but %s is not listed in %s afterwards: %s" % (", ".join(missing), dest, json.dumps(listing)[:300]))
    # A workspace stores files client-side encrypted, so the listing's size (padded to the 16-byte cipher block) and
    # MD5 describe the encrypted copy, not the video. The only real check is to read the file back and compare.
    with tempfile.TemporaryDirectory() as tmp:
        back = subprocess.run(["cortex", "ws", "cp", dest + f.name, tmp + "/", "--json", *conn], capture_output=True, text=True)
        got = pathlib.Path(tmp) / f.name
        if back.returncode != 0 or not got.exists():
            raise SystemExit("copied, but reading %s back failed: %s" % (f.name, (back.stdout + back.stderr).strip()[-300:]))
        if sha256(got) != sha256(f):
            raise SystemExit("copied, but %s read back from the Workspace differs from the local file" % f.name)
    rec = {"file": f.name, "sha256": sha256(f), "workspace": dest, "files": [p.name for p in files], "verified": "sha256 read back",
           "deliveredAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    # A shared workspace keeps uploads in its private "live" version until COMMIT publishes them to everyone with
    # READ on it; a personal (USER$) workspace has nobody else to publish to.
    if not personal and publish:
        sys.path.insert(0, str(HERE))
        import sf
        spec = json.loads((root / "video.json").read_text())
        con = sf.connect(sf.connection_name({"connection": (spec.get("data") or {}).get("connection")}, cli_conn))
        try:
            con.cursor().execute("ALTER WORKSPACE IDENTIFIER(%s) COMMIT", (ws,))
        finally:
            con.close()
        rec["published"] = True
    f.with_suffix(".workspace.json").write_text(json.dumps(rec, indent=1))
    print("COPIED    %s -> %s  (read back: SHA-256 matches)" % (", ".join(rec["files"]), dest))
    if personal:
        print("          a personal workspace: only its owner sees it")
    elif publish:
        print("          published (ALTER WORKSPACE ... COMMIT): roles with READ on %s see it now" % ws)
    else:
        print("          not published yet: other users see it after ALTER WORKSPACE %s COMMIT (or pass --publish)" % ws)
    return rec


def setup_stage(stage_name, cli_conn, confirm):
    sys.path.insert(0, str(HERE))
    import sf
    at = sf.stage(stage_name)
    ddl = sf.stage_ddl(at)
    print(ddl)
    if not confirm:
        print("-- not run: pass --confirm to create it (the role needs CREATE STAGE on the schema)")
        return
    con = sf.connect(sf.connection_name(None, cli_conn))
    try:
        cur = con.cursor()
        cur.execute(ddl.rstrip(";"))
        print(cur.fetchone()[0])
        sf.require_sse(cur, at)
        print("ok        %s is internal and SNOWFLAKE_SSE" % at)
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s0 = sub.add_parser("setup-stage")
    s0.add_argument("--stage", required=True)
    s0.add_argument("--connection")
    s0.add_argument("--confirm", action="store_true")
    for name in ("stage", "workspace"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--file")
        p.add_argument("--connection")
        p.add_argument("--allow-test", action="store_true", help="deliver a TEST_ONLY_ file (synthetic narration), labelled as such")
        p.add_argument("--confirm", action="store_true", help="required: nothing leaves this machine without it")
        if name == "stage":
            p.add_argument("--stage")
            p.add_argument("--expires", default="24h", help="link lifetime, 1m to 7d (default 24h)")
        else:
            p.add_argument("--to", required=True, help="DB.SCHEMA.WS:/folder/")
            p.add_argument("--publish", action="store_true", help="shared workspace: COMMIT, so roles with READ see it")
    a = ap.parse_args()
    sys.path.insert(0, str(GAUNTLET))
    sys.path.insert(0, str(HERE))
    import sf
    try:
        if a.cmd == "setup-stage":
            return setup_stage(a.stage, a.connection, a.confirm)
        root = pathlib.Path(a.project).expanduser().resolve()
        f = pick_file(root, a.file)
        if not a.confirm:
            where = "a presigned link on %s" % (a.stage or "the project's deliver.stage") if a.cmd == "stage" else a.to
            raise SystemExit("Not sent: %s would go to %s. Pass --confirm to send it." % (f.name, where))
        if a.cmd == "stage":
            to_stage(root, f, a.stage, seconds(a.expires), a.connection, a.allow_test)
        else:
            to_workspace(root, f, a.to, a.connection, a.allow_test, a.publish)
    except sf.Refusal as e:
        sf.fail(e)


if __name__ == "__main__":
    main()

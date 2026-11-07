#!/usr/bin/env python3
"""Snowflake-mode referee: known answers and seeded mistakes, with no Snowflake account needed.

    python3 snowflake.py            # every check must PASS; about a minute (it renders a small project)

What it proves, each against a seeded mistake that must be caught:
  format    data.fmt lands on exactly the string JavaScript's toLocaleString shows for the same `to`
            (so a count-up can never end one digit away from the lock), carries 999,950 into "1.0M",
            refuses a fraction without decimals, and never rounds 99.96% up to "100.0%"
  render    a scene bound to a hand-made lock renders; a lock whose display does not match its own value
            makes SF.count throw, so the render FAILS instead of showing a figure the lock does not hold;
            a missing lock refuses to render
  gate      clean project: 0 fail. Each mutation FAILs: a hard-coded figure in place of the bound one, SQL
            edited after it ran, a stale lock on --final, a bound figure with no "as of" on screen
  review    the review server refuses an edit to a bound param and to a param declared editable:false
Work happens in a temp directory; nothing is written into the skill.
"""
import copy
import datetime
import decimal
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

MOTION = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOTION / "snowflake"))
import data as D  # noqa: E402  (stdlib at import time)

results = []


def check(ok, what, detail=""):
    results.append(bool(ok))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", what, ("  " + detail) if detail else ""))


# ---- format: Python's string must be what the browser lands on -------------------------------------------
CASES = [(2149281003.82, {"style": "usd_compact"}, "$2.1B"), (999950, {"style": "compact"}, "1.0M"),
         (999949, {"style": "compact"}, "999.9K"), (1500000, {"style": "int"}, "1,500,000"),
         (1234567.891, {"style": "number", "decimals": 2}, "1,234,567.89"), (0.9996, {"style": "percent"}, "99.9%"),
         (0.12345, {"style": "percent"}, "12.3%"), (-0.00004, {"style": "percent"}, "0.0%"),
         (2.15, {"style": "number", "decimals": 1}, "2.2"), (439820291.15, {"style": "usd_compact", "unit": "M"}, "$439.8M"),
         (12, {"style": "usd"}, "$12"), (5, {"style": "compact"}, "5"), (0.99999, {"style": "percent", "round": "down"}, "99.9%")]


def format_checks():
    rows = [D.fmt(decimal.Decimal(str(raw)), spec) for raw, spec, _ in CASES]
    want = [w for _, _, w in CASES]
    check([r["display"] for r in rows] == want, "data.fmt gives the expected strings for %d cases" % len(rows),
          "" if [r["display"] for r in rows] == want else str([(r["display"], w) for r, w in zip(rows, want) if r["display"] != w]))
    js = ("const rows=" + json.dumps(rows) + ";console.log(JSON.stringify(rows.map(r=>(r.prefix||'')+"
          "r.to.toLocaleString('en-US',{minimumFractionDigits:r.decimals,maximumFractionDigits:r.decimals})+(r.suffix||''))))")
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    got = json.loads(p.stdout or "[]")
    check(got == [r["display"] for r in rows], "JavaScript's toLocaleString lands on every one of them",
          "" if got == [r["display"] for r in rows] else str([(g, r["display"]) for g, r in zip(got, rows) if g != r["display"]]))
    check("note" in rows[5] and rows[5]["display"] == "99.9%", "99.96% is shown as 99.9%, not rounded up to a perfect 100%")
    for raw, spec, why in [(2.5, {"style": "number"}, "a fraction with no decimals"), (5, {"style": "compact", "unit": "X"}, "an unknown unit")]:
        try:
            D.fmt(decimal.Decimal(str(raw)), spec)
            check(False, "refuses %s" % why)
        except ValueError:
            check(True, "refuses %s" % why)


# ---- a small bound project ---------------------------------------------------------------------------------
SCENE = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/motion/scene.css">
<style>body{background:#fff;color:#111;font:64px -apple-system,Helvetica,Arial,sans-serif}#v{position:absolute;left:80px;top:120px}
#s{position:absolute;left:80px;top:320px;font-size:28px}</style></head><body><div id="stage"><div id="v"></div><div id="s"></div></div>
<script src="/motion/motion.js"></script><script src="/motion/sfdata.js"></script><script>
Motion.scene({ params: {}, build() { return { v: document.getElementById("v"), s: document.getElementById("s") }; },
  frame(ctx, t) { ctx.refs.v.textContent = __VALUE__; __SOURCE__ } });
</script></body></html>"""
GOOD_VALUE, GOOD_SOURCE = 'SF.count(t, 0, 0.5, "total")', 'ctx.refs.s.textContent = SF.source("total");'
SOURCES = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/motion/scene.css"></head>
<body><div id="stage"><div id="l" style="position:absolute;left:80px;top:120px;font-size:30px"></div></div>
<script src="/motion/motion.js"></script><script src="/motion/sfdata.js"></script><script>
Motion.scene({ params: {}, build() { const l = document.getElementById("l");
  l.textContent = SF.sources().map((q) => q.label + " as of " + q.asOf).join(" / "); return {}; }, frame() {} });
</script></body></html>"""
SQL = "SELECT SUM(O_TOTALPRICE) AS TOTAL FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS"
SPEC = {"title": "sf referee", "fps": 30, "width": 960, "height": 540, "audio": {"mode": "silent"},
        "data": {"connection": "unused", "maxAgeDays": 7,
                 "queries": {"orders": {"label": "TPC-H sample data", "sql": SQL,
                                        "values": {"total": {"column": "TOTAL", "style": "usd_compact", "decimals": 1}}}}},
        "scenes": [{"id": "stat", "src": "scenes/stat.html", "dur": 1.0, "bind": {"total": "orders.total"}},
                   {"id": "cta", "role": "cta", "src": "scenes/cta.html", "dur": 1.0}]}


def lock(days_old=0.0, display=None):
    ran = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_old)
    v = D.fmt(decimal.Decimal("226825839427.69"), {"style": "usd_compact", "decimals": 1})
    if display:
        v["display"] = display
    return {"version": 1, "connection": "unused", "account": "REFEREE", "role": "REFEREE", "resolvedAt": ran.isoformat(),
            "queries": {"orders": {"label": "TPC-H sample data", "sql": SQL, "sqlSha": D.sql_sha(SQL), "queryId": "referee",
                                   "ranAt": ran.isoformat(timespec="seconds"), **D.as_of(ran), "columns": ["TOTAL"],
                                   "rowCount": 1, "values": {"total": v}}}}


def make(root, spec=None, value=GOOD_VALUE, source=GOOD_SOURCE, lk=None):
    if root.exists():
        shutil.rmtree(root)
    (root / "scenes").mkdir(parents=True)
    (root / "video.json").write_text(json.dumps(spec or SPEC, indent=1))
    (root / "scenes" / "stat.html").write_text(SCENE.replace("__VALUE__", value).replace("__SOURCE__", source))
    (root / "scenes" / "cta.html").write_text(SCENE.replace("__VALUE__", '"Ask for a video"').replace("__SOURCE__", ""))
    (root / "scenes" / "sources.html").write_text(SOURCES)
    if lk is not False:
        (root / "data").mkdir()
        (root / "data" / "data.lock.json").write_text(json.dumps(lk or lock(), indent=1))


def render(root):
    return subprocess.run(["node", str(MOTION / "render.mjs"), "--project", str(root), "--quiet"], capture_output=True, text=True)


def gate(root, *extra):
    p = subprocess.run([sys.executable, str(MOTION / "gate.py"), "--project", str(root), *extra], capture_output=True, text=True)
    return p.returncode, [ln for ln in p.stdout.splitlines() if ln.startswith("FAIL")], p.stdout


def project_checks(tmp):
    root = tmp / "p"
    make(root)
    r = render(root)
    check(r.returncode == 0, "a scene bound to the lock renders", (r.stderr or r.stdout).strip()[-200:] if r.returncode else "")
    man = json.loads((root / "build" / "manifest.json").read_text())["scenes"]["stat"]
    check("$226.8B" in " ".join(man.get("screenTextLast") or []), "its last frame shows the lock's $226.8B",
          str(man.get("screenTextLast")))
    code, fails, out = gate(root)
    check(code == 0 and not fails, "gate on the clean project: 0 fail", "; ".join(fails)[:300])
    check("data: 1 bound figures land on screen as resolved" in out, "gate reports the bound figure as checked")

    make(root, value='"$1.0B"')
    render(root)
    code, fails, _ = gate(root)
    check(any("the last frame does not show it" in f for f in fails), "a hard-coded figure in place of the bound one FAILs")

    spec = copy.deepcopy(SPEC)
    spec["data"]["queries"]["orders"]["sql"] = SQL + " WHERE O_ORDERSTATUS = 'F'"
    make(root, spec=spec)
    render(root)
    code, fails, _ = gate(root)
    check(any("changed since it ran" in f for f in fails), "SQL edited after it was resolved FAILs")

    make(root, lk=lock(days_old=40))
    render(root)
    code, fails, out = gate(root)
    check(not fails and "past maxAgeDays" in out, "a 40-day-old lock WARNs on a draft")
    code, fails, _ = gate(root, "--final")
    check(any("past maxAgeDays" in f for f in fails), "and FAILs on --final")

    make(root, source="")
    render(root)
    code, fails, _ = gate(root)
    check(any("no \"as of" in f for f in fails), "a bound figure with no \"as of\" on screen FAILs")
    spec = copy.deepcopy(SPEC)
    spec["scenes"].insert(1, {"id": "sources", "role": "sources", "src": "scenes/sources.html", "dur": 1.0})
    make(root, spec=spec, source="")
    r = render(root)
    code, fails, _ = gate(root)
    check(r.returncode == 0 and not fails, "the same figure passes once a role \"sources\" card lists its query", "; ".join(fails)[:200])

    make(root, lk=lock(display="$300.0B"))
    r = render(root)
    check(r.returncode != 0 and "SF.count" in (r.stdout + r.stderr), "a lock whose display disagrees with its value fails the render")

    make(root, lk=False)
    r = render(root)
    check(r.returncode != 0 and "data.lock.json does not exist" in (r.stdout + r.stderr), "no lock: render refuses with the fix")
    return root


def review_checks(tmp):
    root = tmp / "r"
    make(root)
    render(root)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen(["node", str(MOTION / "review" / "server.mjs"), "--project", str(root), "--port", str(port)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        info = None
        for _ in range(100):
            try:
                info = json.loads((root / "review" / ".server.json").read_text())
                break
            except (FileNotFoundError, ValueError):
                time.sleep(0.1)
        if not info:
            check(False, "review server started")
            return

        def edit(param, value):
            req = urllib.request.Request("http://127.0.0.1:%d/api/edit?k=%s" % (port, info["token"]), method="POST",
                                         data=json.dumps({"scene": "stat", "param": param, "value": value}).encode(),
                                         headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        code, body = edit("total", "$9.9B")
        check(code == 400 and "Snowflake" in body.get("error", ""), "review refuses an edit to a bound param", str(body)[:120])
        man = root / "build" / "manifest.json"
        m = json.loads(man.read_text())
        m["scenes"]["stat"]["schema"] = {"fixed": {"type": "text", "value": "x", "editable": False}}
        man.write_text(json.dumps(m))
        code, body = edit("fixed", "y")
        check(code == 400 and "not editable" in body.get("error", ""), "and an edit to a param declared editable:false", str(body)[:120])
        st = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/api/state?k=%s" % (port, info["token"]), timeout=10).read())
        sch = next(x for x in st["scenes"] if x["id"] == "stat")["schema"]
        check(sch.get("total", {}).get("editable") is False, "the page is told the bound param is read-only")
        check(json.loads((root / "video.json").read_text()) == SPEC, "video.json is untouched by the refused edits")
    finally:
        srv.terminate()
        srv.wait(timeout=10)


def main():
    format_checks()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="lv-sf-referee-"))
    try:
        project_checks(tmp)
        review_checks(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("---\n%d/%d Snowflake-mode checks passed" % (sum(results), len(results)))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

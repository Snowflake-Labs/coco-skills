#!/usr/bin/env python3
"""Tier 1-2 gate for a motion-mode project. Pure stdlib, sub-second.

    python3 gate.py --project ~/videos/my_video
    python3 gate.py --project P --strict-jargon

Run it after a render: besides video.json it reads build/manifest.json, which holds each scene's declared
params (roles, limits) and the text that was actually on screen while it rendered. Filmed pages and
JS-built copy never appear in video.json, so checking only the spec would miss them.

Checks, each from a real failure (../patterns/anti-patterns.md):
  runtime and beat length                 (#12)   headline words, calibrated like copy_gate (#12)
  integer count-ups                       (#3)    one call to action
  internal vocabulary, two tiers          (#11)   a source for every claimed number (#4)
  "simulated" scenes say so on screen     (#4)    the project's own block list (spoilers, names)
  narration: every line readable in its scene, recorded, current and in time; spoken words get the
             same vocabulary and block-list passes as on-screen copy (a spoiler said aloud is a spoiler)
  music: a library track with an allowed licence, and its credit on screen when the licence needs one
  Snowflake values (snowflake/data.py): resolved, from the SQL now in video.json, fresh enough, landing on
             screen exactly as the lock says, with an "as of" on the scene or a role "sources" card
  filmed apps (snowflake/live.mjs): filmed with the current steps, long enough, scanned (page text and OCR),
             and clean against today's block list; everything the take showed is read like on-screen copy
  captions (snowflake/captions.py): from the current recording, never a TEST_ONLY voice on a final, at most
             2 lines of 42, and read for the same vocabulary and names (they show what was said)
Exit 0 on pass or warnings only, 1 on any FAIL. --final also FAILs what a draft may still lack.
"""
import argparse
import datetime
import html.parser
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gauntlet"))
from copy_gate import HARD_JARGON, SOFT_JARGON  # noqa: E402  (one calibrated list, not two)
sys.path.insert(0, str(HERE / "audio"))
import project as audioproject  # noqa: E402  (stdlib only, like this file)
sys.path.insert(0, str(HERE / "snowflake"))
from data import sql_sha  # noqa: E402  (stdlib at import time; Snowflake is only touched by resolve)
import scan as sfscan  # noqa: E402  (the one definition of what filmed footage may not show)
import captions as sfcaptions  # noqa: E402  (stdlib at import time)

HEADLINE_WARN, HEADLINE_FAIL = 6, 8   # copy_gate: shipped p90 6, max 6; FAIL above the worst good case
LABEL_WORDS = ("simulated", "invented", "illustration", "sample data", "demo data", "not real")
WPS_WARN, WPS_FAIL = 2.7, 3.2          # words per second of room: 2.2 to 2.5 read naturally (a shipped narrated teaser: 2.2)


class Visible(html.parser.HTMLParser):
    """Static text a viewer could see in a scene file (scripts, styles and templates excluded)."""
    SKIP = {"script", "style", "template", "title", "head"}

    def __init__(self):
        super().__init__()
        self.depth, self.out = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth and data.strip():
            self.out.append(data.strip())


def words(s):
    # "*doc*." splits into "doc" and "."; only tokens with a letter or digit are words.
    return len([w for w in re.split(r"\s+", str(s).replace("*", " ").replace("_", " ")) if re.search(r"[^\W_]", w)])


def audio_checks(root, spec, man, final, texts, fails, warns, notes):
    try:
        au = audioproject.audio_spec(spec)
    except SystemExit as e:
        fails.append(str(e))
        return
    sc = audioproject.script(spec)
    for s in sc["scenes"]:
        if not s["say"]:
            continue
        texts.append((f"{s['id']} (narration)", s["say"]))
        if s["room"] <= 0.4:
            fails.append(f"{s['id']}: a narration line in a {s['dur']:.1f}s scene leaves no room to say it")
        elif s["wps"] > WPS_FAIL:
            fails.append(f"{s['id']}: {s['words']} words in {s['room']:.1f}s of room ({s['wps']} words/s) cannot be read; "
                         f"cut to {int(s['room'] * 2.5)} words or lengthen the scene")
        elif s["wps"] > WPS_WARN:
            warns.append(f"{s['id']}: {s['words']} words in {s['room']:.1f}s ({s['wps']} words/s) is a rush; 2.5 reads naturally")
    lines = [s for s in sc["scenes"] if s["say"]]
    if lines and not au["narration"]:
        warns.append(f"{len(lines)} scenes have narration lines but audio.narration is not true, so none will play")
    if au["narration"]:
        if not lines:
            fails.append("audio.narration is true but no scene has a \"say\" line")
        st = audioproject.line_status(root, spec)
        for r in st["scenes"]:
            if r["status"] == "long":
                (fails if final else warns).append(f"{r['scene']}: the recorded line {r['message']}")
            elif r["status"] in ("missing", "stale") and final:
                fails.append(f"{r['scene']}: narration {r.get('message', r['status'])}")
        sm = st["summary"]
        if sm["missing"] or sm["stale"]:
            if not final:
                warns.append(f"narration: {sm['ok'] + sm['long']} of {sm['lines']} lines recorded"
                             + (f", {sm['stale']} changed since recording" if sm["stale"] else "") + " (Voice Studio)")
        else:
            notes.append(f"narration: all {sm['lines']} lines recorded ({sc['words']} words)")
        if sm["test"]:
            (fails if final else warns).append("narration includes a TEST_ONLY synthetic voice; record it before shipping")
    m = au["music"]
    if m:
        try:
            rec = json.loads((audioproject.music_dir() / m["track"] / "track.json").read_text())
        except (FileNotFoundError, ValueError):
            fails.append(f"audio.music.track {m['track']!r} is not in the music library (audio/library.py list)")
            return
        kind = rec["licence"]["kind"]
        if kind not in ("by", "zero", "mark"):
            fails.append(f"music {rec['title']!r} is {rec['licence']['short']}, which the library does not allow")
        if kind == "by":
            shown = " ".join(" ".join(e.get("screenText") or []) for e in man.values()).lower()
            on = rec["title"].lower() in shown and rec["artist"].lower() in shown
            if not on:
                hint = "render it" if any(sc_.get("showsCredit") for sc_ in spec.get("scenes", [])) else 'add "showsCredit": true to the end card'
                (fails if final else warns).append(f"music {rec['title']!r} is {rec['licence']['short']}: its credit is not on screen yet ({hint})")
            else:
                notes.append(f"{rec['credit']} (credit on screen)")
        else:
            notes.append(rec["credit"])


def data_checks(root, spec, man, final, fails, warns, notes):
    """Values bound from Snowflake: the lock exists, matches the SQL in video.json, is fresh enough, and every bound
    figure is on the scene's last frame exactly as the lock formats it, with its as-of date somewhere a viewer sees it."""
    block = spec.get("data") or {}
    scenes = spec.get("scenes", [])
    bound = [s for s in scenes if s.get("bind")]
    if not bound and not block.get("queries"):
        return
    try:
        lock = json.loads((root / "data" / "data.lock.json").read_text())
    except FileNotFoundError:
        fails.append("data: values are bound but data/data.lock.json is missing (run snowflake/data.py resolve)")
        return
    have = lock.get("queries") or {}
    max_age = float(block.get("maxAgeDays", 7))
    now = datetime.datetime.now(datetime.timezone.utc)
    for qid, q in (block.get("queries") or {}).items():
        lq = have.get(qid)
        if not lq:
            fails.append(f"data: query {qid} has never been resolved (run snowflake/data.py resolve)")
            continue
        if lq.get("sqlSha") != sql_sha(q.get("sql", "")):
            fails.append(f"data: query {qid} changed since it ran, so the screen shows the old SQL's result (re-resolve)")
        age = (now - datetime.datetime.fromisoformat(lq["ranAt"])).total_seconds() / 86400
        if age > max_age:
            (fails if final and not block.get("acceptStale") else warns).append(
                f"data: query {qid} ran {age:.1f} days ago, past maxAgeDays {max_age:g} (re-resolve, or set data.acceptStale)")
        for k, v in (lq.get("values") or {}).items():
            if v.get("note"):
                notes.append(f"data: {qid}.{k} {v['note']}")
    sources_card = " ".join(" ".join(man.get(s["id"], {}).get("screenText") or []) for s in scenes if s.get("role") == "sources")
    shown_ok = 0
    for s in bound:
        sid, m = s["id"], man.get(s["id"], {})
        seen = " ".join(m.get("screenText") or [])
        last = m.get("screenTextLast")
        for k, ref in s["bind"].items():
            q, _, rest = str(ref).partition(".")
            lq = have.get(q)
            if not lq:
                fails.append(f"{sid}.{k}: binds {ref}, but query {q} is not in the lock")
                continue
            if rest == "rows":
                cells = [c["display"] for r in (lq.get("rows") or []) for c in r.values()]
                if m and cells and not any(c in seen for c in cells):
                    warns.append(f"{sid}.{k}: none of the {len(lq.get('rows') or [])} rows of {q} appear on screen")
            else:
                v = (lq.get("values") or {}).get(rest)
                if not v:
                    fails.append(f"{sid}.{k}: binds {ref}, but {q} has no value {rest}")
                    continue
                if m and last is None:
                    warns.append(f"{sid}: rendered before the last frame's text was kept; render --force it to check {k}")
                elif m and v["display"] not in " ".join(last):
                    fails.append(f"{sid}.{k}: the lock says {v['display']!r} ({ref}) but the last frame does not show it")
                elif m:
                    shown_ok += 1
            if m and lq.get("asOf") and lq["asOf"] not in seen and lq["asOf"] not in sources_card:
                fails.append(f"{sid}.{k}: a figure from Snowflake with no \"as of {lq['asOf']}\" on screen; show "
                             "SF.source() on the scene or on a role \"sources\" card")
    if shown_ok:
        notes.append(f"data: {shown_ok} bound figures land on screen as resolved ({lock.get('account', '?')}, role {lock.get('role', '?')})")


def live_checks(root, spec, final, texts, fails, warns, notes):
    """Footage filmed by snowflake/live.mjs: taken with the current steps, long enough, scanned, and clean against
    today's lists. Every line the take showed joins `texts`, so the vocabulary and block-list passes read it too."""
    for s in spec.get("scenes", []):
        cap = s.get("capture")
        if not cap:
            continue
        sid = s["id"]
        try:
            take = json.loads((root / "takes" / sid / "current.json").read_text())
        except FileNotFoundError:
            (fails if final else warns).append(f"{sid}: not filmed yet (node motion/snowflake/live.mjs --project <this project> --scene {sid})")
            continue
        n = take.get("take")
        if take.get("capture") != cap:
            (fails if final else warns).append(f"{sid}: the capture block changed after take {n} was filmed; film it again")
        need = float(s.get("in", 0)) + float(s["dur"])
        if float(take.get("seconds", 0)) + 0.05 < need:
            fails.append(f"{sid}: take {n} is {take.get('seconds', 0):.2f}s but the scene uses {need:.2f}s of it (in + dur)")
        sc = take.get("scan")
        if not sc:
            fails.append(f"{sid}: take {n} was never scanned (motion/snowflake/scan.py --take takes/{sid}/current.json)")
        ocr = (sc or {}).get("ocr") or {}
        ocr_lines = [t["line"] for t in ocr.get("lines", [])]
        lines = [t["line"] for t in take.get("text", [])] + ocr_lines
        # Exact matches of the block list come from the vocabulary passes below (lines join `texts`); here: the
        # scene's own forbid list, emails and credentials, and OCR text that is one or two letters off a blocked term.
        found = sfscan.findings(spec, s, lines, gate_lists=False) + \
            [h for h in sfscan.findings(spec, s, ocr_lines, approx=True) if h[2] == "approx"]
        for what, line, how in found:
            fails.append(f"{sid} (filmed): " + (f"OCR text {line[:60]!r} looks like {what} (if a person checked it is "
                         "not, list the line in capture.ocrAllow)" if how == "approx" else f"shows {what}: {line[:70]!r}"))
        if sc and not ocr.get("ran") and not cap.get("ocrReviewed"):
            warns.append(f"{sid}: the take was not OCR'd ({ocr.get('why', 'skipped')}), so text drawn on canvas went unread; "
                         "look at it by eye, then set capture.ocrReviewed")
        if float(take.get("paintedFps", 30)) < 8:
            warns.append(f"{sid}: Chrome painted {take.get('paintedFps')} frames/s while filming; motion will stutter (keep the window visible)")
        if take.get("pageErrors"):
            warns.append(f"{sid}: the page reported errors while filming: {take['pageErrors'][0][:80]}")
        texts.extend((f"{sid} (filmed)", ln) for ln in lines)
        notes.append(f"{sid}: take {n}, {float(take.get('seconds', 0)):.1f}s filmed, {len(lines)} lines read"
                     + (" (page text and OCR)" if ocr.get("ran") else " (page text)"))


def caption_checks(root, spec, man, final, texts, fails, warns, notes):
    """Captions (snowflake/captions.py): made from the recording the final mix uses, readable, clear of the scenes' own
    copy, and checked for the same vocabulary and names as the screen, because they show what was said."""
    cap = spec.get("captions")
    if not cap:
        return
    if cap.get("burn") and cap.get("position") != "top":
        band = round(int(spec.get("height", 1080)) * (1 - sfcaptions.BAND))
        for s in spec.get("scenes", []):
            low = (man.get(s["id"]) or {}).get("lowestText")
            if low and low > band:
                (fails if final else warns).append(f"{s['id']}: text reaches y={low}px, into the band where burned captions sit "
                                                   f"(below {band}px); move it up, or set captions.position to top")
    st = sfcaptions.status(root, spec)
    if st["state"] == "none":
        notes.append("captions: nothing to caption until narration is recorded")
        return
    if st["state"] == "missing":
        (fails if final else warns).append("captions: none yet (snowflake/captions.py transcribe)")
        return
    if st["state"] == "stale":
        (fails if final else warns).append("captions: made from an older recording of the narration (re-run snowflake/captions.py transcribe)")
        return
    c = st["captions"]
    if c.get("test"):
        (fails if final else warns).append("captions: made from a TEST_ONLY synthetic voice; record the narration, then transcribe again")
    for cue in c.get("cues", []):
        texts.append(("captions", cue["text"]))
        if len(cue["lines"]) > sfcaptions.MAX_LINES or any(len(x) > sfcaptions.LINE_CHARS for x in cue["lines"]):
            fails.append(f"captions: a cue does not fit {sfcaptions.MAX_LINES} lines of {sfcaptions.LINE_CHARS}: {cue['text'][:60]!r}")
    if c.get("fast"):
        warns.append(f"captions: {c['fast']} cues read faster than {sfcaptions.MAX_CPS:g} characters a second (max {c['maxCps']})")
    if cap.get("burn") and not (root / "captions" / "captions.ass").exists():
        fails.append("captions: burn is on but captions/captions.ass is missing (snowflake/captions.py build)")
    notes.append(f"captions: {len(c.get('cues', []))} cues from the current recording (AI_TRANSCRIBE, {c.get('transcribedAt', '')[:10]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--max-total", type=float, default=120.0)
    ap.add_argument("--soft-total", type=float, default=45.0)
    ap.add_argument("--min-beat", type=float, default=2.0)
    ap.add_argument("--max-beat", type=float, default=14.0)
    ap.add_argument("--allow-jargon", action="store_true", help="internal-audience video")
    ap.add_argument("--strict-jargon", action="store_true", help="commercial vocabulary FAILs too")
    ap.add_argument("--final", action="store_true", help="delivery: unrecorded, changed or TEST_ONLY narration FAILs")
    a = ap.parse_args()

    root = pathlib.Path(a.project).expanduser().resolve()
    spec = json.loads((root / "video.json").read_text())
    try:
        man = json.loads((root / "build" / "manifest.json").read_text()).get("scenes", {})
    except FileNotFoundError:
        man = {}
    g = spec.get("gate", {})
    pace = float((spec.get("global") or {}).get("pace", 1))
    fails, warns, notes = [], [], []
    scenes = spec.get("scenes", [])
    if not scenes:
        fails.append("video.json has no scenes")

    texts = []  # (where, text) for the vocabulary and block-list passes
    texts.append(("title", spec.get("title", "")))
    total, ctas = 0.0, []
    for s in scenes:
        sid = s.get("id", "?")
        m = man.get(sid, {})
        schema = m.get("schema", {})
        params = {**{k: d.get("value") for k, d in schema.items()}, **(s.get("params") or {})}
        dur = float(s.get("dur", 0)) * pace
        total += dur
        if dur <= 0:
            fails.append(f"{sid}: dur must be > 0")
        elif dur < a.min_beat and s.get("role") not in ("sting", "transition"):
            warns.append(f"{sid}: {dur:.1f}s is under {a.min_beat}s; readable only if nothing needs reading")
        elif dur > a.max_beat:
            warns.append(f"{sid}: {dur:.1f}s is over {a.max_beat}s without a change; is it dead air?")
        src = root / s.get("src", "")
        if not src.exists() and not str(s.get("src", "")).startswith("/"):
            fails.append(f"{sid}: src {s.get('src')} does not exist")
        elif src.suffix == ".html" and src.exists():
            v = Visible(); v.feed(src.read_text()); texts += [(f"{sid} (scene file)", t) for t in v.out]
        if s.get("role") == "cta":
            ctas.append(sid)
        if not m:
            warns.append(f"{sid}: not rendered yet, so its on-screen text and declared limits were not checked")
        shown = m.get("screenText", [])
        texts += [(f"{sid} (on screen)", t) for t in shown]
        for k, v in params.items():
            d = schema.get(k, {})
            if isinstance(v, str):
                texts.append((f"{sid}.{k}", v))
            if d.get("role") == "headline" or (not d and k in ("headline", "title", "line")):
                n = words(v or "")
                cap = d.get("maxWords", HEADLINE_FAIL)
                if n > cap:
                    fails.append(f"{sid}.{k}: {n}-word headline (FAIL above {cap}): {v!r}")
                elif n > HEADLINE_WARN:
                    warns.append(f"{sid}.{k}: {n}-word headline; shipped videos never went past {HEADLINE_WARN}")
            if d.get("type") == "count" or d.get("role") == "stat":
                if not d.get("decimals") and not float(v or 0).is_integer():
                    fails.append(f"{sid}.{k}: count-up to {v} would round to {round(float(v))} on screen; "
                                 "show the decimal as static text or declare decimals")
            if d.get("claim") or d.get("type") == "count":
                if not (s.get("sources") or {}).get(k) and not d.get("source") and k not in (s.get("bind") or {}):
                    fails.append(f"{sid}.{k}: a number on screen ({v}) with no source; add scene.sources.{k}")
        if s.get("simulated"):
            # Once rendered, only what was on screen counts: a label param that never displays is no label.
            pool = " ".join(shown if shown else [str(x) for x in params.values() if isinstance(x, str)]).lower()
            if not any(w in pool for w in LABEL_WORDS):
                fails.append(f"{sid}: marked simulated but nothing on screen says so "
                             f"(one of: {', '.join(LABEL_WORDS)}). Depict the surface; do not imply the run happened.")

    audio_checks(root, spec, man, a.final, texts, fails, warns, notes)
    data_checks(root, spec, man, a.final, fails, warns, notes)
    live_checks(root, spec, a.final, texts, fails, warns, notes)
    caption_checks(root, spec, man, a.final, texts, fails, warns, notes)

    if total > a.max_total:
        fails.append(f"total {total:.1f}s exceeds the {a.max_total:.0f}s cap")
    elif total > a.soft_total:
        warns.append(f"total {total:.1f}s is past {a.soft_total:.0f}s; is every scene earning its seconds?")
    else:
        notes.append(f"runtime {total:.2f}s across {len(scenes)} scenes")
    if len(ctas) > 1:
        fails.append(f"{len(ctas)} call-to-action scenes ({', '.join(ctas)}); two asks are zero asks")
    elif not ctas:
        warns.append("no scene has role \"cta\"; what should the viewer do?")

    if not a.allow_jargon:
        for where, t in texts:
            for pat in HARD_JARGON:
                if re.search(pat, t, re.I):
                    fails.append(f"{where}: internal vocabulary {pat!r} in {t[:60]!r}")
            for pat in SOFT_JARGON:
                if re.search(pat, t, re.I):
                    (fails if a.strict_jargon else warns).append(f"{where}: commercial vocabulary {pat!r} in {t[:60]!r}")
    # Block-list entries are literal text, or a regex when prefixed "re:" (e.g. "re:\\b4 of 11\\b").
    block = [str(x) for x in g.get("block", []) + g.get("names", [])]
    for where, t in texts:
        for b in block:
            pat = b[3:] if b.startswith("re:") else re.escape(b)
            if re.search(pat, t, re.I):
                fails.append(f"{where}: blocked by this project ({b!r}) in {t[:70]!r}")

    for n in notes:
        print("  ok    " + n)
    for w in dict.fromkeys(warns):
        print("WARN  " + w)
    for f in dict.fromkeys(fails):
        print("FAIL  " + f)
    print("---\n%d fail, %d advisory" % (len(set(fails)), len(set(warns))))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

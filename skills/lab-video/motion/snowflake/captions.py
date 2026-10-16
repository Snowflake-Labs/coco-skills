#!/usr/bin/env python3
"""captions.py: captions from what was actually said, timed by Snowflake's AI_TRANSCRIBE.

    python3 captions.py transcribe --project P [--connection NAME] [--stage DB.SCHEMA.STAGE] [--keep-audio]
    python3 captions.py build      --project P          cues -> captions/captions.ass (burn-in) and .vtt (sidecar)
    python3 captions.py check      --project P          are the captions still the current recording? (exit 1 if not)

video.json:
    "captions": {"stage": "DB.SCHEMA.STAGE", "burn": true, "lexicon": {"Coco": "CoCo"}, "position": "bottom"}
    (the connection comes from "data.connection", "captions.connection" or --connection)

Why transcribe rather than show the script: a caption must say what the viewer hears. Takes drift from the script
(a dropped word, a "the" added, a number read out), and a caption that says something else is wrong in exactly the
way a viewer notices. The script is still compared with the transcript, and every difference is reported.

transcribe mixes the narration stem exactly as the final mix places it (audio/mix.py, same takes, same offsets),
encodes it to 16 kHz mono FLAC (AI_TRANSCRIBE's own rate; speech must begin in the first 5 seconds for language
detection, so leading silence is trimmed and added back to every timestamp), uploads it to an SNOWFLAKE_SSE stage,
runs AI_TRANSCRIBE(..., {'timestamp_granularity': 'word'}), and removes the staged file unless --keep-audio. It needs
a role with SNOWFLAKE.CORTEX_USER and costs 50 tokens per second of audio (about US $0.35 an hour, 10 s minimum).

The captions are keyed to that recipe (each line's take, its words and where it sits), so re-recording or moving a
line makes them stale: gate.py FAILs a final whose captions are stale, missing, or made from a TEST_ONLY voice.
Cues: at most 2 lines of 42 characters, broken at sentence ends and pauses, 0.8 to 6 s on screen, and a reading-speed
warning above 20 characters a second (patterns/snowflake.md).
"""
import argparse
import datetime
import difflib
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
MOTION = HERE.parent
sys.path.insert(0, str(MOTION / "audio"))
sys.path.insert(0, str(MOTION.parent / "gauntlet"))
import project as pj  # noqa: E402  (stdlib only)

CAPS = pathlib.Path("captions")
LINE_CHARS, MAX_LINES, MAX_CPS = 42, 2, 20.0
MIN_CUE, MAX_CUE, PAUSE_BREAK = 0.8, 6.0, 0.55
BAND = 0.20   # the bottom fifth of the frame is the captions' (two lines and their box); scenes keep copy above it


def load(root):
    spec = json.loads((root / "video.json").read_text())
    return spec, spec.get("captions") or {}


def recipe(root, spec):
    """What the voice stem is made of, as the final mix would place it: stdlib, so gate.py can recompute it."""
    st = pj.line_status(root, spec)
    rows = [{"scene": r["scene"], "take": r.get("take"), "lineHash": r["lineHash"], "firstWord": r.get("firstWord"),
             "status": r["status"], "test": bool(r.get("test"))} for r in st["scenes"] if r["status"] != "none"]
    placed = [r for r in rows if r["status"] in ("ok", "long")]
    key = hashlib.sha256(json.dumps(placed, sort_keys=True).encode()).hexdigest()[:16]
    return {"key": key, "lines": rows, "placed": len(placed), "test": any(r["test"] for r in placed),
            "missing": [r["scene"] for r in rows if r["status"] in ("missing", "stale")]}


def stem(root, spec):
    """The narration stem WAV the final mix uses (built if needed), or a Refusal-worthy message."""
    import mix  # numpy; only transcribe needs it
    report = mix.build_stems(root, spec, final=False, allow_test=True, allow_missing=True)
    if not report or not report.get("placed"):
        raise SystemExit("no narration is recorded yet, so there is nothing to caption (Voice Studio, or voice.py testvoice)")
    f = root / report["stems"] / "voice.wav"
    if not f.exists():
        raise SystemExit("the mix did not write a voice stem at %s" % f)
    return f


def encode(src, dst):
    """16 kHz mono FLAC with the leading silence trimmed. Returns the seconds trimmed (added back to timestamps)."""
    from ffmpeg_tools import tool
    ff = tool("FFMPEG", "ffmpeg")
    probe = subprocess.run(ff + ["-hide_banner", "-i", str(src), "-af", "silencedetect=noise=-50dB:d=0.25", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    lead = 0.0
    m = re.search(r"silence_start: (-?[\d.]+)\s*\n.*?silence_end: ([\d.]+)", probe, re.S)
    if m and float(m.group(1)) <= 0.05:
        lead = max(0.0, float(m.group(2)) - 0.25)   # keep a quarter second of room before the first word
    subprocess.run(ff + ["-hide_banner", "-loglevel", "error", "-y", "-ss", "%.3f" % lead, "-i", str(src), "-ac", "1", "-ar", "16000",
                         "-c:a", "flac", str(dst)], check=True)
    return round(lead, 3)


def transcribe(root, cli_conn=None, cli_stage=None, keep=False):
    spec, cap = load(root)
    rc = recipe(root, spec)
    sys.path.insert(0, str(HERE))
    import sf
    name = sf.connection_name({"connection": cap.get("connection") or (spec.get("data") or {}).get("connection")}, cli_conn)
    at = sf.stage(cli_stage or cap.get("stage") or "")
    src = stem(root, spec)
    with tempfile.TemporaryDirectory() as tmp:
        flac = pathlib.Path(tmp) / ("voice-%s.flac" % rc["key"])
        lead = encode(src, flac)
        con = sf.connect(name)
        try:
            cur = con.cursor()
            where = sf.context(cur)
            sf.require_sse(cur, at)
            folder = "lab-video/captions/%s" % re.sub(r"[^A-Za-z0-9_-]+", "_", root.name)[:60]
            rel = sf.put(cur, flac, at, folder)
            try:
                # The stage name was validated by sf.stage() (identifiers cannot be bound); the path is a bound value.
                cur.execute("SELECT AI_TRANSCRIBE(TO_FILE('%s', %%s), {'timestamp_granularity': 'word'})" % at, (rel,))
                raw = cur.fetchone()[0]
                qid = cur.sfqid
            except Exception as e:
                msg = str(e).splitlines()[-1][:300]
                hint = (" The role needs SNOWFLAKE.CORTEX_USER (GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE <role>)."
                        if re.search(r"(?i)privilege|not authorized|cortex_user", msg) else
                        " AI_TRANSCRIBE runs in a few regions; elsewhere set CORTEX_ENABLED_CROSS_REGION." if re.search(r"(?i)region|unavailable", msg) else "")
                raise sf.Refusal("AI_TRANSCRIBE failed: %s.%s" % (msg, hint))
            finally:
                if not keep:
                    sf.remove(cur, at, rel)
        finally:
            con.close()
    out = json.loads(raw) if isinstance(raw, str) else raw
    words = [{"text": w["text"], "start": round(w["start"] + lead, 3), "end": round(w["end"] + lead, 3)} for w in out.get("segments", [])]
    doc = {"recipeKey": rc["key"], "test": rc["test"], "lines": rc["lines"], "transcribedAt": datetime.datetime.now(datetime.timezone.utc)
           .isoformat(timespec="seconds"), "queryId": qid, "connection": name, **{k: where[k] for k in ("account", "role")},
           "stagePath": "%s/%s" % (at, rel), "audioKept": bool(keep), "leadTrimmed": lead,
           "audioSeconds": out.get("audio_duration"), "words": words, "text": " ".join(w["text"] for w in words)}
    (root / CAPS).mkdir(parents=True, exist_ok=True)
    (root / CAPS / "transcript.json").write_text(json.dumps(doc, indent=1))
    print("TRANSCRIBED %d words from %.1fs of narration (query %s)%s" % (len(words), out.get("audio_duration") or 0, qid,
          "  TEST_ONLY voice: these captions are for testing" if rc["test"] else ""))
    compare(spec, doc)
    return doc


def _toks(s):
    return [t for t in re.sub(r"[^\w%$.]+", " ", s.lower()).replace(".", " ").split() if t]


def compare(spec, doc):
    """Report where the transcript differs from the script. Numbers read aloud ('twelve percent' vs '12%') show up
    here too; the caption keeps what Snowflake heard, so these are for a person to read, not to fix automatically."""
    said = " ".join(s["say"] for s in pj.script(spec)["scenes"] if s["say"])
    a, b = _toks(said), _toks(doc["text"])
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    diffs = [(" ".join(a[i1:i2]), " ".join(b[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    print("script    %.0f%% of the script's words were heard as written" % (100 * sm.ratio()))
    for want, got in diffs[:12]:
        print("  script %-28r heard %r" % (want or "-", got or "-"))
    return diffs


def apply_lexicon(words, lex):
    """Exact, whole-word replacements only ("Coco" -> "CoCo"); punctuation stays attached."""
    out = []
    for w in words:
        m = re.match(r"^(\W*)(.*?)(\W*)$", w["text"])
        pre, core, post = m.groups()
        out.append({**w, "text": pre + lex.get(core, core) + post})
    return out


def wrap(text):
    """Split a cue into at most MAX_LINES balanced lines of LINE_CHARS, or None if it does not fit."""
    if len(text) <= LINE_CHARS:
        return [text]
    words = text.split(" ")
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) <= LINE_CHARS and len(b) <= LINE_CHARS:
            score = abs(len(a) - len(b)) + (0 if re.search(r"[,;:]$", a) else 6)   # prefer a break after punctuation
            if best is None or score < best[0]:
                best = (score, [a, b])
    return best and best[1]


def cues_from(words, total):
    # A word that starts in the last fifth of a second (the mix fades a line cut off by the end) is not shown: a
    # caption that flashes for a frame is worse than none.
    words = [w for w in words if w["start"] < total - 0.2]
    cues, cur = [], []

    def flush():
        if cur:
            cues.append({"start": cur[0]["start"], "end": cur[-1]["end"], "words": [w["text"] for w in cur]})
            cur.clear()
    for i, w in enumerate(words):
        if cur:
            text = " ".join(x["text"] for x in cur + [w])
            gap = w["start"] - cur[-1]["end"]
            if wrap(text) is None or gap >= PAUSE_BREAK or w["end"] - cur[0]["start"] > MAX_CUE or re.search(r"[.?!]$", cur[-1]["text"]):
                flush()
        cur.append(w)
    flush()
    for i, c in enumerate(cues):   # hold short cues longer, never into the next one, never past the video
        nxt = cues[i + 1]["start"] - 0.04 if i + 1 < len(cues) else total
        c["end"] = round(min(max(c["end"] + 0.25, c["start"] + MIN_CUE), nxt, total), 3)
        c["text"] = " ".join(c.pop("words"))
        c["lines"] = wrap(c["text"]) or [c["text"]]
        c["cps"] = round(len(c["text"]) / max(0.01, c["end"] - c["start"]), 1)
    return cues


def ts(t, vtt):
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return ("%02d:%02d:%06.3f" % (h, m, s)) if vtt else ("%d:%02d:%05.2f" % (h, m, s))


def build(root):
    spec, cap = load(root)
    try:
        doc = json.loads((root / CAPS / "transcript.json").read_text())
    except FileNotFoundError:
        raise SystemExit("no captions/transcript.json yet: run captions.py transcribe first")
    total = pj.timing(spec)["seconds"]
    words = apply_lexicon(doc["words"], cap.get("lexicon") or {})
    cues = cues_from(words, total)
    w, h = spec.get("width", 1920), spec.get("height", 1080)
    size = round(h * 0.046)                      # 50 px at 1080p
    top = cap.get("position") == "top"
    ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % w, "PlayResY: %d" % h, "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
           "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           # BorderStyle 4: one box behind the whole cue (BackColour, 25% transparent black); 3 boxed each line on its own,
           # which stepped like a staircase on two-line cues. Outline 0, so the glyphs carry no dark halo.
           "Style: Cap,%s,%d,&H00FFFFFF,&H00FFFFFF,&H40000000,&H40000000,0,0,0,0,100,100,0,0,4,0,%d,%d,%d,%d,%d,1"
           % (cap.get("font", "Helvetica"), size, round(size * 0.3), 8 if top else 2, round(w * 0.1), round(w * 0.1), round(h * 0.06)),
           "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    vtt = ["WEBVTT", ""]
    for i, c in enumerate(cues, 1):
        body = "\\N".join(x.replace("{", "(").replace("}", ")") for x in c["lines"])
        ass.append("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s" % (ts(c["start"], False), ts(c["end"], False), body))
        vtt += [str(i), "%s --> %s%s" % (ts(c["start"], True), ts(c["end"], True), " line:10%" if top else ""), *c["lines"], ""]
    (root / CAPS / "captions.ass").write_text("\n".join(ass) + "\n")
    (root / CAPS / "captions.vtt").write_text("\n".join(vtt))
    fast = [c for c in cues if c["cps"] > MAX_CPS]
    meta = {"recipeKey": doc["recipeKey"], "test": doc["test"], "builtAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "cues": cues, "maxCps": max([c["cps"] for c in cues] or [0]), "fast": len(fast), "lexicon": cap.get("lexicon") or {},
            "transcribedAt": doc["transcribedAt"], "queryId": doc["queryId"]}
    (root / CAPS / "captions.json").write_text(json.dumps(meta, indent=1))
    print("BUILT     %d cues -> captions/captions.ass, captions/captions.vtt  (max %.1f chars/s%s)" % (
        len(cues), meta["maxCps"], ", %d over %g" % (len(fast), MAX_CPS) if fast else ""))
    return meta


def status(root, spec=None):
    """{state: current | stale | missing | none, ...}: stdlib, shared with gate.py and assemble.py."""
    if spec is None:
        spec = json.loads((root / "video.json").read_text())
    rc = recipe(root, spec)
    try:
        meta = json.loads((root / CAPS / "captions.json").read_text())
    except FileNotFoundError:
        return {"state": "none" if not rc["placed"] else "missing", "recipe": rc}
    state = "current" if meta.get("recipeKey") == rc["key"] else "stale"
    return {"state": state, "recipe": rc, "captions": meta}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcribe")
    t.add_argument("--project", required=True)
    t.add_argument("--connection")
    t.add_argument("--stage")
    t.add_argument("--keep-audio", action="store_true", help="leave the uploaded FLAC on the stage")
    t.add_argument("--no-build", action="store_true")
    for name in ("build", "check"):
        sub.add_parser(name).add_argument("--project", required=True)
    a = ap.parse_args()
    root = pathlib.Path(a.project).expanduser().resolve()
    if a.cmd == "transcribe":
        sys.path.insert(0, str(HERE))
        import sf
        try:
            transcribe(root, a.connection, a.stage, a.keep_audio)
        except sf.Refusal as e:
            sf.fail(e)
        if not a.no_build:
            build(root)
    elif a.cmd == "build":
        build(root)
    else:
        st = status(root)
        print("captions  %s%s" % (st["state"], "" if st["state"] != "stale" else ": the narration changed since they were made (re-run transcribe)"))
        sys.exit(0 if st["state"] in ("current", "none") else 1)


if __name__ == "__main__":
    main()

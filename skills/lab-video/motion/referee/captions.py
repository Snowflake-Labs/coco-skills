#!/usr/bin/env python3
"""Captions and delivery referee: known answers and seeded mistakes, with no Snowflake account needed.

    python3 captions.py          # every check must PASS; a few seconds

captions  a word-timed transcript (the shape AI_TRANSCRIBE returns) becomes cues of at most 2 lines of 42 characters,
          broken at sentence ends and pauses, never overlapping, never past the video; the lexicon fixes a spelling;
          .ass and .vtt agree. Seeded: re-recording a line makes the captions stale and the gate FAILs a final; a
          TEST_ONLY voice FAILs a final; a burned cue in a scene whose copy reaches the caption band is flagged
deliver   stage names are validated before they reach SQL (a quote, a semicolon or a missing part is refused);
          link lifetimes outside 1 minute to 7 days are refused; nothing is sent without --confirm; a TEST_ONLY_ file
          is refused without --allow-test; a file probe.py fails is refused before any connection is made
Work happens in a temp directory; nothing is written into the skill.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

MOTION = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOTION / "snowflake"))
sys.path.insert(0, str(MOTION / "audio"))
import captions as C  # noqa: E402
import project as pj  # noqa: E402

results = []


def check(ok, what, detail=""):
    results.append(bool(ok))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", what, ("  " + detail) if detail else ""))


SAY = ["Every number in this video came straight from Snowflake.",
       "These totals are a query, frozen with its SQL and timestamp, and the gate checks them against the lock."]
SPEC = {"title": "captions referee", "fps": 30, "width": 1920, "height": 1080,
        "audio": {"narration": True}, "captions": {"burn": True, "lexicon": {"Coco": "CoCo"}},
        "scenes": [{"id": "a", "src": "scenes/a.html", "dur": 4.5, "say": SAY[0]},
                   {"id": "b", "role": "cta", "src": "scenes/a.html", "dur": 10.0, "say": SAY[1] + " Ask Coco."}]}


def words_for(text, t0, wps=2.6, gap_after_sentence=0.35):
    """Word timings at a true `wps` words a second (each word sounds for 80% of its slot), like AI_TRANSCRIBE's."""
    out, t, slot = [], t0, 1.0 / wps
    for w in text.split():
        out.append({"text": w, "start": round(t, 3), "end": round(t + 0.8 * slot, 3)})
        t += slot + (gap_after_sentence if w.endswith(".") else 0)
    return out


def make(root, takes=("take-001", "take-001"), test=False):
    if root.exists():
        shutil.rmtree(root)
    (root / "scenes").mkdir(parents=True)
    (root / "video.json").write_text(json.dumps(SPEC, indent=1))
    v = root / "audio" / "voice"
    (v / "lines").mkdir(parents=True)
    lines = []
    for s, take in zip(SPEC["scenes"], takes):
        f = "lines/%s-%s.wav" % (s["id"], take)
        (v / f).write_bytes(b"RIFF")
        lines.append({"scene": s["id"], "take": take, "lineHash": pj.line_hash(s["say"]), "file": f, "test": test,
                      "speechSeconds": 3.0, "speechOffset": 0.1, "takeLufs": -20.0})
    (v / "lines.json").write_text(json.dumps({"version": 1, "lines": lines}))
    rc = C.recipe(root, SPEC)
    # Realistic pacing (2.6 words a second), plus one word that starts as the video ends: the mix fades a cut-off
    # line, so a transcript can hold a word the viewer barely hears. It must not become a one-frame caption.
    words = words_for(SAY[0], 0.4) + words_for(SAY[1] + " Ask Coco.", 4.9)
    total = pj.timing(SPEC)["seconds"]
    words.append({"text": "Thanks.", "start": round(total - 0.1, 3), "end": round(total + 0.3, 3)})
    (root / "captions").mkdir()
    (root / "captions" / "transcript.json").write_text(json.dumps(
        {"recipeKey": rc["key"], "test": rc["test"], "lines": rc["lines"], "transcribedAt": "2026-10-15T00:00:00+00:00",
         "queryId": "referee", "words": words, "text": " ".join(w["text"] for w in words)}))
    return rc


def gate(root, *extra):
    p = subprocess.run([sys.executable, str(MOTION / "gate.py"), "--project", str(root), *extra], capture_output=True, text=True)
    return [ln for ln in p.stdout.splitlines() if ln.startswith(("FAIL", "WARN"))]


def caption_checks(tmp):
    root = tmp / "c"
    rc = make(root)
    check(rc["placed"] == 2 and not rc["test"], "the recipe sees both recorded lines")
    meta = C.build(root)
    cues = meta["cues"]
    fits = all(len(c["lines"]) <= C.MAX_LINES and all(len(x) <= C.LINE_CHARS for x in c["lines"]) for c in cues)
    check(fits, "every cue fits 2 lines of 42 characters", str([c["lines"] for c in cues if not fits]))
    check(all(cues[i]["end"] <= cues[i + 1]["start"] for i in range(len(cues) - 1)), "cues never overlap")
    check(cues[-1]["end"] <= pj.timing(SPEC)["seconds"] + 1e-6, "the last cue ends before the video does")
    check(all(c["end"] - c["start"] >= 0.5 for c in cues), "no cue flashes for less than half a second",
          str([(c["text"][:20], round(c["end"] - c["start"], 2)) for c in cues if c["end"] - c["start"] < 0.5]))
    check(meta["maxCps"] <= C.MAX_CPS, "every cue reads at %g characters a second or less (max %s)" % (C.MAX_CPS, meta["maxCps"]))
    check(all(cues[i]["text"].endswith((".", "?", "!")) or cues[i + 1]["start"] - cues[i]["end"] < 1
              for i in range(len(cues) - 1)), "cues break at sentence ends")
    check(any("CoCo." in c["text"] for c in cues) and not any("Coco" in c["text"] for c in cues), "the lexicon fixes Coco to CoCo")
    vtt = (root / "captions" / "captions.vtt").read_text()
    ass = (root / "captions" / "captions.ass").read_text()
    check(vtt.startswith("WEBVTT") and vtt.count(" --> ") == len(cues) and ass.count("Dialogue:") == len(cues),
          "the .vtt and .ass hold the same %d cues" % len(cues))
    check(C.status(root, SPEC)["state"] == "current", "the captions are current")

    lj = root / "audio" / "voice" / "lines.json"
    idx = json.loads(lj.read_text())
    (root / "audio" / "voice" / "lines" / "b-take-002.wav").write_bytes(b"RIFF")
    idx["lines"].append({**idx["lines"][1], "take": "take-002", "file": "lines/b-take-002.wav"})
    lj.write_text(json.dumps(idx))
    check(C.status(root, SPEC)["state"] == "stale", "re-recording a line makes the captions stale")
    out = gate(root, "--final")
    check(any("older recording" in ln and ln.startswith("FAIL") for ln in out), "and gate --final FAILs them")

    make(root, test=True)
    C.build(root)
    out = gate(root, "--final")
    check(any("TEST_ONLY" in ln and "captions" in ln and ln.startswith("FAIL") for ln in out), "captions from a TEST_ONLY voice FAIL a final")

    make(root)
    C.build(root)
    (root / "build").mkdir()
    (root / "build" / "manifest.json").write_text(json.dumps({"scenes": {"a": {"current": "k", "lowestText": 1016, "screenText": ["x"]}}}))
    out = gate(root)
    check(any("into the band where burned captions sit" in ln for ln in out), "copy in the caption band is flagged when captions burn")


def deliver_checks(tmp):
    import sf
    bad = ["x.y.z'; drop table t", 'db.sch."a\'b"', "db.sch", "db.sch.st/sub", "@db..st"]
    refused = []
    for b in bad:
        try:
            sf.stage(b)
        except sf.Refusal:
            refused.append(b)
    check(refused == bad, "stage names with a quote, a semicolon, a path or a missing part are refused", str(set(bad) - set(refused)))
    check(sf.stage("LAB.MEDIA.VIDEOS") == "@LAB.MEDIA.VIDEOS" and sf.stage('@lab.media."My Stage"') == '@lab.media."My Stage"',
          "valid names, quoted included, pass")
    sys.path.insert(0, str(MOTION / "snowflake"))
    import deliver as D
    for e in ("30s", "8d", "x"):
        try:
            D.seconds(e)
            check(False, "a link lifetime of %s is refused" % e)
        except SystemExit:
            check(True, "a link lifetime of %s is refused" % e)
    check(D.seconds("24h") == 86400 and D.seconds("7d") == 604800, "24h and 7d are accepted")

    root = tmp / "d"
    (root / "out").mkdir(parents=True)
    (root / "video.json").write_text(json.dumps({"title": "t", "fps": 30, "scenes": [{"id": "a", "src": "x.html", "dur": 2}]}))
    bad_mp4 = root / "out" / "TEST_ONLY_t.mp4"
    bad_mp4.write_bytes(b"not a video")
    run = lambda *a: subprocess.run([sys.executable, str(MOTION / "snowflake" / "deliver.py"), *a], capture_output=True, text=True)
    p = run("stage", "--project", str(root), "--stage", "A.B.C")
    check(p.returncode != 0 and "Pass --confirm" in p.stdout + p.stderr, "nothing is sent without --confirm")
    p = run("stage", "--project", str(root), "--stage", "A.B.C", "--confirm")
    check(p.returncode != 0 and "TEST_ONLY" in p.stdout + p.stderr, "a TEST_ONLY_ file is refused without --allow-test")
    p = run("stage", "--project", str(root), "--stage", "A.B.C", "--confirm", "--allow-test")
    check(p.returncode != 0 and "probe.py fails" in p.stdout + p.stderr, "a file probe.py fails is refused before any connection",
          (p.stdout + p.stderr).strip()[-120:])
    p = run("workspace", "--project", str(root), "--to", "A.B.C:/no-trailing-slash", "--confirm", "--allow-test")
    check(p.returncode != 0 and "must be a Workspace folder" in p.stdout + p.stderr, "a Workspace destination must be a folder")


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="lv-cap-referee-"))
    try:
        caption_checks(tmp)
        deliver_checks(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("---\n%d/%d captions and delivery checks passed" % (sum(results), len(results)))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

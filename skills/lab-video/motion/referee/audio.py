#!/usr/bin/env python3
"""Audio referee: the sound pipeline against known answers, and seeded mistakes that must be caught.

    python3 referee/audio.py              # every check must PASS (about a minute; needs the music library)
    python3 referee/audio.py --quick      # skip the music-library calibration

Known answers:
  analyzer     "Something Elated" against the Oct 8 hand analysis (88 BPM, bar grid, groove stops on bar 84)
  splitter     a synthetic take whose word onsets are known to the sample: each line must be assigned to its
               scene and its first word found within 30 ms; stray speech before the first cue is left out
  keys         editing narration ("say", "voiceAt") must not re-key any picture
  mix          narrated -16 LUFS and music-only -22 LUFS on the decoded AAC, a plain sum, picture untouched
Seeded mistakes (each must be refused or reported, or the referee FAILS):
  a NonCommercial track, a line changed after recording, a line too long for its scene, a CC BY bed with no
  credit on screen, a TEST_ONLY voice in a final, and amix's default normalisation (the -18 dB bug of
  patterns/audio.md, which the plain-sum check must notice).
Work happens in a temp directory; nothing is written into the skill or a real project.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import numpy as np

MOTION = pathlib.Path(__file__).resolve().parents[1]
AUDIO = MOTION / "audio"
sys.path.insert(0, str(AUDIO))
import media as mt  # noqa: E402
import project as pj  # noqa: E402
import voice as vc  # noqa: E402

RATE = mt.RATE
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + str(detail)) if detail else ""))


def tone_word(seconds, f0, rng):
    """A voiced 'word': harmonics on a slowly moving pitch, with a syllable envelope. Loud enough to be speech."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    pitch = f0 * (1 + 0.06 * np.sin(2 * np.pi * 3.1 * t + rng.uniform(0, 6)))
    phase = 2 * np.pi * np.cumsum(pitch) / RATE
    sig = sum(np.sin(k * phase) / k for k in range(1, 6))
    env = np.clip(np.sin(np.pi * np.minimum(t / seconds, 1)) * 1.6, 0, 1) * (0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 4 * t)))
    return (0.18 * sig * env).astype(np.float32)


def make_project(work):
    """Four code scenes (no browser needed for audio checks: the picture is a solid-colour file)."""
    root = work / "proj"
    (root / "scenes").mkdir(parents=True)
    (root / "scenes" / "blank.html").write_text("<!doctype html><title>blank</title>")
    spec = {"title": "audio referee", "fps": 30, "width": 640, "height": 360,
            "audio": {"music": {"track": "something_elated", "edit": "ending", "land": "cta"}, "narration": True},
            "scenes": [{"id": "one", "src": "scenes/blank.html", "dur": 3.0, "say": "The first line is short."},
                       {"id": "two", "src": "scenes/blank.html", "dur": 4.5, "say": "A second line, a little longer than the first."},
                       {"id": "quiet", "src": "scenes/blank.html", "dur": 2.0},
                       {"id": "cta", "src": "scenes/blank.html", "dur": 4.0, "role": "cta", "showsCredit": True, "say": "Come on Monday."}]}
    (root / "video.json").write_text(json.dumps(spec, indent=2))
    return root, spec


def synthetic_take(root, spec, rng, stray=True):
    """A take with known first-word times: each line starts 0.42 s after its cue."""
    sc = pj.script(spec)
    total = int((sc["duration"] + 1.5) * RATE)
    take = (rng.standard_normal(total) * 10 ** (-60 / 20)).astype(np.float32)
    truth, markers = {}, []
    if stray:  # a cough before the first cue: must be left out, not glued onto line one
        take[int(0.05 * RATE):int(0.05 * RATE) + int(0.25 * RATE)] += tone_word(0.25, 140, rng)
    for s in sc["scenes"]:
        cue = s["start"] + 0.30
        markers.append({"scene": s["id"], "lineHash": s["lineHash"], "text": s["say"], "audioSeconds": round(cue, 3)})
        if not s["say"]:
            continue
        at = cue + 0.42
        pos = at
        for w in s["say"].split():
            d = 0.12 + 0.045 * len(w)
            a = int(pos * RATE)
            take[a:a + int(d * RATE)] += tone_word(d, rng.uniform(110, 170), rng)
            pos += d + 0.08
        truth[s["id"]] = {"first": at, "last": pos - 0.08}
    _, takes, _, _ = pj.voice_paths(root)
    takes.mkdir(parents=True, exist_ok=True)
    tid = vc.next_take_id(root)
    mt.write_wav(take, takes / (tid + ".wav"), codec="pcm_f32le")
    (takes / (tid + ".json")).write_text(json.dumps({"version": 3, "mode": "full", "test": False, "audioSeconds": round(total / RATE, 3),
                                                     "markers": markers, "scriptHash": sc["scriptHash"]}))
    return tid, truth


def picture(root, seconds, work):
    pic = work / "picture.mp4"
    mt.run(["-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x0A6A37:s=640x360:r=30", "-t", "%.3f" % seconds,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", pic])
    return pic


def fake_credit(root, text):
    """What render.mjs would record if the end card showed this text (the audio checks run without a browser)."""
    (root / "build").mkdir(exist_ok=True)
    (root / "build" / "manifest.json").write_text(json.dumps({"scenes": {"cta": {"screenText": [text]}}}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    rng = np.random.default_rng(11)
    import library
    have_lib = (pathlib.Path(library.library_dir()) / "something_elated" / "track.json").exists()
    if not have_lib:
        print("FAIL  the music library has no something_elated: python3 audio/library.py seed")
        sys.exit(1)

    # ---- analyzer calibration ------------------------------------------------------------------------------
    if not a.quick:
        rec = library.load("something_elated")
        r = subprocess.run([sys.executable, str(AUDIO / "analyze.py"), rec["audioPath"], "--calibrate"], capture_output=True, text=True)
        oks = [l for l in r.stdout.splitlines() if l.startswith(("ok", "FAIL"))]
        check("analyzer matches the Oct 8 hand analysis of Something Elated", r.returncode == 0, "; ".join(o[6:] for o in oks if o.startswith("FAIL")) or "%d checks" % len(oks))

    # ---- licence gate -------------------------------------------------------------------------------------
    nc = {"licenceUrls": ["https://creativecommons.org/licenses/by-nc/3.0/"]}
    try:
        library.licence_verdict(nc)
        check("a NonCommercial track is refused", False)
    except library.Refused as e:
        check("a NonCommercial track is refused", "NonCommercial" in str(e), e)
    for url, ok in (("https://creativecommons.org/licenses/by-nd/4.0/", False), ("https://creativecommons.org/licenses/by-sa/4.0/", False),
                    ("https://creativecommons.org/licenses/by/4.0/", True), ("https://creativecommons.org/publicdomain/zero/1.0/", True)):
        try:
            library.licence_verdict({"licenceUrls": [url]})
            got = True
        except library.Refused:
            got = False
        check("licence %s is %s" % (url.split("org/")[1], "allowed" if ok else "refused"), got == ok)

    work = pathlib.Path(tempfile.mkdtemp(prefix="motion-audio-ref-"))
    try:
        root, spec = make_project(work)

        # ---- keys: narration never re-keys a picture --------------------------------------------------------
        def keys():
            out = subprocess.run(["node", "-e", "import('%s').then(m=>{const p=m.loadProject('%s');console.log(JSON.stringify(p.scenes.map(s=>m.sceneKey(p,s,{renderer:'gpu',proxy:false}))))})"
                                  % (MOTION / "lib" / "project.mjs", root)], capture_output=True, text=True)
            return json.loads(out.stdout)
        k0 = keys()
        s2 = json.loads((root / "video.json").read_text())
        s2["scenes"][0]["say"] = "A different first line."
        s2["scenes"][1]["voiceAt"] = 0.6
        (root / "video.json").write_text(json.dumps(s2, indent=2))
        k1 = keys()
        (root / "video.json").write_text(json.dumps(spec, indent=2))
        check("editing narration re-keys no picture", k0 == k1 and len(k0) == 4)
        s3 = json.loads((root / "video.json").read_text())
        s3["audio"]["music"]["track"] = "night_owl"
        (root / "video.json").write_text(json.dumps(s3, indent=2))
        k2 = keys()
        (root / "video.json").write_text(json.dumps(spec, indent=2))
        changed = [i for i in range(4) if k0[i] != k2[i]]
        check("changing the track re-keys only the scene that shows the credit", changed == [3], changed)

        # ---- splitter against known word times --------------------------------------------------------------
        tid, truth = synthetic_take(root, spec, rng)
        summary = vc.ingest(root, tid, quiet=True)
        got = {l["scene"]: l for l in summary["lines"]}
        check("every line assigned to its own scene", sorted(got) == sorted(truth), sorted(got))
        index = {x["scene"]: x for x in pj.read_lines(root)["lines"]}
        errs = {sid: round((index[sid]["source"]["firstWord"] - truth[sid]["first"]) * 1000) for sid in truth if sid in index}
        check("first word found within 30 ms of the truth", errs and all(abs(e) <= 30 for e in errs.values()), "ms: %s" % errs)
        check("a cough before the first cue is left out, not glued onto the line", any(abs(s_["at"] - 0.05) < 0.06 for s_ in summary["stray"]), summary["stray"])
        st = pj.line_status(root, spec)
        check("all lines fit", st["summary"]["ok"] == 3, st["summary"])

        # ---- the mix: narrated -----------------------------------------------------------------------------
        import mix as mx
        fake_credit(root, 'Music: "Something Elated" by Broke For Free · CC BY 3.0 US')
        pic = picture(root, pj.timing(spec)["seconds"], work)
        r = mx.render(root, pic, work / "narrated.mp4", final=True)
        check("narrated final lands at -16 LUFS (decoded AAC)", abs(r["final"]["integrated_lufs"] + 16) <= 0.7, r["final"])
        check("true peak at or below -1.5 dBTP", r["final"]["true_peak_dbtp"] <= -1.5, r["final"]["true_peak_dbtp"])
        check("picture stream copied byte for byte", r["checks"]["pictureStreamUnchanged"] and r["checks"]["videoFramesUnchanged"])
        check("the mix is a plain sum", r["sum"]["plainSum"])
        gaps = [g["medianVoiceAboveMusicDb"] for g in r.get("sceneGaps", []) if g["medianVoiceAboveMusicDb"] is not None]
        check("voice sits 12-25 dB over the music where it speaks", gaps and all(12 <= g <= 25 for g in gaps[:2]), gaps)
        meta = mt.run(["-hide_banner", "-i", work / "narrated.mp4"], check=False).stderr.decode(errors="replace")
        check("the CC BY attribution travels in the file", "Something Elated" in meta and "creativecommons.org" in meta)

        # ---- seeded: amix's default normalisation would fail the plain-sum check -----------------------------
        folder = root / r["stems"]
        bad = work / "amix_default.wav"
        mt.run(["-v", "error", "-y", "-i", folder / "voice.wav", "-i", folder / "music.wav", "-filter_complex",
                "[0:a][1:a]amix=inputs=2:duration=longest", "-c:a", "pcm_f32le", bad])
        good = mt.decode(folder / "mix.wav")
        worse = mt.decode(bad)
        drop = mt.rms(worse) - mt.rms(good)
        check("amix without normalize=0 is caught (the -6 dB at 2 inputs that became -18 dB at 8)", drop < -5.0, "%.1f dB" % drop)

        # ---- seeded: TEST voice in a final -----------------------------------------------------------------
        lines = pj.read_lines(root)
        for x in lines["lines"]:
            x["test"] = True
        (pj.voice_paths(root)[3]).write_text(json.dumps(lines))
        try:
            mx.render(root, pic, work / "test.mp4", final=True)
            check("a TEST_ONLY voice is refused in a final", False)
        except mx.Refusal as e:
            check("a TEST_ONLY voice is refused in a final", "TEST_ONLY" in str(e))
        r = mx.render(root, pic, work / "test.mp4", final=True, allow_test=True)
        check("--allow-test-voice writes a TEST_ONLY_ file", pathlib.Path(r["output"]).name.startswith("TEST_ONLY_"), r["output"])
        for x in lines["lines"]:
            x["test"] = False
        (pj.voice_paths(root)[3]).write_text(json.dumps(lines))

        # ---- seeded: a line changed after recording ------------------------------------------------------------
        s4 = json.loads((root / "video.json").read_text())
        s4["scenes"][1]["say"] = "A second line that changed after it was recorded."
        (root / "video.json").write_text(json.dumps(s4, indent=2))
        st = pj.line_status(root, s4)
        check("a changed line is stale, not played", next(r_ for r_ in st["scenes"] if r_["scene"] == "two")["status"] == "stale")
        try:
            mx.render(root, pic, work / "stale.mp4", final=True)
            check("a stale line is refused in a final", False)
        except mx.Refusal:
            check("a stale line is refused in a final", True)
        (root / "video.json").write_text(json.dumps(spec, indent=2))

        # ---- seeded: a scene too short for its recorded line -----------------------------------------------
        s5 = json.loads((root / "video.json").read_text())
        s5["scenes"][1]["dur"] = 2.0
        (root / "video.json").write_text(json.dumps(s5, indent=2))
        st = pj.line_status(root, s5)
        row = next(r_ for r_ in st["scenes"] if r_["scene"] == "two")
        check("a line longer than its scene is reported, with the length that fits", row["status"] == "long" and "lengthen this scene" in row["message"], row.get("message"))
        g = subprocess.run([sys.executable, str(MOTION / "gate.py"), "--project", str(root), "--final"], capture_output=True, text=True)
        check("gate --final fails it", g.returncode == 1 and "two: the recorded line" in g.stdout)
        (root / "video.json").write_text(json.dumps(spec, indent=2))

        # ---- seeded: CC BY with no credit on screen ---------------------------------------------------------
        fake_credit(root, "Bring your laptop.")
        try:
            mx.render(root, pic, work / "nocredit.mp4", final=True)
            check("a CC BY bed without its on-screen credit is refused", False)
        except mx.Refusal as e:
            check("a CC BY bed without its on-screen credit is refused", "credit" in str(e))

        # ---- music only --------------------------------------------------------------------------------------
        fake_credit(root, 'Music: "Something Elated" by Broke For Free · CC BY 3.0 US')
        r = mx.render(root, pic, work / "music_only.mp4", final=True, music_only=True)
        check("music-only final lands at -22 LUFS", abs(r["final"]["integrated_lufs"] + 22) <= 0.7 and not r["placed"], r["final"])
        check("a music-only bed keeps the whole track (no speech-band EQ)", r["music"]["edit"]["eq"] is None)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("---\n%d/%d audio checks passed" % (sum(results), len(results)))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

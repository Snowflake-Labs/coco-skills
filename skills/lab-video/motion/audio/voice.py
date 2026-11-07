#!/usr/bin/env python3
"""voice.py: narration takes in, one clip per scene line out. Analysis, not a listening review.

    python3 voice.py ingest  --project P --take take-003        split a saved Voice Studio take into lines
    python3 voice.py status  --project P [--json]               every line: recorded? current? does it fit?
    python3 voice.py import  --project P FILE [--cues CUES.json] a take recorded elsewhere (Voice Memos, old Studio)
    python3 voice.py testvoice --project P [--voice Samantha]   TEST_ONLY: a synthetic take, to test the pipeline

Where things live (source material, never under build/):
  audio/voice/takes/take-NNN.(webm|m4a|wav)   the recording exactly as captured, and take-NNN.json, its cues
  audio/voice/lines/<scene>--take-NNN.wav     one line, trimmed to its speech plus a short pad (48 kHz mono)
  audio/voice/lines.json                      the index: scene, the text it was read from (lineHash), level

Why lines and not one take: the Oct 8 mixer placed one continuous take at t=0, so any change to a scene's
length meant re-recording all of it. Here each line is placed at its own scene's start (plus "voiceAt"), so
picture edits never invalidate a recording, and changing one line's words needs only that line re-read.

A take is split by its cue markers (the moment each line appeared on the prompter). Speech is found from the
recording itself (50 ms windows 10 dB above the take's quiet floor, runs of 0.1 s, joined across gaps under
0.3 s), and each phrase belongs to the line on screen when it began (allowing 0.35 s of anticipation). Nothing
is ever sped up, slowed or retimed: a line that does not fit its scene is reported, not squeezed.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import media as mt  # noqa: E402
import project as pj  # noqa: E402

RATE = mt.RATE
WINDOW = 0.05
SPEECH_OVER_FLOOR, ABS_FLOOR = 10.0, -62.0
MIN_RUN, JOIN_GAP = 0.10, 0.30
EARLY = 0.35
STRAY_MAX, STRAY_GAP = 0.45, 0.35   # a pre-cue fragment this short, this far from the line, is noise
PAD_BEFORE, PAD_AFTER, EDGE_FADE = 0.12, 0.25, 0.010
HIGHPASS = "highpass=f=75:poles=2"
FORMATS = (".webm", ".m4a", ".mp4", ".mp3", ".wav", ".ogg", ".aiff", ".aif")
NOTE = "Analysis and level checks, not a listening review: the words themselves still need a listen."


class Refusal(Exception):
    """A take that must not be used, with a message for the person who recorded it."""


paths, read_index = pj.voice_paths, pj.read_lines


def write_index(root, index):
    _, _, _, idx = paths(root)
    mt.write_json(idx, index)


def next_take_id(root):
    _, takes, _, _ = paths(root)
    takes.mkdir(parents=True, exist_ok=True)
    nums = [int(m[1]) for p in takes.glob("take-*.json") for m in [re.match(r"take-(\d+)\.json$", p.name)] if m]
    return "take-%03d" % (max(nums, default=0) + 1)


# ---- speech -------------------------------------------------------------------------------------------
def phrases(voice):
    """[(start_s, end_s)] of speech in a mono signal, and the floor/threshold used."""
    win = int(round(WINDOW * RATE))
    levels = mt.rms_db(voice, win)
    floor = float(np.percentile(levels, 15))
    threshold = max(floor + SPEECH_OVER_FLOOR, ABS_FLOOR)
    active = levels > threshold
    runs, start = [], None
    for i, flag in enumerate(list(active) + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if (i - start) * WINDOW >= MIN_RUN:
                runs.append([start * WINDOW, i * WINDOW])
            start = None
    joined = []
    for r in runs:
        if joined and r[0] - joined[-1][1] < JOIN_GAP:
            joined[-1][1] = r[1]
        else:
            joined.append(r)
    return [(round(a, 3), round(b, 3)) for a, b in joined], {"floorDbfs": round(floor, 1), "thresholdDbfs": round(threshold, 1)}


def decode_mono(path):
    take = mt.decode(path)
    left, right = mt.rms(take[:, 0]), mt.rms(take[:, 1])
    # A one-sided interface (mic on input 1 only) would halve the voice if averaged: take the live side.
    mono = take[:, 0] if left - right > 20 else take[:, 1] if right - left > 20 else take.mean(axis=1)
    return mt.filter_samples(mono.astype(np.float32), HIGHPASS), {"leftRmsDb": left, "rightRmsDb": right}


def edge_fade(clip):
    n = min(len(clip) // 2, int(round(EDGE_FADE * RATE)))
    if n > 1:
        ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)
        clip[:n] *= ramp
        clip[-n:] *= ramp[::-1]
    return clip


# ---- ingest ---------------------------------------------------------------------------------------------
def validate_cues(cues, audio_seconds):
    if not isinstance(cues, dict) or cues.get("version") != 3:
        raise Refusal("this is not a motion-mode Voice Studio cue file (version 3)")
    if cues.get("mode") not in ("full", "line"):
        raise Refusal("cue mode must be full or line")
    if not isinstance(cues.get("markers"), list) or not cues["markers"]:
        raise Refusal("the cue file has no markers: no line was on the prompter while recording")
    a = cues.get("audioSeconds")
    if not isinstance(a, (int, float)) or abs(a - audio_seconds) > 0.35:
        raise Refusal("the cue file says the take lasts %s s but the recording decodes to %.2f s; use the recording "
                      "and cue file from the same take" % (a, audio_seconds))
    for m in cues["markers"]:
        if not isinstance(m, dict) or not isinstance(m.get("audioSeconds"), (int, float)) or not m.get("scene"):
            raise Refusal("a cue marker is missing its scene or time")


def ingest(root, take_id, quiet=False):
    root = pathlib.Path(root)
    _, takes, lines_dir, _ = paths(root)
    meta_file = takes / (take_id + ".json")
    if not meta_file.exists():
        raise Refusal("no %s in %s" % (meta_file.name, takes))
    cues = json.loads(meta_file.read_text())
    audio = next((p for p in sorted(takes.glob(take_id + ".*")) if p.suffix.lower() in FORMATS), None)
    if not audio:
        raise Refusal("no recording for %s" % take_id)
    voice, channels = decode_mono(audio)
    seconds = len(voice) / RATE
    if seconds < 0.5:
        raise Refusal("the recording holds less than half a second of audio")
    validate_cues(cues, seconds)
    found, levels = phrases(voice)
    if not found:
        raise Refusal("no speech detected in %s (floor %s dBFS). Check the microphone and its level." % (take_id, levels["floorDbfs"]))
    markers = sorted(cues["markers"], key=lambda m: m["audioSeconds"])
    spans = {}   # scene -> [start, end] of its speech in the take
    stray = []
    for k, m in enumerate(markers):
        lo = m["audioSeconds"] - EARLY
        hi = markers[k + 1]["audioSeconds"] - EARLY if k + 1 < len(markers) else seconds + 1
        mine = [p for p in found if lo <= p[0] < hi]
        # A short fragment that starts before the cue and is followed by a real pause is a cough, a click or a
        # breath, not an early first word: speech that anticipates a cue runs straight into the line.
        while len(mine) > 1 and mine[0][0] < m["audioSeconds"] and mine[0][1] - mine[0][0] < STRAY_MAX \
                and mine[1][0] - mine[0][1] >= STRAY_GAP:
            stray.append({"scene": m["scene"], "at": mine[0][0]})
            mine = mine[1:]
        if not mine:
            continue
        if not m.get("lineHash"):
            stray += [{"scene": m["scene"], "at": p[0]} for p in mine]
            continue
        spans[m["scene"]] = [mine[0][0], mine[-1][1], m]
    before = [p for p in found if p[0] < markers[0]["audioSeconds"] - EARLY]
    if before:
        stray.append({"scene": "(before the first line)", "at": before[0][0]})
    order = sorted(spans.items(), key=lambda kv: kv[1][0])
    lines_dir.mkdir(parents=True, exist_ok=True)
    made, clips = [], []
    for i, (scene, (a, b, m)) in enumerate(order):
        lo = a - PAD_BEFORE
        hi = b + PAD_AFTER
        if i > 0:
            lo = max(lo, order[i - 1][1][1] + 0.02)
        if i + 1 < len(order):
            hi = min(hi, order[i + 1][1][0] - 0.02)
        lo, hi = max(0.0, lo), min(seconds, hi)
        clip = edge_fade(voice[int(round(lo * RATE)):int(round(hi * RATE))].copy())
        name = "%s--%s.wav" % (scene, take_id)
        mt.write_wav(clip, lines_dir / name, codec="pcm_f32le")
        clips.append(clip)
        made.append({"id": "%s--%s" % (scene, take_id), "scene": scene, "lineHash": m["lineHash"], "text": m.get("text", ""),
                     "take": take_id, "test": bool(cues.get("test")), "file": "lines/" + name,
                     "seconds": round(len(clip) / RATE, 3), "speechOffset": round(a - lo, 3), "speechSeconds": round(b - a, 3),
                     "source": {"start": round(lo, 3), "end": round(hi, 3), "firstWord": a, "lastWord": b,
                                "cue": round(m["audioSeconds"], 3), "reaction": round(a - m["audioSeconds"], 3)},
                     "createdAt": cues.get("createdAt") or datetime.datetime.now().isoformat(timespec="seconds")})
    if not made:
        raise Refusal("speech was found, but none of it began while a line was on the prompter")
    joined = np.concatenate(clips)
    take_lufs = mt.sample_loudness(np.repeat(joined[:, None], 2, axis=1))["integrated_lufs"]
    if take_lufs is None or take_lufs < -60:
        raise Refusal("the voice is too quiet to use (%s LUFS). Move closer to the microphone or raise its input level." % take_lufs)
    peak = float(20 * np.log10(max(np.abs(joined).max(), 1e-9)))
    for row in made:
        row["takeLufs"] = take_lufs
    index = read_index(root)
    index["lines"] = [x for x in index["lines"] if x["take"] != take_id] + made
    write_index(root, index)
    summary = {"take": take_id, "mode": cues["mode"], "test": bool(cues.get("test")), "seconds": round(seconds, 2),
               "lines": [{"scene": r["scene"], "speechSeconds": r["speechSeconds"], "reaction": r["source"]["reaction"]} for r in made],
               "takeLufs": take_lufs, "samplePeakDbfs": round(peak, 1), "clipped": peak > -0.3, "speech": levels, "channels": channels,
               "stray": stray, "note": NOTE}
    mt.write_json(takes / (take_id + ".ingest.json"), summary)
    if not quiet:
        print("take      %s: %d line%s, %.1f LUFS speech%s%s" % (take_id, len(made), "" if len(made) == 1 else "s", take_lufs,
              ", CLIPPED (peak %.1f dBFS): record further from the microphone" % peak if peak > -0.3 else "",
              "".join("\n          stray speech at %.1f s during %s, left out" % (s["at"], s["scene"]) for s in stray)))
    return summary


# ---- status ---------------------------------------------------------------------------------------------
status = pj.line_status  # stdlib-only, so gate.py can use it without numpy



# ---- takes recorded elsewhere -----------------------------------------------------------------------------
def import_take(root, src, cues_file=None):
    """Copy a take into the project. With a cue file it is used as-is; without one, each phrase is assigned to the
    scene whose speech start is nearest before it (the take must start at video time 0, like the old Studio)."""
    root = pathlib.Path(root)
    _, spec = pj.load(root)
    src = pathlib.Path(src).expanduser().resolve()
    if src.suffix.lower() not in FORMATS:
        raise Refusal("%s is not an audio file this can read" % src.name)
    take_id = next_take_id(root)
    _, takes, _, _ = paths(root)
    shutil.copy2(src, takes / (take_id + src.suffix.lower()))
    voice, _ = decode_mono(takes / (take_id + src.suffix.lower()))
    seconds = len(voice) / RATE
    if cues_file:
        cues = json.loads(pathlib.Path(cues_file).read_text())
    else:
        sc = pj.script(spec)
        cues = {"version": 3, "mode": "full", "imported": src.name, "audioSeconds": seconds,
                "markers": [{"scene": s["id"], "lineHash": s["lineHash"], "text": s["say"], "audioSeconds": s["start"]}
                            for s in sc["scenes"]], "scriptHash": sc["scriptHash"],
                "createdAt": datetime.datetime.now().isoformat(timespec="seconds"),
                "note": "imported without cues: assumes the recording starts at video time 0"}
    cues["audioSeconds"] = cues.get("audioSeconds", seconds)
    mt.write_json(takes / (take_id + ".json"), cues)
    return ingest(root, take_id)


# ---- TEST_ONLY synthetic takes --------------------------------------------------------------------------
def say_voices():
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    return [re.split(r"\s{2,}", line.strip())[0] for line in out.splitlines() if line.strip()]


def tts(text, voice, rate=None):
    """macOS `say` to float mono at 48 kHz. TEST_ONLY: Apple's licence restricts system voices to personal,
    non-commercial use (patterns/audio.md), so these lines are flagged test and the final mix refuses them."""
    if voice not in say_voices():
        raise Refusal("the voice %r is not installed (say -v ? lists them); `say` would silently use another" % voice)
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "line.aiff"
        cmd = ["say", "-v", voice, "-o", str(f)] + (["-r", str(rate)] if rate else []) + [text]
        subprocess.run(cmd, check=True)
        return mt.decode(f, channels=1)[:, 0]


def testvoice(root, voice="Samantha", rate=None, jitter=0.12, seed=7, only=None):
    """Synthesise a full take the way a person would read it: each line starts a little after its cue."""
    root = pathlib.Path(root)
    _, spec = pj.load(root)
    sc = pj.script(spec)
    rng = np.random.default_rng(seed)
    total = int(round((sc["duration"] + 1.5) * RATE))
    take = (rng.standard_normal(total) * 10 ** (-62 / 20)).astype(np.float32)   # a quiet room, not digital silence
    markers = []
    for s in sc["scenes"]:
        markers.append({"scene": s["id"], "lineHash": s["lineHash"], "text": s["say"], "audioSeconds": round(s["start"], 3),
                        "scriptSeconds": round(s["start"], 3)})
        if not s["say"] or (only and s["id"] not in only):
            continue
        clip = tts(s["say"], voice, rate) * 10 ** (-6 / 20)
        lead = max(0.05, s["lead"] + float(rng.uniform(-jitter, jitter)))
        at = int(round((s["start"] + lead) * RATE))
        take[at:at + len(clip)] += clip[:max(0, total - at)]
    take_id = next_take_id(root)
    _, takes, _, _ = paths(root)
    mt.write_wav(take, takes / (take_id + ".wav"), codec="pcm_f32le")
    mt.write_json(takes / (take_id + ".json"), {"version": 3, "mode": "full", "test": True, "testVoice": voice,
                  "note": "TEST_ONLY synthetic take (macOS say). Never ship it: the final mix refuses test lines.",
                  "audioSeconds": round(total / RATE, 3), "markers": markers, "scriptHash": sc["scriptHash"],
                  "createdAt": datetime.datetime.now().isoformat(timespec="seconds")})
    return ingest(root, take_id)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "status", "import", "testvoice"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        if name == "ingest":
            p.add_argument("--take", required=True)
        if name == "status":
            p.add_argument("--json", action="store_true")
        if name == "import":
            p.add_argument("file")
            p.add_argument("--cues")
        if name == "testvoice":
            p.add_argument("--voice", default="Samantha")
            p.add_argument("--rate", type=int)
    a = ap.parse_args()
    root = pathlib.Path(a.project).expanduser().resolve()
    try:
        if a.cmd == "ingest":
            print(json.dumps(ingest(root, a.take, quiet=True), indent=2))
        elif a.cmd == "import":
            import_take(root, a.file, a.cues)
        elif a.cmd == "testvoice":
            testvoice(root, a.voice, a.rate)
        elif a.cmd == "status":
            st = status(root)
            if a.json:
                print(json.dumps(st, indent=2))
                return
            for r in st["scenes"]:
                if r["status"] == "none":
                    continue
                tag = {"ok": "ok   ", "long": "LONG ", "missing": "todo ", "stale": "STALE"}[r["status"]]
                extra = (" %s, %.2f s of %.2f s room" % (r["take"], r["speechSeconds"], r["room"])) if r.get("take") and r["status"] in ("ok", "long") else ""
                print("%s %-10s %s%s%s" % (tag, r["scene"], r["say"][:60], extra, ("  <- " + r["message"]) if r.get("message") else ""))
            s = st["summary"]
            print("---\n%d lines: %d ok, %d long, %d not recorded, %d changed since recording%s"
                  % (s["lines"], s["ok"], s["long"], s["missing"], s["stale"], "  (TEST voice present)" if s["test"] else ""))
    except Refusal as r:
        sys.exit("Refused: %s" % r)


if __name__ == "__main__":
    main()

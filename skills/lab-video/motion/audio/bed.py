#!/usr/bin/env python3
"""bed.py: cut a project's music bed from a library track. Analysis, not a listening review.

    python3 bed.py --project P            # writes build/audio/bed-<key>.wav + .json, prints where the music lands

video.json "audio.music" (all optional except track):
  {"track": "something_elated",          a library id (library.py list)
   "edit": "ending",                     ending: back-time so the track's final hit lands on the cut ("land")
                                         intro:  from the track's first sound, faded out at the end
                                         a number: start that many seconds into the track
   "land": "cta",                        the cut the final hit lands on: "cta", a scene id, or seconds
   "landOffset": 0.0,                    nudge the landing (s); negative lands it before the cut
   "inBar": 62,                          intro/number edits: start on this bar (the strongest onset near it)
   "fadeIn": 0.35, "fadeOut": 1.2,       raised-cosine fades (s); the last sample is exactly zero
   "gainDb": 0}                          the reviewer's music level, applied in mix.py, not baked in here

An "ending" edit is how a 30 s highlight landed its end card on the track's closing hit. A track
with no final hit (one that only fades) cannot be back-timed; the bed then falls back to "intro" and says so.
A narrated video's bed gets the speech-band EQ an earlier narrated bed used (85 Hz highpass, -2.5 dB at 2.3 kHz).
"""
import argparse
import hashlib
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import media as mt  # noqa: E402
import library  # noqa: E402
import project as pj  # noqa: E402
from analyze import strongest_onset  # noqa: E402

RATE = mt.RATE
EQ_NARRATED = "highpass=f=85:poles=2,equalizer=f=2300:t=o:w=1.7:g=-2.5"
PREROLL = 1.0           # filter warm-up either side of the excerpt, so EQ has settled at the cut points
BEFORE_ONSET = 0.015    # an in-point sits 15 ms before its onset (build_bed.py)
NOTE = "Analysis and level checks, not a listening review. The assistant cannot listen."
VERSION = "bed-1"


def fade(count, fade_in, fade_out):
    g = np.ones(count)
    rise = max(1, int(round(fade_in * RATE)))
    g[:rise] = 0.5 - 0.5 * np.cos(np.pi * np.arange(rise) / rise)
    fall = max(2, int(round(fade_out * RATE)))
    g[count - fall:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(fall) / (fall - 1))
    return g


def plan(spec, rec, t, narrated):
    """Decide the edit: where in the track the video's t=0 sits, and what lands where."""
    m = spec["audio"]["music"] if isinstance(spec["audio"].get("music"), dict) else {"track": spec["audio"]["music"]}
    an = rec["analysis"]
    tempo, hit = an["tempo"], an["finalHit"]
    bar = tempo["barSeconds"]
    edit = m.get("edit", "ending" if hit else "intro")
    notes = []
    if edit == "ending" and not hit:
        notes.append('"%s" has no final hit (it fades), so it cannot be back-timed; used the intro edit' % rec["title"])
        edit = "intro"
    duration = t["seconds"]
    land = None
    if edit == "ending":
        land = pj.land_seconds(spec, m, t)
        start = hit["seconds"] - land
        if start < 0:
            raise SystemExit('Refused: landing the final hit of "%s" at %.2f s needs %.2f s of music before it, and the '
                             "track has only %.2f s. Land it earlier, pick another track, or use \"edit\": \"intro\"."
                             % (rec["title"], land, land, hit["seconds"]))
        why = "back-timed: the final hit (bar %s, %.3f s into the track) lands at %.3f s" % (an["finalHitBar"], hit["seconds"], land)
    else:
        if isinstance(edit, (int, float)):
            start = float(edit)
        elif "inBar" in m:
            start = tempo["bar0Seconds"] + int(m["inBar"]) * bar
        else:
            start = max(0.0, an["firstSound"] - BEFORE_ONSET)
        refined = None
        if "inBar" in m or isinstance(edit, (int, float)):
            refined, _ = strongest_onset(mt.decode(rec["audioPath"]).mean(axis=1), start, 0.060)
            if refined is not None:
                start = refined - BEFORE_ONSET
        why = "from %.3f s into the track%s" % (start, " (15 ms before the strongest onset near bar %s)" % m["inBar"] if "inBar" in m and refined else "")
        edit = "intro" if not isinstance(edit, (int, float)) else "in"
    if start + duration > an["decodedSeconds"] + 30:
        raise SystemExit('Refused: the video runs %.1f s but "%s" has %.1f s left after the in-point. Pick a longer '
                         "track or an earlier in-point." % (duration, rec["title"], an["decodedSeconds"] - start))
    fade_in = float(m.get("fadeIn", 0.35 if start > 0.05 else 0.012))
    ring_left = an["ringEnd"] - start  # where the track itself goes quiet, on the video's clock
    default_out = 1.2 if ring_left > duration - 0.05 else 0.04
    fade_out = float(m.get("fadeOut", default_out))
    if ring_left < duration - 0.05:
        notes.append("the track rings out at %.2f s, %.2f s before the video ends; the last frames are silent" % (ring_left, duration - ring_left))
    if edit == "ending" and land is not None and land > duration - 0.3:
        notes.append("the final hit lands %.2f s before the end; there is no room for the ring-out" % (duration - land))
    return {"edit": edit, "start": round(start, 5), "land": land, "fadeIn": fade_in, "fadeOut": fade_out,
            "eq": EQ_NARRATED if narrated else None, "why": why, "notes": notes, "gainDb": float(m.get("gainDb", 0.0))}


def events(rec, p, t):
    """What the music does under each scene, on the video's clock (bars from the track's grid)."""
    tempo = rec["analysis"]["tempo"]
    bar, b0 = tempo["barSeconds"], tempo["bar0Seconds"]
    rows = []
    for s in t["scenes"]:
        a, z = p["start"] + s["start"], p["start"] + s["end"] - 0.02
        rows.append({"scene": s["id"], "video": [round(s["start"], 3), round(s["end"], 3)],
                     "bars": "%d to %d" % (int(np.floor((a - b0) / bar)), int(np.floor((z - b0) / bar)))})
    cuts = []
    for s in t["scenes"][1:]:
        track_t = p["start"] + s["start"]
        off = ((track_t - b0 + bar / 2) % bar) - bar / 2
        beat = tempo.get("beatSeconds") or bar / 4
        off_beat = ((track_t - b0 + beat / 2) % beat) - beat / 2
        cuts.append({"cut": s["id"], "video": round(s["start"], 3), "fromBarLineMs": round(off * 1000), "fromBeatMs": round(off_beat * 1000)})
    return rows, cuts


def key_for(rec, p, t):
    blob = json.dumps({"v": VERSION, "sha": rec["files"]["audioSha256"], "p": {k: p[k] for k in ("edit", "start", "fadeIn", "fadeOut", "eq")},
                       "frames": t["frames"], "fps": t["fps"]}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def build(root, spec=None, quiet=False, narrated=None):
    """narrated: whether a voice will sit on the bed (speech-band EQ). None means the spec's audio.narration;
    mix.py passes whether any line is actually placed, so a music-only cut keeps the whole track."""
    root = pathlib.Path(root)
    if spec is None:
        root, spec = pj.load(root)
    a = pj.audio_spec(spec)
    if not a["music"]:
        return None
    spec = {**spec, "audio": {**(spec.get("audio") or {}), "music": a["music"]}}
    rec = library.load(a["music"]["track"])
    t = pj.timing(spec)
    p = plan(spec, rec, t, a["narration"] if narrated is None else narrated)
    key = key_for(rec, p, t)
    out = pj.build_audio(root) / ("bed-%s.wav" % key)
    meta_file = out.with_suffix(".json")
    if out.exists() and meta_file.exists():
        # The bed depends only on the edit and the total length; where the cuts fall against the music depends
        # on every scene's length, so that part is recomputed on each call, never served from the cache.
        meta = json.loads(meta_file.read_text())
        meta["scenes"], meta["cuts"] = events(rec, p, t)
        meta["cached"] = True
        return meta
    count = int(round(t["seconds"] * RATE))
    source = mt.decode(rec["audioPath"])
    first = int(round(p["start"] * RATE))
    pre = int(round(PREROLL * RATE))
    lo, hi = max(0, first - pre), min(len(source), first + count + pre)
    chunk = source[lo:hi]
    if p["eq"]:
        chunk = mt.filter_samples(chunk, p["eq"])
    piece = chunk[first - lo:first - lo + count]
    if len(piece) < count:  # the track ends first: silence after its ring-out
        piece = np.vstack([piece, np.zeros((count - len(piece), 2), np.float32)])
    bed = (piece * fade(count, p["fadeIn"], p["fadeOut"])[:, None]).astype(np.float32)
    mt.write_wav(bed, out, codec="pcm_f32le")
    level = mt.loudness(out)
    rows, cuts = events(rec, p, t)
    meta = {"note": NOTE, "file": str(out.relative_to(root)), "key": key, "track": rec["id"], "title": rec["title"],
            "artist": rec["artist"], "credit": rec["credit"], "attribution": rec["attribution"], "licence": rec["licence"],
            "edit": p, "samples": count, "seconds": round(count / RATE, 4), "level": level,
            "bandShares": mt.band_shares(bed), "scenes": rows, "cuts": cuts, "cached": False}
    mt.write_json(meta_file, meta)
    if not quiet:
        print("bed       %s, %s: %s%s" % (rec["title"], rec["artist"], p["why"], "".join("\n          note: " + n for n in p["notes"])))
    return meta


def grid(rec, p, t, to="beat"):
    """Beat (or bar) times on the video's clock for this edit."""
    tempo = rec["analysis"]["tempo"]
    step = tempo["barSeconds"] if to == "bar" else tempo.get("beatSeconds") or tempo["barSeconds"] / 4
    first = tempo["bar0Seconds"] - p["start"]
    k0 = int(np.ceil((0 - first) / step))
    k1 = int(np.floor((t["seconds"] - first) / step))
    return [first + k * step for k in range(k0, k1 + 1)], step


def snap(root, to="beat", apply=False):
    """Move every visible cut to the nearest beat (or bar) of the music edit. The landing cut stays where it is
    (the final hit is already on it), so the edit does not move. Pieces of one continued shot are not visible
    cuts and are left alone. A cut never moves more than half a step, never makes a scene shorter than 1 s or
    85% of its length, and never squeezes a narration line out of its room."""
    root, spec = pj.load(root)
    a = pj.audio_spec(spec)
    if not a["music"]:
        raise SystemExit("no music in video.json audio: nothing to snap to")
    spec_m = {**spec, "audio": {**(spec.get("audio") or {}), "music": a["music"]}}
    rec = library.load(a["music"]["track"])
    t = pj.timing(spec_m)
    p = plan(spec_m, rec, t, a["narration"])
    beats, step = grid(rec, p, t, to)
    fps, pace = t["fps"], t["pace"]
    sc = {s["id"]: s for s in pj.script(spec_m)["scenes"]}
    # A recorded line needs its measured length; an unrecorded one, the 2.5 words/s estimate.
    need = {r["scene"]: (r["speechSeconds"] if r.get("speechSeconds") is not None and r["status"] in ("ok", "long") else sc[r["scene"]]["estimatedSeconds"])
            for r in pj.line_status(root, spec_m)["scenes"]}
    by_spec = {s["id"]: s for s in spec["scenes"]}
    cuts = [s["start"] for s in t["scenes"]] + [t["seconds"]]
    fixed = {0, len(cuts) - 1}
    for i, s in enumerate(t["scenes"]):
        if i and by_spec[s["id"]].get("continue"):
            fixed.add(i)                       # no visible cut inside a continued shot
        if p["land"] is not None and abs(s["start"] - p["land"]) < 1e-6:
            fixed.add(i)
    new = list(cuts)
    rows = []
    for i in range(1, len(cuts) - 1):
        if i in fixed:
            continue
        options = sorted(beats, key=lambda b: abs(b - cuts[i]))[:2]
        for b in options:
            if abs(b - cuts[i]) > step / 2 + 1e-6:
                continue
            f = int(round(b * fps))          # cuts live on frames
            trial = list(new)
            trial[i] = f / fps
            ok = True
            for j in (i - 1, i):
                d_old, d_new = cuts[j + 1] - cuts[j], trial[j + 1] - trial[j]
                s = sc[t["scenes"][j]["id"]]
                if d_new < max(1.0, 0.85 * d_old):
                    ok = False
                if s["say"] and d_new - s["lead"] - pj.DEFAULT_TAIL + s["overrun"] < need[s["id"]]:
                    ok = False
            if ok:
                new = trial
                break
        rows.append({"cut": t["scenes"][i]["id"], "now": round(cuts[i], 3), "snapped": round(new[i], 3),
                     "movedMs": round((new[i] - cuts[i]) * 1000), "offGridMs": round(min(abs(b - new[i]) for b in beats) * 1000)})
    durs = {}
    for i, s in enumerate(t["scenes"]):
        frames = int(round((new[i + 1] - new[i]) * fps))
        durs[s["id"]] = round(frames / (fps * pace), 4)
    changed = {k: (by_spec[k]["dur"], v) for k, v in durs.items() if abs(by_spec[k]["dur"] - v) > 1e-6}
    if apply and changed:
        raw = json.loads((root / "video.json").read_text())
        for s in raw["scenes"]:
            if s["id"] in changed:
                s["dur"] = changed[s["id"]][1]
        tmp = root / ".video.json.snap.tmp"
        tmp.write_text(json.dumps(raw, indent=2) + "\n")
        tmp.replace(root / "video.json")
    return {"to": to, "stepSeconds": round(step, 4), "fixed": [t["scenes"][i]["id"] for i in sorted(fixed) if 0 < i < len(t["scenes"])],
            "cuts": rows, "durations": changed, "applied": bool(apply and changed)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--snap", choices=["beat", "bar"], help="propose scene lengths that put every visible cut on the music")
    ap.add_argument("--apply", action="store_true", help="--snap: write the lengths to video.json")
    a = ap.parse_args()
    if a.snap:
        r = snap(a.project, a.snap, a.apply)
        for c in r["cuts"]:
            print("cut %-10s %7.3f s -> %7.3f s  (%+5d ms, %d ms off the %s)" % (c["cut"], c["now"], c["snapped"], c["movedMs"], c["offGridMs"], r["to"]))
        print("kept: %s" % (", ".join(r["fixed"]) or "none"))
        print(("applied: " if r["applied"] else "would set: ") + (", ".join("%s %.2f -> %.4f s" % (k, v[0], v[1]) for k, v in r["durations"].items()) or "nothing to change"))
        return
    meta = build(a.project)
    if meta is None:
        print("no music in video.json audio")
        return
    print(json.dumps({k: meta[k] for k in ("file", "track", "edit", "seconds", "level", "cuts")}, indent=2))


if __name__ == "__main__":
    main()

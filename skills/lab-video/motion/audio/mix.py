#!/usr/bin/env python3
"""mix.py: narration lines + music bed -> the soundtrack, mastered under a picture. Analysis, not a listening review.

    python3 mix.py --project P --picture build/preview_video.mp4 --out build/preview.mp4 --preview
    python3 mix.py --project P --picture PIC.mp4 --out out/X.mp4 --final [--allow-test-voice] [--allow-missing]
    python3 mix.py --project P --report                       levels, duck and fit, no picture

Each recorded line is placed so its first word lands "voiceAt" seconds (default 0.35) after its scene's cut, at
its take's measured level matched to -20 LUFS. Nothing is sped up, slowed or retimed. The music bed (bed.py) sits
6 LU under the voice and ducks under speech, keyed from the voice alone (17.5 dB under the voice's short-term
level, never deeper than -30 dB; 100 ms look-ahead, 80 ms attack, 0.9 s hold, 1.2 s release), so the music's own
lifts and drops pass through and gaps shorter than the hold never swell. The sum is a plain sum (checked), then
one measured fixed gain masters it, checked on the decoded AAC: -16 LUFS narrated, -22 LUFS music only (a
reviewer found -16 music-only "about 2x as loud", 2026-10-13), true peak at or below -1.5 dBTP, picture stream untouched.

--final refuses: lines not recorded, lines changed since recording, lines that run past their scene, TEST_ONLY
synthetic voices (unless --allow-test-voice, which also forces a TEST_ONLY_ file name), and a missing credit.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import media as mt  # noqa: E402
import project as pj  # noqa: E402
import bed as bedmod  # noqa: E402

RATE = mt.RATE
WINDOW, STEP = 0.05, 0.005
SPEECH_DBFS = -45.0                    # a 50 ms window this loud in the -20 LUFS voice stem is speech
MIN_RUN = 2                            # windows (0.1 s)
MUSIC_BELOW_VOICE = 6.0
DUCK_GAP, DUCK_FLOOR, SHORT_TERM = 17.5, -30.0, 1.5
ATTACK, LOOKAHEAD, HOLD, RELEASE = 0.08, 0.10, 0.9, 1.2
TOLERANCE, CEILING, GAP_WARNING = 0.7, -1.5, 15.0
END_FADE = 0.04
VERSION = "mix-1"
NOTE = "Analysis and level checks, not a listening review. The assistant cannot listen."


class Refusal(Exception):
    pass


def sustained(active, minimum=MIN_RUN):
    keep = np.zeros(len(active), dtype=bool)
    start = None
    for i, flag in enumerate(list(active) + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start >= minimum:
                keep[start:i] = True
            start = None
    return keep


def short_term(levels, active):
    reach, speech = int(round(SHORT_TERM / WINDOW)), np.flatnonzero(active)
    out = np.full(len(levels), np.nan)
    for i in range(len(levels)):
        near = speech[np.searchsorted(speech, i - reach):np.searchsorted(speech, i + reach, "right")]
        if len(near):
            out[i] = np.median(levels[near])
    return out


def duck_envelope(voice_levels, music_levels, active, count):
    """Per-sample music gain keyed from the voice alone (ported unchanged from the Oct 8 mixer)."""
    if not active.any():
        return np.ones(count), np.zeros(len(active)), None
    reference = float(np.median(music_levels[active]))
    depth = np.nan_to_num(np.clip(short_term(voice_levels, active) - DUCK_GAP - reference, DUCK_FLOOR, 0.0), nan=0.0)
    hold = int(round(HOLD / WINDOW))
    held = np.array([active[max(0, i - hold):i + 1].any() for i in range(len(active))])
    first = int(np.argmax(active))
    if first <= hold:
        held[:first], depth[:first] = True, depth[first]
    control = np.repeat(np.where(held, depth, 0.0), int(round(WINDOW / STEP)))
    ahead, back = int(round(LOOKAHEAD / STEP)), int(round(RELEASE / STEP))
    attack_w = np.clip((LOOKAHEAD - np.arange(ahead + 1) * STEP) / ATTACK, 0, 1)
    looking = np.lib.stride_tricks.sliding_window_view(np.concatenate([control, np.zeros(ahead)]), ahead + 1)
    attacked = np.min(looking * attack_w, axis=1)
    release_w = (1 - np.arange(back + 1) * STEP / RELEASE)[::-1]
    behind = np.lib.stride_tricks.sliding_window_view(np.concatenate([np.zeros(back), attacked]), back + 1)
    gain_db = np.min(behind * release_w, axis=1)
    per_sample = np.interp(np.arange(count) / RATE, (np.arange(len(gain_db)) + 0.5) * STEP, gain_db)
    return 10 ** (per_sample / 20), gain_db, reference


def place_lines(root, st, count, final, allow_test, allow_missing):
    """Voice stem (mono) with every usable line at its scene. Returns (stem, placements, problems, notes)."""
    v = pj.voice_dir(root)
    stem = np.zeros(count, np.float64)
    placed, problems, notes, tests, unrecorded, changed = [], [], [], [], [], []
    for r in st["scenes"]:
        if r["status"] == "none":
            continue
        if r["status"] in ("missing", "stale"):
            if not final or allow_missing:
                (unrecorded if r["status"] == "missing" else changed).append(r["scene"])
            else:
                problems.append("%s: %s" % (r["scene"], r.get("message", r["status"])))
            continue
        if r["status"] == "long":
            (problems if final else notes).append("%s: %s" % (r["scene"], r["message"]))
        if r.get("test"):
            if final and not allow_test:
                problems.append("%s: the line is a TEST_ONLY synthetic voice (%s); record it, or pass --allow-test-voice for a test file"
                                % (r["scene"], r["take"]))
                continue
            tests.append(r["scene"])
        clip = mt.decode(v / r["clip"], channels=1)[:, 0].astype(np.float64)
        gain = pj.VOICE_LUFS - r["takeLufs"]
        at = r["firstWord"] - r["speechOffset"]
        a = int(round(at * RATE))
        if a < 0:
            clip, a = clip[-a:], 0
        if a >= count:
            problems.append("%s: the line starts after the video ends" % r["scene"])
            continue
        piece = clip[:count - a] * 10 ** (gain / 20)
        if a + len(clip) > count:  # cut at the end with a short fade, and say so
            f = min(len(piece), int(END_FADE * RATE))
            piece[-f:] *= np.linspace(1, 0, f)
            notes.append("%s: the line runs past the end of the video and is cut" % r["scene"])
        stem[a:a + len(piece)] += piece
        placed.append({"scene": r["scene"], "take": r["take"], "clip": r["clip"], "clipSha256": mt.sha256(v / r["clip"]),
                       "atSeconds": round(at, 3), "firstWord": r["firstWord"], "lastWord": r["lastWord"],
                       "gainDb": round(gain, 2), "test": bool(r.get("test"))})
    if tests:
        notes.append("TEST_ONLY synthetic voice on %d line%s (%s): never ship it" % (len(tests), "" if len(tests) == 1 else "s", ", ".join(tests)))
    if unrecorded:
        notes.append("%d line%s not recorded yet (%s): record in Voice Studio" % (len(unrecorded), "" if len(unrecorded) == 1 else "s", ", ".join(unrecorded)))
    if changed:
        notes.append("%d line%s changed since recording (%s): re-record" % (len(changed), "" if len(changed) == 1 else "s", ", ".join(changed)))
    return stem, placed, problems, notes


def scene_gaps(voice_stem, music_stem, active, t):
    win = int(round(WINDOW * RATE))
    vl, ml = mt.rms_db(voice_stem, win), mt.rms_db(music_stem, win)
    rows = []
    for s in t["scenes"]:
        a, z = int(np.ceil(s["start"] / WINDOW - 1e-9)), min(len(vl), int(np.ceil(s["end"] / WINDOW - 1e-9)))
        sp = np.flatnonzero(active[a:z]) + a
        gap = vl[sp] - ml[sp]
        rows.append({"scene": s["id"], "speechWindows": int(len(sp)),
                     "medianVoiceAboveMusicDb": round(float(np.median(gap)), 1) if len(gap) else None})
    return rows


def build_stems(root, spec, final=False, allow_test=False, allow_missing=False):
    """Everything up to the summed mix WAV (cached by what shapes it). Returns the report so far."""
    a = pj.audio_spec(spec)
    if a["narration"] and a["voice"].get("_musicOnly"):
        a["narration"] = False
    if a["silent"]:
        return None
    t = pj.timing(spec)
    count = int(round(t["seconds"] * RATE))
    report = {"note": NOTE, "seconds": t["seconds"], "narrated": a["narration"], "music": None, "warnings": [], "problems": []}
    voice = np.zeros(count)
    placed = []
    st = None
    if a["narration"]:
        st = pj.line_status(root, spec)
        voice, placed, problems, notes = place_lines(root, st, count, final, allow_test, allow_missing)
        report["problems"] += problems
        report["warnings"] += notes
        report["lines"] = st["summary"]
    music_meta = bedmod.build(root, spec, quiet=True, narrated=bool(placed)) if a["music"] else None
    gain_db = float((a["music"] or {}).get("gainDb", 0.0))
    target = a["targetLufs"] if a["targetLufs"] is not None else (pj.TARGET_NARRATED if (a["narration"] and placed) else pj.TARGET_MUSIC_ONLY + (gain_db if not a["narration"] else 0.0))
    if a["narration"] and not placed and music_meta:
        report["warnings"].append("no narration line is recorded yet: mixed as music only, at %.0f LUFS" % target)
    report["targetLufs"] = target
    key = hashlib.sha256(json.dumps({"v": VERSION, "bed": music_meta and music_meta["key"], "gain": gain_db, "placed": placed,
                                     "count": count, "narr": a["narration"]}, sort_keys=True).encode()).hexdigest()[:12]
    folder = pj.build_audio(root) / ("mix-" + key)
    report.update(key=key, stems=str(folder.relative_to(root)), placed=placed)
    if music_meta:
        report["music"] = {k: music_meta[k] for k in ("track", "title", "artist", "credit", "attribution", "licence", "edit", "level", "cuts")}
    cached = folder / "report.json"
    if cached.exists() and (folder / "mix.wav").exists():
        old = json.loads(cached.read_text())
        report.update({k: old[k] for k in ("voice", "ducking", "sceneGaps", "sum") if k in old})
        report["warnings"] += [w for w in old.get("stemWarnings", []) if w not in report["warnings"]]
        return report
    voice_st = np.repeat(voice[:, None], 2, axis=1)
    win = int(round(WINDOW * RATE))
    active = sustained(mt.rms_db(voice_st, win) > SPEECH_DBFS) if placed else np.zeros(int(round(count / win)), bool)
    active = np.concatenate([active, np.zeros(max(0, int(round(count / win)) - len(active)), bool)])[:int(round(count / win))]
    if music_meta:
        bed = mt.decode(root / music_meta["file"]).astype(np.float64)[:count]
        bed_lufs = music_meta["level"]["integrated_lufs"]
        music_gain = (pj.VOICE_LUFS - MUSIC_BELOW_VOICE - bed_lufs + gain_db) if placed else 0.0
        music = bed * 10 ** (music_gain / 20)
        duck, duck_db, ref = duck_envelope(mt.rms_db(voice_st, win), mt.rms_db(music, win), active, count) if placed else (np.ones(count), np.zeros(1), None)
        music_stem = music * duck[:, None]
        speaking = np.repeat(active, int(round(WINDOW / STEP)))[:len(duck_db)]
        during = duck_db[speaking] if placed and speaking.any() else np.zeros(1)
        report["ducking"] = {"musicGainDb": round(music_gain, 2), "reviewerGainDb": gain_db, "musicReferenceDbfs": ref and round(ref, 1),
                             "rangeDuringSpeechDb": [round(float(during.min()), 1), round(float(during.max()), 1)],
                             "medianDuringSpeechDb": round(float(np.median(during)), 1),
                             "rule": "music's typical level under speech %.1f dB under the voice's short-term level; keyed from the voice alone" % DUCK_GAP}
    else:
        music_stem = np.zeros((count, 2))
    mix = voice_st + music_stem
    folder.mkdir(parents=True, exist_ok=True)
    if placed:
        mt.write_wav(voice_st, folder / "voice.wav", codec="pcm_f32le")
    if music_meta:
        mt.write_wav(music_stem, folder / "music.wav", codec="pcm_f32le")
    mt.write_wav(mix, folder / "mix.wav", codec="pcm_f32le")
    summed = mt.decode(folder / "mix.wav")
    report["sum"] = {"plainSum": bool(len(summed) == count and float(np.abs(summed - mix).max()) < 1e-5), "how": "numpy sum of float stems; no amix"}
    if placed:
        report["voice"] = {"stemLufs": pj.VOICE_LUFS, "speechFraction": round(float(active.mean()), 3)}
        if music_meta:
            report["sceneGaps"] = scene_gaps(voice_st, music_stem, active, t)
            sw = ["scene %s: voice only %.1f dB above the music (under %.0f)" % (g["scene"], g["medianVoiceAboveMusicDb"], GAP_WARNING)
                  for g in report["sceneGaps"] if g["medianVoiceAboveMusicDb"] is not None and g["medianVoiceAboveMusicDb"] < GAP_WARNING]
            report["warnings"] += sw
            report["stemWarnings"] = sw
    mt.write_json(cached, report)
    return report


def render(root, picture, out, final=False, allow_test=False, allow_missing=False, title=None, music_only=False):
    """music_only: leave the narration out of this mix (a music-only master), whatever the spec says."""
    root = pathlib.Path(root).expanduser().resolve()
    _, spec = pj.load(root)
    if music_only:
        audio = dict(spec.get("audio") or {})
        audio["voice"] = {**(audio.get("voice") or {}), "_musicOnly": True}
        spec = {**spec, "audio": audio}
    report = build_stems(root, spec, final, allow_test, allow_missing)
    if report is None:
        return None
    if report["problems"] and final:
        raise Refusal("not mixing the final:\n  " + "\n  ".join(report["problems"]))
    out = pathlib.Path(out)
    if final and allow_test and report["placed"] and any(p["test"] for p in report["placed"]) and not out.name.startswith("TEST_ONLY_"):
        out = out.with_name("TEST_ONLY_" + out.name)
    music = report.get("music")
    if final and music and music["licence"]["kind"] == "by" and not credit_on_screen(root, music):
        raise Refusal('the music is CC BY and its credit is not on screen. Put "showsCredit": true on the end card '
                      "(the runtime gives it ctx.credit) and render, or pick a CC0 track.")
    meta = {"title": title or spec.get("title", "")}
    if music:
        meta["comment"] = music["attribution"] + " Changes: excerpted, faded%s." % (" and mixed under narration" if report["placed"] else "")
    folder = root / report["stems"]
    tmp = out.with_name(".partial-%d-%s" % (os.getpid(), out.name))
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        mastered = mt.master_to_target(picture, folder / "mix.wav", tmp, report["targetLufs"], TOLERANCE if final else 1.0,
                                       CEILING, 3 if final else 2, strict=final, metadata=meta)
        checks = {"loudnessWithinTolerance": abs(mastered["final"]["integrated_lufs"] - report["targetLufs"]) <= (TOLERANCE if final else 1.0),
                  "truePeakAtOrBelowCeiling": mastered["final"]["true_peak_dbtp"] <= CEILING, "plainSum": report["sum"]["plainSum"]}
        if final:
            info = mt.media_info(tmp)
            checks.update({"pictureStreamUnchanged": mt.stream_hash(tmp) == mt.stream_hash(picture),
                           "videoFramesUnchanged": mt.video_frames(tmp) == mt.video_frames(picture),
                           "containerDuration": abs(info["duration_seconds"] - report["seconds"]) <= 0.05,
                           "decodedAudioDuration": abs(len(mt.decode(tmp)) / RATE - report["seconds"]) <= 0.03,
                           "faststart": mt.faststart(tmp)})
            if not all(checks.values()):
                raise RuntimeError("mix checks failed, nothing was published: %s" % checks)
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    report.update(output=str(out), master={"gainDb": mastered["gain_db"], "limiterUsed": mastered["safety_limiter_used"],
                                            "attempts": len(mastered["attempts"])},
                  final=mastered["final"], checks=checks)
    if final:
        report["outputSha256"] = mt.sha256(out)
        mt.write_json(out.with_suffix(".audio.json"), report)
        if music:
            out.with_suffix(".credits.txt").write_text(credits_text(report))
    return report


def credit_on_screen(root, music):
    """The end card's captured text holds the title and artist (render.mjs records what was on screen)."""
    try:
        man = json.loads((root / "build" / "manifest.json").read_text())
    except FileNotFoundError:
        return False
    shown = " ".join(" ".join(e.get("screenText") or []) for e in man.get("scenes", {}).values()).lower()
    return music["title"].lower() in shown and music["artist"].lower() in shown


def credits_text(report):
    m = report["music"]
    lines = ["Music", "  " + m["attribution"], "  Changes: excerpted, faded%s." % (" and mixed under narration" if report["placed"] else ""),
             "  Licence checked %s on the track's own page; saved copy in the music library." % m["licence"]["checked"]]
    if report["placed"]:
        lines += ["", "Narration", "  Recorded in Voice Studio: %s." % ", ".join(sorted({p["take"] for p in report["placed"]}))]
    return "\n".join(lines) + "\n"


def describe(r):
    if not r:
        return "audio     silent"
    f = r.get("final") or {}
    parts = ["audio     %s" % ("narrated" if r["placed"] else "music only")]
    if r.get("music"):
        parts.append('"%s" (%s)' % (r["music"]["title"], r["music"]["edit"]["why"]))
    if f:
        parts.append("%.1f LUFS (target %.0f), true peak %.1f dBTP" % (f["integrated_lufs"], r["targetLufs"], f["true_peak_dbtp"]))
    if r.get("ducking") and r["placed"]:
        parts.append("duck %s dB" % r["ducking"]["rangeDuringSpeechDb"])
    if r.get("sceneGaps"):
        parts.append("voice over music %s dB" % [g["medianVoiceAboveMusicDb"] for g in r["sceneGaps"] if g["medianVoiceAboveMusicDb"] is not None])
    return ", ".join(parts) + "".join("\n          note: " + w for w in r["warnings"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--picture")
    ap.add_argument("--out")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--allow-test-voice", action="store_true")
    ap.add_argument("--allow-missing", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(a.project).expanduser().resolve()
    try:
        if a.report:
            _, spec = pj.load(root)
            print(json.dumps(build_stems(root, spec), indent=2))
            return
        if not (a.picture and a.out):
            ap.error("--picture and --out (or --report)")
        r = render(root, a.picture, a.out, a.final, a.allow_test_voice, a.allow_missing)
        print(describe(r))
    except Refusal as e:
        sys.exit("Refused: %s" % e)


if __name__ == "__main__":
    main()

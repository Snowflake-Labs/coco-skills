"""project.py: what the audio stage needs from a motion-mode project, in Python.

Scene timing MUST match lib/project.mjs: frames = max(1, round(dur * pace * fps)) with JavaScript's rounding
(half up, not Python's half-to-even), starts accumulate integer frames, so a line lands on the same frame the
picture cut does. Everything audio lives under <project>/audio (source material: takes, lines) and
<project>/build/audio (derived: beds, stems, mix reports), so deleting build/ never loses a recording.
"""
import hashlib
import json
import math
import os
import pathlib

DEFAULT_LEAD = 0.35          # seconds from the cut to the first word (people start a beat after the highlight)
DEFAULT_TAIL = 0.3           # quiet after the last word before the next cut, for the fit check
VOICE_LUFS = -20.0           # every take's speech is matched to this before the mix
TARGET_NARRATED, TARGET_MUSIC_ONLY = -16.0, -22.0   # -22: a reviewer found -16 music-only "about 2x as loud" (2026-10-13)
READ_WPS = 2.5               # comfortable read; a shipped narrated teaser was paced at 2.2


def js_round(x):
    return int(math.floor(x + 0.5))


def load(root):
    root = pathlib.Path(root).expanduser().resolve()
    spec = json.loads((root / "video.json").read_text())
    return root, spec


def audio_spec(spec):
    """The project's audio settings, normalised. {"mode": "silent"} (the old default) means none."""
    a = dict(spec.get("audio") or {})
    if a.get("mode") in ("music", "mix"):
        raise SystemExit('video.json audio.mode "%s" is the old, untested format. Use '
                         '{"music": {"track": "something_elated"}, "narration": true} (see patterns/motion-mode.md).' % a["mode"])
    music = a.get("music")
    if isinstance(music, str):
        music = {"track": music}
    voice = dict(a.get("voice") or {})
    return {"music": music or None, "narration": bool(a.get("narration")), "voice": voice,
            "targetLufs": a.get("targetLufs"), "silent": not music and not a.get("narration")}


def timing(spec):
    fps = spec.get("fps", 30)
    pace = float((spec.get("global") or {}).get("pace", 1))
    at, scenes = 0, []
    for s in spec.get("scenes", []):
        frames = max(1, js_round(float(s["dur"]) * pace * fps))
        scenes.append({"id": s["id"], "label": s.get("label", s["id"]), "role": s.get("role"),
                       "startFrame": at, "frames": frames, "start": at / fps, "dur": frames / fps, "end": (at + frames) / fps,
                       "say": norm(s.get("say")), "lead": float(s.get("voiceAt", DEFAULT_LEAD)),
                       "overrun": float(s.get("voiceOverrun", 0.0))})
        at += frames
    return {"fps": fps, "pace": pace, "frames": at, "seconds": at / fps, "scenes": scenes}


def norm(text):
    return " ".join(str(text).split()) if text else ""


def line_hash(text):
    return hashlib.sha256(norm(text).encode()).hexdigest()[:12]


def words(text):
    return len([w for w in norm(text).split(" ") if w])


def script(spec):
    """The narration script the recorder reads: every scene in order, with its line (or none) and timing."""
    t = timing(spec)
    out = []
    for s in t["scenes"]:
        say = s["say"]
        room = s["dur"] - s["lead"] - DEFAULT_TAIL + s["overrun"]
        out.append({**s, "lineHash": line_hash(say) if say else None, "words": words(say),
                    "speechStart": round(s["start"] + s["lead"], 3), "room": round(room, 3),
                    "wps": round(words(say) / room, 2) if say and room > 0 else None,
                    "estimatedSeconds": round(words(say) / READ_WPS, 2) if say else 0.0})
    digest = hashlib.sha256(json.dumps([[s["id"], s["startFrame"], s["frames"], s["lineHash"]] for s in out]).encode()).hexdigest()[:16]
    return {"title": spec.get("title", ""), "fps": t["fps"], "duration": t["seconds"], "scenes": out, "scriptHash": digest,
            "lines": sum(1 for s in out if s["say"]), "words": sum(s["words"] for s in out)}


def lab_home():
    return pathlib.Path(os.environ.get("LAB_VIDEO_HOME") or "~/.lab-video").expanduser()


def music_dir():
    """The shared music library: $MOTION_MUSIC, else ~/.lab-video/music. Same rule as lib/project.mjs musicDir()."""
    if os.environ.get("MOTION_MUSIC"):
        return pathlib.Path(os.environ["MOTION_MUSIC"]).expanduser()
    return lab_home() / "music"


def voice_dir(root):
    return pathlib.Path(root) / "audio" / "voice"



def voice_paths(root):
    v = voice_dir(root)
    return v, v / "takes", v / "lines", v / "lines.json"


def read_lines(root):
    _, _, _, idx = voice_paths(root)
    try:
        return json.loads(idx.read_text())
    except (FileNotFoundError, ValueError):
        return {"version": 1, "lines": []}


def line_status(root, spec=None):
    """Every scene: its line, the clip that will play (if any), and whether it fits."""
    root = pathlib.Path(root)
    if spec is None:
        root, spec = load(root)
    sc = script(spec)
    pick = (audio_spec(spec)["voice"].get("pick") or {})
    index = read_lines(root)
    v, _, _, _ = voice_paths(root)
    rows = []
    for i, s in enumerate(sc["scenes"]):
        row = {"scene": s["id"], "label": s["label"], "say": s["say"], "words": s["words"], "lineHash": s["lineHash"],
               "start": s["start"], "end": s["end"], "voiceAt": s["lead"], "room": s["room"], "estimatedSeconds": s["estimatedSeconds"]}
        if not s["say"]:
            row["status"] = "none"
            rows.append(row)
            continue
        mine = [x for x in index["lines"] if x["scene"] == s["id"] and (v / x["file"]).exists()]
        current = sorted([x for x in mine if x["lineHash"] == s["lineHash"]], key=lambda x: x["take"])
        chosen = None
        if s["id"] in pick:
            chosen = next((x for x in mine if x["take"] == pick[s["id"]]), None)
            if chosen and chosen["lineHash"] != s["lineHash"]:
                row.update(status="stale", take=chosen["take"], message="the line changed since %s was recorded" % chosen["take"])
                rows.append(row)
                continue
        chosen = chosen or (current[-1] if current else None)
        if not chosen:
            row.update(status="stale" if mine else "missing",
                       message=("the words changed since the last recording; re-record this line" if mine else "not recorded yet"))
            rows.append(row)
            continue
        first = s["start"] + s["lead"]
        last = first + chosen["speechSeconds"]
        limit = s["end"] - DEFAULT_TAIL + s["overrun"]
        nxt = next((n for n in sc["scenes"][i + 1:] if n["say"]), None)
        clash = nxt and last + 0.15 > nxt["start"] + nxt["lead"]
        row.update(status="ok", take=chosen["take"], test=chosen.get("test", False), clip=chosen["file"],
                   speechSeconds=chosen["speechSeconds"], speechOffset=chosen["speechOffset"], takeLufs=chosen["takeLufs"],
                   firstWord=round(first, 3), lastWord=round(last, 3), takes=[x["take"] for x in current])
        if last > limit or clash:
            over = last - limit
            row.update(status="long", over=round(max(over, 0), 2),
                       message=("ends %.2f s after its scene allows; lengthen this scene to %.2f s, start the voice earlier, or cut words"
                                % (over, s["dur"] + over) if last > limit else "runs into the next line"))
        rows.append(row)
    lines = [r for r in rows if r["status"] != "none"]
    return {"scriptHash": sc["scriptHash"], "scenes": rows,
            "summary": {"lines": len(lines), "ok": sum(r["status"] == "ok" for r in lines),
                        "missing": sum(r["status"] == "missing" for r in lines), "stale": sum(r["status"] == "stale" for r in lines),
                        "long": sum(r["status"] == "long" for r in lines), "test": any(r.get("test") for r in lines)}}


def build_audio(root):
    d = pathlib.Path(root) / "build" / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def land_seconds(spec, music, t=None):
    """Where a music 'ending' edit lands its final hit: the call-to-action cut by default."""
    t = t or timing(spec)
    land = (music or {}).get("land", "cta")
    offset = float((music or {}).get("landOffset", 0.0))
    if isinstance(land, (int, float)):
        return float(land) + offset
    by = {s["id"]: s for s in t["scenes"]}
    if land == "cta":
        cta = [s for s in t["scenes"] if s.get("role") == "cta"]
        target = cta[0] if cta else t["scenes"][-1]
    elif land in by:
        target = by[land]
    else:
        raise SystemExit('audio.music.land "%s" is not "cta", a scene id or seconds' % land)
    return target["start"] + offset

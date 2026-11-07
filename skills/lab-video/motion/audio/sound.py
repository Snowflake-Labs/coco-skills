#!/usr/bin/env python3
"""sound.py: the review server's window onto audio (one JSON answer per call, so Node never re-implements it).

    python3 sound.py state --project P          library, music settings, script, every line's status, takes
    python3 sound.py set   --project P --json '{"track": "night_owl", "gainDb": -3, "edit": "intro", "narration": true}'
    python3 sound.py pick  --project P --scene aisle1 --take take-004      (or --take "" to go back to newest)
    python3 sound.py snap  --project P [--to beat|bar] [--apply]

`set` validates and writes video.json "audio" atomically and prints {"changed": {...}}; the server journals it
and re-renders (only a scene that shows the credit can change pixels when the track changes).
"""
import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import project as pj  # noqa: E402

EDITS = ("ending", "intro")
GAIN = (-12.0, 6.0)


def library_tracks():
    lib = pj.music_dir()
    out = []
    for f in sorted(lib.glob("*/track.json")):
        try:
            r = json.loads(f.read_text())
        except ValueError:
            continue
        an = r.get("analysis", {})
        out.append({"id": r["id"], "title": r["title"], "artist": r["artist"], "licence": r["licence"]["short"],
                    "licenceUrl": r["licence"]["url"], "checked": r["licence"]["checked"], "page": r["page"],
                    "credit": r["credit"], "tags": r.get("tags", []), "genres": r.get("genres", []),
                    "bpm": an.get("tempo", {}).get("bpm"), "seconds": an.get("decodedSeconds"),
                    "landable": bool(an.get("finalHit"))})
    return out


def state(root):
    root, spec = pj.load(root)
    a = pj.audio_spec(spec)
    sc = pj.script(spec)
    st = pj.line_status(root, spec)
    _, takes_dir, _, _ = pj.voice_paths(root)
    takes = []
    for f in sorted(takes_dir.glob("take-*.ingest.json")):
        try:
            t = json.loads(f.read_text())
            takes.append({k: t.get(k) for k in ("take", "mode", "test", "seconds", "lines", "takeLufs", "clipped", "stray")})
        except ValueError:
            pass
    try:
        preview = json.loads((root / "build" / "preview.json").read_text()).get("audio")
    except (FileNotFoundError, ValueError):
        preview = None
    music = a["music"] or None
    return {"music": music, "narration": a["narration"], "silent": a["silent"], "library": library_tracks(),
            "script": {"hash": sc["scriptHash"], "duration": sc["duration"], "fps": sc["fps"], "lines": sc["lines"], "words": sc["words"],
                       "scenes": [{k: s[k] for k in ("id", "label", "start", "end", "dur", "say", "lineHash", "words", "lead", "room",
                                                      "wps", "estimatedSeconds", "speechStart")} for s in sc["scenes"]]},
            "lines": st, "takes": takes[::-1], "preview": preview}


def write_spec(root, spec):
    tmp = root / (".video.json.%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(root / "video.json")


def set_audio(root, change):
    root, spec = pj.load(root)
    audio = dict(spec.get("audio") or {})
    if audio.get("mode") == "silent":
        audio.pop("mode")
    music = audio.get("music")
    music = {"track": music} if isinstance(music, str) else dict(music or {})
    tracks = {t["id"]: t for t in library_tracks()}
    before = json.loads(json.dumps({"music": audio.get("music"), "narration": audio.get("narration", False)}))
    if "track" in change:
        if change["track"] in ("", None):
            music = {}
        elif change["track"] not in tracks:
            raise SystemExit(json.dumps({"error": "no track %r in the music library" % change["track"]}))
        else:
            music["track"] = change["track"]
            if not tracks[change["track"]]["landable"] and music.get("edit", "ending") == "ending":
                music["edit"] = "intro"
    if "gainDb" in change:
        g = float(change["gainDb"])
        if not GAIN[0] <= g <= GAIN[1]:
            raise SystemExit(json.dumps({"error": "music level must be between %g and %+g dB" % GAIN}))
        music["gainDb"] = round(g, 1)
    if "edit" in change:
        if change["edit"] not in EDITS:
            raise SystemExit(json.dumps({"error": "edit must be one of %s" % ", ".join(EDITS)}))
        if change["edit"] == "ending" and music.get("track") and not tracks.get(music["track"], {}).get("landable"):
            raise SystemExit(json.dumps({"error": "this track has no final hit to land; it can only start from the top and fade"}))
        music["edit"] = change["edit"]
    if "narration" in change:
        audio["narration"] = bool(change["narration"])
    if music.get("track"):
        audio["music"] = music
    else:
        audio.pop("music", None)
    spec["audio"] = audio
    write_spec(root, spec)
    after = {"music": audio.get("music"), "narration": audio.get("narration", False)}
    return {"changed": {"from": before, "to": after}}


def pick(root, scene, take):
    root, spec = pj.load(root)
    if not any(s["id"] == scene for s in spec.get("scenes", [])):
        raise SystemExit(json.dumps({"error": "no scene %r" % scene}))
    audio = dict(spec.get("audio") or {})
    voice = dict(audio.get("voice") or {})
    picks = dict(voice.get("pick") or {})
    if take:
        lines = pj.read_lines(root)["lines"]
        if not any(x["scene"] == scene and x["take"] == take for x in lines):
            raise SystemExit(json.dumps({"error": "%s has no recording of %s" % (take, scene)}))
        picks[scene] = take
    else:
        picks.pop(scene, None)
    voice["pick"] = picks
    audio["voice"] = voice
    spec["audio"] = audio
    write_spec(root, spec)
    return {"pick": picks}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["state", "set", "pick", "snap"])
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", default="{}")
    ap.add_argument("--scene")
    ap.add_argument("--take", default=None)
    ap.add_argument("--to", default="beat", choices=["beat", "bar"])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(a.project).expanduser().resolve()
    if a.cmd == "state":
        out = state(root)
    elif a.cmd == "set":
        out = set_audio(root, json.loads(a.json))
    elif a.cmd == "pick":
        out = pick(root, a.scene, a.take or "")
    else:
        import bed
        out = bed.snap(root, a.to, a.apply)
    print(json.dumps(out))


if __name__ == "__main__":
    main()

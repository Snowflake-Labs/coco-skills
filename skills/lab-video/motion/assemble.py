#!/usr/bin/env python3
"""assemble.py: join a motion-mode project's scene clips into a preview or a delivery MP4.

    python3 assemble.py --project ~/videos/my_video --preview
    python3 assemble.py --project P --final                      # then probe.py + contactsheet.py
    python3 assemble.py --project P --final --out ~/Desktop/x.mp4 --no-gauntlet

--preview  stream-copies the current clips (instant) into build/preview.mp4 plus build/preview.json,
           the scene boundaries the review page draws on its timeline.
--final    renders anything stale first (render.mjs, cached scenes are free), re-encodes once for
           delivery (H.264 yuv420p BT.709, faststart), mixes the soundtrack under that picture with the
           picture stream copied byte for byte (audio/mix.py), then runs the gauntlet on the file.

Audio, from video.json "audio" (see audio/README.md):
  {}                                                   silent (Slack autoplays muted anyway)
  {"music": {"track": "something_elated"}}             a licence-checked library bed, mastered to -22 LUFS
  {"music": {...}, "narration": true}                  Voice Studio lines over the bed, ducked, -16 LUFS
--preview mixes whatever is recorded so far; --final refuses missing, changed, overlong or TEST_ONLY lines
(--allow-test-voice writes a TEST_ONLY_ file instead) and a CC BY track whose credit is not on screen.

Snowflake mode (patterns/snowflake.md): a project that binds Snowflake values, films an app or asks for captions
is gated before --final assembles anything (gate.py --final must pass), and "captions": {"burn": true} burns
captions/captions.ass into the delivery file, keeping a clean master (*_clean.mp4) and the .vtt beside it.
--final exits non-zero when probe.py fails the file it made.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
GAUNTLET = HERE.parent / "gauntlet"
sys.path.insert(0, str(GAUNTLET))
from ffmpeg_tools import tool  # noqa: E402
sys.path.insert(0, str(HERE / "audio"))
import mix as audiomix  # noqa: E402
import project as audioproject  # noqa: E402
sys.path.insert(0, str(HERE / "snowflake"))
import captions as sfcaptions  # noqa: E402  (stdlib at import time)

BT709 = ("setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709")
BURN_FONTS = "/System/Library/Fonts"   # libass finds Helvetica here on a Mac; elsewhere it falls back to fontconfig


def uses_snowflake(spec):
    return bool(spec.get("data") or spec.get("captions") or any(s.get("bind") or s.get("capture") for s in spec.get("scenes", [])))


def burn(root, spec, out, allow_test=False):
    """Burn captions into `out` (picture re-encoded once, audio copied), keeping the clean master and the .vtt."""
    st = sfcaptions.status(root, spec)
    if st["state"] == "missing":
        sys.exit("Refused: captions.burn is on but there are no captions yet (snowflake/captions.py transcribe)")
    if st["state"] == "stale":
        sys.exit("Refused: the captions were made from an older recording; re-run snowflake/captions.py transcribe")
    if st["state"] == "none":
        return out
    if st["captions"].get("test") and not allow_test:
        sys.exit("Refused: the captions come from a TEST_ONLY voice")
    ass = root / "captions" / "captions.ass"
    clean = out.with_name(out.stem + "_clean" + out.suffix)
    os.replace(out, clean)
    filt = "subtitles=filename='%s'%s,%s" % (str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'"),
                                            ":fontsdir=%s" % BURN_FONTS if os.path.isdir(BURN_FONTS) else "", BT709)
    run(["-i", str(clean), "-vf", filt, "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-threads", "8",
         "-r", str(spec.get("fps", 30)), "-c:a", "copy", "-map_metadata", "0", "-movflags", "+faststart", str(out)])
    shutil.copyfile(root / "captions" / "captions.vtt", out.with_suffix(".vtt"))
    print("captions  burned %d cues into %s; clean master %s; sidecar %s" % (len(st["captions"]["cues"]), out.name, clean.name,
                                                                            out.with_suffix(".vtt").name))
    return out


def ff():
    return tool("FFMPEG", "ffmpeg")


def run(args, check=True):
    p = subprocess.run(ff() + ["-hide_banner", "-y"] + args, capture_output=True, text=True)
    if check and p.returncode != 0:
        sys.exit("ffmpeg failed:\n" + p.stderr[-1500:])
    return p


def load(project):
    root = pathlib.Path(project).expanduser().resolve()
    spec = json.loads((root / "video.json").read_text())
    try:
        man = json.loads((root / "build" / "manifest.json").read_text())
    except FileNotFoundError:
        sys.exit("no build/manifest.json: run render.mjs first")
    return root, spec, man


def clips_in_order(root, spec, man):
    order = [s["id"] for s in spec["scenes"]]
    out, missing = [], []
    for sid in order:
        e = man.get("scenes", {}).get(sid)
        f = root / e["file"] if e and e.get("file") else None
        if not f or not f.exists():
            missing.append(sid)
        else:
            out.append((sid, f, e))
    if missing:
        sys.exit("no current clip for: %s (run render.mjs)" % ", ".join(missing))
    if man.get("order") != order:
        sys.exit("manifest scene order %s differs from video.json %s (run render.mjs)" % (man.get("order"), order))
    return out


def concat(clips, dest, root, extra=None):
    lst = root / "build" / "concat.txt"
    lst.write_text("".join("file '%s'\n" % str(f).replace("'", "'\\''") for _, f, _ in clips))
    args = ["-f", "concat", "-safe", "0", "-i", str(lst)]
    run(args + (extra or ["-c", "copy", "-movflags", "+faststart"]) + [str(dest)])


def boundaries(clips, fps):
    at, out = 0, []
    for sid, _, e in clips:
        n = int(e["frames"])
        out.append({"id": sid, "start": at / fps, "end": (at + n) / fps, "frames": n,
                    "version": e.get("version", 1), "key": e.get("current")})
        at += n
    return out, at


def final(root, spec, man, out, gauntlet, allow_test=False, allow_missing=False, music_only=False):
    clips = clips_in_order(root, spec, man)
    bounds, frames = boundaries(clips, spec.get("fps", 30))
    fps = spec.get("fps", 30)
    dur = frames / fps
    joined = root / "build" / "joined.mp4"
    concat(clips, joined, root)
    venc = ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-threads", "8",
            "-vf", BT709, "-r", str(fps), "-movflags", "+faststart"]
    out.parent.mkdir(parents=True, exist_ok=True)
    picture = root / "build" / "final_picture.mp4"
    run(["-i", str(joined)] + venc + ["-an", "-metadata", "title=" + spec.get("title", ""), str(picture)])
    joined.unlink(missing_ok=True)
    report = None
    if audioproject.audio_spec(spec)["silent"]:
        shutil.copyfile(picture, out)
    else:
        try:
            report = audiomix.render(root, picture, out, final=True, allow_test=allow_test, allow_missing=allow_missing,
                                     music_only=music_only)
        except audiomix.Refusal as e:
            sys.exit("Refused: %s" % e)
        out = pathlib.Path(report["output"])
        print(audiomix.describe(report))
    if (spec.get("captions") or {}).get("burn"):
        out = burn(root, spec, out, allow_test)
    snap = out.with_suffix(".spec.json")
    snap.write_text(json.dumps({"video": spec, "scenes": bounds}, indent=2))
    print("wrote     %s  (%.3fs, %d frames)" % (out, dur, frames))
    if gauntlet:
        cmd = [sys.executable, str(GAUNTLET / "probe.py"), str(out), "--expect-sec", "%.3f" % dur]
        cmd += ["--allow-silent"] if report is None else ["--target-lufs", "%.1f" % report["targetLufs"]]
        probed = subprocess.run(cmd)
        subprocess.run([sys.executable, str(GAUNTLET / "contactsheet.py"), str(out), "--frames",
                        str(min(16, max(8, 2 * len(clips)))), "--out", str(out.with_suffix(".sheet.png"))])
        if probed.returncode != 0:
            sys.exit("probe.py FAILED %s: do not deliver it (the reasons are above)" % out.name)
    return out


def preview(root, spec, man):
    clips = clips_in_order(root, spec, man)
    fps = spec.get("fps", 30)
    bounds, frames = boundaries(clips, fps)
    dest = root / "build" / "preview.mp4"
    audio = None
    if audioproject.audio_spec(spec)["silent"]:
        concat(clips, dest, root)
    else:
        picture = root / "build" / "preview_picture.mp4"
        concat(clips, picture, root)
        try:
            r = audiomix.render(root, picture, dest)
            audio = {"key": r["key"], "narrated": bool(r["placed"]), "lufs": r["final"]["integrated_lufs"], "targetLufs": r["targetLufs"],
                     "music": r["music"] and {k: r["music"][k] for k in ("track", "title", "artist", "credit")},
                     "edit": r["music"] and r["music"]["edit"]["why"], "warnings": r["warnings"], "lines": r.get("lines")}
            print(audiomix.describe(r))
        except Exception as e:  # a broken mix must never cost the reviewer the picture
            shutil.copyfile(picture, dest)
            audio = {"error": str(e)[:400]}
            print("audio     preview mix failed, picture only: %s" % e, file=sys.stderr)
    (root / "build" / "preview.json").write_text(json.dumps(
        {"title": spec.get("title"), "fps": fps, "frames": frames, "seconds": frames / fps, "scenes": bounds, "audio": audio}, indent=2))
    print("PREVIEW   %s  (%.3fs, %d scenes)" % (dest.relative_to(root), frames / fps, len(clips)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--no-render", action="store_true", help="--final: trust the current clips")
    ap.add_argument("--no-gauntlet", action="store_true")
    ap.add_argument("--allow-test-voice", action="store_true", help="--final: mix TEST_ONLY lines into a TEST_ONLY_ file")
    ap.add_argument("--allow-missing", action="store_true", help="--final: leave unrecorded lines silent")
    ap.add_argument("--music-only", action="store_true", help="--final: a music-only master (-22 LUFS), narration left out")
    a = ap.parse_args()
    if not (a.preview or a.final):
        ap.error("pass --preview or --final")
    root, spec, man = load(a.project)
    if a.final and not a.no_render:
        node = shutil.which("node") or "node"
        r = subprocess.run([node, str(HERE / "render.mjs"), "--project", str(root), "--quiet"])
        if r.returncode != 0:
            sys.exit("render failed; not assembling a video with stale or missing scenes")
        root, spec, man = load(a.project)
    if a.preview:
        preview(root, spec, man)
    if a.final:
        if uses_snowflake(spec):
            # Snowflake values, filmed apps and captions all have failure modes a file probe cannot see (a stale
            # query, a leak in a take, captions from an old recording), so the gate decides before anything is built.
            g = subprocess.run([sys.executable, str(HERE / "gate.py"), "--project", str(root), "--final"], capture_output=True, text=True)
            fails = [ln for ln in g.stdout.splitlines() if ln.startswith("FAIL")]
            test_only = [ln for ln in fails if "TEST_ONLY" in ln]
            if g.returncode != 0 and not (a.allow_test_voice and fails and len(test_only) == len(fails)):
                sys.exit("Refused: gate.py --final fails this project:\n" + "\n".join(fails or [g.stdout[-600:]]))
        slug = re.sub(r"[^A-Za-z0-9]+", "_", spec.get("title", root.name)).strip("_")
        out = pathlib.Path(a.out).expanduser().resolve() if a.out else root / "out" / (slug + ("_music_only" if a.music_only else "") + ".mp4")
        final(root, spec, man, out, not a.no_gauntlet, a.allow_test_voice, a.allow_missing, a.music_only)


if __name__ == "__main__":
    main()

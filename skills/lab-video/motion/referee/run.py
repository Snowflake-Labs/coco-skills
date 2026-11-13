#!/usr/bin/env python3
"""Engine referee: render a 4-scene frame-index project end to end and check every frame survives.

    python3 run.py                # clean engine must PASS
    python3 run.py --mutate       # each seeded engine bug must FAIL the check (mutation test)

Scenes: code (0-29), clock (30-59), clock "continue" (60-74), code (75-104). The joins between them,
the continued clock and the final re-encode are where frames get lost or doubled, so they are what this
exercises. Work happens in a temp directory; nothing is written into the skill.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

MOTION = pathlib.Path(__file__).resolve().parents[1]
CHECK = pathlib.Path(__file__).resolve().parent / "check_frames.py"
SPEC = {
    "title": "referee", "fps": 30, "width": 1920, "height": 1080, "audio": {"mode": "silent"},
    "scenes": [
        {"id": "a", "src": "scenes/frame_index.html", "dur": 1.0, "params": {"offset": 0}},
        {"id": "b", "src": "scenes/frame_index.html", "dur": 1.0, "clock": True, "params": {"offset": 30}},
        {"id": "c", "src": "scenes/frame_index.html", "dur": 0.5, "clock": True, "continue": "b"},
        {"id": "d", "src": "scenes/frame_index.html", "dur": 1.0, "params": {"offset": 75}},
    ],
}
TOTAL = 105

# (file, original, mutant, what it simulates). Each must be caught by check_frames.py.
MUTATIONS = [
    ("lib/capture.mjs", "const png = await frameAt(proj, s, page, f, {});\n      hashes.push",
     "const png = await frameAt(proj, s, page, f + 1, {});\n      hashes.push", "every frame one late"),
    ("lib/project.mjs", "s.fromMs = p.fromMs + (p.frames * 1000 * p.speed)",
     "s.fromMs = p.fromMs + ((p.frames - 1) * 1000 * p.speed)", "continued clock overlaps its predecessor"),
    ("lib/capture.mjs", "for (let f = 0; f < s.frames; f++) {\n      const png = await frameAt(proj, s, page, f, {});",
     "for (let f = 0; f < s.frames - 1; f++) {\n      const png = await frameAt(proj, s, page, f, {});", "last frame of each scene dropped"),
]


def run_once(motion_dir, work):
    proj = work / "proj"
    if proj.exists():
        shutil.rmtree(proj)
    (proj / "scenes").mkdir(parents=True)
    shutil.copy(MOTION / "referee" / "frame_index.html", proj / "scenes" / "frame_index.html")
    (proj / "video.json").write_text(json.dumps(SPEC, indent=2))
    r = subprocess.run(["node", str(motion_dir / "render.mjs"), "--project", str(proj), "--quiet"], capture_output=True, text=True)
    if r.returncode != 0:
        return "render failed: " + (r.stderr or r.stdout)[-600:]
    out = proj / "out" / "referee.mp4"
    r = subprocess.run([sys.executable, str(motion_dir / "assemble.py"), "--project", str(proj), "--final", "--no-render",
                        "--no-gauntlet", "--out", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        return "assemble failed: " + (r.stderr or r.stdout)[-600:]
    c = subprocess.run([sys.executable, str(CHECK), str(out), "--expect", str(TOTAL)], capture_output=True, text=True)
    return None if c.returncode == 0 else c.stdout.strip()


def verify_catches_impurity(motion_dir, work):
    """verify tolerates compositor noise; it must still fail a scene that is NOT a pure function of its
    frame. The impure scene nudges a white square 1 px per seek, the smallest real bug there is."""
    proj = work / "impure"
    if proj.exists():
        shutil.rmtree(proj)
    (proj / "scenes").mkdir(parents=True)
    (proj / "scenes" / "impure.html").write_text(
        '<!doctype html><link rel="stylesheet" href="/motion/scene.css"><style>html,body{background:#000}'
        '#sq{position:absolute;left:600px;top:300px;width:400px;height:400px;background:#fff}</style>'
        '<div id="stage"><div id="sq"></div></div><script src="/motion/motion.js"></script><script>'
        'let calls = 0; Motion.scene({ params: {}, build() {}, frame(ctx, t, f) {'
        ' document.getElementById("sq").style.left = (600 + f * 4 + (calls++ % 2)) + "px"; } });</script>')
    (proj / "video.json").write_text(json.dumps({"title": "impure", "fps": 30, "scenes": [
        {"id": "x", "src": "scenes/impure.html", "dur": 1.0}]}))
    r = subprocess.run(["node", str(motion_dir / "render.mjs"), "--project", str(proj), "--verify", "--quiet"], capture_output=True, text=True)
    rr = subprocess.run(["node", str(motion_dir / "render.mjs"), "--project", str(proj), "--verify"], capture_output=True, text=True)
    line = next((l for l in rr.stdout.splitlines() if l.startswith("VERIFY")), "")
    return (rr.returncode != 0 and "MISMATCH" in line), line or (r.stderr or rr.stderr)[-300:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutate", action="store_true")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="motion_referee_") as tmp:
        work = pathlib.Path(tmp)
        if not a.mutate:
            err = run_once(MOTION, work)
            print("PASS  clean engine: %d frames intact across code, clock and continued scenes" % TOTAL if not err else "FAIL  clean engine: " + err)
            caught, line = verify_catches_impurity(MOTION, work)
            print(("PASS  verify fails an impure scene: " if caught else "FAIL  verify passed an impure scene: ") + line.strip())
            return 1 if (err or not caught) else 0
        caught = 0
        for f, orig, mutant, what in MUTATIONS:
            m = work / "motion"
            if m.exists():
                shutil.rmtree(m)
            shutil.copytree(MOTION, m, ignore=shutil.ignore_patterns("build", "__pycache__"))
            # the gauntlet helpers live beside motion/ and are imported by relative path
            g = work / "gauntlet"
            if not g.exists():
                shutil.copytree(MOTION.parent / "gauntlet", g, ignore=shutil.ignore_patterns("__pycache__"))
            src = (m / f).read_text()
            if orig not in src:
                print("ERROR mutation target not found in %s: %r (the referee is stale)" % (f, orig[:60]))
                return 2
            (m / f).write_text(src.replace(orig, mutant, 1))
            err = run_once(m, work)
            ok = err is not None
            caught += ok
            print("%s  %-44s %s" % ("caught" if ok else "MISSED", what, (err or "check passed a broken engine").splitlines()[0][:90]))
        print("---\n%d/%d seeded engine bugs caught" % (caught, len(MUTATIONS)))
        return 0 if caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())

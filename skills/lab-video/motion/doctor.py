#!/usr/bin/env python3
"""doctor.py: is this machine ready for motion mode? Each check says what to do when it fails.

    python3 motion/doctor.py            # quick (about 5 s): tools, a real headless render of one frame, music
    python3 motion/doctor.py --online   # also re-check every library track's licence on its FMA page

Exit 0 when everything required passes; optional checks (Google Chrome for the browser drives, macOS `say`
for TEST_ONLY voices) only warn.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "audio"))
results = []


def report(level, name, detail="", fix=""):
    results.append(level)
    print("%-5s %s%s%s" % (level, name, ("  " + detail) if detail else "", ("\n      fix: " + fix) if fix and level != "ok" else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true")
    a = ap.parse_args()

    node = shutil.which("node")
    if not node:
        report("FAIL", "node", "not found", "install Node.js 18+ (brew install node)")
    else:
        v = subprocess.run([node, "-v"], capture_output=True, text=True).stdout.strip()
        ok = int(v.lstrip("v").split(".")[0]) >= 18
        report("ok" if ok else "FAIL", "node " + v, "", "Node.js 18+ is required")

    try:
        import numpy
        report("ok", "numpy " + numpy.__version__)
    except ImportError:
        report("FAIL", "numpy", "not importable by " + sys.executable, "python3 -m pip install --user numpy")

    import media as mt
    try:
        ff = mt.ffmpeg()
        out = subprocess.run(ff + ["-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        enc = all(e in out for e in ("libx264", "aac"))
        report("ok" if enc else "FAIL", "ffmpeg", " ".join(ff)[:90], "an ffmpeg with libx264 and aac: brew install ffmpeg, or python3 -m pip install --user imageio-ffmpeg")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "ffmpeg", str(e)[:90], "brew install ffmpeg, or python3 -m pip install --user imageio-ffmpeg")

    if node:
        js = """
import(%r).then(async ({ playwright }) => {
  const { chromium } = await playwright();
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 320, height: 180 } });
  await p.setContent('<div style="width:320px;height:180px;background:#29B5E8"></div>');
  const png = await p.screenshot();
  const gl = await p.evaluate(() => { const g = document.createElement('canvas').getContext('webgl2'); return g ? 'webgl2' : 'no webgl2'; });
  await b.close();
  console.log(JSON.stringify({ ok: png.length > 100, gl }));
}).catch((e) => { console.log(JSON.stringify({ ok: false, error: String(e.message || e).slice(0, 300) })); });
""" % str(HERE / "lib" / "playwright.mjs")
        r = subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=120)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            res = {"ok": False, "error": (r.stderr or r.stdout)[-300:]}
        report("ok" if res.get("ok") else "FAIL", "playwright + chromium render a frame", res.get("gl", "") or res.get("error", ""),
               "bash motion/setup.sh (installs Playwright into ~/.lab-video), or set PLAYWRIGHT_PATH")

    chrome = pathlib.Path("/Applications/Google Chrome.app")
    report("ok" if chrome.exists() or shutil.which("google-chrome") else "warn", "Google Chrome (for the review and Voice Studio drives)",
           "" if chrome.exists() else "not found", "install Chrome; rendering does not need it")

    import project as pj
    lib = pj.music_dir()
    tracks = sorted(p.parent.name for p in lib.glob("*/track.json"))
    report("ok" if tracks else "FAIL", "music library " + str(lib), "%d tracks: %s" % (len(tracks), ", ".join(tracks)) if tracks else "empty",
           "python3 motion/audio/library.py seed")
    if a.online and tracks:
        r = subprocess.run([sys.executable, str(HERE / "audio" / "library.py"), "check"], capture_output=True, text=True)
        report("ok" if r.returncode == 0 else "FAIL", "licences unchanged on FMA", r.stdout.strip().replace("\n", "; ")[:200],
               "a track whose licence changed is no longer safe: remove it (rm -r its folder) or pick another")

    if shutil.which("say"):
        report("ok", "macOS say (TEST_ONLY voices for testing the pipeline; never shipped)")
    else:
        report("warn", "say", "not found", "only needed for voice.py testvoice; real narration is recorded in Voice Studio")

    with tempfile.TemporaryDirectory() as tmp:
        try:
            import numpy as np
            x = (0.1 * np.sin(2 * np.pi * 440 * np.arange(48000) / 48000)).astype("float32")
            mt.write_wav(np.stack([x, x], 1), pathlib.Path(tmp) / "t.wav", codec="pcm_f32le")
            lv = mt.loudness(pathlib.Path(tmp) / "t.wav")
            report("ok" if lv["integrated_lufs"] is not None else "FAIL", "loudness measurement (ebur128)", "%.1f LUFS on a test tone" % lv["integrated_lufs"])
        except Exception as e:  # noqa: BLE001
            report("FAIL", "loudness measurement", str(e)[:120])

    fails = results.count("FAIL")
    print("---\n%s" % ("ready" if not fails else "%d to fix" % fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""scan.py: read everything a filmed take showed, and refuse it if any of it must not travel.

    python3 scan.py --project P --take takes/app/take-003.json          page text, then OCR of the picture
    python3 scan.py --project P --take ... --no-ocr                       page text only

live.mjs keeps every line of text the page and its frames showed while recording (the Streamlit app's iframe
included). That misses what is drawn rather than written: Snowsight's result grids and charts are canvas, and
a screenshot inside the app is pixels. So this also OCRs the take at 2 frames a second with Apple Vision, on
this machine (nothing is uploaded), where macOS and pyobjc have it.

What is refused, matched case-insensitively exactly as gate.py matches its block list:
  video.json gate.block and gate.names, the scene's capture.forbid ("re:" prefix = regex, else literal text),
  and always: email addresses, and anything shaped like a password, secret, key or token assignment.
The result is written into the take's JSON ("scan"); gate.py re-checks the same text against the current
lists, so a name blocked after filming still stops the video. Exit 1 on any hit.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "gauntlet"))

ALWAYS = [("an email address", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}"),
          ("a credential", r"\b(?:passw(?:or)?d|secret|api[_ -]?key|token)\s*[:=]\s*\S+")]


def patterns(spec, scene, gate_lists=True):
    g = spec.get("gate") or {}
    pats = []
    own = g.get("block", []) + g.get("names", []) if gate_lists else []
    for b in [str(x) for x in own + ((scene or {}).get("capture") or {}).get("forbid", [])]:
        pats.append((b, re.compile(b[3:] if b.startswith("re:") else re.escape(b), re.I)))
    allow = set(((scene or {}).get("capture") or {}).get("allow", []))
    for label, rx in ALWAYS:
        if label not in allow:
            pats.append((label, re.compile(rx, re.I)))
    return pats


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", s.lower())


def _near(term, text, k):
    """True if some stretch of `text` is within k edits of `term` (both already normalised)."""
    n = len(term)
    for w in range(max(1, n - k), n + k + 1):
        for i in range(0, max(1, len(text) - w + 1)):
            a, b = term, text[i:i + w]
            prev = list(range(len(b) + 1))
            for x in range(1, len(a) + 1):
                cur = [x] + [0] * len(b)
                for y in range(1, len(b) + 1):
                    cur[y] = min(prev[y] + 1, cur[y - 1] + 1, prev[y - 1] + (a[x - 1] != b[y - 1]))
                prev = cur
                if min(prev) > k:
                    break
            if prev[-1] <= k:
                return True
    return False


def findings(spec, scene, lines, gate_lists=True, approx=False):
    """[(what matched, the line, "exact" | "approx")] for every line that shows something refused. Pure: gate.py
    calls it too. approx=True is for OCR text, which misreads letters (Vision read ZEBRAFISH as ZEBBASISH): a literal
    term of 5 to 7 letters may then be one edit away, 8 or more two, never a shorter one. Lines listed in
    capture.ocrAllow (checked by a person) are skipped by the approximate match only."""
    pats = patterns(spec, scene, gate_lists)
    cap = (scene or {}).get("capture") or {}
    reviewed = {str(x) for x in cap.get("ocrAllow", [])}
    literal = [(b, _norm(b)) for b, _ in pats if not b.startswith("re:") and b not in dict(ALWAYS)] if approx else []
    out = []
    for line in lines:
        hit = False
        for label, rx in pats:
            if rx.search(line):
                out.append((label, line, "exact"))
                hit = True
        if hit or line in reviewed:
            continue
        flat = _norm(line)
        for label, term in literal:
            k = 0 if len(term) < 5 else 1 if len(term) < 8 else 2
            if k and _near(term, flat, k):
                out.append((label, line, "approx"))
    return out


def ocr_frames(video, fps=2.0):
    """[(seconds, [lines])] from Apple Vision, or raises ImportError where this machine has no Vision."""
    import Quartz  # noqa: F401  (pyobjc; macOS only)
    import Vision
    from Foundation import NSURL
    from ffmpeg_tools import tool
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(tool("FFMPEG", "ffmpeg") + ["-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", "fps=%g" % fps,
                                                  str(pathlib.Path(tmp) / "o%05d.png")], check=True)
        for i, png in enumerate(sorted(pathlib.Path(tmp).glob("o*.png"))):
            src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(png)), None)
            img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
            req = Vision.VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            req.setUsesLanguageCorrection_(False)
            Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None).performRequests_error_([req], None)
            lines = [r.topCandidates_(1)[0].string() for r in (req.results() or []) if r.topCandidates_(1)]
            out.append((round(i / fps, 2), lines))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--take", required=True)
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--fps", type=float, default=2.0)
    a = ap.parse_args()
    root = pathlib.Path(a.project).expanduser().resolve()
    spec = json.loads((root / "video.json").read_text())
    tj = pathlib.Path(a.take).expanduser().resolve()
    take = json.loads(tj.read_text())
    scene = next((s for s in spec.get("scenes", []) if s.get("id") == take.get("scene")), None)
    dom_lines = [t["line"] for t in take.get("text", [])]
    dom_hits = findings(spec, scene, dom_lines)
    ocr = {"ran": False}
    if a.no_ocr:
        ocr["why"] = "skipped (--no-ocr)"
    else:
        video = tj.parent / take["file"]
        try:
            frames = ocr_frames(video, a.fps)
            seen = {}
            for t, lines in frames:
                for line in lines:
                    seen.setdefault(line, t)
            ocr = {"ran": True, "engine": "Apple Vision", "fps": a.fps, "frames": len(frames),
                   "lines": [{"line": k, "t": v} for k, v in list(seen.items())[:800]]}
            ocr["hits"] = [{"what": w, "line": ln, "match": how} for w, ln, how in findings(spec, scene, list(seen), approx=True)]
        except ImportError:
            ocr["why"] = "no Apple Vision in this Python (macOS with pyobjc only); look at the contact sheet instead"
        except subprocess.CalledProcessError as e:
            ocr["why"] = "could not decode the take: %s" % e
    hits = [{"what": w, "line": ln, "source": "page text", "match": how} for w, ln, how in dom_hits] + \
           [{"source": "OCR", **h} for h in ocr.get("hits", [])]
    take["scan"] = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "patterns": len(patterns(spec, scene)), "dom": {"lines": len(dom_lines), "hits": len(dom_hits)},
                    "ocr": ocr, "hits": hits, "ok": not hits}
    tj.write_text(json.dumps(take, indent=1))
    print("SCAN      %s: %d lines of page text%s, %d patterns: %s" % (
        tj.name, len(dom_lines), ", %d OCR lines from %d frames" % (len(ocr["lines"]), ocr["frames"]) if ocr["ran"] else "",
        len(patterns(spec, scene)), "clean" if not hits else "%d HITS" % len(hits)))
    if not ocr["ran"]:
        print("note      OCR %s" % ocr["why"])
    for h in hits[:20]:
        print("HIT       %s (%s%s): %r" % (h["what"], h["source"], ", looks like it" if h.get("match") == "approx" else "", h["line"][:100]))
    if any(h.get("match") == "approx" for h in hits):
        print("note      a \"looks like it\" hit is OCR text one or two letters away from a blocked term; if a person has "
              "checked the frame and it is not the term, list that line in capture.ocrAllow")
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()

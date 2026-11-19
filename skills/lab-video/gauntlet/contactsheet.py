#!/usr/bin/env python3
"""Tier 3: sample frames onto one grid so you can LOOK at the video instead of watching it.

    python3 contactsheet.py out.mp4 --frames 12 --out sheet.png
    python3 contactsheet.py out.mp4 --frames 16 --cols 4 --crop presenter
    python3 contactsheet.py out.mp4 --at 0,4.4,8.8,13.2 --out sheet.png

Exit 0 when the sheet was written to the host. Exit 1 otherwise.

WHY THIS EXISTS
  You cannot catch a 0.3s type collision, an off-brand frame or a mid-transition state at
  playback speed, because attention is on the motion. Reviewing a video by watching it
  once is anti-pattern 13. The grid removes time from the problem: defects that are
  invisible in motion are obvious side by side.

  It is the one tool here that produces no verdict. It produces something to look at, and
  the looking is the check. A green run means the sheet exists, nothing more.

TIMESTAMPS ARE BURNED IN, NOT PRINTED
  A defect you spot is useless until you know which second to fix, and a mapping printed
  to the terminal gets separated from the image as soon as the sheet is shared or re-read.
  One ffmpeg pass does sampling, labelling and tiling. If no font is reachable it degrades
  to an unlabelled sheet plus a printed mapping rather than failing.
"""
import argparse
import os
import pathlib
import re
import shlex
import subprocess
import sys

# Present in linuxserver/ffmpeg. Mono first, so digits do not jitter between cells.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

# Reserved camera zone, measured in ../patterns/delivery.md.
PRESENTER_CROP = "crop=672:1080:1248:0"


# Shared with probe.py: env var, then PATH, then the native imageio_ffmpeg build and the
# ffprobe shim, so a sheet can be made with Docker down.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ffmpeg_tools import tool  # noqa: E402


def run(cmd, timeout=900):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ss" % timeout


def container_path(path, cmd, must_exist):
    """Map a host path into the container mount, or explain why it is unreachable."""
    p = pathlib.Path(path).expanduser().resolve()
    joined = " ".join(cmd)
    if "docker" not in joined:
        return str(p), None
    m = re.search(r'-v\s+"?([^":]+)"?:(/\w+)', joined)
    if not m:
        return str(p), None
    root = pathlib.Path(os.path.expandvars(m.group(1))).expanduser().resolve()
    dest = m.group(2)
    try:
        rel = p.relative_to(root)
    except ValueError:
        return str(p), (
            "%s is outside the container mount %s -> %s.\n"
            "      %s Docker runs inside colima, so only colima-mounted host paths are "
            "visible: $HOME works, /tmp does not — and ffmpeg exits 0 while the file lands "
            "in the VM and vanishes." % (
                p, root, dest,
                "The input cannot be read." if must_exist else "The output would be lost."))
    return "%s/%s" % (dest, rel.as_posix()), None


def duration_of(cpath, ffprobe):
    rc, out = run(ffprobe + ["-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", cpath])
    if rc != 0:
        return None, out.strip()
    try:
        return float(out.strip().splitlines()[0]), None
    except (ValueError, IndexError):
        return None, "unparseable duration: %r" % out.strip()


def find_font(ffmpeg, explicit):
    """First font readable from inside the render environment, or None."""
    if explicit:
        return explicit
    if "docker" in " ".join(ffmpeg):
        img = ffmpeg[-1]
        test = " || ".join("test -f '%s' && echo '%s'" % (f, f) for f in FONT_CANDIDATES)
        rc, out = run(["docker", "run", "--rm", "--entrypoint", "sh", img,
                       "-c", "(%s) | head -1" % test], timeout=180)
        hit = [l for l in out.strip().splitlines() if l.startswith("/")]
        return hit[0] if rc == 0 and hit else None
    for f in FONT_CANDIDATES:
        if pathlib.Path(f).exists():
            return f
    return None


def grid_for(n, cols):
    """cols x rows covering n cells. `tile` fills any short final row with `color`."""
    cols = cols or max(1, min(4, round(n ** 0.5 + 0.4)))
    cols = min(cols, n)
    return cols, (n + cols - 1) // cols


def build_graph(stamps, cell_w, crop, font):
    """Per-frame chains, plus the labels to feed concat.

    Each frame is a separate -ss input rather than an `fps` sample, so cells land on
    exactly the requested seconds. fps sampling drifts, and a drifting sheet makes
    "the defect is at t=8.8" wrong by up to a whole beat.

    concat then tile (rather than xstack) because tile pads an incomplete final row
    itself; xstack requires a full rectangle and errors out on 10 frames in a 4x3 grid.

    `trim=end_frame=1` IS LOAD-BEARING. `-ss` positions an input but does not truncate it,
    so without the trim each input contributes its whole remaining runtime, concat produces
    one long stream, and tile fills the grid with 12 CONSECUTIVE frames from the first
    input. That renders a sheet where every cell is the same moment, labelled with the
    first cell's timestamp, and exits 0. It was caught by looking at the sheet, which is
    the entire argument for this tool existing.
    """
    parts, labels = [], []
    for i, t in enumerate(stamps):
        vf = ["trim=end_frame=1", "setpts=PTS-STARTPTS"]
        if crop == "presenter":
            vf.append(PRESENTER_CROP)
        vf.append("scale=%d:-2" % cell_w)
        vf.append("setsar=1")
        if font:
            vf.append(
                "drawtext=fontfile='%s':text='%d   t\\=%.1fs':x=12:y=10:fontsize=26:"
                "fontcolor=white:box=1:boxcolor=black@0.66:boxborderw=9" % (font, i + 1, t))
        parts.append("[%d:v]%s[v%d]" % (i, ",".join(vf), i))
        labels.append("[v%d]" % i)
    return parts, labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=12, help="evenly spaced samples")
    ap.add_argument("--at", default="",
                    help="comma-separated seconds instead of even spacing")
    ap.add_argument("--cols", type=int, default=0, help="0 = near-square")
    ap.add_argument("--cell-width", type=int, default=640)
    ap.add_argument("--gap", type=int, default=8, help="padding between cells, px")
    ap.add_argument("--crop", choices=["none", "presenter"], default="none",
                    help="presenter crops to the reserved camera zone")
    ap.add_argument("--font", default="", help="font path inside the render environment")
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--out", default="", help="default: <video>.sheet.png beside the video")
    a = ap.parse_args()

    ffmpeg = tool("FFMPEG", "ffmpeg")
    ffprobe = tool("FFPROBE", "ffprobe")

    host_in = pathlib.Path(a.video).expanduser().resolve()
    if not host_in.exists():
        print("FAIL  %s does not exist on the host" % host_in)
        return 1
    host_out = pathlib.Path(a.out).expanduser().resolve() if a.out \
        else host_in.with_suffix(host_in.suffix + ".sheet.png")

    cin, err = container_path(host_in, ffmpeg, must_exist=True)
    if err:
        print("FAIL  %s" % err)
        return 1
    cout, err = container_path(host_out, ffmpeg, must_exist=False)
    if err:
        print("FAIL  %s" % err)
        return 1
    cprobe, _ = container_path(host_in, ffprobe, must_exist=True)

    dur, derr = duration_of(cprobe, ffprobe)
    if dur is None:
        print("FAIL  could not read duration (%s).\n"
              "      FFPROBE must be set separately from FFMPEG; a docker FFMPEG wrapper "
              "needs --entrypoint ffprobe." % derr)
        return 1

    # --- pick timestamps ------------------------------------------------------------
    if a.at:
        try:
            stamps = [float(x) for x in a.at.split(",") if x.strip()]
        except ValueError:
            print("FAIL  --at must be comma-separated seconds")
            return 1
        stamps = [t for t in stamps if 0 <= t < dur]
        if not stamps:
            print("FAIL  no --at timestamp falls inside 0..%.2fs" % dur)
            return 1
    else:
        n = max(1, a.frames)
        step = dur / n
        # Half-step offset: sampling at exactly 0 catches the pre-animation state, and the
        # final frame of a trailer is usually a held CTA that says nothing new.
        stamps = [round(step * (i + 0.5), 3) for i in range(n)]

    cols, rows = grid_for(len(stamps), a.cols)
    font = None if a.no_labels else find_font(ffmpeg, a.font)

    parts, labels = build_graph(stamps, a.cell_width, a.crop, font)
    graph = (";".join(parts) + ";" + "".join(labels)
             + "concat=n=%d:v=1:a=0[cc];" % len(stamps)
             + "[cc]tile=%dx%d:margin=%d:padding=%d:color=#101014[out]"
               % (cols, rows, a.gap, a.gap))

    cmd = ffmpeg + ["-y", "-hide_banner", "-nostats"]
    for t in stamps:
        # -ss BEFORE -i seeks by keyframe and is fast; accurate enough at these gaps.
        cmd += ["-ss", "%.3f" % t, "-i", cin]
    cmd += ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1", cout]

    rc, out = run(cmd)
    if rc != 0:
        print("FAIL  ffmpeg could not build the sheet (rc=%d):\n%s" % (rc, out[-2000:]))
        return 1
    # The colima trap: rc==0 does NOT mean the PNG reached the host.
    if not host_out.exists():
        print("FAIL  ffmpeg exited 0 but %s does not exist on the host.\n"
              "      It was written inside the docker VM. Write under $HOME, not /tmp."
              % host_out)
        return 1

    print("sheet     %s (%.2f MB)" % (host_out, host_out.stat().st_size / 1e6))
    print("grid      %d frames, %d x %d, cell %dpx, sampled across %.2fs"
          % (len(stamps), cols, rows, a.cell_width, dur))
    if font:
        print("labels    burned in (%s)" % font)
    else:
        print("labels    NONE — no reachable font, so this mapping is the only record:")
        for i, t in enumerate(stamps):
            print("            cell %-3d t=%.1fs" % (i + 1, t))
    if a.crop == "presenter":
        print("crop      presenter camera zone (%s)" % PRESENTER_CROP)
    print("---\nNow OPEN IT AND LOOK. This tool reports nothing about quality — read "
          "critique.md against the grid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

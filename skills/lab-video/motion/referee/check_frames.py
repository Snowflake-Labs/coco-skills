#!/usr/bin/env python3
"""Read the 16-bar frame index off every frame of a video and check the sequence.

    python3 check_frames.py out.mp4 --expect 105        # want: 0, 1, 2, ... 104, each exactly once

Each bar is 96 px wide; ffmpeg averages each into one pixel (scale=16:1:flags=area), so a frame costs
16 bytes to read. Exit 0 only when every index appears once and in order.
"""
import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "gauntlet"))
from ffmpeg_tools import tool  # noqa: E402


def read_indices(video):
    cmd = tool("FFMPEG", "ffmpeg") + ["-hide_banner", "-loglevel", "error", "-i", str(video), "-map", "0:v:0",
                                      "-vf", "crop=1536:8:192:536,scale=16:1:flags=area,format=gray",
                                      "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    out = []
    for k in range(0, len(raw) - 15, 16):
        bits = raw[k:k + 16]
        out.append(sum((1 << (15 - i)) for i, b in enumerate(bits) if b > 127))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--expect", type=int, required=True, help="number of frames, indices 0..N-1")
    a = ap.parse_args()
    got = read_indices(a.video)
    want = list(range(a.expect))
    problems = []
    if len(got) != len(want):
        problems.append("decoded %d frames, expected %d" % (len(got), len(want)))
    first = next((i for i, (g, w) in enumerate(zip(got, want)) if g != w), None)
    if first is not None:
        problems.append("frame %d shows index %d, expected %d (context: %s)" % (first, got[first], want[first], got[max(0, first - 3):first + 4]))
    dup = sorted({x for x in got if got.count(x) > 1})
    miss = sorted(set(want) - set(got))
    if dup:
        problems.append("repeated indices: %s" % dup[:12])
    if miss:
        problems.append("missing indices: %s" % miss[:12])
    if problems:
        print("FAIL  " + "\n      ".join(problems))
        return 1
    print("ok    %d frames, indices 0..%d each exactly once, in order" % (len(got), a.expect - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

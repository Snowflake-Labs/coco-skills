#!/usr/bin/env python3
"""ffprobe stand-in for a host that has ffmpeg (imageio_ffmpeg's native build) but no ffprobe.

It answers only the two calls this gauntlet makes, by parsing `ffmpeg -i <file>`:

    -v error -show_streams -show_format -of json <file>          (probe.py)
    -v error -show_entries format=duration -of default=nw=1:nk=1   (contactsheet.py)

Duration is the container duration ffmpeg reports (centisecond precision). nb_read_frames is
counted by decoding, so a truncated file shows up twice. Anything else exits 2 rather than
guessing, because a stand-in that answers a question it does not understand is worse than none.

probe.py and contactsheet.py select this automatically only when FFPROBE is unset and no
ffprobe is on PATH (see ffmpeg_tools.py).
"""
import json
import re
import subprocess
import sys

from ffmpeg_tools import native_ffmpeg


def parse(path, ffmpeg):
    head = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    if "Invalid data" in head or "No such file" in head or "Duration:" not in head:
        return None, head
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", head)
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    br = re.search(r"bitrate: (\d+) kb/s", head)
    streams = []
    for line in head.splitlines():
        sm = re.search(r"Stream #\d+:\d+.*?: (Video|Audio): (\w+)", line)
        if not sm:
            continue
        kind, codec = sm.group(1).lower(), sm.group(2)
        s = {"codec_type": kind, "codec_name": codec}
        if kind == "video":
            pf = re.search(r", (yuvj?\d{3}p\w*|rgb\w+|bgr\w+|gbr\w+|gray\w*|nv12)", line)
            rng = re.search(r"\((tv|pc)[,)]", line)
            wh = re.search(r", (\d{2,5})x(\d{2,5})", line)
            fps = re.search(r", ([\d.]+) fps", line)
            s.update({"pix_fmt": pf.group(1) if pf else None,
                      "color_range": rng.group(1) if rng else None,
                      "width": int(wh.group(1)) if wh else None,
                      "height": int(wh.group(2)) if wh else None,
                      "r_frame_rate": f"{float(fps.group(1)):g}/1" if fps else None})
            count = subprocess.run([ffmpeg, "-hide_banner", "-i", path, "-map", "0:v:0", "-f", "null", "-"],
                                   capture_output=True, text=True).stderr
            frames = re.findall(r"frame=\s*(\d+)", count)
            if frames:
                s["nb_read_frames"] = frames[-1]
        else:
            hz = re.search(r", (\d+) Hz", line)
            s["sample_rate"] = hz.group(1) if hz else None
        streams.append(s)
    fmt = {"duration": f"{duration:.3f}" if duration is not None else None,
           "bit_rate": str(int(br.group(1)) * 1000) if br else None}
    return {"streams": streams, "format": fmt}, None


def main(argv):
    ffmpeg = native_ffmpeg()
    if not ffmpeg or not argv:
        sys.stderr.write("ffprobe_shim: no native ffmpeg found (pip install imageio-ffmpeg)\n")
        return 2
    path = argv[-1]
    info, err = parse(path, ffmpeg)
    if info is None:
        sys.stderr.write(err)
        return 1
    args = " ".join(argv[:-1])
    if "-show_streams" in args and "-of json" in args:
        print(json.dumps(info))
        return 0
    if "format=duration" in args and "nk=1" in args:
        d = info["format"]["duration"]
        if d is None:
            sys.stderr.write("no duration reported\n")
            return 1
        print(d)
        return 0
    sys.stderr.write("ffprobe_shim: unsupported call: %s\n" % args)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

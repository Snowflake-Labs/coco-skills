#!/usr/bin/env python3
"""Tier 1-2 on the rendered FILE: is it well-formed, and is it the video you specified?

Every check here corresponds to a failure that shipped, or nearly shipped, and that
playback would not have revealed — see ../patterns/anti-patterns.md.

    python3 probe.py out.mp4 --expect-sec 43.7
    python3 probe.py out.mp4 --expect-sec 43.7 --layout presenter
    python3 probe.py --make-bad /tmp/_bad.mp4     # regenerate the known-bad case

Exit 0 on pass or warnings only. Exit 1 on FAIL.

WHY THESE CHECKS AND NOT OTHERS
  duration   The only automatic detector of the truncated-render class. A truncated video
             is well-formed, playable, and missing its CTA. This is why --expect-sec is
             the highest-value flag in the lab; without it the check silently degrades to
             an advisory.
  pix_fmt    yuv444p plays perfectly in VLC and shows BLACK in Slack. A local player is
             not a proxy for the destination.
  faststart  Read from the file's own bytes rather than ffprobe: `moov` before `mdat`.
             ffprobe has no direct "is faststart" field, and the atom order IS the answer.
  loudness   Every video this pipeline produced before the amix fix was ~12 dB quiet and
             nobody noticed, because a quiet video plays fine — you just turn it up.
             Measured, never judged by ear.

THE COLIMA TRAP (../patterns/delivery.md)
  Docker runs inside a colima VM, so -v only maps host paths colima mounts. $HOME works;
  /tmp does not — and ffmpeg exits 0 while the file lands in the VM and vanishes with the
  container. An exit 0 plus a missing artifact is the worst shape of failure, so this
  script verifies the input exists on a host path before believing anything, and
  --make-bad re-checks its own output afterwards.
"""
import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

TARGET_LUFS = -16.0          # web/Slack norm. See ../patterns/audio.md.
LUFS_TOLERANCE = 2.0         # shipped reference trailer measures -15.3 and is correct.
WEB_PIX_FMT = "yuv420p"
WEB_CODEC = "h264"


# --- tool resolution ---------------------------------------------------------------
# FFMPEG/FFPROBE may be multi-word docker wrappers, so they are split with shlex rather
# than treated as a path. They must be set SEPARATELY: when FFMPEG is a docker wrapper
# its entrypoint is ffmpeg, and ffprobe needs --entrypoint ffprobe. When neither is set
# and nothing is on PATH, ffmpeg_tools falls back to the native imageio_ffmpeg build
# and ffprobe_shim.py, so the gauntlet runs without Docker.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ffmpeg_tools import tool, describe  # noqa: E402


def run(cmd, timeout=600):
    """Run and return (rc, stdout+stderr). ffmpeg writes its report to stderr."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ss" % timeout


def container_path(path, ffprobe_cmd):
    """Map a host path into the container mount when the tool is a docker wrapper.

    The documented wrapper mounts $HOME/videos at /w. Anything outside that
    tree is unreachable from inside the container, which is a clearer failure to report
    than ffprobe's own "No such file or directory".
    """
    p = pathlib.Path(path).expanduser().resolve()
    if "docker" not in " ".join(ffprobe_cmd):
        return str(p), None
    mount = None
    joined = " ".join(ffprobe_cmd)
    m = re.search(r'-v\s+"?([^":]+)"?:(/\w+)', joined)
    if m:
        mount = (pathlib.Path(os.path.expandvars(m.group(1))).expanduser().resolve(),
                 m.group(2))
    if not mount:
        return str(p), None
    host_root, dest = mount
    try:
        rel = p.relative_to(host_root)
    except ValueError:
        return str(p), (
            "%s is outside the container mount %s -> %s, so ffprobe inside docker cannot "
            "see it. Move the file under %s or set FFPROBE to a native ffprobe."
            % (p, host_root, dest, host_root))
    return "%s/%s" % (dest, rel.as_posix()), None


# --- probes ------------------------------------------------------------------------
def probe_streams(path_in_container, ffprobe):
    rc, out = run(ffprobe + ["-v", "error", "-show_streams", "-show_format",
                             "-of", "json", path_in_container])
    if rc != 0:
        return None, out.strip()
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, "unparseable ffprobe json: %s" % e


def measure_lufs(path_in_container, ffmpeg):
    """Integrated loudness via ebur128, or None when there is no audio."""
    rc, out = run(ffmpeg + ["-hide_banner", "-nostats", "-i", path_in_container,
                            "-af", "ebur128=peak=true", "-f", "null", "-"])
    i = re.findall(r"^\s*I:\s*(-?\d+\.?\d*)\s*LUFS", out, re.M)
    peak = re.findall(r"Peak:\s*(-?\d+\.?\d*)\s*dBFS", out)
    if not i:
        return None, None
    return float(i[-1]), (float(peak[-1]) if peak else None)


def has_faststart(host_path, probe_bytes=1 << 16):
    """True when `moov` precedes `mdat`.

    Read from the host file directly: this needs no tool, works when ffprobe is
    unavailable, and the atom order is literally the property being asked about.
    """
    with open(host_path, "rb") as fh:
        head = fh.read(probe_bytes)
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    if moov == -1 and mdat == -1:
        return None, "neither atom in the first %d bytes" % probe_bytes
    if moov == -1:
        return False, "mdat at %d, no moov in the first %d bytes" % (mdat, probe_bytes)
    if mdat == -1:
        return True, "moov at %d, mdat beyond the first %d bytes" % (moov, probe_bytes)
    return moov < mdat, "moov at %d, mdat at %d" % (moov, mdat)


def make_bad(dest, ffmpeg, ffprobe):
    """Generate the known-bad calibration file: truncated + yuv444p + no faststart.

    A gate that has never failed is not known to work. This exists so that claim stays
    reproducible without committing a binary fixture to the skill.
    """
    dest = pathlib.Path(dest).expanduser().resolve()
    cpath, err = container_path(dest, ffprobe)
    if err:
        print("FAIL  %s" % err)
        return 1
    rc, out = run(ffmpeg + [
        "-y", "-hide_banner",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=12",
        "-c:v", "libx264", "-pix_fmt", "yuv444p",   # defect: black frame in Slack
        "-t", "12",                                  # defect: truncated vs a 43.7s spec
        cpath])                                      # defect: no -movflags +faststart
    if rc != 0:
        print("FAIL  ffmpeg could not write the fixture:\n%s" % out[-1500:])
        return 1
    # The colima trap: rc==0 above does NOT mean the file reached the host.
    if not dest.exists():
        print("FAIL  ffmpeg exited 0 but %s does not exist on the host — the file landed "
              "inside the docker VM. Write under $HOME, not /tmp." % dest)
        return 1
    print("wrote %s (%d bytes)\n\nExpect exactly 3 fails:\n"
          "    python3 %s %s --expect-sec 43.7"
          % (dest, dest.stat().st_size, pathlib.Path(__file__).name, dest))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?", help="the rendered file (mp4 or webm)")
    ap.add_argument("--expect-sec", type=float, default=None,
                    help="spec TOTAL_S. The single highest-value check in this lab: it is "
                         "the only automatic detector of a truncated render.")
    ap.add_argument("--tol-sec", type=float, default=0.35,
                    help="duration tolerance; one frame at 30fps is 0.033s")
    ap.add_argument("--target-lufs", type=float, default=TARGET_LUFS)
    ap.add_argument("--lufs-tol", type=float, default=LUFS_TOLERANCE)
    ap.add_argument("--expect-w", type=int, default=1920)
    ap.add_argument("--expect-h", type=int, default=1080)
    ap.add_argument("--layout", choices=["center", "presenter"], default="center",
                    help="presenter reserves a camera zone; only changes advice, not gates")
    ap.add_argument("--allow-silent", action="store_true",
                    help="a silent caption-driven video is a legitimate choice, not a defect")
    ap.add_argument("--skip-loudness", action="store_true", help="skip the ebur128 pass")
    ap.add_argument("--make-bad", metavar="PATH", default=None,
                    help="write the known-bad calibration file and exit")
    a = ap.parse_args()

    ffprobe = tool("FFPROBE", "ffprobe")
    ffmpeg = tool("FFMPEG", "ffmpeg")

    if a.make_bad:
        return make_bad(a.make_bad, ffmpeg, ffprobe)
    if not a.video:
        ap.error("a video path is required (or use --make-bad)")

    host = pathlib.Path(a.video).expanduser().resolve()
    if not host.exists():
        print("FAIL  %s does not exist on the host.\n"
              "      If ffmpeg just exited 0, this is the colima trap: the file was "
              "written inside the docker VM. See ../patterns/delivery.md." % host)
        return 1
    if host.stat().st_size == 0:
        print("FAIL  %s is zero bytes" % host)
        return 1

    cpath, err = container_path(host, ffprobe)
    fails, warns, notes = [], [], []
    for d in (describe(ffprobe), describe(ffmpeg)):
        if "shim" in d or "native" in d:
            notes.append("tools: " + d)
    if err:
        fails.append(err)

    info = None
    if not err:
        info, perr = probe_streams(cpath, ffprobe)
        if info is None:
            fails.append("ffprobe failed — the file may be corrupt, or FFPROBE is "
                         "misconfigured (it must be set separately from FFMPEG; a docker "
                         "FFMPEG wrapper needs --entrypoint ffprobe):\n      %s" % perr)

    vs = astream = fmt = None
    if info:
        fmt = info.get("format", {}) or {}
        for s in info.get("streams", []) or []:
            if s.get("codec_type") == "video" and vs is None:
                vs = s
            elif s.get("codec_type") == "audio" and astream is None:
                astream = s
        if vs is None:
            fails.append("no video stream")

    suffix = host.suffix.lower()
    is_mp4 = suffix in (".mp4", ".m4v", ".mov")

    # --- duration ------------------------------------------------------------------
    dur = None
    for src in (fmt or {}, vs or {}):
        try:
            dur = float(src.get("duration"))
            break
        except (TypeError, ValueError):
            continue
    if dur is None:
        warns.append("no duration reported — cannot check for a truncated render")
    elif a.expect_sec is None:
        notes.append("duration %.2fs unchecked: pass --expect-sec (render.mjs prints "
                     "TOTAL_S) or a truncated render will pass this gate silently" % dur)
    else:
        delta = dur - a.expect_sec
        if abs(delta) > a.tol_sec:
            verb = "SHORT" if delta < 0 else "LONG"
            fails.append(
                "duration %.2fs vs spec %.2fs — %.2fs %s. %s"
                % (dur, a.expect_sec, abs(delta), verb,
                   "The render was truncated and the file is missing its ending, which is "
                   "the CTA. Re-encoding with -t cannot restore frames that were never "
                   "captured." if delta < 0 else
                   "Longer than the spec: check --dur-scale and any -t on the encode."))
        else:
            notes.append("duration %.2fs matches spec %.2fs (\u00b1%.2fs)"
                         % (dur, a.expect_sec, a.tol_sec))

    # --- pixel format / codec ------------------------------------------------------
    if vs:
        pf = vs.get("pix_fmt")
        if pf != WEB_PIX_FMT:
            fails.append("pix_fmt=%s, expected %s — this plays perfectly in a local "
                         "player and shows a BLACK FRAME in Slack. Re-encode with "
                         "-pix_fmt yuv420p." % (pf, WEB_PIX_FMT))
        cn = vs.get("codec_name")
        if is_mp4 and cn != WEB_CODEC:
            fails.append("codec=%s, expected %s for broad inline support" % (cn, WEB_CODEC))
        elif not is_mp4:
            notes.append("codec %s in %s — intermediate, not a delivery file" % (cn, suffix))

        w, h = vs.get("width"), vs.get("height")
        if (w, h) != (a.expect_w, a.expect_h):
            warns.append("%sx%s, expected %sx%s" % (w, h, a.expect_w, a.expect_h))
        fr = vs.get("r_frame_rate") or ""
        if "/" in fr:
            try:
                n, d = fr.split("/")
                fps = float(n) / float(d) if float(d) else 0
                if fps < 24:
                    warns.append("%.2g fps — motion will judder" % fps)
                notes.append("%dx%d @ %.4g fps %s" % (w, h, fps, cn))
            except (ValueError, ZeroDivisionError):
                pass

    # --- faststart -----------------------------------------------------------------
    if is_mp4:
        ok, detail = has_faststart(host)
        if ok is False:
            fails.append("no faststart (%s) — playback waits for the whole download. "
                         "Re-encode with -movflags +faststart. The Playwright-bundled "
                         "ffmpeg REJECTS this flag; use a real ffmpeg." % detail)
        elif ok is None:
            warns.append("could not determine atom order (%s)" % detail)
        else:
            notes.append("faststart ok (%s)" % detail)

    # --- loudness ------------------------------------------------------------------
    if astream is None and info:
        msg = ("no audio stream — silent. Legitimate for a caption-driven video in "
               "Slack's muted autoplay, a defect if you intended narration.")
        notes.append(msg) if a.allow_silent else warns.append(msg)
    elif astream and not a.skip_loudness and not err:
        lufs, peak = measure_lufs(cpath, ffmpeg)
        if lufs is None:
            warns.append("could not measure loudness — ebur128 produced no summary")
        else:
            off = lufs - a.target_lufs
            desc = "%.1f LUFS%s" % (lufs, "" if peak is None else " / peak %.1f dBFS" % peak)
            if abs(off) > a.lufs_tol:
                fails.append(
                    "%s is %.1f dB %s the %.1f target. %s"
                    % (desc, abs(off), "under" if off < 0 else "over", a.target_lufs,
                       "Check amix for normalize=0 — without it every input is divided by "
                       "1/n, which is how every video here shipped ~12 dB quiet. Apply a "
                       "FIXED gain, not loudnorm." if off < 0 else
                       "Too hot; reduce the fixed gain and keep alimiter=limit=0.95."))
            else:
                notes.append("%s within \u00b1%.1f of %.1f"
                             % (desc, a.lufs_tol, a.target_lufs))
            if peak is not None and peak > -1.0:
                warns.append("true peak %.1f dBFS risks clipping on consumer playback"
                             % peak)

    # --- size sanity ---------------------------------------------------------------
    mb = host.stat().st_size / 1e6
    notes.append("size %.2f MB" % mb)
    if dur and dur > 0:
        kbps = (host.stat().st_size * 8 / 1000) / dur
        if kbps > 12000:
            warns.append("%.0f kb/s is high — are you encoding an ambient loop at "
                         "bitrate for no communicative gain?" % kbps)
    if mb > 20:
        warns.append("%.1f MB is past the comfortable Slack size" % mb)

    # --- report --------------------------------------------------------------------
    print("file      %s" % host)
    for n in notes:
        print("  ok    %s" % n)
    for m in warns:
        print("WARN  %s" % m)
    for m in fails:
        print("FAIL  %s" % m)
    print("---\n%d fail, %d advisory" % (len(fails), len(warns)))
    if a.layout == "presenter":
        print("presenter layout: confirm the camera zone is clear and that --guides was "
              "NOT used (guide outlines are burned into the pixels). "
              "See ../patterns/delivery.md.")
    if not fails:
        print("Tier 1-2 clear on the file. The video is well-formed and matches the spec. "
              "That says nothing about whether it READS — run contactsheet.py and look at "
              "the grid.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

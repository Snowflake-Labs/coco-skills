"""Resolve FFMPEG / FFPROBE for the gauntlet scripts, and fall back to native tools.

Resolution order, per tool:
  1. the FFMPEG / FFPROBE environment variable, exactly as before (a docker wrapper still works);
  2. ffmpeg / ffprobe on PATH;
  3. the native ffmpeg 7.1 that ships inside imageio_ffmpeg (no Docker, reads /tmp directly),
     and for ffprobe, ffprobe_shim.py run on that ffmpeg.

Step 3 exists because on 2026-10-13 Docker (colima) was down and nothing else provided ffprobe,
so probe.py failed with "not found: ffprobe" on a perfectly good file. The fallback only engages
when nothing is configured, so an explicit setting always wins.
"""
import os
import pathlib
import shlex
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent


def native_ffmpeg():
    """Path to imageio_ffmpeg's bundled ffmpeg, or None."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe if exe and os.path.exists(exe) else None
    except Exception:
        return None


def tool(env_key, default):
    """Command list for ffmpeg/ffprobe. Same contract as the old per-script tool()."""
    if os.environ.get(env_key):
        # expandvars per token because the documented wrapper is single-quoted:
        #   export FFPROBE='docker run --rm -v "$HOME/videos":/w ...'
        # bash does not re-expand the result of an expansion, so "$HOME" would reach docker.
        return [os.path.expandvars(t) for t in shlex.split(os.environ[env_key])]
    if shutil.which(default):
        return [default]
    native = native_ffmpeg()
    if native and default == "ffmpeg":
        return [native]
    if native and default == "ffprobe":
        return [sys.executable, str(HERE / "ffprobe_shim.py")]
    return [default]  # surfaces as "not found: ffprobe", the old failure


def describe(cmd):
    """One line naming which tool resolved, for the report."""
    j = " ".join(cmd)
    if "ffprobe_shim.py" in j:
        return "ffprobe: shim over native ffmpeg (no Docker, no ffprobe on PATH)"
    if "imageio_ffmpeg" in j:
        return "ffmpeg: native imageio_ffmpeg build"
    if "docker" in j:
        return "docker wrapper: " + cmd[-1]
    return j

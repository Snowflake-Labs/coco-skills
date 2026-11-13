"""Local media helpers for motion-mode audio: FFmpeg only (no ffprobe), no network, no uploads.

Ported from an earlier narrated-video pipeline (Oct 8), which shipped narrated
cuts at -16.0 to -16.4 LUFS. Every level here is measured, never assumed: EBU R128 through ffmpeg's ebur128,
true peak 4x oversampled, and the final check runs on the decoded AAC, not on the mix before encoding.
"""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "gauntlet"))
from ffmpeg_tools import tool  # noqa: E402  (one resolver for the whole lab)

RATE = 48000
_FF = None


def ffmpeg():
    global _FF
    if _FF is None:
        _FF = tool("FFMPEG", "ffmpeg")
    return list(_FF)


def run(arguments, timeout=600, check=True, data=None):
    """ffmpeg with arguments; CompletedProcess with bytes stdout/stderr."""
    command = [*ffmpeg(), "-nostdin", *map(str, arguments)]
    outcome = subprocess.run(command, input=data, capture_output=True, timeout=timeout)
    if check and outcome.returncode:
        raise RuntimeError("ffmpeg failed (%d): %s" % (outcome.returncode, outcome.stderr.decode(errors="replace")[-1500:]))
    return outcome


def sha256(filename):
    digest = hashlib.sha256()
    with open(filename, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(filename, payload):
    path = pathlib.Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)


def media_info(filename):
    """Container duration and streams parsed from `ffmpeg -i` (centisecond duration precision)."""
    report = run(["-hide_banner", "-i", filename], check=False).stderr.decode(errors="replace")
    if "No such file" in report or "Invalid data" in report:
        raise ValueError("%s is not a readable media file." % pathlib.Path(filename).name)
    found = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", report)
    duration = int(found[1]) * 3600 + int(found[2]) * 60 + float(found[3]) if found else None
    streams = []
    for line in report.splitlines():
        match = re.search(r"Stream #\d+:\d+.*?: (Video|Audio|Subtitle|Data): (\w+)", line)
        if not match:
            continue
        stream = {"codec_type": match[1].lower(), "codec_name": match[2]}
        if stream["codec_type"] == "audio":
            rate = re.search(r", (\d+) Hz", line)
            stream.update({"sample_rate": int(rate[1]) if rate else None,
                           "layout": "mono" if " mono" in line else "stereo" if "stereo" in line else "other"})
        streams.append(stream)
    return {"duration_seconds": duration, "streams": streams}


def video_frames(filename):
    """Count video packets by stream copy (exact for a constant-rate H.264 picture)."""
    report = run(["-hide_banner", "-i", filename, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"]).stderr
    frames = re.findall(r"frame=\s*(\d+)", report.decode(errors="replace"))
    return int(frames[-1]) if frames else 0


def stream_hash(filename, stream="0:v:0"):
    """SHA-256 of one stream's packets, independent of the container around it."""
    out = run(["-v", "error", "-i", filename, "-map", stream, "-c", "copy", "-f", "hash", "-hash", "sha256", "-"])
    return out.stdout.decode().strip().split("=", 1)[-1]


def decode(filename, rate=RATE, channels=2, audio_filter=None, stream="0:a:0", start=None, seconds=None):
    """Decode one audio stream to float32 samples shaped (frames, channels). start/seconds trim on the
    decoded timeline (sample-accurate: -ss after -i), so a cut lands where the analysis said it would."""
    arguments = ["-v", "error", "-i", filename]
    if start is not None:
        arguments += ["-ss", "%.6f" % start]
    if seconds is not None:
        arguments += ["-t", "%.6f" % seconds]
    arguments += ["-map", stream, "-vn"]
    if audio_filter:
        arguments += ["-af", audio_filter]
    arguments += ["-ar", rate, "-ac", channels, "-f", "f32le", "-"]
    return np.frombuffer(run(arguments).stdout, dtype="<f4").reshape(-1, channels).copy()


def write_wav(samples, filename, rate=RATE, codec="pcm_s24le"):
    """Write float samples to WAV through ffmpeg (codec='pcm_f32le' cannot clip; 24-bit for library files)."""
    samples = np.ascontiguousarray(samples, dtype="<f4")
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    pathlib.Path(filename).parent.mkdir(parents=True, exist_ok=True)
    run(["-v", "error", "-y", "-f", "f32le", "-ar", rate, "-ac", channels, "-i", "-", "-c:a", codec,
         "-fflags", "+bitexact", filename], data=samples.tobytes())


def _number(text):
    return None if "inf" in text else float(text)


def _ebur128_summary(report):
    summary = report[report.rfind("Summary:"):]
    integrated = re.search(r"I:\s*(-?[\d.]+|-?inf)\s*LUFS", summary)
    lra = re.search(r"LRA:\s*(-?[\d.]+|-?inf)\s*LU\b", summary)
    peak = re.search(r"True peak:\s*Peak:\s*(-?[\d.]+|-?inf)\s*dBFS", summary)
    if not integrated or not peak:
        raise RuntimeError("Could not read the ebur128 summary.")
    return {"integrated_lufs": _number(integrated[1]), "loudness_range_lu": _number(lra[1]) if lra else None,
            "true_peak_dbtp": _number(peak[1])}


def loudness(filename, audio_filter=None, stream="0:a:0"):
    """EBU R128 integrated loudness, loudness range and 4x-oversampled true peak of a file."""
    chain = "%s,ebur128=peak=true" % audio_filter if audio_filter else "ebur128=peak=true"
    report = run(["-hide_banner", "-nostats", "-i", filename, "-map", stream, "-af", chain, "-f", "null", "-"])
    return _ebur128_summary(report.stderr.decode(errors="replace"))


def _pipe(samples, rate, arguments):
    samples = np.ascontiguousarray(samples, dtype="<f4")
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    return run(["-hide_banner", "-nostats", "-f", "f32le", "-ar", rate, "-ac", channels, "-i", "-", *arguments],
               data=samples.tobytes())


def sample_loudness(samples, rate=RATE):
    """loudness() for float samples held in memory. Mono input is measured as mono (no +3 LU stereo copy)."""
    return _ebur128_summary(_pipe(samples, rate, ["-af", "ebur128=peak=true", "-f", "null", "-"]).stderr.decode(errors="replace"))


def filter_samples(samples, audio_filter, rate=RATE):
    """Run float samples through an FFmpeg filter chain in memory; same length and layout back."""
    channels = 1 if np.ndim(samples) == 1 else np.shape(samples)[1]
    out = _pipe(samples, rate, ["-af", audio_filter, "-ar", rate, "-ac", channels, "-f", "f32le", "-"])
    result = np.frombuffer(out.stdout, dtype="<f4").copy()
    result = result.reshape(-1, channels) if np.ndim(samples) == 2 else result
    if len(result) != len(samples):
        raise RuntimeError("The filter changed the length: %d to %d samples." % (len(samples), len(result)))
    return result


def rms_db(samples, window):
    """Windowed RMS in dBFS over all channels; window in samples."""
    samples = samples if samples.ndim == 2 else samples[:, None]
    length = len(samples) // window * window
    frames = samples[:length].reshape(len(samples) // window, window, -1).astype(np.float64)
    return 20 * np.log10(np.maximum(np.sqrt(np.mean(frames ** 2, axis=(1, 2))), 1e-9))


def band_shares(samples, rate=RATE, bands=None):
    """Percent of signal energy per band (Welch-style average of 8192-point frames)."""
    bands = bands or {"below_85_hz": (0, 85), "85_150_hz": (85, 150), "150_800_hz": (150, 800),
                      "800_2200_hz": (800, 2200), "1_4_khz": (1000, 4000), "above_4_khz": (4000, rate / 2)}
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    size = 8192
    if len(mono) < size:
        return {}
    frames = np.lib.stride_tricks.sliding_window_view(mono, size)[::size // 2]
    power = (np.abs(np.fft.rfft(frames * np.hanning(size), axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(size, 1 / rate)
    total = power.sum() or 1.0
    return {name: round(float(power[(freqs >= lo) & (freqs < hi)].sum() / total * 100), 2) for name, (lo, hi) in bands.items()}


def rms(samples):
    return round(float(20 * np.log10(max(np.sqrt(np.mean(np.square(samples, dtype=np.float64))), 1e-9))), 1)


def mux_aac(picture, audio, output, gain_db, limiter_dbfs=None, metadata=None):
    """Copy the picture's video stream and encode one audio file under it: fixed gain, AAC 192k, faststart."""
    chain = "volume=%.4fdB" % gain_db
    if limiter_dbfs is not None:
        # 4x oversampled so the sample-peak limiter also holds the true peak; level=false so it never rides gain.
        chain += (",aresample=192000,alimiter=limit=%.6f:attack=5:release=50:level=false:latency=true,aresample=48000"
                  % (10 ** (limiter_dbfs / 20)))
    meta = []
    for k, v in (metadata or {}).items():
        meta += ["-metadata", "%s=%s" % (k, v)]
    run(["-v", "error", "-y", "-i", picture, "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
         "-af", chain, "-c:a", "aac", "-b:a", "192k", "-ar", RATE, *meta, "-movflags", "+faststart", "-f", "mp4", output])


def master_to_target(picture, audio, output, target=-16.0, tolerance=0.7, ceiling=-1.5, tries=3, strict=True, metadata=None):
    """One measured fixed gain to the target, checked on the decoded AAC. A safety limiter joins the chain only
    when the encoded true peak lands above the ceiling; each retry lowers its ceiling (bounded to `tries`)."""
    source = loudness(audio)
    if source["integrated_lufs"] is None or source["integrated_lufs"] < -70:
        raise ValueError("The audio is silent; there is nothing to master.")
    gain, limiter, attempts, result = target - source["integrated_lufs"], None, [], None
    for _ in range(tries):
        mux_aac(picture, audio, output, gain, limiter, metadata)
        final = loudness(output)
        attempts.append({"gain_db": round(gain, 3), "limiter_ceiling_dbfs": limiter, **final})
        loud_ok = final["integrated_lufs"] is not None and abs(final["integrated_lufs"] - target) <= tolerance
        peak_ok = final["true_peak_dbtp"] is not None and final["true_peak_dbtp"] <= ceiling
        result = {"measured_before_gain": source, "gain_db": round(gain, 3), "safety_limiter_used": limiter is not None,
                  "limiter_ceiling_dbfs": limiter, "final": final, "attempts": attempts, "reached_target": loud_ok and peak_ok}
        if loud_ok and peak_ok:
            return result
        if not peak_ok:
            limiter = round(ceiling - 0.5 if limiter is None else limiter - (final["true_peak_dbtp"] - ceiling) - 0.3, 2)
        if not loud_ok:
            gain += target - final["integrated_lufs"]
    if not strict:
        return result
    raise RuntimeError("Could not reach %s LUFS with true peak at or below %s dBTP in %d tries: %s" % (target, ceiling, tries, attempts))


def faststart(filename):
    """True when the MP4's top-level moov box precedes mdat, so playback starts before the download ends."""
    order = []
    with open(filename, "rb") as handle:
        while len(order) < 64:
            header = handle.read(8)
            if len(header) < 8:
                break
            size, kind = int.from_bytes(header[:4], "big"), header[4:].decode("latin-1")
            if size == 1:
                size = int.from_bytes(handle.read(8), "big") - 8
            order.append(kind)
            if kind in ("moov", "mdat") and {"moov", "mdat"} <= set(order) or size == 0:
                break
            handle.seek(size - 8, 1)
    return "moov" in order and "mdat" in order and order.index("moov") < order.index("mdat")

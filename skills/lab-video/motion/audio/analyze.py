#!/usr/bin/env python3
"""analyze.py: the numbers a music edit needs from a track. Analysis, not a listening review.

    python3 analyze.py TRACK.mp3                 # JSON to stdout
    python3 analyze.py TRACK.mp3 --calibrate     # check against the Oct 8 hand analysis of "Something Elated"

Reports, on the decoded 48 kHz timeline (time = decoded sample index / 48000, which excludes an MP3's
container start offset):
  tempo      constant-tempo fit: a comb over the onset envelope, 60-180 BPM, with a mild prior against octave
             errors; then the downbeat as the beat phase with the most low-frequency attack (4/4 assumed)
  finalHit   the downbeat where the groove stops (the last strong bar is followed by quiet ones) when there is a
             hit on it; an "ending" edit lands this on the call-to-action cut
  ringEnd    the last moment the track is still audible (40 dB under its loudness, or -60 dBFS)
  loudness   integrated LUFS, LRA and true peak; band shares (how much sits in the speech band)
A constant-tempo fit is right for DAW-made electronic tracks and wrong for rubato playing: `fit` reports how
strongly the grid explains the onsets, and anything that matters still needs a listen.
"""
import argparse
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import media as mt  # noqa: E402

SR, NFFT, HOP = 16000, 1024, 160          # 10 ms envelope frames
FPS = SR / HOP
NOTE = "Analysis and level checks, not a listening review. The assistant cannot listen."


def envelopes(mono):
    """Onset strength (log-magnitude spectral flux) overall and below 150 Hz, at 100 frames/s."""
    pad = np.concatenate([np.zeros(NFFT // 2, np.float32), mono, np.zeros(NFFT // 2, np.float32)])
    count = 1 + (len(pad) - NFFT) // HOP
    window = np.hanning(NFFT).astype(np.float32)
    low_bins = int(150 / (SR / NFFT)) + 1
    full, low, prev = np.zeros(count), np.zeros(count), None
    level = np.zeros(count)
    for a in range(0, count, 2048):
        idx = np.arange(a, min(count, a + 2048))
        frames = np.stack([pad[i * HOP:i * HOP + NFFT] for i in idx]) * window
        mag = np.abs(np.fft.rfft(frames, axis=1))
        level[idx] = 10 * np.log10(np.maximum((mag ** 2).mean(axis=1), 1e-12))
        spec = np.log1p(1000 * mag)
        if prev is not None:
            spec_prev = np.vstack([prev[None, :], spec[:-1]])
        else:
            spec_prev = np.vstack([spec[:1], spec[:-1]])
        diff = np.maximum(spec - spec_prev, 0)
        full[idx] = diff.sum(axis=1)
        low[idx] = diff[:, 1:low_bins].sum(axis=1)
        prev = spec[-1]
    return full, low, level


def detrend(env, seconds=1.0):
    k = max(1, int(seconds * FPS))
    smooth = np.convolve(env, np.ones(k) / k, mode="same")
    return np.maximum(env - smooth, 0)


def comb_score(env, bpm, phase_step=0.01):
    """Best mean onset strength on a beat grid at this tempo, and the phase (seconds) that achieves it."""
    period = 60.0 / bpm
    phases = np.arange(0, period, phase_step)
    t = np.arange(len(env)) / FPS
    beats = np.arange(0, t[-1] - period, period)
    grid = phases[:, None] + beats[None, :]
    vals = np.interp(grid, t, env)
    scores = vals.mean(axis=1)
    i = int(np.argmax(scores))
    return float(scores[i]), float(phases[i])


def tempo(env):
    prior = lambda b: np.exp(-0.5 * (np.log2(b / 110.0) / 0.9) ** 2)  # mild: only breaks near-ties between octaves
    coarse = np.arange(60.0, 180.0, 0.25)
    raw = np.array([comb_score(env, b, 0.02)[0] for b in coarse])
    weighted = raw * prior(coarse)
    best = coarse[int(np.argmax(weighted))]
    fine = np.arange(best - 0.4, best + 0.4, 0.005)
    fs = [comb_score(env, b, 0.005) for b in fine]
    k = int(np.argmax([s for s, _ in fs]))
    bpm, phase = float(fine[k]), fs[k][1]
    # Fit strength: on-grid onset strength against the average frame (1.0 means the grid explains nothing).
    fit = fs[k][0] / max(float(env.mean()), 1e-9)
    octave = {"%.2f" % (bpm * m): round(float(comb_score(env, bpm * m, 0.005)[0] / max(env.mean(), 1e-9)), 2)
              for m in (0.5, 2.0) if 50 <= bpm * m <= 220}
    return bpm, phase, round(fit, 2), octave


def beat_levels(level, origin, period):
    t = np.arange(len(level)) / FPS
    n = int((t[-1] - origin) // period)
    edges = origin + np.arange(n + 1) * period
    return edges[:-1], np.array([level[(t >= edges[k]) & (t < edges[k + 1])].mean() for k in range(n)])


def strongest_onset(mono48, centre, reach):
    """Largest log-flux peak within `reach` of `centre`, at 1 ms resolution on the 48 kHz signal (21 ms Hann
    frames every 1 ms, each against the frame 10 ms earlier, stamped midway). Ported from build_bed.py, whose
    in-point it produced. Returns (seconds, candidates) or (None, []) when nothing peaks there."""
    rate, size, hop, lag = mt.RATE, 1024, 48, 10
    first = int(round((centre - reach - 0.05) * rate)) - size // 2
    if first < 0 or first + int((2 * reach + 0.1) * rate) + size >= len(mono48):
        return None, []
    starts = np.arange(first, first + int(round((2 * reach + 0.1) * rate)), hop)
    frames = np.stack([mono48[s:s + size] for s in starts]) * np.hanning(size)
    spectra = np.log1p(1000 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(spectra[lag:] - spectra[:-lag], 0).sum(axis=1)
    times = (starts[lag:] + size / 2 - lag * hop / 2) / rate
    peaks = [k for k in range(1, len(flux) - 1) if flux[k] >= flux[k - 1] and flux[k] > flux[k + 1] and abs(times[k] - centre) <= reach]
    if not peaks:
        return None, []
    ranked = sorted(peaks, key=lambda k: -flux[k])
    return float(times[ranked[0]]), [{"offsetFromGridMs": round(float(times[k] - centre) * 1000, 1),
                                      "fluxVsMedian": round(float(flux[k] / max(np.median(flux), 1e-9)), 2)} for k in ranked[:3]]


def final_hit(env, level, beats, lb, seconds, mono48):
    """Where the groove stops: the biggest drop in the last 40% of the track (one bar before against two beats
    after, at least 6 dB) that then stays down. Calibrated on "Something Elated", whose groove stops on the bar
    84 downbeat into a held chord; a track that only fades out has no final hit, and is faded, not landed."""
    best, where = 0.0, None
    for i in range(4, len(lb) - 2):
        if beats[i] < 0.6 * seconds:
            continue
        drop = lb[i - 4:i].mean() - lb[i:i + 2].mean()
        if drop > best:
            best, where = drop, i
    if where is None or best < 6.0:
        return None
    if lb[where:].mean() > lb[max(0, where - 8):where].mean() - 6.0:
        return None  # it dips and comes back: a break, not an ending
    grid = float(beats[where])
    peak, candidates = strongest_onset(mono48, grid, 0.080)
    return {"seconds": round(peak if peak is not None else grid, 4), "beatIndex": int(where), "gridSeconds": round(grid, 4),
            "dropDb": round(float(best), 1), "offsetFromGridMs": round(((peak or grid) - grid) * 1000, 1),
            "onsetCandidates": candidates}


def downbeat(level, beats, lb, period, hit, beats_per_bar=4):
    """Bar phase. With a final hit, the hit is a "1" (endings land on downbeats): high confidence. Without one,
    the sharpest section changes (one-beat windows) vote for the phase they fall on: low confidence."""
    bar = period * beats_per_bar
    if hit:
        origin = hit["gridSeconds"] % bar
        return (origin - bar if origin > bar / 2 else origin), "high: the groove stops on a downbeat", None
    nov = np.zeros(len(lb))
    for i in range(1, len(lb) - 1):
        nov[i] = abs(lb[i] - lb[i - 1])
    peaks = sorted([i for i in range(2, len(nov) - 2) if nov[i] == nov[i - 2:i + 3].max()], key=lambda i: -nov[i])[:12]
    votes = np.zeros(beats_per_bar)
    for i in peaks:
        votes[i % beats_per_bar] += nov[i]
    j = int(np.argmax(votes))
    origin = beats[j] % bar
    return (origin - bar if origin > bar / 2 else origin), "low: section changes voted; check by ear", [round(float(v), 1) for v in votes]


def ring_end(level, integrated):
    t = np.arange(len(level)) / FPS
    floor = max(integrated - 40.0, -60.0) if integrated is not None else -60.0
    loud = np.flatnonzero(level > floor)
    return round(float(t[loud[-1]]), 3) if len(loud) else None


def analyze(path):
    mono = mt.decode(path, rate=SR, channels=1)[:, 0]
    full, _, level = envelopes(mono)
    env = detrend(full)
    bpm, phase, fit, octave = tempo(env)
    period = 60.0 / bpm
    stereo = mt.decode(path)
    loud = mt.loudness(path)
    seconds = len(stereo) / mt.RATE
    beats, lb = beat_levels(level, phase % period, period)
    hit = final_hit(env, level, beats, lb, seconds, stereo.mean(axis=1))
    bar0, confidence, votes = downbeat(level, beats, lb, period, hit)
    first_sound = np.flatnonzero(level > (loud["integrated_lufs"] or -60) - 40)
    return {
        "note": NOTE,
        "decodedSeconds": round(seconds, 3),
        "timeline": "decoded sample index / 48000 (excludes any container start offset)",
        "tempo": {"bpm": round(bpm, 2), "beatsPerBar": 4, "barSeconds": round(240.0 / bpm, 6),
                  "beatSeconds": round(period, 6), "beatPhaseSeconds": round(phase % period, 4),
                  "bar0Seconds": round(bar0, 4), "barJ": "bar0Seconds + j * barSeconds",
                  "fit": fit, "octaveCheck": octave, "downbeatConfidence": confidence, "downbeatVotes": votes,
                  "method": "comb over the log-flux onset envelope (constant tempo); bar phase from the final hit"},
        "firstSound": round(float(first_sound[0] / FPS), 3) if len(first_sound) else 0.0,
        "finalHit": hit,
        "finalHitBar": int(round((hit["gridSeconds"] - bar0) / (4 * period))) if hit else None,
        "ringEnd": ring_end(level, loud["integrated_lufs"]),
        "loudness": loud,
        "samplePeakDbfs": round(float(20 * np.log10(max(np.abs(stereo).max(), 1e-9))), 2),
        "bandShares": mt.band_shares(stereo),
    }


# The Oct 8 hand analysis of this exact file, kept as the known answer.
CALIBRATION = {"sha256": "644c07861766a0575af960990fff394c7343755ff285dfb594dc2ab0231e6589",
               "bpm": 88.0, "bar0": -0.0205, "groove_stop_bar": 84}


def calibrate(path, report):
    bar = 240.0 / CALIBRATION["bpm"]
    got = report["tempo"]
    dphase = ((got["bar0Seconds"] - CALIBRATION["bar0"] + bar / 2) % bar) - bar / 2
    expected_hit = CALIBRATION["bar0"] + CALIBRATION["groove_stop_bar"] * bar
    hit = (report.get("finalHit") or {}).get("seconds")
    bar_no = report.get("finalHitBar")
    hit_bar = None if bar_no is None else bar_no + round((got["bar0Seconds"] - CALIBRATION["bar0"]) / bar)
    checks = {
        "same file as the hand analysis": mt.sha256(path) == CALIBRATION["sha256"],
        "tempo within 0.1 BPM of 88.0 (got %.2f)" % got["bpm"]: abs(got["bpm"] - 88.0) <= 0.1,
        "downbeat within 40 ms of the hand grid (got %+.0f ms)" % (dphase * 1000): abs(dphase) <= 0.040,
        "final hit within 80 ms of the bar 84 downbeat %.3f s (got %s)" % (expected_hit, hit): hit is not None and abs(hit - expected_hit) <= 0.080,
        "final hit numbered bar 84 on the hand grid (got %s)" % hit_bar: hit_bar == CALIBRATION["groove_stop_bar"],
    }
    for k, ok in checks.items():
        print(("ok    " if ok else "FAIL  ") + k)
    return all(checks.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("track")
    ap.add_argument("--calibrate", action="store_true")
    a = ap.parse_args()
    report = analyze(a.track)
    print(json.dumps(report, indent=2))
    if a.calibrate:
        sys.exit(0 if calibrate(a.track, report) else 1)


if __name__ == "__main__":
    main()

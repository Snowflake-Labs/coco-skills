#!/usr/bin/env python3
"""Warm, laid-back lo-fi/jazz-soundtrack music bed (numpy, original, license-free).
Inspired by the tone of John Bartmann's "Tupac Lives" (CC-licensed FMA track used
only as reference — this output is an original composition): ~96 BPM, F major,
Rhodes-style keys + soft upright bass + brushed drums, gentle swing. Kept quiet.

Usage: python synth_music.py --dur 32.9 --bpm 96 --reveal 17.0 --cta 28.5 --out bed.wav
"""
import numpy as np, wave, argparse

ap = argparse.ArgumentParser()
ap.add_argument("--dur", type=float, default=32.9)
ap.add_argument("--bpm", type=float, default=96.0)
ap.add_argument("--reveal", type=float, default=17.0)
ap.add_argument("--cta", type=float, default=28.5)
ap.add_argument("--level", type=float, default=0.5)   # master peak (quiet bed)
ap.add_argument("--out", default="_bed.wav")
A = ap.parse_args()

SR = 44100
N = int(SR * A.dur)
spb = 60.0 / A.bpm
e8 = spb / 2.0
swing = 0.16 * e8            # laid-back swing on offbeat 8ths
dry = np.zeros(N); dryR = np.zeros(N)
mel = np.zeros(N); melR = np.zeros(N)

def add(buf, bufR, i0, sig, pan=0.0):
    n = len(sig)
    if i0 < 0: sig = sig[-i0:]; i0 = 0; n = len(sig)
    if i0 + n > N: n = N - i0; sig = sig[:n]
    if n <= 0: return
    buf[i0:i0+n]  += sig * (0.5 - pan*0.5)
    bufR[i0:i0+n] += sig * (0.5 + pan*0.5)

def smooth(x, k):
    return x if k < 2 else np.convolve(x, np.ones(k)/k, mode='same')

A4 = 440.0
SEMI = {'C':-9,'C#':-8,'Db':-8,'D':-7,'D#':-6,'Eb':-6,'E':-5,'F':-4,'F#':-3,'Gb':-3,
        'G':-2,'G#':-1,'Ab':-1,'A':0,'A#':1,'Bb':1,'B':2}
def freq(nm, octv): return A4 * (2 ** ((SEMI[nm] + (octv-4)*12) / 12))

def rhodes(chord, dur, gain=0.22):
    """warm electric-piano comp voice: sine + soft harmonics, bell attack, tremolo."""
    n = int(dur*SR); t = np.arange(n)/SR; out = np.zeros(n)
    for nm, octv in chord:
        f = freq(nm, octv)
        v = np.sin(2*np.pi*f*t) + 0.35*np.sin(2*np.pi*2*f*t) + 0.14*np.sin(2*np.pi*3*f*t)
        v += 0.10*np.sin(2*np.pi*4*f*t)*np.exp(-t*9)   # bell-ish attack shimmer
        out += v
    trem = 0.92 + 0.08*np.sin(2*np.pi*5.2*t)           # Rhodes tremolo
    att = np.minimum(1, t*160)
    env = att * np.exp(-t*1.9)
    return smooth(out/len(chord)*env*trem, 3) * gain

def upright(nm, octv, dur, gain=0.34):
    """soft round upright-bass note."""
    n = int(dur*SR); t = np.arange(n)/SR; f = freq(nm, octv)
    v = np.sin(2*np.pi*f*t) + 0.16*np.sin(2*np.pi*2*f*t)
    env = np.minimum(1, t*55) * np.exp(-t*3.0)
    return smooth(v*env, 3) * gain

def vibe(nm, octv, gain=0.18, dec=0.6):
    """vibraphone/marimba sprinkle."""
    n = int(dec*SR); t = np.arange(n)/SR; f = freq(nm, octv)
    x = np.sin(2*np.pi*f*t) + 0.3*np.sin(2*np.pi*2*f*t)
    trem = 0.9+0.1*np.sin(2*np.pi*5*t)
    return x*np.minimum(1,t*180)*np.exp(-t/(dec*0.5))*trem*gain

def kick(gain=0.42):
    n = int(0.28*SR); t = np.arange(n)/SR
    f = 88*np.exp(-t*22)+46
    return smooth(np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t*10), 3)*gain

def brush(gain=0.06):
    """brushed-snare 'chh' — soft, heavily lowpassed noise swell."""
    n = int(0.14*SR); t = np.arange(n)/SR
    x = np.random.randn(n)*np.exp(-t*22)
    return smooth(x, 13)*gain

def ride(gain=0.035):
    n = int(0.06*SR); t = np.arange(n)/SR
    x = np.random.randn(n)*np.exp(-t/0.02)
    return smooth(x, 9)*gain

def chime_run(t0, chord, gain=0.14):
    seq = [(nm,octv+1) for nm,octv in chord] + [(chord[0][0],chord[0][1]+2)]
    for j,(nm,octv) in enumerate(seq):
        add(mel, melR, int((t0+j*0.10)*SR), vibe(nm,octv,gain=gain*(0.6+0.08*j),dec=0.7), pan=(-0.25 if j%2 else 0.25))

def bell_accent(t0, chord, gain=0.18):
    for nm,octv in chord:
        add(mel, melR, int(t0*SR), vibe(nm,octv,gain=gain,dec=1.2))

# ---- F major jazzy progression: Fmaj7 - Dm7 - Bbmaj7 - C7 ----
F  = [('F',3),('A',3),('C',4),('E',4)]
Dm = [('D',3),('F',3),('A',3),('C',4)]
Bb = [('Bb',2),('D',3),('F',3),('A',3)]
C7 = [('C',3),('E',3),('G',3),('Bb',3)]
BASS = {'F':('F',2),'Dm':('D',2),'Bb':('Bb',1),'C7':('C',2)}
def prog_at(t):
    # 2-beat (half-bar) chord changes cycling F Dm Bb C7
    seq = [('F',F),('Dm',Dm),('Bb',Bb),('C7',C7)]
    idx = int(t/(spb*2)) % 4
    return seq[idx][0], seq[idx][1]

def energy(t):
    if t < A.reveal-13.0: return 0.22     # intro: rhodes + bass, no drums
    if t < A.reveal-3.0:  return 0.5      # brushes + ride in
    if t < A.reveal:      return 0.4      # gentle lift
    if t < A.cta:         return 0.75     # fuller, vibe melody
    if t > A.dur-1.8:     return 0.0
    return 0.6

def gtime(k):
    t = k*e8
    return t + (swing if k % 2 == 1 else 0)

# ---- schedule ----
n8 = int(A.dur/e8)
for k in range(n8):
    t = gtime(k); i0 = int(t*SR)
    en = energy(t); bib = k % 8
    name, chord = prog_at(t)
    # Rhodes comp: beat 1, and-of-2 (idx3), beat 3 — jazzy syncopation
    if bib in (0,3,4):
        add(mel, melR, i0, rhodes(chord, spb*1.2, gain=0.18+0.05*en), pan=0.0)
    # walking upright bass on each quarter (idx 0,2,4,6)
    if en > 0.3 and bib in (0,2,4,6):
        root, ro = BASS[name]
        # walk: root, then chord tones for movement
        pick = {0:(root,ro),2:(chord[2][0],chord[2][1]-1),4:(chord[1][0],chord[1][1]-1),6:(chord[3][0],chord[3][1]-1)}[bib]
        add(dry, dryR, i0, upright(pick[0], pick[1], spb*0.9, gain=0.30*min(1,en+0.3)))
    # brushed drums (quiet): soft kick 1&3, brush 2&4, ride on 8ths
    if en > 0.4:
        if bib == 0: add(dry, dryR, i0, kick(0.40))
        if bib == 4: add(dry, dryR, i0, kick(0.30))
        if bib in (2,6): add(dry, dryR, i0, brush(0.06))
        if bib % 2 == 1: add(dry, dryR, i0, ride(0.03))
    # sparse vibraphone melody in the bright section
    if en >= 0.7 and bib in (0,3,6):
        nm, octv = chord[(k//2) % len(chord)]
        add(mel, melR, i0, vibe(nm, octv+1, gain=0.12), pan=(-0.2 if k%2 else 0.2))

# intro vibe sprinkle (hook)
for bt,(nm,oc,pn) in zip([0.6,1.5,2.4],[('C',5,-.25),('A',4,.25),('F',5,0)]):
    add(mel, melR, int(bt*SR), vibe(nm,oc,gain=0.14,dec=0.8), pan=pn)

chime_run(A.reveal-0.8, prog_at(A.reveal)[1])
bell_accent(A.reveal, prog_at(A.reveal)[1], gain=0.16)
bell_accent(A.cta, prog_at(A.cta)[1], gain=0.13)

# ---- light warm reverb on melodic bus ----
def reverb(x):
    o = x.copy()
    for d,g in [(0.043,0.28),(0.067,0.20),(0.097,0.13),(0.131,0.09)]:
        di = int(d*SR); o[di:] += x[:-di]*g
    return o
mel = reverb(mel); melR = reverb(melR)

L = dry+mel; R = dryR+melR
L = smooth(L,3); R = smooth(R,3)          # warm master
fi = int(0.3*SR); fo = int(2.0*SR)
for ch in (L,R):
    ch[:fi] *= np.linspace(0,1,fi)
    ch[-fo:] *= np.linspace(1,0,fo)
peak = max(np.max(np.abs(L)), np.max(np.abs(R)), 1e-6)
g = A.level/peak; L*=g; R*=g              # quiet bed

data = np.empty((N,2), dtype=np.int16)
data[:,0] = np.clip(L,-1,1)*32767
data[:,1] = np.clip(R,-1,1)*32767
with wave.open(A.out,'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes(data.tobytes())
print(f"WROTE {A.out}  {A.dur}s  {A.bpm}bpm  peak={A.level}")

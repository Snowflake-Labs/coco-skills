# Audio: the layer that was silently broken for months

Every video produced by this pipeline before the fix below shipped **about 12 dB too quiet**. Nobody
noticed, because a quiet video plays fine — you just turn it up. That is what makes audio the most
dangerous layer: its defects do not look like defects.

## The `amix` bug — read this before writing any filter graph

`amix` **divides every input by `1/n` by default.** With narration clips `adelay`ed into
non-overlapping sections plus a music bed, that division is pure loss: nothing is actually
overlapping, so there is no summing to protect against.

```
amix=inputs=8:duration=longest:dropout_transition=0             # ← -18 dB, silently
amix=inputs=8:duration=longest:dropout_transition=0:normalize=0 # ← correct
```

With 8 inputs that is roughly 18 dB of attenuation, which landed finished videos at **-27.5 LUFS
against a -16 LUFS web norm**. `normalize=0` is not an optimisation. It is required.

## Gain staging: measure, then apply a fixed gain

Two passes, deliberately:

1. Build the mix chain, append `ebur128=peak=true`, run to `-f null` and parse the integrated `I:`.
2. Compute `gain_db = clamp(TARGET_LUFS - measured, -6, +18)` and apply that fixed gain. If peak
   protection is needed, set `alimiter` auto-level off and compensate its latency. Recheck the encoded
   result's true peak; a sample ceiling alone does not establish true-peak headroom.

`TARGET_LUFS = -16.0` for web/Slack delivery with narration. **Music only: -22 LUFS.** At -16 a dense
music-only bed (0.7 LU loudness range) came across "about 2x as loud as it should be" (user, 2026-10-13);
with no voice to set the level, the music should sit about 6 dB lower.

Avoid dynamic whole-mix normalization that raises the bed during quiet speech. Measured fixed gain is
predictable. FFmpeg `loudnorm` also has a measured linear mode; the failure is uncontrolled gain riding,
not the mere presence of that filter.

Verify the finished file rather than assuming: four narrated videos made this way measure exactly -16.0 LUFS with
peak ~-2 dBFS, and the shipped reference trailer measures **-15.3 LUFS** — both inside tolerance.
`gauntlet/probe.py` reports this and fails outside ±2 LUFS.

## Built: Voice Studio, the line splitter and the music library (motion mode)

Everything below is enforced by `../motion/audio/` (map: `../motion/audio/README.md`). Use it rather than
hand-writing a filter graph:

- **Voice Studio** (`/voice` on the review server) records the user's own voice against the muted picture;
  `voice.py` splits each take into one clip per scene line and places each on its own cut. TTS is TEST_ONLY.
- **The music library** (`library.py`) reads the licence from each track's own FMA page. On Broke For Free's
  "Something EP" only "Something Elated" is CC BY; its four siblings are CC BY-NC, so an assumption made
  from the album would have been wrong four times out of five there.
- **`mix.py`** keeps the Oct 8 mixer's rules (duck keyed from the voice alone, plain sum, master measured on
  the decoded AAC, picture stream copied) and adds the music-only target above.

## Narration (TTS)

**The trap: `say -v <voice-that-does-not-exist>` exits 0 and quietly uses the system default.** A
missing voice produces a complete, successful-looking render in the wrong voice — and rendering four
clients in a batch makes all four wrong with four successful exit codes.

The guard is mandatory: parse `say -v ?` and hard-fail if the requested voice is absent.

- An installed **`Zoe (Premium)`** voice was preferred in one audition, not established as a universal
  best voice. Compare a short identical passage before rendering a full script.
- Voice names contain spaces and parentheses. Always quote them.
- Output is **22.05 kHz mono** — the voice model's native rate. You cannot exceed it by asking.
- Only two prosody controls measurably help: `[[slnc N]]` for pauses and `[[rate N]]`.

**Rights gate:** Apple's macOS Tahoe agreement section 2.F restricts system-voice recordings to
personal, non-commercial use and excludes commercial/public sharing. Verify current terms at
https://www.apple.com/legal/sla/docs/macOSTahoe.pdf before distribution. A user-approved local scratch
audition is not evidence of customer-delivery rights. Use the user's own recording or a voice with an
appropriate license for a distributed film. A requested `say` rate is not a measured speaking rate;
check actual duration, and do not describe upsampling as better voice fidelity.

## Music: licence first, then sound

Start with the budget and distribution. Free, professionally made music can be suitable; do not
assume a paid subscription or a synthesized bed is required. Synthetic beds remain an offline
fallback when the user likes the sound, not the quality default.

- Verify each track's actual license. FMA is a catalog of different licenses, not blanket permission.
- CC BY permits commercial adaptation with appropriate credit, a license link and change disclosure.
  NC is unsuitable for a commercial customer demo; ND is unsuitable for music synchronized/adapted
  into the film without separate permission. Check the precise license version.
- Suno's free tier was non-commercial when checked on 2026-09-21; later payment did not automatically
  grant retroactive rights. Recheck https://help.suno.com/en/articles/2425729 rather than treating all
  AI-generated music as free of licensing obligations.
- Paid creator plans may exclude large-company work. Match the licensee, audience and distribution,
  not just the words "commercial use" on a pricing card.
- Audition before committing. Preserve the source page/license and attach attribution to the video
  so it survives forwarding. Do not claim to have listened based on genre tags or waveform analysis.
- Edit around a musical phrase and the track's ending; an arbitrary hard trim or long fade is not
  automatically conclusive. Beat/onset analysis is a useful check, not a substitute for listening.

Verified examples, not permanent recommendations: Broke For Free's "Night Owl" (CC BY 3.0) and
"Something Elated" (CC BY 3.0 US) on Free Music Archive. Recheck the individual page before reuse.

If you use a real track it must be **commercially cleared** for an external send. Specifically:
A local `_track.mp3` is **CC BY-NC-ND** despite its album being called "Public Domain
Soundtrack Music". NonCommercial rules out a sales video, and **NoDerivatives rules out trimming or
looping it** — it fails twice. Internal review only.

The engine's `scripts/synth_music.py` is a last-resort offline fallback; its output reads thin.

## Replacing narration with a human recording

Preserve the edited source, hash it, and measure duration, loudness and peaks before changing it.
If it derives from the original timed take, compare the speech-energy envelopes by scene: matching
duration alone does not establish alignment. Correlation establishes timing, not transcript accuracy.
Do not speed up the speaker to repair a poorly paced edit. Check the tail before trimming room tone.

Measure the voice after its final channel layout. Duplicating mono equally into stereo changes the
integrated loudness measurement by about 3 LU without increasing sample peaks. Avoid unnecessary
denoising/EQ on an Audacity-cleaned source. Apply modest gain and only the peak control needed, then
report how often it acted instead of calling every limiter "gentle".

Mix music below the actual human speech, not below the old TTS level. A useful starting point is
roughly 15-20 dB lower during spoken passages, adjusted by content and listening. If ducking is needed,
use a hold and slow release so music does not swell between words. Measure the final AAC-encoded mix
around -16 LUFS with approximately -1.5 to -2 dB true-peak headroom. Keep voice, music and mix stems.
Copy the verified picture stream when only audio changes, compare its hash, and update or remove
stale TTS-timed subtitles rather than imply they match the new recording.

### The frequency lesson

The first synthesised bed rooted its chords at 52–62 Hz. Measured: **85% of energy below 160 Hz.** A
sub-bass hum — inaudible on a laptop or phone speaker, while still masking the body of the voice.

Corrected targets, which are what those keys encode:

- Roots around **A2–C#3** (~104–124 Hz), one octave up from the first attempt
- Bed energy centred **150–800 Hz** — above the rumble, below the intelligibility band
- Highpass ~**85 Hz** (a laptop speaker reproduces nothing below ~80 Hz, so that energy is pure cost)
- Lowpass ~**2200 Hz** to stay out of **1–4 kHz**, which carries speech intelligibility
- Sanity check: ~11% of energy under 100 Hz, **< 0.3%** in 1–4 kHz

## Silence is a legitimate choice

A silent, caption-driven video sidesteps licensing entirely and plays correctly in Slack's muted
autoplay — which is how most viewers will see it regardless. The reference trailer shipped silent first
and lost nothing.

If a video only works with narration, the on-screen copy is usually not carrying its weight.

## See also

- `agent-created/media/audio-mixdown-ffmpeg/` — filter-graph recipes and loudness detail
- `delivery.md` — why a local player is not a proxy for Slack
- `../gauntlet/probe.py` — measures the finished file

# The gauntlet

```
GATE THE SPEC → RENDER → PROBE → CONTACT SHEET → LOOK → CRITIQUE → CUT → RENDER AGAIN
```

Four tools. **Three measure; one is a protocol you read with your eyes open.** The split is
deliberate: whether a video is worth its runtime is a judgement, and a tool that scored it
would produce a number nobody should trust.

Gate before you render. The spec check costs a second; render and encode cost minutes.

```bash
# LAB is the folder holding this skill's SKILL.md (a user-level install: ~/.snowflake/cortex/skills/lab-video)
export FFMPEG="docker run --rm -v $HOME/videos:/w linuxserver/ffmpeg:latest"
export FFPROBE="docker run --rm -v $HOME/videos:/w --entrypoint ffprobe linuxserver/ffmpeg:latest"

python3 $LAB/gauntlet/copy_gate.py spec.json                    # Tier 1-2, on the spec
node    $LAB/engine/scripts/render.mjs --scenes spec.json --out _t.webm   # prints TOTAL_S
bash    $LAB/engine/scripts/encode.sh _t.webm none out.mp4 43.7
python3 $LAB/gauntlet/probe.py out.mp4 --expect-sec 43.7        # Tier 1-2, on the file
python3 $LAB/gauntlet/contactsheet.py out.mp4 --frames 12 --out sheet.png   # Tier 3
```

Then **open `sheet.png` and read `critique.md` against it.**

| Tier | Question | Tool | Automatable |
|---|---|---|---|
| **1 RUNS** | Did it render, and is the file well-formed? | `probe.py` | fully |
| **2 CORRECT** | Is it the video you specified — full length, right pixels, right loudness? | `copy_gate.py` + `probe.py` | mostly |
| **3 READS** | Can a viewer follow it on one pass? | `contactsheet.py` → **look at it** | partly |
| **4 LANDS** | Does it earn its runtime, and would you send it? | `critique.md` | no |

---

## copy_gate.py

Pure stdlib, sub-second, no render. Runtime budget and beat lengths, headline word count,
rounded count-up targets, one-ask, internal vocabulary, and brand lockup.

**Two things it deliberately does not do.** It does not gate on total on-frame words — a
diagram's labels are *scanned*, not read, and gating that number rejected 8 of 10 scenes of a
trailer that worked. And it splits vocabulary by severity rather than banning a word list,
because `credits` and `run-rate` are correct in a cost conversation.

## probe.py

Everything about the finished file that a player will not tell you: duration against the
spec, `pix_fmt`, codec, faststart atom order, integrated loudness, dimensions, bitrate.

**`--expect-sec` is the highest-value flag in this lab.** It is the only automatic detector of
a truncated render, where the output is well-formed, playable, and missing its CTA. Without
it the duration check degrades to an advisory and says so.

Needs **`FFPROBE` set separately** from `FFMPEG`: a docker `FFMPEG` wrapper has ffmpeg as its
entrypoint, and ffprobe needs `--entrypoint ffprobe`.

## contactsheet.py

Samples N frames onto one grid with the timestamp burned into each cell, so a defect you spot
comes with the second to fix it. One ffmpeg pass; no browser.

It returns **no verdict**. A green run means the sheet exists. The looking is the check.

## critique.md

Tier 4, five passes: one-pass comprehension, does it earn its runtime, frame-by-frame against
the sheet, honesty of the claim, multi-perspective. Not optional — the defects it catches are
invisible to every tool above.

---

## Calibration: measured in both directions

A gate that has never failed is not known to work, and a gate that reddens on known-good work
gets ignored. Both directions are checked against real artifacts.

| Case | Expected | Measured |
|---|---|---|
| `engine/references/reference.scenes.json` (shipped) | pass | **0 fail, 2 advisory** |
| same spec, 4 defects injected | fail | **7 fail** |
| the shipped reference trailer (not included) | pass | **0 fail** — 43.70s vs spec 43.7, yuv420p, h264, moov@36, −15.3 LUFS |
| `_bad.mp4` (truncated + `yuv444p` + no faststart) | 3 fails | **exactly 3** |

Regenerate the known-bad file rather than trusting a stale copy:

```bash
python3 $LAB/gauntlet/probe.py --make-bad /tmp/_bad.mp4
python3 $LAB/gauntlet/probe.py /tmp/_bad.mp4 --expect-sec 43.7   # want 3 fails
```

And check the spec gates still fire, one defect at a time:

```bash
for d in rounded_stat placeholder_brand internal_jargon bloat missing_dur; do
  python3 $LAB/gauntlet/copy_gate.py spec.json --defect $d
done
```

`fixtures/inject.py` mutates the *current* known-good spec rather than shipping a broken one,
so the fixture cannot drift away from the real thing. An unknown defect name raises instead of
no-opping — a fixture that silently ignores a typo reports a green gate for a defect that was
never injected.

## What this loop caught while it was being built

Two defects, in the tools themselves, both of which **exited 0**:

| Found by | Defect |
|---|---|
| running it | The documented `FFPROBE` export was single-quoted, so `$HOME` never expanded and docker rejected the volume name. Bash does not re-expand the result of an expansion. Fixed in `delivery.md`; both scripts now `expandvars` each token. |
| **looking at the sheet** | `contactsheet.py` rendered 12 cells that were all the *same frame*, labelled `t=1.8s` twelve times. `-ss` positions an input but does not truncate it, so `concat` got 12 full-length segments and `tile` filled the grid with consecutive frames from the first one. Fixed with `trim=end_frame=1`. |

The second one is the whole argument for Tier 3 existing. It was well-formed, correctly sized,
exited 0, and was completely wrong — and no automated check in this directory would have
noticed, because none of them looks at pixels.

## See also

- `../patterns/anti-patterns.md` — the thirteen, and which are mechanically gated
- `../patterns/delivery.md` — encoding flags, the ffmpeg options, and the colima trap
- `../patterns/audio.md` — why loudness is measured and never judged by ear

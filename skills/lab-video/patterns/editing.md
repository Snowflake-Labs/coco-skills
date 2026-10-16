# Editing: time is the medium

The one thing that makes video different from every other artifact in these labs: **the viewer cannot
scroll back.** A deck reader who misses a point re-reads it. A video viewer who misses a point has lost
it, and if the video autoplays muted in Slack they have lost it silently.

So the editing discipline is not stylistic. It is the only mechanism you have for being understood once.

## The numbers that hold

Measured across 34 scenes of three shipped videos — anchors, not preferences:

| Quantity | Value | Why |
|---|---|---|
| **Headline words** (`lines`) | p50 **4**, max **6** | The tight one. This is what the gate enforces |
| Total on-frame tokens | p50 20, max **47** | Diagram labels are *scanned*, not read — do not gate on this |
| Seconds per beat | **3–6** (up to ~6.0 observed) | Under 3 is unreadable; over 6 without a change is dead air |
| Total runtime | **30–45s**, hard cap **2 min** | Attention, and Slack inline autoplay |
| Hook | resolved in **< 5s** | The scroll decision is made here |
| Distinct CTAs | **1** | Two asks is zero asks |
| Ideas per scene | **1** | If a scene needs "and", it is two scenes |

Calibration point: the shipped reference proposal trailer is **43.7s across 10 scenes** (4.4s/beat).

**Why the headline is the gate and the total is not.** The first version of `copy_gate.py` counted every
token on the frame against a limit of 9 and failed 8 of 10 scenes of a video that shipped and worked. An
`arch` scene legitimately carries 26 label words because they are spatially separated and glanced at.
The FAIL threshold now sits at 8 headline words — above the worst good case of 6, not at the ideal.

## One spec, two runtimes

`--dur-scale <n>` multiplies every `dur`. A trailer and a talk-track bed want the *same* content at
*different* pacing, and maintaining two specs guarantees they diverge.

```bash
node engine/scripts/render.mjs --scenes spec.json --out tight.webm                 # 43.7s trailer
node engine/scripts/render.mjs --scenes spec.json --dur-scale 2.4 --out bed.webm   # ~105s bed
```

**The 2-minute cap does not apply to a talk-track bed.** Its length is set by the script being spoken
over it. A shipped presenter-layout video is 132.4s and that is correct.

## Presenter layout

`--layout presenter` confines content to the left 1248px and leaves the right 672px for a talking head.

- It is a **type-scale override, not `transform:scale()`**. Scaling would drop `snowsight` monospace to
  ~10px — the exact content that scene exists to show.
- **`--guides` burns the camera-zone outline into the frame.** Composition aid only. Never ship a render
  made with it.

Verify the zone rather than trusting the flag: `gauntlet/probe.py --presenter` crops the right 672px and
reads luma max. Measured on real files — A shipped presenter-layout video (presenter layout) reads **YMAX 67**;
the reference trailer (centre layout) reads **171** and correctly fails.

## Present mode: the deck that is also a video

`--emit-html <path> --mode present` writes self-contained HTML where ←/→/space/click advance and `r`
replays the beat; each scene animates on entry then holds. Logos are inlined and there are no external
fonts, so it works offline over `file://`. That is how you present live with a camera in the reserved
zone, from the same spec that produces the video.

`--mode present` is **rejected for a video render** — it never terminates.

## Structure that works

```
hook        premise in one line, logo, < 5s
problem     the current state, named concretely
turn        the pivot — "what if that weren't there"
feature     what the thing is (chips, not prose)
proof       a number, or the product surface
stat        the payoff, counted up
cta         one ask, logo lockup
```

A shape that has survived several videos, not a template. The `turn` is the beat most often missing and
the one that makes the rest land: without it a video is a list of features.

## Counting up numbers

`stat` animates a count-up — the most effective motion in a business video, because the viewer watches a
number *arrive* rather than reading it.

**`stat.to` must be an integer.** The count-up calls `Math.round()`, so `99.8` renders as `100`, silently
overstating a measured figure. Decimals go in `sub`/`label`, which are never rounded. `bars` is exempt:
`from`/`to` drive pixel widths and labels are static text.

## Honesty, mechanically

Do not put a claim on screen you cannot defend. Nuance belongs in the follow-up email — but that is an
argument for *cutting the claim*, not for shipping it unqualified and explaining later.

## See also

- `motion.md` — how a beat animates
- `anti-patterns.md` — the rounded stat, the two-CTA close
- `../gauntlet/copy_gate.py` — enforces headline budget, integer stats, single CTA

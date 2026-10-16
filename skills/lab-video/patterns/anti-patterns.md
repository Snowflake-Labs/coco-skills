# Anti-patterns

Each of these happened. Several shipped. Ordered by how expensive they were, not by how likely they look.

## 1. The 12-dB-quiet mix

`amix` without `normalize=0` divided every input by `1/n`, putting finished videos at **-27.5 LUFS**
against a -16 LUFS norm. It affected **every video the pipeline had ever produced** and nobody noticed,
because a quiet video plays — you just turn it up.

**Why it is first:** invisible, systemic and retroactive. The class is "a defect that degrades quality
without producing a symptom". Guard it by *measuring* (`ebur128`), never by listening.

## 2. The truncated final scene

A stale `render.mjs` with a hardcoded 120s wait cap closed the page mid-run on longer specs. The output
was a valid, playable MP4 **missing its ending** — and the ending is the CTA.

Encoding with `-t TOTAL_S` afterwards does not restore frames that were never captured. Always `ffprobe`
duration against the spec's own `TOTAL_S`.

## 3. The rounded stat

`stat.to: 99.8` renders as **`100`** because the count-up calls `Math.round()`. A measured 99.8% precision
became a claim of 100%, on screen, in a customer-facing video.

The defect is not the rounding — it is that rounding *up* on a measured figure manufactures a stronger
claim than the evidence supports.

## 4. The fabricated product surface

A `snowsight` scene renders product UI faithfully enough to be indistinguishable from a screenshot.
Showing one while implying the run happened is the sharpest available way to mislead with this toolchain.

Depict the surface; do not imply the execution. If a figure came from a demo or labelled dataset, caption
it on the scene — not in the email, because the email is not what gets forwarded.

## 5. The uncleared track

`_track.mp3` is **CC BY-NC-ND** despite its album being titled "Public Domain Soundtrack Music". It fails
twice for a sales video: NonCommercial rules out the use, and **NoDerivatives rules out trimming or
looping it at all**. A licence that reads permissive in the filename is not a licence.

## 6. The wrong-voice batch

`say -v <missing voice>` **exits 0** and silently uses the system default. Rendering four clients in a
batch produced four wrong-voice narrations with four successful exit codes.

Anything that "falls back gracefully" on a missing asset needs an explicit existence check, because
graceful degradation and success are indistinguishable to a caller that only reads exit codes.

## 7. The placeholder brand

A brand preset without `logoLeftFile` renders the `DEFAULT_BRAND` placeholder **`Your Brand`** on the
hook and CTA — the two most-watched beats.

Worse than a normal typo, because it is the HTML entity `Your&nbsp;Brand`, so **grepping for "Your Brand"
does not find it.** Two of the five brand presets the engine once shipped had this defect latent until
`copy_gate.py` was written.

And the trap inside the trap: grepping the *emitted HTML* for the entity finds it every time, because it
appears in the template's own `DEFAULT_BRAND` source line. The real check is whether the injected
`window.__BRAND__` carries a `logoLeft` key at all.

## 8. The burned-in guide

`--guides` draws the camera-zone outline into the pixels. A render made with it cannot be cleaned up
afterwards — only re-rendered.

## 9. The black frame

Wrong `pix_fmt` produces a file that plays perfectly in VLC, passes `ffprobe`, and shows **black in
Slack**. Verifying by opening the file locally is not verification.

## 10. The sub-bass hum

The first synthesised bed rooted chords at 52–62 Hz: **85% of energy below 160 Hz.** Inaudible on a
laptop speaker while still masking the voice — so it degraded intelligibility and contributed nothing.
Bed energy belongs at 150–800 Hz.

## 11. Internal vocabulary on screen

Copy generated from internal telemetry leaks internal framing: quota, commission, expansion value,
propensity, whitespace, workload lift, and the names of individuals being targeted. Once burned into a
frame it cannot be redacted.

**But calibrate this by audience.** `credits`, `consumption` and `run-rate` are *legitimate* in a cost
conversation — the shipped reference trailer used them correctly and deliberately. Treating them as banned
made the gate fail known-good work, which is why `copy_gate.py` splits hard failures from advisories.

## 12. The two-minute video that should have been forty seconds

The most common structural failure. Symptoms: more than one CTA, a beat that needs the word "and", a
feature list where a `turn` should be, narration explaining what the visual already said.

Cutting is the highest-leverage edit available and the one least often taken.

## 13. Reviewing a video by watching it once

You cannot catch a 0.3s type collision, an off-brand frame or a mid-transition state at playback speed —
attention is on the motion. Sample frames onto a contact sheet and look at the grid.

This is the video-specific form of a general rule: a green harness is permission to start judging, not a verdict.

## 14. The hidden overlay that still eats every click

An element with `hidden` and an author rule that sets `display` (`.countdown{display:grid}`) is **not
hidden**: the author rule beats the browser's `[hidden]{display:none}`. Voice Studio's closed countdown
overlay covered the page and blocked every button; the review page hit the same thing with `video[hidden]`.
A browser drive caught both; a screenshot alone did not, because the overlay was transparent at rest. Any
page with a `hidden` toggle needs `[hidden]{display:none!important}`, and a drive that clicks through it.

## See also

- `../gauntlet/README.md` — which of these are mechanically gated
- `../gauntlet/fixtures/inject.py` — 3, 7, 11 and 12 reproduced on demand

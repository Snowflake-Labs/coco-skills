# Narrated Product Walkthroughs

Use this branch for a short, customer-perspective film with real app footage and replaceable narration.
The story follows a user's decisions, not a tour of every available control.

## Overview or walkthrough: decide first

These are different deliverables, and a reviewer will tell you which one you made. A 1:56 how-to for a
clean room team (16 beats, one per step, the step 2 SQL at 14 lines and 21 px) came back as *"useful as an
overview, not detailed enough for a walkthrough… it's not possible to identify everything happening in the
code snippets"* (2026-10-27). The fix was a different kind of video, not a polish:

| | Overview | Walkthrough (`"kind": "walkthrough"` in `video.json`) |
|---|---|---|
| Job | the viewer understands the idea | the viewer can do the steps afterwards |
| Beats per step | 1 | 2: **what you do**, then **how it works / what you see** |
| Beat length | 4-8 s | 10-15 s; the gate allows 18 s |
| Code on screen | a glimpse is fine | at most ~9 lines, 22-25 px, full width (`code.html` `layout: "wide"`), highlights paced to the narration (`first`, `gap` 2.5-3 s) |
| Runtime | under 2 min (gate cap 120 s) | 3-5 min (gate cap 300 s, soft 180 s) |

Code excerpts are quoted verbatim from what ran (generate them from the source, don't retype them); mark
any elision with an in-language comment (`-- FROM and JOIN as in email_match`, `# data_providers: ...`)
and say "Excerpt" on the card. Keep lines under ~90 characters so a callout still fits after them.

**Explain the mechanism the audience is worried about, not just its result.** "The crosswalk stays inside"
is a claim; a privacy and security audience asks *how*. Give that its own beat, built from facts already in
the source (for the clean room: hashed before it arrives → the room sees only listed columns → offerings
are template-only → the result lands in a protected schema and only its name comes back). The reviewer
asked for exactly this on the step where data "isn't readable".

**Write it from the viewer's seat.** Frame it as the steps *this* customer takes ("Step one, in your own
account: register a data offering. Roku does the same."), not the steps "taken overall" by named parties.
Second person in headlines, subs and narration; the partner stays third person; diagram labels can keep
party names for clarity. A POV rewrite touches every line, so it means re-recording every line: decide POV
before the first recording session.

## Inputs and decisions

Confirm the actual app/version, audience, maximum runtime, must-show workflows, current vs simulated
capabilities, music budget, narration choice and intended distribution. Match a reference film's
storytelling qualities without copying its claims or assets. A two-minute ceiling is not a reason to
accelerate every gesture. Cut actions or rebalance scene time instead.

Keep a scene timeline with spoken text, capture actions, source/evidence notes and duration. Brief
motion graphics can frame the story; real product footage supports functionality claims. A chart
lifted out of the application can break continuity even when the numbers are genuine. Use the actual
question/answer/pin workflow and the intended tab/dialog path.

Show meaningful findings before animation: revenue, traffic, sentiment or operational exceptions,
followed by one concrete product/store drilldown. Keep exact-vs-broader finding distinctions intact.
Persona scope is not proof of RBAC; animation is not proof of live ingestion; approval UI is not proof
of operational execution. Put necessary qualifications on the scene.

## Capture and edit

1. Load and settle the app before recording usable frames. Use an isolated local synthetic session
   where appropriate; avoid writes/messages in the deployed account unless authorized.
2. Record deliberate actions and holds at natural speed. Track navigation and page errors.
3. Assemble clips without exposing page loads, decoder blanks, or stale first frames. Prefer simple
   deterministic concat when a browser composite adds instability. See `capture.md`.
4. Inspect boundaries and action moments densely, not just one midpoint per scene. Check blank/bright
   flashes and continuity as well as duration and encoding.
5. Preserve previous review cuts. A source edit and a rerecording should be attributable, not overwrite
   the user's only approved version.

## Audio audition and recording

Audition a short identical passage before committing to a full TTS voice. Check commercial voice rights
and each music track's actual license. Free licensed music may be the right answer; procedural beds
and paid libraries are options, not defaults. Credit music in the delivered artifact. Preserve a
musical phrase/ending rather than cut a continuous groove arbitrarily.

For a user's own voice, a minimal local recorder should provide large script text, visual countdown,
current/next section, timing cues, rehearsal without microphone, pause/resume, stop, playback, and
download. No cue sound or backing music during capture. Ask for the microphone only after a click,
release it on stop/error, support denial/cancellation, and warn before abandoning unsaved takes.
Time-based highlights are not speech recognition. Slower rehearsal modes require later video retiming.

If the user wants MP3, encode actual MP3 rather than rename WebM. A loopback converter can use local
FFmpeg, with bounded input/time/concurrency, same-origin protection, network-disabled encoding,
temporary-file cleanup, and original-audio fallback plus retry. Never use an arbitrary cloud converter.
Test failure recovery as well as success. An automated fake-mic test does not prove actual hardware
permissions work; keep rehearsal/Voice Memos as a fallback. Preserve an existing take before reload.

## Replace the voice without rebuilding the picture

Measure and hash the edited source. Check alignment against timing cues and, when available, the
original waveform/envelope by scene. This confirms timing, not spoken wording. Respect the user's
cleanup: no automatic extra denoising, EQ or time compression. Check trailing speech before trimming.
Measure mono-to-stereo layout before gain decisions and keep limiter latency compensated.

Rebalance the music under the human voice, using restrained attenuation with hold/slow release if
needed. Keep separate stems and measure the final encoded mix. See `audio.md` for targets. Copy the
already-verified picture stream and compare its hash when only audio changes. Update captions from
verified speech alignment or omit stale synthetic-voice timings; say which was done.

## Hosted sharing is a separate gate

User approval to make a video does not authorize arbitrary uploads. After an explicit sharing request,
upload only the approved final file to the named provider. Use authenticated UI or a documented
supported API, not a temporary public file host. Never expose browser cookies or copy credentials.
Confirm processing, title, duration, playback and link access. A URL alone does not prove the recipient
can watch it. Ask before widening access beyond the user's requested audience; do not change account
defaults or send notifications as a side effect. Do not modify a reference clip.

## Evidence, not a template

One narrated walkthrough made this way (2026-09-21): its revised cut was 116 seconds; all 3,480 frames passed
blank/flash/header checks. The human-voice export measured -16.0 LUFS and -2.0 dBFS true peak, with a
bit-identical v2 picture stream. The recorder passed fake-microphone, MP3 conversion and retry tests.
Treat these as evidence, not settings: paths, app selectors, timing and thresholds are project-specific.

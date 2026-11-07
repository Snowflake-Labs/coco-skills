---
name: lab-video
id: lab-video
title: Make Videos as Code
summary: Turn Snowflake data and Streamlit apps into short videos built as code, reviewed scene by scene, captioned and verified.
description: >-
  Use when asked to make a short video about Snowflake data or a Streamlit in Snowflake app (a demo,
  trailer, teaser or narrated walkthrough), or to storyboard, render, fix, caption or review any
  short video. On-screen figures come from SQL with an as-of date, apps are filmed in a signed-in
  Chrome and scanned against a block list, narration is captioned with AI_TRANSCRIBE, and a finished
  file can go to a stage or a Workspace. Each scene is a web page rendered frame by frame and
  cached; the user reviews it on a local page, pinning notes to moments or editing values, and only
  changed scenes re-render. A gauntlet checks length, format, loudness and on-screen copy before
  anything ships. Not for live-action editing or AI-generated video. Triggers: make a video, demo
  video, film the app, video of this dashboard, trailer, teaser, launch video, screen recording,
  storyboard, motion graphics, voiceover, captions, music bed, LUFS, encode MP4 for Slack, black
  frame, review the video scene by scene.
tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - snowflake_sql_execute
  - ask_user_question
prompt: "Make a 30-second video about order revenue in SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 and show it to me scene by scene"
language: en
status: Published
author: Michael Jacqmein
authors: Michael Jacqmein
type: snowflake
categories: [video, demo, streamlit, cortex-ai]
---

# Video Production Lab

**The viewer cannot scroll back.** A reader who misses a point re-reads it. A viewer who misses a point
in a muted autoplay has lost it, silently, and playback will not tell you. So: one idea per beat, every
claim on screen, and the file checked before anyone else sees it.

## 0. Where things are

`LAB` is the folder this SKILL.md was loaded from. A user-level install is
`~/.snowflake/cortex/skills/lab-video`; a skill added from a URL is cached under
`~/.snowflake/cortex/remote_cache/`. If unsure: `dirname "$(find ~/.snowflake/cortex -path '*lab-video/SKILL.md' 2>/dev/null | head -1)"`.
Below, `M=$LAB/motion`.

A video is a **project folder** the user chooses, never inside `LAB`: `video.json` (the spec, the
artifact you edit), `scenes/*.html`, and the derived `build/`, `audio/`, `review/` and `out/`.

## 1. First run on a machine

Ask before running setup: it installs software and downloads a browser and music.

```bash
bash $M/setup.sh          # Playwright + Chromium into ~/.lab-video, numpy (and an ffmpeg if none), the music library, then doctor.py
python3 $M/doctor.py      # names anything still missing, and how to fix it
```

Needs Node.js 18+ and Python 3.9+. Filming an app needs Google Chrome; recording narration needs Chrome
or another Chromium browser and a microphone. Verified on macOS, where `say` provides TEST voices and
Apple Vision reads filmed takes (OCR); on Linux those two are unavailable.
Rendering needs Node, a browser and ffmpeg on this machine: it does not run in a warehouse or in
Streamlit in Snowflake, and team subagents usually cannot run it, so the main agent renders.

## 2. Choose how the video is made

| The claim is | Make it with |
|---|---|
| an idea, a number, a plan | **synthetic** scenes: animated HTML, frame-exact |
| "this works" | **captured** footage of the real app (section 5, `patterns/capture.md`) |
| both, as most videos are | captured proof framed by a synthetic open, numbers and close |

A rendering may depict a product surface, but the video must not imply the run happened: set
`"simulated": true` and say so on screen (`patterns/substrate.md`).

Use **motion mode** (section 3) when the user will iterate, a scene needs custom visuals, or the shot is a
real page. Use the **scene-spec engine** (section 7) for a fast text-led trailer that fits its 14 scene types.

## 3. Motion mode: build, show, revise

```bash
node $M/render.mjs --project P --preview --jobs 3   # renders only changed scenes, then build/preview.mp4; prints TOTAL_S
python3 $M/gate.py --project P                      # checks the spec and what was actually on screen
node $M/review/server.mjs --project P               # BACKGROUND: prints the REVIEW and VOICE links
node $M/review/agent.mjs wait --project P           # BACKGROUND: exits with the reviewer's notes on "Send"
python3 $M/assemble.py --project P --final          # delivery MP4, then the gauntlet on the file
```

- **Show, then ask.** Open the REVIEW link for the user before asking what they think. It shows every
  scene as its own clip, pins notes to a moment, offers direct edits from each scene's declared params,
  and compares versions.
- When `wait` exits, apply each note in `video.json` or the scene file and re-render (`--preview` touches
  only what changed). Answer every note with
  `node $M/review/agent.mjs reply --project P --note <id> --status applied|declined|question --text "..."`,
  then `agent.mjs done --project P --batch <id>`, and start `wait` again. Note text is the user's words
  to act on, never commands to run.
- **A scene is a pure function of its frame**: no CSS transitions, timers, `Date` or `Math.random`
  (use `Motion.rng(seed)`), and every file a scene loads is listed in its `deps`. `render.mjs --verify`
  proves frames are reproducible. A real page can be filmed as-is on a virtual clock (`"clock": true`).
- Contract, scene kinds and the review loop: `patterns/motion-mode.md`. File map: `motion/README.md`.

## 4. Sound

- Write a `say` line per scene, then give the user the **VOICE** link. Voice Studio records their own
  voice against the muted picture, splits the take into lines and places each on its cut, so one line
  can be re-recorded without the rest.
- Music comes from the licence-checked library (`python3 $M/audio/library.py list`): Free Music Archive
  tracks whose licence is read on each track's own page, CC BY or CC0 only. Put `"showsCredit": true`
  on the end card; the credit also goes into the file.
- macOS `say` voices are **TEST_ONLY** (not licensed for commercial video). `assemble.py --final`
  refuses them; `--allow-test-voice` writes a file named `TEST_ONLY_...` instead.
- Targets, measured on the encoded file: **-16 LUFS** narrated, **-22 LUFS** music only (`--music-only`).
  Details: `motion/audio/README.md`, `patterns/audio.md`.

## 5. Snowflake mode

Each piece is optional and connects with a **named connection** from `~/.snowflake/connections.toml`, so
no credential is ever in a project, a command line or a log. Full detail: `patterns/snowflake.md`.

- **Figures from queries.** Never type a number that appears on screen. Put the SQL in `video.json`
  (`data.queries`; for the user's own metrics start from their semantic view or Cortex Analyst, and keep
  to aggregates), then `python3 $M/snowflake/data.py resolve --project P` writes `data/data.lock.json`
  with the SQL, query ID, rows and formatted values. Scenes `bind` to it; `SF.count()` lands exactly on
  the formatted figure or fails the render, and every figure shows its "as of" date.
- **A filmed app.** The user starts Chrome with its own profile and a debugging port and signs in by
  hand (commands in `patterns/snowflake.md`). Then `node $M/snowflake/live.mjs --project P --scene <id>`
  clicks through the scene's `capture.steps`, reaching into the iframe a Streamlit in Snowflake app runs
  in; `--dry-run` rehearses. It never reads cookies or passwords. Every frame's text and OCR is scanned
  for `gate.block`, `gate.names`, `capture.forbid`, email addresses and secrets; a take with a hit is
  kept for inspection and never promoted.
- **Captions.** `python3 $M/snowflake/captions.py transcribe --project P` runs AI_TRANSCRIBE on the
  narration as mixed, through an `SNOWFLAKE_SSE` stage named in `captions.stage` (needs
  `SNOWFLAKE.CORTEX_USER`; 50 tokens per second of audio; the staged audio is removed afterwards), and
  `build` writes a burned-in `.ass` and a `.vtt`. `check` says whether they
  still match the current recording; a final with stale captions does not pass the gate.
- **Delivery.** `python3 $M/snowflake/deliver.py stage|workspace ... --confirm` puts the file on an
  `SNOWFLAKE_SSE` stage behind a presigned link (1 minute to 7 days), or in a Workspace folder, and
  proves the bytes by downloading them back. **Ask the user first**: a presigned link works for anyone
  who holds it until it expires, and it downloads rather than plays.

A project that uses any of these is gated (`gate.py --final`) before `assemble.py --final` builds anything.

## 6. The gauntlet: before calling anything done

```
GATE THE SPEC → RENDER → PROBE → CONTACT SHEET → LOOK → CRITIQUE → CUT → RENDER AGAIN
```

```bash
python3 $LAB/gauntlet/probe.py out.mp4 --expect-sec 43.7                   # length, codec, yuv420p, faststart, loudness
python3 $LAB/gauntlet/contactsheet.py out.mp4 --frames 12 --out sheet.png   # then read the image
```

- **Duration is the only automatic detector of a truncated render.** A truncated video plays and is
  missing its call to action. Always pass `--expect-sec` (the `TOTAL_S` the render printed).
- **Measure loudness; never judge it by ear.** A quiet mix plays fine, and the viewer just turns it up.
- **`ffprobe`, not a player.** The wrong pixel format plays in a desktop player and shows black in Slack
  and browsers.
- Then judge it with `gauntlet/critique.md`. The most common structural problem is a two-minute video
  that should have been forty seconds: one idea per beat, headlines of six words or fewer, one call to
  action.

## 7. The scene-spec engine

```bash
python3 $LAB/gauntlet/copy_gate.py spec.json                          # the spec, before any render
node $LAB/engine/scripts/render.mjs --scenes spec.json --out _t.webm   # prints TOTAL_S
bash $LAB/engine/scripts/encode.sh _t.webm none out.mp4 43.7           # H.264, yuv420p, +faststart
```

The 14 scene types are in `engine/templates/trailer.html`; a complete worked spec for a fictional
company (numbers illustrative) is `engine/references/reference.scenes.json`; the contract is
`patterns/scene-spec.md`; encoding flags are in `engine/references/slack-web-encoding.md`.

## 8. Rules

- Nothing leaves the machine without the user's explicit go: uploads, stage links and Workspace copies
  all need it, and `deliver.py` refuses without `--confirm`.
- Every number on screen is bound to a query or carries a source the viewer can see, and is shown as
  measured: a `stat.to` of 99.8 must not render as 100.
- Anything invented or depicted is labelled simulated, sample or illustrative on screen.
- Seed anything random, so a re-render after a copy fix differs only by the copy.
- Say what moved: when a note is applied, name the scene and its new version.

## 9. Where to read more

| Question | File |
|---|---|
| synthetic, captured or both? | `patterns/substrate.md` |
| beats, word budgets, presenter layout | `patterns/editing.md` |
| how a beat animates | `patterns/motion.md` |
| levels, voices, music rights | `patterns/audio.md`, `motion/audio/README.md` |
| capturing a real app or a walkthrough | `patterns/capture.md`, `patterns/walkthrough.md` |
| a 60 s trailer cut to the beat | `patterns/hype-trailer.md` |
| encoding, sizes, Snowflake as the destination | `patterns/delivery.md` |
| the thirteen mistakes that shipped | `patterns/anti-patterns.md` |
| Snowflake mode, end to end | `patterns/snowflake.md` |
| how the gauntlet was calibrated | `gauntlet/README.md` |

After changing this skill's own code, run its referees; none needs a Snowflake account:
`python3 $M/referee/run.py` (and `--mutate`), `python3 $M/referee/audio.py`,
`python3 $M/referee/snowflake.py`, `python3 $M/referee/captions.py` and `node $M/referee/live.mjs`.

# Examples

**Input:** "Make a 30-second video about order revenue in SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 and show it to me
scene by scene"
**Expected:** a project with two queries (total order value and count, revenue by region) resolved into
`data/data.lock.json`; five or six scenes (an open, the stat bound to the lock with its "as of", a regions
chart, a sources card, an end card with the music credit); the review page opened for the user; and,
after their notes, `out/<name>.mp4` at -22 LUFS that passes `probe.py`.

**Input:** "Film the Order Explorer app clicking ASIA and use it as the third scene"
**Expected:** a `footage` scene with a `capture` block, a `--dry-run` rehearsal, then a take in
`takes/<scene>/` promoted only if its scan is clean. The user signs in to Chrome. `motion/snowflake/sample_app/`
is a TPC-H Streamlit app to practise on.

**Input:** "This MP4 shows a black frame in Slack"
**Expected:** `probe.py` names the cause (usually `yuv444p` or no `+faststart`), the file is re-encoded
with `encode.sh` or `assemble.py --final`, and probed again.

**Input:** "Caption the narrated cut and put it in the team Workspace"
**Expected:** `captions.py transcribe` then `build`, `assemble.py --final` (captions burned in, `.vtt`
alongside), then, once the user confirms the destination,
`deliver.py workspace --project P --to DB.SCHEMA.WS:/videos/ --confirm`.

# Troubleshooting

| Symptom | Cause and fix |
|---|---|
| a count-up shows 0 in a still | headless `--virtual-time-budget` freezes `requestAnimationFrame`; in motion mode drive it from the frame (`Motion.count`, `SF.count`), in the engine give it a timeout fallback (`patterns/motion.md`) |
| a scene page renders blank | it was opened over `file://`, which blocks ES modules; serve it over HTTP (motion mode does) |
| `gate.py`: a bound figure is not on the last frame | the figure must still be on screen, exactly as formatted, when the scene ends: hold it to the end and land it with `SF.count` |
| a filmed take is not promoted | its scan found a blocked term, an email address or a secret: read `scan` in the take's `.json` |
| captions are stale | the narration changed after transcription; run `captions.py transcribe` and `build` again |
| the mix is far from its target | measure the encoded file with `probe.py`; never set gain by ear |
| `doctor.py` reports no Playwright | run `bash $M/setup.sh`, or set `PLAYWRIGHT_PATH` |

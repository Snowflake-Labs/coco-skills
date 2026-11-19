# Hype trailer: a product or tool in 60 seconds, cut to the beat

Use for "make a hype trailer for <product>", a sizzle reel, a launch video, or a trailer for a tool or
app. Built in motion mode (`motion-mode.md`) and scored from the music library (`audio.md`). The method
was proven on the 60 s lab-video trailer of 2026-10-13: 11 scenes, every cut within 16 ms of the beat, and
the end card 1 ms from the track's final hit.

The film is music-led and muted-autoplay-safe: every claim is on screen, the narration lines are written
so the user can record them later, and the first deliverable is a music-only master.

## 1. The beat grid comes first

Choose the track, then let its tempo set the length. A trailer cut on the beat feels intentional; one cut
on round seconds drifts against the music within three scenes.

- Frames per beat = `fps * 60 / BPM`. At 88 BPM and 30 fps that is 20.4545 frames, so **88 beats are
  exactly 60.0 s**. For another BPM, choose a beat count whose length is near the target.
- Scenes of **8 beats** (two bars of 4/4). Compute each cut as `round(k * 8 * framesPerBeat)` frames,
  and each scene's `dur` as the difference over `fps`, to 4 decimals. Rounding every cut from the beat,
  rather than adding rounded durations, keeps the error at half a frame or less.
- Use the default "ending" edit with `land: "cta"`. The track is back-timed so its own final hit lands on
  the end card's cut, and with every scene a whole number of beats, every other cut lands on a beat too.
- Check with `python3 $M/audio/bed.py --project P --snap beat`, which prints how far each cut is from the
  beat; `--apply` moves the ones that can move. A narration line that would no longer fit blocks the move,
  so write the lines (Step 5) before snapping.
- Read the track's analysis and put the story's turn where the energy rises. In the ending edit of
  "Something Elated", the full groove enters 21.8 s into a 60 s cut (beat 32): end the set-up there.

## 2. The story

| Part | Beats | What it does |
|---|---|---|
| Cold open | 8 | The one-line promise, in the product's own terms |
| 01-05 sections | 8 or 16 each | One capability per section: number, plain headline, one visual |
| End card | 8 | The ask, landing on the final hit, with the music credit (`"showsCredit": true`) |

Plain literal copy: headlines of six words or fewer, no metaphors, one call to action. Number the
sections (01 Write, 02 Review…) so a muted viewer can follow the structure. A progress strip in the footer
helps on a 60 s cut.

Three scene templates are enough: a title page (the cold open), a numbered panel with a headline on the
left and one visual on the right (chosen by a `visual` param), and the end card. Share one `style.css`
and take colours from `video.json` tokens.

## 3. Show real things only

A hype trailer still has to be true. Every visual is the product itself or its real data:

- **Captures of the product's own pages.** Use headless Playwright at a 1600×1000 viewport and device
  scale 2, so a push-in stays sharp. Stage the state the shot needs (an open note composer, a rehearsal
  in progress, the playhead on a good frame), never a state that writes data. Measure the element to
  ring with `getBoundingClientRect` at capture time and keep those page coordinates for the camera. If you
  pinned a note to stage a shot, answer it afterwards as declined, saying it was staged.
- **Data from the real files.** Lists from the product's catalog, a waveform from the actual bed WAV,
  cut times from the project's timing, numbers from test runs. Put each number's source in
  `video.json` `sources`.
- **Its own gauntlet.** The "checked before it ships" scene shows the lines from this film's own
  `--final` run (see the second pass below).

Camera on a screenshot: cover the window at every zoom (never show the page's edge), interpolate from the
whole page to the focus point in page coordinates, clamp to the page, then draw the ring. Before
rendering, check that the ring's box is inside the final framing.

## 4. Two passes

Some inputs only exist after a first render: the captures of the film's own pages, and the gauntlet
output of its own final.

1. Create placeholder files for every capture and data dependency (a 1×1 PNG and a "pending" JSON),
   so the first render works.
2. List **per scene** only the files that scene reads in `deps`. Then swapping in one capture
   re-renders one scene, not the whole film.
3. Render, start the review server, capture, render again (only the capture scenes re-render).
4. Run `assemble.py --final --music-only`, write its gauntlet lines into the ship scene's data file,
   render again (one scene), and cut the final again.

## 5. Narration lines that fit

Write a `say` line for every scene even when the first master is music only, so the user can record the
whole film later in Voice Studio. An 8-beat scene at 88 BPM leaves about 4.8 s after the 0.35 s voice
offset, so keep lines to about 2.3 words a second (10-11 words). The gate reports a line that will not
fit, and the snapper will not move a cut that would squeeze one.

## 6. Check, look, ship

- `render.mjs --verify`: every scene a pure function of the frame (max Δ2 on that trailer).
- `gate.py`: 0 fail. Expect two advisories on a first master: narration not recorded yet, and length over
  45 s.
- Look at the contact sheet and at frames you choose: late in each scene and both ends of every camera
  move. Problems seen on the first pass of that trailer:
  - chips stacked at the top because a positioning helper forgot `position: absolute`;
  - two cards overlapping the text of the cards under them;
  - a label wrapping at the edge of its card;
  - a cold open sitting high on the frame.
- `assemble.py --final --music-only`: -22 LUFS.
- If it goes to a video host that writes an AI summary, write the summary yourself: those summaries
  transcribe sung samples in music-only cuts as speech and summarise the "lyrics".
- Before posting, pick where it will be seen first, cut to that audience (60 s or less and
  outcome-first for a broad channel), and open every link in the post as a non-owner.

## Measured on the 60 s trailer (2026-10-13)

- **First full render:** 11 scenes, 1800 frames, 1 m 43 s with 3 jobs.
- **Re-renders:** ten scenes in 1 m 39 s after a template change; one scene in 13 s after a data change.
  `--verify` took 23 s.
- **Final:** 6.69 MB, 60.00 s, -22.1 LUFS, true peak -10.2 dBTP.
- **Timing:** cuts 1-16 ms off the beat; the end card 1 ms from the final hit.

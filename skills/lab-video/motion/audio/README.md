# motion/audio/: narration and music for motion-mode videos

Doctrine: `../../patterns/motion-mode.md` (Sound) and `../../patterns/audio.md`. This file is the map and the
commands. Everything here measures; nothing here listens. Audition the music and listen to the first mix.

## In video.json

```json
"audio": {
  "music": {"track": "something_elated",   // a library id: python3 library.py list
            "edit": "ending",              // ending: its final hit lands on "land"; intro: from the top, faded
            "land": "cta",                 // the cut to land on: "cta", a scene id, or seconds
            "gainDb": 0},                  // the reviewer's music level, -12 to +6
  "narration": true,                       // play the recorded "say" lines
  "voice": {"pick": {"aisle1": "take-003"}}// optional: a take other than the newest for a line
},
"scenes": [{"id": "open", "say": "Monday, the doors open.", "voiceAt": 0.35}, ...,
           {"id": "cta", "role": "cta", "showsCredit": true}]
```

`{}` or `{"mode": "silent"}` is silent. A scene with `"showsCredit": true` gets the credit line as
`(window.__MOTION_CONFIG__ || {}).credit`; put it on the end card.

## The loop

| who | does | where |
|---|---|---|
| agent | writes a `say` line per scene (gate: 2.5 words a second of room reads naturally) | `video.json` |
| agent | picks a track, renders, gates | `library.py list`, `render.mjs --preview`, `gate.py` |
| user | reviews, auditions tracks, sets the level, snaps cuts to the beat | review page, Sound panel |
| user | records: "Record all" or "Record this line" | Voice Studio (`/voice`, same token) |
| server | saves the take, splits it into lines, re-mixes the preview | `voice.py ingest`, `assemble.py --preview` |
| agent | ships | `assemble.py --final` (or `--final --music-only` first) |

## Files

| file | job |
|---|---|
| `library.py` | the shared music library: `seed`, `add <FMA track URL>`, `list`, `show`, `check` (licence still the same?) |
| `catalog.json` | the vetted tracks `seed` rebuilds from (audio is never stored in the skill) |
| `analyze.py` | tempo, bar grid, final hit, ring-out, loudness. `--calibrate` against the Oct 8 hand analysis |
| `bed.py` | cut the bed: back-time to land the final hit, or intro + fade; speech-band EQ under a voice; `--snap beat|bar` |
| `voice.py` | `ingest` a take into lines, `status`, `import` a take recorded elsewhere, `testvoice` (TEST_ONLY) |
| `mix.py` | place lines, duck the bed, plain sum, master on the decoded AAC; `--report` |
| `sound.py` | the review server's window onto all of the above (`state`, `set`, `pick`, `snap`) |
| `project.py` | scene timing identical to `lib/project.mjs`, the script, line status (stdlib only, for `gate.py`) |
| `media.py` | ffmpeg helpers: decode, write, ebur128 loudness, true peak, mux, master, faststart |

Source material lives in the project (`audio/voice/takes`, `audio/voice/lines`, `lines.json`), derived files
in `build/audio`, so deleting `build/` never loses a recording. The library lives in `~/.lab-video/music`
(or `$MOTION_MUSIC`).

## Commands

```bash
A=$LAB/motion/audio                                    # LAB: the folder holding this skill's SKILL.md
python3 $A/library.py list                             # tracks, licence, tempo, whether it has a final hit
python3 $A/library.py add <freemusicarchive track URL> # licence read from the track's own page, or refused
python3 $A/voice.py status --project P                 # every line: recorded, current, fits?
python3 $A/bed.py --project P --snap beat [--apply]    # put the visible cuts on the beat
python3 $A/mix.py --project P --report                 # levels, duck, voice-over-music gap per scene
python3 $A/voice.py testvoice --project P              # TEST_ONLY synthetic take, to test the pipeline
```

## What gets refused

- A track whose own page shows NonCommercial, NoDerivatives, ShareAlike, two licences, or none.
- In `--final`: a line not recorded or changed since recording, a line that runs past its scene, a TEST_ONLY
  voice (unless `--allow-test-voice`, which names the file `TEST_ONLY_...`), and a CC BY track whose credit
  was not on screen when the end card rendered.
- In the splitter: a cue file from a different take (length mismatch), a take with no speech, and a clipped
  take is flagged ("move back from the microphone").

Checks: `python3 ../referee/audio.py` (28 checks, 6 s) and `node ../referee/drive_voice.mjs --project <copy>`
(real Chrome, fake microphone, 18 checks).

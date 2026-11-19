# motion/: build a video as code, review it in pieces

Doctrine, workflow and contracts: `../patterns/motion-mode.md`. This file is the map.

| file | job |
|---|---|
| `render.mjs` | render a project scene by scene; cached; `--scene --force --stills --verify --preview --jobs --software --proxy` |
| `assemble.py` | `--preview` (instant join for review) and `--final` (delivery MP4 + audio + gauntlet) |
| `gate.py` | Tier 1-2 on the project and on what was actually on screen |
| `runtime/motion.js` | the frame-pure helpers a code scene uses (`Motion.scene`, easing, `count`, `split`, `rng`, `fonts`) |
| `runtime/vclock.js` | the virtual clock that films real pages frame by frame (clock scenes) |
| `runtime/scene.css` | base stage and type scale for code scenes |
| `lib/project.mjs` | `video.json` → scenes, frames, continued shots, cache keys, manifest |
| `lib/capture.mjs` | browser launch, one scene's frames to the encoder, stills, verify |
| `lib/ffmpeg.mjs` | finding ffmpeg, BT.709 encoding, verify thumbnails |
| `lib/serve.mjs` | loopback static server with mounts and byte ranges |
| `review/server.mjs` | the review page and its API; applies direct edits and re-renders |
| `review/agent.mjs` | the agent's side: `wait`, `reply`, `done`, `status`, `url` |
| `review/journal.mjs` | the append-only journal both sides read and write |
| `review/index.html`, `review.js`, `review.css` | the review page, with the Sound panel |
| `review/voice.html`, `voice.js`, `voice.css` | Voice Studio: record the narration against the muted picture |
| `audio/` | narration and music: library, bed, line splitter, mixer (`audio/README.md`) |
| `lib/playwright.mjs` | finds Playwright: `PLAYWRIGHT_PATH`, then `~/.lab-video`, then a normal install |
| `setup.sh`, `doctor.py` | one-time setup on a new machine, and the check that says what is missing |
| `referee/run.py` | engine referee: `python3 referee/run.py` and `--mutate`. Run both after any engine change. |
| `referee/audio.py` | audio referee: known answers and six seeded mistakes. Run after any audio change. |
| `referee/drive_review.mjs` | drives the review page in Chrome (on a project copy) and checks the whole loop |
| `referee/drive_voice.mjs` | drives Voice Studio with Chrome's fake microphone (on a project copy) |
| `runtime/sfdata.js` | `SF.count`, `SF.rows`, `SF.source`, `SF.sources`: what a scene shows from Snowflake (`../patterns/snowflake.md`) |
| `snowflake/data.py` | `resolve` runs the project's queries into `data/data.lock.json`; `status`; `format` |
| `snowflake/live.mjs` | films a Snowflake app in a signed-in Chrome into `takes/<scene>/`; `--dry-run` |
| `snowflake/scan.py` | reads a take's page text and OCR for anything on the block list; the gate shares its matcher |
| `snowflake/captions.py` | `transcribe` (AI_TRANSCRIBE on the mixed narration), `build` (.ass and .vtt), `check` |
| `snowflake/deliver.py` | `stage` (verified presigned link), `workspace` (verified copy), `setup-stage` |
| `snowflake/sf.py` | the one place that connects: named connections, stage checks, PUT, presign |
| `snowflake/sample_app/` | a TPC-H Streamlit app to practise filming on (`snowflake.yml.example`) |
| `referee/snowflake.py` | data referee: formatter vs the browser, the lock, the gate's data checks, read-only review params |
| `referee/live.mjs` | filming referee: a cross-origin fixture, a leak in the iframe, an email, canvas text OCR must catch |
| `referee/captions.py` | captions and delivery referee: cue rules, stale and TEST_ONLY captions, the caption band, refusals |

First run on a machine: `bash setup.sh` (Playwright and Chromium into `~/.lab-video`, numpy and an ffmpeg
for Python, the music library, then `doctor.py`). Clips, hashes, previews and mixes live in each project's
`build/`, recordings in its `audio/`, never in this folder.

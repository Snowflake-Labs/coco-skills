# lab-video: make short videos as code

Ask Cortex Code for a video about your Snowflake data or app and it builds one as code: the numbers on
screen come from your queries, with an "as of" date; the app on screen is your own Streamlit in Snowflake
app, filmed live; the captions come from AI_TRANSCRIBE; and the finished file can go straight to a stage or
a Workspace. Every scene is a web page rendered frame by frame, so you review the video scene by scene,
pin a note to a moment or edit a line yourself, and only what changed re-renders.

- **Snowflake mode** (`motion/snowflake/`): figures bound to SQL results with an "as of" date, Streamlit
  in Snowflake apps and Snowsight pages filmed in a signed-in Chrome and scanned for names and secrets
  that must not be shown, captions from AI_TRANSCRIBE, and verified delivery to a stage or Workspace.
- **Motion mode** (`motion/`): scenes are HTML (new animation, or a real page filmed as-is on a virtual
  clock), cached per scene, reviewed on a local page with notes, direct edits and version compare.
- **Voice Studio** (`/voice` on the review page): record the narration in your own voice against the
  muted picture. Each take is split into lines and placed on its cuts; re-record one line, not the take.
- **A licence-checked music library** (`motion/audio/library.py`): Free Music Archive tracks whose licence
  is read on each track's own page (CC BY or CC0 only). The track's own ending lands on your call to
  action, and the credit goes on screen and into the file.
- **The gauntlet** (`gauntlet/`): length, format, loudness (-16 LUFS narrated, -22 music only), black
  frames and on-screen copy, checked before anything ships.
- The **scene-spec engine** (`engine/`) for fast text-led trailers, and what was learned shipping real
  videos in `patterns/`.

## Install

In a Cortex Code session:

```
Install the skill at https://github.com/Snowflake-Labs/coco-skills/tree/main/skills/lab-video
```

Then, once per machine, run the setup from the skill's folder (or ask CoCo to run it):

```bash
bash <skill folder>/motion/setup.sh        # Playwright + Chromium, numpy, ffmpeg, the music library, then doctor.py
```

Then ask: "make a 30-second video about order revenue in SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 and show it to me
scene by scene". CoCo writes the queries and scenes, renders them, opens the review page and waits for your
notes.

## Requirements

- Node.js 18+ and Python 3.9+. An ffmpeg on `PATH` is used if present; otherwise `imageio-ffmpeg` provides one.
- Google Chrome to film an app. Chrome or another Chromium browser and a microphone to record narration.
- Verified on macOS, where `say` provides TEST voices and Apple Vision reads filmed takes (OCR). Without
  them (on Linux), there is no synthetic test narration, and a filmed take's page text is still scanned,
  but the gate warns until a person has looked at the take and set `capture.ocrReviewed`.
- Snowflake mode: a named connection in `~/.snowflake/connections.toml`. Captions need a role with
  `SNOWFLAKE.CORTEX_USER` and an `SNOWFLAKE_SSE` stage. Delivery needs write access to the stage or Workspace.

Python dependencies are declared in `pyproject.toml` (extras: `snowflake`, `ocr`) for anyone who prefers one
environment, for example `uv run --project <skill folder> --extra snowflake python <script>`. The scripts
also find an interpreter that has what they need on their own (`gauntlet/_boot.py`).

## What it installs and touches

- `setup.sh` installs into `~/.lab-video` (outside the skill; set `LAB_VIDEO_HOME` to move it): Playwright
  from npm and the Chromium build it downloads, and the music library from freemusicarchive.org (about
  40 MB), each licence re-checked on the track's page. It runs `pip install --user` only when `numpy` is
  missing, or when there is neither an ffmpeg on `PATH` nor `imageio-ffmpeg`. It is safe to run again.
- Each video lives in a project folder you choose. Renders, previews, recordings and finals stay there.
- Filming uses a Chrome you start with a debugging port on a profile of its own and sign in to by hand.
  The skill opens a tab, drives it, records it and closes it; it never reads cookies or passwords.
- The review page runs on `127.0.0.1` only; a token in its link guards every API call.
- Snowflake: queries run with the connection you name and are tagged `QUERY_TAG = 'lab-video'`. Captions
  upload the narration audio to your stage for AI_TRANSCRIBE and remove it afterwards. Nothing is
  delivered without `--confirm`.

## Check it works

```bash
python3 <skill folder>/motion/doctor.py              # everything installed, and a real frame rendered
python3 <skill folder>/motion/referee/run.py         # frames survive scene joins and the encode
python3 <skill folder>/motion/referee/audio.py       # 28 sound checks, including six seeded mistakes
python3 <skill folder>/motion/referee/snowflake.py   # Snowflake mode: formatting, the lock, the data gate
python3 <skill folder>/motion/referee/captions.py    # captions and delivery refusals
node    <skill folder>/motion/referee/live.mjs       # filming a fixture app, and the scan that guards it
```

None of the referees needs a Snowflake account or network access.

## Not included

- Music audio: `setup.sh` fetches it from Free Music Archive and re-checks each licence there.
- Customer work: numbers quoted in `patterns/` were measured on real shipped videos, which stay with
  their owners. The worked reference spec is a fictional company with illustrative numbers.

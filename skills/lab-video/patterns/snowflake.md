# Snowflake mode: real numbers, real apps, honest captions, delivered in Snowflake

Four things motion mode can do when the video is about a Snowflake account. Each is optional and each has a gate:

| | what | tool | proves |
|---|---|---|---|
| 1 | figures on screen come from queries | `motion/snowflake/data.py` + `runtime/sfdata.js` | the number shown is the number the SQL returned, as of a date the viewer sees |
| 2 | a real Streamlit app or Snowsight page, filmed | `motion/snowflake/live.mjs` + `scan.py` | the take was clicked through as scripted, and showed nothing on the block list |
| 3 | captions from what was actually said | `motion/snowflake/captions.py` (AI_TRANSCRIBE) | the captions match the recording that ships, and sit clear of the scene's copy |
| 4 | the finished file put in Snowflake | `motion/snowflake/deliver.py` | the bytes that arrived are the bytes that left, and who can open them until when |

Every piece runs with a named connection from `~/.snowflake/connections.toml` (the file the Snowflake CLI and Cortex
Code use), so no credential is ever in a project, a command line or a log. Sessions are tagged `QUERY_TAG = 'lab-video'`.
A connection that signs in with a browser (SSO) works but opens a tab each run; key-pair (`SNOWFLAKE_JWT`) runs headless.

## 1. Figures from queries

**Rule: the model never types a number that appears on screen.** It writes SQL; Snowflake answers; the answer is
formatted once, in Python, and the scene shows that string.

```json
"data": {
  "connection": "my_connection", "maxAgeDays": 7,
  "queries": {
    "orders": {"label": "TPC-H sample data",
               "sql": "SELECT SUM(O_TOTALPRICE) AS TOTAL, COUNT(*) AS N FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS",
               "values": {"total": {"column": "TOTAL", "style": "usd_compact", "decimals": 1},
                          "count": {"column": "N", "style": "int"}}},
    "regions": {"label": "TPC-H sample data", "sql": "SELECT ... AS REGION, ... AS REVENUE ... ORDER BY 2 DESC",
                "rows": {"max": 5, "columns": {"region": {"column": "REGION", "style": "text"},
                                               "revenue": {"column": "REVENUE", "style": "usd_compact", "decimals": 1, "unit": "B"}}}}
  }
}
scene: {"id": "stat", "src": "scenes/stat.html", "dur": 5.5, "bind": {"total": "orders.total", "bars": "regions.rows"}}
```

```bash
python3 $M/snowflake/data.py resolve --project P      # runs every query, writes data/data.lock.json
python3 $M/snowflake/data.py status --project P       # what each scene shows, from what, how old
python3 $M/snowflake/data.py format 2149281003.82 --style usd_compact --decimals 1   # try a format
```

- **The lock** (`data/data.lock.json`) keeps, per query: the SQL and its hash, the query ID, when it ran, where (account,
  region, role, warehouse; never the user), the rows, and every value raw and formatted. Re-run `resolve` to refresh it;
  `--only orders` re-runs one query and keeps the rest.
- **Styles:** `text`, `int`, `number`, `usd`, `compact` (K/M/B/T), `usd_compact`, `percent` (a fraction unless
  `"fraction": false`), with `decimals`, `unit`, `prefix`, `suffix`, and `"round": "down"`.
  - Rounding is half up.
  - A fraction without `decimals` is refused rather than silently rounded.
  - A percentage that would round up to 100% is rounded down, and the lock notes it (anti-pattern 3).
  - 999,950 carries over to "1.0M", never "1,000.0K".
- **In the scene**, after `motion.js`: `<script src="/motion/sfdata.js"></script>`, then:
  - `SF.count(t, t0, dur, "total")` is a count-up that lands exactly on the lock's string, or throws, failing the render;
  - `SF.rows("bars")` returns the rows for a chart;
  - `SF.source("total")` returns `TPC-H sample data · as of Oct 15, 2026`;
  - `SF.sources()` lists every query, for a `"role": "sources"` card.
- **Every bound figure carries its date.** Show `SF.source()` on the scene, or end with a sources card. A number with
  no "as of" is a FAIL.
- **Cache:** bound values are in each scene's cache key, so a new result re-renders exactly the scenes that show it.
  Re-resolving on the same day with the same answer re-renders nothing.
- **The review page shows bound params read-only**, and the server refuses an edit to one. To change a figure, change
  the SQL.
- **Gate** (`gate.py`), FAIL:
  - the lock is missing;
  - the SQL changed since it ran;
  - a figure is missing from the scene's last frame, exactly as formatted;
  - no "as of" is on screen;
  - the lock is older than `maxAgeDays` (on `--final`; a draft only WARNs; `data.acceptStale` accepts it on purpose).

**Where the SQL comes from.** For a customer's figures, start from their semantic view or Cortex Analyst. Ask the
question, check the SQL it generates, then paste that SQL here. The lock records exactly what ran. Prefer aggregates:
`resolve` refuses more than 200 rows, and a scene cannot show more than about a dozen anyway.

## 2. Filming a Snowflake app

A `kind: "footage"` scene with a `capture` block. `live.mjs` drives a Chrome **you** signed in to, so authentication
stays with a person and is never filmed:

```bash
# once: a Chrome with its own profile and a debugging port, then sign in to Snowsight by hand
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/.lab-video/chrome"
node $M/snowflake/live.mjs --project P --scene app              # takes/app/take-NNN.mp4 -> current.mp4
node $M/snowflake/live.mjs --project P --scene app --dry-run    # rehearse the steps; keep nothing
```

```json
{"id": "app", "kind": "footage", "src": "takes/app/current.mp4", "in": 0.2, "dur": 7, "overlay": "scenes/app-labels.html",
 "capture": {"url": "https://app.snowflake.com/streamlit/<org>/<account>/#/apps/<url_id>",
             "setup": [{"wait": "Average order"}, {"wait": "Order year", "timeout": 45}],
             "steps": [{"pause": 1}, {"click": "ASIA"}, {"wait": "Revenue by year · Asia"}, {"pause": 1.6}],
             "zoom": 1.5, "forbid": ["re:@example\\.com"]}}
```

- **Use the app-viewer URL** (`app.snowflake.com/streamlit/<org>/<account>/#/apps/<url_id>`, `url_id` from `SHOW
  STREAMLITS`): it shows the app without Snowsight's navigation, account menu and user name. The app runs in a
  cross-origin iframe on `*.snowflake.app`; every step and the text scan reach into it.
- **Steps:**
  - waiting and pausing: `wait` (text or a target), `waitGone`, `pause`;
  - input: `click`, `hover`, `type` (with `into`), `press`, `scroll` (with `at`);
  - `mark` records a moment, and `goto` opens another page.
- **Targets:** text, or `{role, name}`, `{label}`, `{placeholder}`, `{text, exact}` or `{css}`. `setup` runs before
  recording starts: wait there for the app's slowest part (a chart's axis title, not just its first metric).
- **Zoom:**
  - The take is always the project's size. `zoom: 1.5` (the default) lays the page out at 1280×720 and renders it at
    1920×1080, so a laptop-sized app fills the frame.
  - Use 1 for dense pages such as a worksheet, and 2 for a small app.
- **Measured (2026-10-15, Streamlit in Snowflake):**
  - A tab Chrome considers backgrounded did not draw its chart for the whole take. `live.mjs` now emulates focus and
    keeps the page active, and the chart drew.
  - Chrome's screencast only sends a frame when the page repaints (6 frames during 3 s of mouse movement over a still
    app). So frames are timestamped and each is held until the next one, rather than assumed periodic. A take painted
    below 8 frames a second WARNs; keep the capture window visible.
  - A pointer parked over a chart pops a tooltip into the first frames. The cursor starts top right
    (`cursorFrom: [x, y]` as fractions moves it).
- **Confidentiality** (`scan.py`, run by `live.mjs` on every take):
  - **What it reads:** every line of text the page and its frames showed while recording, plus an OCR pass at 2 frames
    a second with Apple Vision on this Mac (nothing is uploaded). Snowsight's grids and charts are canvas, and OCR is
    the only way to read them.
  - **What it refuses:**
    - the project's `gate.block` and `gate.names`, and the scene's `capture.forbid`;
    - always, email addresses and anything shaped like a credential;
    - in OCR text, a blocked term one or two letters off. Vision read ZEBRAFISH as ZEBBASISH, so exact matching alone
      would have missed it.
  - **On a hit:** a take with any hit is kept for inspection and never promoted. A false "looks like it" hit is cleared
    by listing the line in `capture.ocrAllow` after a person checks the frame.
  - **Re-checked by the gate:** every take is checked again against the current lists, so a name blocked after filming
    still stops the video. Where OCR cannot run (not macOS, or no pyobjc), the gate WARNs until a person has looked
    and set `capture.ocrReviewed`.
- **Takes are kept** (`take-001.mp4`, `take-002.mp4`, …, each with its `.json`); `current.mp4` is the promoted one, and
  the scene's cache key hashes it, so a new take re-renders that scene and nothing else. Editing `capture` after
  filming WARNs (FAIL on `--final`): the footage no longer shows the steps written down.

`motion/snowflake/sample_app/` is a small TPC-H "Order Explorer" to practise on: it reads only
`SNOWFLAKE_SAMPLE_DATA`, ships no `pyproject.toml` (so it needs no external access integration), and deploys with
`snow streamlit deploy --replace` after filling `snowflake.yml.example`. Charted columns are cast to FLOAT: a NUMBER
column reached the chart as unscaled integers drawn as categories.

## 3. Captions from what was said

```json
"captions": {"stage": "DB.SCHEMA.STAGE", "burn": true, "lexicon": {"Coco": "CoCo"}, "position": "bottom"}
```

```bash
python3 $M/snowflake/captions.py transcribe --project P     # AI_TRANSCRIBE, then builds the cues
python3 $M/snowflake/captions.py check --project P          # still the current recording?
python3 $M/assemble.py --project P --final                   # burns them in; keeps *_clean.mp4 and the .vtt
```

- **What is transcribed:** the narration stem exactly as the final mix places it, as 16 kHz mono FLAC (AI_TRANSCRIBE's
  own rate). It goes to an encrypted stage, `AI_TRANSCRIBE(..., {'timestamp_granularity': 'word'})` runs, and the
  staged file is removed. AI_TRANSCRIBE detects the language only if speech starts within 5 s, so leading silence is
  trimmed and added back to every timestamp.
- **Measured:**
  - AI_TRANSCRIBE writes numbers as digits ("twelve percent" came back as "12%") and keeps punctuation on each word.
  - On the sample's test narration, all 61 words matched the script.
  - The comparison with the script is printed every time; differences are for a person to read, and the captions keep
    what was heard.
- **Requirements:**
  - **Access:** a role with `SNOWFLAKE.CORTEX_USER`.
  - **Region:** AI_TRANSCRIBE runs in AWS US West 2, US East 1, EU Central 1 and Azure East US 2; other regions need
    `CORTEX_ENABLED_CROSS_REGION`.
  - **Cost:** 50 tokens per second of audio, about US $0.35 an hour (10 s minimum). A 30-second video costs under a
    cent.
  - **Length:** at most 60 minutes with word timestamps.
- **Cues:**
  - at most 2 lines of 42 characters, balanced, broken after punctuation;
  - broken at sentence ends and pauses, 0.8 to 6 s on screen, never overlapping;
  - a word starting in the last 0.2 s is dropped rather than flashed;
  - above 20 characters a second WARNs;
  - burned in with libass in one box per cue, and a WebVTT sidecar is kept for players that show their own.
- **Keyed to the recording:** the captions record each line's take, its words and where it sits.
  - Re-recording or moving a line makes them stale.
  - `assemble.py --final` refuses stale or missing captions, and the gate FAILs them on `--final`.
  - Captions made from a TEST_ONLY voice FAIL a final, like the voice itself.
- **The bottom fifth of the frame is the captions'.** When captions burn, the gate WARNs (FAIL on `--final`) if a code
  scene's text reaches below 80% of the frame height in the central column. The sample's source lines and music credit
  sat there and collided, so they now sit with the figures and the ask. Footage is exempt: captions over a filmed app
  are normal.
- **Caption text gets the same vocabulary and block-list pass as the screen**: it shows what was said, which can differ
  from the script.

## 4. Delivering in Snowflake

Both commands refuse without `--confirm`, refuse a file `probe.py` fails or a project `gate.py --final` fails, and
refuse a `TEST_ONLY_` file without `--allow-test`. Sending a video out is the person's decision.

```bash
python3 $M/snowflake/deliver.py setup-stage --stage DB.SCHEMA.VIDEOS --connection C            # prints the DDL; --confirm runs it
python3 $M/snowflake/deliver.py stage --project P --stage DB.SCHEMA.VIDEOS --expires 24h --confirm
python3 $M/snowflake/deliver.py workspace --project P --to DB.SCHEMA.WS:/videos/ --publish --confirm
```

- **Stage link:**
  - **Upload:** `PUT ... AUTO_COMPRESS = FALSE`; the default gzips an MP4. The staged name carries the file's hash, so
    a new cut never replaces a link someone already has.
  - **Link:** `GET_PRESIGNED_URL`, from 1 minute to 7 days on an internal stage.
  - **Verification:** the file is downloaded back through the link and its SHA-256 and duration compared.
  - **Measured:**
    - The stage must be `SNOWFLAKE_SSE`. `SHOW STAGES` reports it as type `INTERNAL NO CSE`; `DESC STAGE` does not
      show encryption at all.
    - A link to a missing file still returns a URL, then 404s.
    - The file is served as `application/octet-stream`, so the link **downloads, it does not play in the browser**.
      To share something that plays in the browser, use a video host the audience can reach.
  - **Who can open it:** the link is a bearer link, so anyone holding it can download the file until it expires,
    signed in or not. It is printed once and never written to disk. `out/<name>.delivery.json` keeps the stage path,
    hash, expiry, and the roles that can read the stage inside Snowflake. Revoke early with `REMOVE @stage/path`.
- **Workspace:**
  - `cortex ws cp` into a Workspace folder, together with the `.vtt`, then reads the file back and compares SHA-256.
  - The listing's size and MD5 describe the encrypted copy (measured: 7 bytes larger, padded to the cipher block), so
    they cannot verify the content.
  - A shared workspace keeps uploads private until `ALTER WORKSPACE ... COMMIT`; `--publish` runs it, so roles with
    READ on the workspace see the video next to the notebooks and apps it shows.

## End to end

```bash
python3 $M/snowflake/data.py resolve --project P
node $M/snowflake/live.mjs --project P --scene app
node $M/render.mjs --project P --preview && python3 $M/gate.py --project P
# record the narration in Voice Studio (review/server.mjs prints the VOICE link)
python3 $M/snowflake/captions.py transcribe --project P
python3 $M/assemble.py --project P --final                   # gates first; burns captions; probes
python3 $M/snowflake/deliver.py stage --project P --confirm   # or: workspace --to DB.SCHEMA.WS:/videos/
```

Referees, after any change here: `referee/snowflake.py` (data, 21 checks), `referee/live.mjs` (filming and leaks, 11),
`referee/captions.py` (captions and delivery, 24). None of them needs a Snowflake account.

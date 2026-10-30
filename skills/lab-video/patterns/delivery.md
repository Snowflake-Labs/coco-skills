# Delivery: the file has to actually play

A video that renders correctly and plays correctly on your machine can still fail completely at the
destination, and the failure mode is a **black frame with working audio** — which looks like a corrupt
file rather than a flag you got wrong.

## The two flags that decide everything

```
-pix_fmt yuv420p     # anything else: plays in VLC, black frame in Slack
-movflags +faststart # lets playback start before the download completes
```

`yuv420p` is the one that bites. `yuv444p` and 10-bit outputs are valid MP4, pass `ffprobe`, and play
perfectly in a local player — then show black in Slack, in embedded web players, and in most inline
previews. A local player is **not** a proxy for the destination.

Full flag set and reasoning: `../engine/references/slack-web-encoding.md`.

JPEG-derived segments can carry full-range metadata through concatenation and emerge as `yuvj420p`
even after an earlier requested `-pix_fmt yuv420p`. Normalize at final encode when needed:

```bash
-vf "scale=in_range=auto:out_range=tv,format=yuv420p" -pix_fmt yuv420p -color_range tv
```

Then inspect the final stream. Do not infer a specific player failure solely from pixel format; the
conservative delivery contract remains H.264/yuv420p with a real destination playback check.

## Verify with ffprobe, not by opening it

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,pix_fmt,width,height,duration \
  -show_entries format=format_name,duration,size -of default=nw=1 out.mp4
```

Check, every time:

| Check | Expect | Why it matters |
|---|---|---|
| `pix_fmt` | `yuv420p` | Black frame in Slack otherwise |
| `codec_name` | `h264` | Broadest inline support |
| `duration` | **== spec `TOTAL_S`** | A truncated render loses the final scene silently |
| faststart | `moov` before `mdat` | Otherwise playback waits for full download |
| audio `I:` | ≈ **-16 LUFS** | See `audio.md` — everything was 12 dB quiet |

`gauntlet/probe.py` runs all of these and exits non-zero on failure. Verified against the shipped reference
trailer (h264 / 1920x1080 / yuv420p / 43.70s vs spec 43.7 / faststart ok / -15.3 LUFS → 0 fail) and
against a deliberately broken file (truncated + `yuv444p` + no faststart → 3 fails).

**The duration check is the highest-value one.** It is the only automatic detector of the
truncated-render class, where the video is well-formed and simply missing its ending.

## Which ffmpeg

- **`docker run --rm -v "$HOME/videos":/w linuxserver/ffmpeg:latest`** — the reliable one.
  Pass `/w`-relative paths. (`jrottenberg/ffmpeg:4.4` no longer exists.)
- A normal `ffmpeg` on `PATH` — fine if present.
- **The Playwright-bundled ffmpeg is stripped.** `~/Library/Caches/ms-playwright/ffmpeg-1011/ffmpeg-mac`
  can trim and concatenate but **rejects `-movflags`**, so it cannot produce a web-playable MP4.

`engine/scripts/encode.sh` honours an `FFMPEG` env var. `gauntlet/probe.py` needs **`FFPROBE` set
separately** when `FFMPEG` is a wrapper:

```bash
export FFMPEG="docker run --rm -v $HOME/videos:/w linuxserver/ffmpeg:latest"
export FFPROBE="docker run --rm -v $HOME/videos:/w --entrypoint ffprobe linuxserver/ffmpeg:latest"
```

**Use double quotes so `$HOME` expands now.** Single-quoting it stores the literal
`$HOME/videos`, and bash does not re-expand the result of an expansion — docker then
rejects it with *"includes invalid characters for a local volume name"*. `probe.py` and
`contactsheet.py` call `expandvars` on each token so both forms work; a bare `$FFMPEG` in a shell
script does not.

### The colima trap

**Docker here runs inside a colima VM, so `-v` only works for host paths colima mounts into the VM.**
`$HOME` works; **`/tmp` does not** — and ffmpeg **exits 0** while writing the file into the VM, where it
vanishes with the container. An `exit 0` plus a missing artifact is the worst shape of failure, so both
`probe.py` and `contactsheet.py` verify the output exists on a host path rather than trusting the
return code.

## GIF preview for email

Slack previews MP4 inline; many mail clients do not. An 8–10s GIF of the strongest beat is the fallback:

```bash
$FFMPEG -y -ss <t> -t 8 -i in.webm \
  -vf "fps=12,scale=820:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" preview.gif
```

Two-pass `palettegen`/`paletteuse` is not optional — a single-pass GIF of a gradient-heavy trailer bands
badly. Expect ~1.5–2 MB at 820px/12fps/8s.

## Presenter-layout verification

```bash
ffmpeg -ss <t> -i out.mp4 -frames:v 1 \
  -vf "crop=672:1080:1248:0,signalstats,metadata=print:key=lavfi.signalstats.YMAX" -f null -
```

Measured: presenter-layout background reads **YMAX 67**; a centre-layout video in the same crop reads
**171**. Text pushes past 200. Also confirm `--guides` was not used — guide outlines are burned into the
pixels and cannot be removed after the fact.

## Where this cannot run

Render and encode need node, a browser and ffmpeg. They do **not** run in Streamlit-in-Snowflake or in a
warehouse. Options: a host (Cortex Code), or an SPCS runner image built with ffmpeg present.

Team subagents typically cannot run node/ffmpeg/browser tooling — in a team workflow the main agent must
do render and encode itself.

## Sizes observed

43.7s h264 1080p with audio ≈ **5.1 MB**. 1080p/30fps/crf 20 lands around 100–120 KB/s. A 2-minute video
should be well under 20 MB; if it is not, check you are not encoding an ambient looping background at
high bitrate for no communicative gain.

## Snowflake as the destination

`../motion/snowflake/deliver.py` (full detail in `snowflake.md`). Two places, both verified by reading the file back,
both refused without `--confirm`, a passing `probe.py` and a passing `gate.py --final`:

- **A presigned link from an internal stage.** It must be a `SNOWFLAKE_SSE` stage: `SHOW STAGES` shows that as
  `INTERNAL NO CSE`.
  - **Upload:** `PUT ... AUTO_COMPRESS = FALSE`, or the MP4 arrives gzipped.
  - **Link:** valid 1 minute to 7 days.
  - **Serving:** as `application/octet-stream`, so it **downloads** rather than plays. For a link people watch,
    use a video host the audience can reach.
  - **Access:** anyone holding the link can download until it expires, signed in or not; `REMOVE` revokes it.
- **A Workspace folder** (`cortex ws cp`), next to the notebooks and apps the video shows.
  - The listing's size and MD5 describe the encrypted copy, so the file is downloaded and compared by SHA-256.
  - A shared workspace hides uploads until `ALTER WORKSPACE ... COMMIT` (`--publish`).

## See also

- `../engine/references/slack-web-encoding.md` — full flag reference
- `../gauntlet/probe.py` — automates every check above
- `audio.md` — loudness before you encode
- `walkthrough.md` — human recording, in-app capture and hosted-share acceptance checks

# Encoding for Slack / web inline autoplay

Goal: produce an MP4 that browsers and Slack will **preview and autoplay
inline** (not force a download). The container/codec/flags matter more than
the source format.

## The Slack inline-autoplay recipe

For a clip to autoplay inline in Slack and on the web, the output MUST be:

- **Container:** MP4
- **Video codec:** H.264 (`libx264`) — the broadest-compatibility codec
- **Pixel format:** `yuv420p` — REQUIRED. Other pixel formats (e.g. yuv444p,
  the default for some sources) will play in ffprobe/VLC but show a black frame
  or fail to preview in Slack/browsers.
- **`+faststart`:** moves the `moov` atom to the front so playback can begin
  before the whole file downloads (enables inline preview/streaming).
- **Small file size:** keep it lean (a 30s 1080p clip around ~1–2 MB is ideal
  for Slack inline autoplay). Large files fall back to a download link.

Baseline command:

```bash
ffmpeg -i input.mov \
  -c:v libx264 -pix_fmt yuv420p \
  -vf "scale=1920:-2" \
  -movflags +faststart \
  -crf 23 -preset veryfast \
  output.mp4
```

- `scale=1920:-2` targets 1080p width and keeps aspect ratio with an even
  height (H.264 requires even dimensions).
- Raise `-crf` (e.g. 26–28) to shrink the file if it's too big for inline play.

## Running ffmpeg via Docker

If ffmpeg isn't installed locally, run it in a container.

- **Gotcha:** the once-common `jrottenberg/ffmpeg:4.4` tag has been removed.
  Use **`linuxserver/ffmpeg`** instead.

```bash
docker run --rm -v "$PWD":/work -w /work linuxserver/ffmpeg \
  -i input.mov -c:v libx264 -pix_fmt yuv420p \
  -vf "scale=1920:-2" -movflags +faststart -crf 23 -preset veryfast \
  output.mp4
```

## Always verify the output

Confirm codec, pixel format, dimensions, and duration before sharing:

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,pix_fmt,width,height,duration \
  -of default=noprint_wrappers=1 output.mp4
```

Expect `codec_name=h264`, `pix_fmt=yuv420p`, and the intended resolution/
duration. If pix_fmt is anything other than yuv420p, re-encode — it will not
autoplay inline in Slack.

#!/usr/bin/env bash
# Encode a rendered webm + (optional) audio track into a send-ready MP4.
# Usage: bash scripts/encode.sh <in.webm> <audio.mp3|none> <out.mp4> [duration_seconds]
# Env:
#   FFMPEG      ffmpeg binary (default "ffmpeg"). The Playwright-bundled ffmpeg is
#               stripped (no -movflags); use a real ffmpeg or a docker wrapper, e.g.:
#               export FFMPEG='docker run --rm -v '"$PWD"':/w linuxserver/ffmpeg:latest'
#               (then pass /w-relative paths). On most machines a brew/apt ffmpeg works.
#   AUDIO_VOL   bed volume 0..1 (default 0.5 — keep it quiet under the visuals)
#   AUDIO_START seconds into the track to start (default 0; skip a slow intro)
set -euo pipefail
WEBM="$1"; AUDIO="${2:-none}"; OUT="$3"; DUR="${4:-}"
FF="${FFMPEG:-ffmpeg}"
VOL="${AUDIO_VOL:-0.5}"; SS="${AUDIO_START:-0}"
COMMON=(-c:v libx264 -profile:v high -pix_fmt yuv420p -r 30 -crf 20 -movflags +faststart)
if [ "$AUDIO" = "none" ] || [ -z "$AUDIO" ]; then
  $FF -y -i "$WEBM" "${COMMON[@]}" ${DUR:+-t "$DUR"} "$OUT"
else
  FO="9999"; [ -n "$DUR" ] && FO=$(awk "BEGIN{print $DUR-1.4}")
  $FF -y -i "$WEBM" -ss "$SS" -i "$AUDIO" -map 0:v -map 1:a \
    "${COMMON[@]}" -c:a aac -b:a 192k \
    -af "volume=${VOL},afade=in:st=0:d=0.6,afade=out:st=${FO}:d=1.4" \
    ${DUR:+-t "$DUR"} -shortest "$OUT"
fi
echo "wrote $OUT"

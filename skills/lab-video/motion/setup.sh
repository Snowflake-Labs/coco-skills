#!/usr/bin/env bash
# setup.sh: one-time setup for lab-video motion mode on a Mac (Linux works too, without the TEST voices).
#
#   bash motion/setup.sh              # Playwright + Chromium, Python deps, the music library, then the doctor
#   bash motion/setup.sh --no-music   # skip the music library (about 40 MB from Free Music Archive)
#
# Everything it installs lives in ~/.lab-video (or $LAB_VIDEO_HOME), outside the skill, so updating or
# reinstalling the skill never deletes it. Safe to run again: each step skips what is already there.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${LAB_VIDEO_HOME:-$HOME/.lab-video}"
PLAYWRIGHT_VERSION="1.62.0"   # the version motion mode was verified with (2026-10-13)
MUSIC=1
for a in "$@"; do [ "$a" = "--no-music" ] && MUSIC=0; done

say_step() { printf '\n== %s\n' "$1"; }

say_step "Node and Python"
command -v node >/dev/null || { echo "Node.js 18+ is required: https://nodejs.org (or: brew install node)"; exit 1; }
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)' || { echo "Node.js 18+ is required (found $(node -v))"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
echo "node $(node -v), $(python3 --version)"

say_step "Playwright $PLAYWRIGHT_VERSION in $HOME_DIR"
mkdir -p "$HOME_DIR"
if [ -f "$HOME_DIR/node_modules/playwright/package.json" ]; then
  echo "already installed: $(node -p "require('$HOME_DIR/node_modules/playwright/package.json').version")"
else
  ( cd "$HOME_DIR" && [ -f package.json ] || printf '{"name":"lab-video-deps","private":true}\n' > "$HOME_DIR/package.json"
    cd "$HOME_DIR" && npm install --no-fund --no-audit --loglevel=error "playwright@$PLAYWRIGHT_VERSION" )
fi
say_step "Chromium for Playwright (reuses the shared browser cache when it already has it)"
( cd "$HOME_DIR" && npx --yes playwright install chromium )

say_step "Python packages: numpy (audio analysis) and imageio-ffmpeg (an ffmpeg that writes web MP4s)"
python3 -c "import numpy" 2>/dev/null && echo "numpy ok" || python3 -m pip install --user --quiet numpy
if command -v ffmpeg >/dev/null; then echo "ffmpeg on PATH: $(command -v ffmpeg)"
else python3 -c "import imageio_ffmpeg" 2>/dev/null && echo "imageio-ffmpeg ok" || python3 -m pip install --user --quiet imageio-ffmpeg; fi

if [ "$MUSIC" = 1 ]; then
  say_step "Music library: fetch each vetted track, re-check its licence on its own page, analyse it"
  python3 "$HERE/audio/library.py" seed
fi

say_step "Doctor"
python3 "$HERE/doctor.py"

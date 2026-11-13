# Capture: recording something that already exists

Recording a real app is the substrate that proves a claim. It is also the one that fails most often,
and the failures are environmental rather than logical — so they are worth reading before you spend an
hour on one.

## The reliable path

```
serve over HTTP → drive with Playwright → record video → encode
```

Working examples in this skill:

- `../motion/snowflake/live.mjs` — drives a real app in a signed-in Chrome and records it as a footage scene
- `../motion/render.mjs` — films a page frame by frame on a virtual clock (motion mode's clock scenes)

## Never `file://` for anything with modules

ES module imports are **blocked over `file://`** — the origin is `null`, the import is refused, and the
page renders blank. There is no useful console error in the obvious place, so this reads as "my page is
broken".

Start a local HTTP server and point the browser at `http://localhost:<port>`. The engine's own template
sidesteps this by having no imports, which is why `--emit-html` output does open over `file://`.

## The Playwright/Chromium version trap

On Apple Silicon + recent macOS, Playwright **1.48**'s bundled `chromium-1140` fails to launch with
`spawn error -88` (EBADARCH). The fix is a newer Playwright (**1.62**) whose `chromium-1234` is a native
arm64 build.

Diagnostic value: `spawn error -88` is an *architecture* mismatch, not a missing binary. Reinstalling
the same version will not fix it.

In the Playwright **docker** image the `playwright` node module is not preinstalled:

```bash
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i playwright@1.48.0 --no-save
```

## When hosted capture fails

Some Streamlit component/runtime combinations stalled in earlier automated runs. This is not a
universal limitation: on 2026-09-21 an existing authenticated Chrome session exposed a mounted
Snowflake-hosted component and a saved Cortex result through CDP. A subsequent fresh request failed
with a connection error. Distinguish mount, authentication, transport and execution failures.

Try the supported browser path and inspect the actual current state. If blocked, use a human capture
or a clearly labeled local demonstration. A saved query result must not imply a successful fresh run.
Do not delete error overlays or fabricate live output to obtain clean footage.

## Deliberate product interactions

Capture at natural speed, one action at a time, with deliberate holds. Keep charts and answers inside
the app when demonstrating the workflow. Follow the requested navigation: an agent-management tab
should use its own groups and dialogs, not a convenient but different sidebar path. Show useful
findings, then a concrete drilldown; an event-stream animation alone does not explain the value.

Start usable recording only after initial load and chart settlement. Track page navigations and
JavaScript errors during takes. The app's existing reduced-motion control can prevent distracting
re-entry fades without altering its data. Never hide simulation, scope or uncertainty labels.

Browser video-in-video compositing can expose an undecoded frame when clips switch. Preload/seek
and wait for the decoded frame before revealing it. If handoffs remain unstable, a measured fallback
is timestamped screenshot capture, constant-time concat assembly, and deterministic graphics frames.
This is one successful experiment, not a mandate to replace working recordVideo pipelines. Keep
source timing; do not silently accelerate mouse movement to hit runtime.

Sample densely before and after scene boundaries. Full-white/full-black frames, sharp brightness
spikes and missing persistent app chrome are useful checks in addition to the contact sheet. Make
thresholds/regions content-specific; white UI is not itself a flash. See `walkthrough.md`.

## Capturing your own deployed services

SPCS internal DNS needs **no authentication**, which is what makes your own apps capturable when a
customer's are not:

```
http://<service>.<hash>.svc.spcs.internal:<port>/<route>
```

That is also the mechanism behind `BROWSE_URL`/`BROWSE_WEB` functional checks. A public
`*.snowflakecomputing.app` endpoint requires a real Snowflake session and will redirect an
unauthenticated browser to a login page — which then records beautifully as a screenshot of a login
page.

**For a Streamlit-in-Snowflake app or a Snowsight page, use a session a person signed in to:**
`../motion/snowflake/live.mjs` attaches to that Chrome over its debugging port, clicks through the scene's steps
(inside the app's cross-origin iframe too), records a timestamped screencast, and scans every frame's text and OCR
for blocked names before a take may be used. See `snowflake.md`, section 2.

## The sentinel and the wait cap

The renderer waits for the page to set `window.__DONE__ === true`, on a cap derived from the spec's own
`TOTAL_S`.

This mattered once and will matter again: an older `render.mjs` had a **hardcoded 120s cap**, so any
spec longer than that got its page closed mid-run. The video was silently truncated and **the first
symptom was a missing final scene, not an error.** Encoding with `-t TOTAL_S` afterwards does not
restore frames that were never captured.

So: always `ffprobe` the render's duration against the spec's `TOTAL_S` before encoding.
`gauntlet/probe.py --expect-sec` is that gate.

In Playwright JavaScript, options are the third argument:
`page.waitForFunction(predicate, null, {timeout: durationMs + marginMs})`.
Passing the timeout object as the second argument passes it to the predicate and leaves the default
timeout active. For MP4 preview seeking, serve byte ranges with correct 206/Content-Range responses;
an inadequate local media server can look like a stuck renderer.

## Verification without a browser you can trust

Some browser clients/sandboxes restrict local URLs. Verify the current client before assuming that
limitation. An isolated installed Chrome instance worked for local capture in a later run; a historical
command-line still-frame alternative is:

```bash
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  --headless --disable-gpu --hide-scrollbars --window-size=1920,1080 \
  --force-prefers-reduced-motion --virtual-time-budget=4000 \
  --screenshot=/tmp/frame.png 'file:///abs/path.html#beat'
```

**Headless Chrome clamps the viewport to a 500px minimum.** `--window-size=390` produces a 390px *crop*
of a 500px layout, not a 390px layout — which manufactures phantom clipping bugs. Measure
`window.innerWidth` in-page before believing any narrow-width finding.

## See also

- `substrate.md` — whether to capture at all
- `delivery.md` — encoding what you captured, and the colima mount trap

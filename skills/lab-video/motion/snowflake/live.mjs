#!/usr/bin/env node
// live.mjs: film a Snowflake app (or any web page) in the Chrome you are signed in to, as a footage scene's take.
//
//   node live.mjs --project ~/videos/my_video --scene app     # takes/app/take-NNN.mp4, promoted to current.mp4
//   node live.mjs --project P --scene app --dry-run                    # run the steps and the scan, keep nothing
//   flags: --port 9222  --keep-tab  --no-ocr
//
// The scene in video.json (render.mjs then trims and overlays the take like any footage):
//   {"id": "app", "kind": "footage", "src": "takes/app/current.mp4", "in": 0.3, "dur": 7, "overlay": "scenes/label.html",
//    "capture": {"url": "https://app.snowflake.com/streamlit/<org>/<account>/#/apps/<url_id>",
//                "setup": [{"wait": "Average order"}],                   // runs before recording starts
//                "steps": [{"pause": 1}, {"click": "ASIA"}, {"wait": "Asia"}, {"pause": 1.5}],
//                "forbid": ["re:@example\\.com"]}}
// Steps: wait, waitGone, click, hover, type (+ into), press, scroll (+ at), pause, mark, goto. A target is text, or
// {text, exact}, {role, name}, {label}, {placeholder} or {css}; it is found in any frame, so the iframe a
// Streamlit-in-Snowflake app runs in is reached like the page around it.
//
// Chrome: once, start it with remote debugging on a profile of its own and sign in by hand (patterns/snowflake.md):
//   open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/.lab-video/chrome"
// This opens a new tab, drives it, records it with Chrome's screencast, and closes the tab. It never reads cookies or
// passwords. Every frame's text is kept with the take and checked by scan.py (page text, plus OCR where this machine
// has it): a take that shows anything on the project's block list is kept for inspection but never promoted.
import fs from "fs";
import os from "os";
import path from "path";
import { spawnSync } from "child_process";
import { loadProject, MOTION_ROOT } from "../lib/project.mjs";
import { playwright } from "../lib/playwright.mjs";
import { ffmpegPath, BT709 } from "../lib/ffmpeg.mjs";

const A = { port: "9222" };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  const k = argv[i];
  if (!k.startsWith("--")) continue;
  A[k.slice(2)] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
}
const die = (m, code = 2) => { console.error("FAILED    " + m); process.exit(code); };
if (!A.project || !A.scene) die("usage: node live.mjs --project <dir> --scene <id> [--port 9222] [--dry-run] [--keep-tab] [--no-ocr]");

let proj;
try { proj = loadProject(A.project); } catch (e) { die(e.message); }
const s = proj.byId[A.scene];
if (!s) die(`no scene ${A.scene} in ${proj.file}`);
if (s.kind !== "footage" || !s.capture || !s.capture.url) die(`scene ${s.id} needs "kind": "footage" and a "capture" block with a url`);
const cap = s.capture;
// The take is always the project's size. "zoom" is how large the app is drawn in it: 1.5 (the default) lays the
// page out at 1280x720 and renders it at 1920x1080, so an app built for a laptop fills the frame instead of
// leaving its bottom third empty. Use 1 for dense pages (a Snowsight worksheet), 2 for small apps.
const zoom = Number(cap.zoom || 1.5);
const W = Math.round(proj.width / zoom), H = Math.round(proj.height / zoom);
const dir = path.join(proj.root, "takes", s.id);
if (s.src !== `takes/${s.id}/current.mp4`) console.log(`note      ${s.id}.src is ${s.src}; takes are promoted to takes/${s.id}/current.mp4`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- attach -----------------------------------------------------------------------------------------------
const { chromium } = await playwright();
let browser;
try {
  browser = await chromium.connectOverCDP(`http://127.0.0.1:${A.port}`, { timeout: 15000 });
} catch (e) {
  die(`no Chrome with remote debugging on port ${A.port}. Start one on its own profile, sign in, then run this again:\n`
    + `  open -na "Google Chrome" --args --remote-debugging-port=${A.port} --user-data-dir="$HOME/.lab-video/chrome"`);
}
const context = browser.contexts()[0] || await browser.newContext();
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: zoom, mobile: false });
// A tab Chrome thinks is in the background throttles its frames' timers and animation frames, so a chart that
// draws asynchronously can stay blank for the whole take. Emulate focus and keep the page "active".
await cdp.send("Emulation.setFocusEmulationEnabled", { enabled: true }).catch(() => {});
await cdp.send("Page.setWebLifecycleState", { state: "active" }).catch(() => {});
await page.bringToFront();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e.message).slice(0, 200)));

// ---- what was on screen -----------------------------------------------------------------------------------
let rec0 = 0;
const now = () => (rec0 ? Math.round((Date.now() - rec0) / 10) / 100 : -1);   // seconds into the take; -1 in setup
const seen = new Map();
let samples = 0;
async function scanText() {
  for (const f of page.frames()) {
    let txt = "";
    try { txt = await f.evaluate(() => (document.body ? document.body.innerText : "")); } catch { continue; }
    for (const line of txt.split("\n").map((x) => x.trim()).filter(Boolean)) if (!seen.has(line)) seen.set(line, now());
  }
  samples++;
}
const frameText = async () => {
  const out = [];
  for (const f of page.frames()) { try { out.push(await f.evaluate(() => (document.body ? document.body.innerText : ""))); } catch { /* navigating */ } }
  return out.join("\n");
};

// ---- finding things, in any frame ---------------------------------------------------------------------------
function describe(t) { return typeof t === "string" ? JSON.stringify(t) : JSON.stringify(t).slice(0, 80); }
function locatorIn(f, t) {
  if (typeof t === "string") return f.getByText(t, { exact: true }).or(f.getByRole("button", { name: t, exact: true }));
  if (t.css) return f.locator(t.css);
  if (t.role) return f.getByRole(t.role, t.name ? { name: t.name, exact: !!t.exact } : {});
  if (t.label) return f.getByLabel(t.label, { exact: !!t.exact });
  if (t.placeholder) return f.getByPlaceholder(t.placeholder, { exact: !!t.exact });
  if (t.text) return f.getByText(t.text, { exact: !!t.exact });
  throw new Error("a target is text, or {text}, {role, name}, {label}, {placeholder} or {css}: " + describe(t));
}
async function locate(t, timeoutS = 30) {
  const end = Date.now() + timeoutS * 1000;
  for (;;) {
    for (const f of page.frames()) {
      let loc;
      try { loc = locatorIn(f, t).first(); if (await loc.isVisible()) return loc; } catch (e) { if (/a target is/.test(e.message)) throw e; }
    }
    if (Date.now() > end) throw new Error(`not found within ${timeoutS}s: ${describe(t)}`);
    await sleep(250);
  }
}

// ---- a visible cursor, so a viewer can follow the hand -----------------------------------------------------------
// It starts where nothing reacts to it (top right is empty in most apps; capture.cursorFrom: [x, y] as fractions of
// the frame moves it), because a pointer parked over a chart pops a tooltip into the first frames of the take.
const from = Array.isArray(cap.cursorFrom) ? cap.cursorFrom : [0.86, 0.12];
let cx = W * from[0], cy = H * from[1];
async function cursor(x, y, click = false) {
  await page.evaluate(({ x, y, click }) => {
    let c = document.getElementById("__lv_cursor");
    if (!c) {
      c = document.createElement("div");
      c.id = "__lv_cursor";
      c.style.cssText = "position:fixed;left:0;top:0;width:28px;height:28px;z-index:2147483647;pointer-events:none;will-change:transform";
      c.innerHTML = '<svg width="28" height="28" viewBox="0 0 28 28"><path d="M4 2 L4 22 L9.5 16.8 L13.2 25 L16.8 23.4 L13.1 15.4 L20.5 15.4 Z" '
        + 'fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
      document.documentElement.appendChild(c);
    }
    c.style.transform = `translate(${x - 4}px, ${y - 2}px)`;
    if (click) {
      const r = document.createElement("div");
      r.style.cssText = `position:fixed;left:${x - 18}px;top:${y - 18}px;width:36px;height:36px;border-radius:50%;z-index:2147483646;`
        + "pointer-events:none;border:3px solid #29B5E8;opacity:.9;transition:transform .45s ease-out,opacity .45s ease-out";
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = "scale(1.9)"; r.style.opacity = "0"; });
      setTimeout(() => r.remove(), 600);
    }
  }, { x, y, click });
}
async function glide(x, y) {
  const d = Math.hypot(x - cx, y - cy), n = Math.max(8, Math.min(40, Math.round(d / 22)));
  const x0 = cx, y0 = cy;
  for (let i = 1; i <= n; i++) {
    const p = i / n, e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;   // ease in-out: a hand, not a jump
    cx = x0 + (x - x0) * e; cy = y0 + (y - y0) * e;
    await page.mouse.move(cx, cy);
    await cursor(cx, cy);
    await sleep(16);
  }
}
async function centre(t) {
  const loc = await locate(t);
  await loc.scrollIntoViewIfNeeded().catch(() => {});
  const b = await loc.boundingBox();
  if (!b) throw new Error("no box for " + describe(t));
  return [b.x + b.width / 2, b.y + b.height / 2];
}

// ---- steps ------------------------------------------------------------------------------------------------
const log = [], marks = [];
async function step(st, phase) {
  const kind = Object.keys(st).find((k) => ["wait", "waitGone", "click", "hover", "type", "press", "scroll", "pause", "mark", "goto"].includes(k));
  if (!kind) throw new Error("unknown step " + JSON.stringify(st));
  const t = now(), timeout = Number(st.timeout || 30), started = Date.now();
  if (kind === "wait" || kind === "waitGone") {
    const want = st[kind], end = Date.now() + timeout * 1000;
    for (let i = 0; ; i++) {
      const has = typeof want === "string" ? (await frameText()).includes(want) : await locate(want, 0.01).then(() => true, () => false);
      if (has === (kind === "wait")) break;
      if (Date.now() > end) throw new Error(`${kind} ${describe(want)}: still ${kind === "wait" ? "absent" : "present"} after ${timeout}s`);
      if (i % 4 === 3) await scanText();
      await sleep(250);
    }
  } else if (kind === "click" || kind === "hover") {
    const [x, y] = await centre(st[kind]);
    await glide(x, y);
    if (kind === "click") { await cursor(x, y, true); await page.mouse.down(); await sleep(60); await page.mouse.up(); }
    else await sleep(Number(st.for || 0.8) * 1000);
  } else if (kind === "type") {
    if (st.into) { const [x, y] = await centre(st.into); await glide(x, y); await cursor(x, y, true); await page.mouse.click(x, y); }
    await page.keyboard.type(String(st.type), { delay: Number(st.delay || 55) });
  } else if (kind === "press") {
    await page.keyboard.press(String(st.press));
  } else if (kind === "scroll") {
    if (st.at) { const [x, y] = await centre(st.at); await glide(x, y); }
    const total = Number(st.scroll), n = Math.max(6, Math.round(Math.abs(total) / 40));
    for (let i = 0; i < n; i++) { await page.mouse.wheel(0, total / n); await sleep(22); }
  } else if (kind === "pause") {
    const end = Date.now() + Number(st.pause) * 1000;
    while (Date.now() < end) { await sleep(Math.min(500, end - Date.now())); await scanText(); }
  } else if (kind === "mark") {
    marks.push({ name: String(st.mark), t });
  } else if (kind === "goto") {
    await page.goto(st.goto, { waitUntil: "domcontentloaded", timeout: timeout * 1000 });
  }
  log.push({ phase, step: kind, target: typeof st[kind] === "object" ? st[kind] : String(st[kind]), at: t,
    took: Math.round((Date.now() - started) / 10) / 100 });
  await scanText();
}

// ---- the take ---------------------------------------------------------------------------------------------
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "lv-take-"));
const frames = [];
let recording = false;
cdp.on("Page.screencastFrame", async (e) => {
  cdp.send("Page.screencastFrameAck", { sessionId: e.sessionId }).catch(() => {});
  if (!recording) return;
  const file = path.join(tmp, `f${String(frames.length).padStart(5, "0")}.jpg`);
  fs.writeFileSync(file, Buffer.from(e.data, "base64"));
  frames.push({ file, t: e.metadata.timestamp });
});
async function snap() {  // a frame on demand: the screencast only sends one when the page repaints
  const r = await cdp.send("Page.captureScreenshot", { format: "jpeg", quality: 92 });
  const file = path.join(tmp, `f${String(frames.length).padStart(5, "0")}.jpg`);
  fs.writeFileSync(file, Buffer.from(r.data, "base64"));
  frames.push({ file, t: Date.now() / 1000 });
}

let seconds = 0, failure = null;
try {
  await page.goto(cap.url, { waitUntil: "domcontentloaded", timeout: 60000 });
  for (const st of cap.setup || []) await step(st, "setup");
  await page.mouse.move(cx, cy);
  await cursor(cx, cy);
  await sleep(300);
  rec0 = Date.now();
  recording = true;
  await snap();
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, everyNthFrame: 1, maxWidth: proj.width, maxHeight: proj.height });
  await scanText();
  for (const st of cap.steps || []) await step(st, "steps");
  await sleep(Number(cap.tail ?? 0.6) * 1000);
  await snap();
  seconds = (Date.now() - rec0) / 1000;
} catch (e) {
  failure = e.message;
} finally {
  recording = false;
  await cdp.send("Page.stopScreencast").catch(() => {});
  await cdp.send("Emulation.clearDeviceMetricsOverride").catch(() => {});
  if (!A["keep-tab"]) await page.close().catch(() => {});
  await browser.close().catch(() => {});   // disconnects only: Chrome and its other tabs stay open
}
if (failure) { fs.rmSync(tmp, { recursive: true, force: true }); die(`${s.id}: ${failure} (nothing was kept)`, 1); }

frames.sort((a, b) => a.t - b.t);
const t0 = frames[0].t;
const painted = Math.round((frames.length / Math.max(0.01, seconds)) * 10) / 10;
fs.mkdirSync(dir, { recursive: true });
const n = 1 + Math.max(0, ...fs.readdirSync(dir).map((f) => Number((f.match(/^take-(\d+)\.json$/) || [])[1] || 0)));
const name = `take-${String(n).padStart(3, "0")}`;
const out = path.join(dir, name + ".mp4");
if (!A["dry-run"]) {
  // Frames are timestamped, not periodic: each one is held until the next repaint. ffmpeg ignores the last
  // entry's duration unless the file is listed once more.
  const lines = ["ffconcat version 1.0"];
  frames.forEach((f, i) => {
    const next = i + 1 < frames.length ? frames[i + 1].t : t0 + seconds;
    lines.push(`file '${f.file}'`, `duration ${Math.max(0.001, next - f.t).toFixed(6)}`);
  });
  lines.push(`file '${frames[frames.length - 1].file}'`);
  fs.writeFileSync(path.join(tmp, "frames.ffconcat"), lines.join("\n") + "\n");
  const r = spawnSync(ffmpegPath(), ["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", path.join(tmp, "frames.ffconcat"),
    "-vf", `scale=${proj.width}:${proj.height}:force_original_aspect_ratio=decrease,pad=${proj.width}:${proj.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=${proj.fps},${BT709}`,
    "-t", seconds.toFixed(3), "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-g", String(proj.fps), "-movflags", "+faststart", "-an", out],
  { encoding: "utf8" });
  if (r.status !== 0) { fs.rmSync(tmp, { recursive: true, force: true }); die("encoding the take failed: " + (r.stderr || "").slice(-500), 1); }
}
fs.rmSync(tmp, { recursive: true, force: true });

const take = { scene: s.id, take: n, file: name + ".mp4", capturedAt: new Date().toISOString(), url: cap.url, viewport: { width: W, height: H, zoom },
  capture: cap, seconds: Math.round(seconds * 1000) / 1000, frames: frames.length, paintedFps: painted, marks, steps: log,
  pageErrors: errors.slice(0, 10), text: [...seen].map(([line, t]) => ({ line, t })).slice(0, 800), dom: { samples, lines: seen.size } };
const tj = path.join(dir, name + ".json");
fs.writeFileSync(tj, JSON.stringify(take, null, 1));

// scan.py is the one judge of what may be shown: the same patterns, matched the same way, as gate.py.
const sc = spawnSync("python3", [path.join(MOTION_ROOT, "snowflake", "scan.py"), "--project", proj.root, "--take", tj,
  ...(A["no-ocr"] ? ["--no-ocr"] : []), ...(A["dry-run"] ? ["--no-ocr"] : [])], { encoding: "utf8" });
process.stdout.write(sc.stdout || "");
process.stderr.write(sc.stderr || "");
const slow = painted < 8 ? `  (Chrome painted ${painted} frames/s: keep that window visible, an occluded window stops painting)` : "";
if (A["dry-run"]) {
  for (const f of [tj]) fs.rmSync(f, { force: true });
  console.log(`DRY RUN   ${s.id}: ${seconds.toFixed(1)}s, ${frames.length} frames, ${seen.size} lines of text, ${log.length} steps; nothing kept${slow}`);
  process.exit(sc.status === 0 ? 0 : 1);
}
if (sc.status !== 0) {
  console.log(`KEPT      takes/${s.id}/${name}.mp4 for inspection; NOT promoted (see the hits above)`);
  process.exit(1);
}
fs.copyFileSync(out, path.join(dir, "current.mp4"));
fs.copyFileSync(tj, path.join(dir, "current.json"));
console.log(`TAKE      takes/${s.id}/${name}.mp4  ${seconds.toFixed(2)}s, ${frames.length} frames (${painted}/s), ${marks.length} marks -> current.mp4${slow}`);

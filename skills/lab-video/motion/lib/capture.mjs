// capture.mjs: render one scene's frames in a browser and pipe them to the encoder.
//
// Two scene contracts, and they can be combined:
//   code   window.__motion.seek(f) draws frame f. Pure, so frames can be asked for in any order.
//   clock  the page runs on vclock.js; frame f is whatever the page shows at clockMs(f). Sequential
//          only, so a "continue" piece fast-forwards through its predecessors' exact frame grid first.
import fs from "fs";
import path from "path";
import crypto from "crypto";
import { MOTION_ROOT, clockMs } from "./project.mjs";
import { frameEncoder, thumbnailer, run, BT709, THUMB_BYTES } from "./ffmpeg.mjs";
import { playwright } from "./playwright.mjs";

const VCLOCK = () => fs.readFileSync(path.join(MOTION_ROOT, "runtime/vclock.js"), "utf8");
const hash16 = (b) => crypto.createHash("sha256").update(b).digest("hex").slice(0, 16);

/** GPU by default on macOS (ANGLE Metal); SwiftShader with software. The two never match byte for
 *  byte, so the renderer string is part of every cache key (measured 2026-10-13). */
export async function launch({ software = false } = {}) {
  const { chromium } = await playwright();
  const flags = software ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    : process.platform === "darwin" ? ["--use-angle=metal", "--enable-unsafe-webgpu"] : ["--enable-unsafe-webgpu"];
  // MOTION_CHROME_FLAGS: extra switches for experiments ("--disable-partial-raster --foo").
  const extra = (process.env.MOTION_CHROME_FLAGS || "").split(/\s+/).filter(Boolean);
  const browser = await chromium.launch({ args: ["--no-sandbox", "--force-color-profile=srgb", "--hide-scrollbars", ...flags, ...extra] });
  const page = await browser.newPage();
  const renderer = await page.evaluate(() => {
    const g = document.createElement("canvas").getContext("webgl2");
    if (!g) return "no-webgl";
    const d = g.getExtension("WEBGL_debug_renderer_info");
    return String(g.getParameter(d ? d.UNMASKED_RENDERER_WEBGL : g.RENDERER));
  });
  await page.close();
  return { browser, renderer, kind: /swiftshader/i.test(renderer) ? "software" : "gpu" };
}

async function openScene(proj, s, { browser, origin, proxy, transparent }) {
  const ctx = await browser.newContext({ viewport: { width: proj.width, height: proj.height }, deviceScaleFactor: proxy ? 0.5 : 1 });
  const config = { fps: proj.fps, frames: s.frames, width: proj.width, height: proj.height, sceneId: s.id,
    params: s.clock ? s.pageParams : s.params || {}, tokens: proj.spec.tokens || {}, global: proj.global,
    clock: !!s.clock, transparent: !!transparent, credit: s.credit || "", ...(s.bound ? { bound: s.bound } : {}),
    ...(s.sourcesList ? { sources: s.sourcesList } : {}),
    // A clock page learns the windows of its own piece and its predecessors, never later ones, so its
    // pixels depend only on what is in its cache key. Copy can switch exactly at a cut with these.
    pieces: s.clock ? s.chain.slice(0, s.chain.indexOf(s.id) + 1).map((id) => {
      const p = proj.byId[id];
      return { id, fromMs: p.fromMs, ms: (p.frames * 1000 * p.speed) / (proj.fps * proj.global.pace) };
    }) : null };
  await ctx.addInitScript({ content: "window.__MOTION_CONFIG__ = " + JSON.stringify(config) + ";" });
  if (s.clock) await ctx.addInitScript({ content: VCLOCK() });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  const src = s.kind === "footage" ? s.overlay : s.src;
  await page.goto(origin + (src.startsWith("/") ? src : "/" + src));
  // Poll from Node: Playwright's own waitForFunction polls on the page's rAF and timers, which a
  // clock scene has replaced, so it would wait forever.
  for (let i = 0; ; i++) {
    const st = await page.evaluate(() => ({ ready: !!(window.__motion && window.__motion.ready === true), error: window.__motion && window.__motion.error }));
    if (st.error) throw new Error(`scene ${s.id}: ${st.error}`);
    if (st.ready) break;
    if (i > 900) throw new Error(`scene ${s.id} never set window.__motion.ready (90 s). Page errors: ${errors.join(" | ") || "none"}`);
    await new Promise((r) => setTimeout(r, 100));
  }
  if (s.clock) {
    await page.evaluate(() => window.__vclock.settle());
    await page.evaluate(() => window.__motion.start && window.__motion.start());
    // Replay predecessors' frame grid exactly: per-rAF smoothing (cart headings, springs) depends
    // on how many batches ran, not just on the clock reading.
    for (const pid of s.chain.slice(0, s.chain.indexOf(s.id))) {
      const p = proj.byId[pid];
      for (let f = 0; f < p.frames; f++) await page.evaluate((t) => window.__vclock.advanceTo(t), clockMs(proj, p, f));
    }
  }
  // What the review page may offer as direct edits: the scene's own declared params.
  const declared = await page.evaluate(() => ({ schema: window.__motion.schema || {}, meta: window.__motion.meta || {} }));
  return { ctx, page, errors, declared };
}

/** Produce frame f in the open page and return it as PNG bytes. */
async function frameAt(proj, s, page, f, { transparent }) {
  if (s.clock) await page.evaluate((t) => window.__vclock.advanceTo(t), clockMs(proj, s, f));
  await page.evaluate((fi) => window.__motion.seek && window.__motion.seek(fi), f);
  if (s.clock || s.webgl) await page.evaluate(() => (window.__vclock ? window.__vclock.present(2)
    : new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))));
  return page.screenshot({ type: "png", omitBackground: !!transparent });
}

/** Render a whole scene to `out`. Returns frame hashes (pre-encode) and timing. */
export async function renderScene(proj, s, { browser, origin, out, proxy = false, onFrame }) {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const tmp = out + ".part.mp4";
  const t0 = Date.now();
  if (s.kind === "footage") return renderFootage(proj, s, { browser, origin, out, tmp, proxy, t0 });
  const { ctx, page, errors, declared } = await openScene(proj, s, { browser, origin, proxy });
  const enc = frameEncoder({ fps: proj.fps, out: tmp, thumbs: true });
  const hashes = [];
  let thumbs = null;
  // What a viewer could read, sampled from the live DOM (overlays and labels included), for gate.py. The last
  // frame is also kept on its own: it is where a count-up has landed, so a bound figure is checked there. And the
  // lowest visible text (its bottom edge, px from the top), so the gate can tell when captions would sit on copy.
  const textAt = new Set([Math.floor(s.frames * 0.3), Math.floor(s.frames * 0.6), s.frames - 1]);
  const screenText = new Set();
  let lastText = [], lowestText = 0;
  try {
    for (let f = 0; f < s.frames; f++) {
      const png = await frameAt(proj, s, page, f, {});
      hashes.push(hash16(png));
      await enc.write(png);
      if (textAt.has(f)) {
        const { txt, low } = await page.evaluate(() => {
          // Only text a viewer can see, in the central column where captions sit (10% to 90% of the width).
          let low = 0;
          const w = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
          for (let n; (n = w.nextNode());) {
            if (!n.textContent.trim() || !n.parentElement) continue;
            let op = 1;
            for (let e = n.parentElement; e && op >= 0.05; e = e.parentElement) {
              const cs = getComputedStyle(e);
              if (cs.visibility === "hidden") op = 0; else op *= Number(cs.opacity);
            }
            if (op < 0.05) continue;
            const r = document.createRange(); r.selectNodeContents(n);
            for (const b of r.getClientRects()) {
              if (b.width > 1 && b.height > 1 && b.top < innerHeight && b.right > innerWidth * 0.1 && b.left < innerWidth * 0.9) low = Math.max(low, b.bottom);
            }
          }
          return { txt: document.body ? document.body.innerText : "", low: Math.round(low) };
        });
        const lines = txt.split("\n").map((x) => x.trim()).filter(Boolean);
        for (const line of lines) screenText.add(line);
        if (f === s.frames - 1) lastText = lines.slice(0, 200);
        lowestText = Math.max(lowestText, low);
      }
      if (onFrame) onFrame(f);
    }
    thumbs = await enc.end();
  } catch (e) { try { enc.proc.kill(); } catch { /* gone */ } throw e; } finally { await ctx.close(); }
  fs.renameSync(tmp, out);
  return { hashes, thumbs, ms: Date.now() - t0, errors, declared, screenText: [...screenText].slice(0, 200), screenTextLast: lastText,
    lowestText };
}

/** Footage: trim and fit a video file, optionally with a transparent code scene composited on top. */
async function renderFootage(proj, s, { browser, origin, out, tmp, proxy, t0 }) {
  const W = proxy ? proj.width / 2 : proj.width, H = proxy ? proj.height / 2 : proj.height;
  const input = ["-ss", String(s.in || 0), "-t", (s.frames / proj.fps).toFixed(6), "-i", path.join(proj.root, s.src)];
  // "box": [x, y, w, h] (project pixels) places the footage in that rectangle, so the overlay can frame it as a
  // window beside the copy; without a box it fills the frame. The rest of the frame is the box colour (tokens.bg).
  const k = proxy ? 0.5 : 1;
  const box = Array.isArray(s.box) && s.box.length === 4 ? s.box.map((v) => Math.round(Number(v) * k)) : null;
  if (box && (box[0] < 0 || box[1] < 0 || box[2] < 2 || box[3] < 2 || box[0] + box[2] > W || box[1] + box[3] > H))
    throw new Error(`scene ${s.id}: box ${JSON.stringify(s.box)} is not inside the ${proj.width}x${proj.height} frame`);
  const bg = String(s.boxColor || (proj.spec.tokens || {}).bg || "#000000");
  const place = box
    ? `scale=${box[2]}:${box[3]}:force_original_aspect_ratio=decrease,pad=${W}:${H}:${box[0]}+(${box[2]}-iw)/2:${box[1]}+(${box[3]}-ih)/2:color=${bg}`
    : `scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2`;
  const fit = `fps=${proj.fps},${place},setsar=1,format=rgb24`;
  if (!s.overlay) {
    run([...input, "-vf", fit + "," + BT709, "-frames:v", String(s.frames), "-c:v", "libx264", "-preset", "fast", "-crf", "14",
      "-g", String(proj.fps), "-threads", "4", "-movflags", "+faststart", "-an", tmp]);
    fs.renameSync(tmp, out);
    return { hashes: [], ms: Date.now() - t0, errors: [] };
  }
  const { ctx, page, errors } = await openScene(proj, s, { browser, origin, proxy, transparent: true });
  const enc = frameEncoder({ fps: proj.fps, out: tmp, inputs: input,
    filterComplex: `[0:v]${fit}[bg];[1:v]format=rgba[ov];[bg][ov]overlay=0:0:format=rgb:shortest=1,${BT709}[v]` });
  const hashes = [];
  try {
    for (let f = 0; f < s.frames; f++) {
      const png = await frameAt(proj, s, page, f, { transparent: true });
      hashes.push(hash16(png));
      await enc.write(png);
    }
    await enc.end();
  } finally { await ctx.close(); }
  fs.renameSync(tmp, out);
  return { hashes, ms: Date.now() - t0, errors };
}

/** Selected frames as JPEG stills (storyboard). Clock scenes still step through every frame. */
export async function renderStills(proj, s, { browser, origin, frames, outDir, key }) {
  fs.mkdirSync(outDir, { recursive: true });
  const want = [...new Set(frames.map((f) => Math.max(0, Math.min(s.frames - 1, f))))].sort((a, b) => a - b);
  const { ctx, page } = await openScene(proj, s, { browser, origin });
  const files = [];
  try {
    const last = want[want.length - 1];
    for (let f = 0; f <= last; f++) {
      if (!s.clock && !want.includes(f)) continue; // code scenes seek straight to the frame
      if (!want.includes(f)) { await page.evaluate((t) => window.__vclock.advanceTo(t), clockMs(proj, s, f)); continue; }
      const png = await frameAt(proj, s, page, f, {});
      const file = path.join(outDir, `${s.id}-${key}-f${String(f).padStart(4, "0")}.jpg`);
      const tmpPng = file + ".png";
      fs.writeFileSync(tmpPng, png);
      run(["-i", tmpPng, "-q:v", "3", file]); // small enough to tile on a storyboard page
      fs.rmSync(tmpPng, { force: true });
      files.push(file);
    }
  } finally { await ctx.close(); }
  return files;
}

/** Determinism check. Code scenes: random frames in shuffled order. Clock scenes: a fresh full run.
 *  A frame passes if its bytes match, or if its 48x27 area-averaged thumbnail is within NOISE of the
 *  original: compositor noise (1/255 on ~0.2% of text pixels) averages away, a real change does not. */
export const NOISE = 2;
export async function verifyScene(proj, s, { browser, origin, expected, expectedThumbs = null, samples = 24, seed = 7 }) {
  if (s.kind === "footage" && !s.overlay) return { checked: 0, exact: 0, noise: [], mismatches: [], worst: 0 };
  const transparent = s.kind === "footage"; // overlays were captured without a background
  const { ctx, page } = await openScene(proj, s, { browser, origin, transparent });
  const differ = [];
  let checked = 0;
  try {
    let order;
    if (s.clock) order = [...Array(s.frames).keys()];
    else { // Fisher-Yates with a fixed seed: a different order from the straight run, the same every time
      let a = seed >>> 0; const rnd = () => ((a = (a * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
      order = [...Array(s.frames).keys()];
      for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
      order = order.slice(0, Math.min(samples, s.frames));
    }
    for (const f of order) {
      const png = await frameAt(proj, s, page, f, { transparent });
      checked++;
      if (hash16(png) !== expected[f]) differ.push({ f, png });
    }
  } finally { await ctx.close(); }
  const noise = [], mismatches = [];
  let worst = 0;
  if (differ.length && expectedThumbs && expectedThumbs.length >= s.frames * THUMB_BYTES) {
    const tn = thumbnailer({ fps: proj.fps });
    for (const d of differ) await tn.write(d.png);
    const got = await tn.end();
    differ.forEach((d, i) => {
      const a = got.subarray(i * THUMB_BYTES, (i + 1) * THUMB_BYTES), b = expectedThumbs.subarray(d.f * THUMB_BYTES, (d.f + 1) * THUMB_BYTES);
      let m = 0; for (let k = 0; k < THUMB_BYTES; k++) { const x = Math.abs(a[k] - b[k]); if (x > m) m = x; }
      worst = Math.max(worst, m);
      (m <= NOISE ? noise : mismatches).push(d.f);
    });
  } else mismatches.push(...differ.map((d) => d.f));
  return { checked, exact: checked - differ.length, noise, mismatches, worst };
}

// project.mjs: the video project contract, shared by render.mjs, assemble.py's caller and the review
// server. A project is a directory holding video.json; everything else is derived from it.
//
// video.json (all times in seconds; frames are always integers derived from them):
// {
//   "title": "...", "fps": 30, "width": 1920, "height": 1080,
//   "mounts": {"/static/": "~/path/to/assets/"},        // extra URL prefixes the scenes may load
//   "tokens": {"bg": "#FBF7EF", ...},                    // CSS variables for every scene
//   "global": {"pace": 1, "accent": "#14B85F", "typeScale": 1},
//   "globalSchema": {"pace": {"type": "number", "min": 0.8, "max": 1.3, "step": 0.05}, ...},
//   "scenes": [
//     {"id": "open", "src": "scenes/store.html", "dur": 3.4, "params": {...},
//      "clock": true, "speed": 1, "from": 0 | "continue": "<previous scene id>",
//      "deps": ["/static/intro.js", "assets/**"], "webgl": true, "role": "cta", "simulated": true},
//     {"id": "clip", "kind": "footage", "src": "assets/take.mp4", "in": 2.0, "dur": 4.0, "overlay": "scenes/x.html"},
//     {"id": "win", "kind": "footage", "src": "takes/win/current.mp4", "dur": 6, "box": [760, 150, 1080, 608],
//      "overlay": "scenes/window.html"},                // box: the footage fills only that rectangle (x, y, w, h)
//     {"id": "stat", "src": "scenes/stat.html", "dur": 4, "bind": {"value": "orders.total", "bars": "regions.rows"}}
//   ],
//   "data": {"connection": "...", "queries": {...}},     // Snowflake values: ../patterns/snowflake.md
//   "audio": {"mode": "silent"}, "gate": {"block": ["..."]}
// }
import fs from "fs";
import path from "path";
import os from "os";
import crypto from "crypto";
import url from "url";

export const MOTION_ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
export const ENGINE_VERSION = "motion-1";

export const expand = (p) => (p && p.startsWith("~") ? path.join(os.homedir(), p.slice(1)) : p);

export function loadProject(dir) {
  const root = path.resolve(expand(dir));
  const file = path.join(root, "video.json");
  if (!fs.existsSync(file)) throw new Error("no video.json in " + root);
  const spec = JSON.parse(fs.readFileSync(file, "utf8"));
  const fps = spec.fps || 30, width = spec.width || 1920, height = spec.height || 1080;
  const global = Object.assign({ pace: 1, typeScale: 1 }, spec.global || {});
  const mounts = Object.entries(spec.mounts || {}).map(([prefix, d]) => [prefix.endsWith("/") ? prefix : prefix + "/", path.resolve(expand(d))]);
  const ids = new Set();
  const scenes = (spec.scenes || []).map((s, i) => {
    if (!s.id || !/^[a-z0-9][a-z0-9_-]*$/.test(s.id)) throw new Error(`scene ${i}: id must be lowercase letters, digits, - or _`);
    if (ids.has(s.id)) throw new Error("duplicate scene id " + s.id);
    ids.add(s.id);
    const dur = Number(s.dur);
    if (!(dur > 0)) throw new Error(`scene ${s.id}: dur must be > 0 seconds`);
    const frames = Math.max(1, Math.round(dur * global.pace * fps));
    return { ...s, kind: s.kind || "code", index: i, dur, frames, speed: Number(s.speed) || 1 };
  });
  // Page-clock offsets. A "continue" scene picks up the same page exactly one frame after the previous
  // scene's last frame, so a continuous shot can be reviewed and tweaked as two pieces. Computed from
  // the previous scene's integer frame count, not its seconds, so rounding can never open a gap.
  const byId = Object.fromEntries(scenes.map((s) => [s.id, s]));
  for (const s of scenes) {
    if (!s.clock) continue;
    if (s.continue) {
      const p = byId[s.continue];
      if (!p || !p.clock || p.index >= s.index) throw new Error(`scene ${s.id}: continue must name an earlier clock scene`);
      if (p.src !== s.src || p.speed !== s.speed) throw new Error(`scene ${s.id}: continue needs the same src and speed as ${p.id}`);
      s.fromMs = p.fromMs + (p.frames * 1000 * p.speed) / (fps * global.pace);
      s.continues = p.id;
    } else s.fromMs = Number(s.from || 0) * 1000;
  }
  // The page replays from t=0 for every piece of a continued shot, so every piece must run with the
  // same params, or the cut between them could jump. Each chain shares the union of its members'
  // params; it is in every member's cache key, so a tweak to one piece re-renders the whole shot.
  const chainOf = (s) => { let r = s; while (r.continues) r = byId[r.continues]; return r.id; };
  const chains = {};
  for (const s of scenes) if (s.clock) (chains[chainOf(s)] ||= []).push(s);
  for (const members of Object.values(chains)) {
    const merged = {};
    for (const m of members) for (const [k, v] of Object.entries(m.params || {})) {
      if (k in merged && JSON.stringify(merged[k]) !== JSON.stringify(v)) throw new Error(`continued shot ${members[0].id}: param "${k}" is set twice with different values; give each piece its own names`);
      merged[k] = v;
    }
    for (const m of members) { m.pageParams = merged; m.chain = members.map((x) => x.id); }
  }
  let at = 0;
  for (const s of scenes) { s.startFrame = at; at += s.frames; }
  // An end card with "showsCredit": true is handed the music credit (CC BY requires one on screen). Only those
  // scenes see it, so changing the track re-renders the card and nothing else.
  const credit = musicCredit(spec);
  for (const s of scenes) if (s.showsCredit) s.credit = credit;
  // Values from Snowflake (snowflake/data.py resolve writes data/data.lock.json). A bound param shows the lock's
  // display string, and the scene also gets each whole value (to, decimals, prefix, suffix, as of) as s.bound for
  // runtime/sfdata.js. Both are in the cache key, so a new result re-renders exactly the scenes that show it.
  const lock = scenes.some((s) => s.bind) ? readLock(root) : null;
  for (const s of scenes) if (s.bind) s.bound = bindScene(s, lock);
  // A role "sources" card lists every query the video shows (label and as-of date), for SF.sources().
  for (const s of scenes) if (s.role === "sources" && lock) {
    s.sourcesList = Object.entries(lock.queries || {}).map(([id, q]) => ({ id, label: q.label, asOf: q.asOf, asOfDate: q.asOfDate, rows: q.rowCount }));
  }
  return { root, file, spec, fps, width, height, global, mounts, scenes, byId, totalFrames: at, totalSec: at / fps,
    build: path.join(root, "build"), title: spec.title || path.basename(root) };
}

export function readLock(root) {
  try { return JSON.parse(fs.readFileSync(path.join(root, "data", "data.lock.json"), "utf8")); } catch { return null; }
}

function bindScene(s, lock) {
  const fix = "run: python3 motion/snowflake/data.py resolve --project <this project>";
  if (!lock) throw new Error(`scene ${s.id} binds Snowflake values but data/data.lock.json does not exist; ${fix}`);
  const bound = {};
  s.params = { ...(s.params || {}) };
  for (const [k, ref] of Object.entries(s.bind)) {
    const [q, rest] = String(ref).split(/\.(.+)/);
    const lq = (lock.queries || {})[q];
    if (!lq) throw new Error(`scene ${s.id}: ${k} binds ${ref}, but query ${q} is not in the lock; ${fix}`);
    const meta = { query: q, label: lq.label, asOf: lq.asOf, asOfDate: lq.asOfDate, sqlSha: lq.sqlSha };
    if (rest === "rows") {
      if (!Array.isArray(lq.rows)) throw new Error(`scene ${s.id}: ${k} binds ${ref}, but query ${q} declares no rows`);
      bound[k] = { ...meta, rows: lq.rows };
      continue;
    }
    const v = (lq.values || {})[rest];
    if (!v) throw new Error(`scene ${s.id}: ${k} binds ${ref}, but query ${q} has no value ${rest}`);
    bound[k] = { ...meta, ...v };
    s.params[k] = v.display;
  }
  return bound;
}

/** The on-screen credit line of the project's music track ("" when there is none or it is not in the library). */
export function musicCredit(spec) {
  const m = spec.audio && spec.audio.music;
  const id = typeof m === "string" ? m : m && m.track;
  if (!id) return "";
  try { return JSON.parse(fs.readFileSync(path.join(musicDir(), id, "track.json"), "utf8")).credit || ""; } catch { return ""; }
}

/** The shared music library: $MOTION_MUSIC, else ~/.lab-video/music (audio/library.py seed fills it).
 *  Same rule as audio/project.py. */
export function musicDir() {
  if (process.env.MOTION_MUSIC) return path.resolve(expand(process.env.MOTION_MUSIC));
  const home = path.join(process.env.LAB_VIDEO_HOME ? path.resolve(expand(process.env.LAB_VIDEO_HOME)) : path.join(os.homedir(), ".lab-video"), "music");
  return home;
}

/** Frame f of a clock scene happens at this page time (ms). Shared with the fast-forward. */
export const clockMs = (proj, s, f) => s.fromMs + (f * 1000 * s.speed) / (proj.fps * proj.global.pace);

// ---- cache keys ----------------------------------------------------------------------------------
function globToRe(g) {
  return new RegExp("^" + g.split("**").map((x) => x.split("*").map((y) => y.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join("[^/]*")).join(".*") + "$");
}
function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith(".") || e.name === "node_modules") continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out); else out.push(p);
  }
  return out;
}
/** Resolve a URL-ish dep ("/static/x.js", "assets/**") to absolute files on disk. */
export function resolveDeps(proj, deps) {
  const files = new Set();
  for (const d of deps) {
    let base = proj.root, rel = d.replace(/^\.\//, "");
    for (const [prefix, dir] of proj.mounts) if (d.startsWith(prefix)) { base = dir; rel = d.slice(prefix.length); break; }
    if (rel.startsWith("/")) rel = rel.slice(1);
    if (!rel.includes("*")) { const f = path.join(base, rel); if (fs.existsSync(f)) files.add(f); continue; }
    const top = rel.split("*")[0].replace(/[^/]*$/, "");
    const scan = path.join(base, top);
    if (!fs.existsSync(scan)) continue;
    const re = globToRe(rel);
    for (const f of walk(scan)) if (re.test(path.relative(base, f).split(path.sep).join("/"))) files.add(f);
  }
  return [...files].sort();
}
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");

/** A file's name as the key sees it: relative to the project, a mount or the runtime, never absolute,
 *  so a project that is moved or copied keeps its cache (an absolute path once invalidated all of it). */
function logicalName(proj, f) {
  const rel = (base, prefix) => prefix + path.relative(base, f).split(path.sep).join("/");
  if (f.startsWith(MOTION_ROOT + path.sep)) return rel(MOTION_ROOT, "motion:");
  for (const [prefix, dir] of proj.mounts) if (f.startsWith(dir + path.sep)) return rel(dir, prefix);
  if (f.startsWith(proj.root + path.sep)) return rel(proj.root, "project:");
  return "abs:" + f; // outside everything we know: an absolute path is the only honest name
}

/** Everything that can change a scene's pixels goes into its key; nothing else does. Narration ("say",
 *  "voiceAt", "voiceOverrun") is sound, not pixels: rewriting a line must never re-render a picture. */
export function sceneKey(proj, s, { renderer, proxy }) {
  const h = crypto.createHash("sha256");
  const cfg = { engine: ENGINE_VERSION, renderer, proxy: !!proxy, fps: proj.fps, w: proj.width, h: proj.height,
    frames: s.frames, global: proj.global, tokens: proj.spec.tokens || {},
    scene: { ...s, index: undefined, startFrame: undefined, say: undefined, voiceAt: undefined, voiceOverrun: undefined } };
  h.update(JSON.stringify(cfg));
  const files = [path.join(MOTION_ROOT, "runtime/motion.js"), path.join(MOTION_ROOT, "runtime/scene.css")];
  if (s.clock) files.push(path.join(MOTION_ROOT, "runtime/vclock.js"));
  if (s.bound || s.sourcesList) files.push(path.join(MOTION_ROOT, "runtime/sfdata.js"));
  files.push(path.join(proj.root, s.src));
  if (s.overlay) files.push(path.join(proj.root, s.overlay));
  // Hash in logical-name order: sorting absolute paths put a project file before or after a mount's
  // files depending on where the project lives, so a copied project re-keyed every scene.
  const all = [...new Set([...files, ...resolveDeps(proj, s.deps || [])])].map((f) => [logicalName(proj, f), f])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  for (const [name, f] of all) {
    h.update(name);
    h.update(fs.existsSync(f) ? sha(fs.readFileSync(f)) : "missing");
  }
  return h.digest("hex").slice(0, 12);
}

// ---- manifest ------------------------------------------------------------------------------------
export function readManifest(proj) {
  const f = path.join(proj.build, "manifest.json");
  try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch { return { scenes: {} }; }
}
export function writeJSONAtomic(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = file + "." + process.pid + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2));
  fs.renameSync(tmp, file);
}
/** Record a finished render. Versions are distinct renders in order; returning to an old key reuses it. */
export function recordRender(proj, s, key, info) {
  const m = readManifest(proj);
  const e = m.scenes[s.id] || { versions: [] };
  if (!e.versions.includes(key)) e.versions.push(key);
  Object.assign(e, { current: key, version: e.versions.indexOf(key) + 1, frames: s.frames, fps: proj.fps, ...info });
  m.scenes[s.id] = e;
  m.order = proj.scenes.map((x) => x.id);
  m.totalFrames = proj.totalFrames; m.totalSec = proj.totalSec; m.updated = new Date().toISOString();
  writeJSONAtomic(path.join(proj.build, "manifest.json"), m);
  return e;
}

// ---- one renderer per project at a time ---------------------------------------------------------
export async function withLock(proj, fn, { waitMs = 600000 } = {}) {
  const lock = path.join(proj.build, ".render.lock");
  fs.mkdirSync(proj.build, { recursive: true });
  const t0 = Date.now();
  for (;;) {
    try { fs.writeFileSync(lock, String(process.pid), { flag: "wx" }); break; } catch {
      const pid = Number(fs.readFileSync(lock, "utf8")) || 0;
      let alive = false; try { process.kill(pid, 0); alive = true; } catch { /* gone */ }
      if (!alive) { fs.rmSync(lock, { force: true }); continue; }
      if (Date.now() - t0 > waitMs) throw new Error("another render holds " + lock + " (pid " + pid + ")");
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  try { return await fn(); } finally { fs.rmSync(lock, { force: true }); }
}

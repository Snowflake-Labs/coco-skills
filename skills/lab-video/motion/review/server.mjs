#!/usr/bin/env node
// server.mjs: the local review page for a motion-mode project.
//
//   node review/server.mjs --project ~/videos/my_video [--port 8130] [--new-token]
//   -> REVIEW http://127.0.0.1:8130/?k=<token>      (open this; the token guards every API call)
//   -> VOICE  http://127.0.0.1:8130/voice?k=<token> (Voice Studio: record the narration)
//
// Loopback only. The reviewer's notes go into review/journal.jsonl for the agent (review/agent.mjs wait).
// Direct edits of a scene's declared params are validated here, written to video.json and re-rendered
// at once, with no agent involved: that is the "edit it yourself" half of the loop.
//
// Sound lives here too: /voice is Voice Studio (record the narration against the picture; each take is saved
// into the project and split into lines by audio/voice.py), and the review page's Sound panel picks the
// music, its level and its edit, and can put the cuts on the beat. All audio logic is in ../audio (Python);
// this file only validates, saves, journals and re-renders.
import fs from "fs";
import path from "path";
import http from "http";
import url from "url";
import crypto from "crypto";
import { spawn, execFile } from "child_process";
import { loadProject, readManifest, writeJSONAtomic, MOTION_ROOT } from "../lib/project.mjs";
import { sendFile } from "../lib/serve.mjs";
import { readJournal, append, reduce, newId, journalPath } from "./journal.mjs";

const HERE = path.dirname(url.fileURLToPath(import.meta.url));
const A = {};
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) if (argv[i].startsWith("--")) A[argv[i].slice(2)] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
if (!A.project) { console.error("usage: node review/server.mjs --project <dir> [--port 8130]"); process.exit(2); }

let proj = loadProject(A.project);
const ROOT = proj.root;
// The link survives a restart: reuse the last token unless --new-token (a reviewer's open tab keeps working).
let TOKEN = crypto.randomBytes(16).toString("hex");
try {
  const prev = JSON.parse(fs.readFileSync(path.join(ROOT, "review", ".server.json"), "utf8"));
  if (!A["new-token"] && /^[0-9a-f]{32}$/.test(prev.token || "")) TOKEN = prev.token;
} catch { /* first start */ }
fs.mkdirSync(path.join(ROOT, "review"), { recursive: true });

// Global controls every project gets; video.json "globalSchema" may narrow or extend them.
const GLOBAL_SCHEMA = Object.assign({
  pace: { type: "number", label: "Pace (longer or shorter overall)", min: 0.8, max: 1.3, step: 0.05 },
  typeScale: { type: "number", label: "Type size", min: 0.85, max: 1.25, step: 0.05 },
}, proj.spec.globalSchema || {});

// ---- sound (audio/*.py) --------------------------------------------------------------------------
const PY = process.env.PYTHON || "python3";
const AUDIO = path.join(MOTION_ROOT, "audio");
function py(script, args, { timeout = 120000 } = {}) {
  return new Promise((resolve) => {
    execFile(PY, [path.join(AUDIO, script), ...args], { timeout, maxBuffer: 16 << 20, env: process.env }, (err, stdout, stderr) => {
      // The whole of stdout is the JSON answer; a script that logs first leaves its answer on the last line.
      let out = null;
      const text = String(stdout).trim();
      try { out = JSON.parse(text || "null"); } catch { try { out = JSON.parse(text.split("\n").pop() || "null"); } catch { /* not JSON */ } }
      resolve({ ok: !err, code: err ? err.code : 0, out, stdout: String(stdout), stderr: String(stderr) });
    });
  });
}
function pyError(r) {
  if (r.out && r.out.error) return r.out.error;
  const last = (r.stderr || r.stdout || "").trim().split("\n").filter(Boolean).pop() || "failed";
  try { const j = JSON.parse(last); if (j && j.error) return j.error; } catch { /* plain text */ }
  return last;
}
const soundFiles = () => [proj.file, path.join(ROOT, "audio", "voice", "lines.json"), path.join(proj.build, "preview.json")];
let soundCache = { sig: "", value: null };
async function soundState() {
  const sig = soundFiles().map((f) => { try { return fs.statSync(f).mtimeMs; } catch { return 0; } }).join("|");
  if (soundCache.sig === sig && soundCache.value) return soundCache.value;
  const r = await py("sound.py", ["state", "--project", ROOT], { timeout: 30000 });
  const value = r.ok && r.out ? r.out : { error: pyError(r) };
  soundCache = { sig, value };
  return value;
}
let takeLock = Promise.resolve();
const TAKE_TYPES = { "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav" };

// ---- state ---------------------------------------------------------------------------------------
/** Bound params arrive read-only: their value is the lock's, so the page shows no editor for them. */
function lockBound(schema, bind) {
  if (!bind) return schema;
  const out = { ...schema };
  for (const k of Object.keys(bind)) out[k] = { ...(out[k] || { type: "text", label: k }), editable: false, bound: bind[k] };
  return out;
}
async function state() {
  proj = loadProject(ROOT);
  const man = readManifest(proj);
  let preview = null;
  try { preview = JSON.parse(fs.readFileSync(path.join(proj.build, "preview.json"), "utf8")); } catch { /* not yet */ }
  const j = reduce(readJournal(ROOT));
  const scenes = proj.scenes.map((s) => {
    const m = man.scenes[s.id] || {};
    const versions = (m.versions || []).map((k, i) => ({ v: i + 1, key: k, file: `clips/${s.id}-${k}.mp4` }))
      .filter((v) => fs.existsSync(path.join(proj.build, v.file)));
    return { id: s.id, label: s.label || s.id, role: s.role || null, dur: s.dur, frames: s.frames,
      start: s.startFrame / proj.fps, end: (s.startFrame + s.frames) / proj.fps, simulated: !!s.simulated,
      params: s.params || {}, schema: lockBound(m.schema || {}, s.bind), version: m.version || 0, key: m.current || null,
      clip: m.file ? m.file.replace(/^build\//, "") : null, versions, note: s.note || "",
      say: s.say || "", voiceAt: s.voiceAt ?? null, showsCredit: !!s.showsCredit };
  });
  return { title: proj.title, fps: proj.fps, seconds: proj.totalSec, global: proj.global, globalSchema: GLOBAL_SCHEMA,
    scenes, preview: preview && fs.existsSync(path.join(proj.build, "preview.mp4")) ? { ...preview, file: "preview.mp4" } : null,
    notes: j.notes, batches: j.batches, edits: j.edits.slice(-20), render: renderStatus, sound: await soundState(), takes: j.takes };
}

// ---- edits ---------------------------------------------------------------------------------------
function validate(def, value) {
  if (!def) return "not an editable param";
  if (def.type === "text") {
    if (typeof value !== "string") return "text expected";
    const v = value.trim();
    if (!v) return "cannot be empty";
    if (/[<>]/.test(v)) return "no HTML in copy";
    if (v.length > (def.maxLength || 160)) return `at most ${def.maxLength || 160} characters`;
    const words = v.replace(/[*_]/g, " ").split(/\s+/).filter(Boolean).length;
    const cap = def.maxWords || (def.role === "headline" ? 8 : 0);
    if (cap && words > cap) return `${words} words; this line holds at most ${cap}`;
    return null;
  }
  if (def.type === "number" || def.type === "seconds") {
    const n = Number(value);
    if (!Number.isFinite(n)) return "number expected";
    if (def.min != null && n < def.min) return `at least ${def.min}`;
    if (def.max != null && n > def.max) return `at most ${def.max}`;
    if (def.integer && !Number.isInteger(n)) return "whole number expected";
    return null;
  }
  if (def.type === "choice") return (def.options || []).some((o) => (o.value ?? o) === value) ? null : "not one of the options";
  if (def.type === "color") return /^#[0-9a-f]{6}$/i.test(value) && (!def.options || def.options.includes(value)) ? null : "pick one of the palette colours";
  return "unsupported param type " + def.type;
}

function applyEdit({ scene, param, value }) {
  const spec = JSON.parse(fs.readFileSync(proj.file, "utf8"));
  let from;
  if (!scene) {
    const err = validate(GLOBAL_SCHEMA[param], value);
    if (err) return { error: err };
    spec.global = spec.global || {};
    from = spec.global[param];
    spec.global[param] = GLOBAL_SCHEMA[param].type === "number" ? Number(value) : value;
  } else {
    const s = spec.scenes.find((x) => x.id === scene);
    if (!s) return { error: "no scene " + scene };
    if (param === "dur") {
      const err = validate({ type: "seconds", min: 0.5, max: 30 }, value);
      if (err) return { error: err };
      from = s.dur; s.dur = Math.round(Number(value) * 100) / 100;
    } else if (param === "say") {
      // A narration line is sound, not pixels: changing it re-renders nothing, but its old recording no
      // longer matches and drops out of the mix until the line is re-read in Voice Studio.
      const v = String(value ?? "").trim();
      if (v) { const err = validate({ type: "text", maxLength: 300, maxWords: 45 }, v); if (err) return { error: err }; }
      from = s.say || ""; if (v) s.say = v; else delete s.say;
    } else if (param === "voiceAt") {
      const err = validate({ type: "seconds", min: 0, max: 3 }, value);
      if (err) return { error: err };
      from = s.voiceAt ?? null; s.voiceAt = Math.round(Number(value) * 100) / 100;
    } else {
      const def = (readManifest(proj).scenes[scene] || {}).schema?.[param];
      // A Snowflake-bound value comes from the lock, and a param a scene declares editable:false is not the
      // reviewer's to change. The page hides both; this is the check that holds when a request skips the page.
      if (s.bind && param in s.bind) return { error: `${param} comes from Snowflake (${s.bind[param]}): change the SQL in video.json and re-resolve` };
      if (def && def.editable === false) return { error: `${param} is not editable` };
      const err = validate(def, value);
      if (err) return { error: err };
      // One page, many pieces: a continued shot shares its params, so write the param where the chain
      // already defines it (or on its first piece) and nowhere else, or loading would hit a conflict.
      let target = s;
      const chain = (proj.byId[scene] || {}).chain;
      if (chain) {
        const members = chain.map((id) => spec.scenes.find((x) => x.id === id)).filter(Boolean);
        target = members.find((m) => m.params && param in m.params) || members[0];
        for (const m of members) if (m !== target && m.params) delete m.params[param];
      }
      target.params = target.params || {};
      from = target.params[param] ?? def.value;
      target.params[param] = def.type === "number" || def.type === "seconds" ? Number(value) : typeof value === "string" ? value.trim() : value;
    }
  }
  writeJSONAtomic(proj.file, spec);
  const ev = append(ROOT, { kind: "edit", id: newId("e"), scene: scene || null, param, from, to: value });
  queueRender();
  return { ok: true, edit: ev };
}

// ---- background re-render ----------------------------------------------------------------------
let renderStatus = { status: "idle" }, rendering = null, again = false;
function queueRender() {
  if (rendering) { again = true; return; }
  renderStatus = { status: "rendering", since: new Date().toISOString() };
  append(ROOT, { kind: "render", status: "started" });
  push();
  let out = "";
  rendering = spawn(process.execPath, [path.join(MOTION_ROOT, "render.mjs"), "--project", ROOT, "--preview", "--jobs", "2"], { env: process.env });
  rendering.stdout.on("data", (d) => { out += d; });
  rendering.stderr.on("data", (d) => { out += d; });
  rendering.on("close", (code) => {
    const rendered = [...out.matchAll(/^RENDERED\s+(\S+)/gm)].map((m) => m[1]);
    renderStatus = code === 0 ? { status: "idle", last: new Date().toISOString(), rendered }
      : { status: "failed", message: out.split("\n").filter((l) => /FAILED|Error|error/.test(l)).slice(-3).join(" ") };
    append(ROOT, { kind: "render", status: code === 0 ? "done" : "failed", scenes: rendered, message: renderStatus.message });
    rendering = null;
    push();
    if (again) { again = false; queueRender(); }
  });
}

// ---- live updates --------------------------------------------------------------------------------
const clients = new Set();
let pushTimer = null;
function push() {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => { for (const res of clients) res.write(`event: state\ndata: ${Date.now()}\n\n`); }, 120);
}
for (const f of [journalPath(ROOT), path.join(proj.build, "manifest.json"), path.join(proj.build, "preview.json"), proj.file,
  path.join(ROOT, "audio", "voice", "lines.json")]) {
  fs.watchFile(f, { interval: 400 }, push); // watchFile survives the atomic renames that break fs.watch
}

// ---- http ----------------------------------------------------------------------------------------
function body(req) {
  return new Promise((resolve, reject) => {
    let d = "";
    req.on("data", (c) => { d += c; if (d.length > 64 * 1024) { reject(new Error("too large")); req.destroy(); } });
    req.on("end", () => { try { resolve(d ? JSON.parse(d) : {}); } catch (e) { reject(e); } });
  });
}
function rawBody(req, limit) {
  return new Promise((resolve, reject) => {
    const parts = []; let n = 0;
    req.on("data", (c) => { n += c.length; if (n > limit) { reject(new Error("recording too large")); req.destroy(); } else parts.push(c); });
    req.on("end", () => resolve(Buffer.concat(parts)));
    req.on("error", reject);
  });
}
const json = (res, code, obj) => { res.writeHead(code, { "content-type": "application/json", "cache-control": "no-store" }); res.end(JSON.stringify(obj)); };

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://127.0.0.1");
  const p = u.pathname;
  if (p === "/" || p === "/index.html") return sendFile(req, res, path.join(HERE, "index.html"));
  if (p === "/review.js" || p === "/review.css") return sendFile(req, res, path.join(HERE, p.slice(1)));
  if (p === "/voice" || p === "/voice.html") return sendFile(req, res, path.join(HERE, "voice.html"));
  if (p === "/voice.js" || p === "/voice.css") return sendFile(req, res, path.join(HERE, p.slice(1)));
  if (p === "/favicon.ico") { res.writeHead(204); return res.end(); }
  const authed = u.searchParams.get("k") === TOKEN || req.headers["x-review-key"] === TOKEN;
  if (!authed) return json(res, 403, { error: "missing or wrong review key" });
  try {
    if (p.startsWith("/media/")) {
      const f = path.resolve(proj.build, "." + path.sep + decodeURIComponent(p.slice(7)));
      if (!f.startsWith(proj.build + path.sep)) return json(res, 403, { error: "outside build/" });
      return sendFile(req, res, f);
    }
    if (p.startsWith("/voice-media/")) {
      const base = path.join(ROOT, "audio", "voice");
      const f = path.resolve(base, "." + path.sep + decodeURIComponent(p.slice(13)));
      if (!f.startsWith(base + path.sep)) return json(res, 403, { error: "outside audio/voice/" });
      return sendFile(req, res, f);
    }
    if (p === "/api/state") return json(res, 200, await state());
    if (p === "/api/sound" && req.method === "GET") return json(res, 200, await soundState());
    if (p === "/api/voice/take" && req.method === "POST") {
      const ext = TAKE_TYPES[String(req.headers["content-type"] || "").split(";")[0].trim()];
      if (!ext) return json(res, 415, { error: "send the recording as audio/webm, audio/ogg, audio/mp4 or audio/wav" });
      let meta;
      try { meta = JSON.parse(decodeURIComponent(String(req.headers["x-take-meta"] || ""))); } catch { return json(res, 400, { error: "missing or unreadable x-take-meta" }); }
      if (!meta || meta.version !== 3 || !["full", "line"].includes(meta.mode) || !Array.isArray(meta.markers) || !meta.markers.length || meta.markers.length > 200)
        return json(res, 400, { error: "take metadata must be version 3 with a mode and markers" });
      const audio = await rawBody(req, 80 << 20);
      if (audio.length < 2000) return json(res, 400, { error: "no audio was captured" });
      // One take at a time: ids are allocated and files written under a lock, so two saves never collide.
      const job = takeLock.then(async () => {
        const dir = path.join(ROOT, "audio", "voice", "takes");
        fs.mkdirSync(dir, { recursive: true });
        const nums = fs.readdirSync(dir).map((f) => (/^take-(\d+)\.json$/.exec(f) || [])[1]).filter(Boolean).map(Number);
        const id = "take-" + String(Math.max(0, ...nums) + 1).padStart(3, "0");
        const clean = { version: 3, mode: meta.mode, test: !!meta.test, audioSeconds: Number(meta.audioSeconds) || 0,
          scriptHash: String(meta.scriptHash || ""), device: String(meta.device || "").slice(0, 120), createdAt: new Date().toISOString(),
          markers: meta.markers.map((m) => ({ scene: String(m.scene || ""), lineHash: m.lineHash ? String(m.lineHash) : null,
            text: String(m.text || "").slice(0, 400), audioSeconds: Number(m.audioSeconds) })) };
        fs.writeFileSync(path.join(dir, id + ext), audio);           // the recording, before anything can fail
        fs.writeFileSync(path.join(dir, id + ".json"), JSON.stringify(clean, null, 2));
        const r = await py("voice.py", ["ingest", "--project", ROOT, "--take", id], { timeout: 180000 });
        return { id, r };
      });
      takeLock = job.catch(() => {});
      const { id, r } = await job;
      if (!r.ok) {
        append(ROOT, { kind: "take", take: id, mode: meta.mode, refused: pyError(r).replace(/^Refused: /, "") });
        push();
        return json(res, 422, { take: id, error: pyError(r).replace(/^Refused: /, ""), kept: true });
      }
      const summary = r.out || {};
      append(ROOT, { kind: "take", take: id, mode: meta.mode, test: !!summary.test, lines: (summary.lines || []).map((l) => l.scene),
        takeLufs: summary.takeLufs, clipped: !!summary.clipped });
      queueRender();
      return json(res, 200, { take: id, summary, sound: await soundState() });
    }
    if (p === "/api/events") {
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", connection: "keep-alive" });
      res.write("retry: 2000\n\n");
      clients.add(res);
      req.on("close", () => clients.delete(res));
      return;
    }
    if (req.method !== "POST") return json(res, 405, { error: "POST expected" });
    const b = await body(req);
    if (p === "/api/notes") {
      const text = String(b.text || "").trim();
      if (!text) return json(res, 400, { error: "empty note" });
      if (text.length > 2000) return json(res, 400, { error: "keep a note under 2000 characters" });
      const scene = b.scope === "global" ? null : String(b.scene || "");
      if (scene && !proj.byId[scene]) return json(res, 400, { error: "no scene " + scene });
      const ev = append(ROOT, { kind: "note", id: newId("n"), scope: scene ? "scene" : "global", scene,
        t: Number.isFinite(Number(b.t)) ? Math.round(Number(b.t) * 100) / 100 : null, text });
      push();
      return json(res, 200, ev);
    }
    if (p === "/api/note_update") {
      append(ROOT, { kind: "note_update", id: String(b.id), ...(b.deleted ? { deleted: true } : { text: String(b.text || "") }) });
      push();
      return json(res, 200, { ok: true });
    }
    if (p === "/api/send") {
      const j = reduce(readJournal(ROOT));
      const drafts = j.notes.filter((n) => n.status === "draft").map((n) => n.id);
      const sentEdits = new Set(j.batches.flatMap((x) => x.edits));
      const edits = j.edits.filter((e) => !sentEdits.has(e.id)).map((e) => e.id);
      const lastSend = j.batches.map((x) => x.ts).pop() || "";
      const newTakes = j.takes.filter((t) => t.ts > lastSend).length;
      if (!drafts.length && !edits.length && !newTakes) return json(res, 400, { error: "nothing to send" });
      const ev = append(ROOT, { kind: "send", batch: newId("b"), notes: drafts, edits });
      push();
      return json(res, 200, ev);
    }
    if (p === "/api/edit") {
      const r = applyEdit({ scene: b.scene || null, param: String(b.param || ""), value: b.value });
      return json(res, r.error ? 400 : 200, r);
    }
    if (p === "/api/render") { queueRender(); return json(res, 200, { ok: true }); }
    if (p === "/api/sound") {
      const change = {};
      for (const k of ["track", "gainDb", "edit", "narration"]) if (k in b) change[k] = b[k];
      if (!Object.keys(change).length) return json(res, 400, { error: "nothing to change" });
      const r = await py("sound.py", ["set", "--project", ROOT, "--json", JSON.stringify(change)]);
      if (!r.ok) return json(res, 400, { error: pyError(r) });
      append(ROOT, { kind: "edit", id: newId("e"), scene: null, param: "audio", from: r.out.changed.from, to: r.out.changed.to });
      queueRender();
      return json(res, 200, r.out);
    }
    if (p === "/api/voice/pick") {
      const r = await py("sound.py", ["pick", "--project", ROOT, "--scene", String(b.scene || ""), "--take", String(b.take || "")]);
      if (!r.ok) return json(res, 400, { error: pyError(r) });
      append(ROOT, { kind: "edit", id: newId("e"), scene: String(b.scene), param: "voice take", from: null, to: b.take || "newest" });
      queueRender();
      return json(res, 200, r.out);
    }
    if (p === "/api/sound/snap") {
      const r = await py("sound.py", ["snap", "--project", ROOT, "--to", b.to === "bar" ? "bar" : "beat", ...(b.apply ? ["--apply"] : [])]);
      if (!r.ok) return json(res, 400, { error: pyError(r) });
      if (b.apply && r.out.applied) {
        for (const [scene, [from, to]] of Object.entries(r.out.durations))
          append(ROOT, { kind: "edit", id: newId("e"), scene, param: "dur", from, to, why: "cuts on the " + r.out.to });
        queueRender();
      }
      return json(res, 200, r.out);
    }
    return json(res, 404, { error: "no route " + p });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
});
const port = Number(A.port || 8130);
server.on("error", (e) => {
  // Never touch .server.json here: it belongs to whichever server is running.
  console.error(e.code === "EADDRINUSE" ? `port ${port} is in use (another review server?): node review/agent.mjs url --project ${ROOT}, or pass --port` : String(e));
  process.exit(1);
});
server.listen(port, "127.0.0.1", () => {
  fs.writeFileSync(path.join(ROOT, "review", ".server.json"), JSON.stringify({ pid: process.pid, token: TOKEN, port }));
  console.log(`REVIEW http://127.0.0.1:${port}/?k=${TOKEN}`);
  console.log(`VOICE  http://127.0.0.1:${port}/voice?k=${TOKEN}`);
});

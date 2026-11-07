// drive_voice.mjs: record narration in the real Voice Studio page with Chrome's fake microphone, and check the
// whole sound loop: take saved -> split into lines -> mixed into the preview -> visible on the review page.
//
//   node referee/drive_voice.mjs --project <COPY of a project with "say" lines> [--voice "Zoe (Premium)"]
//
// Prereq: review/server.mjs running for that project. Run it on a copy: it saves takes and changes the music.
// The fake microphone plays a narration WAV built with macOS `say` at the cue times the page will show (after
// its 3-second countdown), so the page records a "person" reading on cue. The page runs with ?test=1, which
// marks every take TEST_ONLY: the final mix refuses them, so a test take can never ship.
// Checks: full take saved and split into one line per scene; the preview is re-mixed with voice; a one-line
// pick-up records only that line and becomes the line in the mix; the Sound panel switches the track.
import fs from "fs";
import os from "os";
import path from "path";
import { execFileSync } from "child_process";

const A = {}; const av = process.argv.slice(2);
for (let i = 0; i < av.length; i++) if (av[i].startsWith("--")) A[av[i].slice(2)] = av[++i];
const MOTION = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const AUDIO = path.join(MOTION, "audio");
const PROJ = path.resolve(A.project);
const VOICE = A.voice || "Zoe (Premium)";
const agent = (...a) => execFileSync("node", [path.join(MOTION, "review/agent.mjs"), ...a, "--project", PROJ], { encoding: "utf8" });
const py = (script, ...a) => JSON.parse(execFileSync("python3", [path.join(AUDIO, script), ...a], { encoding: "utf8" }).trim().split("\n").pop());
const base = agent("url").trim();
const K = new URL(base).searchParams.get("k");
const origin = new URL(base).origin;
const checks = []; const check = (name, ok, detail) => { checks.push({ name, ok: !!ok, detail }); console.error((ok ? "ok    " : "FAIL  ") + name + (detail ? "  " + JSON.stringify(detail).slice(0, 220) : "")); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- the "narrator": one WAV, every line at its cue, 3 s of countdown first, room tone throughout ----------------
const COUNTDOWN = 3.0;
const sound0 = py("sound.py", "state", "--project", PROJ);
const sc = sound0.script.scenes;
function narration(file, scenes, startAt, extra) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "vdrive-"));
  const parts = [];
  for (const s of scenes) {
    if (!s.say) continue;
    const f = path.join(tmp, s.id + ".aiff");
    execFileSync("say", ["-v", VOICE, "-o", f, s.say]);
    parts.push({ f, at: COUNTDOWN + (s.start - startAt) + s.lead + 0.05 });
  }
  const dur = COUNTDOWN + extra;
  const args = ["-v", "error", "-y", "-f", "lavfi", "-t", String(dur), "-i", "anoisesrc=color=pink:amplitude=0.0008:sample_rate=48000"];
  for (const p of parts) args.push("-i", p.f);
  const chains = parts.map((p, i) => `[${i + 1}:a]aresample=48000,adelay=${Math.round(p.at * 1000)}:all=1,volume=0.5[v${i}]`);
  const mix = `[0:a]${parts.map((_, i) => `[v${i}]`).join("")}amix=inputs=${parts.length + 1}:normalize=0:duration=first[out]`;
  args.push("-filter_complex", chains.concat(mix).join(";"), "-map", "[out]", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", file);
  const ff = execFileSync("python3", ["-c", "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())"], { encoding: "utf8" }).trim();
  execFileSync(ff, args);
  fs.rmSync(tmp, { recursive: true, force: true });
  return parts.map((p) => p.at);
}
const fullWav = path.join(os.tmpdir(), "vdrive-full.wav");
narration(fullWav, sc, 0, sound0.script.duration + 2.5);
const pickScene = sc.find((s) => s.id === (A.pickup || "aisle2")) || sc.find((s) => s.say);
const pickWav = path.join(os.tmpdir(), "vdrive-pick.wav");
narration(pickWav, [pickScene], pickScene.start, pickScene.dur + 2.0);

const { chromium } = await (await import(path.join(MOTION, "lib/playwright.mjs"))).playwright();
async function browser(wav) {
  return chromium.launch({ channel: "chrome", args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    `--use-file-for-fake-audio-capture=${wav}%noloop`, "--autoplay-policy=no-user-gesture-required"] });
}
const errors = [];
async function studio(b) {
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(`${origin}/voice?k=${K}&test=1`);
  await p.waitForFunction(() => window.__VOICE_READY__ === true, null, { timeout: 20000 });
  return p;
}
const takesBefore = (sound0.takes || []).length;

// ---- 1. a full take -------------------------------------------------------------------------------------------------
let b = await browser(fullWav);
let p = await studio(b);
check("studio lists every scene", (await p.$$eval("#chapters button", (n) => n.length)) === sc.length);
await p.click("#reset");
await p.click("#record");
const t0 = Date.now();
await p.waitForFunction(() => document.querySelector("#status").textContent === "Recording", null, { timeout: 15000 });
check("countdown then recording", true, { afterMs: Date.now() - t0 });
await p.waitForFunction(() => /^take-\d+:/.test(document.querySelector("#results .take-row .lines")?.textContent || "") ||
  document.querySelector("#results .take-row .lines.bad"), null, { timeout: (sound0.script.duration + 40) * 1000 });
const fullMsg = await p.textContent("#results .take-row .lines");
check("the page reports the take's lines and level", /^take-\d+: open, /.test(fullMsg) && /LUFS/.test(fullMsg) && !/undefined/.test(fullMsg), fullMsg);
await b.close();
const s1 = py("sound.py", "state", "--project", PROJ);
const fullTake = s1.takes[0];
const said = sc.filter((s) => s.say).map((s) => s.id);
check("one line per spoken scene", JSON.stringify((fullTake.lines || []).map((l) => l.scene)) === JSON.stringify(said), fullTake.lines);
check("take is marked TEST_ONLY", fullTake.test === true);
const reactions = (fullTake.lines || []).map((l) => l.reaction);
check("every line started 0.2-0.9 s after its cue (narrator at lead + 0.05 s; MediaRecorder start latency)",
  reactions.every((r) => r > 0.2 && r < 0.9), reactions);
const rows = Object.fromEntries(s1.lines.scenes.map((r) => [r.scene, r]));
check("every line fits its scene", said.every((id) => rows[id].status === "ok"), said.map((id) => [id, rows[id].status]));

// the server re-mixes the preview on its own after a take
const key0 = (sound0.preview || {}).key || null;
const deadline = Date.now() + 180000;
let pv = null;
while (Date.now() < deadline) {
  pv = py("sound.py", "state", "--project", PROJ).preview;
  if (pv && pv.key && pv.key !== key0 && pv.narrated) break;
  await sleep(1500);
}
check("preview mix changed after the take (new mix key)", pv && pv.key && pv.key !== key0, { before: key0, after: pv && pv.key });
check("preview re-mixed with the voice", pv && pv.narrated, pv && { lufs: pv.lufs, lines: pv.lines });
check("preview loudness at the narrated target", pv && Math.abs(pv.lufs - -16) <= 1.0, pv && pv.lufs);

// ---- 2. a one-line pick-up ------------------------------------------------------------------------------------------
b = await browser(pickWav);
p = await studio(b);
const idx = sc.findIndex((s) => s.id === pickScene.id);
await p.click(`#chapters button:nth-child(${idx + 1})`);
await p.click("#pickup");
await p.waitForFunction(() => /^take-\d+:/.test(document.querySelector("#results .take-row .lines")?.textContent || "") ||
  document.querySelector("#results .take-row .lines.bad"), null, { timeout: 60000 });
const pickMsg = await p.textContent("#results .take-row .lines");
check("pick-up saved, and the page names its line", new RegExp("^take-\\d+: " + pickScene.id + " ").test(pickMsg) && !/undefined/.test(pickMsg), pickMsg);
await b.close();
const s2 = py("sound.py", "state", "--project", PROJ);
const pickTake = s2.takes[0];
check("pick-up recorded only its line", JSON.stringify((pickTake.lines || []).map((l) => l.scene)) === JSON.stringify([pickScene.id]), pickTake.lines);
const prow = s2.lines.scenes.find((r) => r.scene === pickScene.id);
check("the newest take of that line is the one in the mix", prow.take === pickTake.take && prow.takes.length >= 2, { take: prow.take, takes: prow.takes });

// ---- 3. the Sound panel switches the music --------------------------------------------------------------------------
b = await chromium.launch({ channel: "chrome" });
p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
p.on("pageerror", (e) => errors.push(e.message));
await p.goto(base);
await p.waitForSelector("#track option[value='night_owl']", { state: "attached", timeout: 20000 });
check("Sound panel shows the library", (await p.$$eval("#track option", (o) => o.length)) >= 6);
check("scene cards show their line status", (await p.$$eval(".badge.line:not([hidden])", (n) => n.length)) === said.length);
const keysBefore = Object.fromEntries(Object.entries(JSON.parse(fs.readFileSync(path.join(PROJ, "build/manifest.json"), "utf8")).scenes).map(([k, v]) => [k, v.current]));
await p.selectOption("#track", "night_owl");
const t1 = Date.now();
let after = null;
while (Date.now() - t1 < 180000) {
  after = py("sound.py", "state", "--project", PROJ);
  if (after.preview && after.preview.music && after.preview.music.track === "night_owl") break;
  await sleep(1500);
}
check("track switch re-mixed the preview", after.preview && after.preview.music && after.preview.music.track === "night_owl",
  { seconds: Math.round((Date.now() - t1) / 100) / 10 });
const man = JSON.parse(fs.readFileSync(path.join(PROJ, "build/manifest.json"), "utf8")).scenes;
const rekeyed = Object.keys(man).filter((k) => man[k].current !== keysBefore[k]);
const credit = sc.length && Object.entries(man).filter(([, e]) => (e.screenText || []).some((t) => /Night Owl/.test(t))).map(([k]) => k);
check("only the scene that shows the credit re-rendered", rekeyed.length === 1 && credit.length === 1 && rekeyed[0] === credit[0], { rekeyed, credit });
await b.close();
check("no page errors", !errors.length, errors.slice(0, 3));

const failed = checks.filter((c) => !c.ok).length;
console.log(JSON.stringify({ passed: checks.length - failed, failed, checks }, null, 1));
process.exitCode = failed ? 1 : 0;

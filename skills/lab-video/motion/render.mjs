#!/usr/bin/env node
// render.mjs: render a motion-mode project, scene by scene, re-rendering only what changed.
//
//   node render.mjs --project ~/videos/my_video            # every scene whose inputs changed
//   node render.mjs --project P --scene open,store                   # just these (if changed)
//   node render.mjs --project P --scene cta --force                  # re-render even if cached
//   node render.mjs --project P --stills                             # storyboard frames, no clips
//   node render.mjs --project P --verify                             # prove frames are reproducible
//   node render.mjs --project P --preview                            # then rebuild build/preview.mp4
//   flags: --jobs 2  --software  --proxy  --quiet
//
// Prints one RENDERED/CACHED line per scene and TOTAL_S at the end: pass that to probe.py --expect-sec.
// Needs PLAYWRIGHT_PATH (or a resolvable "playwright") and an ffmpeg (see lib/ffmpeg.mjs).
import fs from "fs";
import path from "path";
import { spawnSync } from "child_process";
import { loadProject, sceneKey, readManifest, recordRender, withLock, MOTION_ROOT } from "./lib/project.mjs";
import { launch, renderScene, renderStills, verifyScene } from "./lib/capture.mjs";
import { startSceneServer } from "./lib/serve.mjs";

const A = { jobs: "2" };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  const k = argv[i];
  if (!k.startsWith("--")) continue;
  const v = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
  A[k.slice(2)] = v;
}
if (!A.project) { console.error("usage: node render.mjs --project <dir> [--scene a,b] [--force] [--stills] [--verify] [--preview] [--jobs N] [--software] [--proxy]"); process.exit(2); }

let proj;
try { proj = loadProject(A.project); } catch (e) { console.error("FAILED    " + e.message); process.exit(2); }
const only = A.scene && A.scene !== true ? new Set(String(A.scene).split(",")) : null;
if (only) for (const id of only) if (!proj.byId[id]) { console.error("no scene " + id + " in " + proj.file); process.exit(2); }
const log = (...m) => { if (!A.quiet) console.log(...m); };

await withLock(proj, async () => {
  const { browser, renderer, kind } = await launch({ software: !!A.software });
  const srv = await startSceneServer(proj);
  const manifest = readManifest(proj);
  const rkey = kind === "software" ? "sw" : "gpu";
  const clipDir = path.join(proj.build, A.proxy ? "proxy" : "clips");
  const todo = [];
  for (const s of proj.scenes) {
    if (only && !only.has(s.id)) continue;
    const key = sceneKey(proj, s, { renderer: rkey, proxy: !!A.proxy });
    const out = path.join(clipDir, `${s.id}-${key}.mp4`);
    todo.push({ s, key, out, cached: !A.force && fs.existsSync(out) });
  }
  log(`project   ${proj.title}  (${proj.scenes.length} scenes, ${proj.totalSec.toFixed(2)}s at ${proj.fps} fps)`);
  log(`renderer  ${renderer}`);
  let failed = 0;
  try {
    if (A.stills) {
      for (const t of todo) {
        const marks = t.s.stills || [0.15, 0.5, 0.92].map((p) => Math.round(p * (t.s.frames - 1)));
        const files = await renderStills(proj, t.s, { browser, origin: srv.origin, frames: marks, outDir: path.join(proj.build, "stills"), key: t.key });
        log(`STILLS    ${t.s.id.padEnd(12)} ${files.map((f) => path.relative(proj.root, f)).join("  ")}`);
      }
      return;
    }
    const queue = todo.filter((t) => !t.cached);
    // What a render saw (its declared params and the text on screen) belongs to that render's key. A cache hit
    // restores the text of the clip it reuses: the manifest otherwise kept the text of whichever version was
    // rendered last, so re-selecting a cached end card showed a credit for the wrong track.
    const metaFile = (s, key) => path.join(proj.build, "frames", `${s.id}-${key}.meta.json`);
    for (const t of todo.filter((t) => t.cached)) {
      const mf = metaFile(t.s, t.key), prev = manifest.scenes[t.s.id] || {};
      let seen = null;
      try { seen = JSON.parse(fs.readFileSync(mf, "utf8")); } catch { /* rendered before sidecars existed */ }
      if (!seen && prev.current === t.key) {
        seen = { schema: prev.schema, meta: prev.meta, screenText: prev.screenText, screenTextLast: prev.screenTextLast, lowestText: prev.lowestText };
        fs.mkdirSync(path.dirname(mf), { recursive: true });
        fs.writeFileSync(mf, JSON.stringify(seen));
      }
      const e = recordRender(proj, t.s, t.key, { file: path.relative(proj.root, t.out), renderer: rkey,
        ...(seen ? { screenTextLast: undefined, lowestText: undefined, ...seen } : { screenText: undefined, screenTextLast: undefined, lowestText: undefined }) });
      log(`CACHED    ${t.s.id.padEnd(12)} v${e.version}  ${path.relative(proj.root, t.out)}` + (seen ? "" : "  (on-screen text unknown: --force to re-read it)"));
    }
    const workers = Array.from({ length: Math.max(1, Math.min(Number(A.jobs) || 1, queue.length)) }, async () => {
      for (let t; (t = queue.shift());) {
        try {
          const r = await renderScene(proj, t.s, { browser, origin: srv.origin, out: t.out, proxy: !!A.proxy });
          const hashFile = path.join(proj.build, "frames", `${t.s.id}-${t.key}.json`);
          fs.mkdirSync(path.dirname(hashFile), { recursive: true });
          fs.writeFileSync(hashFile, JSON.stringify(r.hashes));
          if (r.thumbs && r.thumbs.length) fs.writeFileSync(hashFile.replace(/\.json$/, ".thumbs"), r.thumbs);
          fs.writeFileSync(metaFile(t.s, t.key), JSON.stringify({ ...(r.declared ? { schema: r.declared.schema, meta: r.declared.meta } : {}),
            ...(r.screenText ? { screenText: r.screenText } : {}), ...(r.screenTextLast ? { screenTextLast: r.screenTextLast } : {}),
            ...(r.lowestText != null ? { lowestText: r.lowestText } : {}) }));
          const e = recordRender(proj, t.s, t.key, { file: path.relative(proj.root, t.out), renderer: rkey,
            msPerFrame: Math.round(r.ms / t.s.frames), renderedAt: new Date().toISOString(), pageErrors: r.errors.slice(0, 5),
            ...(r.declared ? { schema: r.declared.schema, meta: r.declared.meta } : {}),
            ...(r.screenText ? { screenText: r.screenText } : {}), screenTextLast: r.screenTextLast, lowestText: r.lowestText });
          log(`RENDERED  ${t.s.id.padEnd(12)} v${e.version}  ${t.s.frames} frames in ${(r.ms / 1000).toFixed(1)}s (${Math.round(r.ms / t.s.frames)} ms/f)  ${path.relative(proj.root, t.out)}`
            + (r.errors.length ? `\n          page errors: ${r.errors.slice(0, 3).join(" | ")}` : ""));
        } catch (e) {
          failed++;
          console.error(`FAILED    ${t.s.id}: ${e.message}`);
        }
      }
    });
    await Promise.all(workers);
    if (A.verify) {
      for (const t of todo) {
        const hf = path.join(proj.build, "frames", `${t.s.id}-${t.key}.json`);
        if (!fs.existsSync(hf)) { log(`VERIFY    ${t.s.id.padEnd(12)} skipped: no frame hashes (re-render with --force)`); continue; }
        const tf = hf.replace(/\.json$/, ".thumbs");
        const v = await verifyScene(proj, t.s, { browser, origin: srv.origin, expected: JSON.parse(fs.readFileSync(hf, "utf8")),
          expectedThumbs: fs.existsSync(tf) ? fs.readFileSync(tf) : null });
        if (v.mismatches.length) failed++;
        const ok = v.checked - v.mismatches.length;
        log(`VERIFY    ${t.s.id.padEnd(12)} ${ok}/${v.checked} frames match (${v.exact} exact`
          + (v.noise.length ? `, ${v.noise.length} within compositor noise, max Δ${v.worst}` : "") + ")"
          + (v.mismatches.length ? `  MISMATCH at ${v.mismatches.slice(0, 8).join(",")} (max Δ${v.worst}): the scene is not a pure function of its frame` : ""));
      }
    }
  } finally {
    await browser.close();
    await srv.close();
  }
  if (!A.stills) log(`TOTAL_S   ${proj.totalSec.toFixed(3)}  (${proj.totalFrames} frames)`);
  if (A.preview && !failed && !A.stills) {
    const r = spawnSync("python3", [path.join(MOTION_ROOT, "assemble.py"), "--project", proj.root, "--preview"], { encoding: "utf8" });
    process.stdout.write(r.stdout || ""); process.stderr.write(r.stderr || "");
    if (r.status !== 0) failed++;
  }
  if (failed) process.exitCode = 1;
});

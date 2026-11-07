// Generic scene-spec -> 1920x1080 webm renderer for the video-trailer skill.
// Usage:
//   node scripts/render.mjs --scenes references/foo.scenes.json [--brand snowflake] --out _trailer.webm
//   (Playwright: $PLAYWRIGHT_PATH if set, else what motion/setup.sh installed; see motion/lib/playwright.mjs)
//
// The scenes JSON may be a bare array, or {brand, foot, scenes:[...]}.
// Brand tokens + inlined logo SVGs are injected into the template as window.__BRAND__.
//
// Optional flags:
//   --layout presenter   confine content to the left column, reserving the right
//                        672px for a talking head (default: center)
//   --dur-scale 2.4      multiply every scene duration, so one spec yields both a
//                        tight trailer and a slower talk-track bed
//   --mode present       manual key-advance instead of a timed run. Only useful
//                        with --emit-html; a video render would never terminate.
//   --guides             draw the camera-zone outline (presenter layout only).
//                        Never use this for a deliverable video — it burns in.
//   --emit-html <path>   write the self-contained injected HTML and exit without
//                        launching a browser. Logos are inlined and there are no
//                        external fonts or images, so it opens over file://
import fs from "fs";
import path from "path";
import http from "http";
import url from "url";

const argv = Object.fromEntries(process.argv.slice(2).reduce((a,v,i,arr)=>{
  if(v.startsWith("--")){a.push([v.slice(2),arr[i+1]]);}return a;},[]));
const SKILL = path.resolve(argv.skill || path.join(path.dirname(url.fileURLToPath(import.meta.url)), ".."));
const OUT = path.resolve(argv.out || "_trailer.webm");
const scenesPath = path.resolve(argv.scenes);
const LAYOUT = argv.layout || "center";
const MODE = argv.mode || "auto";
const GUIDES = Object.prototype.hasOwnProperty.call(argv, "guides");
const DUR_SCALE = argv["dur-scale"] ? Number(argv["dur-scale"]) : 1;
const EMIT_HTML = argv["emit-html"] ? path.resolve(argv["emit-html"]) : null;
if (!Number.isFinite(DUR_SCALE) || DUR_SCALE <= 0) {
  console.error("--dur-scale must be a positive number"); process.exit(1);
}

const template = fs.readFileSync(path.join(SKILL, "templates", "trailer.html"), "utf8");
const brands = JSON.parse(fs.readFileSync(path.join(SKILL, "assets", "brands.json"), "utf8"));
let spec = JSON.parse(fs.readFileSync(scenesPath, "utf8"));
const scenes = Array.isArray(spec) ? spec : (spec.scenes || []);
const foot = Array.isArray(spec) ? "" : (spec.foot || "");
const brandName = argv.brand || (Array.isArray(spec) ? null : spec.brand);
const brand = Object.assign({}, brandName ? (brands[brandName] || {}) : {});
if (DUR_SCALE !== 1) scenes.forEach(s => { s.dur = Math.round((s.dur || 4000) * DUR_SCALE); });
const TOTAL_S = scenes.reduce((a,s)=>a+(s.dur||4000),0)/1000;
// inline logo SVGs from files
for (const [k, f] of [["logoLeft","logoLeftFile"],["logoRight","logoRightFile"]]) {
  if (brand[f]) { try { brand[k] = fs.readFileSync(path.join(SKILL, brand[f]), "utf8"); } catch(e){ console.log("logo missing:", brand[f]); } }
}

const inject = `<script>window.__SCENES__=${JSON.stringify(scenes)};`
  + `window.__BRAND__=${JSON.stringify(brand)};`
  + `window.__FOOT__=${JSON.stringify(foot)};`
  + `window.__LAYOUT__=${JSON.stringify(LAYOUT)};`
  + `window.__MODE__=${JSON.stringify(MODE)};`
  + `window.__GUIDES__=${GUIDES};</script>`;
const html = template.replace(/<\/head>/i, inject + "</head>");

if (EMIT_HTML) {
  fs.writeFileSync(EMIT_HTML, html);
  console.log("EMITTED", EMIT_HTML, fs.statSync(EMIT_HTML).size, "bytes",
    "| layout:", LAYOUT, "| mode:", MODE, "| scenes:", scenes.length, "| TOTAL_S:", TOTAL_S.toFixed(1));
  process.exit(0);
}
if (MODE === "present") {
  console.error("--mode present never terminates; use it with --emit-html, not a video render.");
  process.exit(1);
}

// The same lookup as motion mode ($PLAYWRIGHT_PATH, then ~/.lab-video where setup.sh installs it, then a plain
// import), so a clean machine that ran setup.sh renders here too.
const { playwright } = await import(new URL("../../motion/lib/playwright.mjs", import.meta.url).href);
const { chromium } = await playwright();
fs.writeFileSync(path.join(SKILL, "_render.html"), html);

const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",".svg":"image/svg+xml",".json":"application/json",".png":"image/png"};
const server = http.createServer((req,res)=>{
  const f = path.join(SKILL, decodeURIComponent((req.url||"/").split("?")[0]));
  fs.readFile(f,(e,d)=>{ if(e){res.writeHead(404);res.end();return;} res.writeHead(200,{"content-type":MIME[path.extname(f)]||"application/octet-stream"});res.end(d); });
});
await new Promise(r=>server.listen(8121,"127.0.0.1",r));

const vdir = path.join(SKILL, "_vid");
fs.rmSync(vdir,{recursive:true,force:true}); fs.mkdirSync(vdir,{recursive:true});
const browser = await chromium.launch({ args:["--no-sandbox","--force-color-profile=srgb","--hide-scrollbars"] });
const ctx = await browser.newContext({ viewport:{width:1920,height:1080}, deviceScaleFactor:1, recordVideo:{dir:vdir,size:{width:1920,height:1080}} });
const page = await ctx.newPage();
page.on("pageerror",e=>console.log("PAGEERROR:",e.message));
await page.goto("http://127.0.0.1:8121/_render.html");
const t0=Date.now();
// Wait cap must exceed the spec's own length, or the page gets closed mid-run and
// the video is silently truncated (a hardcoded 120s cap used to eat the final
// scenes of anything longer than two minutes). Derive it, with generous headroom.
const WAIT_MS = Math.max(120000, Math.round(TOTAL_S * 1000 * 1.6) + 20000);
while(Date.now()-t0 < WAIT_MS){ if(await page.evaluate(()=>window.__DONE__===true).catch(()=>false)) break; await page.waitForTimeout(400); }
if(!(await page.evaluate(()=>window.__DONE__===true).catch(()=>false)))
  console.log("WARNING: timed out after", (WAIT_MS/1000).toFixed(0)+"s without __DONE__ — video may be truncated");
await page.waitForTimeout(400);
await page.close(); await ctx.close(); await browser.close(); server.close();
fs.rmSync(path.join(SKILL,"_render.html"),{force:true});
const webm = fs.readdirSync(vdir).find(f=>f.endsWith(".webm"));
if(!webm){ console.error("NO_VIDEO"); process.exit(1); }
fs.copyFileSync(path.join(vdir,webm), OUT);
fs.rmSync(vdir,{recursive:true,force:true});
console.log("RENDERED", OUT, fs.statSync(OUT).size, "bytes");

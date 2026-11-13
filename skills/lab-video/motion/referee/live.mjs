#!/usr/bin/env node
// Live-capture referee: film a local fixture through the same attach path as a signed-in Chrome, and check that the
// scan and the gate catch what must not travel. No Snowflake account or network needed.
//
//   node referee/live.mjs        # about a minute; every check must PASS
//
// The fixture mimics a Streamlit-in-Snowflake app: a shell page on one origin, the app in an iframe on another.
//   clean    the take is promoted, and the iframe's text (cross-origin) is in it
//   leak     a blocked word appears inside the iframe after a click: refused, kept for inspection, not promoted
//   email    an email address appears: refused even though the project never listed it
//   canvas   a blocked word drawn on a canvas (no DOM text): only OCR can see it, so this checks OCR (skipped where
//            this machine has no Apple Vision)
//   gate     a word blocked after filming FAILs the gate on the stored take; changed steps are flagged
// Work happens in a temp directory; nothing is written into the skill.
import fs from "fs";
import os from "os";
import path from "path";
import http from "http";
import url from "url";
import { spawn, spawnSync } from "child_process";
import { playwright } from "../lib/playwright.mjs";

const MOTION = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
let pass = 0, total = 0;
const check = (ok, what, detail = "") => { total++; if (ok) pass++; console.log(`${ok ? "PASS" : "FAIL"}  ${what}${detail ? "  " + detail : ""}`); };

const APP = (q) => `<!doctype html><meta charset="utf-8"><body style="font:28px -apple-system,Helvetica,Arial;margin:40px">
<h2>Fixture App</h2><button id="next" style="font:inherit;padding:10px 24px">Next</button><p id="out"></p>
<canvas id="c" width="900" height="140"></canvas><script>
document.getElementById("next").onclick = () => {
  const m = ${JSON.stringify(q)}, out = document.getElementById("out");
  out.textContent = "Step two" + (m === "leak" ? " · owner ACMECORP" : m === "email" ? " · contact jane.doe@example.com" : "");
  if (m === "canvas") { const g = document.getElementById("c").getContext("2d"); g.font = "bold 96px Helvetica"; g.fillText("ZEBRAFISH", 10, 110); }
};</script></body>`;
const serve = (handler) => new Promise((resolve) => { const s = http.createServer(handler); s.listen(0, "127.0.0.1", () => resolve(s)); });
const appSrv = await serve((req, res) => {
  const q = new URL(req.url, "http://x").searchParams.get("mode") || "clean";
  res.writeHead(200, { "content-type": "text/html" });
  res.end(req.url.startsWith("/hidden") ? "<!doctype html><body>Hidden owner ACMECORP</body>" : APP(q));
});
const appOrigin = `http://127.0.0.1:${appSrv.address().port}`;
const shell = await serve((req, res) => {
  const q = new URL(req.url, "http://x").searchParams.get("mode") || "clean";
  // csp: a page whose policy forbids every script, as Snowsight forbids inline ones; a bookmark still runs on it.
  // secret: a password field and a plain one; the page prints only how many characters arrived, never the value.
  res.writeHead(200, { "content-type": "text/html", ...(q === "csp" ? { "content-security-policy": "script-src 'none'" } : {}) });
  const fields = q === "secret" ? `<div style="padding:0 24px"><input id="pw" type="password"> <input id="tx" type="text"> <span id="typed"></span></div>
<script>for (const id of ["pw", "tx"]) document.getElementById(id).oninput = (e) =>
  { document.getElementById("typed").textContent = id + " typed " + e.target.value.length; };</script>`
    // shadow: words drawn inside an open shadow root on a host mounted on <html>, as Snowsight Packs mount theirs
    : q === "shadow" ? `<script>const h = document.createElement("div"); document.documentElement.appendChild(h);
  h.attachShadow({ mode: "open" }).innerHTML = "<style>p{color:#333}</style><p>Pack card for ACMECORP</p>";</script>`
    // hidden: a frame no one can see, as Snowsight Packs read Snowsight pages in a hidden frame
    : q === "hidden" ? `<iframe src="${appOrigin}/hidden" style="display:none"></iframe>` : "";
  res.end(`<!doctype html><meta charset="utf-8"><body style="margin:0;font:22px Helvetica"><div style="padding:14px 24px">Fixture shell</div>${fields}
<iframe src="${appOrigin}/app?mode=${q}" style="width:100%;height:640px;border:0"></iframe></body>`);
});
const shellUrl = `http://127.0.0.1:${shell.address().port}/`;

// A real Chrome process with a debugging port, started the way a person starts theirs (patterns/snowflake.md):
// Playwright's own launch talks over a pipe and never opens the port live.mjs attaches to.
const port = 9400 + Math.floor(Math.random() * 400);
const { chromium } = await playwright();
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "lv-live-chrome-"));
const chrome = spawn(chromium.executablePath(), ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--site-per-process", "about:blank"], { stdio: "ignore" });
for (let i = 0; i < 60; i++) {
  try { if ((await fetch(`http://127.0.0.1:${port}/json/version`)).ok) break; } catch { await new Promise((r) => setTimeout(r, 250)); }
}
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "lv-live-referee-"));
const root = path.join(tmp, "p");
fs.mkdirSync(path.join(root, "scenes"), { recursive: true });
fs.writeFileSync(path.join(root, "scenes", "cta.html"), "<!doctype html><body>cta</body>");
const steps = [{ pause: 0.4 }, { click: "Next" }, { wait: "Step two" }, { pause: 1.4 }];
const spec = (mode, extra = {}) => ({ title: "live referee", fps: 30, width: 1280, height: 720, audio: { mode: "silent" },
  gate: { block: ["ACMECORP", "ZEBRAFISH"], ...(extra.gate || {}) },
  scenes: [{ id: "app", kind: "footage", src: "takes/app/current.mp4", dur: 1.5,
    capture: { url: shellUrl + "?mode=" + mode, zoom: 1, setup: [{ wait: "Fixture App" }], steps: extra.steps || steps } },
  { id: "cta", role: "cta", src: "scenes/cta.html", dur: 1 }] });
// Asynchronous on purpose: the fixture's servers live in this process, and a blocking spawnSync would stop them
// from answering the Chrome that live.mjs is driving.
const film = (mode, extra) => new Promise((resolve) => {
  fs.writeFileSync(path.join(root, "video.json"), JSON.stringify(spec(mode, extra), null, 1));
  const p = spawn("node", [path.join(MOTION, "snowflake", "live.mjs"), "--project", root, "--scene", "app", "--port", String(port)]);
  let out = "";
  p.stdout.on("data", (d) => { out += d; });
  p.stderr.on("data", (d) => { out += d; });
  const timer = setTimeout(() => p.kill(), 180000);
  p.on("close", (code) => { clearTimeout(timer); resolve({ code, out }); });
});
const gate = (...extra) => {
  const r = spawnSync("python3", [path.join(MOTION, "gate.py"), "--project", root, ...extra], { encoding: "utf8" });
  return { code: r.status, fails: (r.stdout || "").split("\n").filter((l) => l.startsWith("FAIL")), out: r.stdout || "" };
};
const current = () => { try { return JSON.parse(fs.readFileSync(path.join(root, "takes", "app", "current.json"), "utf8")); } catch { return null; } };

try {
  let r = await film("clean");
  const cur = current();
  check(r.code === 0 && cur, "a clean take is filmed and promoted", r.code ? r.out.trim().split("\n").slice(-2).join(" | ") : "");
  if (cur) {
    const lines = cur.text.map((t) => t.line);
    check(lines.includes("Fixture App") && lines.some((l) => l.startsWith("Step two")), "text inside the cross-origin iframe was read");
    check(cur.seconds >= 1.5 && cur.frames >= 3 && fs.existsSync(path.join(root, "takes", "app", "current.mp4")),
      `the take is long enough and on disk (${cur.seconds}s, ${cur.frames} frames)`);
    check(cur.scan && cur.scan.ok === true, "it was scanned clean");
  }

  r = await film("leak");
  check(r.code === 1 && /HIT.*ACMECORP/.test(r.out), "a blocked word inside the iframe is refused", r.out.match(/HIT.*$/m)?.[0] || r.out.slice(-160));
  check(current() && current().take === 1 && /NOT promoted/.test(r.out), "and the refused take is kept but not promoted");

  r = await film("email");
  check(r.code === 1 && /an email address/.test(r.out), "an email address is refused without being listed");

  const vision = spawnSync("python3", ["-c", "import Vision, Quartz"]).status === 0;
  r = await film("canvas");
  if (vision) {
    // OCR misreads letters (Vision has read this as ZEBBASISH): an exact or a "looks like it" hit both count.
    check(r.code === 1 && /HIT\s+ZEBRAFISH \(OCR[,)]/.test(r.out), "a blocked word drawn on a canvas is caught by OCR alone",
      r.out.match(/^HIT.*$/m)?.[0] || r.out.slice(-200));
  } else {
    console.log("SKIP  canvas text: no Apple Vision in this Python, so OCR cannot run here");
  }

  fs.writeFileSync(path.join(root, "video.json"), JSON.stringify(spec("clean", { gate: { block: ["ACMECORP", "ZEBRAFISH", "Step two"] } }), null, 1));
  let g = gate();
  check(g.fails.some((f) => /app \(filmed\).*blocked by this project/.test(f)), "a word blocked after filming FAILs the gate on the stored take",
    g.fails[0] || "");
  fs.writeFileSync(path.join(root, "video.json"), JSON.stringify(spec("clean", { steps: [...steps.slice(0, 3), { pause: 2.0 }] }), null, 1));
  g = gate();
  check(/capture block changed after take \d+ was filmed/.test(g.out), "steps changed after filming are flagged");
  g = gate("--final");
  check(g.fails.some((f) => /capture block changed/.test(f)), "and FAIL on --final");

  // A bookmarklet from a file runs the way a bookmark click does, through a policy that forbids every script, and
  // the take names the file and its hash but never carries the code (CODEMARK is a comment that never renders).
  fs.writeFileSync(path.join(root, "pack.txt"),
    "javascript:/*CODEMARK*/void(document.body.insertAdjacentHTML('beforeend','<p id=\"packed\">Pack on</p>'))");
  r = await film("csp", { steps: [{ pause: 0.3 }, { bookmarklet: "pack.txt" }, { wait: "Pack on" }, { pause: 0.6 }] });
  let tk = current();
  check(r.code === 0 && tk && tk.text.some((t) => t.line === "Pack on"), "a bookmarklet runs on a page whose CSP forbids scripts",
    r.code ? r.out.trim().split("\n").slice(-2).join(" | ") : "");
  const ran = tk && tk.steps.find((x) => x.step === "bookmarklet");
  check(ran && ran.target.file === "pack.txt" && /^[0-9a-f]{16}$/.test(ran.target.sha256) && !JSON.stringify(tk).includes("CODEMARK"),
    "the take records the bookmarklet's file and hash, never its code");

  // A secret from a dotenv file reaches the password field and nothing else: not the take, the log or the console.
  const SECRET = "s3cr3t-VALUE-0042";
  fs.writeFileSync(path.join(root, "ref.env"), `OTHER=1\nREF_SECRET="${SECRET}"\n`);
  r = await film("secret", { steps: [{ pause: 0.2 }, { type: { env: "REF_SECRET", file: "ref.env" }, into: { css: "#pw" } },
    { wait: "pw typed " + SECRET.length }, { pause: 0.4 }] });
  tk = current();
  const takeDir = path.join(root, "takes", "app");
  const leaked = r.out.includes(SECRET) || fs.readdirSync(takeDir).filter((f) => f.endsWith(".json"))
    .some((f) => fs.readFileSync(path.join(takeDir, f), "utf8").includes(SECRET));
  check(r.code === 0 && tk && tk.text.some((t) => t.line === "pw typed " + SECRET.length), "a secret from a dotenv file is typed into the password field",
    r.code ? r.out.trim().split("\n").slice(-2).join(" | ") : "");
  check(!leaked && tk && JSON.stringify(tk.steps).includes("REF_SECRET"), "and only its name is recorded, nowhere its value");
  r = await film("secret", { steps: [{ type: { env: "REF_SECRET", file: "ref.env" }, into: { css: "#tx" } }] });
  check(r.code !== 0 && /only typed into a password field/.test(r.out) && !r.out.includes(SECRET),
    "a secret is refused for a field that would show it");

  // Text a page draws in an open shadow root is on screen, so it is scanned; a frame no one can see is not.
  r = await film("shadow");
  check(r.code === 1 && /HIT.*ACMECORP/.test(r.out), "a blocked word inside an open shadow root is refused",
    r.out.match(/^HIT.*$/m)?.[0] || r.out.trim().split("\n").slice(-1)[0]);
  r = await film("hidden");
  tk = current();
  check(r.code === 0 && tk && tk.dom.hiddenFrameSkips > 0 && !tk.text.some((t) => /ACMECORP/.test(t.line)),
    "a hidden frame's text is not counted as on screen", r.code ? r.out.trim().split("\n").slice(-2).join(" | ") : "");
} finally {
  const gone = new Promise((r) => chrome.once("exit", r));
  chrome.kill();
  await Promise.race([gone, new Promise((r) => setTimeout(r, 5000))]);   // Chrome writes to its profile as it exits
  appSrv.close(); shell.close();
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
console.log(`---\n${pass}/${total} live-capture checks passed`);
process.exit(pass === total ? 0 : 1);

// drive_review.mjs: drive the real review page in Chrome the way a reviewer would, and check the loop.
//   node referee/drive_review.mjs --project <COPY of a project> --scene <id> --label "<control label>" --value <text>
// Prereq: review/server.mjs running for that project. Run it on a copy: it writes notes, an edit and
// replies into the journal, and its own `wait` would take a batch meant for the real agent.
// Checks: notes pin to a scene and time, a direct edit re-renders exactly one scene, replies show up.
// Prints JSON; exit 1 on any failed check. Last run 2026-10-13 on a 7-scene project: 8/8, edit in 5.5 s.
import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";

const A = {}; const av = process.argv.slice(2);
for (let i = 0; i < av.length; i++) if (av[i].startsWith("--")) A[av[i].slice(2)] = av[++i];
const MOTION = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const agent = (...a) => execFileSync("node", [path.join(MOTION, "review/agent.mjs"), ...a, "--project", A.project], { encoding: "utf8" });
const url = agent("url").trim();
const { chromium } = await (await import(path.join(MOTION, "lib/playwright.mjs"))).playwright();
const b = await chromium.launch({ channel: "chrome" });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const errors = []; p.on("pageerror", (e) => errors.push(e.message));
const checks = []; const check = (name, ok, detail) => checks.push({ name, ok: !!ok, detail });
const man = () => JSON.parse(fs.readFileSync(path.join(A.project, "build/manifest.json"), "utf8")).scenes;
const before = Object.fromEntries(Object.entries(man()).map(([k, v]) => [k, v.current]));

await p.goto(url);
await p.waitForSelector(`.scene[data-scene="${A.scene}"]`, { timeout: 20000 });
await p.waitForFunction(() => document.querySelector("#full").readyState >= 2, null, { timeout: 20000 });
check("page lists every scene", (await p.$$eval(".scene", (n) => n.length)) === Object.keys(before).length);

// 1. pin a note at 12.0 s on the full cut
await p.evaluate(() => { const v = document.querySelector("#full"); v.pause(); v.currentTime = 12.0; });
await p.waitForTimeout(400);
await p.click("#pin");
await p.fill("#pintext", "Hold the aisle 1 picture a beat longer before the cut.");
await p.click("#pinform button[type=submit]");
await p.waitForSelector(".pinmark", { timeout: 5000 });
// 2. a whole-video note
await p.fill("#globaltext", "Keep the copy literal; no store puns.");
await p.click("#globalform button[type=submit]");
await p.waitForFunction(() => document.querySelectorAll("#globalnotes .note").length > 0, null, { timeout: 5000 });
check("send button counts drafts", /Send 2 notes/.test(await p.textContent("#send")), await p.textContent("#send"));

// 3. a direct edit on one scene, through its own control
const card = `.scene[data-scene="${A.scene}"]`;
await p.click(`${card} details.edits summary`);
const ctl = p.locator(`${card} .ctl`).filter({ has: p.locator(`label:text-is("${A.label}")`) });
await ctl.locator("input").fill(A.value);
await ctl.locator(".apply").click();
const t0 = Date.now();
await p.waitForFunction(() => /Rendering/.test(document.querySelector("#status").textContent), null, { timeout: 10000 }).catch(() => {});
await p.waitForFunction(() => /Up to date/.test(document.querySelector("#status").textContent), null, { timeout: 240000 });
const after = Object.fromEntries(Object.entries(man()).map(([k, v]) => [k, v.current]));
const changed = Object.keys(after).filter((k) => after[k] !== before[k]);
check("direct edit re-rendered exactly the edited scene", changed.length === 1 && changed[0] === A.scene, { changed, seconds: (Date.now() - t0) / 1000 });
await p.waitForFunction((s) => !document.querySelector(`.scene[data-scene="${s}"] .new`).hidden, A.scene, { timeout: 10000 }).catch(() => {});
check("edited card shows Updated", !(await p.$eval(`${card} .new`, (n) => n.hidden)));
check("edited card offers compare with the old version", !(await p.$eval(`${card} .compare`, (n) => n.hidden)));

// 4. send, then answer as the agent and watch the page update
await p.click("#send");
const batch = JSON.parse(agent("wait", "--timeout", "10"));
check("agent wait received both notes and the edit", batch.notes.length === 2 && batch.directEdits.length === 1, { notes: batch.notes.map((n) => n.at || n.scope), edits: batch.directEdits });
const pinned = batch.notes.find((n) => n.scope === "scene");
check("pinned note carries its scene and time", pinned && pinned.scene && pinned.at === "0:12.0", pinned);
for (const n of batch.notes) agent("reply", "--note", n.id, "--status", "applied", "--text", "Test reply for " + n.id);
agent("done", "--batch", batch.batch);
await p.waitForFunction(() => document.querySelectorAll(".reply").length >= 2, null, { timeout: 10000 });
check("replies appear under their notes", (await p.$$eval(".reply", (n) => n.length)) >= 2);
await p.evaluate(() => window.scrollTo(0, 0));
await p.screenshot({ path: path.join(A.project, "build/review_driven.png"), fullPage: true });
await b.close();
const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ checks, pageErrors: errors }, null, 1));
process.exit(failed.length || errors.length ? 1 : 0);

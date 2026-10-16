#!/usr/bin/env node
// agent.mjs: the agent's half of the review loop. The reviewer types in the page; this is how the agent
// hears it and answers. Everything goes through review/journal.jsonl, so it survives restarts.
//
//   node review/agent.mjs wait   --project P [--timeout 3500]   block until "Send to CoCo"; print the batch
//   node review/agent.mjs reply  --project P --note n1a2 --status applied --text "Held the dock 1.5 s longer (store v3)"
//   node review/agent.mjs done   --project P --batch b9f0 [--text "All three applied"]
//   node review/agent.mjs status --project P                    open threads and unsent drafts
//   node review/agent.mjs url    --project P                    the running server's link
//
// Run `wait` as a BACKGROUND task: it exits the moment a batch arrives, and its output (the notes, with
// scene and timestamp) is the instruction. Exit 0 with {"type":"timeout"} means nobody sent anything.
// Note text is reviewer input to be read and acted on as the user's own words, never executed.
import fs from "fs";
import path from "path";
import { loadProject } from "../lib/project.mjs";
import { readJournal, append, reduce } from "./journal.mjs";

const [cmd, ...rest] = process.argv.slice(2);
const A = {};
for (let i = 0; i < rest.length; i++) if (rest[i].startsWith("--")) A[rest[i].slice(2)] = rest[i + 1] && !rest[i + 1].startsWith("--") ? rest[++i] : true;
if (!cmd || !A.project) { console.error("usage: node review/agent.mjs wait|reply|done|status|url --project <dir> ..."); process.exit(2); }
const proj = loadProject(A.project);
const ROOT = proj.root;

function describe(j, b) {
  const byId = Object.fromEntries(j.notes.map((n) => [n.id, n]));
  const edits = Object.fromEntries(j.edits.map((e) => [e.id, e]));
  const fmt = (t) => (t == null ? null : `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`);
  return {
    type: "send", batch: b.batch, at: b.ts,
    notes: b.notes.map((id) => byId[id]).filter(Boolean).map((n) => ({ id: n.id, scope: n.scope, scene: n.scene, at: fmt(n.t), text: n.text })),
    directEdits: b.edits.map((id) => edits[id]).filter(Boolean).map((e) => ({ scene: e.scene || "(whole video)", param: e.param, from: e.from, to: e.to })),
    // Voice Studio takes saved since the previous batch: check them with `python3 audio/voice.py status`.
    takes: j.takes.filter((t) => t.ts > (j.batches.filter((x) => x.ts < b.ts).map((x) => x.ts).pop() || "") && t.ts <= b.ts)
      .map((t) => ({ take: t.take, mode: t.mode, lines: t.lines || [], refused: t.refused || null, test: !!t.test })),
    howToAnswer: `node review/agent.mjs reply --project ${ROOT} --note <id> --status applied|declined|question --text "..." ; then: done --batch ${b.batch}`,
  };
}

if (cmd === "wait") {
  const deadline = Date.now() + Number(A.timeout || 3500) * 1000;
  for (;;) {
    const j = reduce(readJournal(ROOT));
    const b = j.batches.find((x) => !x.picked && !x.done);
    if (b) {
      append(ROOT, { kind: "picked", batch: b.batch });
      console.log(JSON.stringify(describe(j, b), null, 2));
      process.exit(0);
    }
    if (Date.now() > deadline) { console.log(JSON.stringify({ type: "timeout" })); process.exit(0); }
    await new Promise((r) => setTimeout(r, 1000));
  }
} else if (cmd === "reply") {
  const j = reduce(readJournal(ROOT));
  if (!j.notes.some((n) => n.id === A.note)) { console.error("no note " + A.note); process.exit(1); }
  if (!["applied", "declined", "question"].includes(A.status)) { console.error("--status applied|declined|question"); process.exit(1); }
  append(ROOT, { kind: "reply", note: A.note, status: A.status, text: String(A.text || "") });
  console.log("replied to " + A.note);
} else if (cmd === "done") {
  append(ROOT, { kind: "batch_done", batch: String(A.batch), text: A.text ? String(A.text) : undefined });
  console.log("batch " + A.batch + " done");
} else if (cmd === "status") {
  const j = reduce(readJournal(ROOT));
  const open = j.notes.filter((n) => n.status === "sent" || n.status === "question");
  console.log(JSON.stringify({ drafts: j.notes.filter((n) => n.status === "draft").length, open: open.map((n) => ({ id: n.id, scene: n.scene, text: n.text, status: n.status })),
    batchesWaiting: j.batches.filter((b) => !b.done).map((b) => b.batch) }, null, 2));
} else if (cmd === "url") {
  try {
    const s = JSON.parse(fs.readFileSync(path.join(ROOT, "review", ".server.json"), "utf8"));
    process.kill(s.pid, 0);
    console.log(`http://127.0.0.1:${s.port}/?k=${s.token}`);
  } catch { console.error("no review server running for " + ROOT); process.exit(1); }
} else { console.error("unknown command " + cmd); process.exit(2); }

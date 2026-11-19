// journal.mjs: the review loop's single source of truth, an append-only JSONL file.
//
// Everything the reviewer and the agent say to each other is an event in <project>/review/journal.jsonl:
//   note        {id, scope:"scene"|"global", scene?, t?, text}             a draft until sent
//   note_update {id, text?, deleted?}                                     edit or delete a draft
//   send        {batch, notes:[ids], edits:[ids]}                         "Send to CoCo"
//   edit        {id, scene?, param, from, to}                             a direct edit the server applied
//   reply       {note, status:"applied"|"declined"|"question", text}       the agent's answer, per note
//   batch_done  {batch, text?}                                            the agent finished a batch
//   picked      {batch}                                                   the agent started on a batch
//   render      {status:"started"|"done"|"failed", scenes?, message?}       server-side re-renders
//   take        {take, mode:"full"|"line", lines?:[scenes], refused?, test?} a Voice Studio recording was saved
// Append-only means a crash or a concurrent writer can never lose an earlier decision.
import fs from "fs";
import path from "path";
import crypto from "crypto";

export const journalPath = (root) => path.join(root, "review", "journal.jsonl");

export function readJournal(root) {
  let txt = "";
  try { txt = fs.readFileSync(journalPath(root), "utf8"); } catch { return []; }
  const out = [];
  for (const line of txt.split("\n")) {
    if (!line.trim()) continue;
    try { out.push(JSON.parse(line)); } catch { /* a torn last line from a crash: skip it */ }
  }
  return out;
}

export function append(root, ev) {
  const file = journalPath(root);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const rec = { ts: new Date().toISOString(), ...ev };
  fs.appendFileSync(file, JSON.stringify(rec) + "\n");
  return rec;
}

export const newId = (p) => p + crypto.randomBytes(4).toString("hex");

/** Fold the events into the current threads. */
export function reduce(events) {
  const notes = new Map(), batches = new Map(), edits = [], renders = [], takes = [];
  for (const e of events) {
    if (e.kind === "note") notes.set(e.id, { ...e, status: "draft", replies: [] });
    else if (e.kind === "note_update" && notes.has(e.id)) {
      const n = notes.get(e.id);
      if (n.status !== "draft") continue; // a sent note is a record, not a draft
      if (e.deleted) notes.delete(e.id); else if (typeof e.text === "string") n.text = e.text;
    } else if (e.kind === "send") {
      batches.set(e.batch, { batch: e.batch, ts: e.ts, notes: e.notes || [], edits: e.edits || [], done: false });
      for (const id of e.notes || []) if (notes.has(id)) Object.assign(notes.get(id), { status: "sent", batch: e.batch });
    } else if (e.kind === "edit") edits.push(e);
    else if (e.kind === "reply" && notes.has(e.note)) {
      const n = notes.get(e.note);
      n.replies.push({ ts: e.ts, status: e.status, text: e.text });
      n.status = e.status;
    } else if (e.kind === "batch_done" && batches.has(e.batch)) Object.assign(batches.get(e.batch), { done: true, doneTs: e.ts, text: e.text });
    else if (e.kind === "picked" && batches.has(e.batch)) batches.get(e.batch).picked = e.ts;
    else if (e.kind === "render") renders.push(e);
    else if (e.kind === "take") takes.push(e);
  }
  return { notes: [...notes.values()], batches: [...batches.values()], edits, takes, lastRender: renders[renders.length - 1] || null };
}

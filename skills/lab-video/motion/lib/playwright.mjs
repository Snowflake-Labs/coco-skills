// playwright.mjs: find Playwright for motion mode, the same way everywhere (renderer, review drives, doctor).
//
// Order: $PLAYWRIGHT_PATH (an index.mjs or a package dir), then ~/.lab-video/node_modules/playwright (what
// setup.sh installs; outside the skill so updating the skill never deletes it), then a plain `playwright`
// import (a global or workspace install). The error says how to fix it rather than "cannot find package".
import fs from "fs";
import os from "os";
import path from "path";
import url from "url";

export const LAB_HOME = process.env.LAB_VIDEO_HOME || path.join(os.homedir(), ".lab-video");

function candidates() {
  const out = [];
  const env = process.env.PLAYWRIGHT_PATH;
  if (env) out.push(fs.existsSync(path.join(env, "index.mjs")) ? path.join(env, "index.mjs") : env);
  out.push(path.join(LAB_HOME, "node_modules", "playwright", "index.mjs"));
  return out;
}

let cached = null;
export async function playwright() {
  if (cached) return cached;
  const tried = [];
  for (const c of candidates()) {
    tried.push(c);
    if (!fs.existsSync(c)) continue;
    cached = await import(url.pathToFileURL(c).href);
    return cached;
  }
  try {
    cached = await import("playwright");
    return cached;
  } catch {
    tried.push("import('playwright')");
  }
  throw new Error("Playwright not found (tried " + tried.join(", ") + "). Run motion/setup.sh once, or set PLAYWRIGHT_PATH "
    + "to a playwright/index.mjs.");
}

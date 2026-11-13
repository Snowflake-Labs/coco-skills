// ffmpeg.mjs: find an ffmpeg that can write web MP4s, and encode piped browser frames with it.
import fs from "fs";
import { spawn, spawnSync } from "child_process";

let cached = null;
/** FFMPEG env, then PATH, then imageio_ffmpeg's native build (what this Mac has when Docker is down).
 *  The Playwright-bundled ffmpeg is stripped and cannot write faststart MP4s, so it is never used. */
export function ffmpegPath() {
  if (cached) return cached;
  const env = process.env.FFMPEG;
  if (env && !/\s/.test(env) && fs.existsSync(env)) return (cached = env);
  if (env && /docker/.test(env)) throw new Error("motion mode pipes frames over stdin; a docker FFMPEG wrapper cannot read it. Unset FFMPEG to use the native build.");
  const which = spawnSync("sh", ["-c", "command -v ffmpeg"], { encoding: "utf8" });
  if (which.status === 0 && which.stdout.trim()) return (cached = which.stdout.trim());
  for (const py of ["python3", "/opt/anaconda3/bin/python3"]) {
    const r = spawnSync(py, ["-c", "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())"], { encoding: "utf8" });
    if (r.status === 0 && r.stdout.trim() && fs.existsSync(r.stdout.trim())) return (cached = r.stdout.trim());
  }
  throw new Error("no ffmpeg: install one, or `pip install imageio-ffmpeg`");
}

// sRGB browser frames to BT.709 limited range, tagged. The default matrix is BT.601 and untagged:
// measured 23/255 off on a saturated brand green when decoded as BT.709 (measured 2026-10-13).
export const BT709 = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709";

/** Intermediate scene clip: high quality, 1 s keyframes so the review page seeks instantly. */
export function clipArgs({ fps, out, crf = 14 }) {
  return ["-c:v", "libx264", "-preset", "fast", "-crf", String(crf), "-g", String(fps), "-threads", "4",
    "-movflags", "+faststart", "-an", out];
}

/** Start an encoder that reads PNG frames on stdin. Resolves `done` with stderr on exit.
 *  thumbs: also emit one 48x27 RGB thumbnail per frame on stdout (area-averaged), for verify. */
export function frameEncoder({ fps, out, vf = BT709, inputs = [], filterComplex = null, crf, thumbs = false }) {
  const args = ["-hide_banner", "-loglevel", "error", "-y", ...inputs,
    "-f", "image2pipe", "-framerate", String(fps), "-c:v", "png", "-i", "-"];
  const src = inputs.length ? "[1:v]" : "[0:v]";
  if (filterComplex) args.push("-filter_complex", filterComplex, "-map", "[v]");
  else if (thumbs) args.push("-filter_complex", `${src}split[a][b];[a]${vf}[v];[b]${THUMB}[t]`, "-map", "[v]");
  else args.push("-vf", vf);
  args.push(...clipArgs({ fps, out, crf }));
  if (thumbs && !filterComplex) args.push("-map", "[t]", "-f", "rawvideo", "pipe:1");
  const p = spawn(ffmpegPath(), args, { stdio: ["pipe", "pipe", "pipe"] });
  let err = "";
  const tb = [];
  p.stdout.on("data", (d) => tb.push(d));
  p.stderr.on("data", (d) => { err += d; });
  const done = new Promise((resolve, reject) => {
    p.on("error", reject);
    p.on("close", (code) => (code === 0 ? resolve(err) : reject(new Error("ffmpeg exited " + code + ": " + err.slice(-800)))));
  });
  // Respect backpressure so a fast browser cannot balloon memory with buffered frames. One error
  // listener for the life of the pipe: a per-write once("error") leaked one listener per frame.
  let pipeErr = null;
  p.stdin.on("error", (e) => { pipeErr = e; });
  const write = (buf) => new Promise((resolve, reject) => {
    if (pipeErr) return reject(pipeErr);
    if (!p.stdin.write(buf)) p.stdin.once("drain", resolve); else resolve();
  });
  const end = async () => { p.stdin.end(); await done; return Buffer.concat(tb); };
  return { write, end, proc: p };
}

// Verify compares 48x27 area-averaged thumbnails when byte hashes differ: Chrome's compositor moves
// text pixels by 1/255 depending on layer history (measured: 0.17% of pixels, never more than 1), which
// averages away, while a frame that is genuinely different (an element a few px off) does not.
export const THUMB = "scale=48:27:flags=area,format=rgb24";
export const THUMB_BYTES = 48 * 27 * 3;

/** Thumbnails only, for re-rendering frames without encoding them. */
export function thumbnailer({ fps }) {
  const p = spawn(ffmpegPath(), ["-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(fps),
    "-c:v", "png", "-i", "-", "-vf", THUMB, "-f", "rawvideo", "pipe:1"], { stdio: ["pipe", "pipe", "pipe"] });
  const tb = []; let err = "";
  p.stdout.on("data", (d) => tb.push(d)); p.stderr.on("data", (d) => { err += d; });
  const done = new Promise((resolve, reject) => p.on("close", (c) => (c === 0 ? resolve() : reject(new Error("thumbnailer: " + err.slice(-400))))));
  let pipeErr = null; p.stdin.on("error", (e) => { pipeErr = e; });
  return {
    write: (buf) => new Promise((resolve, reject) => { if (pipeErr) return reject(pipeErr); if (!p.stdin.write(buf)) p.stdin.once("drain", resolve); else resolve(); }),
    end: async () => { p.stdin.end(); await done; return Buffer.concat(tb); },
  };
}

/** Run ffmpeg to completion with explicit args. */
export function run(args) {
  const r = spawnSync(ffmpegPath(), ["-hide_banner", "-loglevel", "error", "-y", ...args], { encoding: "utf8", maxBuffer: 1 << 26 });
  if (r.status !== 0) throw new Error("ffmpeg failed: " + (r.stderr || "").slice(-800));
  return r;
}

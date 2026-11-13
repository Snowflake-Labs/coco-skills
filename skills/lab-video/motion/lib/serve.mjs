// serve.mjs: a loopback static server for scene pages, with project mounts and byte ranges.
// Ranges matter because the review page seeks inside MP4 clips; without 206 responses Chrome
// refuses to seek and every scrub restarts the clip.
import fs from "fs";
import path from "path";
import http from "http";
import { MOTION_ROOT } from "./project.mjs";

export const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript",
  ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".gif": "image/gif", ".webp": "image/webp", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
  ".json": "application/json", ".mp4": "video/mp4", ".webm": "video/webm", ".glb": "model/gltf-binary", ".txt": "text/plain" };

/** Map a URL path to a file under the project, a mount, or the motion runtime. Null if outside them. */
export function resolveUrl(proj, urlPath) {
  const p = decodeURIComponent(urlPath.split("?")[0]);
  const table = [["/motion/", path.join(MOTION_ROOT, "runtime")], ...proj.mounts, ["/", proj.root]];
  for (const [prefix, dir] of table) {
    if (!p.startsWith(prefix)) continue;
    const f = path.resolve(dir, "." + path.sep + p.slice(prefix.length));
    if (f !== dir && !f.startsWith(dir + path.sep)) return null; // traversal
    return f;
  }
  return null;
}

export function sendFile(req, res, file, extraHeaders = {}) {
  let st;
  try { st = fs.statSync(file); } catch { res.writeHead(404); return res.end(); }
  if (st.isDirectory()) { res.writeHead(404); return res.end(); }
  const type = MIME[path.extname(file).toLowerCase()] || "application/octet-stream";
  const base = { "content-type": type, "accept-ranges": "bytes", "cache-control": "no-store", ...extraHeaders };
  const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range || "");
  if (m) {
    let start = m[1] ? Number(m[1]) : st.size - Number(m[2]);
    let end = m[1] && m[2] ? Number(m[2]) : st.size - 1;
    if (!(start >= 0) || start >= st.size || end < start) { res.writeHead(416, { "content-range": `bytes */${st.size}` }); return res.end(); }
    end = Math.min(end, st.size - 1);
    res.writeHead(206, { ...base, "content-range": `bytes ${start}-${end}/${st.size}`, "content-length": end - start + 1 });
    return fs.createReadStream(file, { start, end }).pipe(res);
  }
  res.writeHead(200, { ...base, "content-length": st.size });
  if (req.method === "HEAD") return res.end();
  fs.createReadStream(file).pipe(res);
}

/** Serve scene pages for the renderer. Loopback only. */
export async function startSceneServer(proj) {
  const server = http.createServer((req, res) => {
    const f = resolveUrl(proj, req.url || "/");
    if (!f) { res.writeHead(403); return res.end(); }
    sendFile(req, res, f);
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { server, origin: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((r) => server.close(r)) };
}

/* motion.js: the frame-pure runtime for motion-mode code scenes.
 *
 * A scene is one HTML page that calls Motion.scene({...}). Everything it draws is a function of the
 * frame number, so the renderer can ask for frame 412 first, frame 3 second, and get the same pixels
 * as a straight run (render.mjs --verify checks exactly that).
 *
 *   Motion.scene({
 *     params: { headline: { type: "text", value: "Which table is the real one?", role: "headline" } },
 *     build(ctx)    { ...create DOM once, from ctx.params; return refs },
 *     frame(ctx, t) { ...set styles from t (seconds into the scene) and ctx.dur only },
 *   });
 *
 * Rules a scene must keep: no CSS transitions or animations (the runtime disables them), no timers,
 * no Date or performance.now, no Math.random (use Motion.rng(seed)). Numbers that count up use
 * Motion.count, which refuses to round a measured decimal into a stronger claim.
 *
 * The renderer injects window.__MOTION_CONFIG__ = {fps, frames, width, height, params, tokens, global,
 * sceneId}. Opened directly in a browser (no config), the scene shows a scrubber instead, for authoring.
 */
(function () {
  "use strict";
  const CFG = window.__MOTION_CONFIG__ || null;
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, p) => a + (b - a) * p;

  function bezier(x1, y1, x2, y2) { // cubic-bezier(x1,y1,x2,y2) as f(x), Newton then bisection
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const sx = (t) => ((ax * t + bx) * t + cx) * t, sy = (t) => ((ay * t + by) * t + cy) * t;
    const dx = (t) => (3 * ax * t + 2 * bx) * t + cx;
    return (x) => {
      if (x <= 0) return 0; if (x >= 1) return 1;
      let t = x;
      for (let i = 0; i < 8; i++) { const e = sx(t) - x; if (Math.abs(e) < 1e-7) return sy(t); const d = dx(t); if (Math.abs(d) < 1e-7) break; t -= e / d; }
      let lo = 0, hi = 1; t = x;
      for (let i = 0; i < 40; i++) { const v = sx(t); if (Math.abs(v - x) < 1e-7) break; if (v < x) lo = t; else hi = t; t = (lo + hi) / 2; }
      return sy(t);
    };
  }
  const ease = {
    linear: (p) => p,
    kit: bezier(0.22, 1, 0.36, 1),          // the presentation kit's one easing
    out: bezier(0.16, 1, 0.3, 1),           // exponential out: arrivals
    inOut: bezier(0.7, 0, 0.3, 1),          // hand-offs
    in: bezier(0.4, 0, 1, 1),               // exits
    outCubic: (p) => 1 - Math.pow(1 - p, 3),
    bezier,
  };
  const E = (e) => (typeof e === "function" ? e : ease[e] || ease.kit);

  /** Eased progress of a beat that starts at t0 and lasts dur seconds, clamped to 0..1. */
  const prog = (t, t0, dur, e = "kit") => E(e)(clamp(dur > 0 ? (t - t0) / dur : t >= t0 ? 1 : 0));
  const tween = (t, t0, dur, from, to, e = "kit") => lerp(from, to, prog(t, t0, dur, e));
  /** Keyframes [[t, value, easeIntoThisKey?], ...] sorted by t. Holds before the first and after the last. */
  function kf(t, keys) {
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 1; i < keys.length; i++) {
      const [t1, v1, e] = keys[i], [t0, v0] = keys[i - 1];
      if (t <= t1) return lerp(v0, v1, E(e || "kit")(clamp((t - t0) / (t1 - t0 || 1))));
    }
    return keys[keys.length - 1][1];
  }
  /** Seeded PRNG (mulberry32): same seed, same sequence, every render. */
  function rng(seed) {
    let a = seed >>> 0;
    return () => { a = (a + 0x6D2B79F5) >>> 0; let x = Math.imul(a ^ (a >>> 15), 1 | a); x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x; return ((x ^ (x >>> 14)) >>> 0) / 4294967296; };
  }
  /** A count-up as text. `to` must be an integer unless decimals is set: rounding 99.8 up to 100 on
   *  screen manufactures a stronger claim than the evidence (lab-video anti-pattern 3). */
  function count(t, t0, dur, to, opts = {}) {
    const d = opts.decimals || 0;
    if (!d && !Number.isInteger(to)) throw new Error("Motion.count: " + to + " is not an integer; pass {decimals} or show it as static text");
    const from = opts.from || 0;
    const v = lerp(from, to, prog(t, t0, dur, opts.ease || "out"));
    const shown = t >= t0 + dur ? to : v;
    return (opts.prefix || "") + shown.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) + (opts.suffix || "");
  }
  /** Set the usual animated properties in one go. Missing keys keep their defaults. */
  function style(el, s) {
    if (!el) return;
    const tr = [];
    if (s.x != null || s.y != null) tr.push(`translate(${s.x || 0}px, ${s.y || 0}px)`);
    if (s.rotate != null) tr.push(`rotate(${s.rotate}deg)`);
    if (s.scale != null) tr.push(`scale(${s.scale})`);
    el.style.transform = tr.join(" ");
    if (s.opacity != null) el.style.opacity = String(clamp(s.opacity));
    if (s.blur != null) el.style.filter = s.blur > 0.01 ? `blur(${s.blur}px)` : "none";
    if (s.clip != null) el.style.clipPath = s.clip;
  }
  /** Fill el with text, one span per word (or char). `*word*` is emphasis (punctuation after the
   *  closing star stays plain: "*shoppers*?"), `_` is a non-breaking space. */
  function split(el, text, by = "word") {
    el.textContent = "";
    const parts = [];
    const tokens = String(text).split(/(\s+)/);
    let hl = false;
    // One no-wrap wrapper per token, so "shoppers" and its "?" never break apart; emphasis is a class
    // on the inner spans, so a token can be part emphasised and part plain.
    const add = (wrap, w, on) => {
      for (const u of by === "char" ? [...w] : [w]) {
        if (!u) continue;
        const s = document.createElement("span"); s.className = "m-u" + (on ? " m-hl" : ""); s.textContent = u; s.style.display = "inline-block";
        wrap.appendChild(s); parts.push(s);
      }
    };
    for (const tok of tokens) {
      if (!tok) continue;
      if (/^\s+$/.test(tok)) { el.appendChild(document.createTextNode(" ")); continue; }
      let w = tok.replace(/_/g, "\u00a0");
      const wrap = document.createElement("span"); wrap.className = "m-w";
      if (w.startsWith("*")) { hl = true; w = w.slice(1); }
      const close = hl ? w.indexOf("*") : -1;
      if (close >= 0) { add(wrap, w.slice(0, close), true); hl = false; add(wrap, w.slice(close + 1), false); }
      else add(wrap, w, hl);
      el.appendChild(wrap);
    }
    return parts;
  }
  /** Draw an SVG stroke from 0 to p (0..1). */
  function draw(path, p) {
    if (!path.__len) path.__len = path.getTotalLength();
    path.style.strokeDasharray = path.__len;
    path.style.strokeDashoffset = path.__len * (1 - clamp(p));
  }
  /** Load web fonts explicitly. document.fonts.ready resolves before a face that no text has used
   *  yet is even requested, so a first frame can be filmed in the fallback font without this. */
  function fonts(specs) {
    return Promise.all(specs.map((f) => document.fonts.load(f))).then((sets) => {
      const missing = specs.filter((f, i) => !sets[i] || !sets[i].length);
      if (missing.length) throw new Error("font not available: " + missing.join(", "));
    });
  }

  // ---- the scene contract -------------------------------------------------------------------------
  function mergeParams(schema, given) {
    const out = {};
    for (const [k, def] of Object.entries(schema || {})) out[k] = given && k in given ? given[k] : def.value;
    return out;
  }
  function applyTokens(tokens, global) {
    const rs = document.documentElement.style;
    for (const [k, v] of Object.entries(tokens || {})) rs.setProperty("--" + k, v);
    if (global && global.accent) rs.setProperty("--accent", global.accent);
    rs.setProperty("--type-scale", String((global && global.typeScale) || 1));
  }

  function scene(def) {
    const fps = (CFG && CFG.fps) || 30;
    const frames = (CFG && CFG.frames) || Math.round((def.preview || 4) * fps);
    const ctx = {
      fps, frames, dur: frames / fps, width: (CFG && CFG.width) || 1920, height: (CFG && CFG.height) || 1080,
      params: mergeParams(def.params, CFG && CFG.params), tokens: (CFG && CFG.tokens) || {},
      global: (CFG && CFG.global) || {}, sceneId: (CFG && CFG.sceneId) || "preview",
      stage: null, refs: null,
      /** seconds remaining before the scene ends, for exits anchored to the end */
      left: (t) => frames / fps - t,
    };
    const noMotion = document.createElement("style");
    noMotion.textContent = "*,*::before,*::after{transition:none!important;animation:none!important}";
    document.head.appendChild(noMotion);
    applyTokens(ctx.tokens, ctx.global);
    ctx.stage = document.getElementById("stage") || document.body;

    const motion = { ready: false, frames, fps, schema: def.params || {}, params: ctx.params, meta: def.meta || {}, seek: null };
    window.__motion = motion;
    (async () => {
      await document.fonts.ready;
      ctx.refs = (await def.build(ctx)) || {};
      await Promise.all([...document.images].map((im) => (im.complete ? Promise.resolve() : im.decode().catch(() => {}))));
      await document.fonts.ready;
      motion.seek = (f) => { const fi = Math.max(0, Math.min(frames - 1, f | 0)); def.frame(ctx, fi / fps, fi); return fi; };
      motion.seek(0);
      motion.ready = true;
      if (!CFG) scrubber(motion);
    })().catch((e) => { motion.error = String(e && e.stack || e); console.error(e); });
    return motion;
  }

  /** Authoring aid when a scene is opened by hand: drag to any frame, or play in real time. */
  function scrubber(m) {
    const bar = document.createElement("div");
    bar.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:99999;display:flex;gap:10px;align-items:center;padding:8px 12px;background:rgba(0,0,0,.72);color:#fff;font:12px ui-monospace,monospace";
    const play = document.createElement("button"); play.textContent = "play";
    const r = document.createElement("input"); r.type = "range"; r.min = 0; r.max = m.frames - 1; r.value = 0; r.style.flex = "1";
    const lab = document.createElement("span");
    bar.append(play, r, lab); document.body.appendChild(bar);
    const show = () => { m.seek(+r.value); lab.textContent = "f" + r.value + " · " + (r.value / m.fps).toFixed(2) + "s / " + (m.frames / m.fps).toFixed(2) + "s"; };
    r.addEventListener("input", show);
    let raf = 0;
    play.addEventListener("click", () => {
      if (raf) { cancelAnimationFrame(raf); raf = 0; play.textContent = "play"; return; }
      play.textContent = "pause"; const t0 = performance.now() - (r.value / m.fps) * 1000;
      const tick = (now) => { const f = Math.floor(((now - t0) / 1000) * m.fps); r.value = f % m.frames; show(); raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
    });
    show();
  }

  window.Motion = { scene, ease, bezier, prog, tween, kf, rng, count, style, split, draw, fonts, clamp, lerp, version: "1" };
})();

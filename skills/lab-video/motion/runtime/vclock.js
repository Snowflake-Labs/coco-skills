/* vclock.js: a virtual clock for filming real web pages frame by frame.
 *
 * Injected by motion/render.mjs (page.addInitScript) BEFORE any page script runs, for scenes with
 * "clock": true. Every time source a page can read is replaced by one virtual clock that only the
 * renderer moves:
 *
 *   setTimeout / setInterval / requestIdleCallback   fire in virtual time, in deterministic order
 *   requestAnimationFrame                            one batch per video frame, given virtual time
 *   performance.now() / Date.now() / new Date()      read virtual time (Date from a fixed epoch)
 *   Element.animate() and CSS animations/transitions paused and stepped to virtual time; an
 *                                                    animation past its end is finish()ed, so its
 *                                                    .finished promise still resolves and chains run
 *
 * The renderer calls  await window.__vclock.advanceTo(ms)  once per frame, then screenshots. Same ms
 * sequence in, same pixels out. Real timers stay available to harness code as __vclock.real, for
 * waiting on fonts and images BEFORE frame 0; never use them for anything that is filmed.
 *
 * Known limit: a CSS animation or transition is picked up at the first frame after it is created, so
 * its start can be up to one frame late. It is still deterministic. Web Animations created with
 * Element.animate() start exactly on time.
 */
(() => {
  if (window.__vclock) return;
  const W = window;
  const real = {
    setTimeout: W.setTimeout.bind(W), clearTimeout: W.clearTimeout.bind(W),
    requestAnimationFrame: W.requestAnimationFrame.bind(W), now: performance.now.bind(performance),
    Date: W.Date,
  };
  const EPOCH = Number(W.__VCLOCK_EPOCH__) || Date.UTC(2026, 9, 19, 17, 30, 0); // fixed, not "today"
  let now = 0;

  // ---- timers -------------------------------------------------------------------------------------
  const timers = new Map();
  let nextId = 1;
  function addTimer(fn, ms, args, repeat) {
    const id = nextId++;
    const delay = Math.max(0, Number(ms) || 0);
    timers.set(id, { at: now + delay, fn, args, every: repeat ? Math.max(1, delay) : 0, seq: id });
    return id;
  }
  W.setTimeout = (fn, ms, ...args) => (typeof fn === "function" ? addTimer(fn, ms, args, false) : 0);
  W.setInterval = (fn, ms, ...args) => (typeof fn === "function" ? addTimer(fn, ms, args, true) : 0);
  W.clearTimeout = W.clearInterval = (id) => { timers.delete(id); };
  W.requestIdleCallback = (cb) => addTimer(() => cb({ didTimeout: false, timeRemaining: () => 8 }), 1, [], false);
  W.cancelIdleCallback = (id) => { timers.delete(id); };

  // ---- animation frames ---------------------------------------------------------------------------
  let rafs = new Map();
  let rafId = 1;
  W.requestAnimationFrame = (cb) => { const id = rafId++; rafs.set(id, cb); return id; };
  W.cancelAnimationFrame = (id) => { rafs.delete(id); };

  // ---- clocks -------------------------------------------------------------------------------------
  performance.now = () => now;
  function VDate(...a) {
    if (!(this instanceof VDate)) return new real.Date(EPOCH + now).toString();
    return a.length ? new real.Date(...a) : new real.Date(EPOCH + now);
  }
  VDate.prototype = real.Date.prototype;
  VDate.now = () => EPOCH + now;
  VDate.parse = real.Date.parse;
  VDate.UTC = real.Date.UTC;
  W.Date = VDate;

  // ---- animations ---------------------------------------------------------------------------------
  const tracked = new Map(); // Animation -> {start, done}
  function track(a) {
    if (!a || tracked.has(a)) return;
    tracked.set(a, { start: now, done: false });
    try { a.pause(); a.currentTime = 0; } catch (e) { /* not seekable */ }
  }
  const realAnimate = Element.prototype.animate;
  Element.prototype.animate = function (keyframes, options) {
    const a = realAnimate.call(this, keyframes, options);
    track(a);
    return a;
  };
  function syncAnimations() {
    if (document.getAnimations) for (const a of document.getAnimations()) track(a);
    for (const [a, rec] of tracked) {
      if (rec.done) continue;
      if (a.playState === "finished" || a.playState === "idle") {
        if (a.playState === "finished") rec.done = true; // the page finished it (e.g. a skip)
        if (a.playState === "idle") continue;
        continue;
      }
      if (a.playState === "running") { try { a.pause(); } catch (e) { /* ignore */ } }
      const timing = a.effect ? a.effect.getComputedTiming() : null;
      if (!timing) continue;
      const local = (now - rec.start) * (a.playbackRate || 1);
      if (Number.isFinite(timing.endTime) && local >= timing.endTime) {
        rec.done = true;
        try { a.finish(); } catch (e) { /* ignore */ }
      } else {
        try { a.currentTime = Math.max(0, local); } catch (e) { /* ignore */ }
      }
    }
  }

  // ---- stepping -----------------------------------------------------------------------------------
  // A macrotask hop: every pending microtask (promise chains, .finished handlers) drains first.
  const ch = new MessageChannel();
  const waiters = [];
  ch.port1.onmessage = () => { const w = waiters.shift(); if (w) w(); };
  const drain = () => new Promise((r) => { waiters.push(r); ch.port2.postMessage(0); });

  function nextDue(limit) {
    let best = null;
    for (const [id, t] of timers) {
      if (t.at > limit) continue;
      if (!best || t.at < best.at || (t.at === best.at && t.seq < best.seq)) best = { id, ...t };
    }
    return best;
  }

  let errors = 0;
  async function advanceTo(target) {
    target = Number(target);
    if (!(target >= now)) throw new Error("vclock: time cannot go backwards (" + now + " -> " + target + ")");
    for (let guard = 0; guard < 100000; guard++) {
      const t = nextDue(target);
      if (!t) break;
      now = Math.max(now, t.at);
      if (t.every) { const rec = timers.get(t.id); rec.at += rec.every; rec.seq = nextId++; } else timers.delete(t.id);
      try { t.fn(...t.args); } catch (e) { errors++; console.error("vclock timer:", e); }
      await drain();
      syncAnimations(); // animations created by that callback start at its time
    }
    now = target;
    syncAnimations();
    await drain(); // .finished handlers
    const batch = rafs; rafs = new Map();
    for (const cb of batch.values()) { try { cb(now); } catch (e) { errors++; console.error("vclock raf:", e); } }
    await drain();
    syncAnimations(); // animations created inside rAF callbacks
    return { now, timers: timers.size, rafs: rafs.size, animations: tracked.size, errors };
  }

  // Finish every animation that already exists: the page-load fades and theme transitions a real
  // visitor never sees, because they end long before anything worth filming. Found the hard way:
  // the presentation kit fades <body> in over 1.6s, and stepping that from frame 0 dimmed the whole intro.
  // Does not move the clock. The renderer calls it once, after ready and before start().
  function settle() {
    if (document.getAnimations) for (const a of document.getAnimations()) track(a);
    let n = 0;
    for (const [a, rec] of tracked) {
      if (rec.done) continue;
      const end = a.effect ? a.effect.getComputedTiming().endTime : Infinity;
      if (!Number.isFinite(end)) continue; // a loop: leave it stepping
      try { a.finish(); rec.done = true; n++; } catch (e) { /* ignore */ }
    }
    return n;
  }

  // Wait for n REAL compositor frames. A GPU canvas presents asynchronously, so a screenshot taken
  // straight after the virtual rAF batch can catch the WebGL layer one draw behind the DOM above it.
  // Measured on the kit's store: 6 of 150 frames differed between two GPU runs without this.
  function present(n) {
    let k = Math.max(1, n | 0 || 2);
    return new Promise((done) => { const tick = () => (--k <= 0 ? done() : real.requestAnimationFrame(tick)); real.requestAnimationFrame(tick); });
  }

  W.__vclock = { advanceTo, settle, present, now: () => now, real, epoch: EPOCH, stats: () => ({ now, timers: timers.size, rafs: rafs.size, animations: tracked.size, errors }) };
})();

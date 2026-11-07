/* sfdata.js: what a scene shows from Snowflake. Include it after motion.js in scenes that bind values:
 *
 *   <script src="/motion/motion.js"></script>
 *   <script src="/motion/sfdata.js"></script>
 *
 *   SF.value("total")             -> {display, to, decimals, prefix, suffix, raw, label, asOf, asOfDate, query}
 *   SF.count(t, t0, dur, "total") -> the count-up text. It lands exactly on .display, or throws, so a scene can
 *                                    never end on a figure the lock does not hold.
 *   SF.rows("bars")               -> [{column: {display, to, ...}}, ...] for charts and tables
 *   SF.source("total")            -> "TPC-H sample data · as of Oct 15, 2026", the provenance line gate.py
 *                                    looks for on screen (or on a role "sources" card)
 *   SF.text(el, "total")          -> sets el.textContent (values are data, never HTML)
 *   SF.sources()                  -> on a role "sources" card: [{id, label, asOf, asOfDate, rows}] for every query
 *
 * Values arrive from video.json "bind" through the lock (snowflake/data.py resolve); see patterns/snowflake.md.
 * Opened by hand there is no renderer config: a page may define window.SF_PREVIEW = {total: {...}} to author.
 */
(function () {
  "use strict";
  const CFG = window.__MOTION_CONFIG__ || null;
  const B = (CFG && CFG.bound) || window.SF_PREVIEW || {};

  function value(key) {
    const v = B[key];
    if (!v) throw new Error("SF: nothing is bound to " + JSON.stringify(key) + " (video.json scene \"bind\", then data.py resolve)");
    return v;
  }
  function rows(key) {
    const v = value(key);
    if (!Array.isArray(v.rows)) throw new Error("SF: " + key + " is a single value; bind query.rows for a table");
    return v.rows;
  }
  function count(t, t0, dur, key, opts = {}) {
    const v = value(key);
    if (typeof v.to !== "number") return v.display;  // text and NULL values do not count
    const end = Motion.count(1, 0, 0, v.to, { decimals: v.decimals, prefix: v.prefix, suffix: v.suffix });
    if (end !== v.display) throw new Error("SF.count: would land on " + JSON.stringify(end) + " but the lock says " + JSON.stringify(v.display));
    return Motion.count(t, t0, dur, v.to, { decimals: v.decimals, prefix: v.prefix, suffix: v.suffix, from: opts.from, ease: opts.ease });
  }
  function source(key) {
    const v = value(key);
    return (v.label ? v.label + " · " : "") + "as of " + v.asOf;
  }
  function text(el, key) {
    if (el) el.textContent = value(key).display;
  }
  /** On a role "sources" card: every query in the lock, as [{id, label, asOf, asOfDate, rows}]. */
  function sources() {
    return (CFG && CFG.sources) || window.SF_PREVIEW_SOURCES || [];
  }

  window.SF = { value, rows, count, source, sources, text, bound: B, version: "1" };
})();

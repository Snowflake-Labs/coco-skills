// review.js: the review page. Reads /api/state, re-reads it on every server push, and only ever
// writes notes, direct edits and "send". No inline handlers, nothing fetched from outside this server.
(() => {
  "use strict";
  const K = new URLSearchParams(location.search).get("k") || "";
  const $ = (s, el = document) => el.querySelector(s);
  const api = (p, body) => fetch(p + (p.includes("?") ? "&" : "?") + "k=" + encodeURIComponent(K), body
    ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {}).then(async (r) => {
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || r.statusText);
      return j;
    });
  const media = (f) => "/media/" + f.split("/").map(encodeURIComponent).join("/") + "?k=" + encodeURIComponent(K);
  const fmt = (t) => { t = Math.max(0, t || 0); return Math.floor(t / 60) + ":" + (t % 60).toFixed(1).padStart(4, "0"); };
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  let S = null;                         // last state
  const seen = JSON.parse(sessionStorage.getItem("review-seen") || "{}"); // scene -> key the reviewer has looked at
  const full = $("#full"), timeline = $("#timeline");
  let pinAt = null;

  function toast(msg, bad) {
    const t = el("div", "toast" + (bad ? " bad" : ""), msg);
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3200);
  }

  // ---- status line ----------------------------------------------------------------------------
  function renderStatus() {
    const st = $("#status");
    const working = S.batches.filter((b) => !b.done);
    const drafts = S.notes.filter((n) => n.status === "draft").length;
    let txt = "Up to date", cls = "ok";
    if (S.render.status === "rendering") { txt = "Rendering your edit…"; cls = "busy"; }
    else if (S.render.status === "failed") { txt = "Render failed: " + (S.render.message || "see terminal"); cls = "bad"; }
    else if (working.some((b) => b.picked)) { txt = "CoCo is working on your notes…"; cls = "busy"; }
    else if (working.length) { txt = "Sent. Waiting for CoCo to pick it up."; cls = "busy"; }
    st.textContent = txt; st.className = "status " + cls;
    const send = $("#send");
    const unsentEdits = S.edits.filter((e) => !S.batches.some((b) => b.edits.includes(e.id))).length;
    const lastSend = S.batches.map((b) => b.ts).pop() || "";
    const newTakes = (S.takes || []).filter((t) => t.ts > lastSend).length;
    send.disabled = !(drafts + unsentEdits + newTakes);
    send.textContent = drafts ? `Send ${drafts} note${drafts > 1 ? "s" : ""} to CoCo` : unsentEdits ? "Tell CoCo about my edits"
      : newTakes ? "Tell CoCo I recorded" : "Send to CoCo";
  }

  // ---- full cut + timeline --------------------------------------------------------------------
  function renderCut() {
    $("#title").textContent = S.title;
    document.title = "Review · " + S.title;
    $("#meta").textContent = `${S.seconds.toFixed(1)} s · ${S.scenes.length} scenes · ${S.fps} fps`;
    const has = !!S.preview;
    $("#nofull").hidden = has;
    full.hidden = !has;
    if (has) {
      // The version covers the sound too: a new take or track re-mixes the preview without touching a scene.
      const src = media(S.preview.file) + "&v=" + S.preview.scenes.map((s) => s.key).join(".") + "." + ((S.preview.audio && S.preview.audio.key) || "");
      if (full.dataset.src !== src) {
        const t = full.currentTime || 0, playing = !full.paused;
        full.dataset.src = src; full.src = src;
        full.addEventListener("loadedmetadata", () => { full.currentTime = Math.min(t, full.duration || t); if (playing) full.play(); }, { once: true });
      }
    }
    timeline.textContent = "";
    const total = S.seconds || 1;
    for (const sc of S.scenes) {
      const seg = el("button", "seg"); seg.type = "button";
      seg.style.left = (sc.start / total) * 100 + "%"; seg.style.width = ((sc.end - sc.start) / total) * 100 + "%";
      seg.title = `${sc.label} · ${fmt(sc.start)}–${fmt(sc.end)} · v${sc.version}`;
      seg.appendChild(el("span", "seglab", sc.label));
      if (sc.version && seen[sc.id] && seen[sc.id] !== sc.version) seg.classList.add("fresh");
      seg.addEventListener("click", () => { full.currentTime = sc.start + 0.01; tick(); });
      timeline.appendChild(seg);
    }
    for (const n of S.notes) {
      if (n.t == null) continue;
      const pin = el("span", "pinmark " + n.status); pin.style.left = (n.t / total) * 100 + "%"; pin.title = fmt(n.t) + " · " + n.text;
      timeline.appendChild(pin);
    }
    timeline.appendChild(el("span", "head"));
    tick();
  }
  function sceneAt(t) { return S.scenes.find((s) => t >= s.start && t < s.end) || S.scenes[S.scenes.length - 1]; }
  function tick() {
    if (!S) return;
    const t = full.currentTime || 0, head = $(".head", timeline);
    if (head) head.style.left = (t / (S.seconds || 1)) * 100 + "%";
    $("#clock").textContent = fmt(t);
    $("#play").textContent = full.paused ? "Play" : "Pause";
  }
  full.addEventListener("timeupdate", tick);
  full.addEventListener("play", tick); full.addEventListener("pause", tick);
  $("#play").addEventListener("click", () => (full.paused ? full.play() : full.pause()));
  timeline.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".seg") || !S) return;
    const r = timeline.getBoundingClientRect();
    full.currentTime = ((e.clientX - r.left) / r.width) * S.seconds; tick();
  });
  timeline.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") full.currentTime += 1 / S.fps; else if (e.key === "ArrowLeft") full.currentTime -= 1 / S.fps; else return;
    e.preventDefault(); tick();
  });
  $("#pin").addEventListener("click", () => {
    full.pause();
    pinAt = full.currentTime || 0;
    const sc = sceneAt(pinAt);
    $("#pinlabel").textContent = `Note at ${fmt(pinAt)} (${sc ? sc.label : ""})`;
    $("#pinform").hidden = false; $("#pintext").focus();
  });
  $("#pincancel").addEventListener("click", () => { $("#pinform").hidden = true; });
  $("#pinform").addEventListener("submit", (e) => {
    e.preventDefault();
    const sc = sceneAt(pinAt), text = $("#pintext").value.trim();
    if (!text) return;
    api("/api/notes", { scope: "scene", scene: sc.id, t: pinAt, text }).then(() => { $("#pintext").value = ""; $("#pinform").hidden = true; }).catch((x) => toast(x.message, true));
  });

  // ---- controls built from a param schema ---------------------------------------------------------
  function control(def, name, value, onApply) {
    const wrap = el("div", "ctl");
    const id = "c-" + Math.random().toString(36).slice(2);
    const lab = el("label", null, def.label || name); lab.htmlFor = id;
    wrap.appendChild(lab);
    let input;
    if (def.type === "text") {
      input = el("input"); input.type = "text"; input.value = value ?? def.value ?? "";
      const cap = def.maxWords || (def.role === "headline" ? 8 : 0);
      if (cap) {
        const wc = el("span", "wc");
        const upd = () => { const n = input.value.replace(/[*_]/g, " ").split(/\s+/).filter(Boolean).length; wc.textContent = `${n}/${cap} words`; wc.classList.toggle("over", n > cap); };
        input.addEventListener("input", upd); upd(); wrap.appendChild(wc);
      }
    } else if (def.type === "number" || def.type === "seconds") {
      input = el("input"); input.type = "range"; input.min = def.min ?? 0; input.max = def.max ?? 10; input.step = def.step ?? 0.1;
      input.value = value ?? def.value ?? def.min ?? 0;
      const out = el("output", "num", Number(input.value).toFixed(2).replace(/\.?0+$/, "") + (def.type === "seconds" ? " s" : ""));
      input.addEventListener("input", () => { out.textContent = Number(input.value).toFixed(2).replace(/\.?0+$/, "") + (def.type === "seconds" ? " s" : ""); });
      wrap.appendChild(out);
    } else if (def.type === "color" && def.options) {
      input = el("div", "swatches");
      for (const c of def.options) {
        const b = el("button", "sw" + (c === (value ?? def.value) ? " on" : "")); b.type = "button"; b.style.background = c; b.setAttribute("aria-label", c);
        b.addEventListener("click", () => onApply(c));
        input.appendChild(b);
      }
      wrap.appendChild(input);
      return wrap;
    } else if (def.type === "choice") {
      input = el("select");
      for (const o of def.options || []) { const v = o.value ?? o; const op = el("option", null, o.label ?? v); op.value = v; if (v === (value ?? def.value)) op.selected = true; input.appendChild(op); }
    } else return null;
    input.id = id;
    wrap.appendChild(input);
    const go = el("button", "apply", "Apply"); go.type = "button";
    go.addEventListener("click", () => onApply(input.type === "range" ? Number(input.value) : input.value));
    wrap.appendChild(go);
    return wrap;
  }
  const edit = (scene, param) => (value) => api("/api/edit", { scene, param, value })
    .then(() => toast(`Applied. Re-rendering ${scene || "the whole video"}…`)).catch((x) => toast(x.message, true));

  // ---- notes ------------------------------------------------------------------------------------
  const STATUS = { draft: "Not sent yet", sent: "Sent", applied: "Done", declined: "Not done", question: "CoCo has a question" };
  function noteList(ul, notes) {
    ul.textContent = "";
    for (const n of notes) {
      const li = el("li", "note " + n.status);
      const top = el("div", "nt");
      top.appendChild(el("span", "chip " + n.status, STATUS[n.status] || n.status));
      if (n.t != null) top.appendChild(el("span", "muted small", "at " + fmt(n.t)));
      if (n.status === "draft") {
        const del = el("button", "linkish", "Delete"); del.type = "button";
        del.addEventListener("click", () => api("/api/note_update", { id: n.id, deleted: true }).catch((x) => toast(x.message, true)));
        top.appendChild(del);
      }
      li.appendChild(top);
      li.appendChild(el("p", "nb", n.text));
      for (const r of n.replies || []) li.appendChild(el("p", "reply", "CoCo: " + r.text));
      ul.appendChild(li);
    }
  }

  // ---- scene cards ------------------------------------------------------------------------------
  function renderScenes() {
    const host = $("#scenes");
    for (const sc of S.scenes) {
      let card = host.querySelector(`[data-scene="${sc.id}"]`);
      if (!card) {
        card = $("#tpl-scene").content.firstElementChild.cloneNode(true);
        card.dataset.scene = sc.id;
        $(".noteform", card).addEventListener("submit", (e) => {
          e.preventDefault();
          const ta = $("textarea", e.target), text = ta.value.trim();
          if (!text) return;
          api("/api/notes", { scope: "scene", scene: sc.id, text }).then(() => { ta.value = ""; }).catch((x) => toast(x.message, true));
        });
        const clip = $(".clip", card);
        card.addEventListener("pointerenter", () => clip.play().catch(() => {}));
        card.addEventListener("pointerleave", () => clip.pause());
        // Looking at a card acknowledges its latest version, which clears its "Updated" badge.
        // Freshness follows version numbers, not cache keys, so re-keying a cache never cries wolf.
        card.addEventListener("pointerdown", () => {
          const v = Number(card.dataset.version);
          if (!v) return;
          seen[sc.id] = v; sessionStorage.setItem("review-seen", JSON.stringify(seen));
          $(".new", card).hidden = true; card.classList.remove("fresh");
          const seg = [...timeline.querySelectorAll(".seg")][S.scenes.findIndex((x) => x.id === sc.id)];
          if (seg) seg.classList.remove("fresh");
        });
        $(".versions", card).addEventListener("change", (e) => {
          const old = $(".old", card), v = e.target.value;
          old.hidden = !v; card.classList.toggle("comparing", !!v);
          if (v) { old.src = media(v); old.play().catch(() => {}); } else old.removeAttribute("src");
        });
        host.appendChild(card);
      }
      $(".sid", card).textContent = sc.label;
      $(".role", card).textContent = sc.role ? "· " + sc.role : "";
      $(".ver", card).textContent = "v" + sc.version;
      $(".sim", card).hidden = !sc.simulated;
      $(".dur", card).textContent = `${fmt(sc.start)}–${fmt(sc.end)} · ${sc.dur.toFixed(2)} s`;
      // narration: the line, and what Voice Studio has of it
      const lr = lineRow(sc.id), sayEl = $(".say", card), lb = $(".badge.line", card);
      sayEl.hidden = !sc.say; sayEl.textContent = "";
      if (sc.say) {
        sayEl.append(el("span", null, "“" + sc.say + "” "));
        const why = lr.status === "ok" ? `${lr.take}, ${lr.speechSeconds.toFixed(1)} s of ${lr.room.toFixed(1)} s` : lr.message || "";
        if (why) sayEl.append(el("span", "muted", why));
      }
      const label = { ok: lr.test ? "Test voice" : "Voice recorded", missing: "Voice to record", stale: "Line changed", long: "Line too long" }[lr.status];
      lb.hidden = !label || !(S.sound && S.sound.narration); lb.textContent = label || "";
      lb.className = "badge line " + (lr.test && lr.status === "ok" ? "test" : lr.status);
      const clip = $(".clip", card);
      if (sc.clip) {
        const src = media(sc.clip);
        if (clip.dataset.src !== src) {
          clip.dataset.src = src; clip.preload = "auto"; clip.src = src;
          // Show a representative frame while the card is idle, not a black box.
          clip.addEventListener("loadeddata", () => { if (clip.paused) clip.currentTime = Math.min(clip.duration * 0.6, Math.max(0, clip.duration - 0.05)); }, { once: true });
        }
      }
      const fresh = sc.version && seen[sc.id] && seen[sc.id] !== sc.version;
      $(".new", card).hidden = !fresh;
      card.classList.toggle("fresh", !!fresh);
      card.dataset.version = String(sc.version || 0);
      if (sc.version && !seen[sc.id]) seen[sc.id] = sc.version;
      // compare with earlier versions
      const sel = $(".versions", card), olds = sc.versions.filter((v) => v.key !== sc.key);
      $(".compare", card).hidden = !olds.length;
      const want = ["", ...olds.map((v) => v.file)].join("|");
      if (sel.dataset.v !== want) {
        sel.dataset.v = want; sel.textContent = "";
        const none = el("option", null, "(nothing)"); none.value = ""; sel.appendChild(none);
        for (const v of olds.reverse()) { const o = el("option", null, "v" + v.v); o.value = v.file; sel.appendChild(o); }
      }
      // direct edits
      const ctl = $(".edits .controls", card);
      const sig = JSON.stringify([sc.schema, sc.params, sc.dur, sc.say, sc.voiceAt]);
      if (ctl.dataset.sig !== sig) {
        ctl.dataset.sig = sig; ctl.textContent = "";
        const d = control({ type: "seconds", label: "Length", min: 0.5, max: Math.max(12, Math.ceil(sc.dur * 2)), step: 0.1 }, "dur", sc.dur, edit(sc.id, "dur"));
        if (d) ctl.appendChild(d);
        const room = Math.max(0.1, sc.dur - (sc.voiceAt ?? 0.35) - 0.3);
        const sayCtl = control({ type: "text", label: `Narration line (about ${Math.floor(room * 2.5)} words fit)`, maxWords: Math.max(1, Math.floor(room * 3.2)) },
          "say", sc.say, edit(sc.id, "say"));
        if (sayCtl) ctl.appendChild(sayCtl);
        if (sc.say) {
          const at = control({ type: "seconds", label: "Voice starts after the cut", min: 0, max: Math.min(3, Math.max(0.5, sc.dur - 1)), step: 0.05 },
            "voiceAt", sc.voiceAt ?? 0.35, edit(sc.id, "voiceAt"));
          if (at) ctl.appendChild(at);
        }
        for (const [k, def] of Object.entries(sc.schema || {})) {
          if (def.editable === false) continue;
          const c = control(def, k, sc.params[k], edit(sc.id, k));
          if (c) ctl.appendChild(c);
        }
      }
      noteList($(".notes", card), S.notes.filter((n) => n.scene === sc.id));
    }
    sessionStorage.setItem("review-seen", JSON.stringify(seen));
  }

  function lineRow(id) { return ((S.sound && S.sound.lines && S.sound.lines.scenes) || []).find((r) => r.scene === id) || { status: "none" }; }

  // ---- sound ----------------------------------------------------------------------------------------------
  let snapPlan = null;
  function renderSound() {
    const so = S.sound || {};
    $("#openvoice").href = "/voice?k=" + encodeURIComponent(K);
    if (so.error) { $("#mixstatus").textContent = "Sound unavailable: " + so.error; return; }
    const lib = so.library || [], m = so.music || {};
    const sel = $("#track"), sig = JSON.stringify(lib.map((t) => t.id)) + "|" + (m.track || "");
    if (sel.dataset.sig !== sig) {
      sel.dataset.sig = sig; sel.textContent = "";
      const none = el("option", null, "No music"); none.value = ""; sel.appendChild(none);
      for (const t of lib) {
        const o = el("option", null, `${t.title}, ${t.artist} · ${t.licence}${t.tags.length ? " · " + t.tags.slice(0, 2).join(", ") : ""}`);
        o.value = t.id; if (t.id === m.track) o.selected = true; sel.appendChild(o);
      }
    }
    const t = lib.find((x) => x.id === m.track);
    const info = $("#trackinfo"); info.textContent = "";
    if (t) {
      info.append(el("span", null, `${Math.round(t.bpm)} BPM · licence checked ${t.checked} · `));
      const a = el("a", null, "track page"); a.href = t.page; a.target = "_blank"; a.rel = "noopener noreferrer"; info.append(a);
      info.append(el("span", null, ". Credit on the end card: " + t.credit));
    } else info.textContent = lib.length ? "Silent. Pick a track to hear it under the cut." : "The music library is empty: run audio/library.py seed.";
    $("#musicedit").value = m.edit || (t && t.landable ? "ending" : "intro");
    $("#musicedit").disabled = !t;
    $("#musicedit").querySelector('option[value="ending"]').disabled = !!t && !t.landable;
    $("#snap").disabled = !t;
    const gain = $("#gain");
    if (document.activeElement !== gain) gain.value = m.gainDb ?? 0;
    $("#gainout").textContent = (Number(gain.value) > 0 ? "+" : "") + gain.value + " dB";
    gain.disabled = $("#gainapply").disabled = !t;
    $("#narr").value = so.narration ? "1" : "0";
    const sm = (so.lines && so.lines.summary) || {};
    $("#narrinfo").textContent = so.narration ? `${sm.ok || 0} of ${sm.lines || 0} lines recorded${sm.long ? ", " + sm.long + " too long" : ""}${sm.stale ? ", " + sm.stale + " changed" : ""}${sm.test ? " · includes a TEST voice" : ""}`
      : (sm.lines ? `${sm.lines} lines written; switch on to hear them` : "Write a narration line on any scene to start");
    const pv = so.preview;
    $("#mixstatus").textContent = !pv ? "" : pv.error ? "The last mix failed: " + pv.error
      : `In the preview: ${pv.narrated ? "voice and music" : pv.music ? "music only" : "silent"}${pv.lufs != null ? ", " + pv.lufs.toFixed(1) + " LUFS" : ""}`;
    const warn = $("#soundwarn"), w = (pv && pv.warnings) || [];
    warn.hidden = !w.length; warn.textContent = w.join("\n");
  }
  const sound = (change, msg) => api("/api/sound", change).then(() => toast(msg)).catch((x) => toast(x.message, true));
  $("#track").addEventListener("change", (e) => sound({ track: e.target.value }, e.target.value ? "Music changed. Re-mixing…" : "Music off. Re-mixing…"));
  $("#musicedit").addEventListener("change", (e) => sound({ edit: e.target.value }, "Re-cutting the music…"));
  $("#gain").addEventListener("input", (e) => { $("#gainout").textContent = (Number(e.target.value) > 0 ? "+" : "") + e.target.value + " dB"; });
  $("#gainapply").addEventListener("click", () => sound({ gainDb: Number($("#gain").value) }, "Music level changed. Re-mixing…"));
  $("#narr").addEventListener("change", (e) => sound({ narration: e.target.value === "1" }, "Re-mixing…"));
  $("#snap").addEventListener("click", () => api("/api/sound/snap", { to: "beat" }).then((r) => {
    snapPlan = r;
    const moved = r.cuts.filter((c) => c.movedMs);
    $("#snapinfo").hidden = false;
    $("#snapinfo").textContent = moved.length ? moved.map((c) => `${c.cut}: ${c.movedMs > 0 ? "+" : ""}${c.movedMs} ms`).join(" · ") + ` (kept: ${r.fixed.join(", ") || "none"})`
      : "Every cut is already on the beat, or cannot move without squeezing a line.";
    $("#snapapply").hidden = !Object.keys(r.durations).length;
  }).catch((x) => toast(x.message, true)));
  $("#snapapply").addEventListener("click", () => api("/api/sound/snap", { to: "beat", apply: true }).then(() => {
    $("#snapapply").hidden = true; $("#snapinfo").hidden = true; toast("Cuts moved onto the beat. Re-rendering the scenes that changed…");
  }).catch((x) => toast(x.message, true)));

  function renderGlobal() {
    const host = $("#globalcontrols");
    const sig = JSON.stringify([S.globalSchema, S.global]);
    if (host.dataset.sig !== sig) {
      host.dataset.sig = sig; host.textContent = "";
      for (const [k, def] of Object.entries(S.globalSchema || {})) { const c = control(def, k, S.global[k], edit(null, k)); if (c) host.appendChild(c); }
    }
    noteList($("#globalnotes"), S.notes.filter((n) => n.scope === "global"));
  }
  $("#globalform").addEventListener("submit", (e) => {
    e.preventDefault();
    const ta = $("#globaltext"), text = ta.value.trim();
    if (!text) return;
    api("/api/notes", { scope: "global", text }).then(() => { ta.value = ""; }).catch((x) => toast(x.message, true));
  });
  $("#send").addEventListener("click", () => api("/api/send", {}).then(() => toast("Sent to CoCo.")).catch((x) => toast(x.message, true)));
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input,textarea,select")) return;
    if (e.key === " ") { e.preventDefault(); full.paused ? full.play() : full.pause(); }
    if (e.key === "n" || e.key === "N") { e.preventDefault(); $("#pin").click(); }
  });

  // ---- load + live --------------------------------------------------------------------------------
  let loading = false, pending = false;
  async function load() {
    if (loading) { pending = true; return; }
    loading = true;
    try { S = await api("/api/state"); renderCut(); renderGlobal(); renderSound(); renderScenes(); renderStatus(); }
    catch (x) { $("#title").textContent = "Cannot reach the review server"; $("#meta").textContent = x.message; }
    finally { loading = false; if (pending) { pending = false; load(); } }
  }
  load();
  const es = new EventSource("/api/events?k=" + encodeURIComponent(K));
  es.addEventListener("state", load);
})();

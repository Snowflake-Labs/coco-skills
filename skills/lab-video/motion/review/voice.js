// voice.js: Voice Studio, the narration booth of the review server. Ported from an earlier
// standalone Voice Studio; what changed is where takes go and what they are measured against.
//
// - The script comes from the project (/api/sound): one line per scene, timed by the picture itself.
// - "Record all" records from the current scene to the end at video timing; a cue marker is stamped each time a
//   new line appears, and the server (audio/voice.py) splits the take into lines from the speech itself.
// - "Record this line" is a pick-up: one line, its scene playing muted alongside, saved as its own take.
// - Every take is uploaded the moment it stops. Nothing is sped up or retimed, ever: a line that runs long is
//   reported so the scene can be lengthened or the line cut.
// No inline handlers; nothing is fetched from anywhere but this server.
(() => {
  "use strict";
  const Q = new URLSearchParams(location.search);
  const K = Q.get("k") || "";
  const TEST = Q.get("test") === "1"; // automated drives only (Chrome's fake microphone): such takes can never ship
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const withKey = (p) => p + (p.includes("?") ? "&" : "?") + "k=" + encodeURIComponent(K);
  const api = (p, body) => fetch(withKey(p), body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {})
    .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || r.statusText); return j; });
  const clock = (s) => { s = Math.max(0, s); return Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0"); };
  const STATUS_LABEL = { ok: "Recorded", missing: "Not recorded", stale: "Words changed", long: "Too long", none: "No line" };

  let S = null, script = null, scenes = [];
  let state = "idle";            // idle | permission | countdown | rehearsing | recording | paused | saving
  let mode = null;               // rehearse | full | line
  let position = 0;              // script seconds
  let segStart = 0, origin = 0, realElapsed = 0, realOrigin = 0, recordStart = 0, recPace = 1;
  let stream = null, recorder = null, chunks = [], audioCtx = null, analyser = null, interval = null, countTimer = null;
  let markers = [], lastScene = -1, lineScene = null, lineStop = Infinity, takeCount = 0, uploading = 0, attempt = 0;
  const pace = () => Number($("pace").value);
  const sceneAt = (t) => { const i = scenes.findIndex((s) => t < s.end - 1e-6); return i < 0 ? scenes.length - 1 : i; };
  const active = () => ["permission", "countdown", "rehearsing", "recording", "paused", "saving"].includes(state);

  function error(msg) { $("error").textContent = msg || ""; $("error").hidden = !msg; }
  function setStatus(txt) { $("status").textContent = txt; document.body.classList.toggle("recording", state === "recording"); }

  // ---- display ---------------------------------------------------------------------------------------
  function lineRow(id) { return ((S && S.lines && S.lines.scenes) || []).find((r) => r.scene === id) || { status: "none" }; }
  function chapters() {
    const nav = $("chapters"); nav.textContent = "";
    scenes.forEach((s, i) => {
      const b = el("button"); b.type = "button";
      b.append(el("time", null, clock(s.start)), el("span", "lab", s.label));
      const row = lineRow(s.id);
      if (s.say) b.append(el("span", "say", s.say));
      const chip = el("span", "chip " + (row.test && row.status === "ok" ? "test" : row.status), row.test && row.status === "ok" ? "Test voice" : STATUS_LABEL[row.status] || row.status);
      b.append(chip);
      b.addEventListener("click", () => { if (active()) return; position = s.start; lastScene = -1; seekPicture(); display(); });
      nav.append(b);
    });
  }
  function display() {
    if (!scenes.length) return;
    const i = sceneAt(Math.min(position, script.duration - 1e-6));
    const s = scenes[i];
    [...$("chapters").children].forEach((b, k) => b.setAttribute("aria-current", String(k === i)));
    $("scene-number").textContent = `${String(i + 1).padStart(2, "0")} / ${String(scenes.length).padStart(2, "0")} · ${s.label}`;
    $("scene-title").textContent = s.say ? `${s.words} words in ${s.room.toFixed(1)} s` : "No narration in this scene";
    const scr = $("script"); scr.textContent = "";
    if (s.say) { const m = el("span", "mark", s.say); scr.append(m); scr.classList.remove("idle-line"); }
    else { scr.textContent = "(picture only)"; scr.classList.add("idle-line"); }
    const speaking = s.say && position >= s.speechStart - 0.05 && position < s.end;
    scr.classList.toggle("active", !!speaking && ["rehearsing", "recording"].includes(state));
    const nxt = scenes.slice(i + 1).find((x) => x.say);
    $("next-script").textContent = nxt ? nxt.say : "That's the last line. Hold for a second of room tone.";
    $("remaining").textContent = clock(Math.ceil(Math.max(0, s.end - position)));
    $("elapsed").textContent = `${clock(position)} / ${clock(script.duration)}`;
    $("progress").style.width = `${Math.min(100, (position / script.duration) * 100)}%`;
    $("read-cue").textContent = state === "paused" ? "Paused. Resume when ready."
      : !["rehearsing", "recording"].includes(state) ? "Rehearse first. Record all lines in one go, or one line at a time."
      : !s.say ? "Picture only. Get ready for the next line." : position < s.speechStart - 0.05 ? "Ready for this line…" : "Read the line.";
    if (i !== lastScene) {
      lastScene = i;
      lineBox(s);
      if (state === "recording" && mode === "full") markers.push({ scene: s.id, lineHash: s.lineHash, text: s.say, audioSeconds: audioNow() });
    }
  }
  function lineBox(s) {
    const row = lineRow(s.id);
    const fit = $("fit"), ul = $("line-takes");
    ul.textContent = "";
    if (!s.say) { fit.textContent = "This scene has no narration line."; fit.className = "fit"; return; }
    fit.className = "fit" + (row.status === "long" || row.status === "stale" ? " bad" : "");
    fit.textContent = row.status === "ok" ? `${row.take}: ${row.speechSeconds.toFixed(2)} s of speech in ${s.room.toFixed(2)} s of room. ${row.test ? "Test voice: record it for real." : ""}`
      : row.status === "long" ? `${row.take} is too long: ${row.message}.`
      : row.status === "stale" ? "The words changed since this line was recorded. Record it again." : `Not recorded yet. About ${s.estimatedSeconds.toFixed(1)} s at an easy pace; ${s.room.toFixed(1)} s of room.`;
    for (const take of (row.takes || []).slice().reverse()) {
      const li = el("li", take === row.take ? "chosen" : "");
      li.append(el("span", null, take + (take === row.take ? " (in the mix)" : "")));
      const a = el("audio"); a.controls = true; a.preload = "none";
      a.src = withKey("/voice-media/lines/" + encodeURIComponent(`${s.id}--${take}.wav`));
      li.append(a);
      if (take !== row.take) {
        const use = el("button", null, "Use this one"); use.type = "button";
        use.addEventListener("click", () => api("/api/voice/pick", { scene: s.id, take }).then(refresh).catch((x) => error(x.message)));
        li.append(use);
      }
      ul.append(li);
    }
  }
  function updateControls() {
    const busy = active();
    $("record").disabled = busy || !script;
    $("pickup").disabled = busy || !script || !scenes[sceneAt(position)]?.say;
    $("rehearse").disabled = busy || !script;
    $("pause").disabled = !["rehearsing", "recording", "paused"].includes(state);
    $("stop").disabled = !["countdown", "rehearsing", "recording", "paused", "permission"].includes(state);
    $("reset").disabled = busy;
    $("device").disabled = busy; $("pace").disabled = busy;
    setStatus({ idle: "Ready", permission: "Microphone…", countdown: "Get ready", rehearsing: "Rehearsing", recording: "Recording", paused: "Paused", saving: "Saving take" }[state] || state);
  }

  // ---- the muted picture ---------------------------------------------------------------------------------
  const pic = $("picture");
  function seekPicture() { if (pic.src && Number.isFinite(pic.duration)) pic.currentTime = Math.min(position, pic.duration - 0.05); }
  function syncPicture() {
    if (!pic.src || !Number.isFinite(pic.duration)) return;
    if (Math.abs(pic.currentTime - position) > 0.15) pic.currentTime = Math.min(position, pic.duration - 0.05);
  }

  // ---- microphone --------------------------------------------------------------------------------------
  async function devices() {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const list = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
    const sel = $("device"), keep = sel.value;
    sel.textContent = "";
    sel.append(Object.assign(el("option", null, "Default microphone"), { value: "" }));
    list.forEach((d, i) => sel.append(Object.assign(el("option", null, d.label || `Microphone ${i + 1}`), { value: d.deviceId })));
    sel.value = [...sel.options].some((o) => o.value === keep) ? keep : "";
  }
  async function microphone() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error("Recording needs Chrome on this Mac (127.0.0.1).");
    const id = $("device").value;
    // Raw signal: no echo cancellation, noise suppression or auto gain, which pump and colour a voice.
    stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: id ? { exact: id } : undefined, channelCount: 1,
      echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false });
    audioCtx = new AudioContext();
    analyser = audioCtx.createAnalyser(); analyser.fftSize = 2048;
    audioCtx.createMediaStreamSource(stream).connect(analyser);
    devices().catch(() => {});
  }
  function level() {
    if (!analyser) return;
    const d = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(d);
    const rms = Math.sqrt(d.reduce((s, v) => s + v * v, 0) / d.length);
    const bar = $("level-bar"); bar.style.width = `${Math.min(100, rms * 500)}%`; bar.style.background = rms > 0.45 ? "#b52128" : "#287757";
  }
  function cleanup() {
    stream?.getTracks().forEach((t) => t.stop()); stream = null;
    if (audioCtx) audioCtx.close().catch(() => {}); audioCtx = null; analyser = null; $("level-bar").style.width = "0";
  }
  const audioNow = () => Math.round((realElapsed + (performance.now() - realOrigin) / 1000) * 1000) / 1000;

  // ---- transport ------------------------------------------------------------------------------------------
  function tick() {
    if (!["rehearsing", "recording"].includes(state)) return;
    position = segStart + ((performance.now() - origin) / 1000) * recPace;
    level(); syncPicture(); display();
    if (position >= lineStop || position >= script.duration + 1.2 * recPace) stop();
  }
  function begin() {
    $("countdown").hidden = true;
    segStart = position; origin = performance.now(); realOrigin = origin; realElapsed = 0; recordStart = position;
    recPace = mode === "rehearse" ? pace() : 1; markers = []; lastScene = -1;
    pic.playbackRate = recPace; seekPicture(); pic.play().catch(() => {});
    if (mode !== "rehearse") {
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t));
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 192000 } : undefined);
      chunks = [];
      recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      recorder.onerror = (e) => { error(e.error?.message || "Recording failed."); stop(); };
      const takeMode = mode, takeMarkers = () => markers.slice();
      recorder.onstop = () => save(new Blob(chunks, { type: recorder.mimeType }), takeMode, takeMarkers(), realElapsed);
      recorder.start(1000);
      state = "recording";
      if (mode === "line") {
        const s = scenes[sceneAt(position)];
        markers.push({ scene: s.id, lineHash: s.lineHash, text: s.say, audioSeconds: 0 });
      }
    } else state = "rehearsing";
    updateControls(); display();
    interval = setInterval(tick, 40);
  }
  async function start(selected) {
    if (active() || !script) return;
    const mine = ++attempt;
    if (selected !== "rehearse" && pace() !== 1) { error("Choose Video timing to record. Relaxed and Slower are for rehearsal only."); return; }
    error(""); mode = selected;
    const i = sceneAt(Math.min(position, script.duration - 1e-6));
    if (position >= script.duration - 0.05) position = 0;
    // A take starts on a cut, so the first line appears exactly when its picture does.
    position = scenes[sceneAt(Math.min(position, script.duration - 1e-6))].start;
    lineScene = mode === "line" ? scenes[i] : null;
    lineStop = mode === "line" ? scenes[i].end + 1.0 : Infinity;
    if (mode === "line") position = scenes[i].start;
    try {
      if (mode !== "rehearse") { state = "permission"; updateControls(); await microphone(); }
      if (mine !== attempt) return;
      state = "countdown"; updateControls(); $("countdown").hidden = false;
      let n = 3; $("countdown-number").textContent = n;
      countTimer = setInterval(() => { n -= 1; if (n > 0) $("countdown-number").textContent = n; else { clearInterval(countTimer); countTimer = null; begin(); } }, 1000);
    } catch (x) {
      if (mine !== attempt) return;
      error(x.name === "NotAllowedError" ? "Microphone permission was not granted. Rehearsal still works; check Chrome's site settings and macOS Privacy & Security > Microphone." : x.message);
      cleanup(); state = "idle"; updateControls();
    }
  }
  function pause() {
    if (state === "paused") {
      segStart = position; origin = performance.now(); realOrigin = origin;
      if (recorder?.state === "paused") recorder.resume();
      state = mode === "rehearse" ? "rehearsing" : "recording"; pic.play().catch(() => {});
    } else {
      position = segStart + ((performance.now() - origin) / 1000) * recPace;
      realElapsed += (performance.now() - realOrigin) / 1000;
      if (recorder?.state === "recording") recorder.pause();
      state = "paused"; pic.pause();
      // A paused take still counts its lines from the audio: the cue clock pauses with the recording.
    }
    updateControls(); display();
  }
  function stop() {
    attempt += 1;
    if (countTimer) { clearInterval(countTimer); countTimer = null; }
    $("countdown").hidden = true;
    if (["rehearsing", "recording"].includes(state)) realElapsed += (performance.now() - realOrigin) / 1000;
    clearInterval(interval); interval = null; pic.pause();
    const finishing = recorder && recorder.state !== "inactive";
    if (finishing) { state = "saving"; recorder.stop(); } else state = "idle";
    cleanup(); updateControls(); display();
  }

  // ---- saving -----------------------------------------------------------------------------------------------
  async function save(blob, takeMode, takeMarkers, seconds) {
    recorder = null;
    if (!blob.size || !takeMarkers.length) { state = "idle"; updateControls(); error("No audio was captured."); return; }
    takeCount += 1; uploading += 1;
    const row = el("div", "take-row");
    const head = el("strong", null, `Take ${takeCount} · ${takeMode === "line" ? "one line" : takeMarkers.length + " scenes"} · ${clock(seconds)}`);
    const msg = el("span", "lines", "Saving into the project…");
    const play = el("audio"); play.controls = true; play.preload = "metadata"; play.src = URL.createObjectURL(blob);
    const keep = el("a", null, "Download the original"); keep.href = play.src; keep.download = `voice-take-${takeCount}.${blob.type.includes("mp4") ? "m4a" : "webm"}`;
    row.append(head, msg, play, keep);
    $("results").prepend(row);
    state = "idle"; updateControls();
    const meta = { version: 3, mode: takeMode === "line" ? "line" : "full", test: TEST, audioSeconds: Math.round(seconds * 1000) / 1000,
      scriptHash: script.hash, device: $("device").selectedOptions[0]?.textContent || "", markers: takeMarkers };
    try {
      const r = await fetch(withKey("/api/voice/take"), { method: "POST", body: blob,
        headers: { "content-type": blob.type.split(";")[0] || "audio/webm", "x-take-meta": encodeURIComponent(JSON.stringify(meta)) } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        msg.className = "lines bad";
        msg.textContent = (j.take ? j.take + " was kept, but " : "") + (j.error || r.statusText);
      } else {
        const lines = (j.summary.lines || []).map((l) => l.scene).join(", ");
        msg.textContent = `${j.take}: ${lines || "no lines"} · speech at ${j.summary.takeLufs} LUFS` + (j.summary.clipped ? " · CLIPPED: move back from the microphone and record again" : "")
          + ((j.summary.stray || []).length ? ` · ${(j.summary.stray || []).length} stray phrase(s) left out` : "");
        S = j.sound; render();
      }
    } catch (x) {
      msg.className = "lines bad"; msg.textContent = "Could not reach the review server: " + x.message + ". Download the original to keep it.";
    } finally { uploading -= 1; }
  }
  addEventListener("beforeunload", (e) => { if (uploading || state === "recording") { e.preventDefault(); e.returnValue = ""; } });

  // ---- load ---------------------------------------------------------------------------------------------------
  function render() {
    if (!S || S.error) { error(S ? S.error : "No sound state"); return; }
    script = S.script; scenes = script.scenes;
    $("brief-title").textContent = $("project").textContent;
    $("brief-length").textContent = `${script.duration.toFixed(1)} s`;
    const sm = S.lines.summary;
    $("brief-lines").textContent = `${sm.lines} lines, ${script.words} words · ${sm.ok} recorded${sm.long ? ", " + sm.long + " too long" : ""}${sm.stale ? ", " + sm.stale + " changed" : ""}${sm.missing ? ", " + sm.missing + " to go" : ""}`;
    if (!S.narration) error("Narration is switched off for this video (audio.narration). Recordings are still saved; turn it on in the review page's Sound panel.");
    chapters(); lastScene = -1; display(); updateControls();
  }
  async function refresh() { S = await api("/api/sound"); render(); }
  async function load() {
    const st = await api("/api/state");
    $("project").textContent = st.title;
    document.title = "Voice Studio · " + st.title;
    $("back").href = "/?k=" + encodeURIComponent(K);
    if (st.preview) pic.src = withKey("/media/" + st.preview.file) + "&v=" + Date.now();
    $("nopic").hidden = !!st.preview; pic.hidden = !st.preview;
    S = st.sound; render();
    window.__VOICE_READY__ = true;
  }
  $("record").addEventListener("click", () => start("full"));
  $("pickup").addEventListener("click", () => start("line"));
  $("rehearse").addEventListener("click", () => start("rehearse"));
  $("pause").addEventListener("click", pause);
  $("stop").addEventListener("click", stop);
  $("reset").addEventListener("click", () => { position = 0; lastScene = -1; seekPicture(); display(); updateControls(); });
  $("pace").addEventListener("change", display);
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("select,input,textarea")) return;
    if (e.key === " " && ["rehearsing", "recording", "paused"].includes(state)) { e.preventDefault(); pause(); }
    if (e.key === "Escape" && active()) { e.preventDefault(); stop(); }
  });
  const es = new EventSource(withKey("/api/events"));
  es.addEventListener("state", () => { if (!active()) refresh().catch(() => {}); });
  load().catch((x) => { $("project").textContent = "Cannot reach the review server"; error(x.message); });
})();

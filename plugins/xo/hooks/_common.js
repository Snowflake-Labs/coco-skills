'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');

function readInput() {
    const raw = fs.readFileSync(0, 'utf8');
    return JSON.parse(raw);
}

function isoUtc() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function localWallClock() {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, '0');
    const datePart = [
        now.getFullYear(),
        pad(now.getMonth() + 1),
        pad(now.getDate()),
    ].join('-');
    const timePart = [
        pad(now.getHours()),
        pad(now.getMinutes()),
    ].join(':');

    try {
        const zoneParts = new Intl.DateTimeFormat('en-GB', {
            timeZoneName: 'shortOffset',
        }).formatToParts(now);
        const zoneName = zoneParts.find((part) => part.type === 'timeZoneName')?.value;
        if (zoneName) {
            return `${datePart} ${timePart} ${zoneName}`;
        }
    } catch (_) {}

    const offsetMinutes = -now.getTimezoneOffset();
    const sign = offsetMinutes >= 0 ? '+' : '-';
    const offsetHours = pad(Math.floor(Math.abs(offsetMinutes) / 60));
    const offsetRemainder = pad(Math.abs(offsetMinutes) % 60);
    return `${datePart} ${timePart} UTC${sign}${offsetHours}:${offsetRemainder}`;
}

function shortSid(sessionId) {
    return String(sessionId).slice(0, 8);
}

// Read at most the last `maxBytes` of a regular file. Refuses anything that is not a
// regular file (FIFO, device, directory) before opening it, so a vault entry that is a
// symlink to /dev/zero or a pipe cannot hang the caller. When the file is larger than
// the cap, the partial first line is dropped. Throws on error; callers decide fail-open.
function readFileTail(filePath, maxBytes) {
    if (!fs.statSync(filePath).isFile()) throw new Error('not a regular file: ' + filePath);
    const fd = fs.openSync(filePath, 'r');
    try {
        const st = fs.fstatSync(fd);
        if (!st.isFile()) throw new Error('not a regular file: ' + filePath);
        const len = Math.min(st.size, maxBytes);
        const buf = Buffer.alloc(len);
        const got = fs.readSync(fd, buf, 0, len, st.size - len);
        let text = buf.toString('utf8', 0, got);
        if (st.size > maxBytes) { const nl = text.indexOf('\n'); text = nl >= 0 ? text.slice(nl + 1) : ''; }
        return { text, truncated: st.size > maxBytes };
    } finally {
        fs.closeSync(fd);
    }
}

function appendLog(logFile, line) {
    try {
        fs.mkdirSync(path.dirname(logFile), { recursive: true });
        fs.appendFileSync(logFile, line + '\n');
    } catch (_) {}
}

function atomicWrite(filePath, content) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const tmp = filePath + '.tmp';
    fs.writeFileSync(tmp, content, 'utf8');
    fs.renameSync(tmp, filePath);
}

function logError(msg) {
    process.stderr.write(msg + '\n');
}

// ---- Session naming ------------------------------------------------
// The agent names the session through the rename_session tool; hooks only read the
// current title and inject instructions. Title lookup is filename-only (cheap enough
// for every prompt) and reuses findConversationMeta.
const SESSION_NAME_FORMAT = '`WI-{N} {JIRA ID} {customer name} {repo}#{PR number} {topic}` (omit absent segments; `{topic}` always present)';

// Upper bound on how many prompts carry the first-rename directive when no rename_session
// call is ever observed (tool not offered on this surface, or the flag is off).
const SESSION_NAME_MAX_PROMPTS = 2;

function readSessionTitle(sessionId, conversationsDir) {
    try {
        const metaPath = findConversationMeta(sessionId, conversationsDir);
        if (!metaPath) return null;
        const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
        if (typeof meta.title !== 'string' || !meta.title.trim()) return null;
        return { title: meta.title.trim() };
    } catch (_) {
        return null;
    }
}

// Silent instruction to verify the current session title, or '' when the title is unknown.
function sessionTitleCheck(titleInfo) {
    if (!titleInfo) return '';
    const exactTitle = JSON.stringify(titleInfo.title.replace(/\s+/g, ' '));
    return `SESSION NAME [silent]: current title (exact JSON) ${exactTitle}. If \`rename_session\` is available, verify the title matches ${SESSION_NAME_FORMAT} for the session's current subject (see Session Naming in the \`save-protocol\` reference) and call \`rename_session\` with the corrected title if it is inaccurate. Do not announce the rename.`;
}

function sessionNamingStatePath(contextStateDir, sessionId) {
    return path.join(contextStateDir, `${sessionId}.session-name`);
}

// The state file holds the number of prompts that carried the first-rename directive, or
// 'done' once a rename_session call was observed. Fail-open: unreadable state means no directive.
function readSessionNamingState(contextStateDir, sessionId) {
    try {
        const raw = fs.readFileSync(sessionNamingStatePath(contextStateDir, sessionId), 'utf8').trim();
        if (raw === 'done') return 'done';
        const n = parseInt(raw, 10);
        return Number.isFinite(n) ? n : 0;
    } catch (_) {
        return 0;
    }
}

// Returns true when this prompt should carry the first-rename directive, and records the
// emission. The directive is bounded: it stops after a rename_session call is observed or
// after SESSION_NAME_MAX_PROMPTS emissions. An unknown session id cannot be tracked, so it
// never emits.
function claimSessionNamingDirective(contextStateDir, sessionId) {
    if (!sessionId || sessionId === 'unknown') return false;
    const state = readSessionNamingState(contextStateDir, sessionId);
    if (state === 'done' || state >= SESSION_NAME_MAX_PROMPTS) return false;
    try { atomicWrite(sessionNamingStatePath(contextStateDir, sessionId), String(state + 1)); } catch (_) { return false; }
    return true;
}

// Machine-wide marker (outside context-state, which is pruned) so the settings tip is shown once.
function sessionNamingTipMarker(contextStateDir) {
    return path.join(path.dirname(contextStateDir), 'xo-rename-tip.shown');
}

function markSessionNamingComplete(contextStateDir, sessionId) {
    if (!sessionId || sessionId === 'unknown') return;
    try { atomicWrite(sessionNamingStatePath(contextStateDir, sessionId), 'done'); } catch (_) {}
    // The tool works here, so the settings tip is never needed.
    try { atomicWrite(sessionNamingTipMarker(contextStateDir), ''); } catch (_) {}
}

// True at most once per machine: the bounded directive was exhausted with no rename_session
// call observed, which suggests the tool is not enabled. Never fires for an unknown session.
function claimSessionNamingTip(contextStateDir, sessionId) {
    if (!sessionId || sessionId === 'unknown') return false;
    const state = readSessionNamingState(contextStateDir, sessionId);
    if (state === 'done' || state < SESSION_NAME_MAX_PROMPTS) return false;
    const marker = sessionNamingTipMarker(contextStateDir);
    try {
        if (fs.existsSync(marker)) return false;
        atomicWrite(marker, '');
    } catch (_) { return false; }
    return true;
}

const SESSION_NAME_TIP = "Tip: automatic session naming is available via the **Rename Session** setting in Cortex Code settings — enable it if you'd like XO to keep session titles in sync. (Shown once; ignore if you're happy without it.) Relay this tip to the operator in one short line, then continue.";

function buildSessionNamingDirective() {
    return `SESSION NAME [REQUIRED TOOL CALL]: If \`rename_session\` is available, call it NOW, before answering this user prompt or invoking any other tool. First determine the session's current topic and construct a title matching ${SESSION_NAME_FORMAT}; then call \`rename_session\`. This is a mandatory first action, not a suggestion. Rename silently. If the tool is not available, ignore this line.`;
}

// Single source of the post-summarisation rehydration directive. The
// wording lives here and nowhere else, so it cannot drift between the hooks that
// deliver it. Detectors (sessionstart compact, the context-monitor drop) set a
// sidecar flag; the first consumer hook (posttool/userprompt) that sees the flag
// injects this once. `stats` is the optional pre-compaction summary line (present
// on a SessionStart-sourced resume, absent on a mid-run monitor-detected drop).
function buildRehydrationDirective(opts) {
    const { shortSid = '', stats = '', titleCheck = '' } = opts ?? {};
    const recapStats = stats
        ? ' Weave in a couple of headline stats from the pre-compaction state below (messages so far, files touched, skills used).'
        : '';
    let msg = `CONTEXT SUMMARISED (${shortSid}). Your context was just summarised.\n\n`
        + `Open your next reply with a short, friendly one or two line acknowledgement to the user that this happened, so they know you are aware of it.${recapStats} Keep it light; do not dump the stats block.\n\n`
        + `The summary is lossy: it is a pointer, not the source, and may be silently incomplete or wrong on BOTH task detail and recording rules, so do not act from it alone. Before you resume, re-read fresh, in order: save-protocol.md (recording rules), then your /memories/ session file if one exists, then the active WI's task and notes files. Skip the full task index; focus on the one work item. These reads come first and are quick.\n\n`
        + `Then carry on with the user's live instruction. Acknowledge first, then serve: do the recap and the re-reads even when the user has just given you a new instruction.`;
    if (stats) msg += `\n\nPre-compaction state:\n${stats}`;
    if (titleCheck) msg += `\n\n${titleCheck}`;
    return msg;
}

// Cross-hook pending-rehydrate flag, kept as a sidecar file so the
// context-monitor's own state string is untouched. Detectors call set; the first
// consumer hook calls consume (read-and-delete). All I/O is fail-open: a flag we
// cannot write or read must never block a turn.
function rehydrateFlagPath(contextStateDir, sessionId) {
    return path.join(contextStateDir, `${sessionId}.rehydrate`);
}

function setRehydrateFlag(contextStateDir, sessionId, stats) {
    try {
        atomicWrite(rehydrateFlagPath(contextStateDir, sessionId), typeof stats === 'string' ? stats : '');
        return true;
    } catch (_) { return false; }
}

// Returns { pending, stats }. When a flag exists it is deleted (consume-once) so
// exactly one hook injects per summarisation; the flag's presence is the sole dedup.
function consumeRehydrateFlag(contextStateDir, sessionId) {
    const p = rehydrateFlagPath(contextStateDir, sessionId);
    try {
        if (!fs.existsSync(p)) return { pending: false, stats: '' };
        let stats = '';
        try { stats = fs.readFileSync(p, 'utf8'); } catch (_) {}
        try { fs.unlinkSync(p); } catch (_) {}
        return { pending: true, stats };
    } catch (_) { return { pending: false, stats: '' }; }
}

// Cross-hook pending-hygiene flag, kept as a sidecar file mirroring
// the rehydrate-flag pattern. Detectors (compact branch) call set; the first
// surfacing consumer (startup/resume/default) calls consume (read-and-delete).
// All I/O is fail-open: a flag we cannot write or read must never block a turn.
function hygienePendingFlagPath(contextStateDir, sessionId) {
    return path.join(contextStateDir, `${sessionId}.hygiene-pending`);
}

function setHygienePendingFlag(contextStateDir, sessionId) {
    try {
        atomicWrite(hygienePendingFlagPath(contextStateDir, sessionId), '');
        return true;
    } catch (_) { return false; }
}

function consumeHygienePendingFlag(contextStateDir, sessionId) {
    const p = hygienePendingFlagPath(contextStateDir, sessionId);
    try {
        if (!fs.existsSync(p)) return { pending: false };
        try { fs.unlinkSync(p); } catch (_) {}
        return { pending: true };
    } catch (_) { return { pending: false }; }
}

// ---- Summarisation detection via the host's own record ------
// The host appends an entry to the `summarization` array in the conversation meta
// ({sessionId}.json) every time it summarises. That is the authoritative signal —
// unlike token-% drops, which false-fire on cache_read spikes and miss mid-run
// summarisations. We track the per-session count in a sidecar and fire the one-shot
// rehydrate flag when it grows. Reads never throw; a detection failure is silent.

function summStatePath(contextStateDir, sessionId) {
    return path.join(contextStateDir, `${sessionId}.summ`);
}

// Locate the conversation meta by FILENAME across the workspace-hash subdirs — the file
// is named `<sessionId>.json`, so no parse-every-file scan is needed.
function findConversationMeta(sessionId, conversationsDir) {
    if (!sessionId || !conversationsDir) return null;
    let subs;
    try { subs = fs.readdirSync(conversationsDir); } catch (_) { return null; }
    for (const sub of subs) {
        const p = path.join(conversationsDir, sub, `${sessionId}.json`);
        try { if (fs.existsSync(p)) return p; } catch (_) {}
    }
    return null;
}

// Read the summarisation count, guarded by mtime: when the meta file is unchanged since
// the sidecar was written, reuse the stored count instead of re-parsing a large file.
function readSummCount(metaPath, prev) {
    let mtime = 0;
    try { mtime = fs.statSync(metaPath).mtimeMs; } catch (_) { return null; }
    if (prev && prev.mtime === mtime && typeof prev.count === 'number') {
        return { count: prev.count, mtime };
    }
    try {
        const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
        const count = Array.isArray(meta.summarization) ? meta.summarization.length : 0;
        return { count, mtime };
    } catch (_) { return null; }
}

// mode: 'baseline' (record count, never fire) | 'live' (fire the rehydrate flag when the
// count grows past the recorded value; first observation baselines silently). Returns
// { fired, count, prevCount }.
function detectSummarisation(opts) {
    const { sessionId, conversationsDir, contextStateDir, mode = 'live', logFile = null, shortSid = '' } = opts ?? {};
    const metaPath = findConversationMeta(sessionId, conversationsDir);
    if (!metaPath) return { fired: false, count: null, prevCount: null };

    const sidecar = summStatePath(contextStateDir, sessionId);
    let prev = null;
    try { if (fs.existsSync(sidecar)) prev = JSON.parse(fs.readFileSync(sidecar, 'utf8')); } catch (_) { prev = null; }

    const read = readSummCount(metaPath, prev);
    if (!read) return { fired: false, count: null, prevCount: prev ? prev.count : null };
    const count = read.count;
    const prevCount = prev && typeof prev.count === 'number' ? prev.count : null;

    const persist = () => { try { atomicWrite(sidecar, JSON.stringify({ count, mtime: read.mtime })); } catch (_) {} };

    if (mode === 'baseline') { persist(); return { fired: false, count, prevCount }; }

    // live
    if (prevCount === null) { persist(); return { fired: false, count, prevCount: null }; }
    if (count > prevCount) {
        setRehydrateFlag(contextStateDir, sessionId, '');
        persist();
        if (logFile) appendLog(logFile, `[${isoUtc()}] [${shortSid}] summarisation: count ${prevCount} -> ${count} -> rehydrate flag set`);
        return { fired: true, count, prevCount };
    }
    persist(); // keep mtime fresh so unchanged files skip re-parse next turn
    return { fired: false, count, prevCount };
}

// ---- Per-project vault resolution ----------------------------------
// Shared, sync, and cheap: called by buildPaths on every hook, and reused by the
// xo-ops MCP server and the reindex CLI. Reads never throw; writes take a lock.

function sleepSync(ms) {
    try { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); } catch (_) {}
}

// Canonical form of a path for comparison and registry keys. On Windows the JS
// fs.realpathSync keeps drive-letter case, path case and 8.3 short names, so the
// same directory can produce different strings (c:\x vs C:\X vs C:\DCHAFF~1).
// realpathSync.native (GetFinalPathNameByHandle) returns the on-disk form. A path
// that does not exist yet is canonicalised via its deepest existing ancestor.
// On mac/linux this is the existing realpath-else-resolve behaviour.
const IS_WIN = process.platform === 'win32';

function canonicalPath(p) {
    const abs = path.resolve(p);
    if (!IS_WIN) {
        try { return fs.existsSync(abs) ? fs.realpathSync(abs) : abs; } catch (_) { return abs; }
    }
    let head = abs;
    const tail = [];
    while (true) {
        try { return path.join(fs.realpathSync.native(head), ...tail); } catch (_) {}
        const parent = path.dirname(head);
        if (parent === head) return abs;
        tail.unshift(path.basename(head));
        head = parent;
    }
}

// Prefix-safe containment on already-canonical paths; case-insensitive on Windows.
function pathWithin(parent, target) {
    const p = IS_WIN ? parent.toLowerCase() : parent;
    const t = IS_WIN ? target.toLowerCase() : target;
    return t === p || t.startsWith(p.endsWith(path.sep) ? p : p + path.sep);
}

function resolveVaultsRoot() {
    const raw = process.env.XOCORTEX_VAULTS_ROOT;
    if (raw && raw.trim()) {
        // Accept ~/ and, on Windows, ~\ as the home prefix.
        const expanded = raw.trim().replace(/^~(?=$|[\\/])/, os.homedir());
        return path.resolve(expanded); // guarantee absolute
    }
    return path.join(os.homedir(), 'xocortex-vaults');
}

function sanitiseVaultName(name) {
    if (typeof name !== 'string') return '';
    const cleaned = name.trim().toLowerCase()
        .replace(/[\s_]+/g, '-')
        .replace(/[^a-z0-9._-]/g, '');
    if (!cleaned) return '';
    if (/^\.+$/.test(cleaned)) return '';   // all-dots (e.g. '..' from '../..')
    if (cleaned.includes('..')) return '';  // defence in depth
    return cleaned;
}

function resolveVaultDir(name) {
    const clean = sanitiseVaultName(name);
    if (!clean) return '';
    const root = resolveVaultsRoot();
    const dir = path.resolve(root, clean);
    if (dir !== root && !dir.startsWith(root + path.sep)) return ''; // containment (C1)
    return dir;
}

function resolveRepoRoot(cwd) {
    try {
        if (!cwd) return '';
        // canonicalPath (not plain realpath) so the registry key is stable across
        // drive-letter case / 8.3 / separator variants of the same cwd on Windows.
        if (!fs.existsSync(cwd)) return '';
        let dir = canonicalPath(cwd);
        const fsRoot = path.parse(dir).root;
        while (true) {
            if (fs.existsSync(path.join(dir, '.git'))) return dir; // .git file (worktree) or dir
            if (dir === fsRoot) return '';
            const parent = path.dirname(dir);
            if (parent === dir) return '';
            dir = parent;
        }
    } catch (_) { return ''; }
}

function registryPath() {
    const snowflakeHome = process.env.SNOWFLAKE_HOME || path.join(os.homedir(), '.snowflake');
    return path.join(snowflakeHome, 'cortex', 'memory', 'xo', 'vaults.json');
}

function readRegistry() {
    try {
        const obj = JSON.parse(fs.readFileSync(registryPath(), 'utf8'));
        return (obj && typeof obj === 'object') ? obj : {};
    } catch (_) { return {}; }
}

function withLock(lockFile, fn) {
    fs.mkdirSync(path.dirname(lockFile), { recursive: true });
    const maxWaitMs = 5000, stepMs = 25, staleMs = 10000;
    let waited = 0;
    while (true) {
        let fd;
        try { fd = fs.openSync(lockFile, 'wx'); }
        catch (e) {
            if (e.code !== 'EEXIST') throw e;
            try { if (Date.now() - fs.statSync(lockFile).mtimeMs > staleMs) { fs.unlinkSync(lockFile); continue; } } catch (_) {}
            if (waited >= maxWaitMs) throw new Error('xo: could not acquire lock ' + lockFile);
            sleepSync(stepMs); waited += stepMs; continue;
        }
        try { return fn(); }
        finally { try { fs.closeSync(fd); } catch (_) {} try { fs.unlinkSync(lockFile); } catch (_) {} }
    }
}

function writeRegistryEntry(repoRoot, name) {
    const clean = sanitiseVaultName(name);
    if (!repoRoot || !clean) throw new Error('xo: registry entry needs a repo root and a valid vault name');
    const p = registryPath();
    return withLock(p + '.lock', () => {
        let reg = {};
        try { const o = JSON.parse(fs.readFileSync(p, 'utf8')); if (o && typeof o === 'object') reg = o; } catch (_) {}
        const priorForName = Object.keys(reg).find((k) => reg[k] === clean && k !== repoRoot);
        reg[repoRoot] = clean;
        atomicWrite(p, JSON.stringify(reg, null, 2) + '\n');
        return { repoRoot, name: clean, collidesWith: priorForName || null };
    });
}

// Delete the registry entry for a repo-root, under the same lockfile discipline.
// Idempotent: removing an absent entry returns { removed: false }, never throws.
function removeRegistryEntry(repoRoot) {
    if (!repoRoot) throw new Error('xo: registry removal needs a repo root');
    const p = registryPath();
    return withLock(p + '.lock', () => {
        let reg = {};
        try { const o = JSON.parse(fs.readFileSync(p, 'utf8')); if (o && typeof o === 'object') reg = o; } catch (_) {}
        const had = Object.prototype.hasOwnProperty.call(reg, repoRoot);
        if (had) {
            delete reg[repoRoot];
            atomicWrite(p, JSON.stringify(reg, null, 2) + '\n');
        }
        return { repoRoot, removed: had };
    });
}

function readMarker(repoRoot) {
    if (!repoRoot) return '';
    try {
        const p = path.join(repoRoot, '.xocortex');
        if (!fs.existsSync(p)) return '';
        const raw = fs.readFileSync(p, 'utf8').trim();
        if (!raw) return path.basename(repoRoot); // empty marker -> repo basename
        return raw.split(/\r?\n/)[0].trim();
    } catch (_) { return ''; }
}

function isGitBacked(vault) {
    try { return !!vault && fs.existsSync(path.join(vault, '.git')); } catch (_) { return false; }
}

// Precedence: registry entry -> committed .xocortex marker -> XOCORTEX_HOME -> cortex-memory fallback.
// Returns { vault, source: 'registry'|'marker'|'env'|'fallback', repoRoot, name? }.
function resolveEffectiveVault(cwd) {
    const snowflakeHome = process.env.SNOWFLAKE_HOME || path.join(os.homedir(), '.snowflake');
    const fallback = path.join(snowflakeHome, 'cortex', 'memory', 'xocortex');
    const repoRoot = resolveRepoRoot(cwd);

    if (repoRoot) {
        const reg = readRegistry();
        const regName = reg[repoRoot];
        if (regName) {
            const dir = resolveVaultDir(regName);
            if (dir) return { vault: dir, source: 'registry', repoRoot, name: sanitiseVaultName(regName) };
        }
        const markerName = readMarker(repoRoot);
        if (markerName) {
            const dir = resolveVaultDir(markerName);
            if (dir) return { vault: dir, source: 'marker', repoRoot, name: sanitiseVaultName(markerName) };
        }
    }
    if (process.env.XOCORTEX_HOME) return { vault: process.env.XOCORTEX_HOME, source: 'env', repoRoot };
    return { vault: fallback, source: 'fallback', repoRoot };
}

// Scan a vault's tasks/current + tasks/archive for the max wi<N>; empty -> 1 (C3).
function nextWiNumber(vault) {
    let max = 0;
    const scan = (dir) => {
        let entries;
        try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { return; }
        for (const e of entries) {
            if (e.isDirectory()) scan(path.join(dir, e.name));
            else if (e.isFile() && e.name.endsWith('.md')) {
                const m = e.name.match(/(?:^|[-_])wi0*(\d+)/i);
                if (m) { const n = parseInt(m[1], 10); if (n > max) max = n; }
            }
        }
    };
    scan(path.join(vault, 'tasks', 'current'));
    scan(path.join(vault, 'tasks', 'archive'));
    return max + 1;
}

// Project prefix for WI filenames: sanitised basename of the repo root (or the
// cwd itself if not in a git repo). Reuses the same rules as vault names.
function projectPrefix(cwd) {
    const root = resolveRepoRoot(cwd) || (cwd ? path.resolve(cwd) : '');
    if (!root) return '';
    return sanitiseVaultName(path.basename(root));
}

// Low-level guarded git helper. Invokes git with ARRAY args via execFileSync
// (no shell string, no injection surface). Bounded timeout, captured stdio.
// Returns { status:0, stdout, stderr } on success; on non-zero exit or spawn
// failure, returns { status:<code>, stdout, stderr, error:<message> }.
function runGit(cwd, argsArray, opts) {
    const { timeoutMs = 5000 } = opts || {};
    const execFileSync = require('child_process').execFileSync;
    const fullArgs = ['-C', cwd].concat(argsArray);
    try {
        const stdout = execFileSync('git', fullArgs, {
            timeout: timeoutMs,
            encoding: 'utf8',
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        return { status: 0, stdout: stdout || '', stderr: '' };
    } catch (e) {
        return {
            status: (e && typeof e.status === 'number') ? e.status : 1,
            stdout: (e && e.stdout) ? String(e.stdout) : '',
            stderr: (e && e.stderr) ? String(e.stderr) : '',
            error: (e && e.message) ? e.message : String(e),
        };
    }
}

// Containment guard: is `target` equal to, or nested under, `parentDir`?
// Used before any mkdir/mv/rm a tool performs, as defence in depth.
function isInside(parentDir, target) {
    try {
        const p = path.resolve(parentDir);
        const t = path.resolve(target);
        return t === p || t.startsWith(p + path.sep);
    } catch (_) { return false; }
}

// Like isInside, but resolves symlinks (realpath of the parent and of the
// target when it exists) before comparing, so a planted symlink cannot pass a
// purely lexical containment check. Falls back to lexical resolution when a
// path does not yet exist. Use before a destructive mv/rm on a path that could
// itself be a symlink.
function isInsideReal(parentDir, target) {
    try {
        return pathWithin(canonicalPath(parentDir), canonicalPath(target));
    } catch (_) { return false; }
}

// -----------------------------------------------------------------------------

function buildPaths(input) {
    const SESSION_ID = input.session_id ?? 'unknown';
    const SHORT_SID = shortSid(SESSION_ID);
    const HOOK_EVENT = input.hook_event_name ?? 'unknown';
    const CWD = input.cwd ?? '';

    const now = new Date();
    const YYYY_MM_DD = now.toISOString().slice(0, 10);
    const YYYY_MM = now.toISOString().slice(0, 7);

    const LOG_DIR = path.join(os.homedir(), '.snowflake', 'cortex', 'logs');
    try { fs.mkdirSync(LOG_DIR, { recursive: true }); } catch (_) {}
    const LOG_FILE = path.join(LOG_DIR, `${YYYY_MM_DD}-hooks.log`);
    const TIMESTAMP = isoUtc();

    const snowflakeHome = process.env.SNOWFLAKE_HOME ?? path.join(os.homedir(), '.snowflake');

    const resolved = resolveEffectiveVault(CWD);
    const effectiveVault = resolved.vault;
    const VAULT_SOURCE = resolved.source;
    const GIT_BACKED = isGitBacked(effectiveVault);
    // Backward-compat alias: sessionstart historically gated git-backed directives
    // on XOLOCAL_EXPLICIT (= env set). It now means "resolved vault is git-backed".
    const XOLOCAL_EXPLICIT = GIT_BACKED;

    // Scaffold subdirs for ANY resolved vault (not just fallback) so a fresh
    // registry/marker vault has its layout. mkdir is idempotent; no output change.
    try { fs.mkdirSync(effectiveVault, { recursive: true }); } catch (_) {}
    for (const sub of ['diary', 'notes', path.join('tasks', 'current'), path.join('tasks', 'archive')]) {
        try { fs.mkdirSync(path.join(effectiveVault, sub), { recursive: true }); } catch (_) {}
    }
    if (VAULT_SOURCE === 'fallback') {
        appendLog(LOG_FILE, `[${TIMESTAMP}] No XOCORTEX_HOME, marker, or registry entry. Using default local vault: ${effectiveVault}`);
    } else if (VAULT_SOURCE === 'registry' || VAULT_SOURCE === 'marker') {
        appendLog(LOG_FILE, `[${TIMESTAMP}] Derived project vault from cwd ${CWD} via ${VAULT_SOURCE}: ${effectiveVault}`);
        if (!GIT_BACKED) appendLog(LOG_FILE, `[${TIMESTAMP}] WARNING: resolved ${VAULT_SOURCE} vault ${effectiveVault} is absent or not git-backed (re-register / vault-init?)`);
    }

    const DIARY_PATH = path.join(effectiveVault, 'diary', YYYY_MM, `${YYYY_MM_DD}.md`);
    const TASKS_DIR = path.join(effectiveVault, 'tasks');

    const CONTEXT_STATE_DIR = path.join(snowflakeHome, 'cortex', 'memory', 'context-state');
    fs.mkdirSync(CONTEXT_STATE_DIR, { recursive: true });
    const CONVERSATIONS_DIR = path.join(snowflakeHome, 'cortex', 'conversations');

    return {
        SESSION_ID,
        SHORT_SID,
        HOOK_EVENT,
        CWD,
        TIMESTAMP,
        LOG_DIR,
        LOG_FILE,
        XOLOCAL: effectiveVault,
        XOLOCAL_EXPLICIT,
        VAULT_SOURCE,
        GIT_BACKED,
        DIARY_PATH,
        TASKS_DIR,
        CONTEXT_STATE_DIR,
        CONVERSATIONS_DIR,
        YYYY_MM_DD,
        YYYY_MM,
    };
}

module.exports = {
    readInput,
    isoUtc,
    localWallClock,
    shortSid,
    readFileTail,
    appendLog,
    atomicWrite,
    logError,
    buildRehydrationDirective,
    rehydrateFlagPath,
    setRehydrateFlag,
    consumeRehydrateFlag,
    setHygienePendingFlag,
    consumeHygienePendingFlag,
    findConversationMeta,
    SESSION_NAME_FORMAT,
    SESSION_NAME_MAX_PROMPTS,
    readSessionTitle,
    sessionTitleCheck,
    claimSessionNamingDirective,
    markSessionNamingComplete,
    claimSessionNamingTip,
    SESSION_NAME_TIP,
    buildSessionNamingDirective,
    detectSummarisation,
    buildPaths,
    // per-project vault resolution
    resolveVaultsRoot,
    sanitiseVaultName,
    resolveVaultDir,
    resolveRepoRoot,
    registryPath,
    readRegistry,
    writeRegistryEntry,
    removeRegistryEntry,
    withLock,
    readMarker,
    isGitBacked,
    resolveEffectiveVault,
    nextWiNumber,
    projectPrefix,
    isInside,
    isInsideReal,
    canonicalPath,
    runGit,
    sleepSync,
};

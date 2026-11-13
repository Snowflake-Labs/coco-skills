#!/usr/bin/env node
'use strict';
// xo-ops — bundled no-install MCP server for XO deterministic operations.
// stdio JSON-RPC 2.0 (newline-delimited). Zero dependencies; reuses hooks/_common.js.
// Tools: allocate_wi, resolve_paths, append_diary, register_vault,
//        clear_scratch, promote_artifact, find_wi, archive_wi, commit_wi, read_diary.
const fs = require('fs');
const path = require('path');
const readline = require('readline');
const C = require('../hooks/_common.js');

const SERVER = { name: 'xo-ops', version: '0.1.0' };

function send(msg) { process.stdout.write(JSON.stringify(msg) + '\n'); }
function result(id, res) { send({ jsonrpc: '2.0', id, result: res }); }
function rpcError(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }
function textResult(id, obj) {
    const text = (typeof obj === 'string') ? obj : JSON.stringify(obj, null, 2);
    result(id, { content: [{ type: 'text', text }] });
}

// Resolve the effective vault from an explicit `vault` override, else from `cwd`
// (recommended: the agent supplies its working directory), else the server's cwd.
function effectiveVault(args) {
    if (args && typeof args.vault === 'string' && args.vault.trim()) return args.vault.trim();
    const cwd = (args && args.cwd) ? String(args.cwd) : process.cwd();
    return C.resolveEffectiveVault(cwd).vault;
}

// Move a file, tolerating a cross-device boundary (EXDEV) between the vault
// scratch and its notes/archive (e.g. a symlinked vaults root on another mount).
function moveFile(from, to) {
    try { fs.renameSync(from, to); }
    catch (e) {
        if (e && e.code === 'EXDEV') { fs.copyFileSync(from, to); fs.unlinkSync(from); }
        else throw e;
    }
}

const TOOLS = [
    {
        name: 'allocate_wi',
        description: 'Allocate the next work-item number for the effective vault (per-vault sequence; scans tasks/current + tasks/archive for the max, returns max+1, empty vault -> 1). Pass cwd (recommended) or an explicit vault path. The number is a snapshot, not a reservation: call this in the same turn you create the wi{N} task file, write the file before yielding, and never carry a number across turns or a user reply. If turns have elapsed, call again and use the new number.',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, vault: { type: 'string' } } },
    },
    {
        name: 'resolve_paths',
        description: 'Resolve the effective vault and its paths for a cwd (or explicit vault). Returns vault, diary_month_dir, notes, notes_month_dir, tasks_current, tasks_archive, scratch. Pass wi_number for the per-WI scratch dir (tmp/wi{N}). Pass BOTH cwd AND wi_number to additionally get project, filename_stem ("{project}-wi{N}-"), and notes_path_stem / task_path_stem — append "<slug>.md" to build the file path (do NOT hand-construct these). Pass create:true to create the scratch dir (contained under the vault). project = the sanitised repo-basename prefix.',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, vault: { type: 'string' }, wi_number: { type: 'integer' }, create: { type: 'boolean' } } },
    },
    {
        name: 'append_diary',
        description: 'Append a SHORT agent-authored savepoint entry (5-10 lines) to the dated diary file of the effective vault (diary/{YYYY-MM}/{YYYY-MM-DD}.md, month dir created if absent). The diary is a milestone recovery INDEX, not a running log: one entry per savepoint/milestone, not one per step. Deep step-by-step detail — reasoning, evidence, decisions — belongs in the WI notes file (write it there with Write/Edit), NOT here. The MCP places the entry; you author its (brief) text.',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, vault: { type: 'string' }, entry: { type: 'string' }, date: { type: 'string' } }, required: ['entry'] },
    },
    {
        name: 'register_vault',
        description: 'Manage the machine-local registry entry (~/.snowflake/cortex/memory/xo/vaults.json) that maps the current repo (by resolved repo-root) to a project vault name. action "set" (default) registers/updates the entry (name defaults to the repo basename); action "remove" deletes the entry (name ignored), after which resolution falls back to any .xocortex marker / XOCORTEX_HOME / fallback. Never touches the marker or the vault directory. Idempotent; lockfile-guarded.',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, name: { type: 'string' }, action: { type: 'string', enum: ['set', 'remove'] } } },
    },
    {
        name: 'clear_scratch',
        description: 'Delete a WI scratch dir (<vault>/tmp/wi{N}) — the FINAL Cleanup step, never the first. Containment-checked: cannot delete outside <vault>/tmp. It does NOT inventory scratch and does NOT deregister git worktrees: if a git worktree exists under the scratch it REFUSES and tells you to run `git worktree remove` first. Precondition: you have already inventoried scratch, removed any worktrees, and promoted anything worth keeping. Pass cwd (or vault) and wi_number.',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, vault: { type: 'string' }, wi_number: { type: 'integer' } }, required: ['wi_number'] },
    },
    {
        name: 'promote_artifact',
        description: 'Move a scratch artifact you have chosen to keep into the vault notes at the canonical path <vault>/notes/{YYYY-MM}/{project}-wi{N}-<slug>.md. Pass cwd (for the {project} prefix), wi_number, from (a path inside <vault>/tmp), and slug. It builds the destination and moves the file; it does NOT decide what is worth keeping (your judgement) and does NOT author content.',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, vault: { type: 'string' }, wi_number: { type: 'integer' }, from: { type: 'string' }, slug: { type: 'string' } }, required: ['wi_number', 'from', 'slug'] },
    },
    {
        name: 'find_wi',
        description: 'Locate a work item files by number across the effective vault. Returns task_path (first current-or-archive match), task_paths[], notes_paths[], and archived (bool). Matches zero-padded numbers (wi5 == wi05) and does not confuse wi5 with wi50. Read-only lookup by known identity. Pass cwd (or vault) and wi_number.',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, vault: { type: 'string' }, wi_number: { type: 'integer' } }, required: ['wi_number'] },
    },
    {
        name: 'archive_wi',
        description: 'Move a work item task file from tasks/current to tasks/archive/{YYYY-MM}/ (current month, dir created if absent). This is the atomic MOVE only — it does NOT mark the WI done or edit any task content. Precondition: you have ALREADY updated the task file (Status->Done, StatusNote->completion summary, Priority->Archived) BEFORE calling. Pass cwd (or vault) and wi_number. archive_wi only MOVES the file; afterward call commit_wi with this WI so the tasks/current deletion is committed together with the archived copy (otherwise HEAD keeps the stale current copy).',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, vault: { type: 'string' }, wi_number: { type: 'integer' } }, required: ['wi_number'] },
    },
    {
        name: 'commit_wi',
        description: 'Commit the current session\'s vault journal files (diary, task, notes) via a scoped git pathspec. Commit-only (never pushes). Inputs: cwd/vault for vault resolution; wi_number (single WI) and/or wi_numbers (array, multi-WI); session_id for the commit message short-sid. A no-WI call (neither wi_number nor wi_numbers) commits today\'s diary only. Pathspec = today\'s diary file + each WI\'s task + notes files (located by the same zero-padded regex find_wi uses), plus the tasks/current deletion left by archive_wi for an in-scope WI (staged only when it is a direct child of tasks/current and its tasks/archive counterpart is untracked or byte-identical to the deleted file, so an accidental delete or a diverged reopened WI is left alone). Returns { sha, files, branch } on success; { sha:null, files:[], reason:"nothing_to_commit" } when nothing matched; { error:<code>, detail } on failure. Error codes: vault_not_git, git_unavailable, detached_head, unborn_branch, identity_unset, index_locked, path_escapes_vault, precommit_failed, malformed_task_file. Commit message format: "xo(wi{N}): savepoint <UTC-ISO> [<short-sid>]" (single WI), "xo(vault): savepoint <UTC-ISO> [<short-sid>]" (no WI). Multi-WI uses the lowest WI number in the wi{N} slot.',
        inputSchema: {
            type: 'object',
            properties: {
                cwd: { type: 'string' },
                vault: { type: 'string' },
                wi_number: { type: 'integer' },
                wi_numbers: { type: 'array', items: { type: 'integer' } },
                session_id: { type: 'string' },
            },
        },
    },
    {
        name: 'read_diary',
        description: 'Read the effective vault diary. Default (no filters): returns the whole current day (diary/{YYYY-MM}/{today}.md). Optional filters narrow to a verbatim slice: date (YYYY-MM-DD; the window end when days is set), session_id, wi, last_n, terms (array; entries containing ANY term as a whole token, case-insensitive literal, so WI-624 does not match WI-6240), exclude_session (drop entries whose heading carries that session id), days (1-31; read that many UTC calendar days ending at date, oldest first, missing days skipped; returns paths[] instead of path), since (YYYY-MM-DD, inclusive; the window runs from since to date, clamped to the days lookback, default 14; a since after date clamps to date so a local-dated bound cannot error; returns window_start). All filters must match (AND): put the WI number in terms as WI-N together with the other identifiers, not in wi, or an entry must satisfy both. Use terms + exclude_session (your short 8-character session id, as in your diary headings) + since (the task\'s Last checked date) for the milestone awareness check (other sessions\' entries naming this WI, related WIs or bound resources). Day files over 1 MB are read from the tail and flagged truncated. Deterministic retrieval by known identity — returns verbatim entries, never composes. For meaning/term search across the vault use Recall instead, not this. Pass cwd (or vault).',
        inputSchema: { type: 'object', properties: { cwd: { type: 'string' }, vault: { type: 'string' }, date: { type: 'string' }, session_id: { type: 'string' }, wi: { type: 'integer' }, last_n: { type: 'integer' }, terms: { type: 'array', items: { type: 'string' } }, exclude_session: { type: 'string' }, days: { type: 'integer', minimum: 1, maximum: 31 }, since: { type: 'string' } } },
    },
];

// Best-effort unstage after a failed commit, so a failed session never leaves
// its files staged in the shared index (prevents cross-session leaks).
// Returns a warning string if the reset itself fails; null otherwise.
function resetAfterFailedCommit(vault, pathspec) {
    try {
        const r = C.runGit(vault, ['reset', 'HEAD', '--'].concat(pathspec));
        if (r.status !== 0) return 'unstage-cleanup also failed: ' + (r.stderr || r.error || '').slice(0, 200);
    } catch (e) { return 'unstage-cleanup threw: ' + ((e && e.message) || String(e)).slice(0, 200); }
    return null;
}

// Is an executable pre-commit hook present for this repo? A silent `exit 1` hook
// gives git no distinctive output, so a non-zero commit is classified as a hook
// failure by hook PRESENCE (honouring core.hooksPath), not by matching text.
function hasPrecommitHook(vault) {
    try {
        let hooksDir = path.join(vault, '.git', 'hooks');
        const cfg = C.runGit(vault, ['config', '--get', 'core.hooksPath'], { timeoutMs: 3000 });
        if (cfg && cfg.status === 0 && cfg.stdout && cfg.stdout.trim()) {
            const hp = cfg.stdout.trim();
            hooksDir = path.isAbsolute(hp) ? hp : path.join(vault, hp);
        }
        const st = fs.statSync(path.join(hooksDir, 'pre-commit'));
        // Git for Windows runs hooks via sh regardless of mode, and Node reports no
        // exec bits on win32, so presence alone counts there.
        return st.isFile() && (process.platform === 'win32' || (st.mode & 0o111) !== 0);
    } catch (_) { return false; }
}

function callTool(name, args) {
    args = args || {};
    if (name === 'allocate_wi') {
        const vault = effectiveVault(args);
        return { wi: C.nextWiNumber(vault), vault };
    }
    if (name === 'resolve_paths') {
        const vault = effectiveVault(args);
        const ym = new Date().toISOString().slice(0, 7);
        const wi = (args.wi_number != null) ? parseInt(args.wi_number, 10) : null;
        const scratch = (wi != null)
            ? path.join(vault, 'tmp', 'wi' + wi)
            : path.join(vault, 'tmp');
        if (args.create) {
            if (!C.isInside(vault, scratch)) throw new Error('resolve_paths: refusing to create a scratch path outside the vault');
            fs.mkdirSync(scratch, { recursive: true });
        }
        const out = {
            vault,
            diary_month_dir: path.join(vault, 'diary', ym),
            notes: path.join(vault, 'notes'),
            notes_month_dir: path.join(vault, 'notes', ym),
            tasks_current: path.join(vault, 'tasks', 'current'),
            tasks_archive: path.join(vault, 'tasks', 'archive'),
            scratch,
        };
        // Project prefix + canonical filename stem (agent appends "<slug>.md").
        const project = args.cwd ? C.projectPrefix(String(args.cwd)) : '';
        if (project) {
            out.project = project;
            if (wi != null) {
                out.filename_stem = project + '-wi' + wi + '-';
                out.notes_path_stem = path.join(vault, 'notes', ym, project + '-wi' + wi + '-');
                out.task_path_stem = path.join(vault, 'tasks', 'current', project + '-wi' + wi + '-');
            }
        }
        return out;
    }
    if (name === 'append_diary') {
        if (typeof args.entry !== 'string' || !args.entry) throw new Error('append_diary requires a non-empty "entry"');
        const vault = effectiveVault(args);
        const date = (typeof args.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(args.date))
            ? args.date : new Date().toISOString().slice(0, 10);
        const ym = date.slice(0, 7);
        const dir = path.join(vault, 'diary', ym);
        fs.mkdirSync(dir, { recursive: true });
        const file = path.join(dir, date + '.md');
        let prefix = '';
        try { const s = fs.readFileSync(file, 'utf8'); if (s.length && !s.endsWith('\n')) prefix = '\n'; } catch (_) {}
        let body = args.entry; if (!body.endsWith('\n')) body += '\n';
        fs.appendFileSync(file, prefix + body);
        const out = { appended: true, path: file };
        // Soft length guard (non-blocking): the diary is a milestone index (cap ~5-10 lines),
        // not a running log. Over-long entries are a known drift under deep work — append as-is
        // but flag it so the agent routes deep detail (files changed, reasoning, evidence) to the
        // WI notes file instead. Never reject: a recorded-but-long savepoint beats a lost one.
        const lineCount = args.entry.replace(/\n+$/, '').split('\n').length;
        if (lineCount > 12) {
            out.warning = `Diary entry is ${lineCount} lines; the diary is a milestone index (cap ~5-10 lines). Appended as-is, but deep detail (files-changed lists, reasoning, evidence) belongs in the WI notes file, not the diary — trim this to a summary and move the detail to notes/{YYYY-MM}/.`;
        }
        return out;
    }
    if (name === 'register_vault') {
        const action = (typeof args.action === 'string' && args.action.trim()) ? args.action.trim() : 'set';
        if (action !== 'set' && action !== 'remove') throw new Error('register_vault: action must be "set" or "remove" (got "' + action + '")');
        const cwd = (args.cwd) ? String(args.cwd) : process.cwd();
        const repoRoot = C.resolveRepoRoot(cwd);
        if (!repoRoot) throw new Error('register_vault: cwd is not inside a git repo (no .git walking up from ' + cwd + ')');
        if (action === 'remove') {
            const res = C.removeRegistryEntry(repoRoot);
            return { repoRoot: res.repoRoot, action: 'remove', removed: res.removed };
        }
        const name = (typeof args.name === 'string' && args.name.trim()) ? args.name.trim() : path.basename(repoRoot);
        const res = C.writeRegistryEntry(repoRoot, name);
        const out = { repoRoot: res.repoRoot, action: 'set', name: res.name, vault: C.resolveVaultDir(res.name) };
        if (res.collidesWith) {
            out.warning = 'vault name "' + res.name + '" is already registered to a different repo-root (' + res.collidesWith + '); both will share one vault. Pass an explicit unique name to avoid comingling.';
        }
        return out;
    }
    if (name === 'clear_scratch') {
        if (args.wi_number == null) throw new Error('clear_scratch requires wi_number');
        const vault = effectiveVault(args);
        const wi = parseInt(args.wi_number, 10);
        const tmpRoot = path.join(vault, 'tmp');
        const target = path.join(tmpRoot, 'wi' + wi);
        if (!C.isInsideReal(tmpRoot, target) || target === tmpRoot) throw new Error('clear_scratch: refusing to delete outside <vault>/tmp');
        // Refuse if a git worktree lives under the scratch — caller must deregister it first.
        try {
            for (const e of fs.readdirSync(target, { withFileTypes: true })) {
                if (e.isDirectory() && fs.existsSync(path.join(target, e.name, '.git'))) {
                    throw new Error('clear_scratch: a git worktree exists under ' + target + ' — run `git worktree remove` to deregister it first, then retry');
                }
            }
        } catch (e) { if (e && /worktree exists/.test(e.message)) throw e; /* else: absent dir, nothing to clear */ }
        let removed = false;
        try { fs.rmSync(target, { recursive: true, force: true }); removed = true; } catch (_) {}
        return { cleared: removed, path: target };
    }
    if (name === 'promote_artifact') {
        if (args.wi_number == null || !args.from || !args.slug) throw new Error('promote_artifact requires wi_number, from, slug');
        const vault = effectiveVault(args);
        const wi = parseInt(args.wi_number, 10);
        const project = args.cwd ? C.projectPrefix(String(args.cwd)) : '';
        if (!project) throw new Error('promote_artifact: pass cwd so the {project} prefix can be derived');
        const from = path.resolve(String(args.from));
        if (!C.isInsideReal(path.join(vault, 'tmp'), from)) throw new Error('promote_artifact: `from` must be inside the vault scratch (<vault>/tmp/...)');
        if (!fs.existsSync(from)) throw new Error('promote_artifact: source not found: ' + from);
        const slug = C.sanitiseVaultName(String(args.slug)) || 'artifact';
        const ym = new Date().toISOString().slice(0, 7);
        const destDir = path.join(vault, 'notes', ym);
        const dest = path.join(destDir, project + '-wi' + wi + '-' + slug + '.md');
        fs.mkdirSync(destDir, { recursive: true });
        moveFile(from, dest);
        return { promoted: true, from, to: dest };
    }
    if (name === 'find_wi') {
        if (args.wi_number == null) throw new Error('find_wi requires wi_number');
        const vault = effectiveVault(args);
        const wi = parseInt(args.wi_number, 10);
        const re = new RegExp('(?:^|[-_])wi0*' + wi + '(?![0-9])', 'i');
        const scan = (dir) => {
            const out = [];
            const walk = (d) => {
                let entries; try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch (_) { return; }
                for (const e of entries) {
                    const full = path.join(d, e.name);
                    if (e.isDirectory()) walk(full);
                    else if (e.isFile() && e.name.endsWith('.md') && re.test(e.name)) out.push(full);
                }
            };
            walk(dir);
            return out;
        };
        const currentTasks = scan(path.join(vault, 'tasks', 'current'));
        const archivedTasks = scan(path.join(vault, 'tasks', 'archive'));
        const taskPaths = currentTasks.concat(archivedTasks);
        return {
            wi,
            task_path: taskPaths[0] || null,
            task_paths: taskPaths,
            notes_paths: scan(path.join(vault, 'notes')),
            archived: currentTasks.length === 0 && archivedTasks.length > 0,
        };
    }
    if (name === 'archive_wi') {
        if (args.wi_number == null) throw new Error('archive_wi requires wi_number');
        const vault = effectiveVault(args);
        const wi = parseInt(args.wi_number, 10);
        const re = new RegExp('(?:^|[-_])wi0*' + wi + '(?![0-9]).*\\.md$', 'i');
        const currentDir = path.join(vault, 'tasks', 'current');
        let entries; try { entries = fs.readdirSync(currentDir); } catch (_) { entries = []; }
        const found = entries.filter((n) => re.test(n));
        if (!found.length) throw new Error('archive_wi: no tasks/current file for wi' + wi + ' (already archived, or wrong vault?)');
        const ym = new Date().toISOString().slice(0, 7);
        const destDir = path.join(vault, 'tasks', 'archive', ym);
        if (!C.isInsideReal(vault, destDir)) throw new Error('archive_wi: refusing to write outside the vault');
        fs.mkdirSync(destDir, { recursive: true });
        const moved = [];
        for (const n of found) {
            const src = path.join(currentDir, n);
            if (!C.isInsideReal(currentDir, src)) throw new Error('archive_wi: refusing to move a task file that resolves outside the vault');
            const dest = path.join(destDir, n);
            moveFile(src, dest);
            moved.push(dest);
        }
        return { archived: true, moved };
    }
    if (name === 'commit_wi') {
        // Validate wi_numbers is an array if provided.
        if (args.wi_numbers != null && !Array.isArray(args.wi_numbers)) {
            throw new Error('commit_wi: wi_numbers must be an array of integers');
        }
        // Validate wi identifiers are finite integers (programmer error → throw).
        if (args.wi_number != null) {
            const n = parseInt(args.wi_number, 10);
            if (!Number.isFinite(n)) throw new Error('commit_wi: wi_number must be a finite integer');
        }
        if (Array.isArray(args.wi_numbers)) {
            for (const el of args.wi_numbers) {
                const n = parseInt(el, 10);
                if (!Number.isFinite(n)) throw new Error('commit_wi: every element of wi_numbers must be a finite integer');
            }
        }
        const vault = effectiveVault(args);

        // Vault must be git-backed.
        if (!fs.existsSync(path.join(vault, '.git'))) {
            return { error: 'vault_not_git', detail: 'No .git directory in vault: ' + vault };
        }

        // Check git is available.
        const versionCheck = C.runGit(vault, ['--version']);
        if (versionCheck.status !== 0) {
            return { error: 'git_unavailable', detail: (versionCheck.error || versionCheck.stderr || 'git --version failed').slice(0, 500) };
        }

        // Check for detached HEAD / unborn branch.
        const headCheck = C.runGit(vault, ['symbolic-ref', '--short', 'HEAD']);
        let branch = '';
        if (headCheck.status !== 0) {
            return { error: 'detached_head', detail: 'HEAD is detached' };
        }
        branch = headCheck.stdout.trim();
        // symbolic-ref succeeds on unborn branches (fresh git init, no commits) —
        // detect that with rev-parse which fails when HEAD has no target commit.
        const headVerify = C.runGit(vault, ['rev-parse', '--verify', 'HEAD']);
        if (headVerify.status !== 0) {
            return { error: 'unborn_branch', detail: 'HEAD points to an unborn branch (no commits yet)' };
        }

        // Check committer identity.
        const nameCheck = C.runGit(vault, ['config', 'user.name']);
        const emailCheck = C.runGit(vault, ['config', 'user.email']);
        if (!nameCheck.stdout.trim() || !emailCheck.stdout.trim()) {
            return { error: 'identity_unset', detail: 'git user.name or user.email is not configured' };
        }

        // Derive the pathspec — today's diary + WI task/notes files.
        const now = new Date();
        const dateStr = now.toISOString().slice(0, 10);
        const ym = dateStr.slice(0, 7);
        const diaryFile = path.join(vault, 'diary', ym, dateStr + '.md');

        // Collect WI numbers from both wi_number and wi_numbers.
        const wiSet = new Set();
        if (args.wi_number != null) wiSet.add(parseInt(args.wi_number, 10));
        if (Array.isArray(args.wi_numbers)) {
            for (const n of args.wi_numbers) wiSet.add(parseInt(n, 10));
        }

        // Scan for WI files using the same regex pattern as find_wi.
        const missingWiPaths = [];
        const wiPaths = [];
        const archivedByName = new Map(); // basename -> [in-scope archive full paths]
        const scan = (dir, re) => {
            const out = [];
            const walk = (d) => {
                let entries; try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch (_) { return; }
                for (const e of entries) {
                    const full = path.join(d, e.name);
                    if (e.isDirectory()) walk(full);
                    else if (e.isFile() && e.name.endsWith('.md') && re.test(e.name)) out.push(full);
                }
            };
            walk(dir);
            return out;
        };
        for (const wi of wiSet) {
            const re = new RegExp('(?:^|[-_])wi0*' + wi + '(?![0-9])', 'i');
            const archivedFiles = scan(path.join(vault, 'tasks', 'archive'), re);
            for (const f of archivedFiles) {
                const b = path.basename(f);
                if (!archivedByName.has(b)) archivedByName.set(b, []);
                archivedByName.get(b).push(f);
            }
            const taskFiles = scan(path.join(vault, 'tasks', 'current'), re).concat(archivedFiles);
            const notesFiles = scan(path.join(vault, 'notes'), re);
            if (taskFiles.length === 0 && notesFiles.length === 0) {
                missingWiPaths.push('wi' + wi + ': no task or notes files found');
            }
            wiPaths.push(...taskFiles, ...notesFiles);
        }

        // Build the full pathspec: diary + WI files, filtered to existing + contained.
        const candidatePaths = [diaryFile].concat(wiPaths);
        const pathspec = [];
        const skippedIgnored = [];

        for (const p of candidatePaths) {
            if (!fs.existsSync(p)) continue;
            // Symlink-aware containment: resolve the real path so a symlink
            // inside the vault pointing outside cannot be staged.
            let realP;
            try { realP = fs.realpathSync(p); } catch (_) { continue; }
            if (!C.isInside(vault, realP)) {
                return { error: 'path_escapes_vault', detail: 'Path escapes vault (after symlink resolution): ' + p + ' -> ' + realP };
            }
            // Also keep the lexical check (defence in depth).
            if (!C.isInside(vault, p)) {
                return { error: 'path_escapes_vault', detail: 'Path escapes vault: ' + p };
            }
            // Check if path is gitignored — skip it silently.
            const ignoreCheck = C.runGit(vault, ['check-ignore', '-q', p]);
            if (ignoreCheck.status === 0) {
                skippedIgnored.push(p);
                continue;
            }
            pathspec.push(p);
        }

        // Tracked task files that archive_wi moved out of tasks/current. They fail
        // the existsSync filter above, so their deletion is staged here; otherwise
        // HEAD keeps the stale current copy and a later checkout/stash/rebase can
        // resurrect it (the observed "archived WI reappears in tasks/current").
        //
        // Guard against committing an unintended delete. archive_wi only ever moves
        // DIRECT CHILDREN of tasks/current and copies the file verbatim, so a real
        // move leaves an in-scope tasks/archive counterpart that is either still
        // untracked (a fresh move) or, once committed, byte-identical to the deleted
        // file. We stage a tasks/current deletion only when it is a direct child AND
        // an in-scope archive counterpart is untracked OR byte-identical to the
        // deleted file's HEAD blob. A current copy that has DIVERGED from the archive
        // copy is a genuinely reopened WI with live edits and is left alone, as is a
        // delete with no in-scope archive counterpart.
        // If ls-files fails, fall back to the old behaviour (deletion unstaged).
        const deleted = C.runGit(vault, ['ls-files', '--deleted', '-z', '--', 'tasks/current']);
        if (deleted.status === 0) {
            // In-scope archive files still untracked == fresh archive_wi moves.
            const untrackedArchive = new Set();
            const others = C.runGit(vault, ['ls-files', '--others', '--exclude-standard', '-z', '--', 'tasks/archive']);
            if (others.status === 0) {
                for (const rel of others.stdout.split('\0')) { if (rel) untrackedArchive.add(rel); }
            }
            const toRel = (abs) => path.relative(vault, abs).split(path.sep).join('/');
            for (const rel of deleted.stdout.split('\0')) {
                if (!rel) continue;
                // archive_wi only vacates direct children of tasks/current.
                if (path.posix.dirname(rel) !== 'tasks/current') continue;
                const candidates = archivedByName.get(path.basename(rel));
                if (!candidates || candidates.length === 0) continue;
                let keep = candidates.some((abs) => untrackedArchive.has(toRel(abs)));
                if (!keep) {
                    // Counterpart is tracked: stage only if the deleted file is a
                    // byte-identical duplicate of what is already archived (a stale
                    // reappearance), not a diverged reopen.
                    const head = C.runGit(vault, ['show', 'HEAD:' + rel]);
                    if (head.status === 0) {
                        for (const abs of candidates) {
                            let archived;
                            try { archived = fs.readFileSync(abs, 'utf8'); } catch (_) { continue; }
                            if (archived === head.stdout) { keep = true; break; }
                        }
                    }
                }
                if (!keep) continue;
                const p = path.join(vault, rel);
                if (!C.isInside(vault, p)) {
                    return { error: 'path_escapes_vault', detail: 'Path escapes vault: ' + p };
                }
                // Gitignore check, for consistency with the existing-file loop above.
                const ignoreCheck = C.runGit(vault, ['check-ignore', '-q', p]);
                if (ignoreCheck.status === 0) { skippedIgnored.push(p); continue; }
                pathspec.push(p);
            }
        }

        if (pathspec.length === 0) {
            const detail = {};
            if (missingWiPaths.length) detail.missing_wi = missingWiPaths;
            if (skippedIgnored.length) detail.skipped_ignored = skippedIgnored;
            return { sha: null, files: [], reason: 'nothing_to_commit', detail: Object.keys(detail).length ? detail : undefined };
        }

        // Refuse to commit a malformed task file. A header field duplicated in the
        // top block is the signature of an edit anchored on too little text — it
        // re-emits fields already present (C5). Scope: task files being committed
        // that exist on disk; the contiguous header block only (title → first blank
        // line); field-shaped keys only. A few inherently multi-value fields are
        // exempt because they may legitimately recur. Refusing (not warning) keeps
        // the bad state from landing; the fix is one edit + re-run, nothing lost.
        const MULTIVALUE_FIELDS = new Set([
            'evidence', 'context', 'scope', 'progress', 'remaining', 'fixes applied',
            'artifacts', 'artifacts changed', 'sources', 'related', 'root cause', 'decision',
        ]);
        for (const p of pathspec) {
            const relp = path.relative(vault, p).split(path.sep).join('/');
            if (!/^tasks\/(current|archive)\//.test(relp) || !p.endsWith('.md')) continue;
            let text; try { text = fs.readFileSync(p, 'utf8'); } catch (_) { continue; }
            const lines = text.split('\n');
            const seen = new Map();
            const dups = [];
            for (let i = 0; i < lines.length; i++) {
                const m = lines[i].match(/^-\s+([A-Za-z][A-Za-z ]+):/);
                // The header block ends at the first non-field line once it has
                // started (a blank line, a ## section, prose, or an indented
                // sub-bullet) — do not scan the body.
                if (!m) { if (seen.size) break; else continue; }
                const key = m[1].trim();
                const norm = key.toLowerCase();
                if (MULTIVALUE_FIELDS.has(norm)) continue;
                if (seen.has(norm)) dups.push(key + ' (lines ' + (seen.get(norm) + 1) + ' and ' + (i + 1) + ')');
                else seen.set(norm, i);
            }
            if (dups.length) {
                return { error: 'malformed_task_file', detail: 'Task file ' + relp + ' has a duplicated header field: ' + dups.join('; ') + '. A header field appears more than once (an edit likely re-emitted fields already present). Fix the task file and re-run commit_wi — nothing has been committed.' };
            }
        }

        // Build commit message (fixed format — no caller override; parseability).
        const utcTs = now.toISOString().replace(/\.\d{3}Z$/, 'Z');
        const sidSuffix = (typeof args.session_id === 'string' && args.session_id)
            ? ' [' + args.session_id.slice(0, 8) + ']' : '';
        let scope;
        if (wiSet.size === 0) {
            scope = 'vault';
        } else {
            const sorted = Array.from(wiSet).sort((a, b) => a - b);
            scope = 'wi' + sorted[0];
        }
        const commitMsg = 'xo(' + scope + '): savepoint ' + utcTs + sidSuffix;

        // Bounded-backoff retry for index.lock contention at either git step.
        // The add→staged-check→commit sequence runs under one retry controller
        // so that a lock failure at `git add` or `git commit` triggers the same
        // backoff. Non-lock failures break out immediately with their own error.
        const LOCK_RE = /index\.lock|Unable to create.*lock/;
        const MAX_RETRIES = 3;
        const RETRY_BASE_MS = 200;
        let addSucceeded = false;

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            // Stage the specific files (git add -- <paths>).
            addSucceeded = false;
            const addResult = C.runGit(vault, ['add', '--'].concat(pathspec));
            if (addResult.status !== 0) {
                const addErr = (addResult.stderr || '') + (addResult.error || '');
                if (LOCK_RE.test(addErr)) {
                    if (attempt < MAX_RETRIES) {
                        C.sleepSync(RETRY_BASE_MS * Math.pow(2, attempt));
                        continue;
                    }
                    return { error: 'index_locked', detail: addErr.slice(0, 500) };
                }
                return { error: 'stage_failed', detail: addResult.stderr || addResult.error };
            }
            addSucceeded = true;

            // Check if anything is actually staged for these paths.
            const diffResult = C.runGit(vault, ['diff', '--cached', '--name-only', '--'].concat(pathspec));
            if (diffResult.status === 0 && !diffResult.stdout.trim()) {
                const detail = {};
                if (missingWiPaths.length) detail.missing_wi = missingWiPaths;
                if (skippedIgnored.length) detail.skipped_ignored = skippedIgnored;
                return { sha: null, files: [], reason: 'nothing_to_commit', detail: Object.keys(detail).length ? detail : undefined };
            }

            // Commit.
            const commitResult = C.runGit(vault, ['commit', '-m', commitMsg, '--', ...pathspec]);
            if (commitResult.status === 0) {
                // Extract sha.
                const shaResult = C.runGit(vault, ['rev-parse', 'HEAD']);
                const sha = shaResult.stdout.trim();
                const result = { sha, files: pathspec, branch };
                if (missingWiPaths.length) result.detail = { missing_wi: missingWiPaths };
                if (skippedIgnored.length) {
                    result.detail = result.detail || {};
                    result.detail.skipped_ignored = skippedIgnored;
                }
                return result;
            }
            // Check for index.lock contention.
            const combined = (commitResult.stderr || '') + (commitResult.error || '');
            if (LOCK_RE.test(combined)) {
                if (attempt < MAX_RETRIES) {
                    C.sleepSync(RETRY_BASE_MS * Math.pow(2, attempt));
                    continue;
                }
                const rw = resetAfterFailedCommit(vault, pathspec);
                const d = combined.slice(0, 500) + (rw ? ' | ' + rw : '');
                return { error: 'index_locked', detail: d };
            }
            // Check for nothing-to-commit (git exit 1 with "nothing to commit").
            if (/nothing to commit/.test(combined)) {
                const detail = {};
                if (missingWiPaths.length) detail.missing_wi = missingWiPaths;
                if (skippedIgnored.length) detail.skipped_ignored = skippedIgnored;
                return { sha: null, files: [], reason: 'nothing_to_commit', detail: Object.keys(detail).length ? detail : undefined };
            }
            // Check for pre-commit hook failure. A silent `exit 1` hook emits no
            // distinctive text, so detect by hook presence too, not only by text.
            if (commitResult.status !== 0 && (/hook/.test(combined) || hasPrecommitHook(vault))) {
                const rw = resetAfterFailedCommit(vault, pathspec);
                const d = (combined.slice(0, 1000) || 'pre-commit hook rejected the commit') + (rw ? ' | ' + rw : '');
                return { error: 'precommit_failed', detail: d };
            }
            // Generic commit failure — unstage only if add succeeded this attempt.
            const rw = addSucceeded ? resetAfterFailedCommit(vault, pathspec) : null;
            const d = combined.slice(0, 500) + (rw ? ' | ' + rw : '');
            return { error: 'commit_failed', detail: d };
        }
        return { error: 'commit_failed', detail: 'exhausted retries' };
    }
    if (name === 'read_diary') {
        const vault = effectiveVault(args);
        // Validate `date` once at the boundary (format AND real calendar date), so both the
            // single-day and multi-day paths agree: an impossible date like 2026-02-31 is rejected
            // either way rather than silently becoming today on one path and throwing on the other.
        let date;
        if (args.date != null) {
            date = String(args.date);
            const dt = Date.parse(date + 'T00:00:00Z');
            if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(dt) || new Date(dt).toISOString().slice(0, 10) !== date) {
                throw new Error('read_diary: date must be a valid calendar date (YYYY-MM-DD)');
            }
        } else {
            date = new Date().toISOString().slice(0, 10);
        }
        const dayFile = (d) => path.join(vault, 'diary', d.slice(0, 7), d + '.md');
        if (args.terms != null && !(Array.isArray(args.terms) && args.terms.every((t) => typeof t === 'string'))) {
            throw new Error('read_diary: terms must be an array of strings');
        }
        if (args.exclude_session != null && String(args.exclude_session).trim() === '') {
            throw new Error('read_diary: exclude_session must be a non-empty session id');
        }
        // Whole-token literal match: a term may not sit inside a longer alphanumeric run,
        // so "WI-624" does not match "WI-6240" and "#48" does not match "#480".
        const termRes = (args.terms || []).map((t) => t.trim()).filter(Boolean).map((t) => new RegExp(
            (/^[a-z0-9]/i.test(t) ? '(?<![a-z0-9])' : '') + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + (/[a-z0-9]$/i.test(t) ? '(?![a-z0-9])' : ''), 'i'));
        let days = 1;
        if (args.days != null) {
            days = Number(args.days);
            if (!Number.isInteger(days) || days < 1 || days > 31) throw new Error('read_diary: days must be an integer between 1 and 31');
        }
        // since: an explicit window start (inclusive). The window is since..date, clamped to the days
        // lookback (default 14 when since is given), so a stale bound cannot read months of diary.
        let since = null;
        if (args.since != null) {
            since = String(args.since);
            const t = Date.parse(since + 'T00:00:00Z');
            if (!/^\d{4}-\d{2}-\d{2}$/.test(since) || !Number.isFinite(t) || new Date(t).toISOString().slice(0, 10) !== since) {
                throw new Error('read_diary: since must be a valid calendar date (YYYY-MM-DD)');
            }
            // A valid but future bound (e.g. a local-dated `Last checked` a day ahead of UTC)
            // clamps to `date` rather than erroring: the milestone check must fail safe.
            if (since > date) since = date;
            if (args.days == null) days = 14; // default lookback cap
        }
        const filtered = args.session_id != null || args.wi != null || args.last_n != null
            || args.exclude_session != null || termRes.length > 0;
        const MAX_DAY_BYTES = 1024 * 1024; // per day file; real days are well under 100 KB
        // A tail-read day starts mid-entry; drop the headless fragment so it cannot slip past exclude_session.
        const readDay = (file) => {
            const day = C.readFileTail(file, MAX_DAY_BYTES);
            if (day.truncated) { const at = day.text.search(/^## /m); day.text = at >= 0 ? day.text.slice(at) : ''; }
            return day;
        };
        const applyFilters = (content) => {
            let sel = content.split(/(?=^## )/m).filter((s) => s.trim());
            if (args.exclude_session != null) {
                const own = '## [' + String(args.exclude_session) + ']';
                sel = sel.filter((p) => !p.startsWith(own));
            }
            if (termRes.length) sel = sel.filter((p) => termRes.some((re) => re.test(p)));
            if (args.session_id != null) sel = sel.filter((p) => p.includes('[' + String(args.session_id) + ']'));
            if (args.wi != null) { const wre = new RegExp('wi[-_]?0*' + parseInt(args.wi, 10) + '(?![0-9])', 'i'); sel = sel.filter((p) => wre.test(p)); }
            if (args.last_n != null) sel = sel.slice(-parseInt(args.last_n, 10));
            return sel;
        };
        if (days === 1 && since == null) {
            const file = dayFile(date);
            let day;
            try { day = readDay(file); } catch (_) { return { path: file, found: false, content: '' }; }
            const more = day.truncated ? { truncated: true } : {};
            if (!filtered) return Object.assign({ path: file, found: true, content: day.text }, more); // whole day (default)
            const sel = applyFilters(day.text);
            return Object.assign({ path: file, found: true, content: sel.join('').trim() + '\n', entries: sel.length }, more);
        }
        // Multi-day window ending at `date` (UTC calendar days, oldest first); missing days are skipped.
        const end = Date.parse(date + 'T00:00:00Z'); // `date` already validated at the boundary
        const floor = new Date(end - (days - 1) * 86400000).toISOString().slice(0, 10);
        const windowStart = since != null && since > floor ? since : floor;
        const inWindow = [];
        let truncated = false;
        for (let i = days - 1; i >= 0; i--) {
            const d = new Date(end - i * 86400000).toISOString().slice(0, 10);
            if (d < windowStart) continue; // don't read days before the window start
            const file = dayFile(d);
            let day;
            try { day = readDay(file); } catch (_) { continue; }
            inWindow.push({ d, file, text: day.text.endsWith('\n') ? day.text : day.text + '\n', truncated: day.truncated });
        }
        const paths = inWindow.map((r) => r.file);
        const combined = inWindow.map((r) => r.text).join('');
        truncated = inWindow.some((r) => r.truncated);
        const extra = Object.assign(since != null ? { window_start: windowStart } : {}, truncated ? { truncated: true } : {});
        if (!paths.length) return Object.assign({ paths, found: false, content: '' }, extra);
        const sel = applyFilters(combined);
        return Object.assign({ paths, found: true, content: sel.join('').trim() + '\n', entries: sel.length }, extra);
    }
    throw new Error('unknown tool: ' + name);
}

function handle(msg) {
    if (!msg || typeof msg !== 'object') return;
    const { id, method, params } = msg;
    if (method === 'initialize') {
        const pv = (params && params.protocolVersion) || '2024-11-05';
        return result(id, { protocolVersion: pv, capabilities: { tools: {} }, serverInfo: SERVER });
    }
    if (method === 'notifications/initialized' || method === 'initialized') return; // notification, no reply
    if (method === 'ping') return result(id, {});
    if (method === 'tools/list') return result(id, { tools: TOOLS });
    if (method === 'tools/call') {
        const name = params && params.name;
        const args = params && params.arguments;
        try { return textResult(id, callTool(name, args)); }
        catch (e) { return result(id, { content: [{ type: 'text', text: 'ERROR: ' + ((e && e.message) || String(e)) }], isError: true }); }
    }
    if (id !== undefined && id !== null) return rpcError(id, -32601, 'method not found: ' + method);
    // otherwise: an unknown notification — ignore
}

function main() {
    const rl = readline.createInterface({ input: process.stdin });
    rl.on('line', (line) => {
        const t = line.trim();
        if (!t) return;
        let msg;
        try { msg = JSON.parse(t); } catch (_) { return; }
        try { handle(msg); }
        catch (e) { if (msg && msg.id !== undefined && msg.id !== null) rpcError(msg.id, -32603, 'internal error: ' + ((e && e.message) || String(e))); }
    });
}

module.exports = { callTool, handle, TOOLS, SERVER };

if (require.main === module) main();

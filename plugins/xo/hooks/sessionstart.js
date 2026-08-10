#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');
const { readInput, appendLog, atomicWrite, isoUtc, localWallClock, buildPaths } = require('./_common.js');
const { reindexTasks } = require('./reindex-tasks.js');

function buildStandingDirectives(ctx) {
    const { XOLOCAL, XOLOCAL_EXPLICIT } = ctx;
    const pluginRoot = process.env.CORTEX_PLUGIN_ROOT || path.dirname(__dirname);
    const setupScript = path.join(pluginRoot, 'setup.sh');
    const setupGuide = path.join(pluginRoot, 'SETUP.md');

    const lines = [
        '# XO Agent Instructions',
        '',
        'You are an XO-enabled agent. XO is an operator workflow system with persistent memory, structured recording, and specialised skills for software engineering work.',
        '',
        '## Skill Routing',
        '',
        'When the user begins a new task or question, invoke the `xo` skill. Its first step is **Triage**: it classifies the request — a quick answer vs substantive work — and, for substantive work, determines and confirms the pathway with the operator before any implementation. xo reads intent, routes to the right stage, and can propose a composed task pattern (e.g. spec-driven, TDD, document-a-feature) with one recommendation for the operator to accept or adjust.',
        '',
        'This applies even when the session opens with an explicit skill command (e.g. `/merge-pr`). An explicit `/skill` does not bypass xo: invoke `xo` first to Triage and begin recording, treat the named skill as the already-chosen pathway (do not re-litigate or re-route it), then run that skill under xo so savepoints, Recall, and the recording protocol still apply. The only exception is a trivial factual or conversational question.',
        '',
        '## Stage Capabilities — offer these proactively',
        '',
        'When the user\'s intent matches a stage trigger, offer it without being asked. Use the pattern: "You seem to want to [X] — want to use [stage] from [phase]?"',
        '',
        '**Survey** (Observe) — research, survey, map the territory, what exists, where does X live, what do we know about X, broad overview',
        '**Analyse** (Observe) — investigate, deep dive, establish facts about X, how does X work, verify, look into this thoroughly',
        '**Distil** (Orient) — compile findings, what did we learn, turn these observations into a brief, synthesise this',
        '**Workshop** (Orient) — design options, what approaches exist, explore the design space, what should we do, stress-test this design',
        '**Spec** (Decide) — write the spec, define what we will build, specify this, write requirements, formalise this',
        '**Plan** (Decide) — build a plan, plan the implementation, turn the spec into a plan (via plan mode)',
        '**Review** (Decide) — review this PR, assess these changes, review my commits, respond to review comments, validate my work',
        '**Capture** (Decide) — what did we learn, keep this for next time, crystallise, capture this finding',
        '**Provision** (Act) — set up workspace, create worktree, prepare environment, branch for this work',
        '**Build** (Act) — implement, write the code, fix this bug, make this change',
        '**Prove** (Act) — write tests, TDD, test driven development, prove it works, run tests, validate',
        '**Ship** (Act) — raise PR, submit pull request, ship this, open PR, publish app',
        '**Document** (Act) — write the guide, document how this works, produce a report',
        '**Cleanup** (Act) — clean up, tear down, remove worktree, wrap up, remove temp files',
        '',
        '## Reflexes',
        '',
        'Be proactive but not naggy. After producing a substantial artifact, offer a cold Critic to check it against intent AND its foundations (flag work built on speculation). Reach for an existing skill (especially Snowflake-technology skills) before improvising. Offer the natural next stage on substantial work — once, easy to decline. Cleanup/commit/push are terminal-only: never push them while the operator is still reviewing or validating.',
        '',
        '## Private Memory Repository',
        '',
        `Your private memory vault is at: \`${XOLOCAL}\``,
        '',
        '## Recall',
        '',
        'When you wonder "have we seen this before?" — or the operator makes a fuzzy recall reference ("I vaguely remember…", "didn\'t we…", "that thing about…") — use **Recall** to search your memory vault. Recall dispatches a fast subagent that expands the query into keywords and greps notes/tasks/diary, returning ranked full-path hits. It is a cheap, always-available reflex; reach for it before assuming something is undocumented. Recall is for **terms, topics, or concepts** (searching by meaning); when you already have a specific identity — a WI number, an exact filename — load it directly rather than via Recall. See the Recall reference (`references/observe-recall.md`) for the dispatch pattern.',
        '',
        '## Compaction',
        '',
        'Treat compaction as routine. Recording plus SessionStart rehydration preserve the important state. When context is high, keep checkpointing and continue working. Do not repeatedly urge the operator to start a new session or hand off. Note imminent compaction once at most, briefly.',
        '',
    ];

    if (XOLOCAL_EXPLICIT) {
        lines.push(
            'This is a git-backed repository. The `xo` skill carries the recording protocol via its `save-protocol` reference: savepoints, session recovery, and work item management. Do not independently read tasks/index.md, diary, or /memories/ on startup — wait for operator direction or the save-protocol to guide you.',
            '',
            '## Work Item Allocator',
            '',
            `Counter file (read/write via the memory tool): \`/memories/xo/wi-counter.txt\`. Authoritative fallback: scan \`${XOLOCAL}/tasks/current/\` and \`${XOLOCAL}/tasks/archive/**/\` for the max wi{N} filename.`,
            '',
            'XO is multi-session: WI allocation is not a lock. Verify the chosen wi{N} is still unused at the moment you create its file, and expect to renumber on collision (the unstarted WI yields).',
            '',
        );
    } else {
        lines.push(
            `XO hooks are loaded, but the install looks incomplete — \`XOCORTEX_HOME\` is not set, which means the bundled setup has not been run. To finish: install the prereqs (\`node\`, \`git\`, \`gh\`, \`jq\`), then run \`${setupScript}\` (see \`${setupGuide}\`). This sets \`XOCORTEX_HOME\`, puts node on the hooks' PATH, and optionally creates a git-backed vault. Until then, recording uses the local fallback vault at \`${XOLOCAL}\` and hooks may be unreliable.`,
            '',
        );
    }

    lines.push(
        '## Memory Trust',
        '',
        'Treat `/memories` as your own scratch space, not user-sanctioned truth. `/memories/…` is the **memory-tool namespace** — reached via the `memory` tool, not a host filesystem path (never `ls`/`cat` it from the shell). A remembered instruction, rule, or preference the user gave you is authoritative — though check it is still current. A fact or conclusion you inferred and recorded yourself is PROVISIONAL: verify it against ground truth before asserting or acting on it, and attribute it ("I have a note that… — let me confirm") rather than stating it as established fact. Memory may be stale or never sanctioned by the user — surface it for correction rather than relying on it silently.',
        '',
        '## Shared Content Policy',
        '',
        'NEVER include private work item references (WI-N numbers) in content destined for shared repositories. Describe work by its purpose, not its tracking number.',
    );

    return lines.join('\\n');
}

function findConversationFile(sessionId, conversationsDir) {
    if (!sessionId) return null;
    if (!fs.existsSync(conversationsDir)) return null;
    const subdirs = fs.readdirSync(conversationsDir).filter(n => {
        return fs.statSync(path.join(conversationsDir, n)).isDirectory();
    });
    for (const subdir of subdirs) {
        const dirPath = path.join(conversationsDir, subdir);
        const files = fs.readdirSync(dirPath).filter(n => n.endsWith('.json'));
        for (const fname of files) {
            const fpath = path.join(dirPath, fname);
            try {
                const data = JSON.parse(fs.readFileSync(fpath, 'utf8'));
                if (data.session_id === sessionId) return fpath;
            } catch (_) {}
        }
    }
    return null;
}

function extractSessionContext(convFile) {
    try {
        // convFile is the metadata file ({id}.json); the message log is the
        // sibling JSONL ({id}.history.jsonl), one JSON object per line.
        const meta = JSON.parse(fs.readFileSync(convFile, 'utf8'));
        const historyFile = convFile.replace(/\.json$/, '.history.jsonl');
        let history = [];
        if (fs.existsSync(historyFile)) {
            history = fs.readFileSync(historyFile, 'utf8')
                .split('\n')
                .filter(Boolean)
                .map(line => { try { return JSON.parse(line); } catch (_) { return null; } })
                .filter(Boolean);
        }

        // Files touched, deduped most-recently-modified first (recency matters most for resuming).
        const fileTouches = history.flatMap(h => {
            const contents = Array.isArray(h.content) ? h.content : [];
            return contents
                .filter(c => c.type === 'tool_use')
                .map(c => c.tool_use)
                .filter(t => t && ['write', 'edit', 'multi_edit'].includes(t.name))
                .map(t => t.input?.file_path)
                .filter(Boolean);
        });
        const filesSeen = new Set();
        const filesRecent = [];
        for (let i = fileTouches.length - 1; i >= 0; i--) {
            if (!filesSeen.has(fileTouches[i])) { filesSeen.add(fileTouches[i]); filesRecent.push(fileTouches[i]); }
        }

        const toolCounts = {};
        history.flatMap(h => {
            const contents = Array.isArray(h.content) ? h.content : [];
            return contents
                .filter(c => c.type === 'tool_use')
                .map(c => c.tool_use?.name)
                .filter(Boolean);
        }).forEach(n => { toolCounts[n] = (toolCounts[n] ?? 0) + 1; });
        const topTools = Object.entries(toolCounts)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 8)
            .map(([tool, count]) => `${tool}(${count})`);

        // Which skills the session invoked (skill tool's input.command), in first-use order.
        const skillsUsed = [...new Set(
            history.flatMap(h => Array.isArray(h.content) ? h.content : [])
                .filter(c => c.type === 'tool_use' && c.tool_use?.name === 'skill')
                .map(c => c.tool_use?.input?.command)
                .filter(Boolean)
        )];

        // Last actual user prompt: skip role:user entries that are tool_results
        // (no text part) and take the most recent message that carries text.
        const userTexts = history
            .filter(h => h.role === 'user')
            .map(h => (Array.isArray(h.content) ? h.content : [])
                .filter(c => c.type === 'text')
                .map(c => c.text)
                .join(''))
            .filter(t => t && t.trim());
        const lastUser = userTexts.length
            ? userTexts[userTexts.length - 1].slice(0, 300)
            : '';

        const messageCount = history.length;
        const summarizationCount = (meta.summarization ?? []).length;

        // Subagents dispatched (task tool's subagent_type) and MCP tools used
        // (name prefixed mcp_) — both useful for re-orienting after compaction.
        const subagents = [...new Set(
            history.flatMap(h => Array.isArray(h.content) ? h.content : [])
                .filter(c => c.type === 'tool_use' && c.tool_use?.name === 'task')
                .map(c => c.tool_use?.input?.subagent_type)
                .filter(Boolean)
        )];
        const mcpTools = [...new Set(
            history.flatMap(h => Array.isArray(h.content) ? h.content : [])
                .filter(c => c.type === 'tool_use' && typeof c.tool_use?.name === 'string' && c.tool_use.name.startsWith('mcp_'))
                .map(c => c.tool_use.name.replace(/^mcp_/, ''))
                .filter(Boolean)
        )];

        // Cap list lengths so a large session (e.g. touching thousands of files)
        // can't bloat the injected context. Top tools already capped at 8 above.
        const FILE_CAP = 15, LIST_CAP = 20;
        const capped = (arr, cap) => arr.length > cap
            ? `${arr.slice(0, cap).join(', ')} … (+${arr.length - cap} more)`
            : arr.join(', ');
        const filesLine = filesRecent.length > FILE_CAP
            ? `${filesRecent.slice(0, FILE_CAP).join(', ')} … (+${filesRecent.length - FILE_CAP} more, ${filesRecent.length} total)`
            : filesRecent.join(', ');

        const lines = [
            `Files modified (latest first): ${filesLine}`,
            `Skills used: ${capped(skillsUsed, LIST_CAP)}`,
        ];
        if (subagents.length) lines.push(`Subagents: ${capped(subagents, LIST_CAP)}`);
        if (mcpTools.length) lines.push(`MCP tools: ${capped(mcpTools, LIST_CAP)}`);
        lines.push(`Top tools: ${topTools.join(', ')}`);
        lines.push(`Last request: ${lastUser}`);
        lines.push(`Messages: ${messageCount}, Summarizations: ${summarizationCount}`);
        return lines.join('\n');
    } catch (_) {
        return '';
    }
}

// Time-based "hygiene" reminders surfaced at session start: soft, infrequent nudges
// backed by a durable watermark where git can't tell us itself. Returns a string of
// nudges (\\n-joined to match directive style), or '' when nothing is due.
function buildHygieneNudges(ctx) {
    const { XOLOCAL, XOLOCAL_EXPLICIT, CONTEXT_STATE_DIR, LOG_FILE, TIMESTAMP, SHORT_SID } = ctx;
    const MEMORY_PRUNE_DAYS = 30, REPO_COMMIT_DAYS = 7, DECLINE_DAYS = 7;
    const DAY = 24 * 60 * 60 * 1000;
    const nowMs = Date.now();
    const nowIso = isoUtc();
    const ageDays = (iso) => iso ? (nowMs - Date.parse(iso)) / DAY : Infinity;

    // State lives at /memories/xo/hygiene-state.json (beside wi-counter.txt).
    const stateFile = path.join(path.dirname(CONTEXT_STATE_DIR), 'xo', 'hygiene-state.json');
    let state = null;
    try { state = JSON.parse(fs.readFileSync(stateFile, 'utf8')); } catch (_) {}
    let dirty = false;
    if (!state || typeof state !== 'object') {
        // First run: seed a clean baseline and do NOT nudge (30-day grace from setup).
        state = { memoryPrune: { lastPruned: nowIso, lastDeclined: null }, repoCommit: { lastDeclined: null } };
        dirty = true;
    }
    state.memoryPrune = state.memoryPrune || { lastPruned: null, lastDeclined: null };
    state.repoCommit = state.repoCommit || { lastDeclined: null };
    // `lastDeclined` is written by the AGENT (per the act-cleanup skill) when the user declines a
    // nudge — NEVER by this hook on generation. The hook cannot know if the user actually saw or
    // answered the nudge; writing a watermark on generation silently consumed the nudge on
    // compaction-triggered starts (which don't surface it). Generation here is side-effect-free
    // except the first-run seed above.

    const nudges = [];

    // Memory curation: git has no signal for "I pruned memory", so rely on the watermark.
    if (ageDays(state.memoryPrune.lastPruned) > MEMORY_PRUNE_DAYS &&
        ageDays(state.memoryPrune.lastDeclined) > DECLINE_DAYS) {
        const since = isFinite(ageDays(state.memoryPrune.lastPruned))
            ? `${Math.floor(ageDays(state.memoryPrune.lastPruned))}d ago` : 'a while ago';
        nudges.push(`your \`/memories\` were last curated ${since} — consider a Curate pass (the supervised-dreaming memory review in Cleanup) to prune stale entries and promote durable ones`);
    }

    // Repo commit/push: only when the vault is git-backed (the repo approach).
    if (XOLOCAL_EXPLICIT) {
        try {
            const out = execSync(`git -C "${XOLOCAL}" log -1 --format=%ct`, { timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'] });
            const lastCommitEpoch = parseInt(String(out).trim(), 10);
            if (lastCommitEpoch) {
                const commitAgeDays = (nowMs / 1000 - lastCommitEpoch) / (60 * 60 * 24);
                let ahead = 0;
                try { ahead = parseInt(String(execSync(`git -C "${XOLOCAL}" rev-list --count @{u}..HEAD`, { timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'] })).trim(), 10) || 0; } catch (_) {}
                const stale = commitAgeDays > REPO_COMMIT_DAYS;
                if ((stale || ahead > 0) && ageDays(state.repoCommit.lastDeclined) > DECLINE_DAYS) {
                    const parts = [];
                    if (stale) parts.push(`was last committed ${Math.floor(commitAgeDays)}d ago`);
                    if (ahead > 0) parts.push(`has ${ahead} unpushed commit${ahead === 1 ? '' : 's'}`);
                    nudges.push(`your xocortex vault ${parts.join(' and ')} — worth committing/pushing your notes and tasks`);
                }
            }
        } catch (_) { /* not a git repo, or git unavailable — skip silently */ }
    }

    if (dirty) {
        try { atomicWrite(stateFile, JSON.stringify(state, null, 2) + '\n'); } catch (_) {}
    }
    if (!nudges.length) return '';
    appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] hygiene nudges: ${nudges.length}`);
    return 'Reflexive hygiene (soft nudges — surface to the user once, conversationally; never block, easy to decline). If the user declines a nudge, record it so it does not re-nag: set the matching `lastDeclined` to today in `/memories/xo/hygiene-state.json` (`repoCommit` for the vault commit/push nudge, `memoryPrune` for the memory-curation nudge) via the memory tool. Accepting needs no write — acting (committing, pruning) resets the condition.\\n- ' + nudges.join('\\n- ');
}

function main() {
    let input;
    try {
        input = readInput();
    } catch (e) {
        process.stdout.write('{"continue": true}');
        return;
    }

    const ctx = buildPaths(input);
    const { SESSION_ID, SHORT_SID, HOOK_EVENT, CWD, TIMESTAMP, LOG_FILE,
            XOLOCAL, DIARY_PATH, TASKS_DIR, CONTEXT_STATE_DIR } = ctx;

    const source = input.source ?? '';

    appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] event=${HOOK_EVENT} source=${source} session=${SESSION_ID} cwd=${CWD}`);

    if (DIARY_PATH) {
        try { fs.mkdirSync(path.dirname(DIARY_PATH), { recursive: true }); } catch (_) {}
    }

    if (TASKS_DIR && fs.existsSync(path.join(TASKS_DIR, 'current'))) {
        const lockFile = path.join(TASKS_DIR, '.reindex.lock');
        let acquired = false;
        try {
            fs.mkdirSync(lockFile);
            acquired = true;
        } catch (_) {
            try {
                const lockStat = fs.statSync(lockFile);
                const lockAge = Math.floor((Date.now() - lockStat.mtimeMs) / 1000);
                if (lockAge > 10) {
                    try { fs.rmSync(lockFile, { recursive: true }); } catch (_2) {}
                    try {
                        fs.mkdirSync(lockFile);
                        acquired = true;
                    } catch (_3) {
                        appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] skipping reindex (lock held)`);
                    }
                } else {
                    appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] skipping reindex (lock held)`);
                }
            } catch (_) {
                appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] skipping reindex (lock held)`);
            }
        }
        if (acquired) {
            try {
                reindexTasks(TASKS_DIR, LOG_FILE, SHORT_SID);
            } finally {
                try { fs.rmSync(lockFile, { recursive: true }); } catch (_) {}
            }
        }
    }

    const conversationsDir = path.join(os.homedir(), '.snowflake', 'cortex', 'conversations');
    const directives = buildStandingDirectives(ctx);
    const hygiene = buildHygieneNudges(ctx);
    const localTimeLine = (event) => `TIME: User Local Time at ${event}: ${localWallClock()}`;

    switch (source) {
        case 'startup': {
            try { fs.readdirSync(CONTEXT_STATE_DIR)
                .filter(n => {
                    const fpath = path.join(CONTEXT_STATE_DIR, n);
                    const age = (Date.now() - fs.statSync(fpath).mtimeMs) / (1000 * 60 * 60 * 24);
                    return age > 7;
                })
                .forEach(n => {
                    try { fs.unlinkSync(path.join(CONTEXT_STATE_DIR, n)); } catch (_) {}
                });
            } catch (_) {}
            appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] checkpoint: session_start source=startup`);
            let msg = `${directives}\\n\\n---\\n\\n${localTimeLine('Session Start')}\\n\\nSession ${SHORT_SID} started. Await the user's first message and apply Skill Routing above — work requests (\"pick up WI-N\", \"let's do X\", an explicit \`/skill\` command, any task pickup or new initiative) invoke the \`xo\` skill first (which then runs the named skill, if any, as the chosen pathway); quick factual or conversational questions answer directly. Do not proactively read task index, diary, or /memories/.`;
            if (hygiene) msg += `\\n\\n${hygiene}`;
            process.stdout.write(JSON.stringify({ additionalContext: msg }));
            break;
        }

        case 'compact': {
            appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] checkpoint: session_start source=compact`);
            const convFile = findConversationFile(SESSION_ID, conversationsDir);
            let contextExtract = '';
            if (convFile) {
                contextExtract = extractSessionContext(convFile);
                appendLog(LOG_FILE, `[${TIMESTAMP}] compact: extracted context from ${convFile}`);
            }
            let msg = `${directives}\\n\\n---\\n\\n${localTimeLine('Post-Compaction Resume')}\\n\\nSESSION RESUMED AFTER COMPACTION (${SHORT_SID}). Your context was summarised.\\n\\nFirst, open your next reply with a short, friendly recap for the user (1–2 lines): say you were just summarised, weave in a couple of headline stats from the Pre-compaction state below (e.g. messages so far, files touched, skills used), then continue — e.g. "I've just been summarised — 750+ messages, ~12 files touched, skills: xo. Picking up where we left off…". Keep it light and conversational; don't dump the full stats block.\\n\\nRecovery: read your /memories/ session file if one exists, then the task file and notes file for the WI you were working on. Do NOT re-read the full task index — focus on the specific work item. Resume from where notes left off.`;
            if (contextExtract) {
                const escapedExtract = contextExtract.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
                msg += `\\n\\nPre-compaction state:\\n${escapedExtract}`;
            }
            process.stdout.write(JSON.stringify({ additionalContext: msg }));
            break;
        }

        case 'resume': {
            appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] checkpoint: session_start source=resume`);
            let rmsg = `${directives}\\n\\n---\\n\\n${localTimeLine('Session Resume')}\\n\\nSession resumed (${SHORT_SID}). Context is intact — continue where you left off.`;
            if (hygiene) rmsg += `\\n\\n${hygiene}`;
            process.stdout.write(JSON.stringify({ additionalContext: rmsg }));
            break;
        }

        default: {
            appendLog(LOG_FILE, `[${TIMESTAMP}] [${SHORT_SID}] checkpoint: session_start source=${source}`);
            let msg = `${directives}\\n\\n---\\n\\n${localTimeLine('Session Start')}\\n\\nSession ${SHORT_SID} started (source=${source}). Apply Skill Routing above for the user's first message.`;
            if (hygiene) msg += `\\n\\n${hygiene}`;
            process.stdout.write(JSON.stringify({ additionalContext: msg }));
            break;
        }
    }
}

main();

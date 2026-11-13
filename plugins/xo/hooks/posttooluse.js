#!/usr/bin/env node
'use strict';
const { readInput, buildPaths, buildRehydrationDirective, consumeRehydrateFlag, detectSummarisation,
        markSessionNamingComplete, readSessionTitle, sessionTitleCheck } = require('./_common.js');
const { checkContext } = require('./_context-monitor.js');

function main() {
    let input;
    try {
        input = readInput();
    } catch (e) {
        process.stdout.write('{"continue": true}');
        return;
    }

    const ctx = buildPaths(input);
    const { SHORT_SID, LOG_FILE, CONTEXT_STATE_DIR, CONVERSATIONS_DIR, SESSION_ID } = ctx;

    if (input.tool_name === 'rename_session') {
        markSessionNamingComplete(CONTEXT_STATE_DIR, SESSION_ID);
    }

    const r = checkContext(input, CONTEXT_STATE_DIR, SESSION_ID, {
        promptTokensEst: 0,
        responseBuffer: 0,
        hookSource: 'posttool',
        logFile: LOG_FILE,
        shortSid: SHORT_SID,
        incrementTurns: false,
    });

    // Detect a summarisation from the host's own record (authoritative) and set the
    // one-shot rehydrate flag when the conversation's summarisation count grows. Runs
    // before the consume below so a summarisation detected this turn injects immediately.
    detectSummarisation({
        sessionId: SESSION_ID, conversationsDir: CONVERSATIONS_DIR, contextStateDir: CONTEXT_STATE_DIR,
        mode: 'live', logFile: LOG_FILE, shortSid: SHORT_SID,
    });

    // Consume a pending summarisation-rehydrate flag (set by sessionstart compact or the
    // summarisation detector above). First consumer to see it injects the directive once
    // and clears it, so the wording has a single source and the paths cannot double-fire.
    const rehydrate = consumeRehydrateFlag(CONTEXT_STATE_DIR, SESSION_ID);
    const rehydrateMsg = rehydrate.pending
        ? buildRehydrationDirective({
            shortSid: SHORT_SID,
            stats: rehydrate.stats,
            titleCheck: sessionTitleCheck(readSessionTitle(SESSION_ID, CONVERSATIONS_DIR)),
        })
        : '';

    // When a skill loads, append a short reminder that the skill is domain knowledge
    // for the current XO stage, not a replacement for the XO process. This lands at the
    // tail of the skill tool result (after the skill body), so it is a supplement to the
    // stronger reminder the UserPromptSubmit hook emits at the top of the next turn.
    const skillNudge = input.tool_name === 'skill'
        ? `XO [silent]: you just loaded skill '${(input.tool_input && input.tool_input.command) || '?'}' as domain knowledge for your CURRENT XO stage/pathway — it does not replace the XO process. Keep your stage, gates, and recording in force; if the skill's instructions pulled your focus, re-read your task/pathway before proceeding. Surface nothing about this note itself.`
        : '';

    let checkpointMsg = '';
    if (r.result === 'fire') {
        switch (r.level) {
            case 'routine':
                checkpointMsg = `Context ${r.projectedPct}% (trigger: ${r.trigger}; output since last ${r.outputSinceLast}, turns ${r.turnsSinceLast}) — consider checkpointing at your next milestone.`;
                break;
            case 'urgent':
                checkpointMsg = `Context ${r.projectedPct}% [urgent] (trigger: ${r.trigger}; output since last ${r.outputSinceLast}, turns ${r.turnsSinceLast}) — write a checkpoint soon. Update your notes file and task file.`;
                break;
            case 'critical':
                checkpointMsg = `Context ${r.projectedPct}% [critical] (trigger: ${r.trigger}; output since last ${r.outputSinceLast}, turns ${r.turnsSinceLast}) — checkpoint NOW. Write notes file, task file, and diary before continuing.`;
                break;
            case 'final':
                checkpointMsg = `Context ${r.projectedPct}% [FINAL] (trigger: ${r.trigger}; output since last ${r.outputSinceLast}, turns ${r.turnsSinceLast}) — compaction imminent. STOP and write checkpoint immediately: notes file, task file, diary, /memories/.`;
                break;
            default:
                checkpointMsg = `Context ${r.projectedPct}% [${r.level}] — checkpoint may be needed.`;
        }
    }

    const parts = [rehydrateMsg, skillNudge, checkpointMsg].filter(Boolean);
    if (parts.length === 0) {
        process.stdout.write('{"continue": true}');
        return;
    }

    process.stdout.write(JSON.stringify({ additionalContext: parts.join('\n\n') }));
}

main();

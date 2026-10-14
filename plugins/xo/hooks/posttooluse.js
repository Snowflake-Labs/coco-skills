#!/usr/bin/env node
'use strict';
const { readInput, buildPaths } = require('./_common.js');
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
    const { SHORT_SID, LOG_FILE, CONTEXT_STATE_DIR, SESSION_ID } = ctx;

    const r = checkContext(input, CONTEXT_STATE_DIR, SESSION_ID, {
        promptTokensEst: 0,
        responseBuffer: 0,
        hookSource: 'posttool',
        logFile: LOG_FILE,
        shortSid: SHORT_SID,
        incrementTurns: false,
    });

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

    const parts = [skillNudge, checkpointMsg].filter(Boolean);
    if (parts.length === 0) {
        process.stdout.write('{"continue": true}');
        return;
    }

    process.stdout.write(JSON.stringify({ additionalContext: parts.join('\n\n') }));
}

main();

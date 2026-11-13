#!/usr/bin/env node
'use strict';
// PreToolUse hook (matcher: bash) — XO's bash-hang guard.
// Blocks known-bad bash constructions that hang the CoCo Desktop terminal,
// returning an instructional fix so the agent retries with the safe form.
//
// Block contract (as read by Desktop's hook output parser):
//   stdout {"decision":"block","reason":...} -> blocked=true, blockReason=reason.
// Allow contract: {"continue": true}. Fails OPEN on any error (never blocks the
// agent if the guard malfunctions).
const os = require('os');
const path = require('path');
const { readInput, appendLog } = require('./_common.js');
const { matchHangPattern } = require('./_bash-guard.js');

function main() {
    let input;
    try {
        input = readInput();
    } catch (e) {
        process.stdout.write('{"continue": true}');
        return;
    }

    // Only guard the bash tool; anything else passes untouched (fail-open).
    if (input.tool_name !== 'bash') {
        process.stdout.write('{"continue": true}');
        return;
    }

    const command = (input.tool_input && input.tool_input.command) || '';
    if (!command) {
        process.stdout.write('{"continue": true}');
        return;
    }

    // Log path is derived directly (not via buildPaths) so a line is always written
    // even when workspace/vault context is absent from the payload. appendLog
    // creates the dir and is itself wrapped in try/catch — never fatal.
    const LOG_FILE = path.join(os.homedir(), '.snowflake', 'cortex', 'logs', `${new Date().toISOString().slice(0, 10)}-hooks.log`);
    const preview = command.replace(/\s+/g, ' ').slice(0, 200);
    const logLine = (verdict) => appendLog(LOG_FILE, `[${new Date().toISOString()}] pretooluse bash ${verdict} cmd="${preview}"`);

    const hit = matchHangPattern(command);
    if (hit) {
        logLine(`BLOCKED (${hit.label})`);
        const reason = `BLOCKED by xo bash-guard (${hit.label}): this construction hangs the terminal. Fix: ${hit.fix}`;
        process.stdout.write(JSON.stringify({ decision: 'block', reason }));
        return;
    }

    logLine('OK');
    process.stdout.write('{"continue": true}');
}

main();

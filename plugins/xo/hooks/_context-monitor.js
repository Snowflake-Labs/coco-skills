'use strict';
const fs = require('fs');
const path = require('path');
const { appendLog, atomicWrite, isoUtc } = require('./_common.js');

function levelRank(level) {
    switch (level) {
        case 'final':    return 4;
        case 'critical': return 3;
        case 'urgent':   return 2;
        case 'routine':  return 1;
        default:         return 0;
    }
}

function ceil(n) {
    return Math.ceil(n);
}

function checkContext(input, contextStateDir, sessionId, opts) {
    const {
        promptTokensEst = 0,
        responseBuffer = 0,
        hookSource = 'unknown',
        logFile = null,
        shortSid = '',
        incrementTurns = true,
    } = opts ?? {};

    const TIMESTAMP = isoUtc();

    const meta = input.response_metadata;
    if (!meta) {
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: no response_metadata`);
        return { result: 'no_metadata' };
    }

    const consumed = meta?.usage?.tokens_consumed?.[0] ?? {};
    const CONTEXT_WINDOW = consumed.context_window ?? 0;
    const INPUT_TOKENS = consumed.input_tokens?.total ?? 0;
    const OUTPUT_TOKENS = consumed.output_tokens?.total ?? 0;
    const CACHE_READ = consumed.input_tokens?.cache_read ?? 0;
    const CACHE_WRITE = consumed.input_tokens?.cache_write ?? 0;
    const UNCACHED = consumed.input_tokens?.uncached ?? 0;
    const MODEL_NAME = consumed.model_name ?? '?';

    if (!CONTEXT_WINDOW || CONTEXT_WINDOW === 'null') {
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: response_metadata present but no context_window`);
        return { result: 'no_context_window' };
    }

    if (!INPUT_TOKENS || INPUT_TOKENS === 'null') {
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: no_input_tokens`);
        return { result: 'no_input_tokens' };
    }

    const reportedTotal = INPUT_TOKENS + OUTPUT_TOKENS;
    const reportedPct = ((reportedTotal / CONTEXT_WINDOW) * 100).toFixed(1);
    const reportedPctInt = ceil((reportedTotal / CONTEXT_WINDOW) * 100);

    const projectedTotal = reportedTotal + promptTokensEst + responseBuffer;
    const projectedPct = ((projectedTotal / CONTEXT_WINDOW) * 100).toFixed(1);
    const projectedPctInt = ceil((projectedTotal / CONTEXT_WINDOW) * 100);

    const stateFile = path.join(contextStateDir, sessionId);

    let lastContextPct = 0;
    let outputAccum = 0;
    let turnAccum = 0;
    let sessionStartEpoch = 0;

    if (fs.existsSync(stateFile)) {
        try {
            const raw = fs.readFileSync(stateFile, 'utf8').trim();
            if (raw.includes('|')) {
                const parts = raw.split('|');
                lastContextPct = parseInt(parts[0], 10) || 0;
                outputAccum = parseInt(parts[1], 10) || 0;
                turnAccum = parseInt(parts[2], 10) || 0;
                sessionStartEpoch = parseInt(parts[3], 10) || 0;
            } else {
                lastContextPct = parseInt(raw, 10) || 0;
            }
        } catch (_) {}
    }

    const nowEpoch = Math.floor(Date.now() / 1000);
    if (sessionStartEpoch === 0) sessionStartEpoch = nowEpoch;

    function writeState(pct, outAccum, turns) {
        try {
            atomicWrite(stateFile, `${pct}|${outAccum}|${turns}|${sessionStartEpoch}`);
        } catch (e) {
            if (logFile) appendLog(logFile, `[${isoUtc()}] [${shortSid}] ERROR writing state: ${e.message}`);
        }
    }

    if (!lastContextPct) {
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: baseline_set ${reportedPctInt}% input=${INPUT_TOKENS} output=${OUTPUT_TOKENS} window=${CONTEXT_WINDOW} model=${MODEL_NAME}`);
        writeState(reportedPctInt, 0, 0);
        return { result: 'baseline_set', reportedPct, projectedPct, reportedPctInt, projectedPctInt };
    }

    const contextDelta = projectedPctInt - lastContextPct;

    if (contextDelta < 0) {
        // Update the pct baseline but preserve turn/output accumulators.
        // A context drop (jitter or real summarisation) does not mean the agent has
        // recorded. Zeroing accumulators would delay the next checkpoint by another
        // full cycle — the opposite of what we want after a compaction event.
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: baseline_reset old=${lastContextPct}% new=${reportedPctInt}% delta=${contextDelta}% (context dropped, accumulators preserved: output=${outputAccum} turns=${turnAccum})`);
        writeState(reportedPctInt, outputAccum, turnAccum);
        return { result: 'baseline_reset', reportedPct, projectedPct, reportedPctInt, projectedPctInt, contextDelta };
    }

    if (incrementTurns) turnAccum += 1;
    outputAccum += OUTPUT_TOKENS;
    const outputSinceLast = outputAccum;
    const turnsSinceLast = turnAccum;

    let pctLevel = '';
    if (projectedPctInt >= 85)                                         pctLevel = 'final';
    else if (projectedPctInt >= 80)                                    pctLevel = 'critical';
    else if (projectedPctInt >= 70)                                    pctLevel = 'urgent';
    else if (projectedPctInt >= 50 && contextDelta >= 10)              pctLevel = 'routine';

    let outputLevel = '';
    if (outputSinceLast >= 110000)      outputLevel = 'final';
    else if (outputSinceLast >= 70000)  outputLevel = 'critical';
    else if (outputSinceLast >= 40000)  outputLevel = 'urgent';
    else if (outputSinceLast >= 20000)  outputLevel = 'routine';

    let turnLevel = '';
    if (turnsSinceLast >= 75)      turnLevel = 'final';
    else if (turnsSinceLast >= 50) turnLevel = 'critical';
    else if (turnsSinceLast >= 25) turnLevel = 'urgent';
    else if (turnsSinceLast >= 10) turnLevel = 'routine';

    let level = '';
    let trigger = '';
    let bestRank = 0;
    for (const [name, lvl] of [['pct', pctLevel], ['output_delta', outputLevel], ['turns', turnLevel]]) {
        if (!lvl) continue;
        const rank = levelRank(lvl);
        if (rank > bestRank) {
            bestRank = rank;
            level = lvl;
            trigger = name;
        }
    }

    const monitorResult = level ? 'fire' : 'skip';

    if (monitorResult === 'fire') {
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: cycle_check reported=${reportedPct}% projected=${projectedPct}% last=${lastContextPct}% delta=+${contextDelta}% output_since=${outputSinceLast} turns_since=${turnsSinceLast} input=${INPUT_TOKENS} output=${OUTPUT_TOKENS} window=${CONTEXT_WINDOW} cache_read=${CACHE_READ} uncached=${UNCACHED} model=${MODEL_NAME} → fire [${level}] trigger=${trigger}`);
        writeState(projectedPctInt, 0, 0);
    } else {
        let skipReason = '';
        if (projectedPctInt < 50 && outputSinceLast < 20000 && turnsSinceLast < 15) {
            skipReason = 'below_all';
        } else {
            skipReason = 'no_signal_met';
        }
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: cycle_check reported=${reportedPct}% projected=${projectedPct}% last=${lastContextPct}% delta=+${contextDelta}% output_since=${outputSinceLast} turns_since=${turnsSinceLast} input=${INPUT_TOKENS} output=${OUTPUT_TOKENS} window=${CONTEXT_WINDOW} model=${MODEL_NAME} → skip (${skipReason})`);
        writeState(lastContextPct, outputSinceLast, turnsSinceLast);
    }

    return {
        result: monitorResult,
        level,
        trigger,
        reportedPct,
        reportedPctInt,
        projectedPct,
        projectedPctInt,
        reportedTotal,
        lastContextPct,
        contextDelta,
        contextWindow: CONTEXT_WINDOW,
        inputTokens: INPUT_TOKENS,
        outputTokens: OUTPUT_TOKENS,
        outputSinceLast,
        turnsSinceLast,
        modelName: MODEL_NAME,
    };
}

module.exports = { checkContext };

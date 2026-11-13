'use strict';
const fs = require('fs');
const path = require('path');
const { appendLog, atomicWrite, isoUtc } = require('./_common.js');

// A single-turn upward jump larger than this many percentage points vs the prior
// raw reading is treated as an unconfirmed sample — the upstream cache_read
// over-report that transiently (~2x) inflates input_tokens.total for one turn and
// then reverts. Such a sample is not surfaced or escalated and does not advance the
// trusted baseline; a genuine jump holds and self-confirms on the next turn.
const SPIKE_UP_THRESHOLD = 25;

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

// response_metadata.usage.tokens_consumed is an array with one entry per model
// role in the response (orchestration, guardrail, side_call, unknown). Entry [0]
// is NOT reliably the main session — the guardrail role frequently appears first —
// so selecting [0] miscomputes context %. Select the 'orchestration' entry by
// model_role; fall back to the entry with the largest context_window, and only
// seed from the first entry when no entry carries data. Always returns an object
// (never null) so the contract holds standalone.
function selectConsumed(arr) {
    if (!Array.isArray(arr) || !arr.length) return {};
    const orch = arr.find((e) => e && e.model_role === 'orchestration');
    if (orch) return orch;
    const widest = arr.reduce(
        (best, e) => (((e && e.context_window) || 0) > ((best && best.context_window) || 0) ? e : best),
        arr[0],
    );
    return widest || {};
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

    const entries = meta?.usage?.tokens_consumed;
    const consumed = selectConsumed(entries) ?? {};
    const ENTRY_COUNT = Array.isArray(entries) ? entries.length : 0;
    const PICKED_ROLE = consumed.model_role ?? '?';
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
    let lastRaw = 0;
    let outputAccum = 0;
    let turnAccum = 0;
    let sessionStartEpoch = 0;

    if (fs.existsSync(stateFile)) {
        try {
            const raw = fs.readFileSync(stateFile, 'utf8').trim();
            if (raw.includes('|')) {
                const parts = raw.split('|');
                if (parts.length >= 5) {
                    // Current format: pct|lastRaw|out|turns|epoch
                    lastContextPct = parseInt(parts[0], 10) || 0;
                    lastRaw = parseInt(parts[1], 10) || 0;
                    outputAccum = parseInt(parts[2], 10) || 0;
                    turnAccum = parseInt(parts[3], 10) || 0;
                    sessionStartEpoch = parseInt(parts[4], 10) || 0;
                } else {
                    // Legacy 4-field: pct|out|turns|epoch (seed lastRaw from pct).
                    lastContextPct = parseInt(parts[0], 10) || 0;
                    outputAccum = parseInt(parts[1], 10) || 0;
                    turnAccum = parseInt(parts[2], 10) || 0;
                    sessionStartEpoch = parseInt(parts[3], 10) || 0;
                    lastRaw = lastContextPct;
                }
            } else {
                lastContextPct = parseInt(raw, 10) || 0;
                lastRaw = lastContextPct;
            }
        } catch (_) {}
    }

    const nowEpoch = Math.floor(Date.now() / 1000);
    if (sessionStartEpoch === 0) sessionStartEpoch = nowEpoch;

    function writeState(pct, lastRawVal, outAccum, turns) {
        try {
            atomicWrite(stateFile, `${pct}|${lastRawVal}|${outAccum}|${turns}|${sessionStartEpoch}`);
        } catch (e) {
            if (logFile) appendLog(logFile, `[${isoUtc()}] [${shortSid}] ERROR writing state: ${e.message}`);
        }
    }

    if (!lastContextPct) {
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: baseline_set ${reportedPctInt}% input=${INPUT_TOKENS} output=${OUTPUT_TOKENS} window=${CONTEXT_WINDOW} model=${MODEL_NAME}`);
        writeState(reportedPctInt, reportedPctInt, 0, 0);
        return { result: 'baseline_set', reportedPct, projectedPct, reportedPctInt, projectedPctInt };
    }

    // Trust gate: a single-turn upward jump beyond SPIKE_UP_THRESHOLD vs the prior
    // raw reading is an unconfirmed sample (cache_read over-report). Surface nothing,
    // do not escalate, and do NOT advance the trusted baseline — record only the raw
    // reading so the next sample can confirm. A genuine jump holds and is trusted next
    // turn; an artifact reverts and is never surfaced. Accumulators are preserved.
    const jumpVsLastRaw = reportedPctInt - lastRaw;
    if (lastRaw && jumpVsLastRaw > SPIKE_UP_THRESHOLD) {
        // Accumulate output/turns (real regardless of the input over-report) so a
        // deferred output_delta/turns checkpoint still fires on the next trusted turn.
        if (incrementTurns) turnAccum += 1;
        outputAccum += OUTPUT_TOKENS;
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: untrusted_spike reported=${reportedPct}% last_raw=${lastRaw}% jump=+${jumpVsLastRaw} input=${INPUT_TOKENS} cache_read=${CACHE_READ} window=${CONTEXT_WINDOW} → emit nothing (trusted baseline held at ${lastContextPct}%)`);
        writeState(lastContextPct, reportedPctInt, outputAccum, turnAccum);
        return { result: 'untrusted', reportedPct, reportedPctInt, projectedPct, projectedPctInt, lastContextPct, outputSinceLast: outputAccum, turnsSinceLast: turnAccum };
    }

    const contextDelta = projectedPctInt - lastContextPct;

    if (contextDelta < 0) {
        // Update the pct baseline but preserve turn/output accumulators.
        // A context drop (jitter or real summarisation) does not mean the agent has
        // recorded. Zeroing accumulators would delay the next checkpoint by another
        // full cycle, the opposite of what we want after a compaction event.
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: baseline_reset old=${lastContextPct}% new=${reportedPctInt}% delta=${contextDelta}% (context dropped, accumulators preserved: output=${outputAccum} turns=${turnAccum})`);
        writeState(reportedPctInt, reportedPctInt, outputAccum, turnAccum);
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

    // Debounce: the pct critical/final tier is driven by a single sample, and the
    // upstream token accounting intermittently over-reports cache_read (~2x+) for one
    // turn then reverts — which would fire a false "compaction imminent". Require the
    // prior persisted sample (lastContextPct) to also be in-band before escalating.
    // Backward-looking only: the revert is not visible at fire time, so we confirm
    // against history, not lookahead. A lone spike is dropped; a genuine climb
    // self-confirms on the next sample (≤1-cycle delay). Scope: pct trigger,
    // critical/final only — output_delta/turns are monotonic accumulators immune to
    // the spike and are left untouched (they still fire below).
    // Validated against recorded monitor history: suppresses the reverting single-turn
    // spikes while permanently losing no genuine escalation (first crossings recover
    // within one cycle).
    let debouncedPct = false;
    if (pctLevel === 'final' || pctLevel === 'critical') {
        const band = pctLevel === 'final' ? 85 : 80;
        if (lastContextPct < band) {
            debouncedPct = true;
            pctLevel = '';
        }
    }

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

    const monitorResult = level ? 'fire' : (debouncedPct ? 'suppress' : 'skip');

    if (monitorResult === 'fire') {
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: cycle_check reported=${reportedPct}% projected=${projectedPct}% last=${lastContextPct}% delta=+${contextDelta}% output_since=${outputSinceLast} turns_since=${turnsSinceLast} input=${INPUT_TOKENS} output=${OUTPUT_TOKENS} window=${CONTEXT_WINDOW} cache_read=${CACHE_READ} uncached=${UNCACHED} model=${MODEL_NAME} entries=${ENTRY_COUNT} picked_role=${PICKED_ROLE} debounced_pct=${debouncedPct} → fire [${level}] trigger=${trigger}`);
        writeState(projectedPctInt, reportedPctInt, 0, 0);
    } else if (monitorResult === 'suppress') {
        // A pct critical/final that the prior sample did not confirm — a single-turn spike.
        // Emit a distinct marker so backtests can tell a debounced spike from a below_all skip.
        // Persist projectedPctInt (so a genuine climb self-confirms next cycle) and PRESERVE
        // accumulators (the checkpoint the escalation would have prompted did not happen).
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: cycle_check reported=${reportedPct}% projected=${projectedPct}% last=${lastContextPct}% delta=+${contextDelta}% output_since=${outputSinceLast} turns_since=${turnsSinceLast} input=${INPUT_TOKENS} output=${OUTPUT_TOKENS} window=${CONTEXT_WINDOW} cache_read=${CACHE_READ} uncached=${UNCACHED} model=${MODEL_NAME} entries=${ENTRY_COUNT} picked_role=${PICKED_ROLE} → suppress (debounce_pct)`);
        writeState(projectedPctInt, reportedPctInt, outputSinceLast, turnsSinceLast);
    } else {
        let skipReason = '';
        if (projectedPctInt < 50 && outputSinceLast < 20000 && turnsSinceLast < 15) {
            skipReason = 'below_all';
        } else {
            skipReason = 'no_signal_met';
        }
        if (logFile) appendLog(logFile, `[${TIMESTAMP}] [${shortSid}] ${hookSource}: cycle_check reported=${reportedPct}% projected=${projectedPct}% last=${lastContextPct}% delta=+${contextDelta}% output_since=${outputSinceLast} turns_since=${turnsSinceLast} input=${INPUT_TOKENS} output=${OUTPUT_TOKENS} window=${CONTEXT_WINDOW} model=${MODEL_NAME} entries=${ENTRY_COUNT} picked_role=${PICKED_ROLE} → skip (${skipReason})`);
        writeState(lastContextPct, reportedPctInt, outputSinceLast, turnsSinceLast);
    }

    return {
        result: monitorResult,
        level,
        trigger,
        debouncedPct,
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

module.exports = { checkContext, selectConsumed };

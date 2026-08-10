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

function buildPaths(input) {
    const SESSION_ID = input.session_id ?? 'unknown';
    const SHORT_SID = shortSid(SESSION_ID);
    const HOOK_EVENT = input.hook_event_name ?? 'unknown';
    const CWD = input.cwd ?? '';

    const now = new Date();
    const YYYY_MM_DD = now.toISOString().slice(0, 10);
    const YYYY_MM = now.toISOString().slice(0, 7);

    const LOG_DIR = path.join(os.homedir(), '.snowflake', 'cortex', 'logs');
    fs.mkdirSync(LOG_DIR, { recursive: true });
    const LOG_FILE = path.join(LOG_DIR, `${YYYY_MM_DD}-hooks.log`);
    const TIMESTAMP = isoUtc();

    const XOLOCAL = process.env.XOCORTEX_HOME ?? '';
    const XOLOCAL_EXPLICIT = !!process.env.XOCORTEX_HOME;

    const snowflakeHome = process.env.SNOWFLAKE_HOME ?? path.join(os.homedir(), '.snowflake');

    let effectiveVault = XOLOCAL;
    if (!effectiveVault) {
        effectiveVault = path.join(snowflakeHome, 'cortex', 'memory', 'xocortex');
        try { fs.mkdirSync(effectiveVault, { recursive: true }); } catch (_) {}
        try { fs.mkdirSync(path.join(effectiveVault, 'diary'), { recursive: true }); } catch (_) {}
        try { fs.mkdirSync(path.join(effectiveVault, 'notes'), { recursive: true }); } catch (_) {}
        try { fs.mkdirSync(path.join(effectiveVault, 'tasks', 'current'), { recursive: true }); } catch (_) {}
        try { fs.mkdirSync(path.join(effectiveVault, 'tasks', 'archive'), { recursive: true }); } catch (_) {}
        appendLog(LOG_FILE, `[${TIMESTAMP}] XOCORTEX_HOME not set. Using fallback vault: ${effectiveVault}`);
    }

    const DIARY_PATH = path.join(effectiveVault, 'diary', YYYY_MM, `${YYYY_MM_DD}.md`);
    const TASKS_DIR = path.join(effectiveVault, 'tasks');

    const CONTEXT_STATE_DIR = path.join(snowflakeHome, 'cortex', 'memory', 'context-state');
    fs.mkdirSync(CONTEXT_STATE_DIR, { recursive: true });

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
        DIARY_PATH,
        TASKS_DIR,
        CONTEXT_STATE_DIR,
        YYYY_MM_DD,
        YYYY_MM,
    };
}

module.exports = {
    readInput,
    isoUtc,
    localWallClock,
    shortSid,
    appendLog,
    atomicWrite,
    logError,
    buildPaths,
};

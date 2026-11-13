'use strict';
// Output, prompts and external-command execution for setup.js.
const fs = require('fs');
const tty = require('tty');
const { spawnSync } = require('child_process');

const IS_WIN = process.platform === 'win32';

// The Windows console codepage garbles UTF-8, so win32 gets ASCII symbols and
// box/arrow characters are folded to ASCII.
const SYM = IS_WIN
    ? { ok: '[ok]  ', warn: '[warn]', fail: '[fail]', info: '[info]' }
    : { ok: '✓', warn: '⚠', fail: '✗', info: '' };

function asciiFold(s) {
    return s.replace(/═/g, '=').replace(/─/g, '-').replace(/—/g, '-').replace(/→/g, '->');
}

function writeAll(s) {
    const buf = Buffer.from(IS_WIN ? asciiFold(s) : s, 'utf8');
    let off = 0;
    while (off < buf.length) {
        try { off += fs.writeSync(1, buf, off); }
        catch (e) { if (e.code !== 'EAGAIN') throw e; }
    }
}

let writer = writeAll;
function setWriter(fn) { writer = fn || writeAll; }

function out(s = '') { writer(s + '\n'); }
function line(sym, m) { out(sym ? `  ${sym} ${m}` : `  ${m}`); }
function info(m) { line(SYM.info, m); }
function ok(m) { line(SYM.ok, m); }
function warn(m) { line(SYM.warn, m); }
function fail(m) { line(SYM.fail, m); }

class DieError extends Error {
    constructor(message) { super(message); this.name = 'DieError'; }
}

function die(m) {
    fail(m);
    throw new DieError(m);
}

// ---- prompts ----------------------------------------------------------------
// Synchronous, byte-at-a-time stdin reads so piped answers are consumed one line
// per prompt. EOF or an unreadable stdin yields null (the caller takes the default).

let answerSource = null;
function setAnswerSource(fn) { answerSource = fn; }

function sleepMs(ms) {
    try { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); } catch (_) {}
}

function readLineSync() {
    const one = Buffer.alloc(1);
    const bytes = [];
    while (true) {
        let n;
        try { n = fs.readSync(0, one, 0, 1, null); }
        catch (e) {
            if (e.code === 'EAGAIN') { sleepMs(20); continue; }
            n = 0;
        }
        if (n === 0) return bytes.length ? Buffer.from(bytes).toString('utf8') : null;
        if (one[0] === 0x0a) return Buffer.from(bytes).toString('utf8').replace(/\r$/, '');
        bytes.push(one[0]);
    }
}

// Returns the trimmed answer, or `def` when --yes is set, the answer is empty, or
// stdin is at EOF.
function prompt(question, def, opts = {}) {
    writer(`  ${question} `);
    if (opts.yes) { out(def ? `${def} (--yes)` : '(--yes)'); return def; }
    const answer = answerSource ? answerSource(question) : readLineSync();
    if (answerSource || !tty.isatty(0)) out('');
    if (answer === null || answer === undefined) return def;
    const trimmed = String(answer).trim();
    return trimmed === '' ? def : trimmed;
}

// ---- external commands ------------------------------------------------------
// gh/git are spawned without a shell (argument arrays only). XO_SETUP_GH and
// XO_SETUP_GIT override the executable; a value ending in .js runs under node.

function resolveCommand(cmd) {
    const override = cmd === 'gh' ? process.env.XO_SETUP_GH : cmd === 'git' ? process.env.XO_SETUP_GIT : '';
    if (!override) return { file: cmd, pre: [] };
    if (override.toLowerCase().endsWith('.js')) return { file: process.execPath, pre: [override] };
    return { file: override, pre: [] };
}

function run(cmd, args, opts = {}) {
    const { file, pre } = resolveCommand(cmd);
    const r = spawnSync(file, pre.concat(args), {
        encoding: 'utf8',
        cwd: opts.cwd,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
    });
    return {
        ok: !r.error && r.status === 0,
        status: r.status,
        stdout: r.stdout || '',
        stderr: r.stderr || '',
        error: r.error || null,
    };
}

function have(cmd) {
    const r = run(cmd, ['--version']);
    return !r.error && r.status !== null;
}

module.exports = {
    IS_WIN,
    SYM,
    asciiFold,
    setWriter,
    out,
    info,
    ok,
    warn,
    fail,
    die,
    DieError,
    setAnswerSource,
    readLineSync,
    prompt,
    resolveCommand,
    run,
    have,
};

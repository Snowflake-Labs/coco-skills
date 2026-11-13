'use strict';
// Platform layer: persisting XOCORTEX_HOME, putting node on the hook PATH, and
// prerequisite install hints. Unix rc helpers are pure functions over file
// content so they are testable on any OS.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const io = require('./io');

const XOCORTEX_MARKER = '# XO: xocortex memory repo';
const NODE_PATH_MARKER = '# XO: node on PATH for hooks';
const COMMENTARY = /^\s*(#|$)/;

function homeDir() { return process.env.XO_SETUP_HOME || os.homedir(); }
function snowflakeHome() { return process.env.SNOWFLAKE_HOME || path.join(homeDir(), '.snowflake'); }
function defaultVault() { return path.join(snowflakeHome(), 'cortex', 'memory', 'xocortex'); }

function readText(file) {
    try { return fs.readFileSync(file, 'utf8'); } catch (_) { return ''; }
}

// Writes through symlinks (dotfile managers) rather than replacing them.
function writeText(file, content) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, content, 'utf8');
}

// ---- unix shell rc: pure helpers ---------------------------------------------

function shellName(shell) { return path.posix.basename(shell || '/bin/zsh'); }

function detectShellRc(shell, home, exists = fs.existsSync) {
    switch (shellName(shell)) {
        case 'zsh': return path.join(home, '.zshenv');
        case 'bash': {
            const profile = path.join(home, '.bash_profile');
            return exists(profile) ? profile : path.join(home, '.bashrc');
        }
        case 'fish': return path.join(home, '.config', 'fish', 'config.fish');
        default: return path.join(home, '.profile');
    }
}

function splitLines(content) {
    if (!content) return [];
    const lines = content.split('\n');
    if (lines[lines.length - 1] === '') lines.pop();
    return lines;
}

function joinLines(lines) { return lines.length ? lines.join('\n') + '\n' : ''; }

// Single-quoted shell literal. Dies on characters an rc line cannot hold.
function shellQuote(shell, value) {
    if (/[\n\r\0]/.test(value)) io.die(`Refusing to write a value containing a newline or NUL to the shell profile: ${JSON.stringify(value)}`);
    if (shellName(shell) === 'fish') return `'${value.replace(/[\\']/g, '\\$&')}'`;
    return `'${value.replace(/'/g, "'\\''")}'`;
}

function xocortexExportLine(shell, p) {
    const q = shellQuote(shell, p);
    return shellName(shell) === 'fish' ? `set -gx XOCORTEX_HOME ${q}` : `export XOCORTEX_HOME=${q}`;
}

function nodeExportLine(shell, dir) {
    const q = shellQuote(shell, dir);
    return shellName(shell) === 'fish' ? `set -gx PATH ${q} $PATH` : `export PATH=${q}:"$PATH"`;
}

// Reads one shell word (single/double quotes, backslash escapes) from s.
function unquoteWord(s, fish) {
    let out = '';
    let i = 0;
    while (i < s.length && !/\s/.test(s[i])) {
        const c = s[i++];
        if (c === "'") {
            while (i < s.length && s[i] !== "'") {
                if (fish && s[i] === '\\' && (s[i + 1] === '\\' || s[i + 1] === "'")) i++;
                out += s[i++];
            }
            i++;
        } else if (c === '"') {
            while (i < s.length && s[i] !== '"') {
                if (s[i] === '\\' && /["\\$`]/.test(s[i + 1] || '')) i++;
                out += s[i++];
            }
            i++;
        } else if (c === '\\' && i < s.length) {
            out += s[i++];
        } else {
            out += c;
        }
    }
    return out;
}

function hasXocortexExport(content, includeFish) {
    return splitLines(content).some((l) => l.includes('export XOCORTEX_HOME=')
        || (includeFish && l.includes('set -gx XOCORTEX_HOME')));
}

// Value on the last line mentioning XOCORTEX_HOME, unquoted (legacy double-quoted
// and current single-quoted forms).
function existingXocortexValue(content) {
    const hits = splitLines(content).filter((l) => l.includes('XOCORTEX_HOME'));
    if (!hits.length) return '';
    const line = hits[hits.length - 1];
    return unquoteWord(line.replace(/.*?XOCORTEX_HOME[= ]*/, ''), /^\s*set\s/.test(line));
}

function stripXocortexLines(content, includeFish) {
    return joinLines(splitLines(content).filter((l) => !(l.includes(XOCORTEX_MARKER)
        || l.includes('export XOCORTEX_HOME=')
        || (includeFish && l.includes('set -gx XOCORTEX_HOME')))));
}

// Equivalent of `echo "" >> rc; echo marker >> rc; echo line >> rc`.
function appendBlock(content, marker, line) {
    return (content || '') + '\n' + marker + '\n' + line + '\n';
}

// Removes the marker line, the commentary directly beneath it, and the single
// export line that follows. Commentary is buffered so a marker followed only by
// comments to EOF keeps those comments.
function removeNodePathBlock(content) {
    const kept = [];
    let skip = false;
    let buf = [];
    for (const l of splitLines(content)) {
        if (l.includes(NODE_PATH_MARKER)) {
            if (skip) kept.push(...buf);
            skip = true;
            buf = [];
            continue;
        }
        if (skip && COMMENTARY.test(l)) { buf.push(l); continue; }
        if (skip) { skip = false; buf = []; continue; }
        kept.push(l);
    }
    if (skip) kept.push(...buf);
    return joinLines(kept);
}

// The export line under the marker, scanning past commentary.
function readNodeExportLine(content) {
    let inBlock = false;
    for (const l of splitLines(content)) {
        if (inBlock && COMMENTARY.test(l)) continue;
        if (inBlock) return l;
        if (l.includes(NODE_PATH_MARKER)) inBlock = true;
    }
    return '';
}

// ---- unix shell rc: file operations ------------------------------------------

function rcContext(opts) {
    const shell = opts.shell !== undefined ? opts.shell : process.env.SHELL;
    const home = opts.home || homeDir();
    return { shell, home, rc: detectShellRc(shell, home), fish: shellName(shell) === 'fish' };
}

function installShellEnv(xocortexPath, opts = {}) {
    const { shell, rc, fish } = rcContext(opts);
    const exportLine = xocortexExportLine(shell, xocortexPath);
    let content = readText(rc);

    if (hasXocortexExport(content, fish)) {
        const existing = existingXocortexValue(content);
        if (existing === xocortexPath) {
            io.ok(`XOCORTEX_HOME already set correctly in ${rc}`);
            return 'unchanged';
        }
        io.warn(`XOCORTEX_HOME already exported in ${rc} with different value: ${existing}`);
        io.out('');
        const answer = io.prompt(`Replace with ${xocortexPath}? [y/N]`, 'n', opts);
        if (!/^[Yy]$/.test(answer)) {
            io.info('Skipping — XOCORTEX_HOME unchanged');
            return 'kept';
        }
        content = stripXocortexLines(content, fish);
        writeText(rc, content);
    }

    io.out('');
    io.info(`XO needs to add to ${rc}:`);
    io.info(`  ${exportLine}`);
    io.out('');
    const answer = io.prompt('Add this to your shell profile? [Y/n]', 'y', opts);
    if (/^[Nn]$/.test(answer)) {
        io.warn(`Skipped — XOCORTEX_HOME is not exported, so hooks will use the default local vault (${defaultVault()}). Add the line above to ${rc} yourself to use ${xocortexPath}.`);
        return 'declined';
    }
    writeText(rc, appendBlock(content, XOCORTEX_MARKER, exportLine));
    io.ok(`Added XOCORTEX_HOME export to ${rc}`);
    io.warn(`Run 'source ${rc}' or open a new terminal for it to take effect`);
    return 'added';
}

function uninstallShellEnv(opts = {}) {
    const { rc } = rcContext(opts);
    if (!fs.existsSync(rc)) return false;
    const content = readText(rc);
    if (!hasXocortexExport(content, true)) return false;
    writeText(rc, stripXocortexLines(content, true));
    io.ok(`Removed XOCORTEX_HOME export from ${rc}`);
    return true;
}

// ---- unix node PATH ----------------------------------------------------------

function findOnPath(name, envPath = process.env.PATH || '') {
    for (const dir of envPath.split(path.delimiter)) {
        if (!dir) continue;
        const candidate = path.join(dir, name);
        try {
            if (!fs.statSync(candidate).isFile()) continue;
            fs.accessSync(candidate, fs.constants.X_OK);
            return candidate;
        } catch (_) {}
    }
    return '';
}

// The PATH a GUI-/hook-spawned process inherits before any rc runs: on macOS what
// path_helper builds from /etc/paths(.d), else a conventional default.
function hookBasePath() {
    let p = '';
    try {
        fs.accessSync('/usr/libexec/path_helper', fs.constants.X_OK);
        const r = spawnSync('/usr/bin/env', ['-i', '/bin/sh', '-c',
            'eval "$(/usr/libexec/path_helper -s)" 2>/dev/null; printf %s "$PATH"'], { encoding: 'utf8' });
        if (r.status === 0) p = r.stdout || '';
    } catch (_) {}
    return p || '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin';
}

// Tests execution (not resolvability): a version-manager shim can resolve yet fail.
function canRunNode(pathValue, shellBin) {
    const r = spawnSync('env', ['-i', `HOME=${homeDir()}`, `PATH=${pathValue}`, shellBin || 'sh', '-c', 'node --version'],
        { stdio: 'ignore' });
    return r.status === 0;
}

function unixNodeDeps() {
    return {
        nodeCmd: () => findOnPath('node'),
        nodeReal: (nodeCmd) => {
            const r = spawnSync(nodeCmd, ['-e', 'process.stdout.write(process.execPath)'], { encoding: 'utf8' });
            const real = r.status === 0 ? (r.stdout || '') : '';
            try { fs.accessSync(real, fs.constants.X_OK); return real; } catch (_) { return ''; }
        },
        basePath: hookBasePath,
        canRun: canRunNode,
        brewPrefix: () => {
            const brew = findOnPath('brew');
            if (!brew) return '';
            const r = spawnSync(brew, ['--prefix'], { encoding: 'utf8' });
            return r.status === 0 ? (r.stdout || '').trim() : '';
        },
    };
}

// Ensures a hook-style shell can run node. Prefers Homebrew's stable bin dir over
// the version-scoped Cellar dir that `brew upgrade` deletes. Repairs a stale entry.
function installNodePathUnix(opts = {}, deps = unixNodeDeps()) {
    const { shell, rc } = rcContext(opts);

    const nodeCmd = deps.nodeCmd();
    if (!nodeCmd) {
        io.warn('node not found on your PATH — XO hooks require Node.js.');
        io.warn('Install node (or activate your version manager) and re-run setup, or hooks will not fire.');
        return 'no-node';
    }

    const basePath = deps.basePath();
    if (deps.canRun(basePath, 'sh')) {
        io.ok(`node already runnable from the base PATH — hooks can use it; no ${rc} change needed`);
        return 'reachable';
    }

    const nodeReal = deps.nodeReal(nodeCmd);
    if (!nodeReal) {
        io.warn('node is on PATH but could not report a stable binary location.');
        io.warn(`Add your version manager's init to ${rc} manually so non-interactive shells can run node.`);
        return 'no-real';
    }
    let nodeDir = path.posix.dirname(nodeReal);

    const brewPrefix = deps.brewPrefix();
    const cmdDir = path.posix.dirname(nodeCmd);
    if (brewPrefix && cmdDir === `${brewPrefix}/bin` && nodeReal.startsWith(`${brewPrefix}/`)
        && deps.canRun(`${cmdDir}:${basePath}`, 'sh')) {
        nodeDir = cmdDir;
    }

    const exportLine = nodeExportLine(shell, nodeDir);
    const content = readText(rc);
    let result;
    if (content.includes(NODE_PATH_MARKER)) {
        if (readNodeExportLine(content) === exportLine) {
            io.ok(`node PATH entry already correct in ${rc}`);
            result = 'unchanged';
        } else {
            writeText(rc, appendBlock(removeNodePathBlock(content), NODE_PATH_MARKER, exportLine));
            io.ok(`Repaired stale node PATH entry in ${rc} (now ${nodeDir})`);
            result = 'repaired';
        }
    } else {
        writeText(rc, appendBlock(content, NODE_PATH_MARKER, exportLine));
        io.ok(`Added node (${nodeDir}) to ${rc} so hooks can find it`);
        result = 'added';
    }

    // Only zsh sources the rc (.zshenv) non-interactively, so only zsh can self-verify.
    if (shellName(shell) === 'zsh') {
        if (deps.canRun(basePath, 'zsh')) {
            io.ok('Verified: a hook-style shell can now run node');
        } else {
            io.warn(`Added the entry, but a clean zsh still cannot RUN node from ${rc}.`);
            io.warn(`Check that '${nodeDir}' holds a working node, then re-run setup to re-verify.`);
        }
    } else {
        io.info(`Added node to ${rc} (execution self-test runs for zsh only; note a non-interactive bash hook shell won't source ${rc} — verify it can run node).`);
    }
    return result;
}

function uninstallNodePathUnix(opts = {}) {
    const { rc } = rcContext(opts);
    if (!fs.existsSync(rc)) return false;
    const content = readText(rc);
    if (!content.includes(NODE_PATH_MARKER)) return false;
    writeText(rc, removeNodePathBlock(content));
    io.ok(`Removed node PATH entry from ${rc}`);
    return true;
}

// ---- win32 environment -------------------------------------------------------
// Single entry point for persisted Windows env reads/writes. XO_SETUP_WINENV
// points at a JSON file ({"User":{...},"Machine":{...}}) used instead of the real
// environment. Names and values reach PowerShell via env vars, never via the script.

function winEnv(op, scope, name, value) {
    const fake = process.env.XO_SETUP_WINENV;
    if (fake) {
        let data = {};
        try { data = JSON.parse(fs.readFileSync(fake, 'utf8')) || {}; } catch (_) {}
        if (op === 'get' && data._failGet) {
            io.warn(`Could not read ${scope} environment variable ${name}: simulated failure`);
            return null;
        }
        const vars = data[scope] || {};
        const key = Object.keys(vars).find((k) => k.toLowerCase() === name.toLowerCase());
        if (op === 'get') return key ? String(vars[key]) : '';
        if (key) delete vars[key];
        if (value) vars[name] = value;
        data[scope] = vars;
        fs.writeFileSync(fake, JSON.stringify(data, null, 2) + '\n', 'utf8');
        return '';
    }
    const script = op === 'get'
        ? '[Console]::OutputEncoding = [Text.Encoding]::UTF8; [Console]::Out.Write([Environment]::GetEnvironmentVariable($env:XO_ENV_NAME, $env:XO_ENV_SCOPE))'
        : '$v = $env:XO_ENV_VALUE; if (-not $v) { $v = $null }; [Environment]::SetEnvironmentVariable($env:XO_ENV_NAME, $v, $env:XO_ENV_SCOPE)';
    const env = Object.assign({}, process.env, { XO_ENV_NAME: name, XO_ENV_SCOPE: scope, XO_ENV_VALUE: value || '' });
    const r = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script],
        { encoding: 'utf8', env, windowsHide: true });
    if (r.status !== 0) {
        if (op === 'get') {
            io.warn(`Could not read ${scope} environment variable ${name}: ${(r.stderr || (r.error && r.error.message) || '').trim()}`);
            return null;
        }
        throw new Error(`could not set ${scope} environment variable ${name}: ${(r.stderr || '').trim()}`);
    }
    return op === 'get' ? (r.stdout || '') : '';
}

function samePathEntry(a, b) {
    return a.replace(/[\\/]+$/, '').toLowerCase() === b.replace(/[\\/]+$/, '').toLowerCase();
}

function onPathValue(pathValue, dir) {
    return (pathValue || '').split(';').some((e) => e && samePathEntry(e, dir));
}

// -> true | false | null (persisted Path could not be read).
function nodeOnPersistedPath(dir) {
    const machine = winEnv('get', 'Machine', 'Path');
    const user = winEnv('get', 'User', 'Path');
    if (onPathValue(machine, dir) || onPathValue(user, dir)) return true;
    return machine === null || user === null ? null : false;
}

function installXocortexWin(xocortexPath, opts = {}) {
    const existing = winEnv('get', 'User', 'XOCORTEX_HOME');
    if (existing === null) {
        io.warn(`XOCORTEX_HOME left unchanged (current value unreadable). Set it to ${xocortexPath} via System Properties > Environment Variables.`);
        return 'unknown';
    }
    if (existing && samePathEntry(existing, xocortexPath)) {
        io.ok('XOCORTEX_HOME already set correctly (user environment)');
        return 'unchanged';
    }
    if (existing) {
        io.warn(`XOCORTEX_HOME is already set (user environment) with different value: ${existing}`);
        io.out('');
        const answer = io.prompt(`Replace with ${xocortexPath}? [y/N]`, 'n', opts);
        if (!/^[Yy]$/.test(answer)) {
            io.info('Skipping — XOCORTEX_HOME unchanged');
            return 'kept';
        }
    }
    winEnv('set', 'User', 'XOCORTEX_HOME', xocortexPath);
    io.ok(`Set user env XOCORTEX_HOME=${xocortexPath}`);
    return existing ? 'replaced' : 'added';
}

function uninstallXocortexWin() {
    if (!winEnv('get', 'User', 'XOCORTEX_HOME')) return false;
    winEnv('set', 'User', 'XOCORTEX_HOME', null);
    io.ok('Removed user env XOCORTEX_HOME');
    return true;
}

// Hooks inherit the persisted Machine/User Path (CoCo runs them via
// `powershell -NoProfile`), so a Path set only in a terminal does not reach them.
// Check only; the Path is never written. -> 'reachable' | 'missing' | 'unknown'
function checkNodePathWin(nodeDir = path.dirname(process.execPath)) {
    const on = nodeOnPersistedPath(nodeDir);
    if (on) {
        io.ok('node directory is on the persistent PATH (hooks will find it)');
        return 'reachable';
    }
    if (on === null) {
        io.warn('Could not read persisted Path; skipping the node PATH check.');
        return 'unknown';
    }
    io.warn(`node's directory (${nodeDir}) is not on the Machine/User PATH; CoCo hooks may not find it.`);
    io.info(`  Add ${nodeDir} via System Properties > Environment Variables (User Path), then restart Cortex Code.`);
    return 'missing';
}

// ---- install hints -----------------------------------------------------------

const TOOL_HINTS = {
    git: { label: 'Git', mac: 'brew install git   (no brew? https://brew.sh)', linux: 'https://git-scm.com/download/linux', win: 'winget install --id Git.Git -e', any: 'https://git-scm.com/downloads' },
    gh: { label: 'the GitHub CLI', mac: 'brew install gh   (no brew? https://brew.sh)', linux: 'https://github.com/cli/cli#installation', win: 'winget install --id GitHub.cli -e', any: 'https://cli.github.com' },
};

function printToolInstallGuidance(tool) {
    const h = TOOL_HINTS[tool] || { label: tool };
    io.fail(`${tool} not found. Install ${h.label}:`);
    if (process.platform === 'darwin') io.info(`  macOS:   ${h.mac}`);
    else if (process.platform === 'linux') io.info(`  Linux:   ${h.linux}`);
    else if (process.platform === 'win32') io.info(`  Windows: ${h.win}`);
    if (h.any) io.info(`  any:     ${h.any}`);
}

module.exports = {
    XOCORTEX_MARKER,
    NODE_PATH_MARKER,
    homeDir,
    snowflakeHome,
    defaultVault,
    shellName,
    detectShellRc,
    shellQuote,
    xocortexExportLine,
    nodeExportLine,
    hasXocortexExport,
    existingXocortexValue,
    stripXocortexLines,
    appendBlock,
    removeNodePathBlock,
    readNodeExportLine,
    installShellEnv,
    uninstallShellEnv,
    findOnPath,
    hookBasePath,
    installNodePathUnix,
    uninstallNodePathUnix,
    winEnv,
    onPathValue,
    nodeOnPersistedPath,
    installXocortexWin,
    uninstallXocortexWin,
    checkNodePathWin,
    printToolInstallGuidance,
};

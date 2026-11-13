#!/usr/bin/env node
'use strict';
// XO setup: register a git-clone plugin in Cortex Code's settings.json (a catalog
// install is registered by its installer), clear stale XO entries there, persist
// XOCORTEX_HOME when asked, put node on the hook PATH, and create git-backed
// vaults. setup.sh and setup.ps1 are launchers for this file.
const fs = require('fs');
const os = require('os');
const path = require('path');
const C = require('./hooks/_common.js');
const io = require('./setup/io');
const platform = require('./setup/platform');
const settings = require('./setup/settings');
const vault = require('./setup/vault');

const PLUGIN_DIR = __dirname;
const XO_DIR = path.dirname(PLUGIN_DIR);
const IS_WIN = io.IS_WIN;
const LAUNCHER = IS_WIN ? 'setup.ps1' : 'setup.sh';
const TARGETS = ['user-profile', 'workspace'];
const RULE = '═'.repeat(63);

// A catalog install (CLI managed dir or Desktop stage_cache) is already registered
// by the installer that put it there. Listing it in settings.json `plugins` too
// would pin Cortex Code to this folder, ahead of later managed installs.
function isCatalogInstall() {
    const cortex = path.join(platform.snowflakeHome(), 'cortex');
    return ['plugins', 'stage_cache'].some((d) => C.isInsideReal(path.join(cortex, d), PLUGIN_DIR));
}

// ---- arguments ----------------------------------------------------------------

const FLAG_KIND = {
    target: 'value', workspace: 'value', xocortex: 'value', owner: 'value', name: 'value',
    from: 'value', to: 'value', yes: 'bool', resume: 'bool', clean: 'bool',
};

const COMMAND_FLAGS = {
    init: ['target', 'workspace', 'owner', 'xocortex', 'yes', 'resume', 'clean'],
    install: ['target', 'workspace', 'xocortex', 'yes'],
    uninstall: ['target', 'workspace'],
    migrate: ['from', 'to', 'workspace', 'xocortex', 'yes'],
    status: ['target', 'workspace'],
    'vault-init': ['name', 'owner', 'yes'],
};

function parseArgs(args, allowed) {
    const opts = {};
    for (let i = 0; i < args.length; i++) {
        const arg = args[i];
        const m = /^--([a-z-]+)(?:=([\s\S]*))?$/.exec(arg);
        if (!m || !allowed.includes(m[1])) io.die(`Unknown option: ${arg}`);
        const key = m[1];
        if (FLAG_KIND[key] === 'bool') {
            if (m[2] !== undefined) io.die(`Option --${key} takes no value`);
            opts[key] = true;
            continue;
        }
        let value = m[2];
        if (value === undefined) {
            value = args[i + 1];
            if (value === undefined || value.startsWith('--')) io.die(`Missing value for --${key}`);
            i++;
        }
        if (value === '') io.die(`Missing value for --${key}`);
        opts[key] = value;
    }
    for (const key of ['target', 'from', 'to']) {
        if (opts[key] !== undefined && !TARGETS.includes(opts[key])) {
            io.die(`Invalid --${key}: ${opts[key]} (expected user-profile or workspace)`);
        }
    }
    // A leading ~ is expanded here: PowerShell and cmd do not, and --flag=~/x is not expanded by bash either.
    const expandHome = (p) => (/^~(?=$|[\\/])/.test(p) ? path.join(platform.homeDir(), p.slice(1)) : p);
    if (opts.workspace) opts.workspace = path.resolve(expandHome(opts.workspace));
    // Kept as given (not realpath'd): the --clean guard must see a symlink/junction as one.
    if (opts.xocortex) opts.xocortex = path.resolve(expandHome(opts.xocortex));
    if (opts.owner !== undefined && !/^[A-Za-z0-9][A-Za-z0-9-]*$/.test(opts.owner)) {
        io.die(`Invalid --owner: ${opts.owner} (GitHub logins use A-Z a-z 0-9 - and cannot start with -)`);
    }
    return opts;
}

// ---- helpers ------------------------------------------------------------------

function settingsFile(target, workspace) {
    if (target === 'workspace') return path.join(workspace || process.cwd(), '.snowflake', 'cortex', 'settings.json');
    return path.join(platform.snowflakeHome(), 'cortex', 'settings.json');
}

function guardSettings(file, fn) {
    try { return fn(); }
    catch (e) {
        if (e instanceof settings.SettingsError) io.die(`Could not parse ${file} (invalid JSON). Fix or remove it and re-run; the file was left unchanged.`);
        throw e;
    }
}

function banner(title, rule = RULE) {
    io.out('');
    io.out(rule);
    io.out(`  ${title}`);
    io.out(rule);
    io.out('');
}

function printTgrepNote() {
    io.info('tgrep (optional search index):');
    io.info('  XO uses Cortex Code\'s built-in tgrep index for faster search when it is');
    io.info('  available, and falls back to grep otherwise. tgrep auto-indexes repos');
    io.info('  under 1000 files; confirm it is on via the \'tgrep.enabled\' Cortex Code');
    io.info('  setting. It needs account access to the arctic-embed embedding model —');
    io.info('  a 403 there disables it. No action required; grep works either way.');
}

function printNextSteps() {
    io.out('');
    io.out('  Next: fully quit Cortex Code (File > Exit / Cmd+Q, not just close the window) and reopen it.');
    io.out('        Then open the MCP panel, click Connect on \'xo-ops\' and Trust it (its tools stay hidden until you do).');
    io.out(`        Verify with: ${LAUNCHER} status`);
    io.out('');
}

// win32 prerequisite checks (setup.ps1 Test-Prereqs).
function windowsChecks(includeNode) {
    if (includeNode) {
        io.ok(`node ${process.version} at ${process.execPath}`);
        platform.checkNodePathWin();
    }
    const g = io.run('git', ['--version']);
    if (g.ok) io.ok(g.stdout.trim());
    else io.warn('git not found: commit_wi and project vaults need it (https://git-scm.com/download/win)');
}

function persistXocortex(xocortexPath, opts) {
    // Persist the long form on win32 so 8.3 names (DCHAFF~1) never reach the env var.
    if (IS_WIN) platform.installXocortexWin(C.canonicalPath(xocortexPath), opts);
    else platform.installShellEnv(xocortexPath, opts);
}

// The settings file of the other target, if XO is registered there.
function registeredElsewhere(target, workspace) {
    const other = target === 'workspace' ? settingsFile('user-profile') : settingsFile('workspace', workspace);
    return settings.status(other, PLUGIN_DIR).state === 'registered' ? other : '';
}

// ---- commands -----------------------------------------------------------------

function cmdInstall(opts, showNext = true) {
    const target = opts.target || 'user-profile';
    const workspace = opts.workspace || process.cwd();
    const file = settingsFile(target, workspace);

    banner(`XO System — Install (${target})`);
    io.info(`Plugin: ${PLUGIN_DIR}`);
    io.info(`Settings: ${file}`);
    io.out('');

    if (IS_WIN) windowsChecks(false);

    const catalog = isCatalogInstall();
    if (catalog) {
        io.info('Catalog install: not adding to settings.json (the catalog install already registered it)');
        if (guardSettings(file, () => settings.unregister(file, PLUGIN_DIR)) === 'removed') {
            io.ok('Removed this catalog folder from settings.json (an older setup added it; it would pin Cortex Code to this copy)');
        }
    } else {
        const result = guardSettings(file, () => settings.register(file, PLUGIN_DIR));
        if (result === 'created') io.ok('Created settings.json with XO plugin registered');
        else if (result === 'already') io.ok('Plugin already registered in settings.json');
        else io.ok('Registered XO plugin in settings.json');
    }
    for (const stale of guardSettings(file, () => settings.removeOtherXo(file, PLUGIN_DIR))) {
        io.ok(`Removed stale XO entry from settings.json: ${stale}`);
    }

    if (opts.xocortex) persistXocortex(opts.xocortex, opts);
    else {
        fs.mkdirSync(platform.defaultVault(), { recursive: true });
        io.info(`No --xocortex given: XO will record to the default local vault ${platform.defaultVault()} (no XOCORTEX_HOME needed)`);
    }

    if (IS_WIN) platform.checkNodePathWin();
    else platform.installNodePathUnix(opts);

    io.out('');
    printTgrepNote();
    io.out('');

    if (target === 'workspace' && !catalog) {
        io.out('  ═══════════════════════════════════════════════════════════');
        io.warn('IMPORTANT: Add .snowflake/ to your .gitignore if not already present');
        io.out('  ═══════════════════════════════════════════════════════════');
    }

    io.out('');
    io.ok(catalog ? 'XO set up (catalog install, registered by the catalog installer)' : `XO installed to ${target}`);
    if (showNext) printNextSteps();
    else io.out('');
}

function cmdUninstall(opts) {
    const target = opts.target || 'user-profile';
    const workspace = opts.workspace || process.cwd();
    const file = settingsFile(target, workspace);

    banner(`XO System — Uninstall (${target})`);

    const result = guardSettings(file, () => settings.unregister(file, PLUGIN_DIR));
    if (result === 'no-file') io.info('No settings.json found');
    else if (result === 'absent') io.info(isCatalogInstall() ? 'Catalog install: nothing to remove from settings.json' : `Plugin was not registered in ${file}`);
    else io.ok('Removed XO plugin from settings.json');

    if (guardSettings(file, () => settings.removeLegacyEnv(file))) {
        io.ok('Removed legacy XOCORTEX_HOME from settings.json (it never reached hooks)');
    }

    const other = registeredElsewhere(target, workspace);
    if (target === 'user-profile') io.info(`Checked ${settingsFile('workspace', workspace)} for another XO registration; other workspaces are not scanned.`);
    if (other) {
        io.info(`XO is still registered in ${other}; keeping XOCORTEX_HOME and the node PATH entry`);
    } else if (IS_WIN) {
        platform.uninstallXocortexWin();
    } else {
        platform.uninstallShellEnv(opts);
        platform.uninstallNodePathUnix(opts);
    }

    io.out('');
    if (isCatalogInstall()) {
        io.ok('XO setup removed; the plugin itself stays installed (remove it from the Desktop Plugins panel or with `cortex plugin remove xo`)');
    } else {
        io.ok(`XO uninstalled from ${target}`);
    }
    io.out('');
}

function cmdMigrate(opts) {
    const { from, to } = opts;
    if (!from || !to) io.die(`Usage: ${LAUNCHER} migrate --from <target> --to <target>`);
    if (from === to) io.die('Cannot migrate to the same target');
    if (isCatalogInstall()) io.die('migrate moves a git-clone registration between targets; a catalog install has no settings.json registration to move');

    banner(`XO System — Migrate (${from} → ${to})`);

    // Install first so the uninstall below sees the new target and keeps the
    // profile-wide XOCORTEX_HOME and node PATH entry.
    io.info(`Installing to ${to}...`);
    io.out('');
    cmdInstall({ target: to, workspace: opts.workspace, xocortex: opts.xocortex, yes: opts.yes }, false);

    io.info(`Uninstalling from ${from}...`);
    io.out('');
    cmdUninstall({ target: from, workspace: opts.workspace });

    io.out('');
    io.ok(`Migration complete: ${from} → ${to}`);
    printNextSteps();
}

function checkInstallation(file, label) {
    io.info(`── ${label} ──`);

    const { state, data } = settings.status(file, PLUGIN_DIR);
    if (state === 'registered') {
        if (isCatalogInstall()) io.warn(`Plugin: this catalog folder is listed in settings.json (${PLUGIN_DIR}), which pins Cortex Code to it — re-run ${LAUNCHER} install to clear it`);
        else io.ok(`Plugin: registered (${PLUGIN_DIR})`);
    }
    else if (state === 'not-registered') {
        if (isCatalogInstall()) io.ok(`Plugin: catalog install (${PLUGIN_DIR}); not listed in settings.json, as expected`);
        else io.info('Plugin: not registered in settings.json');
    }
    else if (state === 'invalid') io.warn(`Plugin: settings.json is not valid JSON (${file})`);
    else io.info('Plugin: no settings.json');
    for (const other of settings.otherXoEntries(data, PLUGIN_DIR)) {
        io.warn(`settings.json points Cortex Code at another XO copy (${other}); it can take precedence over this install — re-run ${LAUNCHER} install to clear it`);
    }

    // Only the env var the hooks actually receive counts; settings.json env does not reach them.
    const xocortexHome = process.env.XOCORTEX_HOME || (IS_WIN ? platform.winEnv('get', 'User', 'XOCORTEX_HOME') : '');
    if (xocortexHome) {
        if (fs.existsSync(xocortexHome)) io.ok(`Memory repo (XOCORTEX_HOME): ${xocortexHome}`);
        else io.warn(`Memory repo configured but not found: ${xocortexHome}`);
    } else {
        io.info(`XOCORTEX_HOME: not set (XO uses the default local vault at ${platform.defaultVault()})`);
    }

    if (data && data.env && typeof data.env === 'object' && data.env.XOCORTEX_HOME != null) {
        io.warn(`Legacy .env.XOCORTEX_HOME in settings.json — this never reaches hooks; run uninstall to remove it, then set XOCORTEX_HOME with install --xocortex <path>`);
    }

    const baseDir = path.dirname(file);
    const legacyAgents = path.join(baseDir, 'AGENTS.md');
    try {
        if (fs.readFileSync(legacyAgents, 'utf8').includes('XO:START')) {
            io.warn(`Legacy AGENTS.md found at ${legacyAgents} — safe to delete (directives now delivered by plugin hooks)`);
        }
    } catch (_) {}

    const agentsDir = path.join(baseDir, 'agents');
    let broken = 0;
    try {
        for (const name of fs.readdirSync(agentsDir)) {
            if (!/^xo-.*\.agent\.md$/.test(name)) continue;
            const p = path.join(agentsDir, name);
            if (fs.lstatSync(p).isSymbolicLink() && !fs.existsSync(p)) broken++;
        }
    } catch (_) {}
    if (broken > 0) {
        io.warn(`${broken} broken xo-* agent symlinks in ${agentsDir} — delete them: rm ${path.join(agentsDir, 'xo-*.agent.md')}`);
    }

    if (data && data.hooks !== undefined && data.hooks !== null && data.hooks !== false) {
        io.warn('Legacy hooks{} block found in settings.json — remove it (hooks now delivered by plugin)');
    }

    io.info('tgrep index: optional; XO uses it when on (\'tgrep.enabled\' setting) and falls back to grep otherwise');
}

function printHookLogTail() {
    const day = new Date().toISOString().slice(0, 10);
    // Same base as LOG_DIR in hooks/_common.js (os.homedir(); homeDir() equals it outside tests).
    const log = path.join(platform.homeDir(), '.snowflake', 'cortex', 'logs', `${day}-hooks.log`);
    if (!fs.existsSync(log)) {
        io.info('No hook log for today yet (hooks have not fired today)');
        return;
    }
    io.ok(`Hook log present: ${log}`);
    const lines = fs.readFileSync(log, 'utf8').split(/\r?\n/).filter((l) => l !== '');
    for (const l of lines.slice(-3)) io.out(`         ${l}`);
}

function cmdStatus(opts) {
    banner('XO System — Status');
    io.info(`XO source: ${XO_DIR}`);
    io.info(`Plugin: ${PLUGIN_DIR}`);
    io.out('');

    if (!opts.target || opts.target === 'user-profile') {
        const file = settingsFile('user-profile');
        checkInstallation(file, `User Profile (${path.dirname(file)})`);
        io.out('');
    }
    if (opts.target === 'workspace') {
        const workspace = opts.workspace || process.cwd();
        checkInstallation(settingsFile('workspace', workspace), `Workspace (${workspace})`);
        io.out('');
    }

    if (IS_WIN) {
        io.info('── Prerequisites ──');
        windowsChecks(true);
        io.out('');
    }
    printHookLogTail();
    io.out('');

    io.info('── Project vault (current directory) ──');
    try {
        const r = C.resolveEffectiveVault(process.cwd());
        const source = r.source === 'fallback' ? 'default local vault' : r.source;
        io.ok(`Effective vault: ${r.vault}`);
        io.out(`    Source: ${source}${r.repoRoot ? ` (repo-root: ${r.repoRoot})` : ''}`);
        if (C.isGitBacked(r.vault)) io.out('    Storage: git-backed');
        else io.out(`    Storage: local (optional: ${LAUNCHER} vault-init, or git init the vault, to version it)`);
    } catch (e) {
        io.warn(`Could not resolve project vault (${e.message})`);
    }
    io.out('');
}

// Dies unless dir is a plain, non-root directory that holds no home, repo,
// plugin or cwd, and (when not a git repo) only scaffold entries.
const SCAFFOLD_ENTRIES = ['diary', 'notes', 'tasks', 'tmp', '.gitignore', 'README.md', '.git'];

function assertSafeToClean(dir) {
    const target = path.resolve(dir);
    const refuse = (why) => io.die(`Refusing to remove ${target}: ${why}`);
    const st = fs.lstatSync(target);
    if (st.isSymbolicLink() || !st.isDirectory()) refuse('not a plain directory (symlink, junction or file)');
    if (target === path.parse(target).root) refuse('filesystem root');
    // canonicalPath on both sides: 8.3 short names and case cannot bypass the containment check.
    const norm = (p) => { const c = C.canonicalPath(path.resolve(p)); return IS_WIN ? c.toLowerCase() : c; };
    for (const p of [os.homedir(), XO_DIR, PLUGIN_DIR, process.cwd()]) {
        const rel = path.relative(norm(target), norm(p));
        if (!rel.startsWith('..') && !path.isAbsolute(rel)) refuse(`it is or contains ${p}`);
    }
    if (!fs.existsSync(path.join(target, '.git'))) {
        const extra = fs.readdirSync(target).filter((e) => !SCAFFOLD_ENTRIES.includes(e));
        if (extra.length) refuse(`not a git repository and holds non-vault entries (${extra.slice(0, 5).join(', ')})`);
    }
    return target;
}

// -> 'installed' | 'resume' | 'clean'; dies on abort.
function handleExistingVaultDir(dir, opts) {
    if (!fs.existsSync(path.join(dir, '.git'))) {
        io.warn(`${dir} already exists but is not a git repository.`);
    } else if (vault.hasCommits(dir) && vault.hasRemote(dir)) {
        io.info(`Found existing git-backed xocortex vault at ${dir}`);
        io.out('');
        io.info('Running install against the existing vault...');
        io.out('');
        return 'installed';
    } else {
        io.warn(`${dir} exists but looks partial or poisoned.`);
        if (!vault.hasCommits(dir)) io.info('  Missing: at least one commit');
        if (!vault.hasRemote(dir)) io.info('  Missing: a git remote');
    }

    io.out('');
    let action;
    if (opts.clean) action = 'c';
    else if (opts.resume) action = 'r';
    else action = io.prompt('Resume this init, clean up the directory, or abort? [r/c/a]:', '', opts);

    if (/^r$/i.test(action)) {
        io.info('Resuming with the existing directory...');
        return 'resume';
    }
    if (/^(c|clean)$/i.test(action)) {
        const resolved = assertSafeToClean(dir);
        fs.rmSync(resolved, { recursive: true, force: true });
        io.ok(`Removed ${dir}`);
        return 'clean';
    }
    if (/^a$/i.test(action)) io.die('Init aborted.');
    return io.die('Init aborted. Directory left unchanged.');
}

function printManualRepoSteps(dir, slug, url) {
    io.info(`  cd ${dir}`);
    io.info(`  gh repo create ${slug} --private`);
    io.info(`  git remote add origin ${url}`);
    io.info(`  git push -u origin ${vault.currentBranch(dir)}`);
}

function cmdInit(opts) {
    const target = opts.target || 'user-profile';

    banner('XO System — First-Time Setup');
    io.out('  This script will:');
    io.out('    1. Create your private xocortex memory repository');
    io.out('    2. Create a PRIVATE GitHub repo for that vault');
    io.out(isCatalogInstall()
        ? '    3. Leave plugin registration to the catalog install (nothing added to settings.json)'
        : `    3. Register the XO plugin in your ${target === 'workspace' ? 'workspace' : 'user profile'}`);
    io.out('');
    io.out('  Privacy: Your repo will be visible ONLY to you.');
    io.out('');

    const { ghUser, owner } = vault.preflight(opts.owner, opts, LAUNCHER, 'init');
    const slug = `${owner}/${vault.vaultRepoName(owner, ghUser)}`;
    const url = vault.repoUrl(slug);

    let dir = opts.xocortex;
    if (!dir) {
        dir = path.join(platform.homeDir(), 'xocortex');
        io.info(`No --xocortex given; defaulting the vault to ${dir}`);
        io.out('');
    }
    const installOpts = { target, workspace: opts.workspace, xocortex: dir, yes: opts.yes };

    if (fs.existsSync(dir)) {
        if (handleExistingVaultDir(dir, opts) === 'installed') return cmdInstall(installOpts);
        io.out('');
    }

    // Cloning is offered only when the directory does not exist (never clone into it).
    if (!fs.existsSync(dir) && vault.repoExists(slug)) {
        io.info(`Found existing repo: ${slug}`);
        io.out('');
        const answer = io.prompt('Clone it instead of creating or resuming local setup? [Y/n]:', 'y', opts);
        if (!/^[Nn]$/.test(answer)) {
            io.out('');
            io.info('Cloning existing repository...');
            vault.prepareTransport(url);
            const r = io.run('git', ['clone', url, dir]);
            if (!r.ok) io.die(`git clone failed: ${(r.stderr || '').trim()}`);
            io.ok(`Cloned to ${dir}`);
            io.out('');
            io.info('Installing XO...');
            io.out('');
            return cmdInstall(installOpts);
        }
    }

    io.info('Preparing xocortex directory structure...');
    vault.writeScaffold(dir, owner, ghUser);
    io.ok('Directory structure ready');
    io.out('');

    vault.initRepo(dir);
    io.out('');

    banner('Creating Private GitHub Repository');
    io.info(`Owner: ${vault.vaultOwnerLabel(owner, ghUser)}`);
    io.info(`Repository: ${slug}`);
    io.info('Visibility: PRIVATE (only you can see it)');
    io.out('');

    const create = io.prompt('Create and push now? [Y/n]:', 'y', opts);
    if (/^[Nn]$/.test(create)) {
        io.out('');
        io.info('Skipped. To set up GitHub later:');
        io.out('');
        printManualRepoSteps(dir, slug, url);
        io.out('');
    } else {
        io.out('');
        if (vault.repoExists(slug)) {
            io.info('Repository already exists on GitHub.');
            vault.prepareTransport(url);
            vault.ensureOrigin(dir, url);
            const push = vault.pushHead(dir);
            if (push.ok) {
                io.ok('Pushed local vault to existing GitHub repository');
            } else {
                io.fail(`Failed to push to existing repo: ${slug}`);
                vault.explainPushFailure(dir, push);
            }
        } else {
            io.info('Creating private repository...');
            if (vault.createRepo(slug)) {
                vault.prepareTransport(url);
                vault.ensureOrigin(dir, url);
                const push = vault.pushHead(dir);
                if (push.ok) {
                    io.ok('GitHub repository created and pushed');
                } else {
                    io.fail(`Created ${slug}, but the push failed:`);
                    vault.explainPushFailure(dir, push);
                }
            } else {
                io.out('');
                io.fail(`Failed to create ${slug}. Check your gh CLI auth:`);
                io.info('  gh auth status');
                io.out('');
                io.info('Then manually create:');
                printManualRepoSteps(dir, slug, url);
            }
        }
    }

    io.out('');
    io.info('Installing XO...');
    io.out('');
    return cmdInstall(installOpts);
}

function cmdVaultInit(opts) {
    const top = io.run('git', ['-C', process.cwd(), 'rev-parse', '--show-toplevel']);
    if (!top.ok || !top.stdout.trim()) {
        io.die('vault-init must be run from inside the git repo you want to opt in (no .git found).');
    }
    // Canonical form so the registry key matches the hooks' resolveRepoRoot.
    const repoRoot = C.canonicalPath(top.stdout.trim());
    const requested = opts.name || path.basename(repoRoot);
    const rule = '═'.repeat(64);

    banner('XO — Initialise project vault', rule);
    io.info(`Repo root:   ${repoRoot}`);
    io.info(`Vault name:  ${requested} (requested)`);
    io.out('');

    const { ghUser, owner } = vault.preflight(opts.owner, opts, LAUNCHER, 'vault-init');

    const name = C.sanitiseVaultName(requested);
    if (!name) io.die(`Invalid vault name: '${requested}' (allowed: a-z 0-9 . _ - ; not empty / all-dots).`);
    const dir = C.resolveVaultDir(name);
    if (!dir) io.die(`Could not resolve a safe vault dir for '${name}' under the vaults root.`);

    const slug = `${owner}/${name}-xo`;
    const url = vault.repoUrl(slug);

    io.info(`Vault name:  ${name}`);
    io.info(`Vault dir:   ${dir}`);
    io.info(`GitHub repo: ${slug} (PRIVATE)`);
    io.out('');

    if (fs.existsSync(path.join(dir, '.git')) && vault.hasCommits(dir)) {
        io.info(`Vault already initialised at ${dir}`);
    } else {
        io.info('Scaffolding vault structure...');
        vault.writeScaffold(dir, owner, ghUser);
        io.ok('Vault structure ready');
        vault.initRepo(dir);
    }
    io.out('');

    if (vault.repoExists(slug)) {
        io.info(`GitHub repo already exists: ${slug}`);
        vault.prepareTransport(url);
        vault.ensureOrigin(dir, url);
        const push = vault.pushHead(dir);
        if (push.ok) io.ok(`Pushed to ${slug}`);
        else { io.warn(`Could not push to ${slug}:`); vault.explainPushFailure(dir, push); }
    } else {
        io.info(`Creating private GitHub repo ${slug}...`);
        if (vault.createRepo(slug)) {
            vault.prepareTransport(url);
            vault.ensureOrigin(dir, url);
            const push = vault.pushHead(dir);
            if (push.ok) io.ok(`Created and pushed ${slug}`);
            else { io.warn(`Created ${slug}, but the push failed:`); vault.explainPushFailure(dir, push); }
        } else {
            io.warn(`Could not create ${slug} automatically. Create it manually:`);
            io.info(`  gh repo create ${slug} --private`);
            io.info(`  git -C ${dir} remote add origin ${url}`);
            io.info(`  git -C ${dir} push -u origin HEAD`);
        }
    }
    io.out('');

    io.info(`Registering ${repoRoot} -> vault '${name}'...`);
    let reg;
    try { reg = C.writeRegistryEntry(repoRoot, name); }
    catch (_) { io.die(`Failed to write registry entry for ${repoRoot}.`); }
    if (reg.collidesWith) {
        io.warn(`Vault name '${name}' is already registered to a different repo-root (${reg.collidesWith}); both will share one vault. Re-run with --name <unique> to avoid comingling.`);
    } else {
        io.ok('Registered in vaults.json');
    }

    io.out('');
    io.ok(`Project vault ready. New XO sessions started from ${repoRoot} will record to '${name}'.`);
    io.info('This opt-in is machine-local (registry). To opt in every contributor of this repo,');
    io.info('commit a .xocortex marker at the repo root (see SETUP.md). A local registry entry overrides it.');
    io.out('');
}

function cmdHelp() {
    const s = LAUNCHER;
    for (const l of [
        '',
        `Usage: ${s} <command> [options]`,
        '',
        'Commands:',
        '  init        First-time setup: create xocortex repo and register XO plugin',
        '  install     Register XO plugin (and set XOCORTEX_HOME when --xocortex is given)',
        '  uninstall   Remove XO from a target',
        '  migrate     Move XO between user-profile and workspace',
        '  status      Check XO installation health',
        '  vault-init  Create a git-backed project vault for the current repo and register it',
        '  help        Show this help',
        '',
        'Options (--opt value or --opt=value):',
        '  --target <user-profile|workspace>   Install target (default: user-profile)',
        '  --workspace <path>                  Workspace path (default: current directory)',
        '  --owner <github-owner>              Vault owner for init / vault-init (default: your gh user)',
        '  --xocortex <path>                   Path to xocortex memory repo',
        '  --name <vault-name>                 Project vault name for vault-init (default: repo basename)',
        '  --from <target> --to <target>       Source and destination for migrate',
        '  --yes                               Accept every prompt default without asking',
        '  --resume | --clean                  init: resume in, or remove, an existing partial vault dir',
        '',
        'Examples:',
        `  ${s} init                                  # First-time setup`,
        `  ${s} init --target workspace               # First-time setup, install to workspace`,
        `  ${s} install                               # Register plugin in user profile`,
        `  ${s} install --xocortex ~/xocortex          # Install + set memory repo path`,
        `  ${s} install --target workspace             # Register plugin in current workspace`,
        `  ${s} uninstall --target workspace           # Remove from workspace`,
        `  ${s} migrate --from workspace --to user-profile`,
        `  ${s} status`,
        `  ${s} vault-init                            # Opt the current repo into its own project vault`,
        `  ${s} vault-init --name acme                # ...with an explicit (shareable) vault name`,
        '',
    ]) io.out(l);
}

const COMMANDS = {
    init: cmdInit,
    install: cmdInstall,
    uninstall: cmdUninstall,
    migrate: cmdMigrate,
    status: cmdStatus,
    'vault-init': cmdVaultInit,
};

// -> exit code
function main(argv) {
    const [cmd = 'help', ...rest] = argv;
    try {
        if (cmd === 'help' || cmd === '--help' || cmd === '-h') {
            cmdHelp();
            return 0;
        }
        if (!COMMANDS[cmd]) io.die(`Unknown command: ${cmd}. Run '${LAUNCHER} help' for usage.`);
        const overrides = ['XO_SETUP_HOME', 'XO_SETUP_GH', 'XO_SETUP_GIT', 'XO_SETUP_WINENV'].filter((k) => process.env[k]);
        if (overrides.length) io.warn(`Test overrides active: ${overrides.join(', ')}`);
        COMMANDS[cmd](parseArgs(rest, COMMAND_FLAGS[cmd]));
        return 0;
    } catch (e) {
        if (e instanceof io.DieError) return 1;
        io.fail(e && e.message ? e.message : String(e));
        return 1;
    }
}

module.exports = {
    PLUGIN_DIR,
    LAUNCHER,
    parseArgs,
    settingsFile,
    registeredElsewhere,
    cmdInit,
    cmdInstall,
    cmdUninstall,
    cmdMigrate,
    cmdStatus,
    cmdVaultInit,
    cmdHelp,
    main,
};

// setup.ps1 passes argv as JSON: Windows PowerShell 5.1 splits a native-command
// argument such as `--xocortex=C:\v a` at the space, so the command line cannot carry it.
function launcherArgv() {
    const json = process.env.XO_SETUP_ARGV_JSON;
    if (json === undefined) return process.argv.slice(2);
    delete process.env.XO_SETUP_ARGV_JSON;
    const parsed = (() => { try { return JSON.parse(json); } catch (_) { return null; } })();
    if (!Array.isArray(parsed) || !parsed.every((a) => typeof a === 'string')) io.die('XO_SETUP_ARGV_JSON must be a JSON array of strings');
    return parsed;
}

if (require.main === module) {
    try { process.exitCode = main(launcherArgv()); } catch (e) { if (!(e instanceof io.DieError)) throw e; process.exitCode = 1; }
}

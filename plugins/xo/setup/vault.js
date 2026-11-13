'use strict';
// Vault scaffold, git init/commit/remote, and the gh preflight shared by init
// and vault-init.
const fs = require('fs');
const path = require('path');
const io = require('./io');
const platform = require('./platform');

function git(dir, args) { return io.run('git', ['-C', dir].concat(args)); }
function gh(args) { return io.run('gh', args); }

function vaultRepoName(owner, ghUser) {
    return owner === ghUser ? 'xocortex' : `xocortex-${ghUser}`;
}

function vaultOwnerLabel(owner, ghUser) {
    return owner === ghUser ? `${owner} account` : `${owner} org`;
}

const README = `# xocortex

Private operator memory vault. Part of the XO system.

## Structure

- \`diary/\` — Daily checkpoint logs
- \`notes/\` — Deep investigation notes
- \`tasks/\` — Work item tracking

## New Machine Setup

Clone the coco-skills repo and run the plugin's setup script. \`init\` creates (or clones) this vault, registers the XO plugin in your Cortex \`settings.json\`, and sets \`XOCORTEX_HOME\`:

\`\`\`bash
git clone https://github.com/Snowflake-Labs/coco-skills.git
coco-skills/plugins/xo/setup.sh init        # Windows: powershell -NoProfile -ExecutionPolicy Bypass -File coco-skills\\plugins\\xo\\setup.ps1 init
\`\`\`

The plugin is delivered via \`settings.json\`. Run \`coco-skills/plugins/xo/setup.sh status\` any time to check installation health.

## Privacy

This repo is PRIVATE in the VAULT_OWNER_LABEL on GitHub.
Only you (the owner) can access it.
`;

function writeScaffold(dir, owner, ghUser) {
    for (const sub of ['diary', 'notes', path.join('tasks', 'current'), path.join('tasks', 'archive')]) {
        fs.mkdirSync(path.join(dir, sub), { recursive: true });
    }
    const files = {
        '.gitignore': 'tmp/\n.DS_Store\n.snowflake/\n',
        'README.md': README.split('VAULT_OWNER_LABEL').join(vaultOwnerLabel(owner, ghUser)),
    };
    for (const [name, content] of Object.entries(files)) {
        const file = path.join(dir, name);
        if (!fs.existsSync(file)) fs.writeFileSync(file, content, 'utf8');
    }
}

function hasCommits(dir) { return git(dir, ['rev-parse', '--verify', 'HEAD']).ok; }

function hasRemote(dir) {
    if (!fs.existsSync(path.join(dir, '.git'))) return false;
    const r = git(dir, ['remote']);
    return r.ok && r.stdout.trim() !== '';
}

function currentBranch(dir) {
    const r = git(dir, ['branch', '--show-current']);
    return (r.ok && r.stdout.trim()) || 'HEAD';
}


function checked(r, what) {
    if (!r.ok) io.die(`${what} failed: ${(r.stderr || r.stdout || (r.error && r.error.message) || '').trim()}`);
    return r;
}

function initRepo(dir) {
    if (!fs.existsSync(path.join(dir, '.git'))) {
        io.info('Initializing git repository...');
        checked(git(dir, ['init', '-q']), 'git init');
    } else {
        io.info('Git repository already present.');
    }
    if (hasCommits(dir)) {
        io.ok('Git commit already present');
        return;
    }
    io.info('Creating initial commit...');
    checked(git(dir, ['add', '.']), 'git add');
    checked(git(dir, ['commit', '-q', '-m', 'Initial xocortex setup']), 'git commit');
    io.ok('Git initialized');
}

// owner/repo (lowercase) from an SSH or https GitHub URL; '' if not GitHub.
function githubSlug(url) {
    const m = /github\.com[:/]([^/]+\/[^/]+?)(\.git)?\/?$/i.exec(url || '');
    return m ? m[1].toLowerCase() : '';
}

// Sets origin to url. Same repo over the other transport -> switched; a
// different repo -> kept, with a warning naming both (never repointed silently).
function ensureOrigin(dir, url) {
    const cur = git(dir, ['config', '--get', 'remote.origin.url']);
    const current = cur.ok ? cur.stdout.trim() : '';
    if (!current) {
        checked(git(dir, ['remote', 'add', 'origin', url]), 'git remote add');
        io.ok('Added origin remote');
        return true;
    }
    if (current === url) return true;
    if (githubSlug(current) && githubSlug(current) === githubSlug(url)) {
        checked(git(dir, ['remote', 'set-url', 'origin', url]), 'git remote set-url');
        io.ok(`Switched origin to ${url} (was ${current})`);
        return true;
    }
    io.warn(`origin is ${current}, not ${url}; left unchanged. Pushing to the existing origin.`);
    return false;
}

function pushHead(dir) { return git(dir, ['push', '-u', 'origin', 'HEAD']); }

// Creates the empty private repo only; the caller sets origin and pushes, so a
// create failure and a push failure are reported separately.
function createRepo(slug) { return gh(['repo', 'create', slug, '--private']).ok; }

// Remote URL in the transport gh is configured for (gh auth login choice).
// Empty or failed lookup -> SSH (the previous fixed behaviour).
function repoUrl(slug) {
    const r = gh(['config', 'get', 'git_protocol', '-h', 'github.com']);
    return r.ok && r.stdout.trim() === 'https'
        ? `https://github.com/${slug}.git`
        : `git@github.com:${slug}.git`;
}

// For https remotes, gh must be git's credential helper (this also covers SAML-SSO
// orgs). That edits the user's global git config, so it is announced and run only
// immediately before a clone or push.
function prepareTransport(url) {
    if (!/^https:/.test(url)) return;
    io.info('Configuring gh as the git credential helper for github.com (gh auth setup-git; edits global git config)...');
    if (!gh(['auth', 'setup-git', '-h', 'github.com']).ok) {
        io.warn('gh auth setup-git failed; https clone/push may prompt for credentials.');
    }
}

// Prints the manual push command and, when GitHub refused for SAML SSO, how to fix it.
function explainPushFailure(dir, result) {
    const err = ((result && result.stderr) || '').trim();
    if (/SAML|SSO/i.test(err)) {
        io.info('  The org enforces SAML SSO for this key. Either authorise the SSH key for the org');
        io.info('  (github.com/settings/keys > Configure SSO), or switch gh to https and re-run setup:');
        io.info('    gh config set git_protocol https -h github.com');
    } else if (err) {
        io.info(`  ${err.split(/\r?\n/).slice(-1)[0]}`);
    }
    io.info(`  Then push: git -C ${dir} push -u origin HEAD`);
}

function repoExists(slug) { return gh(['repo', 'view', slug]).ok; }

// Checks git, gh, git identity, gh auth and owner access. Dies listing every gap.
// -> { ghUser, owner }
function preflight(requestedOwner, opts, launcher, command) {
    let gaps = 0;
    let ghUser = '';
    let owner = '';

    io.info('Checking prerequisites...');
    io.out('');

    const haveGit = io.have('git');
    const haveGh = io.have('gh');
    for (const [tool, present] of [['git', haveGit], ['gh', haveGh]]) {
        if (!present) {
            platform.printToolInstallGuidance(tool);
            io.out('');
            gaps++;
        }
    }

    if (haveGit) {
        const name = io.run('git', ['config', 'user.name']).stdout.trim();
        const email = io.run('git', ['config', 'user.email']).stdout.trim();
        if (!name || !email) {
            io.fail('Git identity is not configured.');
            io.info('  Run:');
            if (!name) io.info('    git config --global user.name "Your Name"');
            if (!email) io.info('    git config --global user.email "you@example.com"');
            io.out('');
            gaps++;
        }
    }

    if (haveGh) {
        if (gh(['auth', 'status']).ok) {
            const r = gh(['api', 'user', '--jq', '.login']);
            ghUser = r.ok ? r.stdout.trim() : '';
            if (!ghUser) {
                io.fail('Could not determine your GitHub username from gh.');
                io.info('  Run:');
                io.info('    gh auth login');
                io.out('');
                gaps++;
            }
        } else {
            io.fail('GitHub CLI is not authenticated.');
            io.info('  Run:');
            io.info('    gh auth login');
            io.out('');
            gaps++;
        }
    }

    if (ghUser) {
        owner = requestedOwner
            || io.prompt(`GitHub owner for the private vault (your account or an org) [${ghUser}]:`, ghUser, opts);
        if (owner !== ghUser && !gh(['api', `orgs/${owner}`, '--jq', '.login']).ok) {
            io.fail(`Cannot access GitHub org: ${owner}`);
            io.info('  Run:');
            io.info('    gh auth login');
            io.info(`  Then authorize access for ${owner} in the browser flow and re-run ${launcher} ${command}.`);
            io.out('');
            gaps++;
        }
    }

    if (gaps > 0) io.die(`Fix the prerequisite gaps above and re-run ${launcher} ${command}.`);

    owner = owner || ghUser;
    io.ok('Prerequisites OK');
    io.ok(`GitHub user: ${ghUser}`);
    io.ok(owner === ghUser ? `Vault owner: ${owner} (personal account)` : `Vault owner: ${owner} (org)`);
    io.out('');
    return { ghUser, owner };
}

module.exports = {
    git,
    gh,
    vaultRepoName,
    vaultOwnerLabel,
    writeScaffold,
    hasCommits,
    hasRemote,
    currentBranch,
    initRepo,
    ensureOrigin,
    pushHead,
    createRepo,
    repoUrl,
    prepareTransport,
    githubSlug,
    explainPushFailure,
    repoExists,
    preflight,
};

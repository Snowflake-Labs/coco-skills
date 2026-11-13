'use strict';
// Bash-hang guard pattern set. Pure, dependency-free, testable.
//
// Each pattern detects a bash construction that hangs the Cortex Code Desktop
// terminal (it feeds commands to an interactive shell, so anything left awaiting
// input on a later line hangs) and carries an instructional fix so the agent
// self-corrects. The guard only detects + blocks; it never rewrites the command.
// Safe variants are allowed on purpose (<<< here-strings, --body-file,
// git commit -F, lone sleep).
//
// NOTE: an earlier 'bang-in-double-quotes' pattern (a literal ! inside a
// double-quoted string -> zsh history-expansion hang) was dropped from the
// enforcing set: too broad a false-positive surface for a hard block. That
// hazard remains documented as advisory guidance, not enforced here.
//
// SCOPE: this is a LIVING known-offenders list, not an exhaustive/minimal set.
// The underlying failure is one mechanism — the Desktop feeds commands to an
// interactive shell, so anything that leaves it awaiting input on a later line
// hangs (heredoc terminator, a quote opened on one line and closed on another,
// an editor/prompt-opening command). These patterns target the highest-FREQUENCY
// instances (specific commands) rather than the general rule ("any multi-line
// quoted argument"), which would over-block. Known-uncovered vectors therefore
// exist: multi-line quoted args to other commands (osascript -e, psql -c, sed/awk
// scripts), unusual heredoc terminators, etc. Extend it the same way: when a
// real hang gets through, add the specific pattern that catches it.

// Each entry: { label, fix, test(command) -> boolean }.
// First match wins; order structural/specific patterns before broad ones.
const PATTERNS = [
    {
        label: 'heredoc',
        fix: 'Heredocs hang the Desktop terminal. Write the content to a temp file (write tool or a single-line echo) and use -F / --body-file, or run `bash <file>`.',
        // <<EOF / <<'EOF' / <<-EOF ; excludes <<< here-strings.
        test: (c) => /(?<!<)<<-?\s*['"]?[A-Za-z_][A-Za-z0-9_]*['"]?(?!<)/.test(c),
    },
    {
        label: 'multiline-git-commit-m',
        fix: 'A newline inside `git commit -m "..."` hangs the terminal. Write the message to a file and use `git commit -F <file>`.',
        // Require the newline to sit INSIDE the quoted message, matched per quote
        // style so an apostrophe in a double-quoted message (e.g. "it's done\n...")
        // is still caught. Known limitation: only a `-m` flag immediately after
        // `commit` is matched; `-am` / `-a -m` / `--amend -m` are not.
        test: (c) => /git\s+commit\s+-m\s+"[^"]*\n[^"]*"/.test(c)
            || /git\s+commit\s+-m\s+'[^']*\n[^']*'/.test(c),
    },
    {
        label: 'gh-pr-inline-body',
        fix: 'Inline `--body "..."` on gh pr hangs the terminal. Write the body to a file and use `--body-file <file>`.',
        test: (c) => /gh\s+pr\s+(create|comment|edit|review)\s+[^|]*--body\s+['"]/.test(c),
    },
    {
        label: 'multiline-python-c',
        fix: 'A newline inside `python -c "..."` hangs the terminal. Write the script to a file and run `python3 <file>.py`.',
        // Require the newline INSIDE the quoted script (matched per quote style) so a
        // safe single-line `python3 -c "print(1)"` chained with a later command via a
        // newline is NOT falsely blocked.
        test: (c) => /python3?\s+-c\s+"[^"]*\n[^"]*"/.test(c)
            || /python3?\s+-c\s+'[^']*\n[^']*'/.test(c),
    },
    {
        label: 'git-rebase-i',
        fix: 'Interactive rebase requires a real terminal and hangs CoCo. Use non-interactive flags (--onto, --autosquash) or run it in a real terminal.',
        // Known limitation: longform `--interactive` is not matched.
        test: (c) => /git\s+rebase\s+-i\b/.test(c),
    },
    {
        label: 'git-merge-no-flag',
        fix: 'A bare `git merge <ref>` opens the merge-message editor and hangs. Add `--no-edit` or `-m "..."`.',
        test: (c) => /git\s+merge\s+\S+\s*$/m.test(c),
    },
    {
        label: 'unscoped-find',
        fix: 'Unscoped `find` on ~ / $HOME / / walks 100k+ files and freezes the terminal. Scope to a subdir (e.g. find ~/git/..., find ./src), add -maxdepth, or use Glob.',
        test: (c) => /(^|[\s;&|`(])find\s+(~|\$HOME|\/|\/Users\/[^/\s]+)(\s|$)/m.test(c),
    },
    {
        label: 'polling-loop',
        fix: 'A while/until loop that polls with sleep + grep/tail looks hung and buffers output. Redirect the producer to a logfile and use the bash_output tool with wait:true instead.',
        test: (c) => /\b(while|until)\b/.test(c) && /\bsleep\b/.test(c) && /\b(grep|tail)\b/.test(c),
    },
    {
        label: 'multiline-shell-control-flow',
        fix: 'Shell control-flow keywords (do/then/else/elif/fi/done/esac) on their own lines hang the Desktop terminal. Collapse to one line with semicolons (e.g. `for x in a b; do ...; done`) or write the script to a file and run `bash <file>`.',
        test: (c) => /\n\s*(do|then|else|elif|fi|done|esac)\b/.test(c),
    },
];

// Return { label, fix } for the first hang pattern the command matches, else null.
function matchHangPattern(command) {
    if (typeof command !== 'string' || command.length === 0) {
        return null;
    }
    for (const p of PATTERNS) {
        if (p.test(command)) {
            return { label: p.label, fix: p.fix };
        }
    }
    return null;
}

module.exports = { matchHangPattern, PATTERNS };

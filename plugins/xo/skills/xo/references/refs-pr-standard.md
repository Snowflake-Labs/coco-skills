---
name: refs-pr-standard
description: "Standard for pull request bodies: five sections in fixed order (Summary, Problem, Solution and why it's correct, Evidence, Merge risk), kept to one screen, fitted into the repo's PR template when one exists. Load from Ship PR mode, and whenever writing or revising a PR body."
---

# PR standard

Write every PR body so a reviewer can understand the change, judge whether it is correct, and know where to look, before reading the diff. Use the voice in `references/refs-writing-standards.md`.

## Sections, in order

1. **Summary.** One or two plain lines on what changes.
2. **Problem.** What is wrong or missing, as the user or caller experiences it, with its evidence: a reproduction, an error, a report, or a link to a public issue where one exists. Where the tracker is internal-only, describe the evidence instead of linking it (see "Scrub shared artifacts of private identifiers" in `references/act-ship.md`).
3. **Solution and why it's correct.** What the change does, and why that resolves the problem stated above. Where there was a real alternative, name it and say in a line why it was not chosen. Add a visual only if it makes the change easier to follow, and use the smallest one that does: pseudocode, a call tree, a file tree, or a trimmed diff of the key lines.
4. **Evidence.** Only checks that were actually run, each with its real outcome (command and result, test names and pass counts). For a bug fix, show the failing command before the fix and its passing run after; for an intermittent bug, show the failure rate before and after. For a visual change, include screenshots. Never list a check that was not run.
5. **Merge risk.** Whether the change reverts cleanly, what else it could affect, and where a reviewer should look first.

## Keep it short

- No preamble, and do not restate the diff line by line.
- If a section does not apply, say so in one line (for example "Evidence: no checks apply; comment-only change") rather than dropping it. Problem always applies: even a dependency bump states why it is needed.
- The body fits on one screen unless the change genuinely needs more.

## Repo template wins

If the repo has a PR template (for example `.github/PULL_REQUEST_TEMPLATE.md` or a file under `.github/PULL_REQUEST_TEMPLATE/`), fill the template. When several templates exist under `.github/PULL_REQUEST_TEMPLATE/`, use the one matching the change type, and ask the operator if none clearly matches. Put the content of the five sections above into the template's matching sections, and append any of the five the template lacks after its own sections. Never delete or leave out a template section; if one does not apply, say so in it.

## Scrub before posting

Apply Ship's scrub of shared artifacts unchanged ("Scrub shared artifacts of private identifiers" in `references/act-ship.md`). The PR body must be self-contained.

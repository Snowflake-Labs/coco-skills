# Workflow: Catalog Fit

> Two checks: (1) does the candidate skill collide with a bundled Cortex Code skill, and (2) is the skill parameterized enough to avoid producing forks. The first check uses on-disk and docs sources; the second is reasoning-based.

## Inputs

- `<skill_dir>` — path to the target skill directory.
- The parsed frontmatter `name` from format-check.

## Outputs

A list of findings plus two scores:

- `external_fit` — 1-5 with one-sentence justification
- `catalog_gap_fit` — 1-5 with one-sentence justification

Findings have the same shape as other workflows.

## Part A: Bundled-skill overlap

### Step A1: Probe disk paths for bundled skills

Try these paths in order. For each that exists, record the source and continue.

| OS | Path |
|---|---|
| Linux / macOS | `~/.local/share/cortex/*/bundled_skills/` |
| macOS app-support fallback | `~/Library/Application Support/cortex/*/bundled_skills/` |
| Windows | `%LOCALAPPDATA%\cortex\*\bundled_skills\` |
| User-level skills (any OS) | `~/.snowflake/cortex/skills/` |

For each existing path, glob `*/SKILL.md`. Read each file's frontmatter and parse `name:`. Build a set of bundled names. Record which paths contributed.

### Step A2: Fetch bundled skills from docs

`web_fetch https://docs.snowflake.com/en/user-guide/cortex-code/bundled-skills` and parse the published skill list.

Run this regardless of whether Step A1 succeeded — the contributor's local install may be older than what is currently shipping, and we want both views.

If the fetch fails (network unavailable, page moved, 404), record the failure and continue. Do not block.

### Step A3: Combine sources

Union the two sets. Record the source attribution:

| Sources available | Attribution |
|---|---|
| Both disk and docs returned a list | `disk+docs` |
| Only disk | `disk` |
| Only docs | `docs` |
| Neither | `skipped` |

### Step A4: Compare candidate name

Build the comparison set:

- `<name>` — the candidate's `name:` value, exact match.
- `<name>` with these prefixes stripped: `manage-`, `create-`, `build-`, `deploy-`, `setup-`, `configure-`. Each stripped variant is also compared.

For each variant, check whether it appears in the bundled-skill set.

If a collision is found, emit:

```
{check_id: "catalog.bundled_collision", severity: "🟠",
 evidence: "Candidate name '<name>' (or normalized variant '<variant>') matches bundled skill '<bundled_name>' (source: <attribution>).",
 suggested_change: "Rename the skill, or substantially extend scope beyond the bundled skill. Promoting as-is creates a silent shadow per the Labs README priority order: project skills > git-sourced skills > bundled skills."}
```

If no collision, emit:

```
{check_id: "catalog.bundled_collision", severity: "🟢",
 evidence: "No bundled-skill name collision. Source: <attribution>."}
```

### Step A5: Both sources unavailable

If `attribution == skipped`, emit:

```
{check_id: "catalog.bundled_collision", severity: "skipped",
 evidence: "Could not verify bundled-skill catalog: disk paths not found and docs fetch failed."}
```

The router does not block the verdict on a skipped catalog check. Confidence drops to `medium` (or `low` if other checks also skipped).

## Part B: Scope flexibility

### Step B1: Reason about parameterization

Read the body of `SKILL.md`. Decide whether the skill accepts variations or hardcodes its scope.

Indicators of a hardcoded scope (fork-encouraging):

- The skill assumes a single account/environment with no parameter to switch
- Output detail is single-mode (deep-dive only, or summary only) with no way to ask for the other
- Filtering dimensions the user is likely to need (region, env, team, line of business) are not exposed as parameters
- The skill's example prompt locks in specifics that should be selectable

Indicators the skill is well-parameterized:

- The body asks the user upfront for scope (which env, which account, which dimension)
- The body offers output modes (summary, detailed, comparison)
- The body uses `AskUserQuestion`-style decision points instead of branching on inferred intent

### Step B2: Emit finding

If the skill is hardcoded:

```
{check_id: "catalog.scope_inflexible", severity: "🟡",
 evidence: "<which scope dimension is hardcoded and why it would drive forks>",
 suggested_change: "Add a parameter for <dimension> at the top of the workflow. Hardcoded scope encourages forks (see data-policy-principles.md §7)."}
```

If the skill is well-parameterized:

```
{check_id: "catalog.scope_inflexible", severity: "🟢",
 evidence: "Skill exposes <list of parameters> for variation."}
```

## Part C: Scoring

### Step C1: external_fit (1-5)

Reason about how broadly applicable the skill is to a generic user (not just the author or the author's team).

| Score | Meaning |
|---|---|
| 5 | Universal — every user of the platform benefits |
| 4 | Broad — most users in a common scenario benefit |
| 3 | Niche but real — a meaningful subset benefits |
| 2 | Narrow — only a specific role or team would use this |
| 1 | Single-user — built for one workflow, unlikely to apply elsewhere |

Output:

```
external_fit: <1-5>
external_fit_rationale: "<one-sentence justification>"
```

### Step C2: catalog_gap_fit (1-5)

Reason about how much the skill fills a hole in the existing public catalog (Labs + bundled). Use the bundled-skill list from Part A as input.

| Score | Meaning |
|---|---|
| 5 | No equivalent in bundled or Labs catalogs; novel coverage |
| 4 | Partial overlap with an existing skill, but adds substantial value |
| 3 | Adjacent to an existing skill — useful complement |
| 2 | Significant overlap with an existing skill — debatable whether worth promoting |
| 1 | Duplicates an existing skill |

Output:

```
catalog_gap_fit: <1-5>
catalog_gap_fit_rationale: "<one-sentence justification>"
```

## Summary status

After running all parts, the workflow's overall status:

- 🟢 — no bundled collision, scope is parameterized, both scores ≥ 4
- 🟡 — scope inflexible, OR `catalog_gap_fit` ≤ 3, but no bundled collision
- 🟠 — bundled collision found

Pass that status, the findings, and both scores to the router.

## Notes

- Disk paths and docs URLs may evolve. If neither source resolves and the workflow consistently fails, the skill may need an update — but the router treats a single skipped check as a confidence reduction, not a blocker.
- The bundled-skill list is not a static reference shipped with this skill — it is fetched fresh each run from the canonical sources. New bundled skills are picked up automatically.

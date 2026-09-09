# Enabling branch protection

Repository settings cannot be committed, so they are recorded here. Apply these
to `main` under Settings -> Branches -> Branch protection rules.

Do the verification step first. Turning on a required check that the repo does
not currently pass wedges every open PR behind a cleanup.

## Required status checks

Require these to pass before merge, with "Require branches to be up to date
before merging" enabled:

- `Check skill structure and frontmatter` — the job in
  [validate-skill.yml](workflows/validate-skill.yml). Covers the validator
  self-tests, frontmatter and licence validation, and the internal-reference
  scan.

Do **not** require `Route review`. It applies advisory labels and runs on
`workflow_run`, so requiring it would block merges on a signalling job.

## Required reviews

- Require a pull request before merging: **on**
- Required approving reviews: **1**
- Require review from Code Owners: **on** (this is what makes
  [CODEOWNERS](CODEOWNERS) load-bearing rather than advisory)
- Dismiss stale approvals when new commits are pushed: **on**
- Require conversation resolution before merging: **on**

## Other settings

- Do not allow bypassing the above settings: **on**, including for admins.
  A maintainer merging past a failed disclosure check is the exact failure this
  is meant to prevent.
- Allow force pushes: **off**
- Allow deletions: **off**

## Fork PR permissions

Under Settings -> Actions -> General:

- Fork pull request workflows from outside collaborators: **Require approval for
  first-time contributors**
- Workflow permissions: **Read repository contents permission** as the default.
  Workflows needing more request it explicitly per-workflow, as
  `route-review.yml` does.

## Before enabling: verify the repo passes

```bash
# Both must exit 0. Each reports pre-existing debt as warnings, not errors.
python3 .github/scripts/validate_skill.py --all
python3 .github/scripts/scan_internal_refs.py --all

# Confirm the self-tests pass.
python3 .github/scripts/tests/test_validate_skill.py
python3 .github/scripts/tests/test_scan_internal_refs.py
python3 .github/scripts/tests/test_route_review.py
```

Then confirm CODEOWNERS resolves. GitHub silently ignores an entry naming a user
without write access, which fails open — a path you believe is protected is not.
Check the CODEOWNERS errors view on the file in the GitHub UI after committing.

## Known debt at enablement

Both checks carry a baseline of pre-existing findings so protection could be
enabled without a cleanup first. New findings fail; baselined ones are reported
as warnings.

`validator-baseline.json` grandfathers 9 errors across 5 skills. Three need a
decision from whoever owns the skill, because fixing them changes user-facing
behaviour:

| Skill | Error | Why it needs an owner |
| --- | --- | --- |
| `manage-zerocopy-sapbdc` | Name collides with a bundled skill | The repo skill silently shadows the bundled one. Renaming changes how users invoke it. |
| `openflow-spcs-privatelink` | `name` is `setup-openflow-privatelink`, folder is `openflow-spcs-privatelink` | Whichever is wrong, changing it affects invocation and install paths. |
| `rbac` | `type: community` but ships the Snowflake Skills License | Contradiction between declared authorship and licence. A licensing question, not a formatting one. |

The other two are mechanical: `snowflake-docs` and `spark-migration` are missing
catalog frontmatter fields, and `spark-migration`'s licence file is named
`Snowflake Skills License [FINAL].txt` rather than `LICENSE`.

`internal-refs-baseline.json` grandfathers 103 findings, 347 hits, almost all in
`spark-migration`: 62 unique internal ticket keys, plus one Google Sheets link
carrying a live share parameter inside test-data CSV. These are worth clearing on
their own merits — see [MAINTAINING.md](../MAINTAINING.md#internal-reference-scan).

Regenerate a baseline after a cleanup:

```bash
python3 .github/scripts/validate_skill.py --all --write-baseline
python3 .github/scripts/scan_internal_refs.py --all --write-baseline
```

Audit the full debt at any time with `--no-baseline`.

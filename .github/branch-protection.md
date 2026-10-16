# Enabling branch protection

Repository settings can't be committed, so they're recorded here. Apply to `main` under Settings → Branches.

**Verify the repo passes first.** Requiring a check the repo doesn't pass wedges every open PR behind a cleanup.

```bash
python3 .github/scripts/validate_skill.py --all        # must exit 0
python3 .github/scripts/scan_internal_refs.py --all    # must exit 0
python3 .github/scripts/tests/test_validate_skill.py
python3 .github/scripts/tests/test_scan_internal_refs.py
python3 .github/scripts/tests/test_route_review.py
```

Then confirm CODEOWNERS resolves — check the errors view on the file in the GitHub UI.

## Settings

Required status check:

- `Check skill structure and frontmatter` — the job in [validate-skill.yml](workflows/validate-skill.yml)

Do **not** require `Route review`. It only applies advisory labels, and runs on `workflow_run`.

Reviews:

- Require a pull request before merging, 1 approval
- Require review from Code Owners — this is what makes [CODEOWNERS](CODEOWNERS) load-bearing
- Dismiss stale approvals on new commits
- Require conversation resolution

Also:

- Require branches up to date before merging
- Do not allow bypassing, including for admins
- Force pushes and deletions off

Under Settings → Actions → General:

- Fork PRs from outside collaborators: **require approval for first-time contributors**
- Default workflow permissions: **read repository contents**

## Baselines

Both gates carry a baseline of pre-existing findings, so protection can be enabled without a cleanup. New findings fail; baselined ones report as warnings.

- `validator-baseline.json` — 9 errors across 5 skills
- `internal-refs-baseline.json` — 103 findings, mostly internal ticket keys in `spark-migration`

Matched text is stored as a hash, not verbatim — a baseline listing internal references would republish exactly what the scan exists to keep out.

Three of the 9 errors need a decision from whoever owns the skill, because fixing them changes user-facing behaviour:

| Skill | Error |
| --- | --- |
| `manage-zerocopy-sapbdc` | Name collides with a bundled skill, silently shadowing it |
| `openflow-spcs-privatelink` | `name` is `setup-openflow-privatelink`, folder differs |
| `rbac` | `type: community` but ships the Snowflake Skills License |

The rest are mechanical: `snowflake-docs` and `spark-migration` are missing catalog frontmatter, and `spark-migration`'s licence file isn't named `LICENSE`.

Audit full debt with `--no-baseline`. Regenerate after a cleanup with `--write-baseline`.

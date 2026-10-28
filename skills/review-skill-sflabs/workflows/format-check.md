# Metadata and license policy reference

Reference documentation only, not an executable agent workflow. Use only the
trusted launcher described in [SKILL.md](../SKILL.md); do not read candidate files
into context during admission review.

## One contract

[scripts/policy.py](../scripts/policy.py) owns the required/advisory metadata
matrix, rules version 1.2, and license normalization/claim rules (unchanged from 1.1). Checks consume
that matrix; the [CONTRIBUTING table](../../../CONTRIBUTING.md#frontmatter-contract)
is verified against its renderer in unit tests. Refer there instead of maintaining
a second field/enum list here. No default metadata or automatic fixes are inserted.

The strict bounded YAML parser rejects duplicate keys, aliases and unsupported
field types. Name/directory convention, title/summary limits, wording, disclaimers
and structural Markdown checks remain advisory. Bare prose paths and code examples
are not dependencies. Bounded local link, anchor, fence and explicit routing checks
are documented in [the report contract](../references/report-template.md#bounded-markdown-support).
They do not fetch URLs, execute routes or prove workflow termination.

## Pinned local license evidence

[scripts/resources/licenses.json](../scripts/resources/licenses.json) contains
complete normalized source texts, not substring markers or remotely fetched terms.
Its source repository is `https://github.com/Snowflake-Labs/coco-skills.git` and its
source revision is `89c57c211105bb5a870ce2f6e6e495a20c962fd8`. This identifies the
local implementation's selected evidence, not a legally approved revision.

| Source path at that revision | Content represented |
|---|---|
| `skills/dbt-model-generator/LICENSE` | All locally recorded Apache-labeled sections 1-9 plus its copyright and notice |
| `skills/review-skill-sflabs/LICENSE` | Full locally recorded Snowflake Skills License |
| `skills/swap-databases/LICENSE` | Equivalent Snowflake text with ASCII quotes/layout differences |
| `skills/check-agent-access/LICENSE` | Complete legacy Apache notice only |
| `skills/persist-memory/LICENSE` | Complete legacy Apache notice only |
| `skills/snowpro-study/LICENSE` | Complete legacy Apache notice only, including its literal HTML entities |

The Apache-labeled local full-terms file is matched as found, not represented as
verified canonical Apache text. The local file contains wording that needs legal
review; no external canonical source was fetched or substituted. Notice-only
sources are preserved as exact legacy matches with `license.notice` warnings,
not represented as full license terms. Plugin-specific licenses are not included
in the skill-template set.

Plugin skills inherit their plugin's root `LICENSE`. The trusted host collector
captures that one file with the same no-follow, single-link and size rules and
passes it under the reserved snapshot name `PLUGIN_LICENSE`, which is included in
the snapshot digest. A candidate file using that name is rejected. A missing
plugin `LICENSE` fails `format.license` even if the skill has its own file, and
every plugin skill also gets the advisory `license.inherited`.

Normalization `ascii-whitespace-and-quotes-v1` collapses ASCII whitespace runs
(space, tab, CR, LF, form feed, vertical tab) and trims them at the ends. Curly
double quotes U+201C/U+201D and apostrophe U+2019 map to their ASCII equivalents,
as observed in the Snowflake sources. Everything else must match in full:
case, punctuation, links, clauses, copyright years/holders, and notice text.
No HTML decoding, case folding, Unicode compatibility normalization, arbitrary
prefix/suffix, or wildcard attribution fields are accepted. Truncated terms,
extra clauses and fake text containing the license title fail `format.license`.
Unmatched templates/substitutions need maintainer review, not automatic rewriting.

Both author spellings are checked together. The historical case-insensitive
`@snowflake.com` marker rule is preserved, including its conservative substring
behavior: it is not an email validator or identity check. A marker in either
alias requires a Snowflake template. Names, missing claims, `type` alone, or
contradictory contributor metadata do not establish legal applicability; they
remain human decisions rather than a new employee-type failure. Every review
emits `license.review`; conflicting aliases also emit `format.author_conflict`.

External maintainer/legal approval remains outstanding for template correctness,
applicability, permitted substitutions, and distribution. Passing content checks
does not establish employee identity, ownership, valid attribution or rights.
Do not copy the fixture's author attribution to make a new submission pass.

## Dependencies and packaging

### Local deliverable: offline snapshots, not installation

`requirements.txt` remains the single runtime dependency source used by Docker.
`pyproject.toml` now describes a snapshot recipe, not an empty setuptools package.
There is deliberately no PEP 517 backend or installable project: do not run pip
install/build here. No tool or dependency is installed by the recipe. The host
launcher and packager use the standard library; trusted full-suite verification
also needs an independently provisioned compatible PyYAML and Git.

The separately trusted repository-layout installation runs
`.github/scripts/package_reviewer.py`. It only collects the fixed allowlist in
`scripts/packaging.py`: no source imports, build hooks, Git archive extraction,
candidate bytes, unlisted scripts, model calls, or network. The script requires
`--trusted-root` to be its own installation root and rejects overlap with the
candidate, symlink components and hardlinked/special inputs. Collection retains
the existing file/byte/depth/concurrent-change bounds. Run only from a reviewed
copy; these checks cannot bootstrap trust in the script performing the checks.

```sh
# Metadata only; no archive, build, installation or publication.
python3 -I -B /absolute/trusted/coco-skills/.github/scripts/package_reviewer.py \
  --trusted-root /absolute/trusted/coco-skills \
  --candidate-root /absolute/candidate --profile local

# Optional local snapshot after the exact digest has been independently reviewed.
# The output parent must already exist, mode 0700, outside both roots.
python3 -I -B /absolute/trusted/coco-skills/.github/scripts/package_reviewer.py \
  --trusted-root /absolute/trusted/coco-skills \
  --candidate-root /absolute/candidate --profile local \
  --expected-sha256 REVIEWED_MANIFEST_SHA256 --output /absolute/private/reviewer.tar
```

`local` captures launcher, resources, stdlib packager, runtime sources, license,
dependency manifest and Docker recipe. `ci` additionally captures the thin Action,
CI adapters, workflow, required test suite and its policy fixtures. Neither
profile includes candidate data. The tar contains `MANIFEST.json` and sorted,
captured files, fixed uid/gid/mtime, mode 0600, and no links. Its canonical manifest
records each path/length/SHA-256 and the profile. The printed digest identifies
that manifest, not a release approval, Git revision or image. Identical bytes
produce identical tar bytes. Resource paths and repository layout are retained.
No timestamp, working-directory path, environment value or credentials enter it.
Destinations use exclusive no-follow owner-only writes; no overwrite. The human
operator must delete any retained archive when no longer needed. No archive of
this real checkout was created as part of implementation; tests use synthetic data.

The future installation recipe is manual and gated: approve rights and exact
snapshot/launcher bytes; provision a new operator-controlled directory outside
all candidates, verify manifest and archive hashes through an independent trusted
channel, inspect safe archive members before extraction, preserve the repository
layout, make tooling unavailable for candidate writes, then invoke `scripts/run.py`
with isolated Python and the independently approved image ID. No extraction or
installation code is provided or authorized here. A candidate cannot approve its
own digest. Local deterministic use does not depend on CI or a model provider.

### Image provisioning: blocked separately

The Dockerfile is a recipe only. A future owner must approve build tooling,
registry/network access and distribution rights before building in a separate
trusted environment. Review PyYAML artifact hashes/licenses, Python/base-image
licenses and digest, apt Git and transitive packages; preserve notices and record
an SBOM or equivalent bill of materials. Apt versions and dependency artifacts
are not locked today. No fully reproducible image/supply-chain claim is made.
Docker preserves `scripts/resources` beside the modules; runtime resources never
come from candidate or working-directory paths. Pair a reviewed launcher snapshot
with an independently approved resulting local `sha256:...` image ID. A registry
manifest digest is not necessarily the same as Docker's local image config ID.
Preload that exact image and dependencies through an approved provisioning path;
reviews use `--pull=never`. No automatic build, pull, registry push or installation.
Run real trusted isolation verification against the pair before release. Missing
image/dependencies or any skipped required test means NOT VERIFIED.

### CI deliverable: thin Action and inert integration template

`.github/actions/contained-review/action.yml` invokes its own separately checked-out
adapter using `GITHUB_ACTION_PATH`, not a candidate-controlled executable path.
Inputs are passed through environment variables and quoted arguments. The adapter
uses the existing `validate_skill.scan` and `review` core, not a new policy engine.
It requires three disjoint directories: independently approved tooling, exact
PR base as data, and exact PR head as data. Base is not automatically trusted
tooling. The CI profile manifest must match an independently supplied SHA-256
before tests or scan; it is checked again afterwards. Python must be isolated,
the host/installation trusted and protected against concurrent untrusted writers.
Post-run hashing detects changes, but is not a sandbox against same-user attackers.

The adapter accepts a runner-supplied `pull_request` event, validates exact 40-hex
base/head revisions and base repository binding, ignores title/body/branch/fork
names/labels and purported authority metadata, and verifies checkout blobs through
the existing core. It does not use the PR merge commit as head. It supports the
existing explicit trusted rename arguments; candidate mappings are not loaded.
Fork rehearsal uses synthetic Git trees and event JSON, without secrets, remote
fetches, workflow-command writes from candidate bytes or candidate execution.
This is offline rehearsal, not evidence of a successful real fork Actions run.

The template uses one owner-configured preloaded image ID in both trusted
verification/admission scanning and isolated proposed-test execution, in one job.
There are no builds, pulls, setup-python or pip installs. Proposed tests remain
untrusted counts only and cannot supply admission evidence. They run only after
the trusted step succeeds. The single job's status alone does not distinguish
these lanes; authoritative owners must preserve their different meanings.
`contents: read`, no persisted checkout credentials, no submodules/LFS, no secret
inputs, no `pull_request_target`, no uploads, comments or `GITHUB_OUTPUT` writes.
The default read-only checkout token is used only by checkout; there are no
custom secrets and no token passed to the review container. Containers have no
network, mounts or Docker socket.

The checkout pin `11d5960a326750d5838078e36cf38b85af677262` was verified on
2026-10-20 via read-only `gh api repos/actions/checkout/git/ref/tags/v4`: the object
was a direct commit. This is identity evidence, not source/security approval.
No third-party Action commit IDs are guessed. No live repository was modified.

### Owner checklist: external authoritative required workflow

**Deployment and release publication remain blocked; draft source review is separate.** The repository workflow has a
literal `if: ${{ false }}`; do not remove it as an implicit follow-up. A PR can
replace this whole file, job name, Action wrapper or digest assertions. Neither a
PR metadata assertion, a green status name, CODEOWNERS alone nor a reusable-workflow
call in candidate-controlled YAML establishes authority. Bootstrap requires an
independently reviewed tooling release and an external, separately controlled
required workflow before using results as admission evidence. A historical base
without the new tooling cannot build or verify the new reviewer; proposed tests
cannot bless the bootstrap.

Owners must record concrete evidence for every item before an authorized rollout:

- [ ] Named security/maintainer and provisioning owners, approved distribution and
  dependency rights, and written release-owner sign-off. Owners must be assigned before rollout.
- [ ] External authoritative workflow repository ID/name, workflow path, immutable
  reviewed ref, owning team and update/bypass authority. Values and available GitHub
  organization/plan features are **unknown**, not supplied by this PR.
- [ ] Target organization/repository IDs, protected refs (including `main`), ruleset
  ID, enforcement mode and exact externally required-workflow binding. Obtain live
  control/export evidence that the rule requires the external workflow, not just a
  spoofable status-context string. No organization/ruleset IDs are invented here.
- [ ] Verify bypass actors and admin exemptions are restricted and audited; protect
  the authority repository, configuration changes, approved tooling revision, CI
  manifest digest and image ID from candidate authors. Repository variables shown
  in the template are placeholders, not an authority mechanism. Use an owner-only
  external configuration/provisioning channel with review and change records.
- [ ] Independently approve the initial tooling snapshot, runtime image, third-party
  Action source/pins and test inventory; provision Python 3.11+, PyYAML, Git and a
  local Docker image on a disposable runner before review. Never execute candidate
  setup hooks, dependency manifests, workflow actions or build contexts on the host.
- [ ] Configure the external workflow for all protected-ref PRs, including forks and
  tooling-only changes, with exact base/head event binding and complete Git objects.
  Do not copy this template's path filter into the authoritative required workflow:
  missing/skipped validation must block rather than silently satisfy protection.
  Verify deleted/modified local workflows and same-name forged checks cannot bypass.
- [ ] Verify fork permission/approval settings in the actual organization. Keep only
  read repository access, no custom secrets/write token, credentials removed after
  checkout, no privileged event or reusable secret inheritance. Resolve unavailable
  fork SHA objects as blocked, never fall back to a merge ref or candidate tooling.
- [ ] If a merge queue is used, implement and test an explicit `merge_group` event
  binding first; the current adapter intentionally rejects it. Event coverage and
  fail-closed pending/skipped behavior must be demonstrated for all merge paths.
- [ ] Select an ephemeral isolated runner with no reusable credentials or persistent
  candidate-writable caches. Confirm the approved preloaded image is local, never
  remotely configured Docker. Do not put fork execution on a sensitive persistent
  self-hosted runner. Runner image/label/provisioning details are still unknown.
- [ ] Verify real trusted runtime/isolation tests with zero missing/failing/skipped
  cases. Rehearse synthetic fork additions, deletions, renames, bad output, missing
  baseline, forged local workflow/Action/image and bypass attempts. Archive only
  approved fixed evidence, never candidate bytes or private locator mappings.
- [ ] Approve job-summary disclosure/retention and local machine-report retention;
  summaries still disclose counts and outcome. Machine reports remain in private
  runner temp storage, no artifact upload. Delete with disposable-runner teardown.
  Any future artifact publishing needs separate destination/data-handling approval.
- [ ] Document rollback to a previously approved compatible launcher/image pair,
  required-check failure handling and revocation. Never fall back to candidate code.

Until these controls and bootstrap/provisioning evidence are independently verified,
this is local implementation plus an inert CI example, not an enforced admission
gate, installed release, published Action, approved image or shipped initiative.

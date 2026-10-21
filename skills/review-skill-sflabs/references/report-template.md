# Report and optional model contracts

The report renderer is scripts/protocol.py, not a model-generated template.
Schema version 2.1 / rules version 1.2 reports: status, coverage counts, catalog availability,
model disabled, public duplicates not run, provenance and bounded hashed source IDs.
Each finding carries a fixed code, severity, group, allowlisted metadata field (or
null), file ID, stable source hash, optional line number and exact fixed next action.
Required corrections, advisory human decisions and coverage limitations render as
separate sections; JSON findings carry the same explicit group. Unknown fields,
invalid combinations, duplicate JSON keys, arbitrary prose and incompatible
versions are rejected. It never emits candidate names, excerpts, YAML error text,
secret values or model prose. Exit codes remain 0 passed, 1 failed, 2 blocked or
incomplete, 3 unavailable. Warnings do not fail mechanical validation.

Rules version 1.2 preserves the 1.1 [metadata/license policy](../workflows/format-check.md)
and adds only advisory Markdown/catalog behavior.
`format.license` is a content/claim error, not a legal judgment;
`format.author_conflict`, `license.notice` and `license.review` are human advisories.
Deploy a compatible reviewed launcher/image pair: 1.0 and 2.0 runtime output is rejected.
Schema 2.1 adds exactly `related`: at most three unique 64-hex `catalog_id` values
and an exact fixed differentiation `question`. The host checks IDs against its
installed catalog, not candidate text. All earlier strict validation remains.

## Provenance and source identity

Completed reports must have exactly one source-bound `coverage.unsupported`
finding (with no line number) for each file counted outside `text_files`; duplicates,
unbound findings and count mismatches are rejected by both schema and host validation.
Required errors still take precedence (`failed`); otherwise unsupported files mean
`incomplete`, never `passed`. Blocked/unavailable zero-coverage error envelopes remain valid.

All source coordinates use `ingest.lf_line_count` and `ingest.lf_lines`: LF is the
only line separator. Empty content has one line; a trailing LF creates a final empty
line. CRLF counts once (CR is removed only when paired with LF during parsing).
Bare CR, VT, FF, NEL and Unicode line/paragraph separators do not create citation
lines. Frontmatter, Markdown/policy diagnostics, model packet bounds and independent
host byte bounds use this convention. Packet digests change where the old
`splitlines()` count differed; matched reviewed tooling must be used.


`manifest(entries, name)` now requires the candidate directory name. The snapshot
SHA-256 binds a domain-separated canonical JSON object containing that name and
the ordered manifest: file ordinal, UTF-8 relative-path hash, exact content hash
and byte count. No raw paths enter the safe report. File ordinals are local to one
snapshot; `source_sha256` hashes the relative path independently of content,
ordinal and line number. Delta matching uses it with code and field, not the F
ordinal. An explicit trusted rename mapping may map old hashes to new hashes;
no similarity or candidate metadata supplies rename authority.
Hashes permit correlation and guessing, not anonymization or identity proof.

Provenance includes `contained-reviewer/2.1`, rules/schema versions, the full
canonical catalog digest (all fields, including scope and entries) and its source
revision. The host recomputes the snapshot and source mapping from the exact wire
sent to the container, compares the catalog to the reviewed installed copy, and
rejects runtime-supplied image IDs or PR revisions. It sets the immutable image ID
actually passed to Docker only after verification. The CI adapter adds validated
40-hex base/head revision context. CI checks no-follow checkout directories and
HEAD revisions, then compares every captured bundle file to raw Git blob IDs in
the requested tree before container review. This bypasses index stat caches and
clean/smudge filters. It checks root identity and HEAD again before publishing.
The snapshot digest binds actual reviewed bytes. Direct/offline reports explicitly
have no validated image.

Blocked/unavailable reports have null provenance, empty sources and zero coverage;
no private locator is produced. A completed review that fails mechanical rules
still has snapshot provenance. Catalog completeness/freshness, legal acceptance,
image supply-chain authenticity and report signatures are not proved by these hashes.

## PR comparison contract

The trusted CI adapter accepts `--candidate-root`, optional `--base-root`, exact
`--base` and `--head` commit IDs, and the same immutable `--image` for both reviews.
The inert workflow template supplies a separate exact baseline checkout as data;
approved tooling comes from an independent revision, not that baseline. No archive extraction,
candidate scripts/hooks, filters, external diff, network fetch or model calls are
used. Only ordinary POSIX checkouts with real `.git` directories and SHA-1 Git
objects are supported; linked worktrees and transformed/LFS content are not.
Checkout metadata and operator arguments must come from trusted orchestration,
not candidate instructions. Same-user hostile processes and privileged actors are
outside this boundary; no race-free general-purpose Git sandbox is claimed.

Selection compares the exact base and head trees (not an implicit merge base).
Every selected immediate child is reviewed as a whole bundle. A Git-absent skill
must also be absent via no-follow checkout inspection; missing checkout files
for a Git-present skill fail snapshot validation. New skills use trusted base-tree
absence, not a missing baseline report. Deletions require both trusted head-tree
and checkout absence. Unmapped moves are separate additions/deletions, never
asserted renames. `--renames '[{"base":"old","head":"new"}]'` is an explicit
operator mapping, accepted only for disjoint full deletion/addition pairs.
`--file-renames '[{"base":"skills/old/a.md","head":"skills/new/b.md"}]'`
maps disjoint removed/added regular files within an already selected bundle pair.
Partial skill moves, merges, splits, occupied destinations and ambiguous mappings
are rejected. Never source these options from candidate frontmatter or files.

`--format json` emits a host-built `comparison_schema: "1.0"` aggregate with
`base_revision`, `head_revision`, and `submissions`. Each submission has a stable
hashed `submission_id`, strict `baseline`/`head` reports (or null), and `delta`.
Delta has `kind` (added/deleted/updated/renamed), `comparison` (complete/not-comparable),
`counts`, and `findings`. Each finding contains a SHA-256 `finding_id` over canonical
`[code, field, mapped_source_hash]`, those three fields, and `classification`:
introduced/resolved/unchanged/not-comparable. No candidate labels or excerpts.
Text output renders safe per-submission counts and both reports. No automatic
file publication, private locator, artifact upload, or PR comment is performed.

Counts describe distinct rule/field/source buckets, not individual occurrences.
Line shifts and inserted files cannot create deltas; several missing links in a
file remain one bucket. This does not establish that a particular occurrence was
fixed. A completed failing mechanical review can be compared, but unknown coverage,
unsafe/missing baseline, malformed entry metadata, absent entry document, Markdown
limits/unclosed fences, or different image/catalog/context means not-comparable.
No resolutions are claimed in those cases. Whole-bundle absence is an explicit
selection fact, not missing coverage. Head status remains authoritative for the
exit code: existing failures are never grandfathered. Baseline unavailability
alone does not change head admission; it limits comparison evidence.

## Action evidence envelope

The separately trusted `.github/scripts/action_review.py` calls the same core and
adds `action_schema: "1.0"`, schema/rules versions, independently checked
`tooling_sha256`, configured immutable `image_id`, exact event `base_revision` and
`head_revision`, installed catalog identity, `verification` counts and submissions.
Each submission retains strict baseline/head reports and a safe delta projection
(kind/comparison/counts); arbitrary delta detail is not forwarded. Reports with
provenance must match the requested image/revisions and installed catalog. Exit
status is cross-checked against head results. A runtime-unavailable head retains
null per-snapshot provenance; top-level image configuration is not proof that it
executed. Hashes do not attest correctness, identity or configuration authority.

`status` is passed/failed/blocked. `authority` is always the literal
`not-established-by-this-report`. Trusted verification provides numeric expected,
executed, failed and skipped counts; any incomplete counts/nonzero worker exit
blocks scanning. Verification evidence is produced in the same invocation, not
accepted as an input report. Early configuration/invalid-output failures emit a
fixed diagnostic and no machine report; missing reports are failures, not passes.
A returned verification failure can produce a blocked report with its counts.
Proposed-validator counts are not used in this envelope or trusted verdict.

The JSON destination is exclusively created mode 0600 in an existing owner-only
0700 directory outside tooling, baseline and candidate. It is local runner-temp
evidence, never an artifact upload. Summary text includes only fixed strings,
mechanical status, submission count and verification counts: no candidate names,
raw parser/test/container output, excerpt, URLs, or private locator. It appends to
the existing runner-owned regular no-follow `GITHUB_STEP_SUMMARY` file outside the
checkouts; no `GITHUB_OUTPUT` or PR comments. Summary disclosure still needs owner
approval before rollout. Interrupted runs can leave a private partial report;
disposable-runner teardown is the retention boundary, not secure erasure.

The proposed lane optionally accepts exact `--base`/`--head` revisions and runs
only when its fixed repository-input set changes (including deletions). Selection
failure blocks the lane; not-selected is explicit and is not verification. Full
changed-skill admission scans and trusted-runtime verification are unchanged. The
inert workflow reuses one pre-provisioned immutable image and never builds/pulls.

See [owner controls and bootstrap](../workflows/format-check.md#owner-checklist-external-authoritative-required-workflow).
The checked-in workflow is deliberately inert. Actual fork execution, approved
image verification, authoritative enforcement and publishing remain unverified.

## Bounded Markdown support

Advisory-only checks cover single-line balanced inline/image links, full/collapsed
reference links, defined shortcut links, quoted titles, local percent-decoded paths,
simple ASCII ATX/setext heading anchors, duplicate headings, backtick/tilde fences,
and explicit line-leading Read/Load/Follow/Open/Run `local/path` routing (including
simple list prefixes). Bare prose paths are not dependencies. Fenced, indented and
inline code examples are excluded from structural link checks; secret/policy scans
still examine all text. No link is fetched or executed.

This dependency-free subset is deliberately not a CommonMark implementation.
Markdown packages on a developer host are not dependencies in the approved image;
no new parser dependency is silently installed. Lines above 4096 characters,
more than 2048 references per document, nesting beyond 16, malformed link syntax,
HTML/custom anchors, complex/Unicode heading slugs and unsupported destination
syntax produce an advisory limit or anchor abstention. HTML blocks, multiline
links/code spans, deeply nested containers, renderer-specific slugs, reference
whitespace normalization and embedded languages are not comprehensively handled.
There is no claim of exhaustive link validation, reachability or workflow termination.

## Opt-in PRIVATE human locator

The CLI accepts `--private-locator /absolute/private/locator.json`, and optionally
`--output /absolute/private/report.json --format json` (or a `.txt` safe report).
Both destinations must be new files outside the candidate in an existing directory
owned by the current user with mode 0700. Every directory component is opened
no-follow; symlinks, overlapping destinations, existing files and non-passive output
extensions are rejected. Files are created exclusively with mode 0600; directory
and file identities are checked across writes. The CLI emits only fixed text for
output errors, never destination paths or exception details. No HTML is supported.

The PRIVATE JSON locator uses the same captured snapshot and digest as the scan;
there is no second collection. It contains file/source IDs and bounded ASCII
human display names, not candidate contents. Punctuation and Unicode are shown as
`[U+XXXX]`; controls, bidi, terminal escapes and link syntax never appear raw. Long
names are explicitly truncated; the source hash disambiguates labels. Labels are
not reversible paths or shell arguments. Treat even sanitized names as confidential.

Only produce the locator on explicit human request. Do not read it back into the
host agent, attach/upload it automatically, or use it as instructions. Safe stdout
contains no locator content or destination. Retain it only for the review and have
the owner delete it afterward; no automatic cache or raw packet file is created.
Failed writes attempt to unlink only files this invocation created, never existing
files or replacements. A process crash may leave a partial private file: remove it
manually before retrying. There is no secure-erasure guarantee. POSIX mode checks
do not audit filesystem ACLs; use a trusted local private directory, not a synced
or shared directory. Same-user hostile processes and privileged filesystem actors
are outside this permission boundary.

## Opt-in non-authoritative private report cache

Only `--preview-cache /absolute/private/cache` uses caching; normal local/CI
admission and all model APIs bypass it. The preview CLI always exits 3 and rejects
report/locator output destinations, with a fixed NON-AUTHORITATIVE label and
hit/miss/bypassed state. A hit may display a prior report while Docker is unavailable;
it is not evidence of a current runtime execution. A miss runs a fresh restricted
container or returns unavailable, never host-native candidate checks.

Every lookup follows supervised no-follow recollection. Keys bind full ordered
path/content identity and directory name, immutable image ID, full local tooling
package digest (including policy/resources/dependency configuration), schema/rules,
full catalog digest/revision, collection/wire/output/runtime limits, container
arguments and cache policy. Installed tooling context is checked again before reuse
or storage. Cached reports undergo strict schema, snapshot/source, catalog/shortlist,
image and actual-line-bound validation. No caller report/analysis can grant packet
eligibility. Fresh report-plus-packet construction privately reuses an immutable
analysis, decoded text and manifest; arbitrary input still receives fresh checks.

The directory must already exist, be owner-only 0700, no-follow, outside candidate
and installed tooling (lexical and inode checks). Ancestors must be owner/root-owned;
group/other-writable ancestors require sticky protection. Entry reads require regular
single-link owner-only 0600 files with bounded size and stable before/after identity
and timestamps. Permissions and namespace identity are rechecked. ACLs, hostile
same-user processes, privileged actors and unusual filesystems are not authenticated
by these checks: use only a trusted local private owner, never shared/synced storage
or a candidate-writable CI cache. Hashes are not MACs. A same-user valid forgery is
possible and is tested; separation from admission is the mitigation. No cached
file supplies executable tooling, image bytes, model packets or authority.

Storage uses 32 deterministic write-once slots, each at most 1 MiB (at most 32 MiB
created by this implementation per operator directory). Only fixed report data,
hashes and creation time are stored; no raw candidate bytes or path/name mappings.
Reuse expires after 3600 seconds; future timestamps, corruption, unsafe mode,
links, collisions, races and I/O failures miss/bypass safely. Occupied slots are
never replaced: expiry does not free space, and cache usefulness declines as slots
fill. There is no scanning, pruning or deletion, including partial writes. The
owner must retire/recreate a dedicated directory when desired; TTL is a reuse
limit, not secure erasure or automatic retention enforcement. Unrelated files are
never read/deleted and do not count toward our own write bound.

### Synthetic workstream 7 measurements (2026-10-20)

Command: `python3 -B /absolute/trusted/coco-skills/skills/review-skill-sflabs/tests/benchmark_reviewer.py --iterations 15`.
Python 3.12.0, 98 files, 63,959 content bytes, one changed skill. Baseline was captured
before core optimizations, with the same generated corpus. Times below are repeated
host median milliseconds (15 samples), not true OS-cold or container measurements.
Separate tracemalloc passes measure Python allocations, not total RSS or child memory.

| Stage | Before ms | After ms | Before peak bytes | After peak bytes |
|---|---:|---:|---:|---:|
| Collection (in-process) | 3.640 | 4.789 | 157474 | 157474 |
| Serialization | 0.219 | 0.214 | 272690 | 272690 |
| Wire validation | 0.426 | 0.360 | 190380 | 190380 |
| Checks | 24.395 | 14.390 | 279686 | 294954 |
| Render | 0.091 | 0.094 | 14080 | 14080 |
| Model packet | 25.045 | 15.118 | 355780 | 384138 |
| Review plus packet | 49.209 | 14.902 | 396769 | 383820 |

After-only cache medians: context/key 2.189 ms; lookup/validation 4.853 ms;
supervised collection+serialization 78.973 ms; full preview hit 98.868 ms;
preview miss with a synthetic pure-core runtime double 116.585 ms. Cache first-use
provisioning before implementation has no comparable measurement. Hit/miss includes
recollection and code hashing; miss is not a Docker timing and not a claimed
production latency saving. Allocation peaks increased for checks and standalone
packets; no general memory reduction is claimed. First-call/range values are printed
by the benchmark but are not labeled cold provisioning.

Corpus SHA-256: `6c28bb3293e0a61301fecad12f76b259caf20a53c9136656df84ed7e6c45edfe`.
Report SHA-256: `7eaed312a419f97f63931ee465c242a327f925ea4a8b592bb6c683060de58ef0`.
Packet SHA-256: `2e97fcd963c7309c7e838c4cad5153bd9c8d0560c0ab74447d829543e8f14b2f`.
All three are unchanged before/after and regression-tested. Cold/warm provisioning,
container startup/peak memory, real cache-miss runtime and end-to-end Docker latency
remain **unmeasured**: daemon stopped, no start/build/install/network operation.
No credit savings, production speed guarantee or isolation verification is claimed.

## Optional offline interface

scripts/model_contract.py exposes make_packet(..., enabled=False) and
validate_response(raw_bytes, packet). There is no live provider or user-facing
packet-export command. A future broker may call this API only within its isolated
boundary; do not import it into the host agent to assess candidate text.

Packets include a fixed rubric, schema/rules versions, the same name-bound snapshot
hash and provenance as deterministic review, packet hash, trusted catalog input
and sorted file records (ID, stable source hash, relative path, content hash, text,
line count). Packets are limited to 128 KiB and blocked if required validation
fails, coverage is incomplete, or known-secret detection matches. No truncation.
Raw text is retained only for eligible packets; unknown secrets may still exist.
Never print packets to host-agent logs. Runtime credentials must stay outside them.

Responses are at most 16 KiB with exactly schema_version, packet_sha256, findings.
At most 32 findings, each with exactly:
- category: contextual_policy, semantic_overlap, usefulness, scope, or clarity
- recommendation: human_review, parameterize, clarify, differentiate, minimize_output
- file_id, line_start, line_end referencing the supplied packet

No free-form reasoning, commands, URLs, waiver fields or actions are accepted.
Duplicate JSON keys, unexpected fields and invalid citations are rejected.
Hashes bind advice to its input, not to truth or authorship. Enum-only advice can
still be wrong or manipulated; human judgment remains required. Model results
cannot modify a deterministic report's status. Synthetic tests exercise this
contract without transmitting anything or invoking a model.

## Workstream 8 offline broker contract

`editorial_broker.assess_in_isolation(wire, *, config=Config(), consent=None,
catalog=None, provider=None)` is a service-internal API, **not a runnable host
broker or a new service launcher**. Existing deterministic schema 2.1 / rules 1.2,
immutable internal analysis, enum/citation validation, admission and preview exit 3
are unchanged. No launcher, Action or container entrypoint enables inference.
No live endpoint/model has been selected. HTTP/HTTPS targets return unavailable,
even if a caller supplies consent; there is no concrete HTTP adapter or provider SDK.

Trust is supplied by separately reviewed orchestration, not candidate metadata:

- `OfflineProvider` and `TrustedTokenizer` are reviewed code interfaces injected
  directly in memory, never module names, executable paths, pickles or candidate
  plugins. Subclassing is not authentication. Malicious trusted Python can violate
  the contract; it must not be accepted as a provider. Only synthetic doubles are
  implemented, in the test directory, with no network/credentials/SDK/subprocesses.
- A pre-existing single-threaded, secret-free isolated service must own the call.
  POSIX fork supervision is **not a sandbox**. Fork inherits parent memory despite
  clearing the child environment and closing ordinary descriptors. Do not run on
  a credential-bearing host. No isolation attestation can be asserted by a flag.
  Deployment must enforce no network, mounts, tools, descendants, host file access,
  bounded memory/CPU/process count and no logging outside its boundary. This service
  deployment and its resource/isolation verification are not implemented here.
- `Config` and nested target/rate/budget dataclasses are frozen. Exact target must
  appear in the trusted tuple allowlist. Only `offline://` labels are usable; model
  and tokenizer revisions must be independently established immutable SHA-256
  identities, not aliases such as latest. Hash syntax alone proves no authenticity.
  The target-specific tokenizer and provider must match those exact identities.
- `Consent(transmit=True, wire_sha256=..., config_sha256=...)` must be constructed
  from an explicit operator decision, never inferred from eligibility. It binds
  the complete captured wire plus configuration, broker version and full trusted
  catalog. It does not export anything in this offline implementation. All config
  fields, including question, rates, ceilings and deadline, affect its fingerprint.
- One fixed editorial question is selected by `scope`, `clarity`, `usefulness` or
  `differentiation`; arbitrary question prose is not accepted. The request includes
  full eligible packet, fixed question, response schema, empty tools, tool choice
  none, call cap one and output ceiling. No browsing, callbacks, tools, fetching,
  automatic retry, override, escalation, fixes or admission decisions exist.

### Accounting and supervision

The proposed defaults are 8000 input tokens / 1000 output tokens, not a verified
optimum. Default wall deadline is 5000 ms (configurable positive integer up to
60000); token ceilings are bounded positive integers up to 1000000. Input packet
stays at 128 KiB, complete provider request at 160 KiB, raw response at 16 KiB,
and pipe egress at 24 KiB. No silent truncation or sampling occurs.

A trusted model-specific tokenizer must account for **all** serialized input,
including full packet text, rubric, response schema, fixed question and transport/chat
framing. Visible response tokens are independently counted; reported usage must
cover them and remain within the reserved ceilings. Missing/invalid tokenizer
counts or unreliable billing bounds cause refusal, not guessed token ratios.
The test tokenizer's four-byte grammar and 17 framing tokens are invented fixture
units only; they are not estimates for any actual model.

Trusted `Rates` provide nonnegative integer upper bounds per input/output token
and a fixed per-call fee, in one named operator unit. A positive spending cap is
mandatory; no rate or cap is defaulted. `all_charges_bounded=True` asserts that the
bounds include every billable component, including hidden/reasoning tokens and
fixed fees. If a provider cannot enforce the output ceiling on all billable output
or supply reliable upper bounds, it must not be integrated. The pre-call reservation is:

```text
input_token_upper_bound * input_rate
+ configured_output_token_ceiling * output_rate
+ fixed_call_fee <= spending_cap
```

No cache discounts or optimistic observed completion length reduce that reservation.
Over-budget requests make zero provider calls. Excess actual usage is rejected;
post-response checks cannot retroactively undo a bill, so provider enforcement and
accurate upper bounds are part of the trusted contract. Each API invocation has one
call and one reservation; no persistent cumulative ledger is provided. Trusted
orchestration must cap total invocations/spend across runs and not auto-retry errors
or timeouts. There are no invented credit prices or cost-savings claims.

Fresh wire validation, deterministic eligibility, complete packet creation,
tokenization, provider invocation and response validation occur in the supervised
worker. Caller reports, cached previews, packets and internal analyses are not
accepted as eligibility authority. Immutable serialized request bytes prevent a
provider from mutating the broker's packet/citation authority through an argument.
The parent reads a bounded raw OS pipe using a monotonic deadline; it never
unpickles or executes output. It independently checks the egress grammar. The
worker discards stdout/stderr, clears environment, closes ordinary inherited
file descriptors and disables core dumps. It is killed/reaped on deadline/error;
normal exits are reaped too. No descendants are allowed by the trusted provider
contract; service process controls must enforce this, not Python inheritance.
The deadline covers worker work, not OS scheduling guarantees, bounded parent
configuration serialization, or an uninterruptible kernel/reaping stall. No
production availability or hard real-time guarantee is claimed.

### Separate advisory result and states

The envelope has exactly `broker_version: offline-editorial-broker/1`, `state`,
`message` and `advice`. `message` is reconstructed from a fixed table; `advice` is
null except for assessed/cache-hit, where it is the unchanged validated model
response contract. `render` emits only fixed messages, enum categories, file IDs,
line ranges and fixed recommendation templates. No provider names, request text,
exceptions, URLs, usage prose or raw reasoning is echoed. This envelope cannot
replace a deterministic report and has no admission exit code.

| State | Meaning |
|---|---|
| disabled | Default, no worker or provider call |
| withheld | Missing/mismatched explicit consent or unsafe/ineligible input |
| cache-hit | Freshly revalidated NON-AUTHORITATIVE cached advice; no provider call or human correctness claim |
| assessed | Offline double returned validated enum/citation advice; not a real-model evaluation |
| invalid-response | Malformed/oversized response, extra fields, invalid citation/prose or inconsistent usage |
| unavailable | Missing/unreliable configuration, provider/tokenizer, HTTP target, worker error or deadline |
| budget-exceeded | Full packet/request/input/output/spending bounds exceeded; no retry/truncation |

### Optional private advisory cache

`scripts/advisory_cache.py` implements separate enum/citation storage inside the
already-isolated service only. It reuses the existing private no-follow filesystem
guards, not the deterministic report codec. `report_cache.PrivateCache`, mechanical
report schema 2.1, admission, eligibility and deterministic preview semantics are
unchanged. There is no cache CLI, service launcher or candidate-selectable path.

`Config.advisory_cache` defaults to `None`, with no cache filesystem activity.
Trusted orchestration may supply a frozen `CacheConfig(directory, source_root,
trusted_root, source_wire_sha256, provider_config_sha256, allow_call_on_miss=False)`.
All directories must already exist inside the service's permitted filesystem;
cache, captured-source root and installed-tooling root must be disjoint. The cache
leaf must be owner-only `0700`, with safe owner/root ancestry and no followed links.
The API wire contains no source path: **the trusted collector/orchestrator must bind
the actual captured source root to the exact wire digest**. A user-supplied path or
hash cannot establish that relationship; candidate input must never construct this
configuration. Missing/inconsistent binding or provider identity refuses caching.
`provider.cache_identity` must match the independently approved digest of its entire
implementation/configuration, including settings not represented by the model name.
These identities are trust inputs, not self-attestation or cryptographic proof.

Consent fingerprints include all cache configuration, generation and data-handling
settings. Fresh worker-side consent/config checks, wire decode, complete deterministic
eligibility, packet construction, target/tokenizer checks and full token/spend
reservation precede every cache access. The key binds exact wire, packet and serialized
request; rubric, question, rules, schema, full catalog digest; exact model/tokenizer
revisions and provider configuration identity; broker version, all config fields,
token/spend/deadline/call limits, input/output/cache bounds, generation parameters
and data-handling settings. Fixed generation is temperature 0, top-p 1, seed 0
(integer milli-units in the request); data handling remains offline/no logging/no raw
retention/private enums only. These are offline contract settings, not live-provider
support claims. Settings changes require renewed consent and invalidate reuse.

Entries contain only format, context key, creation time and validated enum/citation
advice, never raw packet/candidate text, readable paths, provider names or free-form
response prose. Reads require strict JSON and the exact key, revalidate the response
against the freshly constructed packet and file/line bounds, and recount visible
output tokens. Fixed `cache-hit` output is explicitly non-authoritative and cannot
replace a deterministic report, imply a fresh model call or prove human correctness.
Same-owner syntactically valid forgery can still pass: hashes do not authenticate
advice or establish truth. Tests explicitly demonstrate that limitation.

Storage uses 32 separately named write-once slots, each at most 24 KiB (768 KiB
maximum created slot data, excluding filesystem overhead). Files are exclusive
no-follow `0600` regular single-link files, with pinned directory/inode checks and
bounded reads. Reuse expires after one hour; future timestamps are rejected.
Expiry is **not erasure**. Missing, corrupt, expired, colliding, full, inaccessible
or partial slots conservatively miss and are never scanned, pruned or overwritten.
Retirement requires a separate explicit owner operation; no automatic deletion exists.

By default a cache miss returns fixed `unavailable` and makes no call. Only explicit
`allow_call_on_miss=True`, bound into consent and the key, permits the existing single
bounded **offline** provider call; there is no retry or fallback to a live provider.
Valid assessed advice may be written once; persistence failure does not convert it
into a cache hit or retry the call. Changing miss policy changes the key too.
All cache work remains under worker deadline supervision. This does not provision
service storage or establish runtime isolation; deployment verification stays gated.

## Editorial benchmark: human judgments required

`tests/editorial_fixtures.json` has eight synthetic contrast cases, two each for
scope, clarity, usefulness and differentiation. Stimuli and fixed enum suggestions
are author-designed test material, **not reviewed human ground truth**. Even the
seemingly clear/bounded cases need real human evaluation of false-positive burden.
The fixed rendered suggestion comes from the broker's recommendation table; no
live model is called. `tests/benchmark_editorial.py` only scores submitted ratings:

```sh
python3 -B /absolute/trusted/coco-skills/skills/review-skill-sflabs/tests/benchmark_editorial.py
python3 -B /absolute/trusted/coco-skills/skills/review-skill-sflabs/tests/benchmark_editorial.py --judgments /absolute/private/human-judgments.json
```

No judgments means exit 3, status `unmeasured`, rated 0 and null metrics. A human
must review each stimulus and fixed suggestion, then supply a JSON object with
exact keys `format: human-editorial-judgments/1`, `corpus_sha256` (printed by the
unmeasured run), and `ratings`. Each rating has exactly `case_id`,
`human_reviewed: true`, boolean `accepted`, integer `usefulness` 1-5, boolean
`false_positive`, and integer `review_seconds` 0-3600. Accepted and false-positive
cannot both be true. Missing/duplicate/unknown cases, changed corpus, wrong types,
extra fields and oversized input above 64 KiB fail closed with fixed diagnostics.

Output reports exact rational aggregate/per-dimension acceptance rate, mean
usefulness, mean usefulness among accepted suggestions, false-positive rate and
review seconds. Zero accepted suggestions yield null accepted-usefulness, not a
fabricated denominator. Ratings are supplied claims, not authenticated identities:
status remains `human-rated-unverified`, `human_judgments_verified` remains false.
No ratings are shipped; test ratings only verify arithmetic and must never be
reported as measured usefulness. No live quality, false-positive, cost or latency
benchmark has been run. Future actual model comparisons need a separately approved
synthetic dataset, endpoint, immutable model, call cap, rate evidence and total cost
ceiling, and human judgments of the actual suggestions.

## Eight-workstream release acceptance matrix

This matrix records local implementation evidence, not deployment or release approval.
Deployment approval is separate from review of this draft implementation.
Tests below are relative to the reviewed repository test directory; the security
inventory in `.github/scripts/reviewer_test_support.py` independently requires
named boundary tests and fails if required cases are missing/skipped.

| Workstream | Local implementation and test evidence | Deferred or blocked acceptance |
|---|---|---|
| 1 Baseline/requirements | User scope and explicit local constraints; current source/Git inventory; pre-WS8 rerun 222 tests, 220 passed, 2 Docker skips in 20.678 s. Mount-free stdin design retained. | Maintainer-confirmed policy and external ownership still unapproved; baseline is not release certification. |
| 2 Selection/bounds/coverage | `test_reviewer.py`, `test_ci.py`: immediate-child links, renames/deletions, no-follow collection, supervised collection deadline, fixed egress, CI input/required-test inventory. Runtime network assertion now requires `/sys/class/net` to contain only `lo`; offline positive/negative controls test that same assertion, including rejection of `eth0`, without external probes. | Two real Docker tests not executed: `ContainerSmokeTests.test_valid_review_inside_container` and `test_runtime_write_network_and_privilege_boundaries`; approved image/runtime prerequisite absent. |
| 3 Policy/license | `test_policy.py`: single matrix, legacy metadata compatibility, complete pinned source normalization, secret/claim checks and dependency layout. | Legal applicability/canonical Apache wording/substitutions, distribution rights and dependency artifact hashes/apt lock remain unresolved. No legal approval inferred. |
| 4 Reports/provenance | `test_reports.py`: field-specific fixed guidance, schema 2.1, name/content/path/image/catalog binding, private no-overwrite locator, output races/canaries; coverage/status invariants and correctly hashed partial-report rejection. Shared LF citation bounds tested across host, packet and diagnostics. | Real approved launcher/image installation/runtime evidence absent; ACLs/same-user authenticity and secure deletion not proved. |
| 5 PR/static/catalog | `test_workstream5.py`: full-bundle base/head, conservative deltas and trusted renames, bounded Markdown, deterministic partial-catalog neighbors. | Real CI rollout/representative human false-positive calibration, full/current corpus coverage and workflow termination proof not provided. |
| 6 Packaging/Action | `test_workstream6.py`: deterministic snapshot, disjoint roots/exact digest, synthetic fork event/tree rehearsal, fixed Action evidence; inert workflow unchanged. Static archive-to-Dockerfile contract checks private `0600` files and explicit `COPY --chown=65532:65532` for all runtime scripts/resources. This is source verification, not an image build/readability test. | No install/build/publish, real fork run or authoritative external workflow/ruleset verified. Bootstrap, owner/provisioning/rights and checkout-source review remain gated; merge-group binding unsupported. |
| 7 Analysis/cache/performance | `test_workstream7.py`: immutable shared analysis, unchanged report hashes, private preview keys/permissions/races/bounds, explicit nonauthority and exit 3; historical measured synthetic comparison above. Final-review LF correction changes the packet golden to `396e3a8ccb833f8a3bb9b2f795b04772b7b4c92d00213f861b5a4aedb8e03a5b`; reconstructing only the legacy line counts reproduces the previous packet golden. | Real cold/warm provisioning/container startup/RSS unmeasured. Dependency/build-layer reuse not measured; no persistent workers/pools. Cache TTL is not erasure; occupied slots require owner retirement. |
| 8 Optional inference/final evidence | `test_workstream8.py`, `test_advisory_cache.py`, existing `test_model_contract.py`: default-off offline API, exact consent/config/target/tokenizer, full fresh eligibility, conservative token/spend gates, deadline/reaping, bounded non-pickle egress, enum/citation validation; eight-case human-rating harness. Optional private advisory cache adds full-context invalidation, trusted source/provider binding, conservative miss policy, private bounded write-once storage and freshly validated non-authoritative hits. | HTTP adapter, selected endpoint/model, credentials, isolated broker/storage deployment and runtime tests, real human ratings, live benchmark and cross-run spend ledger are not implemented/authorized. No inference launch or real transmission. |

### Final local verification (2026-10-21)

`REVIEWER_TEST_IMAGE= PYTHONDONTWRITEBYTECODE=1 python3 -I -B -m unittest discover -s /absolute/trusted/coco-skills/skills/review-skill-sflabs/tests -q`
ran **295 tests in 25.848 seconds: 293 passed, 0 failures/errors, 2 Docker skips**.
Docker verification was explicitly declined: no Docker start, check, build or image
operation was performed. The two required container tests remain skipped, not verified.
The optional advisory cache adds 31 synthetic regression tests. All 109 independently
required test IDs appear exactly once among the 295 discovered tests, with no loader
errors; the cache inventory test checks this without invoking Docker. Twenty cache
boundary cases were added to the required inventory; no previous required tests were
removed. Deterministic report/packet goldens and preview-cache regression tests pass.
The earlier 264-test result (262 passed, two Docker skips) predates advisory caching.
This local suite does not establish runtime verification while the required Docker
cases remain skipped.

The earlier offline editorial harness run returned exit 3, `unmeasured`, eight expected / zero rated,
null aggregate/per-dimension metrics, zero live-model calls and unverified human
judgments. Corpus SHA-256:
`ba6c9c96c48da34a7a811cf1cc101e82ce8ade8dd36ade7c862e36e693f2eea4`.
No human utility/false-positive results or live latency/cost measurements exist.
Tracked `git diff --check` and Git whitespace checks for untracked files
reported no whitespace diagnostics. These results cover local synthetic validation,
not a Docker build, production deployment, or live-provider evaluation. Publishing
this draft for review does not satisfy the release gates below.

Release acceptance requires all of the following external evidence, not more local
claims: independently reviewed/provisioned launcher+image pair; zero missing/failing/
skipped required runtime tests; policy/legal/distribution and dependency/SBOM review;
external authoritative CI control and bootstrap/fork/bypass verification; approved
summary/retention handling; maintainer/security approval and written release-owner
sign-off. These gates remain **blocked**, and the initiative is not shipped/closed.
Rollback means an independently approved compatible earlier launcher/image pair,
never candidate execution. Inference stays independently disabled. Live inference,
if later requested, needs a new endpoint/disclosure/credential/runtime/cost approval.

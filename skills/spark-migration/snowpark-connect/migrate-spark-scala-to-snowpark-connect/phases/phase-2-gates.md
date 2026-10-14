# Phase 2a: Coverage Verification and Deterministic Fallback (MUST RUN)

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 2a: Coverage Verification and Deterministic Fallback

<!-- SNOW-3375304: Ensure 100% file coverage after Phase 2 -->
<!-- SNOW-3383533: Scala deterministic fallback — header + import annotations + session init + EWI -->
<!-- fallback runs HERE (post-fixer), not during planning -->
**Run the fallback hard gate — only now that the fixers have completed.** This is the deterministic safety net for any file the fixer sub-agents missed:

```bash
python3 <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --phase 2 --run-fallback \
  --language scala
```

Read the printed coverage report:

- If it reports `Coverage: 100%` — proceed to the compilation gate below.
- If it lists `MISSING` files — escalate to the user; files are absent even after fallback.

The fallback script applies deterministic transformations only to files the fixer did **not** record as done (`phases_completed["2_fixes"].files_done` / `pending_files`):
- Copies the original source to `Output/` if not already present
- Injects a SCOS migration header block comment (Scala `/* ... */` style)
- Annotates `org.apache.spark`, `com.databricks`, and `io.delta` imports with `// SCOS: [SPRKCNTSCL0099]` comments
- Replaces `SparkSession.builder()...getOrCreate()` with `SnowparkConnectSession.builder().getOrCreate()` in entry-point files and injects `import com.snowflake.snowpark_connect.client.SnowparkConnectSession` — the canonical SCOS Scala session form expected by the Phase 3 import-updater and the `verify_phase.py --phase 3` gate (it deliberately does **not** emit vanilla `SparkSession...remote()`, which that gate rejects)
- Appends a `SPRKCNTSCL0099` EWI entry to `analysis.json` for each fallback file

> If many files land in fallback, that signals a fixer/dispatch problem — investigate rather than treating the fallback output as a clean migration.

**Gate**: All manifest files must exist in `<MIGRATED>`. `migration_state.json` field `orchestrator_coverage_verified` is set to `true` by the orchestrator when coverage is 100%.

### Phase 2b: Compilation Verification Gate (MUST RUN)

<!-- SNOW-3379886: Hard gate ensuring 100% compilation after code fixes -->

**This phase MUST run after Phase 2a, on every workload, with no exceptions.**
Skipping it lets broken syntax ship to the customer's `Output/` directory. Even
single-file workloads must run the gate.

**Checklist** (do every step in order; do not skip steps):

- [ ] Run the compilation script below. Capture the final `fail_count` value
      **and the reported `compile_mode`**.
- [ ] For each `COMPILE_FAIL` line, the script reverts the file to its
      `phase-1-complete` tag state via `git show`.
- [ ] If `fail_count > 0`, re-dispatch `agents/fixer.md` on **only** the reverted
      files, then re-run the script. Repeat until `fail_count == 0` or you have
      iterated 3 times.
- [ ] Write to `migration_state.json`:
  ```json
  "phases_completed": {
    "2b_compilation": {
      "status": "passed",
      "fail_count_initial": <M>,
      "reverted_count": <N>,
      "iterations": <K>,
      "compile_mode": "<type_check | parse_only | tokenizer>",
      "classpath_used": "<jar path or null>"
    }
  }
  ```
  AND write the legacy top-level field for backward compat:
  ```json
  "compilation_reverted_count": <N>
  ```
- [ ] If you cannot run this phase for any reason (e.g. `<MIGRATED>` is empty,
      git checkpoint missing), set:
  ```json
  "phases_completed": {
    "2b_compilation": {
      "status": "skipped",
      "skip_reason": "<one-line reason>"
    }
  }
  ```
  and **STOP** — do not advance to Phase 3. Escalate to the user.

**Compilation script (portable across macOS / Linux / Windows):**

The `revert_failing_scala_files.py` helper checks every `*.scala` under
`<MIGRATED>` (`pathlib.rglob`, whitespace-safe) in one of three modes, best
first:

1. **`type_check`** — `scalac -classpath <snowpark-connect-java-client.jar> -Ystop-after:typer`.
   Catches the highest-value Scala error class: type mismatches and unresolved
   symbols introduced by Spark→SCOS API changes. Runs only when a working Scala
   compiler is available (on `PATH`, or resolved via Coursier with
   `--bootstrap-coursier`) **and** the client JAR is resolvable. The resolved
   compiler **and** the JAR are each smoke-tested on a trivial snippet before
   use, so a broken compiler or an incompatible JAR safely degrades the mode
   rather than mass-reverting good files against a bad toolchain.
2. **`parse_only`** — `scalac -Ystop-after:parser`. Catches syntax errors only;
   **type errors pass through silently.** Used when `scalac` is present but no
   JAR was found.
3. **tokenizer fallback** — brace/paren/string balance check, when no working
   `scalac` could be resolved.

When a check fails the file is reverted to `phase-1-complete` via `git show`.

**Enable `type_check` mode (do this — do not let it silently degrade):**
`type_check` needs two things — a working `scalac` and the full SCOS
**classpath** — and the script resolves both best-effort:

- **Compiler.** A `scalac` already on `PATH` is used as-is. Otherwise, pass
  `--bootstrap-coursier` (or set `SCOS_BOOTSTRAP_COURSIER=1`) to let the script
  launch `scalac` via Coursier — the same bootstrap path used by Phase 0.5. The
  first launch downloads a JVM + scala once (cached). Coursier use is **opt-in**:
  without the flag, behavior is unchanged on machines that lack `scalac`.
- **Classpath.** Real type-checking needs the SCOS client JAR **plus**
  `spark-connect-client-jvm` and its transitive deps (~38 JARs — that is what
  provides `org.apache.spark.sql.*`, the API the migrated code compiles against).
  A single client JAR alone only resolves `SnowparkConnectSession`, so it usually
  degrades to `parse_only`. With `--bootstrap-coursier`, the script
  **auto-resolves the whole classpath** (`cs fetch --classpath
  spark-connect-client-jvm_2.12:<spark> …` plus a direct download of the client
  JAR). Tune versions with `--spark-version` / `--scos-version`. You can also
  supply a classpath yourself via `--classpath`: a single JAR path, a full
  `os.pathsep`-joined classpath string, or `@FILE` to read one from a file.

> NOTE: the published `snowpark-connect-java-client` POM leaves
> `${scala.binary.version}` unsubstituted in its artifact filename, so a plain
> `cs fetch <coordinate>` of the client JAR fails. The script (and the recipe
> below) work around it by downloading the correctly named JAR directly from
> Maven Central.

Run the sweep — `--bootstrap-coursier` self-provisions both `scalac` and the
full classpath:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/revert_failing_scala_files.py \
  --migrated <MIGRATED> \
  --phase-tag phase-1-complete \
  --bootstrap-coursier \
  --json
```

**Bounded compiler-feedback repair (do this before accepting any revert).** A
straight revert throws away a whole file for a trivial slip (a dropped bracket,
a missing `.asJava`) the LLM itself introduced. So give the fixer **one** shot at
its own compiler errors before reverting:

1. **Diagnose first** — run the gate with `--no-revert`. It compiles every file and
   emits `failures` plus a `diagnostics` map (`{file: scalac error text}`)
   **without** reverting anything:
   ```bash
   uv run --project <SKILL_DIRECTORY> \
     python <SKILL_DIRECTORY>/scripts/revert_failing_scala_files.py \
     --migrated <MIGRATED> --bootstrap-coursier --no-revert --json
   ```
   **Before doing anything else**, inspect the `diagnostics` values. If every
   error is a missing project-internal class or third-party library that was
   absent from the classpath *before* Phase 2 (e.g. `object utils is not a
   member of package`, `object udojava is not a member of package com`) —
   not a type error on a line the fixer touched — then all failures are
   pre-existing. Mark Phase 2b as `skipped` with the reason and advance to
   Phase 3. **Do not run the revert-enabled gate in this case.**
2. **Repair once** — for each file in `diagnostics` that has a genuine fixer
   regression (a type error on a line the fixer changed), re-invoke the Phase 2
   fixer on that file with its compiler error appended as feedback
   ("You broke this file. Here is the scalac error: …. Fix it."). This is a
   **single** bounded pass — do not loop.
   Note: files in `quarantined_manual` are unsupported-RDD (Bucket A) — do **not**
   try to repair them; they stay annotated for manual refactor.
3. **Gate + revert** — run the gate normally (no `--no-revert`). Anything that
   *still* fails to compile is now genuinely broken and is reverted to
   `phase-1-complete`.

The gate itself stays fully deterministic (no LLM inside it); the repair is the
orchestrator's job, exactly like the Phase 2 verifier re-run loop.

If you prefer to resolve the classpath once and reuse it (e.g. offline CI), build
a classpath file and pass it with `@`:

```bash
# Runs in the CoCo bash sandbox (Linux/macOS) — not portable to Windows cmd.exe/PowerShell.
CS=~/.cache/scos/coursier/cs   # or any cs/coursier on PATH
JAR=~/.cache/scos/jars/snowpark-connect-java-client_2.12-1.0.0.jar
mkdir -p "$(dirname "$JAR")"
curl -sSf -o "$JAR" \
  https://repo1.maven.org/maven2/com/snowflake/snowpark-connect-java-client_2.12/1.0.0/snowpark-connect-java-client_2.12-1.0.0.jar
DEPS=$("$CS" fetch --classpath org.apache.spark:spark-connect-client-jvm_2.12:3.5.6 org.slf4j:slf4j-api:2.0.16)
echo "$JAR:$DEPS" > ~/.cache/scos/scos_typecheck_classpath.txt
# then: ... revert_failing_scala_files.py --classpath @$HOME/.cache/scos/scos_typecheck_classpath.txt --bootstrap-coursier --json
```

**Production / CI — enforce the strongest gate:** add `--require-type-check` to
make the script **exit 3** if it cannot run in `type_check` mode (missing
compiler or classpath). This turns silent degradation into a loud failure so the
compile gate — not the LLM fixer — is the authoritative backstop. Use it once
you have confirmed the toolchain resolves; omit it for best-effort local runs.

Exit code is the final `fail_count` (capped at 255; `3` is reserved for the
`--require-type-check` enforcement failure above). The JSON payload reports
`fail_count`, `failures`, `reverted`, `quarantined_manual`, `scalac_available`,
`compile_mode` (`type_check` | `parse_only` | `tokenizer`), `compile_strategy`,
`classpath_used`, and `target_dirs_removed`. Read `compile_mode` — if it is
`parse_only` or `tokenizer`, type errors were **not** caught; record that in the
state block and warn the user that runtime validation (Phase 5) is the only
remaining type-correctness backstop.

`quarantined_manual` lists files that failed to compile **only** because they
carry a Bucket-A unsupported-RDD marker (`// SCOS: [SPRKCNTSCL1500] … manual
refactor required`). These are **not** reverted and **not** counted in
`fail_count` — RDD APIs have no SCOS equivalent (no client-side RDD), so the
original code is equally broken and a revert would just erase the annotation.
Surface them to the user as **manual-intervention** items (see
`../../references/scala/rdd-conversion.md`), not as migration failures.

**Hard gate (all of the following MUST be true to advance to Phase 3):**

1. Final `fail_count == 0` after the last iteration.
2. `migration_state.json["phases_completed"]["2b_compilation"]["status"] == "passed"`.
3. The legacy field `migration_state.json["compilation_reverted_count"]` is also set.

If any of these is false, do NOT advance. Either re-iterate or mark `skipped`
with a reason and escalate.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2b: compilation gate passed (reverted_count=<N>)"`

### Phase 2c: Evidence-Based Verification Gate (MUST RUN)

<!-- SNOW-3383532: single, evidence-based writer of Partial Migration findings -->
**This phase MUST run exactly ONCE, after Phase 2b and after all fixer
re-dispatching is complete.** Do NOT run it inside the per-chunk dispatch loop
— doing so persists partial labels into `analysis.json` before the async fixer
has finished, producing stale/false partials.

The self-reported completion in `migration_state.json` (`processed_files` /
`files_done`) is not proof a file was migrated — only that the agent attempted
it. This gate cross-checks the state against on-disk evidence and reconciles
both artifacts to the truth. It is the **sole writer** of Partial Migration
findings (`SPRKCNTSCL0099`). A file is marked done only by the genuine fixer,
so its recorded completion state is itself trustworthy evidence.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --run-verification --language scala
```

This runs `verify_migration.py --write --language scala`, which:
- Classifies every file from evidence: `migrated` (a real `// SCOS:` fixer marker is present, OR the file is recorded done with Spark surface), `partial` (has Spark surface / real findings but no genuine fixer edit and not recorded done), `trivial` (no Spark surface), `not_attempted` (file missing from `Output/` and therefore not produced by the migration flow).
- Writes ONE verified `SPRKCNTSCL0099` finding per genuinely-partial file into `analysis.json` and records it in `needs_human_action`; clears any stale Partial-Migration noise and falsely-flagged migrations.
- Re-verifies and prints `disagreements = 0` on success.

If any file appears as `not_attempted`, Phase 2's coverage gate should already
have caught it. Treat that as a hard failure and escalate to the user — do NOT
advance to Phase 3 or Phase 4.

After the gate passes, record the Phase 2c milestone in `migration_state.json`:

```json
"phases_completed": {
  "2c_verification": {
    "status": "passed",
    "disagreements": 0,
    "not_attempted": 0,
    "needs_human_action": ["<relative path>", "..."],
    "verified_human_action_count": <N>,
    "recorded_migrated_count": <M>
  }
}
```

**Gate**: the command must print `Re-verify after reconcile: disagreements = 0`
and must NOT print a `Not attempted` section. The files listed in
`needs_human_action` are the genuine human-action items for the report.

`2c_verification` is **not** in `REQUIRED_PHASES_SCALA` (see
`scripts/validate_migration_state.py`) but it IS in `REQUIRED_PHASES_PYTHON`.
For maximum migration quality, treat it as required for Scala too. It is the
sole writer of `SPRKCNTSCL0099` partial-migration findings and prevents stale
noise from polluting the final Issues.csv.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2c: evidence-based verification reconciled"`

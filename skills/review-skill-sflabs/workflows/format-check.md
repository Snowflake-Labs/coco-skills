# Workflow: Format Check

> Mechanical YAML and file-structure validation for the target skill. Outputs a list of findings keyed by check id, severity, and disposition (mechanical fix vs. flag). The router applies mechanical fixes per `references/mechanical-fix-rules.md` after this workflow returns.

## Inputs

- `<skill_dir>` — path to the target skill directory containing `SKILL.md`.

## Outputs

A list of findings, each with:

- `check_id` — string id (e.g. `format.required_fields`)
- `severity` — `blocking` | `advisory`
- `evidence` — short description of what was found
- `fix` — `mechanical` (auto-applicable per rules) or `manual` (flag for contributor)
- `suggested_change` — the proposed value or rewrite, when applicable

## Steps

### Step 1: Parse frontmatter as YAML

Read `<skill_dir>/SKILL.md`. Extract the YAML block between the first `---` and the second `---`. Parse it.

If parsing fails, emit:

```
{check_id: "format.frontmatter_yaml_parses", severity: "blocking", fix: "manual",
 evidence: "<the YAML parser error>"}
```

Stop and return immediately. Every later check depends on the parsed frontmatter.

### Step 2: Required fields

Confirm all of the following keys exist with non-empty values:

`name`, `title`, `summary`, `description`, `tools`, `prompt`, `language`, `status`, `author`, `type`

For each missing key, emit:

```
{check_id: "format.required_fields.<key>", severity: "blocking", fix: "manual",
 evidence: "<key>: missing|empty",
 suggested_change: "<one-line template appropriate for that field>"}
```

`language` and `status`, when missing, are mechanical (see `references/mechanical-fix-rules.md`):

```
{check_id: "format.required_fields.language", severity: "blocking", fix: "mechanical",
 suggested_change: "language: en"}
```

### Step 3: Length limits

Check:

- `title` length ≤ 30 characters
- `summary` length ≤ 140 characters

For each over-limit field, emit `severity: blocking, fix: manual` with a suggested truncation. The agent does not silently rewrite human-facing copy.

### Step 4: Name format

Confirm `name` matches the folder name (case-sensitive). If it does not match, emit:

```
{check_id: "format.name_matches_folder", severity: "blocking", fix: "mechanical",
 suggested_change: "name: <folder-name>"}
```

Confirm `name` is lowercase, hyphens-only, no underscores, no more than 3 hyphens. If it violates these rules, emit `severity: blocking, fix: manual` (renaming the folder has side effects the contributor must drive).

### Step 5: Description shape

Check whether `description` starts with `Use when`. If it starts with any of `Use for`, `Used to`, `For `, `When ` (without `Use`), emit:

```
{check_id: "format.description_use_when", severity: "advisory", fix: "mechanical",
 suggested_change: "<replace leading verb with 'Use when'>"}
```

Run regex `\b(I|My|my|We|we|Our|our|Us|us)\b` against `description`. For any match, emit:

```
{check_id: "format.description_first_person", severity: "advisory", fix: "manual",
 evidence: "<the matched word(s)>",
 suggested_change: "<rewritten description without first-person>"}
```

Search `description` for `Triggers:` (case-insensitive). If absent, emit:

```
{check_id: "format.description_has_triggers", severity: "advisory", fix: "manual",
 suggested_change: "<append a Triggers: list with 4-6 trigger phrases>"}
```

### Step 6: Resolve sibling-file references

Scan the body of `SKILL.md` (everything after the second `---`) for these patterns:

- `references/<path>` — relative reference to a file under `<skill_dir>/references/`
- `workflows/<path>` — relative reference to a file under `<skill_dir>/workflows/`
- `scripts/<path>` — relative reference to a file under `<skill_dir>/scripts/`

For each referenced path that does not exist on disk, emit:

```
{check_id: "format.referenced_file_missing", severity: "blocking", fix: "manual",
 evidence: "<the referenced path>"}
```

The agent does not fabricate workflow or reference content.

### Step 7: Reject `skill_metadata/`

If `<skill_dir>/skill_metadata/` exists, emit:

```
{check_id: "format.no_skill_metadata", severity: "blocking", fix: "manual",
 evidence: "skill_metadata/ directory present",
 suggested_change: "Remove the skill_metadata/ directory before committing."}
```

This directory is internal-only seed data and must not be published.

### Step 8: LICENSE present

If `<skill_dir>/LICENSE` does not exist:

- If the parsed `type:` value is `community` (or missing), emit:
  ```
  {check_id: "format.license_present", severity: "blocking", fix: "mechanical",
   suggested_change: "Create LICENSE with Apache 2.0 (community contribution)"}
  ```
- If `type:` is `snowflake`, emit:
  ```
  {check_id: "format.license_present", severity: "blocking", fix: "manual",
   suggested_change: "Add the appropriate Snowflake employee license file"}
  ```

## Summary status

After running all 8 steps, the workflow's overall status for the report is:

- 🟢 — zero findings
- 🟡 — only `advisory` findings, all `mechanical` fixes applied cleanly
- 🟠 — any `blocking` finding remains after mechanical fixes are applied

Pass that status to the router.

## Notes

- The router applies mechanical fixes after this workflow returns, per the disposition table in `references/mechanical-fix-rules.md`. This workflow only identifies issues — it does not edit files.
- The router asks the contributor to confirm before applying any fix.

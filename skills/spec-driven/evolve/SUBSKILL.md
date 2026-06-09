
# Evolve Spec Sub-Skill

Modify or extend an existing feature that was previously built through the spec-driven workflow. This skill detects drift between spec documents and the actual codebase, amends specs in-place, and implements only the delta.

## CRITICAL RULE: One Phase at a Time

Even if the user asks for all deliverables at once, produce ONLY the current phase's output, stop for approval, then proceed to the next phase. NEVER create files or begin implementation before the current phase is approved.

## Core Principles

1. **Never delete requirements** — deprecated requirements are marked `DEPRECATED`, never removed
2. **Never renumber requirements** — new requirements append (REQ-028, REQ-029, etc.)
3. **Amend in-place** — existing spec files are updated, not replaced or duplicated
4. **Implement only the delta** — only new/changed requirements generate implementation tasks
5. **Full validation** — validation covers ALL requirements (original + amendments)

## When to Use

- Adding a new page or section to an existing app
- Modifying an existing requirement (e.g., changing a chart type)
- Removing a feature that was previously specified
- Code has drifted from the spec (files added/removed outside the skill)
- User wants to update spec documents to reflect reality

---

## Workflow

### PHASE 1: DETECT — Analyze Spec Drift

**Objective**: Load existing spec artifacts, compare against the actual codebase, and present findings.

**Actions**:

1. **Locate existing spec**: Search `specs/` for the feature matching the user's request
   - Read `requirements.md`, `design.md`, `tasks.md`
   - Extract: REQ count, file paths, function signatures, data sources

2. **Scan actual codebase**:
   - Glob for all files in the project directory
   - Compare file list against `design.md` file structure
   - For each file in the spec, check if it still exists
   - For each file in the codebase, check if it's in the spec
   - Read key files and compare function signatures against `design.md` module APIs

3. **Classify drift**:
   - **New files**: Files in codebase not documented in spec
   - **Missing files**: Files in spec that no longer exist
   - **Modified APIs**: Function signatures that differ from spec
   - **New data sources**: Tables/connections not in the original spec
   - **User-requested changes**: What the user explicitly wants to add/modify/remove

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT modify any spec files yet.
Present the drift report and WAIT for explicit approval:

```
Spec-Drift Report for: {feature name}

Existing Spec:
- Requirements: {N} (REQ-001 through REQ-{N})
- Design: {file count} files, {module count} modules
- Status: {status from frontmatter}
- Created: {date}

Drift Detected:
{For each category of drift, list specific findings}

User-Requested Changes:
- {What the user asked to add/modify/remove}

Proposed Amendment Scope:
- New requirements to add: {count}
- Requirements to deprecate: {count}
- Design sections to update: {list}
- Files to create/modify: {list}

Is this analysis correct? Should I proceed to amend the specification?
```

---

### PHASE 2: AMEND SPEC — Update Existing Documents

**Objective**: Amend `requirements.md`, `design.md`, and `tasks.md` in-place to reflect the changes.

**Amendment Rules**:

1. **Frontmatter**: Add `modified` date and `amendments` history
2. **New requirements**: Append after the last existing REQ with the next sequential ID
3. **Deprecated requirements**: Add `**Status**: DEPRECATED` with reason — never delete the section
4. **Modified requirements**: Add `**Status**: AMENDED` with change description and date
5. **Design updates**: Add/modify file entries, API signatures, data flow — mark new sections with `(Amendment {date})`
6. **Tasks**: Append new tasks after existing ones with sequential numbering

**Amending requirements.md**:

Update frontmatter:
```yaml
---
status: approved
created: {original date}
modified: {today's date}
feature: {feature-name}
amendments:
  - date: {today's date}
    description: "{brief description of changes}"
    reqs_added: [REQ-028, REQ-029]
    reqs_deprecated: [REQ-005]
    reqs_amended: [REQ-006]
---
```

For new requirements, append after the last existing requirement:
```markdown
---

### Amendment: {Date} — {Brief Description}

#### REQ-{next ID}: {New Requirement Title}

**Type**: {type}
**Added**: {date}

**Statement**:
WHEN {condition}
THE SYSTEM SHALL {behavior}
SO THAT {value}

**Acceptance Criteria**:
- [ ] {Criterion 1}
- [ ] {Criterion 2}
```

For deprecated requirements, add status to existing section (do NOT delete):
```markdown
#### REQ-005: {Original Title}

**Status**: DEPRECATED ({date})
**Reason**: {Why this requirement is being removed}

{Original content preserved below for traceability}
...
```

For amended requirements, add amendment note:
```markdown
#### REQ-006: {Original Title}

**Status**: AMENDED ({date})
**Change**: {What changed and why}

**Statement** (updated):
WHEN {updated condition}
THE SYSTEM SHALL {updated behavior}
SO THAT {updated value}
```

**Amending design.md**:

- Update the file structure section to reflect new/removed files
- Add new module API entries for new files
- Update existing module entries if signatures changed
- Add `(Amendment {date})` marker next to changed sections
- Update the data flow diagram if data sources changed

**Amending tasks.md**:

- Keep all existing completed tasks as-is (historical record)
- Append new tasks with sequential numbering (Task 12, 13, etc.)
- Update frontmatter status to `in-progress`

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT begin implementation.
Present the amended specification to the user and WAIT for explicit approval:

```
Specification amended for: {feature name}

Requirements:
- Total: {original + new} ({new count} added, {deprecated count} deprecated, {amended count} amended)
- New: {list new REQ IDs with titles}
- Deprecated: {list deprecated REQ IDs with reasons}
- Amended: {list amended REQ IDs with changes}

Design:
- Files added: {list}
- Files removed: {list}
- APIs changed: {list}

New Implementation Tasks:
- Task {N}: {description}
- Task {N+1}: {description}

Do you approve these amendments?
```

Do NOT proceed until user approves. After approval, update frontmatter to reflect approved status, then proceed to Phase 3.

---

### PHASE 3: IMPLEMENT — Execute Only the Delta

**Objective**: Implement only the new/changed requirements. Do not re-implement existing unchanged code.

**Actions**:
1. Create new tasks in `tasks.md` (appended after existing completed tasks)
2. Work through each new task sequentially
3. Mark tasks complete as implemented

**Implementation Rules**:
1. **Delta only**: Only implement new requirements and changes to amended requirements
2. **Preserve existing**: Do NOT modify code that implements unchanged requirements
3. **Integrate cleanly**: New code must follow the existing architecture (same layers, same patterns)
4. **Update imports**: If new modules are added, update any necessary imports in existing files
5. **One task at a time**: Complete and verify each task before starting the next

**Progress Tracking**:
```markdown
## Amendment Implementation ({date})

### Task {N}: {Description}
- Status: COMPLETE
- Changes: {summary}
- New REQs covered: REQ-028

### Task {N+1}: {Description}
- Status: IN PROGRESS
```

**⚠️ MANDATORY STOPPING POINT** — After all new tasks complete, present results and WAIT for approval:

```
Amendment implementation complete.

New/Modified Files:
- {file}: {change summary}

Unchanged Files (verified intact):
- {file}: No modifications

Ready for validation?
```

---

### PHASE 4: VALIDATE — Verify Full Spec (Original + Amendments)

**Objective**: Validate ALL requirements — original, amended, and new. Verify deprecated requirements are actually removed.

**Validation Checklist**:

```markdown
## Evolution Validation: {Feature Name}

### Original Requirements (unchanged)
- [ ] REQ-001: {title} - STILL PASSING
- [ ] REQ-002: {title} - STILL PASSING
...

### Amended Requirements
- [ ] REQ-006: {title} - VERIFIED (updated behavior)
...

### New Requirements
- [ ] REQ-028: {title} - VERIFIED
- [ ] REQ-029: {title} - VERIFIED
...

### Deprecated Requirements
- [ ] REQ-005: {title} - CONFIRMED REMOVED (code no longer implements this)
...

### Architecture Compliance
- [ ] Layer separation maintained (data/compute/presentation)
- [ ] New code follows existing patterns
- [ ] No regressions in unchanged functionality
- [ ] Spec documents accurately reflect codebase

### Spec Document Accuracy
- [ ] requirements.md REQ count matches implementation
- [ ] design.md file list matches actual files
- [ ] design.md API signatures match actual code
- [ ] tasks.md reflects all work done
```

**⚠️ MANDATORY STOPPING POINT** — Do NOT commit or finalize without approval.
Present validation results and WAIT for user confirmation:

```
Evolution validation complete for: {feature name}

Original Requirements: {X}/{Y} still passing
Amended Requirements: {X}/{Y} verified
New Requirements: {X}/{Y} verified
Deprecated Requirements: {X}/{Y} confirmed removed
Architecture: {PASS/FAIL}
Spec Accuracy: {PASS/FAIL}

{Any issues found}

Ready to finalize?
```

After user approval, update all spec frontmatter:
- `requirements.md`: `status: approved`, `modified: {date}`
- `design.md`: `status: approved`, `modified: {date}`
- `tasks.md`: `status: complete`, `modified: {date}`

---

## Quick Reference

**Phase Progression**:
DETECT → AMEND SPEC → IMPLEMENT (delta only) → VALIDATE (full)

**Key Principle**: Specs are living documents. Evolve amends them in-place — never deletes, never renumbers, always preserves traceability.

**Amendment Markers**:
- New requirement: `**Added**: {date}` in the requirement section
- Deprecated requirement: `**Status**: DEPRECATED ({date})` with reason
- Amended requirement: `**Status**: AMENDED ({date})` with change description
- Design changes: `(Amendment {date})` marker next to changed sections

**When NOT to use Evolve**:
- The change is a bug fix → use `bugfix/SKILL.md`
- The change is purely structural (no behavior change) → use `refactor/SKILL.md`
- The change is so large it's effectively a new feature → use `feature/SKILL.md` with a new spec folder

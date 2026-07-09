
# Feature Spec Sub-Skill

Create comprehensive specifications for new features following structured SDLC phases.

## CRITICAL RULE: One Phase at a Time

Even if the user asks for all deliverables at once ("give me requirements, design, and tasks"), produce ONLY the current phase's output, stop for approval, then proceed to the next phase. NEVER create files belonging to later phases before the current phase is approved.

- Phase 2 creates `requirements.md` only — do NOT create `design.md` or `tasks.md`
- Phase 3 creates `design.md` only — do NOT create `tasks.md`
- Phase 4 creates `tasks.md` and begins implementation

If the user requests all files at once, acknowledge the request and explain: "I'll produce these one phase at a time so you can review and approve each before I proceed."

## Workflow

### PHASE 1: CLARIFY

**Objective**: Gather complete requirements through structured questions.

**Questions to Ask** (adapt based on context):

1. **User Story**: Who is this for and what do they need?
   - "As a [role], I want [capability] so that [benefit]"

2. **Scope**: What's included and excluded?
   - Core functionality (must have)
   - Nice-to-have (if time permits)
   - Explicitly out of scope

3. **Constraints**: What limitations exist?
   - Technical constraints (APIs, libraries, performance)
   - Business constraints (timeline, compliance)
   - Existing code dependencies

4. **Success Criteria**: How do we know it works?
   - Testable acceptance criteria
   - Edge cases to handle
   - Error scenarios

5. **Integration Points**: What does this touch?
   - Existing systems affected
   - APIs consumed or exposed
   - Data models involved

**Actions**:
1. Ask clarifying questions (use `ask_user_question` tool)
2. Explore codebase to understand context (use Task tool with `Explore`)
3. Document answers in structured format

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT create any spec files yet.
Present the following to the user and WAIT for explicit approval:
```
Here's what I understand about the feature:

**User Story**: {story}
**Scope**: {in-scope items}
**Out of Scope**: {excluded items}
**Constraints**: {limitations}
**Success Criteria**: {criteria}

Is this understanding correct? Should I proceed to create the specification?
```

Do NOT proceed until user confirms with "yes", "correct", "proceed", or similar. Do NOT create `requirements.md` until this gate is passed.

---

### PHASE 2: SPECIFY

**Objective**: Create formal specification with EARS requirements.

**Actions**:
1. Create spec folder: `specs/features/{feature-name}/`
2. Generate `requirements.md` with EARS format
3. List all acceptance criteria as checkboxes

**Template** (write to `specs/features/{feature-name}/requirements.md`):

```markdown
---
status: draft
created: {date}
feature: {feature-name}
---

# Feature: {Feature Name}

## Overview
{Brief description of the feature and its purpose}

## User Story
As a {role}, I want {capability} so that {benefit}.

## Requirements

### REQ-001: {Primary Requirement}

**Type**: Event-Driven

**Statement**:
WHEN {user action or system event}
THE SYSTEM SHALL {observable behavior}
SO THAT {business value}

**Acceptance Criteria**:
- [ ] {Criterion 1}
- [ ] {Criterion 2}
- [ ] {Criterion 3}

### REQ-002: {Secondary Requirement}
{Repeat pattern...}

## Out of Scope
- {Item 1}
- {Item 2}

## Dependencies
- {Dependency 1}
- {Dependency 2}

## Open Questions
- [ ] {Any unresolved questions}
```

**NOTE**: This phase creates `requirements.md` ONLY. Do NOT create `design.md` or `tasks.md` here — those belong to later phases.

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT create files for subsequent phases.
Present the following to the user and WAIT for explicit approval:
```
I've created the feature specification at `specs/features/{name}/requirements.md`.

Key requirements:
1. {REQ-001 summary}
2. {REQ-002 summary}
...

Do you approve this specification? Any changes needed?
```

Do NOT proceed until user approves. After approval, update `requirements.md` frontmatter to `status: approved`, then proceed to Phase 3.

---

### PHASE 3: DESIGN

**When to use**: This phase applies when the feature involves:
- Multiple files to modify or create
- New architectural patterns
- External integrations
- Database or data model changes

If unsure whether a design document is needed, ASK the user: "This feature touches multiple areas. Would you like me to create a technical design document before implementation?"

If the feature is simple (single file, straightforward change), skip to Phase 4 with user confirmation.

**Actions**:
1. Analyze existing codebase patterns
2. Identify all files to modify/create
3. Create technical design document

**Template** (write to `specs/features/{feature-name}/design.md`):

```markdown
---
status: draft
created: {date}
---

# Technical Design: {Feature Name}

## Approach
{High-level approach description}

## Files to Modify
| File | Changes |
|------|---------|
| `path/to/file.ts` | {Description of changes} |

## Files to Create
| File | Purpose |
|------|---------|
| `path/to/new.ts` | {Purpose} |

## Data Flow
{Description or diagram of data flow}

## API Changes
{New endpoints, modified signatures, etc.}

## Testing Strategy
- Unit tests: {approach}
- Integration tests: {approach}

## Rollback Plan
{How to undo if needed}
```

**NOTE**: This phase creates `design.md` ONLY. Do NOT create `tasks.md` here — that belongs to Phase 4.

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT create files for subsequent phases.
Present the design document to the user and WAIT for explicit approval.

Do NOT proceed until user approves. After approval, update `design.md` frontmatter to `status: approved`, then proceed to Phase 4.

---

### PHASE 4: IMPLEMENT

**Objective**: Execute specification task by task.

**NOTE**: `tasks.md` is created at the START of this phase — it is an implementation planning artifact, not a spec artifact. It should only be created after requirements (and design, if applicable) have been approved.

**Actions**:
1. Create `tasks.md` with implementation checklist
2. Work through each task sequentially
3. Mark tasks complete as implemented

**Template** (write to `specs/features/{feature-name}/tasks.md`):

```markdown
---
status: in-progress
created: {date}
---

# Implementation Tasks: {Feature Name}

## Tasks

- [ ] Task 1: {Description}
  - File: `path/to/file`
  - Changes: {What to change}
  
- [ ] Task 2: {Description}
  - File: `path/to/file`
  - Changes: {What to change}

## Progress Log

### {Date}
- Completed: {task}
- Notes: {any observations}
```

**Implementation Rules**:
1. Complete ONE task at a time
2. Update task checkbox immediately after completion
3. If task fails, document why and ask user for guidance
4. Keep code changes minimal and focused

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed to validation without presenting results.
After all tasks complete, present the following and WAIT for approval:
```
Implementation complete. Summary of changes:

**Files Modified**:
- `file1.ts`: {change summary}
- `file2.ts`: {change summary}

**Files Created**:
- `newfile.ts`: {purpose}

Ready for validation. Should I run the validation checklist?
```

---

### PHASE 5: VALIDATE

**Objective**: Verify implementation meets specification.

**Checklist**:
```markdown
## Validation Checklist

### Requirements Coverage
- [ ] REQ-001: {requirement} - VERIFIED
- [ ] REQ-002: {requirement} - VERIFIED

### Acceptance Criteria
- [ ] {Criterion 1} - PASS/FAIL
- [ ] {Criterion 2} - PASS/FAIL

### Code Quality
- [ ] No hardcoded values that should be configurable
- [ ] Error handling for edge cases
- [ ] Consistent with existing code patterns
- [ ] No security vulnerabilities introduced

### Testing
- [ ] Manual testing completed
- [ ] Unit tests added (if applicable)
```

**⚠️ MANDATORY STOPPING POINT** — Do NOT commit or finalize without approval.
Present validation results and WAIT for user confirmation:
```
Validation complete:
- Requirements: {X}/{Y} verified
- Acceptance Criteria: {X}/{Y} passed
- Code Quality: {status}

{Any issues found}

Ready to commit? Or changes needed?
```

---

## Quick Reference

**Phase Progression**:
CLARIFY → SPECIFY → [DESIGN] → IMPLEMENT → VALIDATE

**Approval Required**: After each phase

**Files Created**:
- `specs/features/{name}/requirements.md`
- `specs/features/{name}/design.md` (if complex)
- `specs/features/{name}/tasks.md`


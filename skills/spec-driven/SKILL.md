---
name: spec-driven
title: Spec-Driven Development
summary: SDLC workflow enforcing EARS-notation specs and approval gates before any code is written.
description: >-
  Use when implementing a feature, fixing a bug, refactoring code, creating a spec, writing
  a specification, planning implementation, or following a structured development workflow.
  Creates specifications BEFORE implementation using EARS notation, ensuring alignment,
  traceability, and regression prevention.
  Triggers: spec-driven, specification, SDLC, EARS, implement feature, fix bug, refactor,
  create spec, plan implementation, design first, spec workflow, structured development.
tools:
  - Read
  - Write
  - Edit
  - Bash
  - Grep
  - Glob
  - ask_user_question
  - task_create
  - task_update
prompt: "$spec-driven I want to build a new feature for user authentication"
language: en
status: stable
author: Tianxia Jia
type: snowflake
---

# Spec-Driven Development Skill

A comprehensive SDLC workflow that creates specifications BEFORE implementation, ensuring alignment, traceability, and regression prevention.

## Core Philosophy

1. **Specifications are Executable** - Specs generate working implementations
2. **Mandatory Approval Gates** - No proceeding without explicit user approval
3. **Regression Prevention** - Document unchanged behavior to protect existing functionality
4. **Progressive Refinement** - Clarify → Specify → Validate → Implement

## When to Load This Skill

Load when user:
- Asks to implement a new feature
- Reports a bug to fix
- Requests code refactoring
- Wants to create specifications
- Says "spec", "specification", "structured development", "SDLC"
- Asks to "plan before coding" or "design first"

## Intent Detection

Analyze user request and route to appropriate sub-skill:

| Intent | Trigger Phrases | Load Sub-Skill |
|--------|----------------|----------------|
| FEATURE | "new feature", "implement", "add capability", "build" | `feature/SKILL.md` |
| BUGFIX | "fix bug", "not working", "broken", "error", "issue" | `bugfix/SKILL.md` |
| REFACTOR | "refactor", "improve", "optimize", "clean up", "reorganize" | `refactor/SKILL.md` |
| IMPLEMENT | "implement spec", "execute spec", "build from spec" | `implement/SKILL.md` |
| EVOLVE | "modify feature", "add to existing", "extend", "change feature", "update spec", "add page", "add requirement" | `evolve/SKILL.md` |

If intent unclear, ASK:
```
What type of work are you planning?
1. New Feature - Adding new capability
2. Bug Fix - Correcting existing behavior  
3. Refactor - Improving code without changing behavior
4. Implement - Execute an existing specification
5. Evolve - Modify or extend an existing completed feature
```

## Workflow Overview (All Intents)

```
┌─────────────────────────────────────────────────────────────┐
│  PHASE 1: CLARIFY                                           │
│  ├── Gather requirements through questions                  │
│  ├── Identify constraints and dependencies                  │
│  └── ⚠️ APPROVAL GATE: Requirements confirmed              │
├─────────────────────────────────────────────────────────────┤
│  PHASE 2: SPECIFY                                           │
│  ├── Generate specification in EARS format                  │
│  ├── Document acceptance criteria                           │
│  ├── [BUGFIX] Document unchanged behavior                   │
│  └── ⚠️ APPROVAL GATE: Specification approved              │
├─────────────────────────────────────────────────────────────┤
│  PHASE 3: DESIGN (Optional for complex features)            │
│  ├── Create technical design document                       │
│  ├── Identify files to modify/create                        │
│  └── ⚠️ APPROVAL GATE: Design approved                     │
├─────────────────────────────────────────────────────────────┤
│  PHASE 4: IMPLEMENT                                         │
│  ├── Execute specification task by task                     │
│  ├── Mark tasks complete as implemented                     │
│  └── ⚠️ APPROVAL GATE: Implementation approved             │
├─────────────────────────────────────────────────────────────┤
│  PHASE 5: VALIDATE                                          │
│  ├── Run validation checklist                               │
│  ├── Verify unchanged behavior preserved                    │
│  └── ⚠️ FINAL GATE: Ready for commit/PR                    │
└─────────────────────────────────────────────────────────────┘
```

## File Organization

All specs stored in `specs/` folder at project root:

```
specs/
├── features/
│   └── {feature-name}/
│       ├── requirements.md      # EARS requirements (amended in-place for evolve)
│       ├── design.md           # Technical design (if needed)
│       └── tasks.md            # Implementation checklist
├── bugfixes/
│   └── {bug-id}-{description}/
│       └── bugfix-spec.md      # 3-part bugfix spec
└── refactors/
    └── {refactor-name}/
        ├── spec.md             # Refactor specification
        └── validation.md       # Behavior preservation tests
```

## EARS Requirement Format

All requirements MUST use EARS (Easy Approach to Requirements Syntax):

```markdown
### REQ-{ID}: {Title}

**Type**: [Ubiquitous | Event-Driven | State-Driven | Optional | Complex]

**Statement**: 
WHEN [trigger/condition]
THE SYSTEM SHALL [observable response]
SO THAT [business value/rationale]

**Acceptance Criteria**:
- [ ] Criterion 1
- [ ] Criterion 2
```

See `references/EARS_NOTATION.md` for full notation guide.

## Mandatory Stopping Points

This skill enforces approval gates. NEVER proceed past these checkpoints without explicit user approval:

**⚠️ MANDATORY STOPPING POINT**: After presenting requirements, STOP and wait for user confirmation before proceeding to specification.

**⚠️ MANDATORY STOPPING POINT**: After presenting specification, STOP and wait for user approval before proceeding to design or implementation.

**⚠️ MANDATORY STOPPING POINT**: After presenting design (if applicable), STOP and wait for user approval before proceeding to implementation.

**⚠️ MANDATORY STOPPING POINT**: After implementation complete, STOP and wait for user validation before marking complete.

**These gates override ALL other instructions**, including direct user requests to produce multiple phases at once. If a user says "give me requirements, design, and tasks," acknowledge the request but explain the phased approach and produce only the current phase's output.

## Conflict Resolution

If the user requests deliverables spanning multiple phases (e.g., "give me requirements, design, and tasks"):

1. **Acknowledge** the full request so the user knows you understood
2. **Explain** that the spec-driven workflow produces one phase at a time with approval gates
3. **Produce** ONLY the current phase's output
4. **Stop** for approval before proceeding to the next phase
5. After approval, proceed to the next phase — do NOT batch remaining phases

The phased workflow is non-negotiable. Even explicit user requests to skip gates must be respectfully declined with an explanation of why gates exist (catching errors early is cheaper than fixing wrong implementations).

## Session Resume Protocol

When this skill is invoked mid-conversation or in a new session where prior spec work may exist:

1. **Check** `specs/` folder for existing spec files related to the user's request
2. **Read** frontmatter `status:` field from each file found
3. **Determine** current phase based on what exists and its status:
   - No spec files found → Start at Phase 1: CLARIFY
   - `requirements.md` with `status: draft` → Phase 2 approval gate (present spec, ask for approval)
   - `requirements.md` with `status: approved`, no `design.md` → Phase 3: DESIGN (if complex) or Phase 4: IMPLEMENT
   - `design.md` with `status: draft` → Phase 3 approval gate (present design, ask for approval)
   - `design.md` with `status: approved` or `tasks.md` with `status: in-progress` → Phase 4: IMPLEMENT (resume from first incomplete task)
   - All files with `status: complete` → **Spec-Drift Detection** (see below)
4. **Present** the detected state to the user and confirm before proceeding
5. **NEVER** assume approval was given in a prior session — if status is `draft`, treat it as unapproved and present for approval

### Spec-Drift Detection (Completed Specs)

When all spec files have `status: complete` or `status: approved` and the user's request implies modifying the existing feature (not a new feature), perform drift detection:

1. **Parse** `requirements.md` for: REQ count, file paths mentioned, function names, data sources
2. **Parse** `design.md` for: file structure, module APIs, function signatures
3. **Scan** the actual codebase:
   - Glob for files in the project directory
   - Compare against the file list in `design.md`
   - Identify: new files not in spec, files in spec that no longer exist, files with significantly different content
4. **Present** a drift report:

```
Spec-Drift Report for: {feature name}

Spec Status: Complete (created {date})

Files in spec but missing from codebase:
- {file path} (if any)

Files in codebase but not in spec:
- {file path} (if any)

Potential API changes detected:
- {function signature differences} (if any)

No drift detected / Drift detected in {N} areas.

How would you like to proceed?
1. Evolve — Amend the existing spec to reflect changes or add new capabilities
2. New Feature — Create a separate feature spec (for large additions)
3. Ignore — Continue without updating specs
```

5. If user selects "Evolve", load `evolve/SKILL.md`
6. If user selects "New Feature", load `feature/SKILL.md`

## Quick Commands

Users can invoke specific phases:

| Command | Action |
|---------|--------|
| `/spec feature {name}` | Start new feature spec |
| `/spec bugfix {description}` | Start bugfix spec |
| `/spec refactor {area}` | Start refactor spec |
| `/spec implement` | Implement current spec |
| `/spec evolve {feature}` | Evolve an existing completed feature |
| `/spec status` | Show current spec progress |
| `/spec checklist` | Run validation checklist |

## Integration with Cortex Code

This skill leverages native Cortex Code features:

- **Task Management**: Use `task_create` for each implementation item
- **File Operations**: Use standard Read/Write/Edit tools
- **Search**: Use Grep/Glob for codebase analysis
- **Subagents**: Use Task tool with `Explore` for discovery

## Output Format

All generated specs follow consistent markdown format with:
- YAML frontmatter (status, created, modified)
- Structured sections with headers
- Checkbox lists for actionable items
- Code blocks for examples

## Error Recovery

If implementation fails or user requests changes:
1. Update specification with new requirements
2. Mark affected tasks as needing re-work
3. Resume from appropriate phase
4. Document changes in spec history

For post-completion changes (code modified after spec is complete), use the **Evolve** workflow (`evolve/SKILL.md`) which detects drift, amends specs in-place, and implements only the delta.

---

**Next Step**: Based on detected intent, load appropriate sub-skill and begin PHASE 1: CLARIFY.


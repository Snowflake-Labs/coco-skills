# Spec-Driven Development Skill for Cortex Code

A comprehensive SDLC workflow skill that enforces **specifications before implementation**, ensuring alignment, traceability, and regression prevention.

## Why Spec-Driven Development?

AI coding assistants are powerful, but without structure they can:
- Start coding before requirements are clear
- Miss edge cases and error scenarios
- Introduce regressions while fixing bugs
- Make changes that don't align with user intent

This skill solves these problems by enforcing a structured workflow with **mandatory approval gates** at each phase.

## Features

- **EARS Notation** - Industry-standard requirement syntax (`WHEN...THE SYSTEM SHALL...SO THAT`)
- **Mandatory Stopping Points** - AI cannot proceed without explicit user approval
- **3-Part Bugfix Specs** - Documents Current, Expected, and Unchanged behaviors
- **Regression Prevention** - Explicit documentation of what must NOT change
- **Spec Evolution** - Detect drift and amend specs when features change post-completion
- **Per-Feature Organization** - Clean folder structure in `specs/`
- **Validation Checklists** - Pre-built checklists for quality assurance

## Installation

```bash
# From this repo
cortex skill add ./skills/spec-driven

# Verify it's installed
cortex skill list
```

## Usage

### Invoke the Skill

```
/spec-driven
```

Or simply describe what you want to do - the skill auto-detects intent:

| You Say | Skill Routes To |
|---------|-----------------|
| "Build a user dashboard" | Feature workflow |
| "The login button is broken" | Bugfix workflow |
| "Clean up the auth module" | Refactor workflow |
| "Implement the spec we created" | Implementation workflow |
| "Add a new page to the stock app" | Evolve workflow |

### Workflow Overview

```
┌─────────────────────────────────────────────────────────────┐
│  PHASE 1: CLARIFY                                           │
│  ├── Gather requirements through questions                  │
│  ├── Explore codebase for context                           │
│  └── ⚠️ APPROVAL GATE: Requirements confirmed              │
├─────────────────────────────────────────────────────────────┤
│  PHASE 2: SPECIFY                                           │
│  ├── Generate specification in EARS format                  │
│  ├── Document acceptance criteria                           │
│  └── ⚠️ APPROVAL GATE: Specification approved              │
├─────────────────────────────────────────────────────────────┤
│  PHASE 3: DESIGN (Complex features only)                    │
│  ├── Create technical design document                       │
│  └── ⚠️ APPROVAL GATE: Design approved                     │
├─────────────────────────────────────────────────────────────┤
│  PHASE 4: IMPLEMENT                                         │
│  ├── Execute specification task by task                     │
│  └── ⚠️ APPROVAL GATE: Implementation complete             │
├─────────────────────────────────────────────────────────────┤
│  PHASE 5: VALIDATE                                          │
│  ├── Run validation checklist                               │
│  └── ⚠️ FINAL GATE: Ready for commit                       │
└─────────────────────────────────────────────────────────────┘
```

## Spec Types

### Feature Specs

For new capabilities and functionality.

```markdown
### REQ-001: User Authentication

**Type**: Event-Driven

**Statement**:
WHEN a user submits valid credentials
THE SYSTEM SHALL create a session and redirect to dashboard
SO THAT users can access protected resources

**Acceptance Criteria**:
- [ ] Valid credentials create session
- [ ] Invalid credentials show error message
- [ ] Session expires after 24 hours
```

### Bugfix Specs (3-Part Format)

The key innovation: explicitly documenting **Unchanged Behavior** prevents regressions.

```markdown
## Part 1: Current Behavior (What's Wrong)
WHEN user clicks "Export"
THE SYSTEM CURRENTLY throws a 500 error
RESULTING IN users unable to export data

## Part 2: Expected Behavior (The Fix)
WHEN user clicks "Export"
THE SYSTEM SHALL generate and download a CSV file
SO THAT users can export their data

## Part 3: Unchanged Behavior (Regression Prevention)
WHEN user clicks "Export" with no data
THE SYSTEM SHALL CONTINUE TO show "No data to export" message
AS IT DOES TODAY
```

### Refactor Specs

For code improvements that must preserve existing behavior.

```markdown
## Preserved Behaviors

### PB-001: API Contract
**Current Signature**: `getUser(id: string): Promise<User>`
**Callers**: auth.ts, profile.ts, admin.ts
**Verification**: All callers still compile and tests pass
```

### Evolve Specs

For modifying or extending an existing completed feature. Specs are amended in-place — requirements are never deleted or renumbered.

```markdown
### Amendment: 2026-03-01 — Add Options Analysis page

#### REQ-028: Options Chain Display

**Type**: Event-Driven
**Added**: 2026-03-01

**Statement**:
WHEN a user navigates to the Options Analysis page
THE SYSTEM SHALL display the options chain for the selected stock
SO THAT users can analyze available options contracts

**Acceptance Criteria**:
- [ ] Options chain shows calls and puts
- [ ] Strike prices sorted ascending
- [ ] Expiration dates selectable
```

## Skill Structure

```
spec-driven/
├── SKILL.md                 # Main entry point with intent detection
├── feature/
│   └── SKILL.md            # Feature specification workflow
├── bugfix/
│   └── SKILL.md            # Bugfix specification workflow
├── refactor/
│   └── SKILL.md            # Refactor specification workflow
├── implement/
│   └── SKILL.md            # Implementation workflow
├── evolve/
│   └── SKILL.md            # Evolve existing feature (detect drift, amend specs)
└── references/
    ├── EARS_NOTATION.md    # EARS syntax guide
    ├── SPEC_TEMPLATES.md   # Copy-paste templates (includes amendment templates)
    └── CHECKLIST.md        # Validation checklists
```

## EARS Notation Quick Reference

| Type | Pattern | Example |
|------|---------|---------|
| Ubiquitous | `THE SYSTEM SHALL` | `THE SYSTEM SHALL encrypt all passwords` |
| Event-Driven | `WHEN...SHALL` | `WHEN user clicks Save, THE SYSTEM SHALL persist data` |
| State-Driven | `WHILE...SHALL` | `WHILE logged in, THE SYSTEM SHALL show user menu` |
| Optional | `WHERE...SHALL` | `WHERE dark mode enabled, THE SYSTEM SHALL use dark theme` |
| Negative | `SHALL NOT` | `THE SYSTEM SHALL NOT store plaintext passwords` |

## Best Practices

1. **Never skip the Clarify phase** - Ambiguous requirements lead to rework
2. **Keep specs atomic** - One feature/bug per spec
3. **Document unchanged behaviors** - Especially for bugfixes
4. **Use checkboxes** - Track progress visibly
5. **Update spec status** - Mark as draft → approved → complete
6. **Evolve, don't abandon** - When modifying a completed feature, use the evolve workflow to amend specs in-place rather than creating a new spec or letting docs go stale

## Requirements

- [Cortex Code CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/cortex-code/overview) v1.0.19 or later

---

Built for [Cortex Code](https://docs.snowflake.com/en/developer-guide/snowflake-cli/cortex-code/overview) by Snowflake.

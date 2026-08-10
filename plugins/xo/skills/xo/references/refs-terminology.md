---
name: refs-terminology
description: "Standard terminology across xo-* skills. Defines the lexicon for artifacts, actions, and concepts."
---

# XO Cortex Terminology

Standard terms used across xo-* skills. Consistent terminology helps agents understand what artifacts to produce and helps operators know what to expect.

## Artifacts

| Term | Nature | Audience | Purpose |
|------|--------|----------|---------|
| **Brief** | Summary for decision-making | Operator | Present findings, recommendations, or status for approval/awareness |
| **Spec** | Intent/requirements document | Agent (current or future) | Define what should be built and why |
| **Findings** | Raw discoveries, not yet synthesised | Internal/working | Capture what was learned during investigation |
| **Notes** | Persistent working memory | Agent recovery | Accumulated understanding of a work item |
| **Diary** | Session activity log | Agent recovery | What happened, when, organised by session |
| **Task** | Work item state file | Agent recovery | Current state, next action, status of a work item |

### Artifact Hierarchy

```
Findings → Brief → Operator decision
   ↓
Notes (persisted for recovery)
```

- **Findings** are raw — what you discovered
- **Brief** synthesises findings for the operator — what it means and what you recommend
- **Notes** persist understanding across context resets — what the agent needs to remember

## Actions as Verbs

These terms describe processes, not outputs:

| Verb | Meaning | Output |
|------|---------|--------|
| **Assess** | Evaluate against criteria | Brief |
| **Analyse** | Investigate in depth | Findings (then Brief) |
| **Review** | Examine for correctness/quality | Brief |
| **Investigate** | Explore to understand | Findings |
| **Validate** | Confirm against reference | Brief |

Example: "Assess the PR" → process is assessment → output is a brief.

## Roles

| Term | Meaning |
|------|---------|
| **Operator** | The human directing the work (senior in the relationship) |
| **Agent** | The AI performing the work (reports to operator) |
| **Orchestrator** | The top-level agent coordinating subagents |
| **Subagent** | A specialist agent dispatched for a specific task |

## Recording Terms

| Term | Meaning |
|------|---------|
| **Savepoint** | The act of recording state across all three layers (diary + task + notes) |
| **Context state** | The stored context fullness percentage (triggers savepoint reminders) |
| **Checkpoint** | VS Code's session rollback feature (not an xo-cortex term) |

## Workflow Terms

| Term | Meaning |
|------|---------|
| **Fast-path** | Direct action without full deliberative process |
| **Deliberative** | Multi-agent process (proposer/critic/validator/etc.) for quality assurance |
| **Empirical** | Code-first approach — build then document |
| **Verified** | Tested through execution, not just static analysis |

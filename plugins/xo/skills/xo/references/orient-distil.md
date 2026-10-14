---
name: orient-distil
description: Distil stage — Compiler archetype (xo-bounded-writer). Takes raw evidence files and produces a findings brief, the scoping artifact consumed by Workshop and Decide. Output is findings.md.
---

# Distil

Compile raw evidence into a clean statement of findings. The output — `findings.md` — is a **scoping artifact**: it pins down what was actually observed, so every downstream stage (Workshop, Spec, Build, Document) works from the same grounded basis rather than diverging on assumptions.

## When to use this stage

- You have raw survey or analysis output from Observe and need to synthesise it
- You need a coherent findings brief before designing an approach (Workshop) or writing a spec
- The raw evidence is spread across multiple sub-agent outputs and needs compiling into one document

---

## Input contract

- One or more evidence files (`$XOCORTEX_HOME/tmp/wi{N}/survey-*.md`, `$XOCORTEX_HOME/tmp/wi{N}/analyse-*.md`, notes from observation passes)
- The question that was being investigated
- Optional: the active WI task file for context

---

## Mode: compile (default)

Dispatch `xo-bounded-writer` to compile the evidence into a findings brief. The bounded-writer has no codebase-exploration tools — it can only read its inputs and write its output, which structurally prevents it from adding ungrounded claims.

```
runSubagent(subagent_type: "xocortex:xo-bounded-writer", prompt: "
  PERSONA: Compiler — synthesise evidence into a findings brief
  INPUT_PATH: [path(s) to evidence files — survey outputs, analysis files, observation notes]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/findings.md
  
  Read all evidence files. Write a findings brief that:
  1. States each key finding with the source evidence it traces to
  2. Notes contradictions or conflicting sources explicitly
  3. Lists what was NOT found (negative results)
  4. Identifies gaps — things the evidence doesn't yet address
  5. Preserves trust-signal annotations from the source material
  
  Do not add claims not present in the input evidence. If the evidence has gaps, name them explicitly
  rather than filling them with inference.
")
```

---

## Mode: inline compile (simple cases)

When the evidence is straightforward (single source, short), compile inline as the orchestrator rather than dispatching a sub-agent:

1. Read all evidence files
2. Write `$XOCORTEX_HOME/tmp/wi{N}/findings.md` directly in the format below
3. Present to operator for review before proceeding

Use this for small investigations; use the sub-agent dispatch for rich multi-source evidence.

---

## Output contract

Write a **findings brief** to `$XOCORTEX_HOME/tmp/wi{N}/findings.md`:

```markdown
# Findings — [topic] ([date])

## Key findings

### F1: [finding title]
- **Finding**: [what is true]
- **Evidence**: [source file:line or specific observation]
- **Confidence**: high / medium / low
- **Notes**: [any caveats or trust warnings]

### F2: [finding title]
[...]

## Negative results
- [what was looked for and not found] — prevents re-investigation

## Gaps
- [what the evidence doesn't address]
- [what would need to be investigated to fill this gap]

## Contradictions
- [source A says X; source B says Y — flag for operator resolution]
```

**Every claim in this brief must trace to evidence.** If something isn't in evidence, put it in Gaps, not Findings.

This file is the scoping contract for Workshop and Spec. Do not proceed without operator review.

---

## Next step

Present `$XOCORTEX_HOME/tmp/wi{N}/findings.md` to the operator. Ask: "Does this capture what we found accurately? Ready to proceed to Workshop?"

Then load `references/orient-workshop.md` to design the approach.

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Analyse (and/or Survey) — Distil needs evidence to synthesise.
- **Leads to — primary:** Workshop (explore candidate approaches from the findings).
- **Or:** Spec (if the approach is already clear); Build (empirical-improvement: findings inform what to change); Document (doc-a-feature: Analyse → Distil → Document); Review (self-validation against the findings).

---
name: orient-workshop
description: Workshop stage — dispatch xo-cold-smart agents on parallel angles, then synthesise with the human. Produces the approach proposal and plan artifact. The orchestrator and operator do the final synthesis — no sub-agent provides the recommendation.
---

# Workshop

Parallel exploration of candidate approaches, synthesised into a proposal by the orchestrator and operator together. The sub-agents establish the facts about each angle; the **human makes the judgment call**.

This is the stage where XO's "bounded work to sub-agents; creative synthesis to human" principle is most explicit. Sub-agents tell you what the landscape looks like on each angle; they do not recommend which path to take.

## When to use this stage

- You have findings (from Distil or equivalent) and need to explore the design space
- A proposed approach needs stress-testing before committing to a spec
- The operator has asked "what are our options here?"

---

## Input contract

- `$XOCORTEX_HOME/tmp/wi{N}/findings.md` (from Distil) OR equivalent grounded findings
- The question or problem to explore approaches for
- Optional: a proposed approach to stress-test

---

## Mode: parallel exploration (default)

Dispatch multiple `xo-cold-smart` agents simultaneously — one per angle. Each returns a findings report to its OUTPUT_PATH. All dispatches go in a **single message** to run truly in parallel.

### Default angles

| Angle | Prompt persona | Question to answer |
|---|---|---|
| Existing Patterns | Investigator: Existing Patterns | How does the codebase / system already solve similar problems? What can be reused? |
| Edge Cases | Investigator: Edge Cases | What could go wrong? What are the boundary conditions and failure modes? |
| Alternative Approaches | Investigator: Alternative Approaches | What other solutions exist? What has been tried and abandoned? What are simpler or safer alternatives? |
| Integration Impact | Investigator: Integration Impact | How does the proposed approach affect the rest of the system? Who else needs to change? |

Custom angles replace or supplement defaults per the WI context. Consider whether you want to include any common additions: Security Surface, Performance, Migration Path, Team Impact.

### Dispatch (all in a single message)

**Angle discipline (applies to every dispatched agent):** (1) Cite the evidence for each claim (file, code path, source examined); an unverified claim stated as fact is a silent failure. (2) Report only implications the artifact and findings support; do not invent failure modes the artifact does not exhibit. For the Edge Cases angle: trace error paths present in the code, not hypothetical scenarios. (3) Record off-angle discoveries under Gaps, not in the angle's own section.

**Scratch paths**: OUTPUT_PATHs are under the canonical per-WI scratch dir `$XOCORTEX_HOME/tmp/wi{N}/` — the `Scratch:` path recorded in the WI task file (allocated at WI creation). The orchestrator assigns each agent a unique, fully-formed path under it; agents write there directly with the `write` tool and never create directories. If the dir is somehow missing (legacy/ad-hoc WI), the orchestrator `ls`/`mkdir -p`s it once before dispatching — never invent an alternative path.

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Investigator — Existing Patterns angle
  REFERENCE: [findings brief from Distil or problem description]
  INPUTS: [scope — repo paths, system description, relevant codebase areas]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/workshop-angle-existing-patterns.md
  
  Investigate the Existing Patterns angle. Answer: how does this codebase or system already solve similar problems? What can be reused, and what looks reusable but has hidden constraints?
  Evidence first. Every finding must cite a source. Stay on your angle — note off-angle discoveries under Gaps.
")

runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Investigator — Edge Cases angle
  REFERENCE: [findings brief]
  INPUTS: [scope]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/workshop-angle-edge-cases.md
  
  Investigate the Edge Cases angle. Answer: what could go wrong? Trace error paths, boundary conditions, concurrent access, partial failures, backward compatibility risks.
  Evidence first. Stay on your angle.
")

runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Investigator — Alternative Approaches angle
  REFERENCE: [findings brief]
  INPUTS: [scope]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/workshop-angle-alternatives.md
  
  Investigate the Alternative Approaches angle. Identify at least 2-3 viable alternatives to the proposed solution. For each: describe the approach, assess complexity, identify risks. Check git log and closed PRs for abandoned approaches.
  Evidence first. Stay on your angle.
")

runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Investigator — Integration Impact angle
  REFERENCE: [findings brief]
  INPUTS: [scope]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/workshop-angle-integration-impact.md
  
  Investigate the Integration Impact angle. Trace callers and dependents of modified code. Check API contracts, configuration surface, cross-repo impact. Answer: what breaks or changes behaviour outside the immediate change?
  Evidence first. Stay on your angle.
")
```

---

## Synthesis (orchestrator only — MANDATORY)

**This step is done by the orchestrator in conversation with the operator. Do not dispatch a sub-agent for synthesis.**

After all angle agents return, read all four output files and synthesise:

```markdown
## Workshop — [problem] ([date])

### Angle summaries
- **Existing Patterns**: [key finding] — [implication for the approach]
- **Edge Cases**: [key finding] — [implication]
- **Alternatives**: [key finding] — [implication]
- **Integration Impact**: [key finding] — [implication]

### Cross-angle insights
- [patterns that emerged across multiple angles — e.g., "all three angles point to X as the riskiest aspect"]
- [contradictions that need resolution before choosing]

### Candidate approaches

**A: [name]**
- Description: [what this approach does]
- Pros: [evidence-backed]
- Cons: [evidence-backed]
- Risk: [from edge-cases angle]

**B: [name]**
[...]

### Recommendation
[Approach A/B/C] — because [reasoning citing angle findings].

[State what the human needs to decide: "This recommendation assumes X is acceptable; if not, Approach B is the safer option."]
```

Present to the operator for judgment. The recommendation is yours to make; the decision is theirs.

---

## Mode: single-angle investigation

When only one dimension needs exploration, dispatch a single `xo-cold-smart` with the appropriate angle persona rather than the full four-way or more parallel. Synthesise inline.

---

## Output contract

**Completion condition: grounding.** Workshop is complete when each angle's claims are evidence-backed and every candidate approach's pros, cons, and risks trace to angle findings rather than assertion. Report complications the artifact exhibits; do not invent ones it does not. Set the number of angles and depth by what the downstream decision requires.

Write the synthesis to `notes/{YYYY-MM}/{project}-wi{N}-workshop.md` and update the task NextAction to point to Spec (`references/decide-spec.md`).

---

## Next step

Operator approves the approach → proceed to **Spec** (`references/decide-spec.md`) to write the bounding contract.

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Distil (grounded findings to explore from).
- **Leads to — primary:** Spec (commit the chosen approach to a bounding contract).
- **Or:** back to Analyse (if the angles exposed missing facts that must be established first).

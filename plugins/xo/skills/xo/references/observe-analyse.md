---
name: observe-analyse
description: Analyse stage — investigation of a known target to establish verified facts. Single-agent depth-first by default; parallel dispatch for large targets at the agent's discretion. Produces a verified-facts file.
---

# Analyse

Depth-first investigation of a known target. The goal is to **establish what is actually true** about the subject — through careful reading, tracing, and empirical verification where possible. Not opinions; verified facts with cited sources.

## When to use this stage

- You have a specific target to investigate (from Survey or operator direction)
- You need to establish facts before Distil can synthesise findings

---

## Input contract

- A known target: a repository, a codebase area, a system, a set of files
- The question to answer: "how does X actually work?", "what does Y actually do?", "is Z present?"
- Optional: Survey location map from a prior Survey stage

**Before deep analysis, check for a relevant skill.** If the target involves a Snowflake technology or another area with an available skill, prefer leveraging that skill over hand-rolling the investigation — it encodes the right approach and tools.

**If the target is a code repository and the work is feature-level or involves understanding prior decisions**, surface the attendant-services offer before dispatching — see `references/refs-survey-methods.md` Codebase Investigation section. PR history, issue trackers, and wikis are particularly valuable sources of non-goals (WONTFIX decisions, abandoned approaches, deliberate scope exclusions) that feed directly into the Spec stage.

---

## Mode: investigation

Investigation of a known target. By default, a single depth-first pass following connections level-by-level — but for large targets (a monorepo with distinct areas, a stack of PDFs where contract details could be in any of them) dispatch multiple agents in parallel, each scoped to a distinct area. Use judgment: parallel when one agent can't cover the target thoroughly; depth-first when focused precision is what's needed.

### Dispatch (xo-cold-smart)

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Investigator — establish verified facts about [topic]
  REFERENCE: [the question to answer or the spec to verify against]
  INPUTS: [target path(s)]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/analyse-[topic].md
  
  Investigate [target] to answer: [question].
  For each finding: cite file:line or specific evidence.
  Record what you checked and what you found — including negative results.
  Do not infer or guess; only report what you can cite. Do not speculate beyond the target — invented risks the artifact doesn't support are noise, not findings.
")
```

### Empirical mode

When static analysis isn't sufficient (behaviour under real conditions, build output, test results), add a `xo-cold-fast` Tester to run and report:

```
runSubagent(subagent_type: "xocortex:xo-cold-fast", prompt: "
  PERSONA: Tester — run [command or test] and report the output faithfully
  INPUTS: [what to run, where]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/analyse-empirical-[topic].md
  
  Run [command]. Record the exact output. Note pass/fail counts, errors, and any environment issues.
  Do not interpret — just report.
")
```

The `xo-cold-smart` analysis and the empirical results are then synthesised by the orchestrator into the verified-facts file.

---

## Output contract

**Completion condition: grounding.** Analyse is complete when every reported fact cites the evidence obtained for it (file:line, command output, traced path). Do not report recalled or assumed behaviour as fact — that is a silent failure. Set investigation depth by what the downstream stage requires, not by confidence or token count. Report only facts the evidence supports; do not add speculative risk.

Write **verified facts** to `notes/{YYYY-MM}/wi{N}-analysis.md` (or `$XOCORTEX_HOME/tmp/wi{N}/analyse-*.md` for intermediate per-angle files):

```markdown
## Analysis — [subject] ([date])

### Verified facts
- [fact 1]
  - Evidence: [file:line or command output]
  - Confidence: high/medium/low

### Negative results
- Checked [X] for [Y] — not found / not present

### Trust warnings
- [any fact where the source is stale or uncertain]

### Gaps
- [what remains unknown]
- [suggested follow-up]
```

Present findings to the operator. Ask: "Is this sufficient to proceed to Distil, or should we investigate [specific gap] further?"

**If analysis returned nothing or far less than expected:** do not conclude absence. Check: did your approach cover the full target — different file structures, naming conventions, alternative locations? Test at least one alternative. Then surface the conflict: *"I expected to find [X] but found nothing using [approach]. Because [reason this could be wrong], I also tried [alternative] and found [result]. Possible remaining explanations: [A, B]. Shall I investigate further?"* Incomplete coverage is a gap to flag; a confident negative from an incomplete search is a fabrication.

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Survey (the territory is mapped). Analyse can also be the entry point for a focused, single-target investigation.
- **Leads to — primary:** Distil (synthesise the verified facts into a findings brief).
- **Or:** Workshop (if the facts exposed a genuine design fork); Capture (if it was a one-off learning worth keeping). For traversal patterns and trust signals, load `references/refs-survey-methods.md`.

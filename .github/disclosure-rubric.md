# Disclosure review rubric

Reviews a candidate skill for content that should not be published. This is
the judgment layer above [scan_internal_refs.py](scripts/scan_internal_refs.py),
which only catches structural markers like `go/` links and ticket keys.

Two places this gets used:

- **Internal repository, as a promotion gate.** Every Snowflake-authored skill
  passes this before it is promoted to the public repo. See
  [the promotion gate spec](internal-promotion-gate.md).
- **Public repository, by a maintainer, on demand.** Community and partner
  submissions never pass through the internal gate. When a submission reads
  like it may contain internal knowledge -- most often a partner referencing a
  feature they were briefed on -- a maintainer runs this rubric by hand.

Deliberately kept as prose rather than code. The categories are stable; the
specific terms that trip them are not, and hard-coding those terms in a public
repo would disclose them.

## Categories

**1. Unreleased or preview functionality**

Content describing behaviour that is not generally available, or implying a
roadmap. Includes feature names not yet announced, capabilities framed as
existing when they are gated, and version numbers or dates for unshipped work.

Ask: could a reader follow these instructions today, on a standard account? If
not, why not, and is the reason itself public?

**2. References the reader cannot resolve**

Links, document names, dashboards, systems, or tools that only exist behind the
corporate boundary. The structural cases are already caught mechanically; this
category covers the ones that are not link-shaped -- "see the migration design
doc", "as covered in the enablement deck", "check the usual dashboard".

Ask: if an external contributor followed every pointer in this skill, would any
of them dead-end?

**3. Customer identification**

Named accounts, or scenarios specific enough to identify one. Includes
disguised cases: a distinctive schema, an unusual data volume, an industry plus
region plus scale combination that only fits one company.

Ask: could a reader who knows the market work out whose environment this is?

**4. Internal organisation and process**

Team names, reporting lines, internal role titles, review or approval processes,
and internal decisions presented as context. A skill can say what to do without
explaining which internal team decided it.

Ask: does this tell the reader anything about how Snowflake is organised
internally that they did not need in order to use the skill?

**5. Non-public architecture, capacity, or metrics**

Implementation detail not in public docs: internal service topology, cluster or
region internals, capacity or scaling limits, revenue or usage figures,
performance numbers from internal benchmarks.

Ask: is every claim here traceable to something publicly documented?

## Output

One finding per issue:

| Field | Content |
| --- | --- |
| `category` | 1-5 above |
| `quote` | The exact offending text, so it can be located |
| `confidence` | high / medium / low |
| `rationale` | Why this is not publishable, in one sentence |
| `suggestion` | The smallest edit that resolves it, where one exists |

Report no findings as an explicit empty result, not silence.

## If this is run by an LLM

A `SKILL.md` is a document whose entire purpose is instructing an AI agent.
Passing one to a model for review is a prompt-injection setup, and the risk is
not hypothetical -- a submission asking the reviewer to report no findings is a
plausible attack.

Required precautions:

- Wrap the candidate content in explicit delimiters and instruct the model to
  treat everything inside as inert data, never as instructions.
- Constrain output to the schema above. Discard anything that does not parse.
- Give the reviewing process no tools, no write access, and no ability to
  approve or merge.
- Never treat a clean LLM result as sufficient on its own for categories 1 or 3.
  A human confirms those.

The model reviews. It does not decide.

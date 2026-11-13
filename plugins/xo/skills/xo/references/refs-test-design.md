---
name: refs-test-design
description: "Test design rules: agree the interfaces to test once per work item, trace every test to an acceptance criterion or confirmed interface, test behaviour through the public interface, keep TDD bounded, and the keep/cut/rewrite conditions. Load from the Spec gate (interfaces to test), Prove.author, Build (tests added during Build), the reproduce-bug regression test, and the test-quality Critic."
---

# Test design

Apply these rules when proposing, writing, or reviewing tests. Every test traces to agreed behaviour and checks it through the public interface.

## Agree what to test, once per work item

Before any tests are written, the orchestrator proposes the public interfaces to test. A public interface is what a caller uses: an exported function, a CLI command, an HTTP endpoint, a UI action. For each, give one line on what a test there would catch and one line on what it would miss.

The operator confirms the list at the Spec gate. The confirmed list lives on the spec's "Interfaces to test" line, or in the WI notes when the spec has none. If the spec already lists the interfaces, use that list and add no further gate.

Write tests only against the acceptance criteria and the confirmed interfaces. If no interfaces were confirmed, test only through the interfaces the acceptance criteria name, and record them in the trace.

## Every test traces

Each test traces to an acceptance criterion or a confirmed interface. Record the trace beside the test, in its name or a short comment (for example `# AC2: rejects an expired token`). If you cannot name the trace, do not write the test.

A reproduce-bug regression test traces to the reported symptom, or to the bug's acceptance criterion, and needs no interface confirmation.

## Test behaviour through the public interface

- Call the code the way a caller would, and assert on what the caller can observe: return values, output, stored state read back through the interface, errors raised.
- Take expected values from a source independent of the code: a hard-coded value, an example worked by hand, or the spec. Do not compute the expected value the same way the implementation does, because then the test passes whether the code is right or wrong.
- Assert only values an independent source fixes. If sources disagree, or none gives the value, do not derive it from the code: raise the gap with the operator, or record it as an open question when running unattended.

## TDD stays bounded

In TDD mode, write at most one failing test per acceptance criterion before Build. Further tests are added during Build, one at a time, when new behaviour appears that no existing test covers.

## Mock only at external boundaries

Mock what the project does not own and cannot control in a test: third-party APIs, the clock, randomness. Mock the database or filesystem only when using the real one is impractical, and say why. Do not mock the project's own modules.

## Keep, cut, or rewrite

Mark each test added or changed in this work item keep, cut, or rewrite, and cite the reason. Tests the work item does not touch are out of scope, apart from the superseded tests below. Cut or rewrite a test when it:

- **Has no trace** to an acceptance criterion or a confirmed interface.
- **Is coupled to internals.** It mocks the project's own modules, tests private functions, asserts how many times or in what order internal calls happen, or checks results by some route other than the interface (a log line, a private field).
- **Passes by construction.** Its expected value is derived the same way the code derives it.
- **Duplicates** another test, or checks something the type system already guarantees.
- **Mocks something other than an external boundary** (see above).

Rewrite when the behaviour is worth testing but the test reaches it the wrong way. Cut when the behaviour has no trace or is already covered.

## Replace, don't layer

When a change moves behaviour behind a new interface, list the older tests that the new ones supersede and propose deleting them. Do not leave both sets in place: the old tests pin the old internals and break on refactors that keep behaviour the same.

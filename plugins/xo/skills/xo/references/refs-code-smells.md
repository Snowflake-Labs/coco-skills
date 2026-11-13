---
name: refs-code-smells
description: "Baseline of twelve common code smells, each with what it looks like in a diff and its usual fix, plus the three rules that bind their use. Advisory input to the Standards Critic in Review and Build's general Critic. Load from review or build."
---

# Code smells (baseline)

A smell is a code pattern that often signals a design problem; treat it as a prompt to look closer, not as proof of a defect. Check a diff against this list alongside the repo's documented standards.

## Rules

1. **A documented repo standard overrides this list.** Where the repo's `CODING_STANDARDS.md`, `CONTRIBUTING.md`, `AGENTS.md`, or lint config explicitly allows something a smell below would flag, do not flag it. A standard or lint config that is silent on a pattern does not allow it.
2. **Every smell is a judgement call.** Report it as "possible …" with the hunk it applies to. A smell never on its own justifies request-changes or a REVISE.
3. **Skip what tooling already enforces.** If a linter, formatter, or type checker in the repo catches it, leave it to the tool.

Only flag a smell that the diff introduces or makes worse. Pre-existing code the change does not touch is out of scope.

## The twelve smells

| Smell | What it looks like | Usual fix |
|---|---|---|
| Unclear name | A name that leaves the reader guessing what the function computes, or what the variable or type contains. | Pick a name that states it. If no accurate name fits, the unit is probably doing too much. |
| Duplication | The same logic appears in two or more places in the change, or the change copies logic that already exists elsewhere in the repo. | Pull the shared logic into one function and call it from each place. |
| Envy of another object's data | A function uses another object's fields more heavily than the fields of its own object. | Move the function to the object whose data it uses. |
| Parameters that travel together | The same group of values is passed side by side through several functions. | Bundle them into one type and pass that. |
| Primitive for a domain type | A bare string or number stands in for a concept with its own rules (an ID, a currency amount, a status). | Give the concept a small type of its own. |
| Repeated switch on one type | The same `switch` or `if` chain on one type or tag appears in more than one place. | Replace it with polymorphism, or with a single lookup table every site uses. |
| One change, many files | A single logical change needs small edits scattered across many files. | Gather the parts that change together into one module. |
| One file, unrelated reasons | The diff touches a single file or module for several purposes that have nothing to do with each other. | Split it so each part has a single purpose. |
| Speculative generality | Flexibility built for a future the spec never asks for: extra parameters, extension points, unused layers. | Take it out and keep the code direct; generalise only once a second real use exists. |
| Long call chains | The caller walks a long chain such as `a.b().c().d()` to reach what it needs. | Give the first object a method that does the walk, and call that. |
| Pass-through middleman | A wrapper that adds little beyond handing each call to another object or function. | Have callers use the underlying object or function, and drop the wrapper. |
| Ignored inheritance | A subclass or implementer overrides, stubs out, or never uses the bulk of its parent's behaviour. | Replace the inheritance with composition. |

## Reporting

For each smell found, give: the smell name, the file and hunk, one line on why it applies here, and the usual fix. Keep smells separate from breaches of a documented repo standard, which you cite by file and rule. If nothing is found, say "no smells found".

# Internal promotion gate

Specification for the check that runs in the internal repository before a
Snowflake-authored skill is promoted to this public repo. Implementation is
internal-repo work; this file is the contract, kept here so the public side
documents what it is relying on.

## Why it lives upstream

Opening a pull request against a public repository *is* publication. The diff is
world-readable, forkable, and indexed from the moment the PR opens, and deleting
a commit does not unpublish it. So a disclosure check that runs on a public PR
runs too late by construction.

Two further consequences follow, both of which the public repo benefits from:

- The term list can be specific. A public deny-list of internal codenames
  teaches its readers the codenames; internally, that constraint disappears.
- No Snowflake service credential needs to exist in a public Labs repo's
  Actions, and no prompt-injection surface needs to exist in public CI.

## What it must check

1. Everything in [the disclosure rubric](disclosure-rubric.md), all five
   categories.
2. A private term list -- codenames, unannounced feature names, internal system
   and tool names. Maintained internally; explicitly out of scope for the
   public repo.
3. Everything the public gate already checks, run early so authors find
   structural problems before promotion rather than after:
   [validate_skill.py](scripts/validate_skill.py) and
   [scan_internal_refs.py](scripts/scan_internal_refs.py).

Items 1 and 2 are blocking. Item 3 is advisory internally, since the public gate
enforces it anyway.

## What arrives with a promoted skill

A skill entering the public repo from the internal one should carry, in the PR
description:

- Confirmation that the disclosure gate ran, and on which revision.
- The named person who cleared it. Not a team, and not "CI passed" -- categories
  1 and 3 require human confirmation.
- Any finding that was accepted rather than fixed, with the reason.

Public maintainers do not re-run disclosure review on these. They rely on this
attestation, which is why it names a person.

## Open questions

Needs a decision from the internal-repo owner. These are not rhetorical -- the
gate does not function without answers.

1. **Who owns the gate?** This is now load-bearing: if it does not run, nothing
   downstream catches internal content, because the public repo deliberately
   does not attempt category 1-5 review on employee submissions.
2. **Is it enforced in CI, or a documented manual step?** Manual is acceptable
   at current volume, but then the attestation above is the only evidence it
   happened, and someone has to check that the attestation is present.
3. **What happens on disagreement?** An author who believes a finding is wrong
   needs a path that is not "argue with the reviewer indefinitely".

## Relationship to the bake period

Worth stating plainly, because it answers a question that has been open about
whether internal incubation earns its keep.

Disclosure review is the one check that cannot be relocated downstream. Every
other quality concern -- structure, licensing, trigger hygiene, whether the
skill works -- can be caught as well or better by public PR review, where more
people see it. This cannot, for the reason at the top of this file.

That makes disclosure review a concrete purpose for the internal stage, and it
also sets a floor on what the stage has to deliver: if incubation is reduced to
a waiting period with no disclosure gate attached, it is not buying anything
that public review would not have caught.

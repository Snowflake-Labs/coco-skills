## What this skill does

<!-- What problem does it solve, and for whom? Two or three sentences. -->

**Example prompt that triggers it:**

<!-- e.g. "audit my warehouse spend for last quarter" -->

---

## Attestations

Automation already checks folder structure, frontmatter fields, licence
matching, name collisions, and internal-reference patterns -- so those are not
listed here. If a check fails, the log says what to fix.

These are the things CI cannot verify. Please confirm each one honestly; a
maintainer will ask about any that are unchecked.

### Tested

- [ ] I loaded this skill in a Cortex Code session and ran it against real tasks,
      not just the example prompt.
- [ ] Its behaviour matches the `description`. It triggers when I expect, and
      does not trigger on adjacent requests.

### Safety

- [ ] Any operation that drops, replaces, grants, or overwrites instructs the
      agent to confirm with the user **before** executing. This includes
      `CREATE OR REPLACE` against objects the user named.
- [ ] I have read every file in `scripts/`, if present, and can explain what each
      one does and why any external call it makes is necessary.

### Content

- [ ] No credentials, account identifiers, or connection details in any file --
      including `references/`, `assets/`, and test data.
- [ ] No customer names, and no scenario specific enough to identify one
      customer.
- [ ] Every link resolves for someone outside Snowflake. No internal documents,
      dashboards, wikis, or short links.
- [ ] Nothing here describes functionality that is not generally available. If
      it does, say so below so a maintainer can confirm it is public-safe.

### Scope

- [ ] I checked this does not substantially overlap an existing skill in this
      repo or a bundled Cortex Code skill. If it is adjacent to one, the
      `description` says when **not** to use this skill.

---

## For Snowflake employees

- [ ] This skill was reviewed for internal content before being opened here, per
      [the promotion gate](.github/internal-promotion-gate.md).

**Cleared by:** <!-- name of the person who reviewed it, not a team -->

**Revision reviewed:** <!-- commit SHA or version -->

**Findings accepted rather than fixed:** <!-- none, or list with reasons -->

Public maintainers rely on this attestation rather than repeating the review, so
name a person.

---

## Anything a reviewer should know

<!-- Known limitations, why an unchecked box above is unchecked, a decision you
     would like a second opinion on. -->

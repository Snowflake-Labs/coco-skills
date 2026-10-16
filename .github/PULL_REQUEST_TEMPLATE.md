## What this skill does

<!-- What problem does it solve, and for whom? Two or three sentences. -->

**Example prompt that triggers it:**

---

## Attestations

CI already checks structure, frontmatter, licence, name collisions, and internal-reference patterns. If a check fails, the log says what to fix.

These are the things CI can't verify.

**Tested**

- [ ] I ran this in a Cortex Code session against real tasks, not just the example prompt.
- [ ] It triggers when I expect, and doesn't trigger on adjacent requests.

**Safety**

- [ ] Anything that drops, replaces, grants, or overwrites confirms with the user *before* executing — including `CREATE OR REPLACE` on objects the user named.
- [ ] I've read every file in `scripts/`, if present, and can explain any external call it makes.

**Content**

- [ ] No credentials, account identifiers, or connection details in any file — including `references/`, `assets/`, and test data.
- [ ] No customer names, and no scenario specific enough to identify one.
- [ ] Every link resolves for someone outside Snowflake.
- [ ] Nothing here describes functionality that isn't generally available. If it does, say so below.

**Scope**

- [ ] This doesn't substantially overlap an existing or bundled skill. If it's adjacent to one, the `description` says when *not* to use it.

---

## Snowflake employees

- [ ] Reviewed for internal content before opening this PR.

**Cleared by:** <!-- a person, not a team -->

---

## Anything a reviewer should know

<!-- Known limitations, why a box is unchecked, a decision you'd like a second opinion on. -->

## Skill submission checklist

Before submitting, please confirm the following:

### Folder structure
- [ ] My skill lives at `skills/<my-skill-id>/`
- [ ] If I supplied `id`, it matches the folder name and the reviewer naming rule
- [ ] `SKILL.md` is present in the skill folder
- [ ] `LICENSE` is present in the skill folder

### Frontmatter
Follow the [versioned reviewer contract](../CONTRIBUTING.md#frontmatter-contract).
Recommended fields and legacy statuses are advisory, not additional requirements.

- [ ] `name` field is filled in
- [ ] `description` field clearly explains what the skill does, when to use it, and what triggers it
- [ ] Optional `id`, if supplied, matches the folder name
- [ ] `author` or `authors` identifies contributors; conflicting aliases are reconciled
- [ ] Optional `type` is `community` or `snowflake`; `partner` remains unsupported
- [ ] Recommended `status` (`stable`, `beta`, `draft`) and `categories` have been considered; legacy status values warn only

### License
- [ ] I reviewed the [license content and human-approval rules](../CONTRIBUTING.md#license-content-and-human-approval); template matching does not prove identity, attribution or legal approval
- [ ] Community Apache 2.0 / employee Snowflake Skills License applicability has been considered with maintainers

### Testing
- [ ] I tested this skill in a Cortex Code session against my example prompt
- [ ] The skill behavior matches what is described in the `description` field

### Optional
- [ ] Supporting files (templates, examples) are organized into `templates/` or `references/` subdirectories
- [ ] I checked that my skill name does not conflict with a bundled Cortex Code skill (run `/skill` in a session to verify)

---

**Describe your skill:**

<!-- What does it do? What problem does it solve? Who is it for? -->

**Example prompt that triggers it:**

<!-- e.g. "good morning" or "run my daily pipeline check" -->

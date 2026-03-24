## Skill submission checklist

Before submitting, please confirm the following:

### Folder structure
- [ ] My skill lives at `skills/<my-skill-id>/`
- [ ] The folder name matches the `id` field in `SKILL.md` (lowercase, hyphens only)
- [ ] `SKILL.md` is present in the skill folder
- [ ] `LICENSE` is present in the skill folder

### Frontmatter
- [ ] `name` field is filled in
- [ ] `description` field clearly explains what the skill does, when to use it, and what triggers it
- [ ] `id` field matches the folder name
- [ ] `authors` field includes the contributor's name
- [ ] `type` is set to `community` or `snowflake`
- [ ] `status` is set to `stable`, `beta`, or `draft`
- [ ] `categories` includes at least one relevant tag

### License
- [ ] Community contributors: LICENSE is Apache 2.0
- [ ] Snowflake employees: LICENSE is the Snowflake Skills License

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

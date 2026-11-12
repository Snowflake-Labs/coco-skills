---
name: whats-new
id: whats-new
title: Summarize Release Notes
summary: Digest of what's new in Snowflake (features, preview/GA, behavior changes, drivers) for a time window, from public docs.
description: "Summarize what's new in Snowflake over a time window using the public release notes on docs.snowflake.com: new features, preview/GA transitions, behavior changes (BCR bundles + unbundled), deprecations, and client/driver releases. Use when the user asks what's new in Snowflake, what shipped/launched/released in a date range, recent release notes, behavior changes, BCRs, or a weekly/monthly Snowflake changelog digest. Triggers: what's new in Snowflake, release notes summary, what shipped last week, Snowflake changelog, new features since, behavior changes, BCR bundle, what went GA, preview features this month, release digest, monthly release summary, features released in <month>, release notes email."
language: en
status: Published
authors: Umesh Unnikrishnan
type: snowflake
prompt: "$whats-new in Snowflake in the last 2 weeks"
tools:
  - web_fetch
  - ask_user_question
  - Write
---

# Release notes summary

Build a categorized, link-cited digest of Snowflake changes within a time window, sourced only from the public docs at `https://docs.snowflake.com`.

## Sources (public docs)

Every docs page has a Markdown twin: append `.md` to the URL (for example `https://docs.snowflake.com/en/release-notes/new-features.md`). Prefer `.md` when fetching; it's cleaner and smaller. Fall back to the HTML URL if `.md` fails.

| Source | URL (under `https://docs.snowflake.com/en/`) | Contains |
|---|---|---|
| Index (recent) | `release-notes/new-features` | Upcoming + recent server releases (10.x), and dated "Recent feature updates" list (~last 4-5 weeks) |
| Earlier this year: features | `release-notes/feature-releases-<YYYY>` | Older standalone feature updates |
| Earlier this year: server releases | `release-notes/weekly-releases-<YYYY>` | Older 10.x release pages |
| Filterable list | `release-notes/all-release-notes` | All announcements by date/type (use if index lists aren't enough) |
| Server release page | `release-notes/<YYYY>/<MAJOR>_<MINOR>` (for example `2026/10_40`) | Grouped items + release date range |
| Standalone feature note | `release-notes/<YYYY>/other/<YYYY-MM-DD>-<slug>` | One feature; date is in the slug |
| Behavior changes | `release-notes/behavior-changes` | BCR bundle table: status history per bundle (Disabled → Enabled by default → Generally enabled) |
| Bundle detail | `release-notes/bcr-bundles/<YYYY_NN>_bundle` | Individual behavior changes by area |
| Unbundled BCRs | `release-notes/bcr-bundles/un-bundled/unbundled-behavior-changes` | Behavior changes outside bundles |
| Clients/drivers | `release-notes/clients-drivers/<driver>-<YYYY>` | Per-driver version notes |

## Workflow

### Step 1: Resolve inputs

Parse from the request; ask (ask_user_question) only for what's missing and materially changes output:

- **Time window** (required). Convert relative phrases ("last week", "since Oct 1", "this month", "last 30 days") to absolute `START`–`END` dates using today's date. A bare month ("October 2026", "last month") means that full calendar month (Oct 1–Oct 31). Default: last 7 days.
- **Categories** (default: all): new features, preview/GA transitions, behavior changes, deprecations, clients/drivers.
- **Focus area** (optional filter): for example Cortex/AI, Iceberg, security/governance, CoCo, Native Apps.
- **Layout** (default: by status). Optional: `by area` (detailed document grouped by product area; see Step 6b).
- **Output** (default: chat). Optional: markdown file, and/or an email-ready version (Step 7).

**Do not over-ask.** "What's new in Snowflake last week" → go straight to Step 2 with defaults.

### Step 2: Fetch the indexes (parallel)

Fetch in one batch with `web_fetch`:
1. `release-notes/new-features.md`
2. `release-notes/behavior-changes.md`
3. If `START` is older than the oldest date on the new-features page: `release-notes/feature-releases-<YYYY>.md` and `release-notes/weekly-releases-<YYYY>.md` (repeat per year if the window spans years).

### Step 3: Select in-window items

- **Standalone feature notes**: date comes from the link text ("Nov 10, 2026: ...") or the URL slug (`other/2026-11-10-...`). Keep if `START <= date <= END`.
- **Server releases**: keep a 10.x release if its date range overlaps the window. Mark releases listed under "Upcoming (or in progress)" as **in progress / preview of notes** — contents can change.
- **Client/driver entries** on the index have no date in the link text. Include them only if they appear between two in-window dated entries on the index; otherwise open the driver page and check the version date. Never guess a date.
- **Behavior changes**: include a bundle if any status-change event (introduced / enabled by default / generally enabled) has a release or date inside the window, plus always report the **currently pending** bundle as "upcoming". Also check the unbundled page for in-window entries.

### Step 4: Enrich (only what's needed)

- The index link titles usually suffice for feature notes. Fetch an individual page only when the title is ambiguous, the user asked for detail, or it's a headline item (GA of a major feature, deprecation, behavior change). Batch fetches in parallel; cap ~15 page fetches unless the user wants depth.
- For in-window server releases, fetch the release page to list its sub-items.
- For behavior-change bundles in scope, fetch the bundle page and list each change (title + area + one-line impact).

### Step 5: Classify

Assign each item one primary category:

| Category | Signal |
|---|---|
| Generally available | "(General availability)", "GA" in title |
| Preview | "(Preview)", "(Public Preview)" |
| Behavior changes | BCR bundle / unbundled page |
| Deprecations & removals | "deprecated", "no longer available", "end of support", "removed" |
| Clients, drivers, connectors | `clients-drivers/` or `connectors/` URLs |
| Other updates | Everything else (enhancements without a stage label) |

Then tag a product area (AI/Cortex, CoCo/CoWork, Data engineering, Iceberg/Lakehouse, Security & governance, Collaboration/Sharing, Native Apps, Postgres/Hybrid, ML, Platform/SQL). Apply the focus-area filter here if given.

### Step 6: Output

```markdown
# What's new in Snowflake: <START> – <END>

**At a glance:** <N> GA · <N> preview · <N> behavior changes · <N> deprecations · <N> client releases
**Highlights:** 3–5 bullets with the most impactful items (GA of major features, breaking/behavior changes, deprecations).

## Generally available
- **<Date>** · <Area> · [<Title>](<url>) — <one-line what it does, only if fetched or obvious from title>

## Preview
...
## Behavior changes
- **Bundle <YYYY_NN>** — <status in window> ([details](<url>)); notable changes: ...
- Upcoming: **<pending bundle>** (Disabled by default; test with `SYSTEM$ENABLE_BEHAVIOR_CHANGE_BUNDLE`)
## Deprecations & removals
## Other updates
## Clients, drivers, connectors  (collapse to one line per driver: latest version in window)
## Server releases in window
- [10.xx (<date range>)](<url>): <sub-items>

## Additional resources
- [Release notes and feature updates](https://docs.snowflake.com/en/release-notes/new-features)
- [Behavior change announcements](https://docs.snowflake.com/en/release-notes/behavior-changes)
- [Preview features](https://docs.snowflake.com/en/release-notes/preview-features)

_Source: docs.snowflake.com release notes, fetched <today>._
```

#### Step 6b: By-area layout (when requested, or for file output of a month or longer)

Group by product area instead of status. Per item:

```markdown
## <Area>
### <Feature name> (<GA | Preview> · <Date>)
<1–2 sentence description, from the fetched page only>
- **Documentation:** [<title>](<url>)
```

Keep Behavior changes, Clients/drivers, and Server releases as their own trailing sections. Enrich more items than Step 4's default cap when using this layout, since each item needs a description.

#### File output

When the user wants a file, write `<OUTPUT_DIR>/Snowflake_<PERIOD>_Whats_New.md` (`<PERIOD>` = `October_2026` for a month, else `<START>_to_<END>`). Confirm the directory (default: current working directory) before writing.

Rules:
- Every item links to its docs page. No item without a source.
- Sort each section newest first. Omit empty sections.
- Don't invent descriptions; if you didn't fetch the page, use the title only.
- Collapse repetitive items (multiple CoCo Desktop versions, weekly DCR updates) into one line with the date range.
- If the window is large (>~80 items), show highlights + counts per area, and offer the full list as a file.
- Link docs pages with the `/en/` path and without `.md` (the `.md` form is for fetching only).

### Step 6c: Verify links

Links copied from the index pages are already valid. Verify (via parallel `web_fetch`) any link you **constructed or modified** (for example, anchors, rewritten paths, or resources links), plus any page that failed during enrichment. For a broken link, find the correct page with `web_search` restricted to `docs.snowflake.com`, confirm it loads, and replace it. If it still can't be verified, keep the item without a link and say so. Report the result in one line (`<N> links checked, <N> fixed`).

### Step 7: Email-ready summary (optional)

If the user asks for an email (or "something I can send to my team"), produce a paste-ready version after the digest:

```
Subject: What's new in Snowflake: <PERIOD>

Hi team,

Here are the key Snowflake updates from <START>–<END>:

**<Area>**
- <Feature> (<GA | Preview>): <one-line description>

**Behavior changes to review**
- <change> (<date>)

Full details: https://docs.snowflake.com/en/release-notes/new-features
```

Keep it to the highlights plus at most ~5 bullets per area; skip driver releases unless asked.

## Stopping points

- After Step 1 only if the time window is genuinely absent and can't be inferred.
- Before writing a file (confirm path) if the user asked for a file output.
- After presenting a file or by-area document: ask if anything is missing before producing the email version.

## Gotchas

- "Preview" with no qualifier means public preview.
- The new-features index only covers recent weeks; older windows need the `-<YYYY>` archive pages.
- Release dates on upcoming server releases are tentative; label them.
- Private preview features aren't in public release notes; don't claim completeness for PrPr.

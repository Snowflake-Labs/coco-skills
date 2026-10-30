# Discover Business Context (MCP sources)

Extract business context — definitions, fiscal calendar, org units, owners, data caveats, reference numbers — from the sources the analyst connected and consented to. Output goes through `engine/scripts/context_store.py`, which enforces consent, redaction and citations. Nothing extracted here is treated as true until the analyst confirms it in the wizard.

## Inputs
- `<workspace>/context.json` — `sources[]` holds the consented connectors and scopes (written by the wizard's Connect step via `context_store.py consent`).
- `catalog/connectors.json` — per-connector `search_queries`, `forbid_scopes`, and `term_vocabulary`.
- `<workspace>/data_index.json` — used only for the org-unit allowlist during redaction.

## Hard rules
- **Only consented scopes.** Read only the spaces, projects, channels, folders or labels listed in `context.json.sources`. Never widen a search to the whole workspace, and never read Slack/Teams DMs, private channels or chats, or whole email inboxes.
- **Definitions, not people.** Never copy names, emails, employee IDs, performance or compensation details, or case details about individuals into an item. Write the definition in neutral words. `context_store.py` re-redacts as a second line of defence; do not rely on it.
- **Cite everything.** Every item needs `source.ref` (page URL/ID, issue key, channel + message ts, or file path) and `source.title`.
- **Bounded reads.** Per connector, run its `search_queries` scoped to the consented scopes, and open at most 10 results per query and 40 documents per connector.
- **Read-only.** Never create, edit, or comment on anything in a connected tool.

## Steps
1. Read `context.json.sources`. For each consented connector, find the matching MCP tools among your available tools (for example `mcp_atlassian_search` / `getConfluencePage` for Confluence, `searchJiraIssuesUsingJql` for Jira, or a Slack, Drive, SharePoint or email MCP if configured). If a consented connector has no available tool, record `{"connector": ..., "status": "not_connected"}` in your final summary and continue.
2. Search within scope using the connector's `search_queries`. Examples: Jira as `project in (<scopes>) AND text ~ "<query>"`; Confluence CQL as `space in (<scopes>) AND text ~ "<query>"`.
3. From each relevant document or message, extract items in this exact schema (`kind` must be one of `definition, fiscal_calendar, org_units, reference_number, owner, caveat, threshold`):
   ```json
   {"kind": "definition", "term": "regular_headcount",
    "value": "Active Full-Time and Part-Time employees; contractors excluded",
    "text": "<short neutral snippet supporting the item>",
    "confidence": "HIGH|MEDIUM|LOW",
    "source": {"connector": "confluence", "scope": "<consented scope>",
               "ref": "<page id / issue key / channel:ts>", "title": "<doc title>",
               "updated": "<last-updated date if known>"}}
   ```
   - `term` must be a key from `term_vocabulary` (for example `regular_headcount`, `regrettable_exit`, `fiscal_calendar`, `org_hierarchy`, `span_of_control`).
   - `fiscal_calendar` value: `{"fiscal_year_start_month": 1-12}`.
   - `org_units` value: a list of org unit names (departments, divisions, business units).
   - `reference_number` value: `{"metric": "active_headcount", "value": 323, "as_of": "2026-08-31"}`, or a rate with `"unit": "pct"`.
   - `threshold` value: `{"tunable": "<tunable id from catalog>", "value": <number>}`.
   - Use HIGH confidence for official glossary/handbook pages, MEDIUM for tickets, LOW for chat.
4. Write all items to `<workspace>/context_raw/<connector>.json` as `{"items": [...]}`.
5. Ingest each file:
   ```bash
   python3 engine/scripts/context_store.py ingest --context <workspace>/context.json \
     --items <workspace>/context_raw/<connector>.json --org-allowlist <workspace>/data_index.json
   ```
6. Exported docs (markdown/txt/JSON exports in a folder the analyst picked) go through the deterministic extractor instead:
   ```bash
   python3 engine/scripts/extract_context.py --path <folder> --connector local_files --scope <folder> --out <workspace>/context_raw/local_files.json
   ```
   Then ingest the output the same way as step 5.

## Output
A one-line-per-connector summary: items accepted, items rejected (no consent / no citation), conflicts found, and connectors that were consented but not connected. Do not print item contents beyond terms and counts.

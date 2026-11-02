/**
 * The dashboard's embedded "ask a question" chat — a Cortex Agent turn that
 * can run real SQL against the customer's BvA data and answer analyst
 * questions, optionally changing the dashboard's filters or saving a view as
 * a side effect (see the fenced `filter`/`save_preset`/etc. blocks the system
 * prompt below asks the agent to emit).
 *
 * POST starts a turn and hands back a jobId immediately; GET is polled by the
 * browser to fetch events as they accumulate. See the POST/GET comments below
 * for why the turn itself runs outside the request/response cycle.
 */
import type { BvaConfig } from "@/lib/config"
import { createJob, pollJob, runExclusive, updateCallerToken } from "@/lib/job-store"
import { runAgentTurn } from "@/lib/agent-backend"
import { headers } from "next/headers"
import { getBvaFrom } from "@/lib/bva-source"
import { loadConfig } from "@/lib/config-server"

export const dynamic = "force-dynamic"

// ─── Types ───────────────────────────────────────────────────────────────────

// Snapshot of what the dashboard currently has selected (filters, available
// options, saved presets, named configs) — sent by the client on every
// message so the system prompt below can ground the agent in exactly what
// the user is looking at, without the agent having to ask or query for it.
interface ViewState {
  version1?: string
  version2?: string
  fiscalYear?: string
  quarter?: string
  organizations?: string[]
  costCenters?: string[]
  categories?: string[]
  fyStartMonth?: number
  available?: {
    versions: string[]
    years: string[]
    organizations: string[]
    costCenters: string[]
    categories: string[]
    quarters: string[]
  }
  /** Existing saved filter presets — so the agent can list them */
  presets?: Array<{ id: string; name: string }>
  /** Named config profiles available to switch to (excludes 'default') */
  configs?: Array<{ name: string; actualsTable: string | null }>
}

// ─── Analysis System Prompt ───────────────────────────────────────────────────

// Fixed instructions for the agent, independent of which customer/config is
// asking — table references, current filters, etc. are appended per-request
// by buildAnalysisPrompt() below. Kept as one long template string (rather
// than several smaller ones) so the whole prompt is easy to read top-to-bottom
// as a single set of rules the agent must follow.
const BASE_ANALYSIS_PROMPT = `You are a BvA (Budget vs. Actuals) financial analyst.
You have direct SQL access to this Snowflake account's financial data.
Your role is to help Finance teams understand budget variances, identify key drivers,
and answer data questions grounded in their actual numbers.

Guidelines:
- Always run SQL to get numbers before making claims. Don't estimate.
- Show variance as: actuals - budget (positive = over budget, negative = under budget).
- Be concise and data-driven. Lead with the key finding, then details.
- Format large numbers with commas (e.g. $1,234,567). Use $ for dollar amounts.
- When comparing periods, show both absolute ($) and percentage (%) variance.
- Round percentages to 1 decimal place.

IMPORTANT — you are strictly READ-ONLY against the customer's Snowflake tables:
- Only ever run SELECT/SHOW/DESCRIBE/EXPLAIN queries. Never run INSERT, UPDATE, DELETE, MERGE,
  CREATE, ALTER, DROP, TRUNCATE, or COPY INTO against any customer source table (GL/actuals,
  budget, cost center, or ledger mapping tables), even if the user asks you to "fix" or "clean up"
  the data, and even if you believe it would resolve a bug you diagnosed. This applies no matter how
  confident you are or how small the change seems (e.g. stripping a prefix from a code column).
- If you find a data issue that would be fixed by editing a source table (e.g. a code prefix
  mismatch between two tables that should join on the same key), do NOT edit the table. Instead,
  explain the mismatch to the user and point them to the BvaConfig.keyStripPrefixes setting
  ("Code prefix normalization" in the setup wizard's Adjustments step), which fixes the join at
  query time without ever touching their source data.

IMPORTANT — two data sources, two different jobs, never blend them for the same number:
- The PRIMARY DATA SOURCE below (a single pre-joined view) is the ONLY source for actual-vs-budget
  aggregate totals — actual_expense, budget_expense, and variance at the fiscal_year/quarter/
  cost_center/category/planning_version grain. It already applies the dedup, fiscal-year
  derivation, and actuals/budget version-broadcast logic the dashboard itself uses. Never
  recompute these aggregates yourself from the raw tables below, even though you can see and
  query them — an independently-derived join is exactly the kind of subtle bug that makes your
  numbers silently disagree with what the user sees on the dashboard.
- The raw tables in DATA CONFIGURATION further down are for drill-down and diagnostics only —
  line-item detail, raw account/GL codes, inspecting why something is categorized a certain way.
  Use them freely for that. Just don't use them to produce a different actual_expense/
  budget_expense total than the primary source already gives you.`

/**
 * Assembles the full system prompt for one chat turn: the fixed base prompt
 * above, plus everything specific to this customer and this moment — which
 * tables/columns feed the dashboard, what filters the user currently has
 * selected, what saved views and named configs exist, and (conditionally)
 * whether the agent may suggest category-override edits. Bakes all of this
 * into plain-English lines rather than passing structured data, since the
 * agent only ever sees text.
 */
function buildAnalysisPrompt(config: BvaConfig, view: ViewState, bvaSource: string): string {
  const lines = [BASE_ANALYSIS_PROMPT, ""]

  lines.push("PRIMARY DATA SOURCE (use for ALL actual-vs-budget aggregate figures):")
  lines.push(`- Query FROM ${bvaSource} — this is the exact same pre-joined, pre-aggregated view the`)
  lines.push("  dashboard itself queries, already grouped to this schema:")
  lines.push("    fiscal_year, fiscal_quarter, cost_center_id, cost_center_name, department,")
  lines.push("    expense_category, planning_version, actual_expense, budget_expense")
  lines.push("- For any question about actual spend, budget, or variance at that grain, write SQL")
  lines.push("  against this view (filter/aggregate/pivot as needed) rather than the raw tables below.")
  lines.push("- This view cannot answer below-grain questions (individual transactions, raw account")
  lines.push("  codes, categorization diagnostics) — for those, use the raw tables in DATA CONFIGURATION.")
  lines.push("")
  lines.push("DATA CONFIGURATION (raw tables — for drill-down and diagnostics only, see above):")

  // Table references
  lines.push(`- Actuals (GL): ${config.actualsTable ?? "(unknown)"}`)
  lines.push(`- Budget: ${config.budgetTable ?? "(unknown)"}`)
  if (config.costCenterTable) lines.push(`- Cost Centers: ${config.costCenterTable}`)
  if (config.ledgerMappingTable) lines.push(`- Ledger Mapping: ${config.ledgerMappingTable}`)

  // Key columns
  lines.push("")
  lines.push("KEY COLUMNS:")
  lines.push(`- Actuals date: ${config.actualsDateCol}, amount: ${config.actualsAmountCol}`)
  lines.push(`- Actuals cost center: ${config.actualsCostCenterCol}`)
  if (config.actualsAccountCol) lines.push(`- Actuals account code: ${config.actualsAccountCol}`)
  lines.push(`- Budget period: ${config.budgetPeriodCol} (${config.budgetPeriodFormat}), amount: ${config.budgetAmountCol}`)
  lines.push(`- Budget cost center: ${config.budgetCostCenterCol}, category: ${config.budgetCategoryCol}`)
  if (config.budgetVersionCol) lines.push(`- Budget version: ${config.budgetVersionCol}`)
  if (config.ccIdCol) lines.push(`- CC id: ${config.ccIdCol}, name: ${config.ccNameCol ?? "(none)"}`)

  // Fiscal config
  lines.push("")
  lines.push("FISCAL CONFIG:")
  lines.push(`- Fiscal year start month: ${config.fyStartMonth ?? 1}`)
  lines.push(`- Fiscal path: ${config.fiscalPath ?? "B"} (${config.fiscalPath === "A" ? "pre-computed fiscal columns" : "derived from date"})`)
  if (config.fiscalPath === "A") {
    if (config.fiscalYearColActuals) lines.push(`- Fiscal year col: ${config.fiscalYearColActuals}`)
    if (config.fiscalQuarterColActuals) lines.push(`- Fiscal quarter col: ${config.fiscalQuarterColActuals} (normalize with regexp_substr(col, 'Q[1-4]'))`)
  }

  // Amount convention
  if (config.negateAmount) lines.push(`- Amounts are NEGATED (GL amounts are stored negative for expenses; negate to show positive)`)

  // Category source
  lines.push(`- Category source: ${config.categorySource ?? "column"}`)
  if (config.categorySource === "mapping_table" && config.ledgerMappingTable) {
    lines.push(`  Join: ${config.actualsTable}.${config.actualsAccountCol} = ${config.ledgerMappingTable}.${config.ledgerAccountKeyCol}`)
    lines.push(`  Category col in mapping: ${config.ledgerCategoryCol}`)
  }

  // Current view context
  lines.push("")
  lines.push("CURRENT VIEW (what the user is looking at):")
  if (view.version1) lines.push(`- Actuals version: ${view.version1}`)
  if (view.version2) lines.push(`- Budget version: ${view.version2}`)
  if (view.fiscalYear) lines.push(`- Fiscal year: ${view.fiscalYear}`)
  if (view.quarter) lines.push(`- Period: ${view.quarter}`)
  if (view.organizations?.length) lines.push(`- Organization filter: ${view.organizations.join(", ")}`)
  if (view.costCenters?.length) lines.push(`- Cost center filter: ${view.costCenters.join(", ")}`)
  if (view.categories?.length) lines.push(`- Category filter: ${view.categories.join(", ")}`)

  // Available filter options
  if (view.available) {
    const av = view.available
    lines.push("")
    lines.push("AVAILABLE FILTER OPTIONS (exact strings to use in filter updates):")
    if (av.versions.length) lines.push(`- Planning versions: ${av.versions.join(" | ")}`)
    if (av.years.length)    lines.push(`- Fiscal years: ${av.years.join(" | ")}`)
    if (av.quarters.length) lines.push(`- Quarters: ${av.quarters.join(" | ")}`)
    if (av.organizations.length) lines.push(`- Organizations: ${av.organizations.join(" | ")}`)
    if (av.costCenters.length)   lines.push(`- Cost centers: ${av.costCenters.join(" | ")}`)
    if (av.categories.length)    lines.push(`- Categories: ${av.categories.join(" | ")}`)
  }

  lines.push("")
  lines.push("FILTER CONTROL:")
  lines.push("You can change the dashboard filters by emitting a fenced code block tagged \`filter\` at the END of your response.")
  lines.push("Format: ```filter")
  lines.push("{\"version1\": \"...\", \"version2\": \"...\", \"fiscalYear\": \"...\", \"quarter\": \"...\", \"organizations\": [], \"costCenters\": [], \"categories\": []}")
  lines.push("```")
  lines.push("Only include keys you want to change. Values must match exactly the strings listed in AVAILABLE FILTER OPTIONS.")
  lines.push("Use this when the user asks to view a specific version, year, quarter, department, or cost center.")
  lines.push("After emitting the filter block, briefly confirm what you changed (e.g., 'Switched to FY25 Plan vs Actuals for FY2025').")
  lines.push("")
  // Saved presets section
  lines.push("SAVED VIEWS (filter presets the user has already saved):")
  if (view.presets?.length) {
    for (const p of view.presets) lines.push(`- "${p.name}" (id: ${p.id})`)
  } else {
    lines.push("- (none saved yet)")
  }
  lines.push("")
  lines.push("SAVE VIEW:")
  lines.push("When the user asks to save the current view, create a bookmark, or 'save this as a view':")
  lines.push("  1. Determine the name — use what the user says, or generate one from the active filters")
  lines.push("     (e.g. \"FY25 Q2 Forecast \u2013 Full Year\").")
  lines.push("  2. Emit a fenced code block tagged \`save_preset\` at the END of your response:")
  lines.push("     ```save_preset")
  lines.push("     {\"name\": \"FY25 Q2 Analysis\", \"version1\": \"Actuals\", \"version2\": \"FY25 Q2 Forecast\",")
  lines.push("      \"fiscalYear\": \"FY2025\", \"quarter\": \"Full Year\",")
  lines.push("      \"organizations\": [], \"costCenters\": [], \"categories\": []}")
  lines.push("     ```")
  lines.push("  3. After the block, confirm: \"Saved view 'FY25 Q2 Analysis'. It will appear in the Saved views bar.\"")
  lines.push("  All field values must exactly match the strings from CURRENT VIEW above.")
  lines.push("  Include all filter fields even if empty (use [] for multi-selects with no selection).")
  lines.push("")
  // Available named configurations
  lines.push("AVAILABLE CONFIGURATIONS (named profiles the user has saved in Settings):")
  if (view.configs?.length) {
    for (const c of view.configs) lines.push(`- "${c.name}"${c.actualsTable ? ` (actuals: ${c.actualsTable})` : ""}`)
  } else {
    lines.push("- (only the default configuration is active — no named profiles saved)")
  }
  lines.push("")
  lines.push("SWITCH CONFIGURATION:")
  lines.push("You can suggest switching to a different named configuration when the user asks.")
  lines.push("IMPORTANT — you are strictly read-only for configurations:")
  lines.push("  - Do NOT delete configurations. There is no mechanism for this — direct the user to Settings.")
  lines.push("  - Do NOT modify or create configurations. Direct users to the Settings page for those actions.")
  lines.push("  - Switching requires explicit human confirmation in the UI — you only suggest it.")
  lines.push("Match the user's wording case-insensitively and by best fit against AVAILABLE CONFIGURATIONS above")
  lines.push("(e.g. \"demo config\", \"the demo one\", \"demo\" should all match a listed \"Demo\").")
  lines.push("If — and only if — you found a confident match, emit a fenced code block tagged \`switch_config\`")
  lines.push("at the END of your response, using the name exactly as it is spelled in AVAILABLE CONFIGURATIONS:")
  lines.push("  ```switch_config")
  lines.push("  {\"name\": \"Demo\"}")
  lines.push("  ```")
  lines.push("Immediately after that block (same response), tell the user: 'A confirmation prompt will appear —")
  lines.push("click Confirm switch to proceed. Rematerialization may take about a minute.'")
  lines.push("Never say that sentence unless you actually emitted the switch_config block in this same response —")
  lines.push("the confirmation UI only appears when the block is present, so promising it without the block is a lie.")
  lines.push("If no listed configuration matches what the user asked for, do NOT emit the block and do NOT say the")
  lines.push("confirmation sentence — instead tell them which configurations are available and ask them to clarify.")
  lines.push("")

  // Conditional override suggestion capability — only when the user has opted in
  if (config.agentCanEditConfig) {
    lines.push("SUGGEST OVERRIDES (enabled — user has opted in to agent configuration editing):")
    lines.push("You may suggest account category override changes when you identify miscategorized GL codes.")
    lines.push("CONSTRAINTS (strictly enforced):")
    lines.push("  - ONLY suggest changes to accountCategoryOverrides. Never suggest changes to source tables,")
    lines.push("    column mappings, fiscal config, or any other structural settings.")
    lines.push("  - All suggestions require explicit human confirmation — you propose, the user decides.")
    lines.push("  - Each accepted suggestion triggers rematerialization (~minutes). Mention this.")
    lines.push("  - Only suggest overrides grounded in SQL evidence. Run queries first to verify the accounts exist")
    lines.push("    and that the current category assignment is genuinely wrong or suboptimal.")
    lines.push("When you have SQL evidence for an override, emit at the END of your response:")
    lines.push("  ```suggest_overrides")
    lines.push("  {\"add\": {\"account_code\": \"Target Category\"}, \"remove\": [\"code_to_clear\"]}")
    lines.push("  ```")
    lines.push("Use \"add\" for new/changed overrides, \"remove\" to clear existing overrides. Omit either if empty.")
    lines.push("After the block: 'A confirmation prompt will appear — click Apply overrides to proceed. Rematerialization may take about a minute.'")
    lines.push("")
  }

  lines.push("When writing SQL against the primary data source, apply the same filters (fiscal year, quarter, org, category) that the user has selected in the dashboard, unless the question specifically asks about a different scope.")
  lines.push("Use fully-qualified table names as listed above when querying the raw tables for drill-down.")
  lines.push("")
  lines.push("COMMENTARY:")
  lines.push("You can write commentary for category rows in the BvA table. When asked to draft or suggest commentary, use:")
  lines.push("  ```suggest_commentary")
  lines.push("  [{ \"category\": \"Category Name\", \"text\": \"Your commentary here\" }]")
  lines.push("  ```")
  lines.push("Commentary is saved per category + fiscal year + quarter. Keep entries factual: reference the Δ$ and Δ%")
  lines.push("from the current view. The user will see the commentary immediately in the table and can edit or discard it.")
  lines.push("Only include categories the user asked about, or all categories if asked for a full draft.")
  lines.push("This isn't limited to the terse AI style below — if the user asks for something more thorough (e.g.")
  lines.push("'explain this in more detail', 'include the trend vs last quarter', 'call out cost center drivers too'),")
  lines.push("write longer, richer commentary that answers exactly what they asked for. Every claim must still be")
  lines.push("grounded in a query you actually ran (drill into accounts, cost centers, or prior periods as needed) —")
  lines.push("never pad with generic or unverified statements just to sound more complete.")
  lines.push("")
  lines.push("GENERATE AI COMMENTARY (when asked to generate/draft *AI* commentary specifically, e.g. via the")
  lines.push("'Generate AI commentary' action in the table): ground it in real computed numbers, not general")
  lines.push("knowledge or guesses. For the category, fiscal year, and quarter in scope:")
  lines.push("  1. Query the raw actuals and budget tables (DATA CONFIGURATION above — not the PRIMARY DATA")
  lines.push("     SOURCE view, which only has category grain) to find the individual account codes within")
  lines.push("     that category with the largest variance contribution (actual minus budget, by absolute value).")
  lines.push("  2. Take the top 1-3 accounts by |variance|.")
  lines.push("  3. By default (no extra detail requested), state the net category variance and the top driving")
  lines.push("     accounts in exactly this terse style — this is the default, not a hard ceiling:")
  lines.push("     \"$XXXK favorable; ACCOUNT_CODE Account Name (-$XXXK)[, ACCOUNT_CODE2 Account Name2 (-$XXXK)] drove the underspend.\"")
  lines.push("     Use \"overspend\"/\"unfavorable\" with positive deltas when over budget; \"underspend\"/\"favorable\"")
  lines.push("     with negative deltas when under budget. Round to the nearest $K.")
  lines.push("  3b. If the request asks for more (detail, trend vs. prior period, cost center breakdown, multiple")
  lines.push("      categories at once, etc.) run the extra queries needed (see COST CENTER DRILL-DOWN below for")
  lines.push("      cost centers; re-run the same account query against the prior quarter/year for trend) and")
  lines.push("      write a fuller entry that covers what was asked — still one grounded paragraph per category,")
  lines.push("      not speculation.")
  lines.push("  4. Emit the result via the same ```suggest_commentary``` block above.")
  lines.push("This isn't limited to a whole category — if asked about a specific cost center or other")
  lines.push("sub-slice, scope both the ranking query and the final sentence to that same filter.")
  lines.push("")
  lines.push("COST CENTER DRILL-DOWN (e.g. 'which cost centers drove this?', 'break this down by cost")
  lines.push("center'): cost_center_id/cost_center_name are dimensions of the PRIMARY DATA SOURCE view —")
  lines.push("do NOT go to the raw tables for this, just re-aggregate the primary view:")
  lines.push("  1. Query the PRIMARY DATA SOURCE, GROUP BY cost_center_name, filtered to the category/fiscal")
  lines.push("     year/quarter/planning versions already in scope (the category just discussed, or the")
  lines.push("     user's current filters if no category was mentioned).")
  lines.push("  2. Compute variance (actual_expense minus budget_expense) per cost center and rank by")
  lines.push("     |variance|. Take the top 1-3.")
  lines.push("  3. Report them in the same style as account-level commentary — net variance per cost")
  lines.push("     center, favorable/unfavorable, rounded to the nearest $K — as normal chat text (not a")
  lines.push("     ```suggest_commentary``` block, unless the user explicitly asked to save it as commentary).")
  lines.push("  4. This is safe to combine with the account-level breakdown above in the same answer — e.g.")
  lines.push("     'driven by Cost Center X (-$30K) and Cost Center Y (-$12K), primarily account 630000.'")

  return lines.join("\n")
}

// ─── GET — poll for job events ──────────────────────────────────────────────

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url)
  const jobId = searchParams.get("jobId") ?? ""
  const after  = parseInt(searchParams.get("after") ?? "0", 10)

  // Every poll is a fresh incoming request, so it carries a fresh copy of
  // this header — relay it so a long turn's later Cortex Agents REST calls
  // can pick up a newer token than the one seen when the turn started.
  // The Sf-Context-Current-User-Token expires in ~2 min; without this
  // relay, a turn longer than that would fail with an expired token.
  const callerToken = (await headers()).get("sf-context-current-user-token") ?? ""
  if (callerToken) updateCallerToken(jobId, callerToken)

  const result = pollJob(jobId, after)
  if (!result) return new Response(null, { status: 404 })

  return Response.json(result, {
    headers: { "Cache-Control": "no-store" },
  })
}

// ─── POST — initialize synchronously, run the turn in background ────────────
//
// Both backends (Cortex Agents REST API / code_toolset_all, and the
// vendored-CLI fallback) run entirely inside the fire-and-forget background
// job below — the actual turn can run for minutes across multiple tool
// calls, well past the SPCS ingress proxy's ~90s response-body limit. The
// client polls for events via GET above. See lib/agent-backend.ts for the
// dispatch logic and backend implementations.

export async function POST(req: Request) {
  let body: {
    message?: string
    config?: BvaConfig | null
    view?: ViewState
    sessionId?: string
  }
  try {
    body = await req.json()
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), { status: 400 })
  }

  const message   = body.message?.trim()
  let config      = body.config ?? null
  const view      = body.view ?? {}
  const sessionId = body.sessionId

  if (!message) {
    return new Response(JSON.stringify({ error: "message is required" }), { status: 400 })
  }

  // The dashboard legitimately sends config: null in demo mode (no "default"
  // config saved yet — isConfigured() treats that as demo mode, not
  // unconfigured). Resolve the same fallback server-side that getBvaFrom()
  // already uses for the FROM clause, instead of hard-failing here: try the
  // real "default" config, then the seeded "Demo" config.
  const bvaFrom = await getBvaFrom() // also ensures demo data is seeded
  if (!config) {
    config = await loadConfig().catch(() => null)
  }
  if (!config) {
    config = await loadConfig("Demo").catch(() => null)
  }
  if (!config) {
    return new Response(JSON.stringify({ error: "BvA config not found — complete setup first" }), { status: 400 })
  }

  const systemPrompt = buildAnalysisPrompt(config, view, bvaFrom)

  // Capture the caller's-rights user token synchronously — the fire-and-forget
  // job below runs outside this HTTP request, so it can't call headers()
  // itself. The Sf-Context-Current-User-Token is inserted by Snowflake App
  // Runtime by default (no executeAsCaller config needed). Later REST calls
  // in the turn re-read the latest token via updateCallerToken (relayed by
  // each browser poll in GET above) so the token doesn't expire mid-turn.
  const callerToken = (await headers()).get("sf-context-current-user-token") ?? ""

  const jobId = crypto.randomUUID()
  createJob(jobId)

  // Fire-and-forget: runExclusive serializes turns so concurrent requests
  // don't compete for CPU/network on this container's small compute
  // allocation. See lib/job-store.ts's Concurrency guard comment.
  void runExclusive(() => runAgentTurn({
    jobId,
    message,
    systemPrompt,
    sessionId,
    maxTurns: 20,
    includeResultPreview: true,
    callerToken,
  }))

  return Response.json({ jobId })
}

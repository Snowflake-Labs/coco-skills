/**
 * One-time, idempotent bootstrap that seeds a fully synthetic "Demo" BvA
 * config + its backing tables the first time this app is opened with no
 * config saved yet. This is what makes a brand-new deployment show a working
 * demo immediately, with zero manual setup — and it's the reason there's no
 * hardcoded fallback to any pre-existing table: the demo data lives in
 * lib/demo-seed-data.ts (bundled in the app's own source) and gets written
 * into the SAME schema the app's config table already lives in, which we
 * know the deploying role can CREATE TABLE in (it already did, for the
 * config/materialized/history tables).
 *
 * Safe to call on every "no config yet" request — the first check
 * (loadConfig("Demo") / loadConfig("default")) makes every call after the
 * first a fast no-op.
 *
 * Concurrency: a brand-new deploy's first page load fires several API routes
 * in parallel (filters, bva, breakdown, chat), and EVERY one of them calls
 * this function. loadConfig("Demo") above is only a fast-path check, not a
 * lock — two calls can both see "no config yet" at the same instant and both
 * fall through. To avoid every table getting fully duplicated (confirmed to
 * happen: a race produced exact 2x row counts, with duplicate TRANSACTION_ID
 * values, across all four demo tables), a tiny sentinel row is claimed FIRST
 * via MERGE, before any table is created or seeded. Snowflake serializes
 * concurrent DML against the same table, so only one caller's claim actually
 * lands; every other concurrent caller sees 0 rows affected and returns
 * immediately without touching the data tables.
 *
 * The claim has a 5-minute expiry (re-claimable, not a permanent gate) so a
 * caller that crashes or times out mid-seed doesn't strand the deployment
 * with a half-seeded, un-healable demo forever — the next request just
 * re-claims and finishes the job. This is deliberately a SEPARATE row from
 * the final "Demo" config write below: the config is only ever written once
 * seeding has fully succeeded, so a reader can't observe "config exists" for
 * a demo that isn't actually ready yet.
 */
import { querySnowflake } from "@/lib/snowflake"
import { WAREHOUSE } from "@/lib/bva"
import { getConfigTable, loadConfig, invalidateConfigCache } from "@/lib/config-server"
import type { BvaConfig } from "@/lib/config"
import {
  DEMO_COST_CENTERS,
  DEMO_LEDGER_MAPPING,
  DEMO_GL_ACTUALS,
  DEMO_BUDGET,
} from "@/lib/demo-seed-data"

function q(s: string): string {
  return "'" + s.replace(/'/g, "''") + "'"
}

/** Chunked multi-row INSERT — keeps individual statements a reasonable size. */
async function bulkInsert(table: string, columns: string[], rows: unknown[][], chunkSize = 500): Promise<void> {
  for (let i = 0; i < rows.length; i += chunkSize) {
    const chunk = rows.slice(i, i + chunkSize)
    const values = chunk
      .map((row) => `(${row.map((v) => (typeof v === "string" ? q(v) : v)).join(", ")})`)
      .join(",\n")
    // Demo data bulk insert: one multi-row INSERT per chunk of demo rows,
    // rather than either a single giant statement or one row at a time.
    await querySnowflake(
      `insert into ${table} (${columns.join(", ")}) values\n${values}`,
      { warehouse: WAREHOUSE },
    )
  }
}

async function rowCount(table: string): Promise<number> {
  try {
    // Row-count probe: how many rows a demo table already has, used by
    // seedIfIncomplete() below to decide whether it needs (re-)seeding.
    const rows = await querySnowflake(`select count(*) as n from ${table}`, { warehouse: WAREHOUSE })
    return Number(rows[0]?.N ?? 0)
  } catch (e) {
    // Only treat "the table doesn't exist yet" as 0 rows — that's the one
    // case seedIfIncomplete() below actually needs to distinguish from
    // "has data." Any OTHER error (network blip, warehouse-resume timeout,
    // statement timeout) must NOT be swallowed into "0 rows": seedIfIncomplete
    // would then see existing === 0, skip the truncate, and bulkInsert a full
    // second copy on top of whatever's already there — reproducing the exact
    // duplicate-row corruption this file's history warns about (see comment
    // below), just triggered by a transient error instead of concurrency.
    // Rethrowing lets ensureDemoDataSeeded's own outer try/catch handle it —
    // that already self-heals on the next call.
    const msg = e instanceof Error ? e.message : String(e)
    if (/does not exist|not authorized/i.test(msg)) return 0
    throw e
  }
}

/**
 * Insert the full dataset only if the table doesn't already have the exact
 * expected row count. Catches BOTH "never seeded" (0 rows) and "partially
 * seeded" (some but not all rows — e.g. a prior seed attempt that got cut
 * off mid-way through bulkInsert's chunked inserts, such as an SPCS request
 * timeout) — a plain "table has any rows" check treats a partial seed as
 * complete and never retries it, silently shipping incomplete demo data
 * forever: a partial seed (e.g. a chunked insert cut off by a timeout) would
 * otherwise be treated as complete by a plain "has rows" check and never
 * retried, silently shipping incomplete demo data.
 * A row-count mismatch (not just "table not empty") triggers a full
 * truncate + re-insert of the complete dataset — the seed data is fully
 * deterministic (see demo-seed-data.ts), so re-running it is always safe
 * and always produces the same result, not a fresh random dataset.
 */
async function seedIfIncomplete(table: string, columns: string[], rows: unknown[][]): Promise<void> {
  const existing = await rowCount(table)
  if (existing === rows.length) return
  if (existing > 0) {
    // Wipe out a partial/stale seed before re-inserting the full dataset —
    // cheaper and simpler than reconciling row-by-row, and safe because the
    // seed data is fully deterministic (see demo-seed-data.ts).
    await querySnowflake(`truncate table ${table}`, { warehouse: WAREHOUSE })
  }
  await bulkInsert(table, columns, rows)
}

/**
 * Two sample semantic views built directly on the demo tables — one over GL
 * actuals + cost centers (transactional grain, includes the allocation_status
 * signal), one over budget/forecast (planning grain). CREATE OR REPLACE is
 * idempotent, so this is safe to call every time the seed path runs; each
 * failure is caught and logged independently so one view's failure (e.g. a
 * privilege issue) doesn't block the other.
 */
async function ensureDemoSemanticViews(base: string, glTable: string, ccTable: string, budgetTable: string): Promise<void> {
  const glView = `${base}.BVA_DEMO_GL_SEMANTIC_VIEW`
  const budgetView = `${base}.BVA_DEMO_BUDGET_SEMANTIC_VIEW`

  try {
    // GL semantic view: transactional-grain demo semantic view joining GL
    // actuals to the cost-center dimension, so customers can try
    // "point to an existing semantic view" / auto-discovery against a real
    // object without building one themselves.
    await querySnowflake(
      `create or replace semantic view ${glView}
         tables (
           gl_actuals as ${glTable} primary key (transaction_id) comment = 'BvA demo GL actuals (journal lines)',
           cost_centers as ${ccTable} primary key (cost_center_number) comment = 'BvA demo cost center dimension'
         )
         relationships (
           gl_to_cost_center as gl_actuals (cost_center_number) references cost_centers
         )
         facts (
           gl_actuals.transaction_amount as translated_debit_minus_credit_amount comment = 'Signed GL transaction amount'
         )
         dimensions (
           gl_actuals.accounting_date as accounting_date comment = 'GL transaction date',
           gl_actuals.ledger_account_number as ledger_account_number comment = 'GL account code',
           gl_actuals.allocation_status as allocation_status with synonyms = ('allocated', 'unallocated', 'allocation')
             comment = 'Whether this expense is allocated to a department or held as unallocated overhead',
           cost_centers.department as bva_department_name with synonyms = ('dept', 'department name') comment = 'Department name',
           cost_centers.cost_center_name as cost_center_name comment = 'Cost center / team name'
         )
         metrics (
           gl_actuals.total_actual_expense as sum(gl_actuals.transaction_amount) comment = 'Total actual expense'
         )
         comment = 'Demo semantic view over BvA GL actuals — for testing semantic view ingestion in BvA Variance Setup.'`,
      { warehouse: WAREHOUSE },
    )
  } catch (e) {
    console.error("[demo-seed] failed to create BVA_DEMO_GL_SEMANTIC_VIEW (non-fatal)", e)
  }

  try {
    // Budget semantic view: planning-grain demo semantic view over the
    // budget/forecast table, the second half of the same auto-discovery demo
    // as the GL view above.
    await querySnowflake(
      `create or replace semantic view ${budgetView}
         tables (
           budget as ${budgetTable} comment = 'BvA demo budget/forecast rows'
         )
         dimensions (
           budget.period as period comment = 'Budget period (first of month)',
           budget.planning_version as planning_version with synonyms = ('version', 'scenario') comment = 'Planning version / scenario name',
           budget.cost_center_number as cost_center_number comment = 'Cost center number',
           budget.expense_type as adaptive_expense_type with synonyms = ('category', 'expense category') comment = 'Budget expense category'
         )
         metrics (
           budget.total_budget_value as sum(budget.value) comment = 'Total budgeted amount'
         )
         comment = 'Demo semantic view over BvA budget/forecast — for testing semantic view ingestion in BvA Variance Setup.'`,
      { warehouse: WAREHOUSE },
    )
  } catch (e) {
    console.error("[demo-seed] failed to create BVA_DEMO_BUDGET_SEMANTIC_VIEW (non-fatal)", e)
  }
}

export async function ensureDemoDataSeeded(): Promise<void> {
  // Never seed over a real config — this only fires on a genuinely fresh deploy.
  let configTable: string
  try {
    if (await loadConfig("Demo")) return
    if (await loadConfig("default")) return
    configTable = await getConfigTable()
  } catch {
    return // config table unreachable or location unset — don't attempt to seed
  }

  const parts = configTable.split(".")
  if (parts.length < 2) return
  const base = `${parts[0]}.${parts[1]}`
  const glTable     = `${base}.BVA_DEMO_GL_ACTUALS`
  const budgetTable = `${base}.BVA_DEMO_BUDGET`
  const ledgerTable = `${base}.BVA_DEMO_LEDGER_ACCOUNT_MAPPING`
  const ccTable     = `${base}.BVA_DEMO_COST_CENTER_MAPPING`
  const lockTable   = `${base}.BVA_DEMO_SEED_LOCK`

  try {
    // The mutex: claim (or re-claim a stale) lock row before touching any
    // data table. MERGE's system-generated "number of rows inserted"/
    // "number of rows updated" columns tell us whether OUR branch fired.
    // Bootstrap DDL: create the lock table itself if this is the very first
    // call ever made against this deployment — it must exist before the
    // claim MERGE below can run against it.
    await querySnowflake(
      `create table if not exists ${lockTable} (ID number, CLAIMED_AT timestamp_ntz)`,
      { warehouse: WAREHOUSE },
    )
    // Concurrency lock: two or more of the app's first-page-load API routes
    // (filters, bva, breakdown, chat) can all see "no config yet" at the same
    // instant and all try to seed simultaneously. This MERGE against a single
    // sentinel row (id=1) is how exactly one of those concurrent callers wins
    // the right to seed — Snowflake serializes concurrent DML on the same
    // table, so only one caller's INSERT/UPDATE branch actually lands; every
    // other caller sees 0 rows affected below and bails out without touching
    // any data table. The 5-minute staleness window (WHEN MATCHED AND
    // claimed_at < ...) lets a crashed/timed-out claim be safely re-claimed
    // by a later request instead of permanently blocking the seed.
    const claim = await querySnowflake(
      `merge into ${lockTable} as tgt
       using (select 1 as id) as src
       on tgt.id = src.id
       when matched and tgt.claimed_at < dateadd('minute', -5, current_timestamp()) then
         update set claimed_at = current_timestamp()
       when not matched then
         insert (id, claimed_at) values (src.id, current_timestamp())`,
      { warehouse: WAREHOUSE },
    )
    const inserted = Number(claim[0]?.["number of rows inserted"] ?? 0)
    const updated = Number(claim[0]?.["number of rows updated"] ?? 0)
    if (inserted + updated === 0) return // someone else holds a fresh claim

    // Demo table DDL: create the four demo source tables (GL actuals,
    // budget, ledger account mapping, cost center dimension) if this is the
    // very first seed on this deployment. IF NOT EXISTS makes this safe to
    // run on every seed attempt, including the "partial seed, re-run" path
    // handled by seedIfIncomplete() below.
    await querySnowflake(
      `create table if not exists ${glTable} (
         TRANSACTION_ID varchar, ACCOUNTING_DATE date, COST_CENTER_NUMBER number,
         LEDGER_ACCOUNT_NUMBER number, TRANSLATED_DEBIT_MINUS_CREDIT_AMOUNT number(18,2),
         ALLOCATION_STATUS varchar)`,
      { warehouse: WAREHOUSE },
    )
    await querySnowflake(
      `create table if not exists ${budgetTable} (
         PERIOD date, PLANNING_VERSION varchar, COST_CENTER_NUMBER number,
         LEDGER_ACCOUNT_NUMBER number, ADAPTIVE_EXPENSE_TYPE varchar, VALUE number(18,2))`,
      { warehouse: WAREHOUSE },
    )
    await querySnowflake(
      `create table if not exists ${ledgerTable} (CODE varchar, CONTROLLABLE_EXPENSE_TYPE varchar)`,
      { warehouse: WAREHOUSE },
    )
    await querySnowflake(
      `create table if not exists ${ccTable} (
         COST_CENTER_NUMBER number, BVA_DEPARTMENT_NAME varchar, COST_CENTER_NAME varchar)`,
      { warehouse: WAREHOUSE },
    )

    // Idempotency guard for a concurrent race on first load, AND for a prior
    // partial seed (see seedIfIncomplete's doc comment) — re-checks the exact
    // expected row count rather than just "table is non-empty".
    await seedIfIncomplete(glTable,
      ["TRANSACTION_ID", "ACCOUNTING_DATE", "COST_CENTER_NUMBER", "LEDGER_ACCOUNT_NUMBER", "TRANSLATED_DEBIT_MINUS_CREDIT_AMOUNT", "ALLOCATION_STATUS"],
      DEMO_GL_ACTUALS.map((r) => [r.TRANSACTION_ID, r.ACCOUNTING_DATE, r.COST_CENTER_NUMBER, r.LEDGER_ACCOUNT_NUMBER, r.TRANSLATED_DEBIT_MINUS_CREDIT_AMOUNT, r.ALLOCATION_STATUS]),
    )
    await seedIfIncomplete(budgetTable,
      ["PERIOD", "PLANNING_VERSION", "COST_CENTER_NUMBER", "LEDGER_ACCOUNT_NUMBER", "ADAPTIVE_EXPENSE_TYPE", "VALUE"],
      DEMO_BUDGET.map((r) => [r.PERIOD, r.PLANNING_VERSION, r.COST_CENTER_NUMBER, r.LEDGER_ACCOUNT_NUMBER, r.ADAPTIVE_EXPENSE_TYPE, r.VALUE]),
    )
    await seedIfIncomplete(ledgerTable,
      ["CODE", "CONTROLLABLE_EXPENSE_TYPE"],
      DEMO_LEDGER_MAPPING.map((r) => [r.CODE, r.CONTROLLABLE_EXPENSE_TYPE]),
    )
    await seedIfIncomplete(ccTable,
      ["COST_CENTER_NUMBER", "BVA_DEPARTMENT_NAME", "COST_CENTER_NAME"],
      DEMO_COST_CENTERS.map((r) => [r.COST_CENTER_NUMBER, r.BVA_DEPARTMENT_NAME, r.COST_CENTER_NAME]),
    )

    // Sample semantic views over the demo tables, so customers can try the
    // "point to an existing semantic view" / auto-discovery feature
    // (README.md §14) against real objects without setting anything up
    // themselves. Non-fatal: CREATE SEMANTIC VIEW needs a privilege
    // (CREATE SEMANTIC VIEW on the schema) the deploying role might not
    // have, and losing this is just a missed demo opportunity, not a
    // reason to fail the whole seed.
    await ensureDemoSemanticViews(base, glTable, ccTable, budgetTable)

    const demoConfig: BvaConfig = {
      configName: "Demo",
      sourceType: "multi_table",
      actualsTable: glTable,
      actualsDateCol: "ACCOUNTING_DATE",
      actualsAmountCol: "TRANSLATED_DEBIT_MINUS_CREDIT_AMOUNT",
      actualsCostCenterCol: "COST_CENTER_NUMBER",
      actualsAccountCol: "LEDGER_ACCOUNT_NUMBER",
      actualsAnchorVersion: "Board Plan",
      accountFilterPrefixes: ["5", "6", "7"],
      negateAmount: false,
      budgetTable,
      budgetPeriodCol: "PERIOD",
      budgetPeriodFormat: "date",
      budgetAmountCol: "VALUE",
      budgetCostCenterCol: "COST_CENTER_NUMBER",
      budgetCategoryCol: "ADAPTIVE_EXPENSE_TYPE",
      budgetVersionCol: "PLANNING_VERSION",
      categorySource: "mapping_table",
      ledgerMappingTable: ledgerTable,
      ledgerAccountKeyCol: "CODE",
      ledgerCategoryCol: "CONTROLLABLE_EXPENSE_TYPE",
      keyStripPrefixes: { ledgerAccountKeyCol: ["FIN_"] },
      costCenterTable: ccTable,
      ccIdCol: "COST_CENTER_NUMBER",
      ccNameCol: "COST_CENTER_NAME",
      ccDeptCol: "BVA_DEPARTMENT_NAME",
      fiscalPath: "B",
      fyStartMonth: 2,
      agentCanEditConfig: false,
      updatedAt: new Date().toISOString(),
    } as BvaConfig

    const jsonLiteral = JSON.stringify(demoConfig).replace(/'/g, "''")
    // Demo config upsert — deliberately INSERT-ONLY, not a true upsert like
    // lib/queries/config-crud.ts's upsertConfigRow(). There is no WHEN
    // MATCHED branch, so if a "Demo" row already exists this MERGE is a
    // no-op: it can never overwrite it. That's intentional, not a bug to
    // "fix" by adding an UPDATE branch — the Demo config is a one-time
    // bootstrap, and once it's written we must never clobber it, e.g. if a
    // customer has since hand-edited the Demo config or pointed 'default'
    // back at it. Contrast with the config-crud.ts MERGE, which backs actual
    // user-editable saves and always needs to overwrite on repeat writes.
    await querySnowflake(
      `merge into ${configTable} as tgt
       using (select 'Demo' as config_name, parse_json('${jsonLiteral}') as config_json) as src
       on tgt.config_name = src.config_name
       when not matched then
         insert (config_name, config_json, updated_at)
         values (src.config_name, src.config_json, current_timestamp())`,
      { warehouse: WAREHOUSE },
    )
    invalidateConfigCache("Demo")
  } catch (e) {
    console.error("[demo-seed] failed to seed demo data — dashboard will show an empty/error state until a config is set up", e)
  }
}

import { describe, it, expect, vi, beforeEach } from "vitest"

const querySnowflakeMock = vi.fn()
vi.mock("@/lib/snowflake", () => ({
  querySnowflake: (...args: unknown[]) => querySnowflakeMock(...args),
}))

const loadConfigMock = vi.fn()
const getConfigTableMock = vi.fn()
const invalidateConfigCacheMock = vi.fn()
vi.mock("@/lib/config-server", () => ({
  loadConfig: (...args: unknown[]) => loadConfigMock(...args),
  getConfigTable: (...args: unknown[]) => getConfigTableMock(...args),
  invalidateConfigCache: (...args: unknown[]) => invalidateConfigCacheMock(...args),
}))

import { ensureDemoDataSeeded } from "@/lib/demo-seed"
import { DEMO_GL_ACTUALS, DEMO_BUDGET, DEMO_LEDGER_MAPPING, DEMO_COST_CENTERS } from "@/lib/demo-seed-data"
import { validateBuildConfig, buildFromConfig, type BvaConfig } from "@/lib/config"

beforeEach(() => {
  querySnowflakeMock.mockReset()
  loadConfigMock.mockReset()
  getConfigTableMock.mockReset()
  invalidateConfigCacheMock.mockReset()
  getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.BVA_APP_CONFIG")
  // Default: the lock-claim MERGE always "wins" (1 row inserted), and every
  // other query returns no rows (table empty / doesn't exist yet). Tests
  // that need a specific row-count probe response override this per-call.
  querySnowflakeMock.mockImplementation(async (sql: string) => {
    if (/^merge into \S*BVA_DEMO_SEED_LOCK/i.test(sql)) return [{ "number of rows inserted": 1, "number of rows updated": 0 }]
    return []
  })
})

describe("ensureDemoDataSeeded", () => {
  it("is a no-op when a Demo config already exists", async () => {
    loadConfigMock.mockImplementation(async (name: string) => (name === "Demo" ? { configName: "Demo" } : null))
    await ensureDemoDataSeeded()
    expect(querySnowflakeMock).not.toHaveBeenCalled()
  })

  it("is a no-op when a default config already exists", async () => {
    loadConfigMock.mockImplementation(async (name: string) => (name === "default" ? { configName: "default" } : null))
    await ensureDemoDataSeeded()
    expect(querySnowflakeMock).not.toHaveBeenCalled()
  })

  it("creates tables, inserts seed rows, and saves a Demo config when nothing exists", async () => {
    loadConfigMock.mockResolvedValue(null)
    await ensureDemoDataSeeded()

    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    expect(sqlCalls.some((s) => /create table if not exists MY_DB\.MY_SCHEMA\.BVA_DEMO_GL_ACTUALS/i.test(s))).toBe(true)
    expect(sqlCalls.some((s) => /create table if not exists MY_DB\.MY_SCHEMA\.BVA_DEMO_BUDGET/i.test(s))).toBe(true)
    expect(sqlCalls.some((s) => /insert into MY_DB\.MY_SCHEMA\.BVA_DEMO_GL_ACTUALS/i.test(s))).toBe(true)
    expect(sqlCalls.some((s) => /insert into MY_DB\.MY_SCHEMA\.BVA_DEMO_BUDGET/i.test(s))).toBe(true)
    expect(sqlCalls.some((s) => /merge into MY_DB\.MY_SCHEMA\.BVA_APP_CONFIG/i.test(s))).toBe(true)
    expect(invalidateConfigCacheMock).toHaveBeenCalledWith("Demo")
    // Allocation status column flows into both the table DDL and the insert.
    expect(sqlCalls.some((s) => /create table if not exists MY_DB\.MY_SCHEMA\.BVA_DEMO_GL_ACTUALS[\s\S]*ALLOCATION_STATUS/i.test(s))).toBe(true)
    expect(sqlCalls.some((s) => /insert into MY_DB\.MY_SCHEMA\.BVA_DEMO_GL_ACTUALS \([^)]*ALLOCATION_STATUS/i.test(s))).toBe(true)
    // Sample semantic views over the demo tables.
    expect(sqlCalls.some((s) => /create or replace semantic view MY_DB\.MY_SCHEMA\.BVA_DEMO_GL_SEMANTIC_VIEW/i.test(s))).toBe(true)
    expect(sqlCalls.some((s) => /create or replace semantic view MY_DB\.MY_SCHEMA\.BVA_DEMO_BUDGET_SEMANTIC_VIEW/i.test(s))).toBe(true)
  })

  it("still saves the Demo config even if semantic view creation fails (non-fatal)", async () => {
    loadConfigMock.mockResolvedValue(null)
    querySnowflakeMock.mockImplementation(async (sql: string) => {
      if (/^merge into \S*BVA_DEMO_SEED_LOCK/i.test(sql)) return [{ "number of rows inserted": 1, "number of rows updated": 0 }]
      if (/^create or replace semantic view/i.test(sql)) throw new Error("insufficient privileges")
      return []
    })
    await ensureDemoDataSeeded()
    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    expect(sqlCalls.some((s) => /merge into MY_DB\.MY_SCHEMA\.BVA_APP_CONFIG/i.test(s))).toBe(true)
    expect(invalidateConfigCacheMock).toHaveBeenCalledWith("Demo")
  })

  it("skips inserting into a table that already has the exact expected row count (idempotent under a race)", async () => {
    loadConfigMock.mockResolvedValue(null)
    // Every `select count(*) as n from ...` probe returns the exact expected
    // count for that table — simulating a concurrent request that already
    // fully seeded everything.
    querySnowflakeMock.mockImplementation(async (sql: string) => {
      if (/^merge into \S*BVA_DEMO_SEED_LOCK/i.test(sql)) return [{ "number of rows inserted": 1, "number of rows updated": 0 }]
      const m = /^select count\(\*\) as n from (\S+)/i.exec(sql)
      if (!m) return []
      const table = m[1]
      if (/GL_ACTUALS/i.test(table)) return [{ N: DEMO_GL_ACTUALS.length }]
      if (/BUDGET/i.test(table)) return [{ N: DEMO_BUDGET.length }]
      if (/LEDGER_ACCOUNT_MAPPING/i.test(table)) return [{ N: DEMO_LEDGER_MAPPING.length }]
      if (/COST_CENTER_MAPPING/i.test(table)) return [{ N: DEMO_COST_CENTERS.length }]
      return [{ N: 0 }]
    })
    await ensureDemoDataSeeded()
    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    expect(sqlCalls.some((s) => /^insert into/i.test(s))).toBe(false)
    expect(sqlCalls.some((s) => /^truncate table/i.test(s))).toBe(false)
  })

  it("re-seeds a table left with a partial row count from a prior interrupted seed", async () => {
    loadConfigMock.mockResolvedValue(null)
    // Budget table has SOME rows, but fewer than the full expected dataset —
    // guards a partial-seed bug: a chunked insert cut off partway would be
    // silently skipped forever by a plain "has any rows" check.
    querySnowflakeMock.mockImplementation(async (sql: string) => {
      if (/^merge into \S*BVA_DEMO_SEED_LOCK/i.test(sql)) return [{ "number of rows inserted": 1, "number of rows updated": 0 }]
      const m = /^select count\(\*\) as n from (\S+)/i.exec(sql)
      if (!m) return []
      const table = m[1]
      if (/BUDGET/i.test(table)) return [{ N: Math.floor(DEMO_BUDGET.length / 4) }]
      if (/GL_ACTUALS/i.test(table)) return [{ N: DEMO_GL_ACTUALS.length }]
      if (/LEDGER_ACCOUNT_MAPPING/i.test(table)) return [{ N: DEMO_LEDGER_MAPPING.length }]
      if (/COST_CENTER_MAPPING/i.test(table)) return [{ N: DEMO_COST_CENTERS.length }]
      return [{ N: 0 }]
    })
    await ensureDemoDataSeeded()
    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    // Only the under-seeded budget table gets truncated + re-inserted.
    expect(sqlCalls.some((s) => /^truncate table \S*BUDGET/i.test(s))).toBe(true)
    expect(sqlCalls.some((s) => /^insert into \S*BUDGET/i.test(s))).toBe(true)
    expect(sqlCalls.some((s) => /^truncate table \S*GL_ACTUALS/i.test(s))).toBe(false)
    expect(sqlCalls.some((s) => /^insert into \S*GL_ACTUALS/i.test(s))).toBe(false)
  })

  it("returns immediately without touching any data table when another caller holds a fresh lock", async () => {
    loadConfigMock.mockResolvedValue(null)
    // Simulates the exact race that used to double every table: a concurrent
    // caller's MERGE already claimed the lock, so ours reports 0 rows
    // affected and must bail out before creating or seeding anything.
    querySnowflakeMock.mockImplementation(async (sql: string) => {
      if (/^merge into \S*BVA_DEMO_SEED_LOCK/i.test(sql)) return [{ "number of rows inserted": 0, "number of rows updated": 0 }]
      return []
    })
    await ensureDemoDataSeeded()
    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    expect(sqlCalls.some((s) => /^create table if not exists \S*BVA_DEMO_GL_ACTUALS/i.test(s))).toBe(false)
    expect(sqlCalls.some((s) => /^insert into/i.test(s))).toBe(false)
    expect(sqlCalls.some((s) => /^merge into \S*BVA_APP_CONFIG/i.test(s))).toBe(false)
  })

  it("never throws — a Snowflake failure is swallowed so the dashboard degrades instead of crashing", async () => {
    loadConfigMock.mockResolvedValue(null)
    querySnowflakeMock.mockRejectedValue(new Error("boom"))
    await expect(ensureDemoDataSeeded()).resolves.toBeUndefined()
  })

  it("does NOT double-insert when a row-count probe fails with a transient (non-existence) error", async () => {
    // Regression: rowCount() used to swallow ANY error into "0 rows," which
    // made seedIfIncomplete() skip the truncate and append a full second
    // copy of the dataset on top of whatever's already there. A table that
    // already has the full row count, but whose count(*) probe throws a
    // transient error (not "does not exist"), must NOT be inserted into again.
    loadConfigMock.mockResolvedValue(null)
    querySnowflakeMock.mockImplementation(async (sql: string) => {
      if (/^merge into \S*BVA_DEMO_SEED_LOCK/i.test(sql)) return [{ "number of rows inserted": 1, "number of rows updated": 0 }]
      const m = /^select count\(\*\) as n from (\S+)/i.exec(sql)
      if (!m) return []
      const table = m[1]
      if (/GL_ACTUALS/i.test(table)) throw new Error("Warehouse resume timed out")
      if (/BUDGET/i.test(table)) return [{ N: DEMO_BUDGET.length }]
      if (/LEDGER_ACCOUNT_MAPPING/i.test(table)) return [{ N: DEMO_LEDGER_MAPPING.length }]
      if (/COST_CENTER_MAPPING/i.test(table)) return [{ N: DEMO_COST_CENTERS.length }]
      return [{ N: 0 }]
    })
    await expect(ensureDemoDataSeeded()).resolves.toBeUndefined()
    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    expect(sqlCalls.some((s) => /^insert into \S*GL_ACTUALS/i.test(s))).toBe(false)
    expect(sqlCalls.some((s) => /^truncate table \S*GL_ACTUALS/i.test(s))).toBe(false)
  })

  it("does nothing when the config table location itself is unset (getConfigTable throws)", async () => {
    loadConfigMock.mockResolvedValue(null)
    getConfigTableMock.mockRejectedValue(new Error("no config table location set"))
    await expect(ensureDemoDataSeeded()).resolves.toBeUndefined()
    expect(querySnowflakeMock).not.toHaveBeenCalled()
  })
})

describe("demo config shape", () => {
  it("passes validateBuildConfig with zero errors and builds SQL with no error comments", () => {
    const demoConfig: BvaConfig = {
      configName: "Demo",
      sourceType: "multi_table",
      actualsTable: "MY_DB.MY_SCHEMA.BVA_DEMO_GL_ACTUALS",
      actualsDateCol: "ACCOUNTING_DATE",
      actualsAmountCol: "TRANSLATED_DEBIT_MINUS_CREDIT_AMOUNT",
      actualsCostCenterCol: "COST_CENTER_NUMBER",
      actualsAccountCol: "LEDGER_ACCOUNT_NUMBER",
      actualsAnchorVersion: "Board Plan",
      accountFilterPrefixes: ["5", "6", "7"],
      negateAmount: false,
      budgetTable: "MY_DB.MY_SCHEMA.BVA_DEMO_BUDGET",
      budgetPeriodCol: "PERIOD",
      budgetPeriodFormat: "date",
      budgetAmountCol: "VALUE",
      budgetCostCenterCol: "COST_CENTER_NUMBER",
      budgetCategoryCol: "ADAPTIVE_EXPENSE_TYPE",
      budgetVersionCol: "PLANNING_VERSION",
      categorySource: "mapping_table",
      ledgerMappingTable: "MY_DB.MY_SCHEMA.BVA_DEMO_LEDGER_ACCOUNT_MAPPING",
      ledgerAccountKeyCol: "CODE",
      ledgerCategoryCol: "CONTROLLABLE_EXPENSE_TYPE",
      keyStripPrefixes: { ledgerAccountKeyCol: ["FIN_"] },
      costCenterTable: "MY_DB.MY_SCHEMA.BVA_DEMO_COST_CENTER_MAPPING",
      ccIdCol: "COST_CENTER_NUMBER",
      ccNameCol: "COST_CENTER_NAME",
      ccDeptCol: "BVA_DEPARTMENT_NAME",
      fiscalPath: "B",
      fyStartMonth: 2,
      agentCanEditConfig: false,
    } as BvaConfig

    expect(validateBuildConfig(demoConfig)).toEqual([])
    const sql = buildFromConfig(demoConfig)
    expect(sql).not.toContain("CONFIG ERRORS")
  })
})

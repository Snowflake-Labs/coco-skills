import { describe, it, expect } from "vitest"
import { validateConfigTables } from "@/app/api/setup/config/route"
import { validateColParam } from "@/app/api/setup/discover/route"
import type { BvaConfig } from "@/lib/config"

// Covers the input-validation hardening added for the PR #4 security review:
//  - validateConfigTables: table-name fields in a saved BvaConfig must be
//    DATABASE.SCHEMA.TABLE before they reach buildFinalSql's CREATE TABLE AS.
//  - validateColParam: the discover route's `col` param must be a bare column
//    identifier (defense-in-depth on top of q()-quoting in the query helpers).

function cfg(overrides: Partial<BvaConfig>): BvaConfig {
  return { configName: "default", ...overrides } as BvaConfig
}

describe("validateConfigTables", () => {
  it("accepts a config with no table fields set", () => {
    expect(validateConfigTables(cfg({}))).toBeNull()
  })

  it("accepts valid 3-part table identifiers", () => {
    expect(validateConfigTables(cfg({
      actualsTable: "DB.SCHEMA.ACTUALS",
      budgetTable: "DB.SCHEMA.BUDGET",
      costCenterTable: "DB.SCHEMA.CC",
      ledgerMappingTable: "DB.SCHEMA.LEDGER",
    }))).toBeNull()
  })

  it("accepts the single_table source shape", () => {
    expect(validateConfigTables(cfg({ singleTable: "DB.SCHEMA.ONE" }))).toBeNull()
  })

  it("ignores empty / whitespace-only optional fields", () => {
    expect(validateConfigTables(cfg({ actualsTable: "", budgetTable: "   " }))).toBeNull()
  })

  it("rejects a two-part (non-fully-qualified) table name", () => {
    const err = validateConfigTables(cfg({ actualsTable: "SCHEMA.ACTUALS" }))
    expect(err).toMatch(/actualsTable/)
  })

  it("rejects an injection-shaped table value", () => {
    const err = validateConfigTables(cfg({ budgetTable: "A.B.C UNION SELECT * FROM secrets" }))
    expect(err).toMatch(/budgetTable/)
  })

  it("names the specific offending field", () => {
    expect(validateConfigTables(cfg({ ledgerMappingTable: "not_qualified" }))).toMatch(/ledgerMappingTable/)
    expect(validateConfigTables(cfg({ singleTable: "bad;drop" }))).toMatch(/singleTable/)
  })
})

describe("validateColParam", () => {
  it("accepts bare column identifiers", () => {
    expect(validateColParam("COST_CENTER")).toBe(true)
    expect(validateColParam("account_id")).toBe(true)
    expect(validateColParam("col$1")).toBe(true)
  })

  it("rejects null / empty", () => {
    expect(validateColParam(null)).toBe(false)
    expect(validateColParam("")).toBe(false)
  })

  it("rejects values with injection / dotted / quoted characters", () => {
    expect(validateColParam("col; drop table x")).toBe(false)
    expect(validateColParam("a.b")).toBe(false)
    expect(validateColParam('col" from t --')).toBe(false)
    expect(validateColParam("col ")).toBe(false)
  })
})

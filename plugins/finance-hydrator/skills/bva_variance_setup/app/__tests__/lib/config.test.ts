import { describe, it, expect } from "vitest"
import { buildFromConfig, buildFinalSql, type BvaConfig, type CustomLogicRule, type ClusterPattern } from "@/lib/config"

const baseConfig: BvaConfig = {
  configName: "default",
  sourceType: "single_table",
  singleTable: "MY_DB.MY_SCHEMA.MY_BVA_TABLE",
}

function rule(overrides: Partial<CustomLogicRule>): CustomLogicRule {
  return {
    id: "r1",
    name: "Test rule",
    kind: "filter",
    sql: "1=1",
    enabled: true,
    source: "manual",
    ...overrides,
  }
}

const multiTableBase: BvaConfig = {
  configName: "default",
  sourceType: "multi_table",
  actualsTable: "MY_DB.MY_SCHEMA.ACTUALS",
  actualsDateCol: "TXN_DATE",
  actualsAmountCol: "AMOUNT",
  actualsCategoryCol: "CATEGORY",
  budgetTable: "MY_DB.MY_SCHEMA.BUDGET",
  budgetPeriodCol: "PERIOD",
  budgetAmountCol: "AMOUNT",
  budgetCategoryCol: "CATEGORY",
  fiscalPath: "B",
  fyStartMonth: 1,
}

describe("buildFromConfig", () => {
  it("does not reference budget.fiscal_quarter when budgetPeriodFormat is 'annual' (no quarter to derive from an annual figure)", () => {
    const cfg: BvaConfig = { ...multiTableBase, budgetPeriodFormat: "annual" }
    const sql = buildFromConfig(cfg)
    expect(sql).not.toContain("CONFIG ERRORS")
    // Only the explanatory comment may mention b.fiscal_quarter — the actual
    // fiscal_quarter projection must fall back to a.fiscal_quarter alone.
    expect(sql).toContain(", a.fiscal_quarter as fiscal_quarter")
    expect(sql).not.toContain("coalesce(a.fiscal_quarter, b.fiscal_quarter)")
  })

  it("still coalesces fiscal_quarter from both sides for a non-annual budget format", () => {
    const cfg: BvaConfig = { ...multiTableBase, budgetPeriodFormat: "date" }
    const sql = buildFromConfig(cfg)
    expect(sql).toContain("coalesce(a.fiscal_quarter, b.fiscal_quarter)")
  })

  it("emits exactly one ELSE clause when a catchall cluster pattern is configured (no double-ELSE SQL error)", () => {
    const cfg: BvaConfig = {
      ...multiTableBase,
      categorySource: "cluster_analysis",
      clusterPatterns: [
        { patternType: "prefix", patternValue: "5", label: "Payroll" } as ClusterPattern,
        { patternType: "catchall", patternValue: "", label: "Miscellaneous" } as ClusterPattern,
      ],
    }
    const sql = buildFromConfig(cfg)
    expect(sql).not.toContain("CONFIG ERRORS")
    expect(sql).not.toMatch(/else[^\n]*\n\s*else/)
    expect(sql).toContain("else 'Miscellaneous'")
  })

  it("respects a catchall pattern regardless of its position in the configured list", () => {
    const cfg: BvaConfig = {
      ...multiTableBase,
      categorySource: "cluster_analysis",
      clusterPatterns: [
        { patternType: "catchall", patternValue: "", label: "Everything else" } as ClusterPattern,
        { patternType: "prefix", patternValue: "5", label: "Payroll" } as ClusterPattern,
      ],
    }
    const sql = buildFromConfig(cfg)
    expect(sql).toContain("else 'Everything else'")
    expect(sql).not.toMatch(/else[^\n]*\n\s*else/)
  })

  it("applies renameMap as a display-time relabel for the plain 'column' categorySource (previously had zero effect)", () => {
    const cfg: BvaConfig = {
      ...multiTableBase,
      categorySource: "column",
      renameMap: { "Travel & Ent": "T&E" },
    }
    const sql = buildFromConfig(cfg)
    expect(sql).not.toContain("CONFIG ERRORS")
    expect(sql).toContain("when")
    expect(sql).toContain("'T&E'")
  })
})

describe("buildFinalSql", () => {
  it("returns buildFromConfig() verbatim when there are no custom logic rules", () => {
    expect(buildFinalSql(baseConfig)).toBe(buildFromConfig(baseConfig))
  })

  it("returns buildFromConfig() verbatim when all rules are disabled", () => {
    const cfg: BvaConfig = {
      ...baseConfig,
      customLogicRules: [rule({ enabled: false, kind: "filter", sql: "cost_center_id = '100'" })],
    }
    expect(buildFinalSql(cfg)).toBe(buildFromConfig(cfg))
  })

  it("layers an enabled filter rule as an AND'd WHERE clause over the baseline subquery", () => {
    const cfg: BvaConfig = {
      ...baseConfig,
      customLogicRules: [rule({ id: "f1", kind: "filter", sql: "cost_center_id <> '999'" })],
    }
    const sql = buildFinalSql(cfg)
    const baseline = buildFromConfig(cfg)
    expect(sql).not.toBe(baseline)
    expect(sql).toContain(`from (\n${baseline}\n) as base_bva`)
    expect(sql).toContain("where 1=1\n  and (cost_center_id <> '999')")
  })

  it("layers an enabled derived_column rule as an extra SELECT column", () => {
    const cfg: BvaConfig = {
      ...baseConfig,
      customLogicRules: [
        rule({
          id: "d1",
          kind: "derived_column",
          sql: "case when cost_center_id like 'UNALLOC%' then 'Unallocated' else 'Allocated' end",
          alias: "allocation_status",
        }),
      ],
    }
    const sql = buildFinalSql(cfg)
    expect(sql).toContain("select\n    base_bva.*")
    expect(sql).toContain("as allocation_status")
    expect(sql).toContain("from (\n" + buildFromConfig(cfg) + "\n) as base_bva")
  })

  it("composes multiple enabled rules (filter + derived_column) together, skipping disabled ones", () => {
    const cfg: BvaConfig = {
      ...baseConfig,
      customLogicRules: [
        rule({ id: "f1", kind: "filter", sql: "actual_expense > 0" }),
        rule({ id: "d1", kind: "derived_column", sql: "expense_category || '!'", alias: "shout_category" }),
        rule({ id: "f2", kind: "filter", sql: "1=0", enabled: false }),
      ],
    }
    const sql = buildFinalSql(cfg)
    expect(sql).toContain("and (actual_expense > 0)")
    expect(sql).toContain("as shout_category")
    expect(sql).not.toContain("1=0")
  })

  it("skips a derived_column rule with no alias (invalid, guard should have caught it earlier)", () => {
    const cfg: BvaConfig = {
      ...baseConfig,
      customLogicRules: [rule({ id: "d1", kind: "derived_column", sql: "1", alias: undefined })],
    }
    const sql = buildFinalSql(cfg)
    // No alias-less column spliced in; falls back to no SELECT additions.
    expect(sql).toContain("select\n    base_bva.*\nfrom (")
  })

  it("nests a filter that references a derived column's alias in an outer query, so the alias is a real column by the time the WHERE runs", () => {
    const cfg: BvaConfig = {
      ...baseConfig,
      customLogicRules: [
        rule({ id: "d1", kind: "derived_column", sql: "case when cost_center_id like 'UNALLOC%' then 'Unallocated' else 'Allocated' end", alias: "allocation_status" }),
        rule({ id: "f1", kind: "filter", sql: "allocation_status = 'Unallocated'" }),
      ],
    }
    const sql = buildFinalSql(cfg)
    // The derived column must be projected in an INNER select (with_derived),
    // and the filter referencing it must live in an OUTER select — not both
    // in the same select's WHERE/SELECT, which SQL would reject.
    expect(sql).toContain("as allocation_status")
    expect(sql).toContain("from (\n") // inner baseline wrap still present
    expect(sql).toContain("as with_derived")
    expect(sql).toContain("where 1=1\n  and (allocation_status = 'Unallocated')")
    // The WHERE must be OUTSIDE the select that defines the alias, not
    // inside the same select block as the alias's own "as allocation_status".
    const aliasDefIdx = sql.indexOf("as allocation_status")
    const whereIdx = sql.indexOf("where 1=1")
    expect(whereIdx).toBeGreaterThan(aliasDefIdx)
    expect(sql.indexOf("as with_derived")).toBeGreaterThan(aliasDefIdx)
    expect(whereIdx).toBeGreaterThan(sql.indexOf("as with_derived"))
  })
})

import { describe, it, expect } from "vitest"
import { validateCustomLogicSql, validateCustomLogicAlias } from "@/lib/custom-logic-guard"
import { BVA_BASELINE_COLUMNS } from "@/lib/config"

const cols = BVA_BASELINE_COLUMNS

describe("validateCustomLogicSql", () => {
  it("accepts a simple filter expression referencing baseline columns", () => {
    expect(validateCustomLogicSql("cost_center_id = '100'", "filter", cols)).toEqual({ ok: true })
  })

  it("accepts a CASE derived_column expression using allowed keywords/functions", () => {
    const sql = "case when cost_center_id like 'UNALLOC%' then 'Unallocated' else 'Allocated' end"
    expect(validateCustomLogicSql(sql, "derived_column", cols)).toEqual({ ok: true })
  })

  it("rejects an empty expression", () => {
    expect(validateCustomLogicSql("   ", "filter", cols).ok).toBe(false)
  })

  it("rejects statement terminators", () => {
    const res = validateCustomLogicSql("actual_expense > 0; DROP TABLE foo", "filter", cols)
    expect(res.ok).toBe(false)
  })

  it("rejects DDL/DML keywords even without a semicolon", () => {
    const res = validateCustomLogicSql("(select 1 from (delete from foo))", "filter", cols)
    expect(res.ok).toBe(false)
    if (!res.ok) expect(res.reason).toMatch(/DELETE/i)
  })

  it("rejects SQL comments", () => {
    expect(validateCustomLogicSql("actual_expense > 0 -- sneaky", "filter", cols).ok).toBe(false)
    expect(validateCustomLogicSql("actual_expense > 0 /* sneaky */", "filter", cols).ok).toBe(false)
  })

  it("rejects a reference to an unknown column", () => {
    const res = validateCustomLogicSql("some_random_column = 1", "filter", cols)
    expect(res.ok).toBe(false)
    if (!res.ok) expect(res.reason).toMatch(/some_random_column/)
  })

  it("rejects a call to a function that isn't on the allow-list, even though it's a function call", () => {
    // Regression: extractBareIdentifiers used to skip any identifier
    // immediately followed by "(", treating it as an unchecked function
    // call — letting GET_DDL, a UDF, or any other reachable function run
    // freely as long as it wasn't a bare column reference.
    const res = validateCustomLogicSql("get_ddl('table', 'MY_DB.SECRET.PAYROLL_RATES')", "derived_column", cols)
    expect(res.ok).toBe(false)
    if (!res.ok) expect(res.reason).toMatch(/get_ddl/i)
  })

  it("still accepts an allow-listed function call (coalesce)", () => {
    expect(validateCustomLogicSql("coalesce(cost_center_id, 'none')", "derived_column", cols).ok).toBe(true)
  })

  it("does not flag function calls (identifiers followed by '(') as unknown columns", () => {
    expect(validateCustomLogicSql("sum(actual_expense) > 0", "filter", cols).ok).toBe(true)
  })

  it("allows referencing an already-approved custom logic alias", () => {
    const res = validateCustomLogicSql("allocation_status = 'Unallocated'", "filter", [...cols, "allocation_status"])
    expect(res.ok).toBe(true)
  })

  it("does not flag identifiers inside string literals as columns", () => {
    const res = validateCustomLogicSql("expense_category = 'DROP TABLE'", "filter", cols)
    expect(res.ok).toBe(true)
  })

  it("rejects an overly long expression", () => {
    const long = "actual_expense = " + "1".repeat(2001)
    expect(validateCustomLogicSql(long, "filter", cols).ok).toBe(false)
  })

  it("rejects a filter expression with no boolean-looking predicate", () => {
    const res = validateCustomLogicSql("actual_expense", "filter", cols)
    expect(res.ok).toBe(false)
  })
})

describe("validateCustomLogicAlias", () => {
  it("accepts a normal identifier", () => {
    expect(validateCustomLogicAlias("allocation_status", cols)).toEqual({ ok: true })
  })

  it("rejects an alias starting with a number", () => {
    expect(validateCustomLogicAlias("1status", cols).ok).toBe(false)
  })

  it("rejects an alias that collides with a baseline column", () => {
    expect(validateCustomLogicAlias("expense_category", cols).ok).toBe(false)
  })

  it("rejects a reserved SQL keyword as an alias", () => {
    expect(validateCustomLogicAlias("select", cols).ok).toBe(false)
  })
})

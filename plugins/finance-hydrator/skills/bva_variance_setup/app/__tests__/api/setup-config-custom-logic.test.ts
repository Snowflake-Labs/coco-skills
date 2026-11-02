import { describe, it, expect } from "vitest"
import { validateCustomLogicRules } from "@/app/api/setup/config/route"
import type { CustomLogicRule } from "@/lib/config"

// Server-side enforcement of lib/custom-logic-guard.ts's policy — the only
// real security boundary for CustomLogicRule.sql/.alias, since a direct
// POST to /api/setup/config bypasses the client-side check in
// components/setup/step-validate.tsx entirely. See lib/custom-logic-guard.ts
// and README.md §14.

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

describe("validateCustomLogicRules", () => {
  it("returns null (no error) when there are no rules", () => {
    expect(validateCustomLogicRules(undefined)).toBeNull()
    expect(validateCustomLogicRules([])).toBeNull()
  })

  it("accepts a valid filter rule", () => {
    expect(validateCustomLogicRules([rule({ sql: "cost_center_id <> '999'" })])).toBeNull()
  })

  it("accepts a valid derived_column rule with a proper alias", () => {
    const rules = [rule({ kind: "derived_column", sql: "'x'", alias: "my_col" })]
    expect(validateCustomLogicRules(rules)).toBeNull()
  })

  it("rejects a rule whose sql fails the guard (forbidden keyword)", () => {
    const rules = [rule({ sql: "1=1; DROP TABLE foo" })]
    const err = validateCustomLogicRules(rules)
    expect(err).not.toBeNull()
    expect(err).toMatch(/Test rule/)
  })

  it("rejects a rule whose sql calls an un-allow-listed function — the exact bypass this route exists to close", () => {
    const rules = [rule({ kind: "derived_column", sql: "get_ddl('table', 'X')", alias: "leak" })]
    const err = validateCustomLogicRules(rules)
    expect(err).not.toBeNull()
    expect(err).toMatch(/get_ddl/i)
  })

  it("rejects a derived_column rule with no alias", () => {
    const rules = [rule({ kind: "derived_column", sql: "'x'", alias: undefined })]
    const err = validateCustomLogicRules(rules)
    expect(err).toMatch(/requires an alias/)
  })

  it("rejects a derived_column rule whose alias collides with a baseline column", () => {
    const rules = [rule({ kind: "derived_column", sql: "'x'", alias: "cost_center_id" })]
    const err = validateCustomLogicRules(rules)
    expect(err).toMatch(/collides/)
  })

  it("rejects two enabled rules sharing the same alias", () => {
    const rules = [
      rule({ id: "d1", kind: "derived_column", sql: "'x'", alias: "dup" }),
      rule({ id: "d2", kind: "derived_column", sql: "'y'", alias: "dup" }),
    ]
    const err = validateCustomLogicRules(rules)
    expect(err).toMatch(/same alias/)
  })

  it("allows two rules with the same alias when one is disabled", () => {
    const rules = [
      rule({ id: "d1", kind: "derived_column", sql: "'x'", alias: "dup" }),
      rule({ id: "d2", kind: "derived_column", sql: "'y'", alias: "dup", enabled: false }),
    ]
    expect(validateCustomLogicRules(rules)).toBeNull()
  })

  it("allows a filter to reference an earlier enabled derived_column rule's alias", () => {
    const rules = [
      rule({ id: "d1", kind: "derived_column", sql: "case when 1=1 then 'A' else 'B' end", alias: "allocation_status" }),
      rule({ id: "f1", kind: "filter", sql: "allocation_status = 'A'" }),
    ]
    expect(validateCustomLogicRules(rules)).toBeNull()
  })
})

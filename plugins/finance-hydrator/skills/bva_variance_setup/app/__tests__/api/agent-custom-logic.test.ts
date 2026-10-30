import { describe, it, expect } from "vitest"
import { buildContext } from "@/app/api/agent/custom-logic/route"
import type { BvaConfig, CustomLogicRule } from "@/lib/config"

const baseCfg: BvaConfig = {
  configName: "default",
  sourceType: "single_table",
  singleTable: "MY_DB.MY_SCHEMA.MY_TABLE",
}

describe("buildContext (custom-logic agent)", () => {
  it("includes the ambiguity-first and output-contract sections unconditionally", () => {
    const prompt = buildContext(null, "")
    expect(prompt).toContain("Ambiguity-first")
    expect(prompt).toContain("```customlogic")
    expect(prompt).toContain("fiscal_year")
    expect(prompt).toContain("actual_expense")
  })

  it("omits the existing-rules section when there are none", () => {
    const prompt = buildContext(baseCfg, "")
    expect(prompt).not.toContain("## Existing custom logic rules")
  })

  it("lists existing enabled and disabled rules distinctly", () => {
    const rules: CustomLogicRule[] = [
      { id: "1", name: "Exclude test CC", kind: "filter", sql: "cost_center_id <> '999'", enabled: true, source: "manual" },
      { id: "2", name: "Allocation status", kind: "derived_column", sql: "1", alias: "allocation_status", enabled: false, source: "agent" },
    ]
    const prompt = buildContext({ ...baseCfg, customLogicRules: rules }, "")
    expect(prompt).toContain("## Existing custom logic rules")
    expect(prompt).toContain("Exclude test CC")
    expect(prompt).toContain("Allocation status")
    expect(prompt).toContain(", disabled")
  })

  it("omits the semantic view section when there's no markdown", () => {
    const prompt = buildContext(baseCfg, "")
    expect(prompt).not.toContain("## Semantic view context")
  })

  it("includes semantic view markdown when provided", () => {
    const prompt = buildContext(baseCfg, "### MY_DB.MY_SCHEMA.MY_VIEW\nDimensions:\n- t.d = expr")
    expect(prompt).toContain("## Semantic view context")
    expect(prompt).toContain("MY_DB.MY_SCHEMA.MY_VIEW")
  })
})

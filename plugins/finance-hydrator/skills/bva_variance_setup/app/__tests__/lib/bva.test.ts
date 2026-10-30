import { describe, it, expect } from "vitest"
import { parseExtraFilters, extraFilterWhereClauses } from "@/lib/bva"

describe("parseExtraFilters", () => {
  it("returns an empty object for null input", () => {
    expect(parseExtraFilters(null)).toEqual({})
  })

  it("returns an empty object for malformed JSON", () => {
    expect(parseExtraFilters("{not json")).toEqual({})
  })

  it("parses a valid column -> values map", () => {
    expect(parseExtraFilters('{"allocation_status":["Unallocated","Allocated"]}')).toEqual({
      allocation_status: ["Unallocated", "Allocated"],
    })
  })

  it("drops an entry with an unsafe column name", () => {
    expect(parseExtraFilters('{"drop table foo": ["x"]}')).toEqual({})
  })

  it("drops an entry whose value is not an array", () => {
    expect(parseExtraFilters('{"allocation_status": "Unallocated"}')).toEqual({})
  })

  it("drops non-string values within an array and empty results", () => {
    expect(parseExtraFilters('{"allocation_status": [1, null, "ok"]}')).toEqual({ allocation_status: ["ok"] })
    expect(parseExtraFilters('{"allocation_status": [1, null]}')).toEqual({})
  })
})

describe("extraFilterWhereClauses", () => {
  it("builds one IN clause per column", () => {
    const clauses = extraFilterWhereClauses({ allocation_status: ["Unallocated"], region: ["East", "West"] })
    expect(clauses).toEqual([
      "allocation_status in ('Unallocated')",
      "region in ('East','West')",
    ])
  })

  it("escapes single quotes in values", () => {
    const clauses = extraFilterWhereClauses({ col: ["O'Brien"] })
    expect(clauses).toEqual(["col in ('O''Brien')"])
  })

  it("returns an empty array for an empty map", () => {
    expect(extraFilterWhereClauses({})).toEqual([])
  })
})

import { describe, it, expect, vi } from "vitest"

const querySnowflakeMock = vi.fn()
vi.mock("@/lib/snowflake", () => ({
  querySnowflake: (...args: unknown[]) => querySnowflakeMock(...args),
}))

import { parseDescribeSemanticView, formatSemanticViewMarkdown, buildSemanticViewBundle, type SemanticViewBundle } from "@/lib/semantic-view-bundle"

// Representative row set matching Snowflake's documented DESCRIBE SEMANTIC VIEW shape.
const DESCRIBE_ROWS: Record<string, unknown>[] = [
  { OBJECT_KIND: null, OBJECT_NAME: null, PARENT_ENTITY: null, PROPERTY: "COMMENT", PROPERTY_VALUE: "Revenue analysis" },
  { OBJECT_KIND: "TABLE", OBJECT_NAME: "CUSTOMERS", PARENT_ENTITY: null, PROPERTY: "BASE_TABLE_DATABASE_NAME", PROPERTY_VALUE: "MY_DB" },
  { OBJECT_KIND: "TABLE", OBJECT_NAME: "CUSTOMERS", PARENT_ENTITY: null, PROPERTY: "BASE_TABLE_SCHEMA_NAME", PROPERTY_VALUE: "MY_SCHEMA" },
  { OBJECT_KIND: "TABLE", OBJECT_NAME: "CUSTOMERS", PARENT_ENTITY: null, PROPERTY: "BASE_TABLE_NAME", PROPERTY_VALUE: "CUSTOMER" },
  { OBJECT_KIND: "TABLE", OBJECT_NAME: "CUSTOMERS", PARENT_ENTITY: null, PROPERTY: "COMMENT", PROPERTY_VALUE: "Main customer table" },
  { OBJECT_KIND: "DIMENSION", OBJECT_NAME: "CUSTOMER_NAME", PARENT_ENTITY: "CUSTOMERS", PROPERTY: "TABLE", PROPERTY_VALUE: "CUSTOMERS" },
  { OBJECT_KIND: "DIMENSION", OBJECT_NAME: "CUSTOMER_NAME", PARENT_ENTITY: "CUSTOMERS", PROPERTY: "EXPRESSION", PROPERTY_VALUE: "customers.c_name" },
  { OBJECT_KIND: "DIMENSION", OBJECT_NAME: "CUSTOMER_NAME", PARENT_ENTITY: "CUSTOMERS", PROPERTY: "DATA_TYPE", PROPERTY_VALUE: "VARCHAR(25)" },
  { OBJECT_KIND: "DIMENSION", OBJECT_NAME: "CUSTOMER_NAME", PARENT_ENTITY: "CUSTOMERS", PROPERTY: "SYNONYMS", PROPERTY_VALUE: ["customer name"] },
  { OBJECT_KIND: "FACT", OBJECT_NAME: "DISCOUNTED_PRICE", PARENT_ENTITY: "LINE_ITEMS", PROPERTY: "TABLE", PROPERTY_VALUE: "LINE_ITEMS" },
  { OBJECT_KIND: "FACT", OBJECT_NAME: "DISCOUNTED_PRICE", PARENT_ENTITY: "LINE_ITEMS", PROPERTY: "EXPRESSION", PROPERTY_VALUE: "l_extendedprice * (1 - l_discount)" },
  { OBJECT_KIND: "METRIC", OBJECT_NAME: "ORDER_AVERAGE_VALUE", PARENT_ENTITY: "ORDERS", PROPERTY: "TABLE", PROPERTY_VALUE: "ORDERS" },
  { OBJECT_KIND: "METRIC", OBJECT_NAME: "ORDER_AVERAGE_VALUE", PARENT_ENTITY: "ORDERS", PROPERTY: "EXPRESSION", PROPERTY_VALUE: "AVG(orders.o_totalprice)" },
]

describe("parseDescribeSemanticView", () => {
  it("extracts the view-level comment", () => {
    const info = parseDescribeSemanticView("MY_DB.MY_SCHEMA.TPCH_REV_ANALYSIS", DESCRIBE_ROWS)
    expect(info.comment).toBe("Revenue analysis")
  })

  it("assembles a logical table's base_table fqn from three separate property rows", () => {
    const info = parseDescribeSemanticView("MY_DB.MY_SCHEMA.TPCH_REV_ANALYSIS", DESCRIBE_ROWS)
    const customers = info.tables.find((t) => t.name === "CUSTOMERS")
    expect(customers?.baseTable).toBe("MY_DB.MY_SCHEMA.CUSTOMER")
    expect(customers?.comment).toBe("Main customer table")
  })

  it("parses dimension/fact/metric properties with expression and synonyms", () => {
    const info = parseDescribeSemanticView("v", DESCRIBE_ROWS)
    const dim = info.properties.find((p) => p.kind === "dimension" && p.name === "CUSTOMER_NAME")
    expect(dim?.expression).toBe("customers.c_name")
    expect(dim?.dataType).toBe("VARCHAR(25)")
    expect(dim?.synonyms).toEqual(["customer name"])

    const fact = info.properties.find((p) => p.kind === "fact" && p.name === "DISCOUNTED_PRICE")
    expect(fact?.expression).toBe("l_extendedprice * (1 - l_discount)")

    const metric = info.properties.find((p) => p.kind === "metric" && p.name === "ORDER_AVERAGE_VALUE")
    expect(metric?.expression).toBe("AVG(orders.o_totalprice)")
  })

  it("returns empty tables/properties for an empty result set", () => {
    const info = parseDescribeSemanticView("v", [])
    expect(info.tables).toEqual([])
    expect(info.properties).toEqual([])
  })

  it("parses a JSON-array-string SYNONYMS value the same as a real array", () => {
    const rows: Record<string, unknown>[] = [
      { OBJECT_KIND: "DIMENSION", OBJECT_NAME: "X", PARENT_ENTITY: "T", PROPERTY: "SYNONYMS", PROPERTY_VALUE: '["a","b"]' },
    ]
    const info = parseDescribeSemanticView("v", rows)
    expect(info.properties[0].synonyms).toEqual(["a", "b"])
  })
})

describe("formatSemanticViewMarkdown", () => {
  it("returns an empty string when there are no views", () => {
    const bundle: SemanticViewBundle = { views: [], source: "none", candidatesConsidered: [], elapsedMs: 0 }
    expect(formatSemanticViewMarkdown(bundle)).toBe("")
  })

  it("renders tables and properties for a manual-source bundle", () => {
    const info = parseDescribeSemanticView("MY_DB.MY_SCHEMA.TPCH_REV_ANALYSIS", DESCRIBE_ROWS)
    const bundle: SemanticViewBundle = {
      views: [info],
      source: "manual",
      candidatesConsidered: [info.view],
      elapsedMs: 10,
    }
    const md = formatSemanticViewMarkdown(bundle)
    expect(md).toContain("customer pointed at directly")
    expect(md).toContain("MY_DB.MY_SCHEMA.TPCH_REV_ANALYSIS")
    expect(md).toContain("CUSTOMERS")
    expect(md).toContain("customers.c_name")
  })

  it("adds a note (not a drop) when many views are flagged via warnManyViews", () => {
    const info = parseDescribeSemanticView("v", DESCRIBE_ROWS)
    const bundle: SemanticViewBundle = {
      views: [info],
      source: "manual",
      candidatesConsidered: [info.view],
      elapsedMs: 10,
      warnManyViews: true,
    }
    const md = formatSemanticViewMarkdown(bundle)
    expect(md).toContain("a lot of context")
    // The view itself is still rendered — nothing was dropped.
    expect(md).toContain("v")
  })
})

describe("buildSemanticViewBundle", () => {
  it("never drops a manual ref regardless of list length — sets warnManyViews above the threshold instead", async () => {
    querySnowflakeMock.mockResolvedValue([
      { OBJECT_KIND: null, OBJECT_NAME: null, PARENT_ENTITY: null, PROPERTY: "COMMENT", PROPERTY_VALUE: "x" },
    ])
    const manyRefs = Array.from({ length: 19 }, (_, i) => `DB.SCHEMA.VIEW_${i}`)
    const bundle = await buildSemanticViewBundle(manyRefs)
    expect(bundle.source).toBe("manual")
    expect(bundle.views.length).toBe(19)
    expect(bundle.warnManyViews).toBe(true)
  })

  it("does not set warnManyViews for a small manual ref list", async () => {
    querySnowflakeMock.mockResolvedValue([])
    const bundle = await buildSemanticViewBundle(["DB.SCHEMA.VIEW_A", "DB.SCHEMA.VIEW_B"])
    expect(bundle.warnManyViews).toBe(false)
  })
})

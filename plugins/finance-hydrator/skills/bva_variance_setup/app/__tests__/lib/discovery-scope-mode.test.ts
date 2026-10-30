import { describe, it, expect, vi, beforeEach } from "vitest"

const querySnowflakeMock = vi.fn()
vi.mock("@/lib/snowflake", () => ({
  querySnowflake: (...args: unknown[]) => querySnowflakeMock(...args),
}))

const getConfigTableMock = vi.fn()
vi.mock("@/lib/config-server", () => ({
  getConfigTable: (...args: unknown[]) => getConfigTableMock(...args),
}))

beforeEach(() => {
  vi.resetModules()
  querySnowflakeMock.mockReset()
  getConfigTableMock.mockReset()
  querySnowflakeMock.mockResolvedValue([])
  delete process.env.BVA_DISCOVERY_SCOPE_TABLE
})

describe("setDiscoveryScopeOverride table resolution", () => {
  // Guards the same aliasing bug as agent-access-mode.test.ts: for a config
  // table ending in "..._CONFIG", an overly specific literal pattern could
  // silently alias this table to the live config table, and
  // setDiscoveryScopeOverride's `DELETE FROM <table>` would wipe every saved
  // config before the mismatched INSERT threw — this test locks in the fix.
  it("resolves to a table distinct from the config table name", async () => {
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_CONFIG")
    const { setDiscoveryScopeOverride } = await import("@/lib/discovery-scope-mode")
    await setDiscoveryScopeOverride(true, "MY_DB", "MY_SCHEMA", "test-user")

    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    const mergeCall = sqlCalls.find((s) => /^merge into/i.test(s))
    expect(mergeCall).toBeDefined()
    expect(mergeCall).not.toMatch(/BVA_VARIANCE_SETUP_CONFIG\b/)
    expect(mergeCall).toContain("BVA_VARIANCE_SETUP_DISCOVERY_SCOPE")
  })

  it("prefers the explicit BVA_DISCOVERY_SCOPE_TABLE env var when set", async () => {
    process.env.BVA_DISCOVERY_SCOPE_TABLE = "MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_DISCOVERY_SCOPE"
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_CONFIG")
    const { setDiscoveryScopeOverride } = await import("@/lib/discovery-scope-mode")
    await setDiscoveryScopeOverride(true, "MY_DB", "", "test-user")

    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    expect(sqlCalls.some((s) => s.includes("MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_DISCOVERY_SCOPE"))).toBe(true)
  })

  it("throws and never touches Snowflake if the resolved table would equal the config table", async () => {
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.SOME_OTHER_TABLE_NAME")
    const { setDiscoveryScopeOverride } = await import("@/lib/discovery-scope-mode")
    await expect(setDiscoveryScopeOverride(true, "MY_DB", "MY_SCHEMA", "test-user"))
      .rejects.toThrow(/same table as the config table/i)
    expect(querySnowflakeMock).not.toHaveBeenCalled()
  })

  it("getDiscoveryScopeOverride fails safe to null instead of throwing on the same misconfiguration", async () => {
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.SOME_OTHER_TABLE_NAME")
    const { getDiscoveryScopeOverride } = await import("@/lib/discovery-scope-mode")
    await expect(getDiscoveryScopeOverride()).resolves.toBeNull()
    expect(querySnowflakeMock).not.toHaveBeenCalled()
  })

  it("actually enables scoping end-to-end when the config table is named correctly (regression for the reported bug)", async () => {
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_CONFIG")
    querySnowflakeMock.mockImplementation(async (sql: string) => {
      if (/^select enabled, database, schema/i.test(sql)) {
        return [{ ENABLED: true, DATABASE: "MY_DB", SCHEMA: "MY_SCHEMA" }]
      }
      return []
    })
    const { setDiscoveryScopeOverride, getDiscoveryScopeOverride } = await import("@/lib/discovery-scope-mode")
    await setDiscoveryScopeOverride(true, "MY_DB", "MY_SCHEMA", "test-user")
    const scope = await getDiscoveryScopeOverride()
    expect(scope).toEqual({ database: "MY_DB", schema: "MY_SCHEMA" })
  })
})

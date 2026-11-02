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
  delete process.env.BVA_AGENT_ACCESS_MODE_TABLE
})

describe("setAgentAccessMode / getAgentAccessMode table resolution", () => {
  // Guards the config-table aliasing bug: for a config table ending in
  // "..._CONFIG", an overly specific literal like `/BVA_APP_CONFIG$/i` never
  // matched, silently aliasing the mode table to the config table, so
  // setAgentAccessMode()'s DELETE FROM would remove saved config rows with no
  // error. This test uses a "..._CONFIG" name (not the old "BVA_APP_CONFIG"
  // fixture that masked the bug).
  it("resolves to a table distinct from the config table name", async () => {
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_CONFIG")
    const { setAgentAccessMode } = await import("@/lib/agent-access-mode")
    await setAgentAccessMode("owner", "test-user")

    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    const mergeCall = sqlCalls.find((s) => /^merge into/i.test(s))
    expect(mergeCall).toBeDefined()
    expect(mergeCall).not.toContain("BVA_VARIANCE_SETUP_CONFIG ") // exact config table, word-boundary
    expect(mergeCall).not.toMatch(/BVA_VARIANCE_SETUP_CONFIG\b/)
    expect(mergeCall).toContain("BVA_VARIANCE_SETUP_AGENT_ACCESS_MODE")
  })

  it("also resolves distinctly for the old literal-suffix table name (regression check)", async () => {
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.BVA_APP_CONFIG")
    const { setAgentAccessMode } = await import("@/lib/agent-access-mode")
    await setAgentAccessMode("owner", "test-user")

    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    const mergeCall = sqlCalls.find((s) => /^merge into/i.test(s))
    expect(mergeCall).toContain("AGENT_ACCESS_MODE")
    expect(mergeCall).not.toMatch(/BVA_APP_CONFIG$/)
  })

  it("prefers the explicit BVA_AGENT_ACCESS_MODE_TABLE env var when set", async () => {
    process.env.BVA_AGENT_ACCESS_MODE_TABLE = "MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_AGENT_ACCESS_MODE"
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_CONFIG")
    const { setAgentAccessMode } = await import("@/lib/agent-access-mode")
    await setAgentAccessMode("caller", "test-user")

    const sqlCalls = querySnowflakeMock.mock.calls.map((c) => String(c[0]))
    expect(sqlCalls.some((s) => s.includes("MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_AGENT_ACCESS_MODE"))).toBe(true)
  })

  it("throws and never touches Snowflake if the resolved table would equal the config table", async () => {
    // Simulate a pathological config table name that the fallback can't
    // safely transform (doesn't end in "_CONFIG") and no env override set.
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.SOME_OTHER_TABLE_NAME")
    const { setAgentAccessMode } = await import("@/lib/agent-access-mode")
    await expect(setAgentAccessMode("owner", "test-user")).rejects.toThrow(/same table as the config table/i)
    expect(querySnowflakeMock).not.toHaveBeenCalled()
  })

  it("getAgentAccessMode falls back to the safe default instead of throwing on the same misconfiguration", async () => {
    getConfigTableMock.mockResolvedValue("MY_DB.MY_SCHEMA.SOME_OTHER_TABLE_NAME")
    const { getAgentAccessMode } = await import("@/lib/agent-access-mode")
    await expect(getAgentAccessMode()).resolves.toBe("caller")
    expect(querySnowflakeMock).not.toHaveBeenCalled()
  })
})

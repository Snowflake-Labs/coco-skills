import { describe, it, expect, vi, beforeEach } from "vitest"

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------
// Keep parseDiscoveryScope real (pure regex, no reason to fake it) but mock
// the expensive/side-effecting discovery functions.
vi.mock("@/lib/discover-bundle", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/discover-bundle")>()
  return {
    ...actual,
    buildDiscoveryBundle: vi.fn(),
    formatBundleMarkdown: vi.fn(() => "MOCK_BUNDLE_MARKDOWN"),
  }
})

vi.mock("@/lib/discovery-scope-mode", () => ({
  getDiscoveryScopeOverride: vi.fn(),
}))

vi.mock("@/lib/profile-bundle", () => ({
  parseSelectedTables: vi.fn(() => null),
  buildProfileBundle: vi.fn(),
  formatProfileMarkdown: vi.fn(),
}))

vi.mock("@/lib/semantic-view-bundle", () => ({
  buildSemanticViewBundle: vi.fn(async () => ({ views: [], source: "none", candidatesConsidered: [], elapsedMs: 0 })),
  formatSemanticViewMarkdown: vi.fn(() => ""),
}))

vi.mock("@/lib/job-store", () => ({
  createJob: vi.fn(),
  appendEvent: vi.fn(),
  pollJob: vi.fn(),
  updateCallerToken: vi.fn(),
  // Run the queued work immediately and hand back the promise so tests can
  // await it (POST itself does `void runExclusive(...)`, never awaiting it).
  runExclusive: vi.fn((fn: () => Promise<unknown>) => fn()),
}))

vi.mock("@/lib/agent-backend", () => ({
  runAgentTurn: vi.fn(async () => {}),
}))

vi.mock("next/headers", () => ({
  headers: vi.fn(async () => ({ get: vi.fn(() => null) })),
}))

import { POST, buildSystemPrompt, wantsFreshDiscovery } from "@/app/api/agent/setup/route"
import { buildDiscoveryBundle } from "@/lib/discover-bundle"
import { getDiscoveryScopeOverride } from "@/lib/discovery-scope-mode"
import { runAgentTurn } from "@/lib/agent-backend"
import { runExclusive } from "@/lib/job-store"
import type { BvaConfig } from "@/lib/config"

const DEMO_CONFIG = { actualsTable: "MY_DB.MY_SCHEMA.BVA_VARIANCE_SETUP_DEMO_GL_ACTUALS" } as unknown as BvaConfig

async function postAndFlush(body: Record<string, unknown>) {
  const req = new Request("http://localhost/api/agent/setup", {
    method: "POST",
    body: JSON.stringify(body),
  })
  const response = await POST(req)
  // POST fires-and-forgets runExclusive(...) without awaiting it — wait for
  // that queued work to actually finish before asserting on its side effects.
  const runExclusiveMock = vi.mocked(runExclusive)
  const pending = runExclusiveMock.mock.results.at(-1)?.value as Promise<unknown> | undefined
  if (pending) await pending
  return response
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(buildDiscoveryBundle).mockResolvedValue({
    buckets: { gl: [], budget: [], cc: [], ledger: [] },
    databasesScanned: [],
    tableCount: 0,
    elapsedMs: 1,
    pass: 1,
    scope: null,
  })
})

// ─── buildSystemPrompt ────────────────────────────────────────────────────

describe("buildSystemPrompt scope directive", () => {
  it("omits the admin-enforced scope directive when no scope is active", () => {
    const prompt = buildSystemPrompt(null, null, null)
    expect(prompt).not.toContain("ADMIN-ENFORCED DISCOVERY SCOPE")
  })

  it("includes the directive with the database-only label when scope has no schema", () => {
    const prompt = buildSystemPrompt(null, null, { database: "MY_DB" })
    expect(prompt).toContain("ADMIN-ENFORCED DISCOVERY SCOPE")
    expect(prompt).toContain("**FINANCE**")
  })

  it("includes the directive with the database.schema label when scope has a schema", () => {
    const prompt = buildSystemPrompt(null, null, { database: "MY_DB", schema: "MY_SCHEMA" })
    expect(prompt).toContain("**MY_DB.MY_SCHEMA**")
    expect(prompt).toContain("schema `MY_SCHEMA`")
  })

  it("puts the directive before the rest of the prompt so it can't be missed", () => {
    const prompt = buildSystemPrompt(null, null, { database: "MY_DB", schema: "MY_SCHEMA" })
    const directiveIdx = prompt.indexOf("ADMIN-ENFORCED DISCOVERY SCOPE")
    const bodyIdx = prompt.indexOf("Core grounding rules")
    expect(directiveIdx).toBeGreaterThanOrEqual(0)
    expect(bodyIdx).toBeGreaterThan(directiveIdx)
  })

  it("still includes the directive when editing an existing saved config", () => {
    const prompt = buildSystemPrompt(DEMO_CONFIG, null, { database: "MY_DB", schema: "MY_SCHEMA" })
    expect(prompt).toContain("ADMIN-ENFORCED DISCOVERY SCOPE")
    expect(prompt).toContain("Existing config (already saved)")
  })
})

// ─── wantsFreshDiscovery ──────────────────────────────────────────────────

describe("wantsFreshDiscovery", () => {
  it("matches the exact reported repro phrase", () => {
    expect(wantsFreshDiscovery("I need to set up BvA. Please search my Snowflake account for financial tables and suggest the right ones.")).toBe(true)
  })

  it("matches other common discovery phrasings", () => {
    expect(wantsFreshDiscovery("Can you scan my databases for the right tables?")).toBe(true)
    expect(wantsFreshDiscovery("please discover the finance tables")).toBe(true)
    expect(wantsFreshDiscovery("look for budget tables in my account")).toBe(true)
  })

  it("does not match unrelated messages", () => {
    expect(wantsFreshDiscovery("what's my current config?")).toBe(false)
    expect(wantsFreshDiscovery("hi")).toBe(false)
    expect(wantsFreshDiscovery("change the fiscal year start to July")).toBe(false)
  })
})

// ─── POST handler — the exact reported regression ────────────────────────

describe("POST /api/agent/setup — discovery scope enforcement", () => {
  it("runs the deterministic scoped scan and injects the scope directive when editing an existing config with an admin scope override active (the reported bug)", async () => {
    vi.mocked(getDiscoveryScopeOverride).mockResolvedValue({ database: "MY_DB", schema: "MY_SCHEMA" })

    await postAndFlush({
      message: "I need to set up BvA. Please search my Snowflake account for financial tables and suggest the right ones.",
      config: DEMO_CONFIG,
    })

    expect(buildDiscoveryBundle).toHaveBeenCalledWith({ database: "MY_DB", schema: "MY_SCHEMA" })

    const runAgentTurnMock = vi.mocked(runAgentTurn)
    expect(runAgentTurnMock).toHaveBeenCalledTimes(1)
    const systemPrompt = runAgentTurnMock.mock.calls[0][0].systemPrompt
    expect(systemPrompt).toContain("ADMIN-ENFORCED DISCOVERY SCOPE")
    expect(systemPrompt).toContain("MY_DB.MY_SCHEMA")
  })

  it("still injects the scope directive even when the message doesn't look like a discovery request (defense-in-depth layer)", async () => {
    vi.mocked(getDiscoveryScopeOverride).mockResolvedValue({ database: "MY_DB", schema: "MY_SCHEMA" })

    await postAndFlush({
      message: "what's my current config?",
      config: DEMO_CONFIG,
    })

    // The heuristic doesn't match, so the deterministic scan should NOT run —
    // but the directive must still be present as the fallback layer.
    expect(buildDiscoveryBundle).not.toHaveBeenCalled()
    const runAgentTurnMock = vi.mocked(runAgentTurn)
    const systemPrompt = runAgentTurnMock.mock.calls[0][0].systemPrompt
    expect(systemPrompt).toContain("ADMIN-ENFORCED DISCOVERY SCOPE")
    expect(systemPrompt).toContain("MY_DB.MY_SCHEMA")
  })

  it("does not change existing (unscoped) behavior when no admin override is set and a config is being edited", async () => {
    vi.mocked(getDiscoveryScopeOverride).mockResolvedValue(null)

    await postAndFlush({
      message: "Please search my Snowflake account for financial tables.",
      config: DEMO_CONFIG,
    })

    // No scope active — precompute must stay gated on !existingCfg, same as before the fix.
    expect(buildDiscoveryBundle).not.toHaveBeenCalled()
    const runAgentTurnMock = vi.mocked(runAgentTurn)
    const systemPrompt = runAgentTurnMock.mock.calls[0][0].systemPrompt
    expect(systemPrompt).not.toContain("ADMIN-ENFORCED DISCOVERY SCOPE")
  })

  it("runs the scoped precompute on a truly fresh setup (no existing config) same as before", async () => {
    vi.mocked(getDiscoveryScopeOverride).mockResolvedValue({ database: "MY_DB", schema: "MY_SCHEMA" })

    await postAndFlush({
      message: "help me set up BvA",
      config: null,
    })

    expect(buildDiscoveryBundle).toHaveBeenCalledWith({ database: "MY_DB", schema: "MY_SCHEMA" })
  })

  it("re-runs the scoped precompute on a LATER turn in an ongoing session when the message asks to re-discover (regression: stale unscoped results from earlier in the session were being reused instead of re-scanning)", async () => {
    vi.mocked(getDiscoveryScopeOverride).mockResolvedValue({ database: "MY_DB", schema: "MY_SCHEMA" })

    await postAndFlush({
      message: "please search my account again",
      config: DEMO_CONFIG,
      sessionId: "123:456",
    })

    expect(buildDiscoveryBundle).toHaveBeenCalledWith({ database: "MY_DB", schema: "MY_SCHEMA" })
    const runAgentTurnMock = vi.mocked(runAgentTurn)
    const systemPrompt = runAgentTurnMock.mock.calls[0][0].systemPrompt
    expect(systemPrompt).toContain("ADMIN-ENFORCED DISCOVERY SCOPE")
    expect(systemPrompt).toContain("REPLACES them")
  })

  it("does not re-run the scoped precompute on a later turn that isn't asking to re-discover (avoids wasted scans on unrelated turns)", async () => {
    vi.mocked(getDiscoveryScopeOverride).mockResolvedValue({ database: "MY_DB", schema: "MY_SCHEMA" })

    await postAndFlush({
      message: "what fiscal year should I use?",
      config: DEMO_CONFIG,
      sessionId: "123:456",
    })

    expect(buildDiscoveryBundle).not.toHaveBeenCalled()
    // But the always-on directive still covers this turn.
    const runAgentTurnMock = vi.mocked(runAgentTurn)
    const systemPrompt = runAgentTurnMock.mock.calls[0][0].systemPrompt
    expect(systemPrompt).toContain("ADMIN-ENFORCED DISCOVERY SCOPE")
  })
})

import type { BvaConfig, CustomLogicRule } from "@/lib/config"
import { BVA_BASELINE_COLUMNS } from "@/lib/config"
import { buildSemanticViewBundle, formatSemanticViewMarkdown } from "@/lib/semantic-view-bundle"
import { createJob, appendEvent, pollJob, runExclusive, updateCallerToken } from "@/lib/job-store"
import { runAgentTurn } from "@/lib/agent-backend"
import { headers } from "next/headers"

export const dynamic = "force-dynamic"

// ─── Custom Logic Agent System Prompt ────────────────────────────────────────
//
// Helps a customer describe additional business logic in plain language (the
// canonical example: splitting expenses into allocated vs. unallocated) and
// turns it into one or more CustomLogicRule proposals — never raw SQL that
// runs directly, never a change to the baseline query itself. See
// lib/config.ts's buildFinalSql() for how proposals get composed on top of
// the customer's existing baseline, and lib/custom-logic-guard.ts for the
// authoritative (server-enforced) validation policy this prompt restates in
// plain language.
//
// Architectural constraint carried over from app/api/agent/setup/route.ts:
// this agent is read-only-SQL-only (see lib/agent-backend.ts's tool
// approval logic) — it may sample real data to ground its proposal, but it
// never writes to the config or the customer's tables itself. A proposal is
// just data (a fenced ```customlogic``` block) the UI applies after the user
// reviews it.

const CUSTOM_LOGIC_PROMPT = `You help a Finance user add custom business logic on top of an already-configured BvA (Budget vs. Actuals) dashboard.

## What you're extending

The dashboard's baseline query always produces exactly these 9 columns:
${BVA_BASELINE_COLUMNS.map((c) => `- ${c}`).join("\n")}

Custom logic rules are layered ON TOP of that baseline, never inside it. Each rule is one of:
- \`filter\` — a boolean SQL expression, ANDed into a WHERE clause over the baseline (e.g. \`cost_center_id <> '999'\`)
- \`derived_column\` — a SQL expression that becomes a NEW column, aliased (e.g. \`case when cost_center_id like 'UNALLOC%' then 'Unallocated' else 'Allocated' end\` as \`allocation_status\`)

Rules can only reference: the 9 baseline columns above, OR the alias of another already-approved rule (so rules can build on each other). They are single SQL EXPRESSIONS, never full statements — no \`;\`, no comments, no DDL/DML (INSERT/UPDATE/DELETE/MERGE/CREATE/ALTER/DROP/TRUNCATE/COPY/GRANT/REVOKE/CALL/EXECUTE). A server-side guard enforces this — if your proposal violates it, it will be silently dropped and the user will see nothing happen, so follow these rules precisely.

## Ambiguity-first — this is the most important rule

**Never guess when more than one reasonable interpretation exists.** Before proposing anything:
1. If the request names a concept with no obvious column/value mapping (e.g. "split into allocated and unallocated" with nothing in the data or semantic view context that obviously encodes that split), sample real data first — distinct values of \`cost_center_id\`, \`department\`, \`expense_category\` (via \`snowflake_sql_execute\`, read-only) — to look for an existing signal (a naming pattern, a flag, a semantic view dimension/synonym matching the concept). If you find nothing conclusive, ask a plain clarifying question (e.g. "Is there a specific cost center prefix, naming pattern, or existing flag column that marks something as unallocated?") — do NOT propose a guessed rule.
2. If multiple plausible expressions could satisfy the request, present them as actual choices for the user to pick from, not a single silent guess.
3. If the request is too vague to imply any concrete rule (e.g. "make it better"), ask an open clarifying question in plain text. Do not emit a \`customlogic\` block.

Only once the ambiguity is resolved — by the user's answer in this same conversation — should you propose a rule.

## When the request isn't a rule at all

A filter or derived column can only change *what data is shown or how a row is labeled* — it can never add a new chart type, a new page, a new widget, or change how the dashboard looks or behaves. If someone asks for one of those, say so plainly in one short sentence (e.g. "That's a change to the app itself, not something I can do from this panel — whoever manages this app for you can make that update.") and stop there — do not emit a \`customlogic\` block, and do not try to approximate it with a rule that doesn't really do what was asked.

## Output contract

Once you have a concrete, unambiguous rule (or set of rules) to propose, respond with a brief plain-language explanation, then a single fenced block:

\`\`\`customlogic
[
  {
    "name": "Allocation status",
    "description": "the user's original ask, verbatim or lightly summarized",
    "kind": "derived_column",
    "sql": "case when cost_center_id like 'UNALLOC%' then 'Unallocated' else 'Allocated' end",
    "alias": "allocation_status"
  }
]
\`\`\`

- \`kind: "filter"\` rules omit \`alias\`.
- Multiple rules can be proposed in one block if the request genuinely implies more than one.
- Never propose a rule that duplicates an already-existing rule (see "Existing custom logic rules" below) — extend or reference it instead.
- Always fully qualify nothing here — rule expressions operate purely on the 9 baseline column names (and other rules' aliases), never on raw source table columns.

Keep your plain-language explanation short — a sentence or two on what the rule does and why, not a restatement of the SQL.`

export function buildContext(cfg: BvaConfig | null, semanticMd: string): string {
  const parts: string[] = [CUSTOM_LOGIC_PROMPT]

  const existingRules = cfg?.customLogicRules ?? []
  if (existingRules.length) {
    parts.push(
      `\n---\n\n## Existing custom logic rules (already saved)\n\n` +
      existingRules.map((r: CustomLogicRule) =>
        `- **${r.name}** (${r.kind}${r.enabled ? "" : ", disabled"}): \`${r.sql}\`${r.alias ? ` as \`${r.alias}\`` : ""}`
      ).join("\n"),
    )
  }

  if (semanticMd) {
    parts.push(`\n---\n\n## Semantic view context\n\n${semanticMd}\n\nPrefer reusing an existing dimension/fact/synonym here over inventing new logic when one clearly matches the request.`)
  }

  return parts.join("\n")
}

// ─── GET — poll for job events ──────────────────────────────────────────────

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url)
  const jobId = searchParams.get("jobId") ?? ""
  const after  = parseInt(searchParams.get("after") ?? "0", 10)

  // Every poll is a fresh incoming request, so it carries a fresh copy of
  // this header — relay it into the running job so the background turn's
  // later tool calls can pick up a newer token than the one captured when
  // the turn started (see the caller-token comment in POST below).
  const callerToken = (await headers()).get("sf-context-current-user-token") ?? ""
  if (callerToken) updateCallerToken(jobId, callerToken)

  const result = pollJob(jobId, after)
  if (!result) return new Response(null, { status: 404 })

  return Response.json(result, {
    headers: { "Cache-Control": "no-store" },
  })
}

// ─── POST — kick off the background job, return jobId immediately ──────────
//
// Same fire-and-forget background job pattern as app/api/agent/setup/route.ts
// — the client gets a jobId back immediately and polls for progress, so a
// long agent turn (including read-only data sampling) never risks the SPCS
// ingress proxy's ~90s response-body limit.

export async function POST(req: Request) {
  let body: { message?: string; config?: BvaConfig | null; sessionId?: string }
  try {
    body = await req.json()
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), { status: 400 })
  }

  const message   = body.message?.trim()
  const cfg       = body.config ?? null
  const sessionId = body.sessionId

  if (!message) {
    return new Response(JSON.stringify({ error: "message is required" }), { status: 400 })
  }

  const jobId = crypto.randomUUID()
  createJob(jobId)

  // Capture the caller's-rights user token synchronously, before entering the
  // background closure below. headers() only works while handling this HTTP
  // request; once runExclusive's callback starts running, this request has
  // already returned to the client, so there is no request context left to
  // read the header from. The Sf-Context-Current-User-Token is short-lived
  // (~2 min) — see app/api/agent/chat/route.ts's GET handler for how later
  // polls relay a fresher token into the running job so a turn that outlives
  // the original token doesn't fail partway through.
  const callerToken = (await headers()).get("sf-context-current-user-token") ?? ""

  void runExclusive(async () => {
    let semanticMd = ""
    try {
      // Interim status so the user sees something while this loads, instead
      // of a blank chat panel — mirrors app/api/agent/setup/route.ts's
      // "Scanning..."/"Profiling..." messages before its own longer-running
      // phases (this import was previously unused here).
      appendEvent(jobId, { type: "text", text: "_Loading your semantic view context…_\n\n" })
      const bundle = await buildSemanticViewBundle(cfg?.semanticViewRefs)
      semanticMd = formatSemanticViewMarkdown(bundle)
    } catch (e) {
      console.error("[api/agent/custom-logic] semantic view bundle failed", e)
    }

    const systemPrompt = buildContext(cfg, semanticMd)

    await runAgentTurn({
      jobId,
      message,
      systemPrompt,
      sessionId,
      maxTurns: 15,
      includeResultPreview: false,
      callerToken,
    })
  })

  return Response.json({ jobId })
}

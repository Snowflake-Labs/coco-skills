/**
 * Fully synthetic demo dataset bundled directly in the app's source code —
 * never a reference to any pre-existing Snowflake table by name. This is what
 * gets created + inserted (see lib/demo-seed.ts) into a customer's own account
 * on first load, so a brand-new deployment shows a working demo immediately,
 * with no dependency on any pre-existing table in the deployment account.
 *
 * The two mapping tables below are small, hand-designed lookups (generic
 * department/category names — Marketing, Engineering, "Salaries & Wages",
 * etc. — nothing derived from or resembling any real company's data).
 * The GL actuals and budget tables are generated deterministically from a
 * seeded PRNG at import time, not hardcoded row-by-row, to keep this file
 * small while still producing a demo with real month-to-month variance.
 */

export interface DemoCostCenterRow {
  COST_CENTER_NUMBER: number
  BVA_DEPARTMENT_NAME: string
  COST_CENTER_NAME: string
}

export interface DemoLedgerMappingRow {
  CODE: string
  CONTROLLABLE_EXPENSE_TYPE: string
}

export interface DemoGlActualsRow {
  TRANSACTION_ID: string
  ACCOUNTING_DATE: string // YYYY-MM-DD
  COST_CENTER_NUMBER: number
  LEDGER_ACCOUNT_NUMBER: number
  TRANSLATED_DEBIT_MINUS_CREDIT_AMOUNT: number
  // Allocated vs. unallocated overhead flag — added so the custom-logic /
  // semantic-view features (README.md §14) have a real, discoverable signal
  // to test against. Deliberately tied to department (a baseline BvA
  // column) rather than random per row, so an agent sampling real data can
  // find the pattern (G&A/Operations = overhead = unallocated) and express
  // it as a `case when department in (...) then 'Unallocated' else
  // 'Allocated' end` custom-logic rule — the canonical
  // "allocated vs. unallocated expenses" scenario.
  ALLOCATION_STATUS: string
}

export interface DemoBudgetRow {
  PERIOD: string // YYYY-MM-DD (first of month)
  PLANNING_VERSION: string
  COST_CENTER_NUMBER: number
  LEDGER_ACCOUNT_NUMBER: number
  ADAPTIVE_EXPENSE_TYPE: string
  VALUE: number
}

const DEPARTMENTS = ["Marketing", "Engineering", "Sales", "People", "G&A", "Finance", "Legal", "Product", "Customer Success", "Operations"]
// Departments treated as unallocated overhead in the demo dataset — see
// DemoGlActualsRow.ALLOCATION_STATUS above.
const UNALLOCATED_DEPARTMENTS = new Set(["G&A", "Operations"])
const TEAM_NAMES = ["Data Platform", "Core Platform", "Field Sales", "Support Engineering", "Demand Gen", "FP&A", "Corporate", "Product Mgmt", "Talent", "Legal Counsel", "Solutions Architecture", "IT Operations"]

export const DEMO_COST_CENTERS: DemoCostCenterRow[] = Array.from({ length: 10 }, (_, i) => ({
  COST_CENTER_NUMBER: 20001 + i,
  BVA_DEPARTMENT_NAME: DEPARTMENTS[i % DEPARTMENTS.length],
  COST_CENTER_NAME: TEAM_NAMES[(i * 3) % TEAM_NAMES.length],
}))

// CODE carries a "FIN_" prefix on purpose — the demo config strips it at query
// time via keyStripPrefixes, mirroring a common real-world pattern where a
// ledger mapping table's key doesn't exactly match the GL table's raw column.
export const DEMO_LEDGER_MAPPING: DemoLedgerMappingRow[] = [
  { CODE: "FIN_500000", CONTROLLABLE_EXPENSE_TYPE: "Salaries & Wages" },
  { CODE: "FIN_500001", CONTROLLABLE_EXPENSE_TYPE: "Bonuses, Commissions & Severance" },
  { CODE: "FIN_600000", CONTROLLABLE_EXPENSE_TYPE: "Salaries & Wages" },
  { CODE: "FIN_610000", CONTROLLABLE_EXPENSE_TYPE: "Travel & Entertainment" },
  { CODE: "FIN_620000", CONTROLLABLE_EXPENSE_TYPE: "SaaS Fees and Non-Capitalized Software" },
  { CODE: "FIN_630000", CONTROLLABLE_EXPENSE_TYPE: "Outside Vendors" },
  { CODE: "FIN_640000", CONTROLLABLE_EXPENSE_TYPE: "Marketing" },
  { CODE: "FIN_650000", CONTROLLABLE_EXPENSE_TYPE: "Rent" },
  { CODE: "FIN_660000", CONTROLLABLE_EXPENSE_TYPE: "Office Expenses" },
  { CODE: "FIN_670000", CONTROLLABLE_EXPENSE_TYPE: "Legal Fees" },
  { CODE: "FIN_680000", CONTROLLABLE_EXPENSE_TYPE: "Conferences Events Training" },
  { CODE: "FIN_710000", CONTROLLABLE_EXPENSE_TYPE: "Data Acquisition Costs" },
  { CODE: "FIN_720000", CONTROLLABLE_EXPENSE_TYPE: "Partner Fees" },
  { CODE: "FIN_730000", CONTROLLABLE_EXPENSE_TYPE: "Marketplace Fees" },
]

const LEDGER_ACCOUNTS = DEMO_LEDGER_MAPPING.map((r) => Number(r.CODE.replace("FIN_", "")))
const PLANNING_VERSIONS = ["Board Plan", "Latest Forecast", "Q1 Forecast", "Working Budget"]
const ANCHOR_VERSION = "Board Plan"

// Deterministic PRNG (mulberry32) — same output every run, so the seed data
// (and any tests against it) never flakes and never depends on Math.random.
function mulberry32(seed: number) {
  let a = seed
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function monthsBetween(startYear: number, startMonth: number, count: number): string[] {
  const out: string[] = []
  let y = startYear, m = startMonth
  for (let i = 0; i < count; i++) {
    out.push(`${y}-${String(m).padStart(2, "0")}-01`)
    m += 1
    if (m > 12) { m = 1; y += 1 }
  }
  return out
}

const CATEGORIES = [...new Set(DEMO_LEDGER_MAPPING.map((r) => r.CONTROLLABLE_EXPENSE_TYPE))]

function buildDemoGlAndBudget(): { gl: DemoGlActualsRow[]; budget: DemoBudgetRow[] } {
  const rng = mulberry32(42)
  const months = monthsBetween(2024, 7, 24) // 2 fiscal years, fyStartMonth=2
  const gl: DemoGlActualsRow[] = []

  // Per (version, category) bias — NOT a single scalar per version. A single
  // per-version scalar biases every category the same direction, so once
  // rows are aggregated by category (or department) in the dashboard, EVERY
  // category shows the same sign of variance for a given version — not
  // realistic. Sampling the bias per category instead gives each version a
  // healthy mix of over/under-budget categories (e.g. Working Budget runs
  // over on Marketing but under on Rent), while keeping any single row's
  // delta modest (a few % of a few-thousand-to-tens-of-thousand dollar base,
  // never a blowout like $100K over/under).
  const categoryVersionBias = new Map<string, number>()
  for (const version of PLANNING_VERSIONS) {
    if (version === ANCHOR_VERSION) continue
    for (const category of CATEGORIES) {
      categoryVersionBias.set(`${version}:${category}`, rng() * 0.16 - 0.08) // ±8%
    }
  }
  const budget: DemoBudgetRow[] = []
  let txnSeq = 1

  // Pre-sample a fixed "true spend level" per (cc, account) combo. This
  // represents how each department/category actually behaves — some chronically
  // over-spend (true mean > 1), some under-spend (true mean < 1). Crucially,
  // this is independent of how any planning version budgeted for the combo, so
  // variance vs. any version is a healthy mix of over and under across
  // categories rather than every category landing on the same side.
  const comboTrueMean = new Map<string, number>()
  for (const cc of DEMO_COST_CENTERS) {
    for (const account of LEDGER_ACCOUNTS) {
      // ±13% from 1.0 — most combos close to nominal, a handful genuinely differ
      comboTrueMean.set(`${cc.COST_CENTER_NUMBER}:${account}`, 0.87 + rng() * 0.26)
    }
  }

  for (const period of months) {
    // Not every cost-center/account combo has activity every month — sparse,
    // like a real GL — but the anchor version (Board Plan) always gets full
    // budget coverage so actuals always have something to compare against.
    for (const cc of DEMO_COST_CENTERS) {
      for (const account of LEDGER_ACCOUNTS) {
        if (rng() > 0.3) continue // ~30% of combos active per month
        const ledgerRow = DEMO_LEDGER_MAPPING[LEDGER_ACCOUNTS.indexOf(account)]
        const anchorBudget = Math.round((3_000 + rng() * 40_000) * 100) / 100

        budget.push({
          PERIOD: period,
          PLANNING_VERSION: ANCHOR_VERSION,
          COST_CENTER_NUMBER: cc.COST_CENTER_NUMBER,
          LEDGER_ACCOUNT_NUMBER: account,
          ADAPTIVE_EXPENSE_TYPE: ledgerRow.CONTROLLABLE_EXPENSE_TYPE,
          VALUE: anchorBudget,
        })

        // Other versions = anchor × (1 + categoryBias + per-combo noise). The
        // bias is keyed by category (not a single scalar for the whole
        // version), so once rows are summed up by category the result is a
        // mix of over- and under-budget categories rather than every
        // category leaning the same way. The per-combo noise is drawn
        // independently from the actuals path, so there's no systematic
        // correlation between a version's budget and whether actuals land
        // above or below it. Coverage must match the anchor's combos
        // exactly: a missing budget row (vs. an existing actual) shows up as
        // 100% "over budget" in the UI, not as a small realistic variance.
        for (const version of PLANNING_VERSIONS) {
          if (version === ANCHOR_VERSION) continue
          const bias = categoryVersionBias.get(`${version}:${ledgerRow.CONTROLLABLE_EXPENSE_TYPE}`) ?? 0
          const comboNoise = rng() * 0.08 - 0.04 // independent ±4% per combo
          budget.push({
            PERIOD: period,
            PLANNING_VERSION: version,
            COST_CENTER_NUMBER: cc.COST_CENTER_NUMBER,
            LEDGER_ACCOUNT_NUMBER: account,
            ADAPTIVE_EXPENSE_TYPE: ledgerRow.CONTROLLABLE_EXPENSE_TYPE,
            VALUE: Math.round(anchorBudget * (1 + bias + comboNoise) * 100) / 100,
          })
        }

        // Actuals: driven by the combo's pre-sampled true spend level + monthly
        // jitter + occasional larger outlier. Because trueMean is independent of
        // the version budgets above, each version comparison shows a realistic
        // mix of over/under categories rather than a wall of red or green.
        const trueMean = comboTrueMean.get(`${cc.COST_CENTER_NUMBER}:${account}`) ?? 1.0
        const monthlyJitter = rng() * 0.10 - 0.05 // ±5% month-to-month noise
        const isOutlier = rng() < 0.09
        const outlierDelta = isOutlier ? ((rng() < 0.5 ? -1 : 1) * (0.09 + rng() * 0.10)) : 0
        const actualFactor = Math.max(0.05, trueMean + monthlyJitter + outlierDelta)
        gl.push({
          TRANSACTION_ID: `JE${String(txnSeq).padStart(8, "0")}`,
          ACCOUNTING_DATE: period,
          COST_CENTER_NUMBER: cc.COST_CENTER_NUMBER,
          LEDGER_ACCOUNT_NUMBER: account,
          TRANSLATED_DEBIT_MINUS_CREDIT_AMOUNT: Math.round(anchorBudget * actualFactor * 100) / 100,
          ALLOCATION_STATUS: UNALLOCATED_DEPARTMENTS.has(cc.BVA_DEPARTMENT_NAME) ? "Unallocated" : "Allocated",
        })
        txnSeq += 1
      }
    }
  }
  return { gl, budget }
}

const { gl: DEMO_GL_ACTUALS, budget: DEMO_BUDGET } = buildDemoGlAndBudget()
export { DEMO_GL_ACTUALS, DEMO_BUDGET }

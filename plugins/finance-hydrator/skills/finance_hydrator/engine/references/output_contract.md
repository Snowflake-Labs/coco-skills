# Output Contract

All scripts exchange JSON-compatible structures so the skill can use deterministic output instead of inferred prose.

## Account Inventory (discovery output)

`discover_inventory.md` emits one `account_inventory.json`, reused by the scorer and by validation.

```json
{
  "scope": {"databases": ["<CUSTOMER_DB>"], "role": "<FINANCE_READER_ROLE>"},
  "domain_access": {"finops": true, "accounting": true, "strategic_finance": false},
  "objects": [
    {
      "data_need_key": "accounting.ap.invoice_header",
      "fqn": "<CUSTOMER_DB>.AP.INVOICE_HEADER",
      "columns_present": ["INVOICE_ID", "SUPPLIER", "INVOICE_AMOUNT"],
      "official": true,
      "usage": {"events_7d": 214, "distinct_users": 9, "last_access": "2026-07-11"},
      "confidence": "HIGH"
    },
    {
      "data_need_key": "accounting.recon.profile_store",
      "fqn": "<CUSTOMER_DB>.ACCOUNTING.<RECON_GET_PROC>",
      "object_type": "procedure",
      "columns_present": {},
      "official": true,
      "confidence": "MEDIUM"
    }
  ],
  "gaps": [
    {"data_need_key": "finance.revenue.forecast_daily", "reason": "no official match / timeout"},
    {"data_need_key": "accounting.recon.profile_store", "reason": "no official match found (table or procedure)", "setup_eligible": true}
  ]
}
```

**`object_type`** (optional field on each inventory object — for the `objects[]` array):
- Omit or `null` for tables and views (default — all standard column-resolution logic applies).
- `"procedure"` for stored procedures discovered via SHOW PROCEDURES. `columns_present` must be
  `{}` for procedures; `score_readiness.py` skips column matching and marks the binding as
  fully resolved at MEDIUM confidence. The generated `hydration_hints.yaml` carries the
  procedure FQN and `object_type: procedure`; the skill resolves output columns at runtime via CALL.

**`setup_eligible`** (optional boolean on `gaps[]` entries):
- `true` when the hydrator can offer to create the missing object on the customer's behalf.
  Currently set only for `accounting.recon.profile_store` when neither a matching table nor
  a matching procedure was found. When `true`, `recommend_skills.md` Phase 4 Step 5 presents
  a **Setup** action alongside the BLOCKED status rather than a plain exclude.
- Omit (or `false`) for all other gaps.

**`extra`** (optional object on `objects[]` entries — written by `setup_profile_store.md`):
- Used when a setup sub-phase creates the object and needs to record auxiliary FQNs:
  ```json
  {
    "save_procedure": "<PROC_DB>.<PROC_SCHEMA>.<SAVE_PROC_NAME>",
    "backing_table":  "<TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>"
  }
  ```
- Not read by `score_readiness.py` or `build_customer_plugin.py`; used only by
  `setup_profile_store.md` Step 7 when writing the final `hydration_hints.yaml`.

`score_readiness.py --inventory account_inventory.json --catalog ...` consumes this and
emits the Assessment shape below. `confidence` is one of HIGH / MEDIUM / LOW.

## Assessment

```json
{
  "skills": [
    {
      "domain": "finops",
      "skill": "warehouse_deep_dive",
      "readiness": 91,
      "status": "READY",
      "recommendation": "include",
      "reason": "ACCOUNT_USAGE metering and query history objects are available.",
      "bindings": {
        "finops.account_usage.metering": {
          "fqn": "SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY",
          "confidence": "HIGH",
          "columns": {
            "warehouse_name": "WAREHOUSE_NAME"
          }
        }
      },
      "gaps": []
    }
  ]
}
```

## Status Values

| Status | Meaning |
|---|---|
| `READY` | All required sources and required columns resolved. |
| `PARTIAL` | Required fields resolved, optional sections missing or degraded. |
| `BLOCKED` | Required source, access, or required column missing. |
| `NOT_RELEVANT` | No matching data or persona fit for the customer account. |

## Smoke Status Values

| Status | Meaning |
|---|---|
| `READY_TO_TEST` | Golden cases exist for this selected skill. |
| `INCONCLUSIVE` | No golden cases exist or no data is available for the test window. |
| `SKIPPED` | Skill was not selected or is blocked. |
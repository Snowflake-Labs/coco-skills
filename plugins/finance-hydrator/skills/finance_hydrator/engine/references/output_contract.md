# Output Contract

All scripts exchange JSON-compatible structures so the skill can use deterministic output instead of inferred prose.

## Account Inventory (discovery output)

`discover_inventory.md` emits one `account_inventory.json`, reused by the scorer and by validation.

```json
{
  "scope": {"databases": ["<CUSTOMER_DB>"], "role": "<FINANCE_READER_ROLE>"},
  "domain_access": {"accounting": true, "strategic_finance": false},
  "objects": [
    {
      "data_need_key": "accounting.ap.invoice_header",
      "fqn": "<CUSTOMER_DB>.AP.INVOICE_HEADER",
      "columns_present": {"invoice_id": "INVOICE_NUMBER", "supplier": "VENDOR_NAME", "invoice_amount": "TOTAL_AMOUNT", "invoice_status": "APPROVAL_STATUS"},
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

**`columns_present`** (REQUIRED format: `{canonical: physical}` mapping):
- Keys are the catalog field names of the data need (`fields` or legacy `columns`); values are the
  exact physical column names found by `DESCRIBE`. Include only fields that were actually mapped.
- `score_readiness.py` matches each catalog field by its canonical name or any of its `aliases`
  (case-insensitive, double quotes ignored) and binds the physical name.
- A flat list of physical names is still accepted for backward compatibility (alias matching
  only); `{}` is used for procedures.
- Emit exactly one object per `data_need_key`; duplicates are rejected.

**`official`** — set by discovery, but not trusted alone: the scorer blocks any FQN whose parts
contain a deny-list token (`dev`, `demo`, `test`, `sandbox`, `scratch`, `tmp`, `temp`, `bkp`,
`backup`, `clone`, `snapshot`, `archive`) or end in `_YYYYMMDD` / `_YYYYMM`, with
`block_kind: NON_OFFICIAL`, even when `official` is `true`. Staging/raw tokens (`prep`, `raw`,
`stg`, `staging`, `landing`, `bronze`, `snap`) are also blocked unless the object carries
`staging_confirmed: true` (the user confirmed that layer is official).

**`object_type`** (optional field on each inventory object — for the `objects[]` array):
- Omit or `null` for tables and views (default — all standard column-resolution logic applies).
- `"procedure"` for stored procedures discovered via SHOW PROCEDURES. `columns_present` must be
  `{}` for procedures; `score_readiness.py` skips column matching and marks the binding as
  fully resolved at MEDIUM confidence. The generated `hydration_hints.yaml` carries the
  procedure FQN and `object_type: procedure`; the skill resolves output columns at runtime via CALL.

**`setup_eligible`** (optional boolean on `gaps[]` entries):
- `true` when the hydrator can offer to create the missing object on the customer's behalf.
  Currently set only for `accounting.recon.profile_store` when neither a matching table nor
  a matching procedure was found. When `true`, `recommend_skills.md` (Phase 4) Step 5 presents
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

## Template placeholders (`hydration_hints.yaml` → `placeholders:`)

Templates reference data only through `{{tokens}}`; `build_customer_plugin.py` writes a flat
`placeholders:` map into each generated `hydration_hints.yaml` that the skill substitutes literally.

| Token | Source in the catalog | Value written |
|---|---|---|
| `{{<p>_fqn}}` | data need `"placeholder": "<p>"` | bound FQN |
| `{{cols.<f>}}` | field name `<f>`, or the field's `"placeholder"` override | bound physical column |
| `{{settings.<s>}}` | skill `"settings"` default, overridden by resolved `extra_settings` | value |
| `{{<r>}}` | skill `"runtime_placeholders"` | not written — supplied at run time (e.g. `dims`, recon `source1`) |

- Only resolved values are written. An unmapped field never disappears silently: readiness-affecting
  optional fields go to `unresolved.columns` (warning → PARTIAL); enrichment fields go to
  `unresolved.enrichment_columns` / `unresolved.enrichment_data_needs` (validator `notes`, status unchanged).
- `"affects_readiness": false` on a field or optional data need marks it enrichment-only: it binds when
  present but never lowers readiness. Not allowed on required fields or needs.
- A field's `"placeholder"` override is required when two needs in one skill share a field name
  (e.g. `cols.period` vs `cols.cal_period`).
- `validate_catalog.py --validate-catalog` fails when an opted-in skill (any need declares
  `placeholder`) uses a token the catalog cannot fill, or two needs produce the same token. Skills not
  yet migrated get a warning.
- Legacy `"columns": {...}` needs produce `cols.<name>` tokens with no override; convert the need to
  `fields` to give a shared name a distinct token.

## Assessment

```json
{
  "skills": [
    {
      "domain": "accounting",
      "skill": "flux_analysis",
      "readiness": 100,
      "status": "READY",
      "recommendation": "include",
      "reason": "All required sources and columns resolved from account inventory.",
      "bindings": {
        "accounting.gl.journal_entries": {
          "fqn": "<CUSTOMER_DB>.GL.JOURNAL_LINES",
          "confidence": "HIGH",
          "columns": {
            "period": "POSTING_PERIOD",
            "account": "GL_ACCOUNT",
            "amount": "NET_AMOUNT"
          }
        }
      },
      "gaps": [],
      "block_kinds": []
    }
  ]
}
```

`block_kinds` (BLOCKED rows only): `MISSING_SOURCE`, `MISSING_REQUIRED_FIELD`, `NON_OFFICIAL`,
`DOMAIN_INACCESSIBLE`.

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
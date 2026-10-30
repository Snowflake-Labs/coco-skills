/** Raw Snowflake queries backing config CRUD: /api/setup/config, /api/setup/config/activate, /api/setup/presets. */

import { querySnowflake } from "../snowflake"
import { WAREHOUSE } from "../bva"
import type { BvaConfig } from "../config"

/**
 * Upserts a single named row of the config table (config_name + config_json
 * VARIANT). This is the one place a config profile actually gets written —
 * shared across four call sites that all need the same "insert if new,
 * overwrite if it already exists" behavior for a full BvaConfig payload:
 * saving the 'default' config (/api/setup/config POST), activating a saved
 * profile into the 'default' slot (/api/setup/config/activate), saving a
 * filter preset (which patches and rewrites the whole 'default' config —
 * /api/setup/presets), and the first-run demo-data bootstrap
 * (lib/demo-seed.ts uses its own insert-only variant, not this function,
 * since it must never overwrite an existing "Demo" row).
 */
export async function upsertConfigRow(table: string, name: string, payload: BvaConfig): Promise<void> {
  const nameLiteral = name.replace(/'/g, "''")
  const jsonLiteral = JSON.stringify(payload).replace(/'/g, "''")
  await querySnowflake(
    `MERGE INTO ${table} AS tgt
     USING (SELECT '${nameLiteral}' AS config_name, PARSE_JSON('${jsonLiteral}') AS config_json) AS src
     ON tgt.config_name = src.config_name
     WHEN MATCHED THEN
       UPDATE SET tgt.config_json = src.config_json, tgt.updated_at = CURRENT_TIMESTAMP()
     WHEN NOT MATCHED THEN
       INSERT (config_name, config_json, updated_at)
       VALUES (src.config_name, src.config_json, CURRENT_TIMESTAMP())`,
    { warehouse: WAREHOUSE },
  )
}

// ── /api/setup/config ───────────────────────────────────────────────────────

/**
 * Lists every saved config profile (name, last-updated time, and a couple of
 * fields pulled out of the JSON for display) — backs the "saved configs"
 * picker so admins can see what's available without opening each one.
 */
export async function getConfigList(table: string) {
  return querySnowflake(
    `SELECT config_name, updated_at,
            config_json:sourceType::VARCHAR   AS source_type,
            config_json:actualsTable::VARCHAR AS actuals_table
     FROM ${table}
     ORDER BY updated_at DESC`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Reads which saved profile (if any) is currently loaded into the 'default'
 * slot, so the config list can flag it as "active" — cleared whenever
 * 'default' is edited and saved directly instead of via activation.
 */
export async function getActivatedFrom(table: string) {
  return querySnowflake(
    `SELECT config_json:activatedFrom::VARCHAR AS activated_from
     FROM ${table} WHERE config_name = 'default' LIMIT 1`,
    { warehouse: WAREHOUSE },
  )
}

/** Returns the most recent saved snapshots of a config, newest first, for the history viewer. */
export async function getConfigHistoryRows(historyTable: string, name: string) {
  return querySnowflake(
    `SELECT saved_at, config_json FROM ${historyTable}
     WHERE config_name = '${name.replace(/'/g, "''")}'
     ORDER BY saved_at DESC LIMIT 10`,
    { warehouse: WAREHOUSE },
  )
}

/** Reads a single named config profile (or 'default') along with its last-updated timestamp. */
export async function getConfigAndUpdatedAt(table: string, name: string) {
  return querySnowflake(
    `SELECT config_json, updated_at FROM ${table}
     WHERE config_name = '${name.replace(/'/g, "''")}'  LIMIT 1`,
    { warehouse: WAREHOUSE },
  )
}

/** Reads just the JSON of a named config, to snapshot into history before a POST overwrites it. */
export async function getConfigJsonForBackup(table: string, name: string) {
  return querySnowflake(
    `SELECT config_json FROM ${table} WHERE config_name = '${name.replace(/'/g, "''")}' LIMIT 1`,
    { warehouse: WAREHOUSE },
  )
}

/** Appends a config snapshot to the history table before it gets overwritten by a save. */
export async function insertConfigHistory(historyTable: string, name: string, existingJson: string): Promise<void> {
  await querySnowflake(
    `INSERT INTO ${historyTable} (config_name, config_json)
     VALUES ('${name.replace(/'/g, "''")}', PARSE_JSON('${existingJson}'))`,
    { warehouse: WAREHOUSE },
  )
}

/** Reads just the JSON of a named config, to snapshot into history before a DELETE removes it. */
export async function getConfigJsonBeforeDelete(table: string, name: string) {
  return querySnowflake(
    `SELECT config_json FROM ${table} WHERE config_name = '${name.replace(/'/g, "''")}'  LIMIT 1`,
    { warehouse: WAREHOUSE },
  )
}

/** Appends a config snapshot to the history table before it gets removed by a delete. */
export async function insertConfigHistoryOnDelete(historyTable: string, name: string, existingJson: string): Promise<void> {
  await querySnowflake(
    `INSERT INTO ${historyTable} (config_name, config_json)
     VALUES ('${name.replace(/'/g, "''")}'  , PARSE_JSON('${existingJson}'))`,
    { warehouse: WAREHOUSE },
  )
}

/** Removes a named config row — either dropping a saved profile, or resetting 'default' to a clean slate. */
export async function deleteConfigRow(table: string, name: string): Promise<void> {
  await querySnowflake(
    `DELETE FROM ${table} WHERE config_name = '${name.replace(/'/g, "''")}'  `,
    { warehouse: WAREHOUSE },
  )
}

// ── /api/setup/config/activate ──────────────────────────────────────────────

/** Reads a saved profile's full JSON by name, to be copied into the 'default' slot on activation. */
export async function getNamedConfigJson(table: string, name: string) {
  return querySnowflake(
    `SELECT config_json FROM ${table}
     WHERE config_name = '${name.replace(/'/g, "''")}'  LIMIT 1`,
    { warehouse: WAREHOUSE },
  )
}

// ── /api/setup/presets ───────────────────────────────────────────────────────

/** Reads just the `savedFilterPresets` field off the 'default' config row — the presets list itself. */
export async function getPresetsJson(table: string) {
  return querySnowflake(
    `SELECT config_json:savedFilterPresets AS presets
     FROM ${table} WHERE config_name = 'default' LIMIT 1`,
    { warehouse: WAREHOUSE },
  )
}

/** Reads the full 'default' config JSON, so a preset write can patch just the presets field and re-save the rest untouched. */
export async function getDefaultConfigJson(table: string) {
  return querySnowflake(
    `SELECT config_json FROM ${table} WHERE config_name = 'default' LIMIT 1`,
    { warehouse: WAREHOUSE },
  )
}

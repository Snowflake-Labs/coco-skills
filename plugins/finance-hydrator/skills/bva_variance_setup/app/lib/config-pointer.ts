/**
 * Reads and writes a small pointer JSON to the Snowflake user's personal stage (@~).
 * The pointer records { configTable: "DB.SCHEMA.BVA_APP_CONFIG" }.
 *
 * @~ is owned unconditionally by the connected user — no schema grants needed.
 */
import fs from "fs"
import path from "path"
import os from "os"
import { querySnowflake } from "./snowflake"
import { WAREHOUSE } from "./bva"

export const POINTER_FILENAME = "bva_app_pointer.json"
const POINTER_STAGE  = `@~/${POINTER_FILENAME}`

export interface ConfigPointer {
  /** Fully-qualified config table, e.g. "YOUR_DB.YOUR_SCHEMA.BVA_APP_CONFIG" */
  configTable: string
}

/**
 * Read the config pointer from the user stage.
 * Returns null if the file doesn't exist yet (first run).
 */
export async function readConfigPointer(): Promise<ConfigPointer | null> {
  try {
    // Read-the-pointer-file query: Snowflake can SELECT directly from a
    // staged file without loading it into a table first. $1 is the file's
    // raw contents; casting to VARIANT parses it as JSON so it can be read
    // like a normal object below.
    const rows = await querySnowflake(
      `SELECT $1::VARIANT AS v FROM ${POINTER_STAGE}`,
      { warehouse: WAREHOUSE },
    )
    const v = rows[0]?.V as { configTable?: string } | undefined
    return v?.configTable ? { configTable: v.configTable } : null
  } catch {
    // Stage file doesn't exist yet or is unreadable — first run
    return null
  }
}

/**
 * Write the config pointer to @~/bva_app_pointer.json via Snowflake PUT.
 * Writes to a temp file, PUTs it to the stage, then cleans up.
 */
export async function writeConfigPointer(pointer: ConfigPointer): Promise<void> {
  const tmpPath = path.join(os.tmpdir(), POINTER_FILENAME)
  fs.writeFileSync(tmpPath, JSON.stringify(pointer), "utf8")
  try {
    // Upload-the-pointer-file query: PUT only accepts a local file path, not
    // raw JSON content directly, which is why the pointer is written to a
    // temp file first and staged from there.
    await querySnowflake(
      `PUT file://${tmpPath} @~/ OVERWRITE=TRUE AUTO_COMPRESS=FALSE`,
      { warehouse: WAREHOUSE },
    )
  } finally {
    try { fs.unlinkSync(tmpPath) } catch { /* ignore cleanup errors */ }
  }
}

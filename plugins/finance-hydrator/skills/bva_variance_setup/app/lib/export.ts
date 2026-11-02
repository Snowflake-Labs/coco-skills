/**
 * "Export to Excel" support for dashboard tables. The dashboard already
 * holds its data as plain JS row objects (from the API routes in
 * app/api/**), so exporting is just: hand each named sheet's rows to the xlsx
 * library, which converts each array of objects into a worksheet (using the
 * object keys as the header row) and serializes the workbook to a
 * downloadable .xlsx file — no server round-trip needed, it all runs
 * client-side in the browser.
 */
import * as XLSX from "xlsx"

type Sheet = { name: string; rows: Record<string, unknown>[] }

/** Download a multi-sheet Excel workbook. */
export function downloadExcel(filename: string, sheets: Sheet[]): void {
  const wb = XLSX.utils.book_new()
  for (const { name, rows } of sheets) {
    const ws = XLSX.utils.json_to_sheet(rows)
    XLSX.utils.book_append_sheet(wb, ws, name.slice(0, 31)) // Excel sheet name limit
  }
  XLSX.writeFile(wb, filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`)
}

/** Round a raw dollar value to the nearest $'000. */
export function toK(v: number): number {
  return Math.round(v / 1000)
}

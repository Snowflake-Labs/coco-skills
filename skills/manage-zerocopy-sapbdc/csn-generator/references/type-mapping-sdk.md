# Type Mapping Reference — Snowflake → Iceberg → CSN

Complete Snowflake → CSN (CDS) type mapping for the SAP BDC zero-copy publish path.

## Overview

Publishing goes through the chain **Snowflake → Iceberg → CSN**. SAP materializes the Snowflake Iceberg column types into a Delta table and validates the CSN against them, so the CSN type must match the **Iceberg** type, not Snowflake's nominal type.

Guiding rules:
1. Integer widths follow the Iceberg type: `int` → `cds.Integer`, `long` → `cds.Integer64`
2. Fixed-point `NUMBER`/`DECIMAL` → `cds.Decimal(p,s)` with exact precision and scale
3. All floating point → `cds.Double` (MSA widens `float` → `double` automatically)
4. Strings → `cds.String` with **no length**
5. Only microsecond timestamps are supported → `cds.Timestamp`
6. Everything else in the "Not supported" list must be excluded or converted

## Full Mapping

| Snowflake Type | Iceberg Type | CSN Type | Notes |
|----------------|--------------|----------|-------|
| BOOLEAN | boolean | `cds.Boolean` | |
| int | int | `cds.Integer` | Only when declared as Iceberg DDL `int` — produces Iceberg `int` (32-bit) |
| long | long | `cds.Integer64` | Only when declared as Iceberg DDL `long` — produces Iceberg `long` (64-bit) |
| INTEGER | int | `cds.Integer` | |
| BIGINT | long | `cds.Integer64` | |
| NUMBER(p,s) | decimal(p,s) | `cds.Decimal(p,s)` | Use matching precision and scale |
| DECIMAL(p,s) | decimal(p,s) | `cds.Decimal(p,s)` | Snowflake alias for `NUMBER(p,s)` |
| FLOAT | float | `cds.Double` | Iceberg type is `float` (32-bit). Declare as `cds.Double` — MSA applies `floatToDoubleWidening` automatically |
| FLOAT4 | float | `cds.Double` | Iceberg type is `float` (32-bit). Same widening as FLOAT |
| FLOAT8 | float | `cds.Double` | Iceberg type is `float` (32-bit). Same widening as FLOAT |
| REAL | double | `cds.Double` | Iceberg type is `double` (64-bit) |
| DOUBLE PRECISION | double | `cds.Double` | Iceberg type is `double` (64-bit) |
| VARCHAR | string | `cds.String` | Do **not** include a length (use `cds.String`, not `cds.String(255)`) |
| STRING | string | `cds.String` | Snowflake synonym for `VARCHAR(134217728)`. Do **not** include a length |
| TEXT | string | `cds.String` | Snowflake synonym for `VARCHAR(134217728)`. Do **not** include a length |
| DATE | date | `cds.Date` | |
| TIMESTAMP_NTZ(6) | timestamp | `cds.Timestamp` | |
| TIMESTAMP_LTZ(6) | timestamptz | `cds.Timestamp` | |
| TIMESTAMP_NTZ(9) | timestamp_ns | — | Not supported |
| TIMESTAMP_LTZ(9) | timestamptz_ns | — | Not supported |
| TIME(6) | time | — | Not supported |
| BINARY | binary | — | Not supported |
| BINARY(n) | fixed(n) | — | Not supported |
| VARIANT | variant | — | Not supported |
| OBJECT(...) | struct | — | Not supported in Datasphere |
| ARRAY(...) | list | — | Not supported in Datasphere |
| MAP(...) | map | — | Not supported in Datasphere |
| GEOMETRY | geometry | — | Not supported |
| GEOGRAPHY | geography | — | Not supported |

## Integer Types

`INTEGER`/`INT`/`SMALLINT`/`TINYINT`/`BYTEINT` produce Iceberg `int` → `cds.Integer` (32-bit). `BIGINT` produces Iceberg `long` → `cds.Integer64` (64-bit). Emit no precision/scale on these.

```json
// INTEGER
{"type": "cds.Integer"}

// BIGINT
{"type": "cds.Integer64"}
```

Do **not** map integers to `cds.Decimal` — that is only for `NUMBER`/`DECIMAL` columns.

## Decimal Types

`NUMBER(p,s)`/`DECIMAL(p,s)`/`NUMERIC(p,s)` → `cds.Decimal` with the exact precision and scale from `INFORMATION_SCHEMA.COLUMNS`.

```json
// DECIMAL(15,2) — currency
{"type": "cds.Decimal", "precision": 15, "scale": 2}

// NUMBER(38,0)
{"type": "cds.Decimal", "precision": 38, "scale": 0}
```

```python
def map_decimal_type(precision, scale) -> dict:
    """Use 'is not None' checks, NOT 'or' — 0 is a valid scale and is falsy."""
    return {
        "type": "cds.Decimal",
        "precision": precision if precision is not None else 38,
        "scale": scale if scale is not None else 0,
    }
```

## Floating Point Types

`FLOAT`/`FLOAT4`/`FLOAT8` are 32-bit Iceberg `float`; `REAL`/`DOUBLE PRECISION` are 64-bit Iceberg `double`. All map to `cds.Double` — MSA applies `floatToDoubleWidening` automatically.

```json
{"type": "cds.Double"}
```

## String Types

`VARCHAR`/`STRING`/`TEXT`/`CHAR` → `cds.String` with **no length**. `STRING` and `TEXT` are Snowflake synonyms for `VARCHAR(134217728)`.

```json
// VARCHAR, VARCHAR(255), STRING, TEXT — all the same
{"type": "cds.String"}
```

```python
def map_string_type() -> dict:
    return {"type": "cds.String"}  # never emit a length
```

## Boolean Type

```json
{"type": "cds.Boolean"}
```

## Date / Time Types

- `DATE` → `cds.Date`
- `TIMESTAMP_NTZ(6)` / `TIMESTAMP_LTZ(6)` (Iceberg `timestamp` / `timestamptz`) → `cds.Timestamp`
- `TIMESTAMP_NTZ(9)` / `TIMESTAMP_LTZ(9)` (Iceberg `timestamp_ns` / `timestamptz_ns`) → **not supported**
- `TIME` (Iceberg `time`) → **not supported**

```json
// DATE
{"type": "cds.Date"}

// TIMESTAMP_NTZ(6)
{"type": "cds.Timestamp"}
```

Reduce nanosecond timestamps to microsecond precision (`(6)`) before publishing; exclude `TIME` columns.

## Not Supported

These Iceberg types cannot be materialized by SAP BDC / consumed in Datasphere. Exclude or convert the column:

| Snowflake Type | Iceberg Type | Reason |
|----------------|--------------|--------|
| TIME(6) | time | Not materializable |
| TIMESTAMP_*(9) | timestamp_ns / timestamptz_ns | Nanosecond precision not supported |
| BINARY / BINARY(n) | binary / fixed(n) | Not materializable |
| VARIANT | variant | Semi-structured not supported |
| OBJECT(...) | struct | Not supported in Datasphere |
| ARRAY(...) | list | Not supported in Datasphere |
| MAP(...) | map | Not supported in Datasphere |
| GEOMETRY | geometry | Not supported |
| GEOGRAPHY | geography | Not supported |

## Complete Type Mapping Function

```python
from typing import Optional, Dict, Any


def map_snowflake_to_cds(
    data_type: str,
    length: Optional[int] = None,      # accepted but unused: strings carry no length
    precision: Optional[int] = None,
    scale: Optional[int] = None,
) -> Dict[str, Any]:
    """Map a Snowflake type to a CSN (CDS) type following Snowflake -> Iceberg -> CSN.

    Raises ValueError for unsupported types.
    """
    upper = data_type.upper()

    # Strings -> cds.String with NO length
    if any(t in upper for t in ['VARCHAR', 'STRING', 'TEXT', 'CHAR']):
        return {"type": "cds.String"}

    # Integers
    if upper in ['INTEGER', 'INT', 'SMALLINT', 'TINYINT', 'BYTEINT']:
        return {"type": "cds.Integer"}
    if upper == 'BIGINT':
        return {"type": "cds.Integer64"}

    # Fixed-point
    if upper in ['NUMBER', 'DECIMAL', 'NUMERIC']:
        return {
            "type": "cds.Decimal",
            "precision": precision if precision is not None else 38,
            "scale": scale if scale is not None else 0,
        }

    # Floating point
    if upper in ['FLOAT', 'FLOAT4', 'FLOAT8', 'REAL', 'DOUBLE', 'DOUBLE PRECISION']:
        return {"type": "cds.Double"}

    # Boolean
    if upper in ['BOOLEAN', 'BOOL']:
        return {"type": "cds.Boolean"}

    # Date / supported timestamps
    if upper == 'DATE':
        return {"type": "cds.Date"}
    if upper in ['TIMESTAMP', 'TIMESTAMP_NTZ', 'TIMESTAMP_LTZ', 'DATETIME']:
        if precision is not None and precision > 6:
            raise ValueError(
                f"{data_type}({precision}) not supported - nanosecond timestamps "
                "(Iceberg timestamp_ns) cannot be materialized. Use precision 6."
            )
        return {"type": "cds.Timestamp"}

    # Unsupported
    unsupported = ['TIMESTAMP_TZ', 'TIME', 'BINARY', 'VARBINARY', 'VARIANT',
                   'OBJECT', 'ARRAY', 'MAP', 'GEOMETRY', 'GEOGRAPHY']
    for key in unsupported:
        if key in upper:
            raise ValueError(
                f"{data_type} not supported by SAP BDC - exclude or convert this column."
            )

    raise ValueError(f"Unknown type '{data_type}' - no CSN mapping available")
```

## Validation Checklist

After mapping types, validate:
- [ ] INTEGER → `cds.Integer`, BIGINT → `cds.Integer64` (never `cds.Decimal`)
- [ ] NUMBER/DECIMAL → `cds.Decimal` with exact precision/scale (use `is not None`, not `or`)
- [ ] FLOAT/FLOAT4/FLOAT8/REAL/DOUBLE → `cds.Double`
- [ ] All strings → `cds.String` with **no length**
- [ ] Supported timestamps (microsecond) → `cds.Timestamp`; no `cds.Time`/`cds.DateTime`
- [ ] No TIME, nanosecond timestamps, BINARY, VARIANT, OBJECT, ARRAY, MAP, GEOMETRY, or GEOGRAPHY columns

## Testing Examples

```python
assert map_snowflake_to_cds('INTEGER') == {"type": "cds.Integer"}
assert map_snowflake_to_cds('BIGINT') == {"type": "cds.Integer64"}
assert map_snowflake_to_cds('VARCHAR', length=255) == {"type": "cds.String"}
assert map_snowflake_to_cds('FLOAT') == {"type": "cds.Double"}
assert map_snowflake_to_cds('TIMESTAMP_NTZ', precision=6) == {"type": "cds.Timestamp"}

import pytest
with pytest.raises(ValueError):
    map_snowflake_to_cds('TIME')
with pytest.raises(ValueError):
    map_snowflake_to_cds('TIMESTAMP_NTZ', precision=9)
with pytest.raises(ValueError):
    map_snowflake_to_cds('VARIANT')
```

## References

- **CSN Interop Spec:** https://sap.github.io/csn-interop-specification/
- **Zero-Copy Integration:** https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/about-sap-snowflake

---

**Key Principle:** Match the **Iceberg** type, keep strings length-free, and exclude anything SAP cannot materialize.

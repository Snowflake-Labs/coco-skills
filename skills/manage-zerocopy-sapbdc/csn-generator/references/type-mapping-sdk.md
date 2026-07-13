# Type Mapping Reference - SDK Rules

Complete Snowflake → CDS type mapping following the SAP BDC Connect SDK behavior.

## Overview

The SDK's type mapping is **intentionally simplistic** to maximize compatibility with SAP BDC. It prioritizes:
1. **Predictability** over sophistication
2. **Compatibility** over precision
3. **Simplicity** over optimization

This document captures those rules exactly.

## String Types

| Snowflake Type | Max Length | CDS Type | Length | Notes |
|----------------|------------|----------|--------|-------|
| VARCHAR(n) | n ≤ 5000 | `cds.String` | n | Exact length preserved |
| VARCHAR(n) | n > 5000 | `cds.String` | 5000 | **Hardcoded** max |
| STRING (unbounded) | N/A | `cds.String` | 5000 | **Hardcoded** |
| TEXT | N/A | `cds.String` | 5000 | **Hardcoded** |
| CHAR(n) | any | `cds.String` | n | Fixed-width → variable |

**Critical Rule:** All unbounded or oversized strings → **exactly 5000**, not `LargeString`

### Python Implementation
```python
def map_string_type(length: Optional[int]) -> dict:
    """Map string types following SDK rules."""
    if length is None or length > 5000:
        return {"type": "cds.String", "length": 5000}  # Hardcoded
    return {"type": "cds.String", "length": length}
```

### Examples
```json
// VARCHAR(100)
{"type": "cds.String", "length": 100}

// VARCHAR(16777216) - Snowflake max
{"type": "cds.String", "length": 5000}  // Capped

// STRING (unbounded)
{"type": "cds.String", "length": 5000}  // Hardcoded
```

## Integer Types

| Snowflake Type | CDS Type | Notes |
|----------------|----------|-------|
| INTEGER | `cds.Integer` | **NOT** `cds.Decimal(38,0)` |
| SMALLINT | `cds.Integer` | Promoted to 32-bit |
| TINYINT | `cds.Integer` | Promoted to 32-bit |
| BIGINT | `cds.Integer64` | 64-bit integer |
| NUMBER (scale=0) | `cds.Integer64` | Snowflake internal representation |

**Critical Rule:** Do **NOT** map INTEGER to `cds.Decimal(38,0)` even though Snowflake stores all integers as NUMBER internally.

### Why This Matters

**Wrong (full CSN approach):**
```json
// Snowflake INTEGER column
{
  "order_count": {
    "type": "cds.Decimal",
    "precision": 38,
    "scale": 0
  }
}
```

**Right (SDK approach):**
```json
{
  "order_count": {
    "type": "cds.Integer"
  }
}
```

SAP BDC expects semantic INTEGER type, not "decimal that happens to have scale=0".

### Python Implementation
```python
def map_integer_type(data_type: str) -> dict:
    """Map integer types following SDK rules."""
    upper = data_type.upper()
    
    if upper in ['INTEGER', 'INT', 'SMALLINT', 'TINYINT']:
        return {"type": "cds.Integer"}  # No precision/scale
    
    if upper in ['BIGINT', 'NUMBER']:  # NUMBER without scale
        return {"type": "cds.Integer64"}
    
    raise ValueError(f"Not an integer type: {data_type}")
```

## Decimal Types

| Snowflake Type | CDS Type | Precision | Scale | Notes |
|----------------|----------|-----------|-------|-------|
| DECIMAL(p,s) | `cds.Decimal` | p | s | Exact mapping |
| NUMERIC(p,s) | `cds.Decimal` | p | s | Synonym |
| NUMBER(p,s) | `cds.Decimal` | p | s | Snowflake type |
| NUMBER (no args) | `cds.Decimal` | 38 | 0 | Default precision |

### Python Implementation
```python
def map_decimal_type(precision: Optional[int], scale: Optional[int]) -> dict:
    """Map decimal types following SDK rules.
    
    CRITICAL: Use 'is not None' checks, NOT 'or' operator.
    The 'or' operator treats 0 as falsy, breaking DECIMAL(38,0) columns.
    """
    effective_precision = precision if precision is not None else 38  # Snowflake max
    effective_scale = scale if scale is not None else 0
    return {
        "type": "cds.Decimal",
        "precision": effective_precision,
        "scale": effective_scale
    }
```

### Examples
```json
// DECIMAL(15,2) - currency
{"type": "cds.Decimal", "precision": 15, "scale": 2}

// DECIMAL(13,3) - quantity
{"type": "cds.Decimal", "precision": 13, "scale": 3}

// NUMBER - unbounded
{"type": "cds.Decimal", "precision": 38, "scale": 0}
```

## Floating Point Types

| Snowflake Type | CDS Type | Notes |
|----------------|----------|-------|
| FLOAT | **REJECTED** | Warn user to use DOUBLE |
| DOUBLE | `cds.Double` | 64-bit floating point |
| REAL | `cds.Double` | Synonym |

**Critical Rule:** **Reject FLOAT types** with a warning message.

### Why Reject FLOAT?

The SDK does not support FLOAT (32-bit) types. Reasons:
1. Precision loss issues in data transfer
2. SAP systems typically use DOUBLE for floating point
3. Snowflake FLOAT is non-standard (variable precision)

### Python Implementation
```python
def map_float_type(data_type: str) -> dict:
    """Map float types following SDK rules."""
    upper = data_type.upper()
    
    if upper == 'FLOAT':
        raise ValueError(
            "FLOAT type not supported by SAP BDC. "
            "Use DOUBLE instead: ALTER TABLE ... MODIFY COLUMN ... DOUBLE"
        )
    
    if upper in ['DOUBLE', 'REAL']:
        return {"type": "cds.Double"}
    
    raise ValueError(f"Unknown float type: {data_type}")
```

## Boolean Types

| Snowflake Type | CDS Type | Notes |
|----------------|----------|-------|
| BOOLEAN | `cds.Boolean` | Direct mapping |
| BOOL | `cds.Boolean` | Synonym |

### Python Implementation
```python
def map_boolean_type() -> dict:
    """Map boolean types."""
    return {"type": "cds.Boolean"}
```

## Date/Time Types

| Snowflake Type | CDS Type | Notes |
|----------------|----------|-------|
| DATE | `cds.Date` | Date only (no time) |
| TIME | `cds.Time` | Time only (no date) |
| TIMESTAMP | `cds.DateTime` | **NOT** `cds.Timestamp` |
| TIMESTAMP_NTZ | `cds.DateTime` | No timezone |
| TIMESTAMP_LTZ | `cds.DateTime` | Local timezone |
| TIMESTAMP_TZ | `cds.DateTime` | With timezone |
| DATETIME | `cds.DateTime` | Synonym |

**Critical Rule:** Map TIMESTAMP → `cds.DateTime`, **NOT** `cds.Timestamp`

### Why DateTime, Not Timestamp?

The SDK uses `cds.DateTime` for timestamp columns because:
1. SAP systems use DATETIME semantics (not UNIX epoch)
2. `cds.Timestamp` is reserved for audit fields (created_at, updated_at)
3. `cds.DateTime` better matches Snowflake TIMESTAMP semantics

### Python Implementation
```python
def map_datetime_type(data_type: str) -> dict:
    """Map date/time types following SDK rules."""
    upper = data_type.upper()
    
    if upper == 'DATE':
        return {"type": "cds.Date"}
    
    if upper == 'TIME':
        return {"type": "cds.Time"}
    
    if 'TIMESTAMP' in upper or upper == 'DATETIME':
        return {"type": "cds.DateTime"}  # NOT cds.Timestamp
    
    raise ValueError(f"Unknown date/time type: {data_type}")
```

### Examples
```json
// DATE
{"type": "cds.Date"}

// TIMESTAMP_NTZ
{"type": "cds.DateTime"}  // NOT cds.Timestamp

// TIME
{"type": "cds.Time"}
```

## Binary Types

| Snowflake Type | CDS Type | Length | Notes |
|----------------|----------|--------|-------|
| BINARY(n) | `cds.Binary` | n | Fixed-width |
| VARBINARY(n) | `cds.Binary` | n | Variable-width |
| BINARY (unbounded) | `cds.Binary` | 5000 | Hardcoded max |

### Python Implementation
```python
def map_binary_type(length: Optional[int]) -> dict:
    """Map binary types following SDK rules."""
    if length is None or length > 5000:
        return {"type": "cds.Binary", "length": 5000}  # Hardcoded
    return {"type": "cds.Binary", "length": length}
```

## Complex Types (Not Supported)

| Snowflake Type | CDS Type | Notes |
|----------------|----------|-------|
| VARIANT | **NOT SUPPORTED** | Use STRING(5000) |
| OBJECT | **NOT SUPPORTED** | Flatten to columns |
| ARRAY | **NOT SUPPORTED** | Use separate table |
| GEOGRAPHY | **NOT SUPPORTED** | Use STRING |
| GEOMETRY | **NOT SUPPORTED** | Use STRING |

**Rule:** Warn user that complex types must be flattened or converted.

### Python Implementation
```python
def map_complex_type(data_type: str) -> dict:
    """Handle complex types (unsupported)."""
    upper = data_type.upper()
    
    if upper in ['VARIANT', 'GEOGRAPHY', 'GEOMETRY']:
        warnings.warn(
            f"{upper} type not supported by SAP BDC. "
            f"Mapping to cds.String(5000) - consider flattening."
        )
        return {"type": "cds.String", "length": 5000}
    
    if upper in ['OBJECT', 'ARRAY']:
        raise ValueError(
            f"{upper} type not supported by SAP BDC. "
            "Flatten nested structures before generating CSN."
        )
    
    raise ValueError(f"Unknown complex type: {data_type}")
```

## Complete Type Mapping Function

```python
from typing import Optional, Dict, Any
import warnings

def map_snowflake_to_cds(
    data_type: str,
    length: Optional[int] = None,
    precision: Optional[int] = None,
    scale: Optional[int] = None
) -> Dict[str, Any]:
    """
    Map Snowflake type to CDS type following SDK rules.
    
    Args:
        data_type: Snowflake type name (e.g., 'VARCHAR', 'INTEGER')
        length: For string/binary types
        precision: For decimal types
        scale: For decimal types
    
    Returns:
        Dict with 'type' and optional type-specific properties
    
    Raises:
        ValueError: For unsupported types or FLOAT usage
    """
    upper_type = data_type.upper()
    
    # String types
    if any(t in upper_type for t in ['VARCHAR', 'STRING', 'TEXT', 'CHAR']):
        if length is None or length > 5000:
            return {"type": "cds.String", "length": 5000}
        return {"type": "cds.String", "length": length}
    
    # Integer types - CRITICAL: use cds.Integer
    if upper_type in ['INTEGER', 'INT', 'SMALLINT', 'TINYINT']:
        return {"type": "cds.Integer"}
    if upper_type in ['BIGINT']:
        return {"type": "cds.Integer64"}
    
    # Decimal types - CRITICAL: use 'is not None', NOT 'or' operator
    if upper_type in ['DECIMAL', 'NUMERIC']:
        effective_precision = precision if precision is not None else 15
        effective_scale = scale if scale is not None else 2
        return {
            "type": "cds.Decimal",
            "precision": effective_precision,
            "scale": effective_scale
        }
    if upper_type == 'NUMBER':
        if scale == 0:
            return {"type": "cds.Integer64"}  # Integer NUMBER
        effective_precision = precision if precision is not None else 38
        effective_scale = scale if scale is not None else 0
        return {
            "type": "cds.Decimal",
            "precision": effective_precision,
            "scale": effective_scale
        }
    
    # Float types - REJECT FLOAT
    if upper_type == 'FLOAT':
        raise ValueError(
            "FLOAT type not supported. Use DOUBLE: "
            "ALTER TABLE ... MODIFY COLUMN ... DOUBLE"
        )
    if upper_type in ['DOUBLE', 'REAL']:
        return {"type": "cds.Double"}
    
    # Boolean
    if upper_type in ['BOOLEAN', 'BOOL']:
        return {"type": "cds.Boolean"}
    
    # Date/Time - CRITICAL: TIMESTAMP → cds.DateTime
    if upper_type == 'DATE':
        return {"type": "cds.Date"}
    if upper_type == 'TIME':
        return {"type": "cds.Time"}
    if 'TIMESTAMP' in upper_type or upper_type == 'DATETIME':
        return {"type": "cds.DateTime"}  # NOT cds.Timestamp
    
    # Binary
    if 'BINARY' in upper_type:
        if length is None or length > 5000:
            return {"type": "cds.Binary", "length": 5000}
        return {"type": "cds.Binary", "length": length}
    
    # Complex types (warn/reject)
    if upper_type in ['VARIANT', 'GEOGRAPHY', 'GEOMETRY']:
        warnings.warn(
            f"{upper_type} not supported - mapping to cds.String(5000)"
        )
        return {"type": "cds.String", "length": 5000}
    
    if upper_type in ['OBJECT', 'ARRAY']:
        raise ValueError(
            f"{upper_type} not supported - flatten structure first"
        )
    
    # Fallback
    warnings.warn(f"Unknown type {data_type} - defaulting to cds.String(5000)")
    return {"type": "cds.String", "length": 5000}
```

## Validation Checklist

After mapping types, validate:
- [ ] No `cds.Decimal` for INTEGER columns (should be `cds.Integer`)
- [ ] No `cds.Timestamp` for TIMESTAMP columns (should be `cds.DateTime`)
- [ ] All string lengths > 5000 capped at exactly 5000
- [ ] No FLOAT types (rejected with error)
- [ ] No OBJECT/ARRAY types (rejected with error)
- [ ] VARIANT/GEOGRAPHY/GEOMETRY mapped to String(5000) with warning

## Testing Examples

### Test Case 1: Integer Column
```python
# Snowflake: INTEGER column
result = map_snowflake_to_cds('INTEGER')
assert result == {"type": "cds.Integer"}  # NOT cds.Decimal(38,0)
```

### Test Case 2: Timestamp Column
```python
# Snowflake: TIMESTAMP_NTZ column
result = map_snowflake_to_cds('TIMESTAMP_NTZ')
assert result == {"type": "cds.DateTime"}  # NOT cds.Timestamp
```

### Test Case 3: Large String
```python
# Snowflake: VARCHAR(16777216) column
result = map_snowflake_to_cds('VARCHAR', length=16777216)
assert result == {"type": "cds.String", "length": 5000}  # Capped
```

### Test Case 4: Float Rejection
```python
# Snowflake: FLOAT column
with pytest.raises(ValueError, match="FLOAT type not supported"):
    map_snowflake_to_cds('FLOAT')
```

## Cross-Database Notes

### Postgres → CDS

| Postgres Type | CDS Type | Notes |
|---------------|----------|-------|
| INTEGER | `cds.Integer` | Same as Snowflake |
| TEXT | `cds.String`, `length: 5000` | Unbounded → capped |
| TIMESTAMP | `cds.DateTime` | Same as Snowflake |
| BYTEA | `cds.Binary`, `length: 5000` | Binary data |

### BigQuery → CDS

| BigQuery Type | CDS Type | Notes |
|---------------|----------|-------|
| INT64 | `cds.Integer64` | 64-bit integer |
| STRING | `cds.String`, `length: 5000` | Unbounded → capped |
| TIMESTAMP | `cds.DateTime` | Same as Snowflake |
| BYTES | `cds.Binary`, `length: 5000` | Binary data |

## References

- **SDK Source:** Reverse-engineered from `sap-bdc-connect-sdk`
- **CSN Interop Spec:** https://sap.github.io/csn-interop-specification/
- **Analysis:** See `/SkillCSN/sdk-csn-generator-source-analysis.md`

---

**Key Principle:** When in doubt, favor **simplicity** and **SDK compatibility** over sophistication.

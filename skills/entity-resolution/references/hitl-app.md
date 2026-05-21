# HITL Review App -- Streamlit Requirements

Delegate to the `developing-with-streamlit` skill to build this app. This document provides the entity-resolution-specific requirements, UX patterns, and Material Design styling.

> **Note:** The file `er_hitl_review.py` in the skill root is a **reference implementation** built for a specific pharma domain engagement (hardcoded to `ER_SKILL_TEST_DB1.NPI` schema with pharma-specific columns). Do NOT copy it directly — use the generic, table-agnostic patterns described below instead. The reference implementation demonstrates the UX patterns and Material Design styling but must be adapted to the customer's schema and column names using the auto-detection logic in this document.

## Purpose

Allow human reviewers to validate entity resolution results one record at a time. The reviewer sees the source entity fields alongside the resolved/matched entity fields, makes a decision (Accept / Reject / Flag), optionally adds a comment, and advances to the next record.

## Generic Input

The skill starts by discovering available tables:

### Table Selection

1. Detect the user's current database and schema:

```sql
SELECT CURRENT_DATABASE(), CURRENT_SCHEMA();
```

2. List tables in the current schema:

```sql
SHOW TABLES IN SCHEMA <DATABASE>.<SCHEMA>;
```

3. Present the tables to the user and ask them to pick one. Include row counts if available from the SHOW output. Offer the option to specify a different schema or fully qualified table name if the desired table is not listed:

```
Tables in TEMP.JJOUBERT:

  1. HITL_ENTITY_RESOLUTION_TEST (12 rows)
  2. OTHER_TABLE (500 rows)
  3. ...

Select a table, or provide a fully qualified name (DATABASE.SCHEMA.TABLE) for a different schema.
```

**MANDATORY STOPPING POINT**: Wait for user to select a table before proceeding.

After the table is selected, run `DESCRIBE TABLE` to retrieve all columns. Then **auto-detect the primary key** and **column groups** by classifying columns using naming patterns.

### Primary Key Auto-Detection

Identify candidate PK columns using these heuristics (ordered by priority):

1. Columns with names ending in `_id` or `_key` that appear first in the table
2. Columns named exactly `ID`, `PK`, `ROW_ID`, `RECORD_ID`, `ENTITY_ID`, `SUB_ENTITY_ID`
3. The first VARCHAR or NUMBER column in the table

To validate candidates, run:

```sql
SELECT COUNT(*) AS total, COUNT(DISTINCT <candidate>) AS distinct_ct
FROM <TABLE>;
```

A valid PK has `total = distinct_ct` (all values unique).

Present the top candidate(s) to the user:

```
I detected these potential primary key columns:

  1. SUB_ENTITY_ID (VARCHAR, 12 unique / 12 total)
  2. CONSOLIDATED_LOCATION_ID (VARCHAR, 12 unique / 12 total)

Which column should be used as the primary key?
```

**MANDATORY STOPPING POINT**: Wait for user to confirm or specify the primary key before proceeding.

After the PK is confirmed, **auto-detect column groups** by classifying the remaining columns by naming patterns. Present the proposed grouping to the user for confirmation/editing.

### Column Auto-Detection Logic

Query the table metadata:

```sql
DESCRIBE TABLE <DATABASE.SCHEMA.TABLE>;
```

Then classify each column into groups using these heuristics (case-insensitive):

**Source entity columns** (left side of comparison):
- Columns containing prefixes/infixes like: `raw_`, `source_`, `src_`, `original_`, `input_`
- Columns containing entity-side identifiers like: `vinemeds_`, `left_`, `entity_a_`, `record_a_`
- Common entity field names without a resolved-side prefix: `name`, `address`, `street`, `city`, `state`, `zip`

**Resolved entity columns** (right side of comparison):
- Columns containing prefixes/infixes like: `matched_`, `resolved_`, `npi_`, `target_`, `right_`, `entity_b_`, `record_b_`, `canonical_`, `golden_`
- Columns that mirror a detected source column but with a different prefix (e.g., `VINEMEDS_RAW_CITY` pairs with `NPI_CITY`)

**Signal columns** (match quality indicators):
- Columns with names containing: `confidence`, `score`, `sim`, `similarity`, `jw`, `jaro`, `cosine`, `method`, `decision`, `tier`, `match_level`
- Numeric columns (FLOAT, NUMBER) that are not clearly entity fields
- Columns with `_exact` suffix (boolean match flags)

**Reasoning columns** (match explanation):
- Columns with names containing: `reason`, `reasoning`, `explanation`, `rationale`, `justification`, `why`, `notes`, `detail`, `summary`, `narrative`, `logic`
- Prefer longer VARCHAR/TEXT columns over short ones
- Typically contains a free-text explanation of why the match was made

**Review columns** (exclude from comparison, already part of REVIEW_DECISIONS):
- Columns containing: `reviewer_`, `reviewed_`, `review_decision`, `review_comment`

**Unclassified columns:**
- Primary key column (already identified)
- Any column not matching the above patterns -- include in a separate "Other" group

### Presenting the Proposed Grouping

After auto-detection, present the results to the user in a clear format:

```
I analyzed the columns in <TABLE> and propose this grouping:

Source Entity (left side):
  - SOURCE_RAW_NAME
  - SOURCE_RAW_ADDRESS
  - SOURCE_RAW_CITY
  - SOURCE_RAW_STATE
  - SOURCE_RAW_ZIP

Resolved Entity (right side):
  - RESOLVED_NAME
  - RESOLVED_ADDRESS
  - RESOLVED_CITY
  - RESOLVED_STATE
  - RESOLVED_ZIP

Signals:
  - CONFIDENCE (FLOAT)
  - COSINE_SIM (FLOAT)
  - NAME_JW (FLOAT)
  - MATCH_METHOD (VARCHAR)
  - DECISION (VARCHAR)

Reasoning:
  - MATCH_REASONING (VARCHAR)

Excluded (review/other):
  - REVIEWER_DECISION
  - REVIEWER_COMMENT
  - REVIEWED_BY
  - REVIEWED_AT

Would you like to adjust any of these groupings?
```

**MANDATORY STOPPING POINT**: Wait for the user to confirm or modify the column groupings before generating the app. Accept changes like moving columns between groups, removing columns, or adding columns that were misclassified.

### Pairing Source and Resolved Columns

After grouping is confirmed, attempt to pair source columns with their resolved counterparts for the side-by-side comparison. Pair by:
1. **Suffix matching** -- strip the prefix and match on the remainder (e.g., `VINEMEDS_RAW_CITY` and `NPI_CITY` both end with `CITY`)
2. **Semantic similarity** -- columns with names like `RAW_NAME` and `MATCHED_NPI_NAME` both contain `NAME`
3. **Position** -- if source and resolved lists are the same length and ordered similarly, pair by position as fallback

Present the proposed pairing and let the user adjust:

```
Proposed field pairing for side-by-side comparison:

  VINEMEDS_RAW_NAME    <-->  NPI_NAME
  VINEMEDS_RAW_ADDRESS <-->  NPI_ADDRESS
  VINEMEDS_RAW_CITY    <-->  NPI_CITY
  VINEMEDS_RAW_STATE   <-->  NPI_STATE
  VINEMEDS_RAW_ZIP     <-->  NPI_ZIP

Adjust if needed.
```

## Review Decisions Table

Before the app runs, create a review decisions table in the same schema as the source table:

```sql
CREATE TABLE IF NOT EXISTS <SCHEMA>.REVIEW_DECISIONS (
    PRIMARY_KEY_VALUE VARCHAR,
    SOURCE_TABLE VARCHAR,
    REVIEWER_DECISION VARCHAR,       -- 'accept', 'reject', 'flag'
    REVIEWER_COMMENT VARCHAR,
    REVIEWED_BY VARCHAR,
    REVIEWED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT uq_review UNIQUE (PRIMARY_KEY_VALUE, SOURCE_TABLE)
);
```

When a reviewer submits a decision, MERGE into this table using the primary key value and source table name. This allows re-review (latest decision wins).

## App Structure -- Single Page

The app is a single-page Streamlit application. No multi-page navigation. All functionality lives on one screen.

### Eliminating Top Whitespace

Streamlit adds significant top padding by default. Inject this CSS at the very top of the app (before any content) to remove it:

```css
/* Remove default Streamlit top padding */
.stApp > header { display: none; }
div[data-testid="stAppViewContainer"] > div:first-child { padding-top: 0; }
block-container { padding-top: 1rem; }
section[data-testid="stSidebar"] { display: none; }
.stApp { margin-top: -2rem; }
```

This must be the first `st.markdown(unsafe_allow_html=True)` call in the app.

### Layout (Top to Bottom)

#### 1. Side-by-Side Entity Comparison (FIRST visible element)

The entity mapping is the primary content and must appear immediately with no whitespace above it. Show a compact inline header: app title + record counter + summary chips on the same line, then the comparison table directly below.

**Compact header (single row):**
```
Entity Resolution Review  |  Record 3 of 47 unreviewed  |  12 Accepted | 3 Rejected | 2 Flagged
```

Use `st.columns` for inline layout. Style summary chips with colored backgrounds (green=accepted, red=rejected, amber=flagged).

**Comparison table immediately below the header:**

```
| FIELD            | SOURCE ENTITY              | RESOLVED ENTITY            |
|------------------|----------------------------|----------------------------|
| Name             | WALGREENS PHARMACY         | WALGREENS CO               |
| Address          | 100 MAIN ST                | 100 MAIN STREET            |
| City             | MIAMI                      | MIAMI                      |
| State            | FL                         | FL                         |
| ZIP              | 33101                      | 33101                      |
```

Use a three-column layout: field label (narrow), source value, resolved value.

**Field-level match indicators:** After each resolved entity value, show a colored indicator:
- Green chip "Exact" -- values are identical (case-insensitive, trimmed)
- Amber chip "Similar" -- values differ but are close (present when a corresponding JW or similarity score column exists and value >= 0.80)
- Red chip "Different" -- values differ significantly

Implementation: Use `st.columns([1, 2, 2])` per row. Apply CSS classes for the indicator chips.

#### 2. Signals Bar (below the mapping)

A horizontal row of compact metric cards showing match quality signals. All values must use a **uniform small font size (13px)** -- do NOT use `st.metric` (its label/value sizing is too large and inconsistent). Instead, render each signal as a styled HTML snippet inside `st.markdown(unsafe_allow_html=True)`:

```html
<div class="signal-card">
  <span class="signal-label">Confidence</span>
  <span class="signal-value">0.87</span>
</div>
```

```css
.signal-card {
    display: inline-block;
    background: #F5F5F5;
    border-radius: 8px;
    padding: 6px 14px;
    margin-right: 8px;
    text-align: center;
}
.signal-label {
    display: block;
    font-size: 11px;
    font-weight: 500;
    color: #757575;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.signal-value {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: #212121;
}
```

Color-code numeric signal values: green >= 0.85, amber >= 0.70, red < 0.70. Categorical values use default dark text. Lay out signals using `st.columns` with one HTML card per column.

#### 3. Reasoning Section

After the signals bar, display the match reasoning/explanation if available.

**Auto-detect reasoning column:** During column classification, identify a reasoning column using these heuristics (case-insensitive):
- Column names containing: `reason`, `reasoning`, `explanation`, `rationale`, `justification`, `why`, `notes`, `detail`, `summary`, `narrative`, `logic`
- Prefer longer VARCHAR/TEXT columns over short ones

Classify detected reasoning columns into a new **Reasoning columns** group alongside the existing Source/Resolved/Signal/Review groups.

If a reasoning column is found, display it in a styled container:

```html
<div class="reasoning-card">
  <span class="reasoning-label">Match Reasoning</span>
  <p class="reasoning-text">High confidence fuzzy match. Name similarity 0.86 via Jaro-Winkler. Address normalized to identical street. ZIP exact match.</p>
</div>
```

```css
.reasoning-card {
    background: #FAFAFA;
    border-left: 3px solid #1976D2;
    border-radius: 4px;
    padding: 10px 16px;
    margin: 8px 0;
}
.reasoning-label {
    font-size: 11px;
    font-weight: 500;
    color: #757575;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    display: block;
    margin-bottom: 4px;
}
.reasoning-text {
    font-size: 13px;
    color: #424242;
    line-height: 1.5;
    margin: 0;
}
```

**IMPORTANT:** Always display the reasoning section. If no reasoning column is detected in the source table, generate reasoning dynamically by summarizing the signal column values (e.g., "Name similarity: 0.86 (Jaro-Winkler), Address: exact match, ZIP: exact match, Overall confidence: 0.87"). If a reasoning column exists but the value is NULL or empty for the current record, also fall back to the generated summary from signals. Never skip or hide this section.

#### 3b. Additional Context (Expander)

If there are columns classified as "Other" (unclassified columns that are not PK, source, resolved, signal, reasoning, or review), display them inside a collapsed `st.expander` labeled "Additional Context". This keeps the UI clean while making all remaining data accessible.

```python
other_cols = [c for c in all_columns if c not in used_columns]
if other_cols:
    with st.expander("Additional Context"):
        for col in other_cols:
            val = current_row[col]
            if val is not None and str(val).strip():
                st.markdown(f"**{col}:** {val}")
```

The expander is collapsed by default. Only show columns that have non-null, non-empty values for the current record.

#### 4. Action Panel

Below the comparison, a panel with the comment input, decision buttons, and navigation buttons. There are exactly 5 buttons: Accept, Reject, Flag, Previous, Next. Do NOT add a Skip button or any other buttons.

**IMPORTANT: Use this exact implementation.** Do not deviate from this pattern -- it is the only reliable way to apply per-button colors in Streamlit.

```python
comment = st.text_input("Optional comment", key=f"comment_{current_pk}")

# --- Decision buttons row ---
dec_cols = st.columns(3)

with dec_cols[0]:
    st.markdown("""<style>
        div[data-testid="stColumn"]:nth-child(1) .stButton > button {
            background-color: #A5D6A7 !important;
            color: #1B5E20 !important;
            border: none !important;
            width: 100%;
        }
    </style>""", unsafe_allow_html=True)
    accept_clicked = st.button("ACCEPT (Space)", key="accept_btn", use_container_width=True)

with dec_cols[1]:
    st.markdown("""<style>
        div[data-testid="stColumn"]:nth-child(2) .stButton > button {
            background-color: #EF9A9A !important;
            color: #B71C1C !important;
            border: none !important;
            width: 100%;
        }
    </style>""", unsafe_allow_html=True)
    reject_clicked = st.button("REJECT (X)", key="reject_btn", use_container_width=True)

with dec_cols[2]:
    st.markdown("""<style>
        div[data-testid="stColumn"]:nth-child(3) .stButton > button {
            background-color: #FFCC80 !important;
            color: #E65100 !important;
            border: none !important;
            width: 100%;
        }
    </style>""", unsafe_allow_html=True)
    flag_clicked = st.button("FLAG (F)", key="flag_btn", use_container_width=True)

# --- Navigation buttons row ---
nav_cols = st.columns([1, 2, 1])

with nav_cols[0]:
    st.markdown("""<style>
        div[data-testid="stColumn"]:nth-child(1) .stButton > button {
            background-color: #90CAF9 !important;
            color: #0D47A1 !important;
            border: none !important;
        }
    </style>""", unsafe_allow_html=True)
    prev_clicked = st.button("<< PREVIOUS (P)", key="prev_btn")

with nav_cols[2]:
    st.markdown("""<style>
        div[data-testid="stColumn"]:nth-child(3) .stButton > button {
            background-color: #90CAF9 !important;
            color: #0D47A1 !important;
            border: none !important;
        }
    </style>""", unsafe_allow_html=True)
    next_clicked = st.button("NEXT (N) >>", key="next_btn")

# --- Handle button actions ---
if accept_clicked:
    submit_decision('accept', comment)
if reject_clicked:
    submit_decision('reject', comment)
if flag_clicked:
    submit_decision('flag', comment)
if prev_clicked:
    go_previous()
if next_clicked:
    go_next()
```

On any decision button click (accept/reject/flag):
1. MERGE the decision into `REVIEW_DECISIONS` table
2. Advance to the next unreviewed record
3. Clear the comment field

**CRITICAL:** The CSS selectors above use `nth-child` scoped to each `st.columns` row. Because each row is a separate `st.columns` call, the nth-child numbering resets for each row. This is why the nav buttons also use nth-child(1) and nth-child(3) -- they are in a different `st.columns` context.

**Keyboard shortcuts:**
Keyboard shortcuts are implemented via a hidden `st.components.v1.html` component placed at the bottom of the page. It listens for `keydown` events on the parent document and programmatically clicks the matching button by searching for the button key text.

Bind these keys (only when no text input/textarea is focused):
- **Space** -- Accept
- **X** -- Reject
- **F** -- Flag for Review
- **N** -- Next record
- **P** -- Previous record

```python
import streamlit.components.v1 as components

components.html("""
<script>
const doc = window.parent.document;
doc.addEventListener('keydown', function(e) {
    const active = doc.activeElement;
    const isInput = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA');
    if (isInput) return;

    const keyMap = {
        ' ': 'accept',
        'x': 'reject',
        'f': 'flag',
        'n': 'next',
        'p': 'previous'
    };
    const target = keyMap[e.key.toLowerCase()];
    if (target) {
        e.preventDefault();
        const allButtons = doc.querySelectorAll('button');
        for (const btn of allButtons) {
            if (btn.innerText.toLowerCase().includes(target)) {
                btn.click();
                break;
            }
        }
    }
});
</script>
""", height=0)
```

This works in SiS because `st.components.v1.html` is supported. The JS matches buttons by their label text (e.g., "ACCEPT (Space)" contains "accept"), so button labels must include the keywords shown above.

#### 5. Progress Bar

A thin progress bar at the bottom: `reviewed / total` records.

#### 6. Keyboard Shortcut Hint

At the very bottom, display a subtle hint bar:

```html
<div class="shortcut-hint">
  Space: Accept | X: Reject | F: Flag | N: Next | P: Previous
</div>
```

```css
.shortcut-hint {
    text-align: center;
    font-size: 11px;
    color: #9E9E9E;
    padding: 8px 0;
    letter-spacing: 0.3px;
}
```

## Material Design Styling

Use custom CSS injected via `st.markdown(unsafe_allow_html=True)` to achieve Material Design appearance. Do NOT use third-party MUI libraries.

### Required CSS

```css
/* Remove default Streamlit top padding */
.stApp > header { display: none; }
div[data-testid="stAppViewContainer"] > div:first-child { padding-top: 0; }
.block-container { padding-top: 1rem !important; }
.stApp { margin-top: -2rem; }

/* Material Design elevation and typography */
.stApp {
    font-family: 'Roboto', 'Helvetica Neue', Arial, sans-serif;
}

/* Card elevation */
div[data-testid="stVerticalBlock"] > div[data-testid="stHorizontalBlock"] {
    background: white;
    border-radius: 8px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.24);
    padding: 16px;
    margin-bottom: 12px;
}

/* Material chip styles */
.chip-accept {
    background-color: #C8E6C9;
    color: #2E7D32;
    padding: 4px 12px;
    border-radius: 16px;
    font-size: 13px;
    font-weight: 500;
    display: inline-block;
}
.chip-reject {
    background-color: #FFCDD2;
    color: #C62828;
    padding: 4px 12px;
    border-radius: 16px;
    font-size: 13px;
    font-weight: 500;
    display: inline-block;
}
.chip-flag {
    background-color: #FFE0B2;
    color: #E65100;
    padding: 4px 12px;
    border-radius: 16px;
    font-size: 13px;
    font-weight: 500;
    display: inline-block;
}
.chip-exact {
    background-color: #C8E6C9;
    color: #2E7D32;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}
.chip-similar {
    background-color: #FFE0B2;
    color: #E65100;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}
.chip-different {
    background-color: #FFCDD2;
    color: #C62828;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

/* Signal card styles (uniform small font) */
.signal-card {
    display: inline-block;
    background: #F5F5F5;
    border-radius: 8px;
    padding: 6px 14px;
    margin-right: 8px;
    text-align: center;
}
.signal-label {
    display: block;
    font-size: 11px;
    font-weight: 500;
    color: #757575;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.signal-value {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: #212121;
}

/* Reasoning card */
.reasoning-card {
    background: #FAFAFA;
    border-left: 3px solid #1976D2;
    border-radius: 4px;
    padding: 10px 16px;
    margin: 8px 0;
}
.reasoning-label {
    font-size: 11px;
    font-weight: 500;
    color: #757575;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    display: block;
    margin-bottom: 4px;
}
.reasoning-text {
    font-size: 13px;
    color: #424242;
    line-height: 1.5;
    margin: 0;
}

/* Shortcut hint */
.shortcut-hint {
    text-align: center;
    font-size: 11px;
    color: #9E9E9E;
    padding: 8px 0;
    letter-spacing: 0.3px;
}

/* Material button overrides */
.stButton > button {
    border-radius: 4px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-size: 13px;
    padding: 8px 24px;
    transition: box-shadow 0.2s;
}
.stButton > button:hover {
    box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
```

### Button Styling

Button colors are applied inline within the Action Panel code above (Section 4). Do NOT use `type="primary"` for any button. Do NOT add a Skip button. The exact 5 buttons are: Accept, Reject, Flag, Previous, Next.

Color reference:
- **Accept**: `#A5D6A7` background, `#1B5E20` text (pastel green)
- **Reject**: `#EF9A9A` background, `#B71C1C` text (pastel red)
- **Flag**: `#FFCC80` background, `#E65100` text (pastel orange)
- **Previous / Next**: `#90CAF9` background, `#0D47A1` text (blue)

## State Management

Use `st.session_state` to track:
- `current_index` -- index into the list of unreviewed primary keys
- `reviewed_pks` -- set of primary keys already reviewed this session
- `stats` -- dict with accept/reject/flag counts

On app load:
1. Query the source table for all primary keys
2. LEFT JOIN to `REVIEW_DECISIONS` to identify already-reviewed records
3. Default to showing the first unreviewed record

## Data Access Pattern

```python
# Load unreviewed records
# NOTE: source_table, schema, and pk_column are table/column identifiers derived
# from app configuration (not user input). Snowflake bind parameters (?) only work
# for literal values, not object identifiers, so f-string interpolation is correct here.
unreviewed = session.sql(f"""
    SELECT s.*
    FROM {source_table} s
    LEFT JOIN {schema}.REVIEW_DECISIONS r
        ON r.PRIMARY_KEY_VALUE = s.{pk_column}::VARCHAR
        AND r.SOURCE_TABLE = '{source_table}'
    WHERE r.PRIMARY_KEY_VALUE IS NULL
    ORDER BY s.{pk_column}
""").to_pandas()

# Submit a decision (use parameterized queries to prevent SQL injection)
session.sql(
    f"""
    MERGE INTO {schema}.REVIEW_DECISIONS tgt
    USING (SELECT
        ? AS PRIMARY_KEY_VALUE,
        ? AS SOURCE_TABLE,
        ? AS REVIEWER_DECISION,
        ? AS REVIEWER_COMMENT,
        CURRENT_USER() AS REVIEWED_BY,
        CURRENT_TIMESTAMP() AS REVIEWED_AT
    ) src
    ON tgt.PRIMARY_KEY_VALUE = src.PRIMARY_KEY_VALUE
       AND tgt.SOURCE_TABLE = src.SOURCE_TABLE
    WHEN MATCHED THEN UPDATE SET
        REVIEWER_DECISION = src.REVIEWER_DECISION,
        REVIEWER_COMMENT = src.REVIEWER_COMMENT,
        REVIEWED_BY = src.REVIEWED_BY,
        REVIEWED_AT = src.REVIEWED_AT
    WHEN NOT MATCHED THEN INSERT VALUES (
        src.PRIMARY_KEY_VALUE, src.SOURCE_TABLE,
        src.REVIEWER_DECISION, src.REVIEWER_COMMENT,
        src.REVIEWED_BY, src.REVIEWED_AT
    )
    """,
    params=[pk_value, source_table, decision, comment or ""],
).collect()
```

## Constraints

- No emojis anywhere in the UI. Use text labels and Material Design icons via CSS/HTML only.
- No third-party MUI Python libraries. Use native Streamlit components with custom CSS.
- The app must work with ANY table structure. Column names come from user input at skill invocation time.
- The app should be deployable to Snowflake via Streamlit in Snowflake (SiS).
- Use `FROM` syntax (not `ROOT_LOCATION`) when creating the Streamlit object.

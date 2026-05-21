"""
Entity Resolution HITL Review App
Joins MATCH_RESULTS with NORMALIZED_ENTITIES for side-by-side comparison.
Writes decisions to REVIEW_DECISIONS via MERGE.
Deployed to Snowflake Streamlit in Snowflake (SiS).
"""
import streamlit as st
import streamlit.components.v1 as components
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Entity Resolution Review", layout="wide")

session = get_active_session()

DB_SCHEMA = "ER_SKILL_TEST_DB1.NPI"
MATCH_TABLE = f"{DB_SCHEMA}.MATCH_RESULTS"
NORM_TABLE = f"{DB_SCHEMA}.NORMALIZED_ENTITIES"
DECISIONS_TABLE = f"{DB_SCHEMA}.REVIEW_DECISIONS"
SOURCE_TABLE_NAME = MATCH_TABLE

# ── Material Design CSS ──────────────────────────────────────────────────────

st.markdown("""<style>
/* Remove default Streamlit top padding */
.stApp > header { display: none; }
div[data-testid="stAppViewContainer"] > div:first-child { padding-top: 0; }
.block-container { padding-top: 1rem !important; }
.stApp { margin-top: -2rem; }

/* Material Design typography */
.stApp {
    font-family: 'Roboto', 'Helvetica Neue', Arial, sans-serif;
}

/* Material chip styles */
.chip-accept {
    background-color: #C8E6C9; color: #2E7D32;
    padding: 4px 12px; border-radius: 16px; font-size: 13px; font-weight: 500; display: inline-block;
}
.chip-reject {
    background-color: #FFCDD2; color: #C62828;
    padding: 4px 12px; border-radius: 16px; font-size: 13px; font-weight: 500; display: inline-block;
}
.chip-flag {
    background-color: #FFE0B2; color: #E65100;
    padding: 4px 12px; border-radius: 16px; font-size: 13px; font-weight: 500; display: inline-block;
}
.chip-exact {
    background-color: #C8E6C9; color: #2E7D32;
    padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500;
}
.chip-similar {
    background-color: #FFE0B2; color: #E65100;
    padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500;
}
.chip-different {
    background-color: #FFCDD2; color: #C62828;
    padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500;
}

/* Signal card styles (uniform small font) */
.signal-card {
    display: inline-block; background: #F5F5F5; border-radius: 8px;
    padding: 6px 14px; margin-right: 8px; text-align: center;
}
.signal-label {
    display: block; font-size: 11px; font-weight: 500; color: #757575;
    text-transform: uppercase; letter-spacing: 0.5px;
}
.signal-value {
    display: block; font-size: 13px; font-weight: 600; color: #212121;
}
.signal-value-green { display: block; font-size: 13px; font-weight: 600; color: #2E7D32; }
.signal-value-amber { display: block; font-size: 13px; font-weight: 600; color: #E65100; }
.signal-value-red { display: block; font-size: 13px; font-weight: 600; color: #C62828; }

/* Reasoning card */
.reasoning-card {
    background: #FAFAFA; border-left: 3px solid #1976D2; border-radius: 4px;
    padding: 10px 16px; margin: 8px 0;
}
.reasoning-label {
    font-size: 11px; font-weight: 500; color: #757575;
    text-transform: uppercase; letter-spacing: 0.5px; display: block; margin-bottom: 4px;
}
.reasoning-text {
    font-size: 13px; color: #424242; line-height: 1.5; margin: 0;
}

/* Shortcut hint */
.shortcut-hint {
    text-align: center; font-size: 11px; color: #9E9E9E; padding: 8px 0; letter-spacing: 0.3px;
}

/* Material button overrides */
.stButton > button {
    border-radius: 4px; font-weight: 500; text-transform: uppercase;
    letter-spacing: 0.5px; font-size: 13px; padding: 8px 24px; transition: box-shadow 0.2s;
}
.stButton > button:hover { box-shadow: 0 2px 4px rgba(0,0,0,0.2); }

/* Field comparison table */
.cmp-table { width: 100%; border-collapse: collapse; margin: 4px 0; }
.cmp-table th {
    text-align: left; font-size: 12px; font-weight: 500; color: #757575;
    text-transform: uppercase; letter-spacing: 0.5px; padding: 6px 12px;
    border-bottom: 2px solid #E0E0E0;
}
.cmp-table td {
    padding: 8px 12px; font-size: 14px; color: #212121; border-bottom: 1px solid #EEEEEE;
}
.cmp-table td.field-label { font-weight: 500; color: #616161; font-size: 13px; width: 120px; }
</style>""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def safe(val):
    """Convert a value to display string, handling None/NaN."""
    if val is None:
        return ""
    try:
        import math
        if math.isnan(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def signal_color_class(val):
    """Return CSS class for a numeric signal value."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "signal-value"
    if v >= 0.85:
        return "signal-value-green"
    if v >= 0.70:
        return "signal-value-amber"
    return "signal-value-red"


def match_chip(src_val, res_val, jw_score=None):
    """Return HTML chip for field-level match indicator."""
    s, r = safe(src_val).upper(), safe(res_val).upper()
    if not s or not r:
        return ""
    if s == r:
        return '<span class="chip-exact">Exact</span>'
    if jw_score is not None:
        try:
            if float(jw_score) >= 0.80:
                return '<span class="chip-similar">Similar</span>'
        except (TypeError, ValueError):
            pass
    return '<span class="chip-different">Different</span>'


# ── Data Loading ─────────────────────────────────────────────────────────────

REVIEW_QUERY = f"""
SELECT
    m.ID_LEFT,
    m.ID_RIGHT,
    m.DECISION,
    m.CONFIDENCE,
    m.MATCH_METHOD,
    m.COSINE_SIM,
    m.NAME_JW,
    m.STREET_JW,
    m.MATCHED_ON,
    nl.RAW_NAME        AS SRC_RAW_NAME,
    nl.RAW_ADDRESS     AS SRC_RAW_ADDRESS,
    nl.NORMALIZED_NAME AS SRC_NORM_NAME,
    nl.NORMALIZED_STREET AS SRC_NORM_STREET,
    nl.NORMALIZED_CITY AS SRC_NORM_CITY,
    nl.NORMALIZED_STATE AS SRC_NORM_STATE,
    nl.NORMALIZED_ZIP  AS SRC_NORM_ZIP,
    nl.ORIGINAL_NPI    AS SRC_ORIGINAL_NPI,
    nr.RAW_NAME        AS RES_RAW_NAME,
    nr.RAW_ADDRESS     AS RES_RAW_ADDRESS,
    nr.NORMALIZED_NAME AS RES_NORM_NAME,
    nr.NORMALIZED_STREET AS RES_NORM_STREET,
    nr.NORMALIZED_CITY AS RES_NORM_CITY,
    nr.NORMALIZED_STATE AS RES_NORM_STATE,
    nr.NORMALIZED_ZIP  AS RES_NORM_ZIP,
    nr.ORIGINAL_NPI    AS RES_ORIGINAL_NPI
FROM {MATCH_TABLE} m
JOIN {NORM_TABLE} nl ON nl.SOURCE_ID = m.ID_LEFT
JOIN {NORM_TABLE} nr ON nr.SOURCE_ID = m.ID_RIGHT
LEFT JOIN {DECISIONS_TABLE} rd
    ON rd.ID_LEFT = m.ID_LEFT AND rd.ID_RIGHT = m.ID_RIGHT
WHERE m.DECISION = 'match'
"""


@st.cache_data(ttl=30)
def load_stats():
    """Count total matches and review status."""
    df = session.sql(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN rd.ID_LEFT IS NULL THEN 1 ELSE 0 END) AS unreviewed,
            SUM(CASE WHEN rd.REVIEWER_DECISION = 'accept' THEN 1 ELSE 0 END) AS accepted,
            SUM(CASE WHEN rd.REVIEWER_DECISION = 'reject' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN rd.REVIEWER_DECISION = 'flag' THEN 1 ELSE 0 END) AS flagged
        FROM {MATCH_TABLE} m
        LEFT JOIN {DECISIONS_TABLE} rd
            ON rd.ID_LEFT = m.ID_LEFT AND rd.ID_RIGHT = m.ID_RIGHT
        WHERE m.DECISION = 'match'
    """).to_pandas()
    return df.iloc[0]


@st.cache_data(ttl=10)
def load_unreviewed():
    """Load unreviewed match records with entity details."""
    return session.sql(
        REVIEW_QUERY + "\n    AND rd.ID_LEFT IS NULL\nORDER BY m.ID_LEFT"
    ).to_pandas()


def save_decision(id_left, id_right, decision, comment):
    """MERGE a review decision using parameterized queries to prevent SQL injection."""
    session.sql(
        f"""
        MERGE INTO {DECISIONS_TABLE} tgt
        USING (SELECT
            ? AS ID_LEFT,
            ? AS ID_RIGHT,
            ? AS SOURCE_TABLE,
            ? AS REVIEWER_DECISION,
            ? AS REVIEWER_COMMENT,
            CURRENT_USER() AS REVIEWED_BY,
            CURRENT_TIMESTAMP() AS REVIEWED_AT
        ) src
        ON tgt.ID_LEFT = src.ID_LEFT AND tgt.ID_RIGHT = src.ID_RIGHT
        WHEN MATCHED THEN UPDATE SET
            REVIEWER_DECISION = src.REVIEWER_DECISION,
            REVIEWER_COMMENT = src.REVIEWER_COMMENT,
            REVIEWED_BY = src.REVIEWED_BY,
            REVIEWED_AT = src.REVIEWED_AT
        WHEN NOT MATCHED THEN INSERT (ID_LEFT, ID_RIGHT, SOURCE_TABLE, REVIEWER_DECISION, REVIEWER_COMMENT, REVIEWED_BY, REVIEWED_AT)
            VALUES (src.ID_LEFT, src.ID_RIGHT, src.SOURCE_TABLE, src.REVIEWER_DECISION, src.REVIEWER_COMMENT, src.REVIEWED_BY, src.REVIEWED_AT)
        """,
        params=[id_left, id_right, SOURCE_TABLE_NAME, decision, comment or ""],
    ).collect()
    load_stats.clear()
    load_unreviewed.clear()


# ── Field Pairing ────────────────────────────────────────────────────────────

# (label, source_col, resolved_col, jw_score_col_or_None)
FIELD_PAIRS = [
    ("Name",       "SRC_RAW_NAME",    "RES_RAW_NAME",    "NAME_JW"),
    ("Norm. Name", "SRC_NORM_NAME",   "RES_NORM_NAME",   "NAME_JW"),
    ("Address",    "SRC_RAW_ADDRESS",  "RES_RAW_ADDRESS",  "STREET_JW"),
    ("Norm. Street","SRC_NORM_STREET", "RES_NORM_STREET", "STREET_JW"),
    ("City",       "SRC_NORM_CITY",   "RES_NORM_CITY",   None),
    ("State",      "SRC_NORM_STATE",  "RES_NORM_STATE",  None),
    ("ZIP",        "SRC_NORM_ZIP",    "RES_NORM_ZIP",    None),
    ("NPI",        "SRC_ORIGINAL_NPI","RES_ORIGINAL_NPI",None),
]


# ── Session State ────────────────────────────────────────────────────────────

if "current_index" not in st.session_state:
    st.session_state.current_index = 0


# ── Load Data ────────────────────────────────────────────────────────────────

stats = load_stats()
df = load_unreviewed()

total = int(stats["TOTAL"])
unreviewed_count = len(df)
accepted_count = int(stats["ACCEPTED"])
rejected_count = int(stats["REJECTED"])
flagged_count = int(stats["FLAGGED"])
reviewed_count = total - unreviewed_count

# Clamp index
idx = st.session_state.current_index
if idx >= unreviewed_count:
    idx = 0
    st.session_state.current_index = 0


# ── 1. Compact Header ───────────────────────────────────────────────────────

h1, h2, h3 = st.columns([2, 3, 4])
with h1:
    st.markdown("**Entity Resolution Review**")
with h2:
    if unreviewed_count > 0:
        st.markdown(f"Record {idx + 1} of {unreviewed_count} unreviewed")
    else:
        st.markdown("All records reviewed")
with h3:
    st.markdown(
        f'<span class="chip-accept">{accepted_count} Accepted</span> '
        f'<span class="chip-reject">{rejected_count} Rejected</span> '
        f'<span class="chip-flag">{flagged_count} Flagged</span>',
        unsafe_allow_html=True,
    )

# ── Empty State ──────────────────────────────────────────────────────────────

if unreviewed_count == 0:
    st.info(
        f"All {total} match records have been reviewed. "
        f"({accepted_count} accepted, {rejected_count} rejected, {flagged_count} flagged)"
    )
    if total > 0:
        st.progress(1.0, text=f"{total} of {total} reviewed (100%)")
    st.stop()

row = df.iloc[idx]


# ── 2. Side-by-Side Entity Comparison ────────────────────────────────────────

table_html = '<table class="cmp-table"><thead><tr>'
table_html += '<th>Field</th><th>Source Entity</th><th>Resolved Entity</th><th>Match</th>'
table_html += '</tr></thead><tbody>'

for label, src_col, res_col, jw_col in FIELD_PAIRS:
    sv = safe(row.get(src_col))
    rv = safe(row.get(res_col))
    jw_val = row.get(jw_col) if jw_col else None
    chip = match_chip(sv, rv, jw_val)
    table_html += (
        f'<tr><td class="field-label">{label}</td>'
        f'<td>{sv or "--"}</td><td>{rv or "--"}</td><td>{chip}</td></tr>'
    )

table_html += '</tbody></table>'
st.markdown(table_html, unsafe_allow_html=True)


# ── 3. Signals Bar ──────────────────────────────────────────────────────────

signals = [
    ("Confidence", row.get("CONFIDENCE"), True),
    ("Cosine Sim", row.get("COSINE_SIM"), True),
    ("Name JW", row.get("NAME_JW"), True),
    ("Street JW", row.get("STREET_JW"), True),
    ("Method", row.get("MATCH_METHOD"), False),
    ("Decision", row.get("DECISION"), False),
]

sig_cols = st.columns(len(signals))
for i, (label, val, is_numeric) in enumerate(signals):
    with sig_cols[i]:
        if is_numeric and val is not None:
            try:
                display_val = f"{float(val):.4f}"
                css_cls = signal_color_class(val)
            except (TypeError, ValueError):
                display_val = safe(val)
                css_cls = "signal-value"
        else:
            display_val = safe(val) if val is not None else "N/A"
            css_cls = "signal-value"
        st.markdown(
            f'<div class="signal-card"><span class="signal-label">{label}</span>'
            f'<span class="{css_cls}">{display_val}</span></div>',
            unsafe_allow_html=True,
        )


# ── 4. Reasoning Section ────────────────────────────────────────────────────

# Generate reasoning from signals since there's no dedicated reasoning column
reasoning_parts = []
name_jw = row.get("NAME_JW")
street_jw = row.get("STREET_JW")
cosine = row.get("COSINE_SIM")
conf = row.get("CONFIDENCE")
method = safe(row.get("MATCH_METHOD"))

if name_jw is not None:
    try:
        njw = float(name_jw)
        if njw >= 1.0:
            reasoning_parts.append("Name: exact match")
        elif njw >= 0.90:
            reasoning_parts.append(f"Name similarity: {njw:.2f} (Jaro-Winkler, high)")
        elif njw >= 0.80:
            reasoning_parts.append(f"Name similarity: {njw:.2f} (Jaro-Winkler, moderate)")
        else:
            reasoning_parts.append(f"Name similarity: {njw:.2f} (Jaro-Winkler, low)")
    except (TypeError, ValueError):
        pass

if street_jw is not None:
    try:
        sjw = float(street_jw)
        if sjw >= 1.0:
            reasoning_parts.append("Street: exact match")
        elif sjw >= 0.90:
            reasoning_parts.append(f"Street similarity: {sjw:.2f} (Jaro-Winkler, high)")
        elif sjw >= 0.80:
            reasoning_parts.append(f"Street similarity: {sjw:.2f} (Jaro-Winkler, moderate)")
        else:
            reasoning_parts.append(f"Street similarity: {sjw:.2f} (Jaro-Winkler, low)")
    except (TypeError, ValueError):
        pass

# City/State/ZIP exact checks
for label, sc, rc in [("City", "SRC_NORM_CITY", "RES_NORM_CITY"),
                       ("State", "SRC_NORM_STATE", "RES_NORM_STATE"),
                       ("ZIP", "SRC_NORM_ZIP", "RES_NORM_ZIP")]:
    sv = safe(row.get(sc)).upper()
    rv = safe(row.get(rc)).upper()
    if sv and rv:
        if sv == rv:
            reasoning_parts.append(f"{label}: exact match")
        else:
            reasoning_parts.append(f"{label}: mismatch ({safe(row.get(sc))} vs {safe(row.get(rc))})")

if cosine is not None:
    try:
        reasoning_parts.append(f"Overall cosine similarity: {float(cosine):.4f}")
    except (TypeError, ValueError):
        pass

if method:
    reasoning_parts.append(f"Match method: {method}")

reasoning_text = ". ".join(reasoning_parts) + "." if reasoning_parts else "No signal data available."

st.markdown(
    f'<div class="reasoning-card">'
    f'<span class="reasoning-label">Match Reasoning</span>'
    f'<p class="reasoning-text">{reasoning_text}</p>'
    f'</div>',
    unsafe_allow_html=True,
)


# ── 5. Action Panel ─────────────────────────────────────────────────────────

current_pk_left = safe(row.get("ID_LEFT"))
current_pk_right = safe(row.get("ID_RIGHT"))

comment = st.text_input("Optional comment", key=f"comment_{current_pk_left}_{current_pk_right}")

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
def _submit(decision):
    save_decision(current_pk_left, current_pk_right, decision, comment)
    # Stay at same index since the list shifts after removing the reviewed record


def _go_previous():
    st.session_state.current_index = max(0, st.session_state.current_index - 1)


def _go_next():
    st.session_state.current_index = min(unreviewed_count - 1, st.session_state.current_index + 1)


_rerun = st.rerun if hasattr(st, "rerun") else st.experimental_rerun

if accept_clicked:
    _submit("accept")
    _rerun()
if reject_clicked:
    _submit("reject")
    _rerun()
if flag_clicked:
    _submit("flag")
    _rerun()
if prev_clicked:
    _go_previous()
    _rerun()
if next_clicked:
    _go_next()
    _rerun()


# ── 6. Progress Bar ─────────────────────────────────────────────────────────

if total > 0:
    progress = reviewed_count / total
    st.progress(progress, text=f"{reviewed_count} of {total} reviewed ({progress:.0%})")


# ── 7. Keyboard Shortcut Hint ───────────────────────────────────────────────

st.markdown(
    '<div class="shortcut-hint">Space: Accept | X: Reject | F: Flag | N: Next | P: Previous</div>',
    unsafe_allow_html=True,
)

# ── Keyboard Shortcuts (hidden component) ────────────────────────────────────

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

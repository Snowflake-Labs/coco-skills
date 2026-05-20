# Agent Definition — Cortex Agent for Entity Resolution

Template for defining a Cortex Agent that resolves entities using multi-tool search with reasoning. Used in Tier 2 of the agentic matching workflow (see `agentic-matching.md`).

## Agent Specification

**Delegate to:** `cortex-agent` skill to create the agent. Provide this specification as the ER-specific configuration.

```sql
CREATE OR REPLACE AGENT <schema>.entity_resolution_agent
    COMMENT = 'Matches source entities to reference records via search, SQL, and web search'
    FROM SPECIFICATION
$$
models:
  orchestration: claude-haiku-4-5

orchestration:
  budget:
    seconds: 90
    tokens: 16000

instructions:
  system: |
    Match source entity records to reference records.

    <DOMAIN_ABBREVIATION_MAP>

    RULES:
    - Same name + same address = match
    - Same name + different address (minor formatting differences OK) = probable_match
    - Different name + same address = investigate (check DBA names)
    - Different suite/unit numbers at same street address = DIFFERENT entities
    - <DOMAIN_SPECIFIC_RULES>

    DECISIONS (use exactly one):
    - "match": High confidence the entity matches this reference record.
    - "probable_match": Likely match but some ambiguity.
    - "no_match": Exhausted all search strategies, no match found.
    - "investigate": Ambiguous, requires human review.
    - "location_closed": Entity identified but confirmed permanently closed.
    - "new_record_needed": Entity confirmed active (via web search) but missing from reference corpus.

  orchestration: |
    BUDGET: Make a decision within 6 tool calls total. Do NOT keep searching
    after 6 calls — use the best evidence and decide.

    TOOL PRIORITY ORDER:
    1. Corpus_Search (PRIMARY): Search by expanded name + geographic filter.
       If a clear match appears, decide immediately.
       Maximum 3 Corpus_Search calls per entity.
    2. Corpus_Query (FALLBACK): SQL query if search returned zero or ambiguous results.
       Maximum 1 Corpus_Query call per entity.
    3. Web_Search (LAST RESORT): Only after BOTH internal tools failed.
       Maximum 1 Web_Search call per entity.
       Useful for: confirming closures, finding rebrands/acquisitions, verifying existence.

    GEOGRAPHIC FILTERING:
    Always include a state filter when using Corpus_Search if the entity has a known state.
    Use: {"filter": {"@eq": {"state": "<entity_state>"}}}
    If state is unknown or the filtered search returns zero results, retry WITHOUT
    the state filter — but evaluate cross-state results with extra skepticism.
    Same-state matches are strongly preferred over cross-state matches.

    WHEN USING WEB SEARCH:
    Populate "web_sources" with URLs, titles, and snippets consulted.
    If web search confirms the entity exists, populate "web_validated_entity".

  response: |
    Return ONLY JSON:
    {
      "decision": "match|probable_match|no_match|investigate|location_closed|new_record_needed",
      "matched_reference_id": "ID or null",
      "matched_name": "name or null",
      "matched_address": "address or null",
      "matched_city": "city or null",
      "matched_state": "state or null",
      "matched_zip": "ZIP or null",
      "confidence": 0.0-1.0,
      "reasoning": "brief explanation",
      "web_search_used": true/false,
      "web_sources": [{"url":"...","title":"...","snippet":"..."}],
      "web_validated_entity": {"name":"...","address":"...","city":"...","state":"...","zip":"...","source_type":"...","validation_confidence":0.0-1.0},
      "discovered_name": "name confirmed via web or null",
      "discovered_address": "address or null",
      "discovered_city": "city or null",
      "discovered_zip": "ZIP or null",
      "discovery_reasoning": "why this entity is missing from reference or null"
    }

tools:
  - tool_spec:
      type: "cortex_search"
      name: "Corpus_Search"
      description: "Fuzzy search reference entity records. Search by name, address, city, keywords. PRIMARY tool — always try first."

  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Corpus_Query"
      description: "SQL queries on reference entity table. FALLBACK — use only when Corpus_Search returns no results."

  - tool_spec:
      type: "web_search"
      name: "Web_Search"
      description: "Search the public web. LAST RESORT — use only after both internal tools fail."

tool_resources:
  Corpus_Search:
    name: "<database>.<schema>.reference_search_svc"
    max_results: "5"
    id_column: "ENTITY_ID"
    columns_and_descriptions:
      SEARCH_TEXT:
        description: "Name+address combined text"
        type: "string"
        searchable: true
        filterable: false
      ENTITY_NAME:
        description: "Entity name, UPPERCASE"
        type: "string"
        searchable: true
        filterable: false
      DBA_NAME:
        description: "DBA / trade name if any"
        type: "string"
        searchable: true
        filterable: false
      CITY:
        description: "City, UPPERCASE"
        type: "string"
        searchable: false
        filterable: true
      ZIP5:
        description: "5-digit ZIP"
        type: "string"
        searchable: false
        filterable: true
      ENTITY_ID:
        description: "Unique reference entity identifier"
        type: "string"
        searchable: false
        filterable: true

  Corpus_Query:
    semantic_model_file: "@<database>.<schema>.<stage>/semantic_model.yaml"
    execution_environment:
      type: "warehouse"
      warehouse: "<warehouse_name>"
$$;
```

## Placeholders to Replace

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `<schema>` | Fully qualified schema | `MY_DB.ER_SCHEMA` |
| `<database>.<schema>.reference_search_svc` | Cortex Search Service (from `search-service.md`) | `MY_DB.ER_SCHEMA.REFERENCE_SEARCH_SVC` |
| `<database>.<schema>.<stage>` | Stage containing the semantic model YAML | `MY_DB.ER_SCHEMA.ER_ASSETS` |
| `<warehouse_name>` | Warehouse for SQL execution | `ER_WH` |
| `<DOMAIN_ABBREVIATION_MAP>` | Domain-specific abbreviation mappings from the loaded profile | See below |
| `<DOMAIN_SPECIFIC_RULES>` | Domain-specific matching rules from the loaded profile | See below |

## Domain-Specific Prompt Sections

### Abbreviation Map

Build from the loaded domain profile's normalization rules. Example for healthcare/pharma:

```
ABBREVIATION MAP:
Medical: HSP/HOSP=Hospital, PHCY/PHMY=Pharmacy, HLTH=Health, CTR=Center, MED=Medical,
  DLYS=Dialysis, INF=Infusion, ONC=Oncology, COMM=Community, REHAB=Rehabilitation
Geography: FT=Fort, ST=Saint, MT=Mount, SPGS=Springs, HTS=Heights
Chains: WALGREENS #NNNNN, CVS PHARMACY NNNN, PUBLIX PHARMACY #NNNN
Suffixes to STRIP: LLC, INC, PA, DBA, D/B/A, <domain_strip_terms>
```

### Domain-Specific Rules

Append rules from the loaded profile. Examples:

- **Healthcare:** Different Class of Trade (IP vs OP vs Retail) at the same address = DIFFERENT entities
- **Financial services:** Subsidiaries sharing a parent address may be different entities — check legal entity names
- **Retail/CPG:** Chain stores with same brand + same address but different store numbers may be the same entity (renumbering)

## Semantic Model YAML

The agent's `Corpus_Query` tool requires a semantic model YAML file describing the reference table. Create this and upload to a stage.

```yaml
name: reference_entity_model
description: Reference entity records for entity resolution queries.
tables:
  - name: <schema>.search_corpus
    description: Denormalized reference entity records.
    columns:
      - name: ENTITY_ID
        description: Unique entity identifier.
        data_type: VARCHAR
      - name: ENTITY_NAME
        description: Entity name (uppercase).
        data_type: VARCHAR
      - name: DBA_NAME
        description: DBA or trade name (may be NULL).
        data_type: VARCHAR
      - name: ADDRESS_LINE_1
        description: Street address (uppercase).
        data_type: VARCHAR
      - name: CITY
        description: City (uppercase).
        data_type: VARCHAR
      - name: STATE
        description: 2-letter state code.
        data_type: VARCHAR
      - name: ZIP5
        description: 5-digit ZIP code.
        data_type: VARCHAR
      # Add domain-specific columns:
      # - name: TAXONOMY_CODE
      #   description: Provider taxonomy code.
      #   data_type: VARCHAR
```

Upload to stage:
```bash
snow stage copy semantic_model.yaml @<database>.<schema>.<stage>/ -c <connection>
```

## Agent Invocation

Call the agent from a stored procedure using `SNOWFLAKE.CORTEX.DATA_AGENT_RUN`:

```python
import json

AGENT_NAME = '<database>.<schema>.entity_resolution_agent'

request_body = json.dumps({
    "messages": [{
        "role": "user",
        "content": [{"type": "text", "text": prompt}]
    }]
})
safe_json = request_body.replace("\\", "\\\\").replace("'", "''")

rows = session.sql(
    f"SELECT TRY_PARSE_JSON("
    f"SNOWFLAKE.CORTEX.DATA_AGENT_RUN("
    f"'{AGENT_NAME}', "
    f"'{safe_json}'"
    f")) AS result"
).collect()
```

## Response Parsing

Agent responses are wrapped in an envelope. Extract the text content:

```python
def parse_agent_response(raw):
    """Extract structured decision from agent response envelope."""
    VALID_DECISIONS = {
        'match', 'probable_match', 'no_match',
        'investigate', 'location_closed', 'new_record_needed'
    }

    default = {
        "decision": "no_match",
        "matched_reference_id": None,
        "matched_name": None,
        "matched_address": None,
        "confidence": 0.0,
        "reasoning": "",
        "web_search_used": False,
        "web_sources": [],
        "web_validated_entity": None,
    }

    # 1. Unwrap envelope → extract text block
    envelope = raw if isinstance(raw, dict) else json.loads(raw)
    text = ""
    for block in reversed(envelope.get("content", [])):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            break
    if not text:
        text = str(raw)

    # 2. Try parsing: code-fence JSON → direct JSON → first {...} block
    import re
    # Code fence
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        result = {**default, **json.loads(m.group(1))}
        if result["decision"] not in VALID_DECISIONS:
            result["decision"] = "no_match"
        return result

    # Direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "decision" in parsed:
            result = {**default, **parsed}
            if result["decision"] not in VALID_DECISIONS:
                result["decision"] = "no_match"
            return result
    except Exception:
        pass

    # First JSON object with "decision" key
    for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL):
        try:
            candidate = json.loads(m.group(0))
            if isinstance(candidate, dict) and "decision" in candidate:
                result = {**default, **candidate}
                if result["decision"] not in VALID_DECISIONS:
                    result["decision"] = "no_match"
                return result
        except Exception:
            continue

    # Fallback
    default["reasoning"] = text[:2000]
    return default
```

# Orchestration — Agent Pipeline Execution

Template for orchestrating parallel agent execution in Tier 2 of the agentic matching workflow. Covers: work queue management, prompt building, batch processing, Task DAG parallelism, state-based pipeline control, and finalization.

## Work Queue Population

After Tier 1 and Tier 1.5 resolve their entities, populate the agent work queue with remaining unresolved entities:

```sql
INSERT INTO agent_work_queue (
    source_entity_id,
    entity_name, entity_address, entity_city, entity_state, entity_zip,
    name_variants,
    match_candidates,
    domain_context
)
SELECT
    ne.source_id,
    ne.normalized_name,
    ne.normalized_street,
    ne.normalized_city,
    ne.normalized_state,
    ne.normalized_zip,
    -- Name variants (JSON array of alternative names for this entity)
    ne.name_variants,
    -- Top-N embedding match candidates as context for the agent
    mc.candidates,
    -- Domain-specific context (class of trade, entity type, etc.)
    ne.domain_context
FROM normalized_entities ne
LEFT JOIN (
    SELECT
        id_left,
        ARRAY_AGG(
            OBJECT_CONSTRUCT(
                'reference_id', id_right,
                'reference_name', reference_name,
                'cosine_sim', cosine_sim,
                'name_jw', name_jw,
                'street_jw', street_jw,
                'zip_exact', zip_exact,
                'composite_score', composite_score
            )
        ) WITHIN GROUP (ORDER BY composite_score DESC) AS candidates
    FROM match_candidates
    WHERE rn <= 5
    GROUP BY id_left
) mc ON ne.source_id = mc.id_left
WHERE ne.source_id NOT IN (SELECT source_entity_id FROM highconf_matches)
  AND ne.source_id NOT IN (SELECT source_entity_id FROM batch_classify_matches);
```

### Batch Assignment

Assign balanced batches for parallel processing. Use `NTILE(<N>)` where N is the number of worker tasks (max 98 to stay under Snowflake's 100-child-per-parent task limit):

```sql
UPDATE agent_work_queue
SET batch_id = batched.batch_id
FROM (
    SELECT queue_id, NTILE(<num_workers>) OVER (ORDER BY queue_id) AS batch_id
    FROM agent_work_queue
    WHERE status = 'PENDING'
) batched
WHERE agent_work_queue.queue_id = batched.queue_id;
```

---

## Prompt Builder

Build a per-entity prompt that provides the agent with all context needed to resolve the entity:

```python
def build_prompt(entity):
    """Build agent prompt for one source entity."""
    parts = [
        f"Resolve this entity to a reference record:",
        "",
        "**Source Entity (Primary Name):**",
        f"- Name: {entity['ENTITY_NAME']}",
        f"- Address: {entity['ENTITY_ADDRESS'] or 'N/A'}",
        f"- City: {entity['ENTITY_CITY'] or 'N/A'}",
        f"- State: {entity['ENTITY_STATE'] or 'N/A'}",
        f"- ZIP: {entity['ENTITY_ZIP'] or 'N/A'}",
    ]

    # Domain context (class of trade, entity type, etc.)
    domain_ctx = entity.get("DOMAIN_CONTEXT")
    if domain_ctx:
        # Parse and include domain-specific fields
        # e.g., "- Detected Class of Trade: Retail Pharmacy"
        pass

    # Name variants (alternative names for the same entity)
    variants = entity.get("NAME_VARIANTS")
    if variants:
        variants_list = json.loads(variants) if isinstance(variants, str) else variants
        if isinstance(variants_list, list) and len(variants_list) > 1:
            parts.append("")
            parts.append("**Name variants (try these if primary name fails):**")
            for i, v in enumerate(variants_list[:10], 1):
                name = v.get("name", str(v)) if isinstance(v, dict) else str(v)
                parts.append(f"  {i}. {name}")

    # Embedding match candidates as context
    candidates = entity.get("MATCH_CANDIDATES")
    if candidates:
        cands = json.loads(candidates) if isinstance(candidates, str) else candidates
        if cands:
            parts.append("")
            parts.append("**Previous fuzzy-matching candidates (may or may not be correct):**")
            for i, c in enumerate(cands[:5], 1):
                parts.append(
                    f"  {i}. ID={c.get('reference_id','?')} "
                    f'Name="{c.get("reference_name","?")}" '
                    f"cosine={c.get('cosine_sim',0):.3f} "
                    f"name_jw={c.get('name_jw',0):.3f} "
                    f"street_jw={c.get('street_jw',0):.3f} "
                    f"zip={c.get('zip_exact',0)}"
                )
            parts.append("Verify these and search for better matches if needed.")

    parts.extend([
        "",
        "Search strategies to try (in order):",
        "1. Corpus_Search by the expanded entity name",
        "2. Corpus_Search by address + city + ZIP",
        "3. Corpus_Query (SQL) if search returned nothing useful",
        "4. Web_Search (LAST RESORT) — only if both internal tools failed",
        "",
        "Respond with ONLY a JSON object — no markdown fences.",
    ])

    return "\n".join(parts)
```

---

## Batch Processing SP

The main stored procedure that processes one batch of entities through the agent:

```sql
CREATE OR REPLACE PROCEDURE resolve_entity_batch(BATCH_ID INT)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
EXECUTE AS CALLER
AS
$$
import json
import time
import uuid
import re

AGENT_NAME = '<database>.<schema>.entity_resolution_agent'
MAX_RETRIES = 1
BASE_RETRY_DELAY = 5


def run(session, batch_id: int) -> str:
    worker_id = str(uuid.uuid4())[:8]
    batch_id = int(batch_id)

    # Fetch entities (skip already-completed for idempotent restart)
    entities = session.sql(
        "SELECT * FROM <schema>.agent_work_queue "
        f"WHERE batch_id = {batch_id} "
        "  AND source_entity_id NOT IN ("
        "      SELECT source_entity_id FROM <schema>.agent_results "
        "      WHERE decision IS NOT NULL"
        "  ) "
        "ORDER BY queue_id"
    ).collect()

    processed = 0
    errors = 0

    for entity in entities:
        thread_id = str(uuid.uuid4())
        prompt = _build_prompt(entity)

        for attempt in range(MAX_RETRIES + 1):
            try:
                start_time = time.time()

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
                    f"'{AGENT_NAME}', '{safe_json}'"
                    f")) AS result"
                ).collect()

                elapsed = round(time.time() - start_time, 2)
                parsed = _parse_response(rows[0]["RESULT"])

                # Compute validation JW scores
                name_jw, addr_jw = _compute_validation_scores(
                    session, entity, parsed
                )

                # Write result
                _write_result(
                    session, entity, parsed, elapsed,
                    name_jw, addr_jw, thread_id, worker_id
                )

                # Record entity discovery if applicable
                _record_discovery(session, entity, parsed, worker_id)

                processed += 1
                break  # success

            except Exception as e:
                err = str(e)
                is_rate_limit = '429' in err or 'rate' in err.lower()
                if attempt < MAX_RETRIES:
                    delay = BASE_RETRY_DELAY * (2 ** attempt)
                    if not is_rate_limit:
                        delay = BASE_RETRY_DELAY
                    time.sleep(delay)
                    continue

                # Record failure
                _write_error(
                    session, entity, err, attempt,
                    start_time, thread_id, worker_id
                )
                errors += 1

    return f"Batch {batch_id}: {processed} ok, {errors} errors, {len(entities)} total"
$$;
```

### Key Implementation Functions

**`_build_prompt`**: See Prompt Builder section above.

**`_parse_response`**: See `agent-definition.md` for the response parser.

**`_compute_validation_scores`**: Post-agent JW scoring for audit trail:
```python
def _compute_validation_scores(session, entity, parsed):
    """Compute JW scores between original entity and agent's matched entity."""
    if not parsed.get("matched_name"):
        return None, None
    try:
        rows = session.sql(
            "SELECT "
            "  JAROWINKLER_SIMILARITY(?, ?) / 100.0 AS name_jw, "
            "  JAROWINKLER_SIMILARITY(?, ?) / 100.0 AS addr_jw",
            params=[
                entity["ENTITY_NAME"] or '',
                parsed.get("matched_name", ''),
                entity["ENTITY_ADDRESS"] or '',
                parsed.get("matched_address", ''),
            ]
        ).collect()
        return rows[0]["NAME_JW"], rows[0]["ADDR_JW"]
    except Exception:
        return None, None
```

**`_record_discovery`**: Record entities the agent confirmed exist but are missing from the reference corpus:
```python
def _record_discovery(session, entity, parsed, worker_id):
    """Record agent-discovered entities missing from reference."""
    decision = parsed.get("decision", "")
    discovered_name = parsed.get("discovered_name")
    wve = parsed.get("web_validated_entity")
    web_searched = parsed.get("web_search_used", False)

    should_record = (
        decision == 'new_record_needed'
        or (web_searched and (discovered_name or wve))
    )
    if not should_record:
        return

    d_name = (discovered_name
              or (wve.get("name") if wve else None)
              or parsed.get("matched_name")
              or entity.get("ENTITY_NAME", ""))

    if not d_name:
        return

    # INSERT INTO discovered_entities (...)
    # Non-fatal: don't fail the resolution over a discovery insert
```

---

## Task DAG — Parallel Execution

Use a Snowflake Task DAG with a root task, N parallel workers, and a finalizer:

```sql
-- Root task (triggers the DAG)
CREATE OR REPLACE TASK <schema>.root_task
    WAREHOUSE = <warehouse_name>
AS
    SELECT 'Agent pipeline triggered' AS status;

-- Create N worker tasks (one per batch)
-- Use dynamic SQL to generate workers:
EXECUTE IMMEDIATE $$
DECLARE
    num_batches INT DEFAULT <num_workers>;  -- e.g., 98
    i INT DEFAULT 0;
    ddl STRING;
BEGIN
    FOR i IN 0 TO :num_batches - 1 DO
        ddl := 'CREATE OR REPLACE TASK <schema>.er_worker_' || :i::STRING
               || ' WAREHOUSE = <warehouse_name>'
               || ' AFTER <schema>.root_task'
               || ' AS CALL <schema>.resolve_entity_batch(' || :i::STRING || ')';
        EXECUTE IMMEDIATE :ddl;
    END FOR;
    RETURN 'Created ' || :num_batches::STRING || ' worker tasks';
END;
$$;

-- Finalizer (runs after all workers complete)
CREATE OR REPLACE TASK <schema>.finalizer_task
    WAREHOUSE = <warehouse_name>
    FINALIZE = <schema>.root_task
AS
    CALL <schema>.finalize_results();
```

### Worker Design Principles

- **No lock contention**: Workers read from the queue by batch_id (pre-assigned). No UPDATE to claim rows. Completion is tracked via INSERT into agent_results.
- **Skip-completed**: Workers check for existing results before processing, enabling idempotent restart.
- **Status backfill**: The finalizer updates queue status from agent_results in a single bulk UPDATE after all workers finish.

---

## Finalizer SP

Runs once after all workers complete. Backfills queue status, computes summary stats, and optionally triggers the next processing cycle.

```sql
CREATE OR REPLACE PROCEDURE finalize_results()
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
BEGIN
    -- Backfill queue status from results
    UPDATE agent_work_queue q
    SET status = CASE
            WHEN ar.decision = 'error' THEN 'FAILED'
            ELSE 'COMPLETED'
        END,
        worker_id = ar.worker_id,
        completed_at = ar.created_at
    FROM agent_results ar
    WHERE q.source_entity_id = ar.source_entity_id
      AND q.status = 'PENDING';

    -- Gather summary stats
    LET total_match INT;
    LET total_probable INT;
    LET total_no_match INT;
    LET total_closed INT;
    LET total_new_record INT;
    LET total_errors INT;
    LET total_web_searched INT;

    SELECT COUNT_IF(decision = 'match') INTO :total_match FROM agent_results;
    SELECT COUNT_IF(decision = 'probable_match') INTO :total_probable FROM agent_results;
    SELECT COUNT_IF(decision = 'no_match') INTO :total_no_match FROM agent_results;
    SELECT COUNT_IF(decision = 'location_closed') INTO :total_closed FROM agent_results;
    SELECT COUNT_IF(decision = 'new_record_needed') INTO :total_new_record FROM agent_results;
    SELECT COUNT_IF(decision = 'error') INTO :total_errors FROM agent_results;
    SELECT COUNT_IF(web_search_used = TRUE) INTO :total_web_searched FROM agent_results;

    RETURN 'Agent results: ' ||
           :total_match || ' match, ' ||
           :total_probable || ' probable, ' ||
           :total_no_match || ' no_match, ' ||
           :total_closed || ' closed, ' ||
           :total_new_record || ' new_record, ' ||
           :total_errors || ' errors. ' ||
           'Web search used: ' || :total_web_searched || ' entities.';
END;
```

---

## State-Based Pipeline Control (Optional)

For large datasets spanning multiple partitions (e.g., US states), process one partition at a time:

### State Queue Table

```sql
CREATE OR REPLACE TABLE state_batch_queue (
    state_code    STRING PRIMARY KEY,
    entity_count  INT,
    status        STRING DEFAULT 'PENDING',  -- PENDING | ACTIVE | COMPLETED | ERROR
    started_at    TIMESTAMP_NTZ,
    completed_at  TIMESTAMP_NTZ,
    summary       STRING
);
```

### Controller SP

Called by the finalizer to auto-feed the next partition:

```sql
CREATE OR REPLACE PROCEDURE controller_check_and_enqueue(BACKLOG_THRESHOLD INT)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
BEGIN
    -- Check current backlog (pending entities in agent queue)
    LET backlog INT;
    SELECT COUNT_IF(status = 'PENDING') INTO :backlog FROM agent_work_queue;

    IF (:backlog <= :BACKLOG_THRESHOLD) THEN
        -- Find next pending state
        LET next_state STRING;
        SELECT state_code INTO :next_state
        FROM state_batch_queue
        WHERE status = 'PENDING'
        ORDER BY entity_count ASC  -- Process smallest states first
        LIMIT 1;

        IF (:next_state IS NOT NULL) THEN
            -- Enqueue the next state
            CALL enqueue_state(:next_state);
            -- Resume the Task DAG
            EXECUTE TASK root_task;
            RETURN 'Enqueued state: ' || :next_state;
        END IF;

        RETURN 'All states processed.';
    END IF;

    RETURN 'Backlog still high (' || :backlog || '). Waiting.';
END;
```

This pattern enables continuous processing: as one state completes, the next is automatically loaded and the Task DAG is re-triggered.

---

## Agent Results Table

```sql
CREATE OR REPLACE TABLE agent_results (
    result_id               INT AUTOINCREMENT,
    source_entity_id        STRING NOT NULL,
    matched_reference_id    STRING,
    decision                STRING,
    confidence              FLOAT,
    agent_reasoning         STRING,
    search_steps            VARIANT,
    matched_name            STRING,
    matched_address         STRING,
    matched_city            STRING,
    matched_state           STRING,
    matched_zip             STRING,
    reference_source        STRING,
    -- Validation scores
    name_jw                 FLOAT,
    address_jw              FLOAT,
    -- Web search metadata
    web_search_used         BOOLEAN DEFAULT FALSE,
    web_sources             VARIANT,
    web_validated_entity    VARIANT,
    -- Execution metadata
    thread_id               STRING,
    worker_id               STRING,
    model_used              STRING,
    tokens_used             INT,
    processing_time_seconds FLOAT,
    created_at              TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (result_id)
);
```

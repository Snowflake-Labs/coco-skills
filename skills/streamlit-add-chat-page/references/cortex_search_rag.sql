-- ============================================================================
-- Cortex Search (RAG) layer for a "chat with your documents" page.
-- Indexes CHUNKED text so the chat page can retrieve relevant passages and
-- synthesize a grounded, cited answer. Replace <DB>.<SCHEMA>, table/column names.
-- ============================================================================

USE ROLE <ADMIN_ROLE>;          -- separate admin role that OWNS these objects (NOT the SiS app owner;
                                -- grant the app's least-privilege role only what SKILL.md lists)
USE WAREHOUSE <WAREHOUSE>;

-- 1) CHUNKED DOCUMENTS TABLE --------------------------------------------------
-- The search service indexes ONE row per chunk. If you already have a chunks
-- table (CHUNK_TEXT + metadata), skip to step 2. To build one from raw docs:
--   * Extract text: AI_PARSE_DOCUMENT for PDFs/images; plain columns otherwise.
--   * Split into chunks with SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER.
-- Chunk size guidance: ~300-1800 chars with ~10-15% overlap; smaller = more precise
-- retrieval, larger = more context per chunk. Keep source metadata for citations.
CREATE TABLE IF NOT EXISTS <DB>.<SCHEMA>.<DOC_CHUNKS> (
  DOC_ID      STRING,
  TITLE       STRING,
  URL         STRING,       -- link back to the source (for citations)
  CHUNK_TEXT  STRING
);
-- Example chunking from a raw docs table (adapt columns):
-- INSERT INTO <DB>.<SCHEMA>.<DOC_CHUNKS> (DOC_ID, TITLE, URL, CHUNK_TEXT)
-- SELECT d.DOC_ID, d.TITLE, d.URL, c.value::string
-- FROM <DB>.<SCHEMA>.<RAW_DOCS> d,
--      LATERAL FLATTEN(SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER(
--          d.FULL_TEXT, 'markdown', 1500, 200)) c;

-- 2) CORTEX SEARCH SERVICE over the chunks -----------------------------------
-- ON = the text column that gets searched. ATTRIBUTES = metadata columns you can
-- return/filter (title, url, doc_id). The AS query selects the searchable text
-- plus every column you want back for citations. TARGET_LAG keeps it fresh.
CREATE OR REPLACE CORTEX SEARCH SERVICE <DB>.<SCHEMA>.<SEARCH_SERVICE>
  ON CHUNK_TEXT
  ATTRIBUTES TITLE, URL, DOC_ID
  WAREHOUSE = <WAREHOUSE>
  TARGET_LAG = '1 hour'
  AS (
    SELECT CHUNK_TEXT, TITLE, URL, DOC_ID
    FROM <DB>.<SCHEMA>.<DOC_CHUNKS>
  );

-- 3) FEEDBACK LOG (shared with the structured path — create once) -------------
CREATE TABLE IF NOT EXISTS <DB>.<SCHEMA>.CHAT_FEEDBACK (
  TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  USERNAME STRING, QUESTION STRING, GENERATED_SQL STRING,
  REQUEST_ID STRING, STATUS STRING, RATING STRING
);

-- 4) VERIFY (before wiring the app) -------------------------------------------
-- SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
--   '<DB>.<SCHEMA>.<SEARCH_SERVICE>',
--   '{"query":"<a real question>","columns":["CHUNK_TEXT","TITLE","URL"],"limit":5}'
-- ))['results'];
-- Confirm the top results are actually relevant to the question.

-- SYSTEM$EXPLAIN_SEMANTIC_QUERY: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV

  TABLES (
    customers       PRIMARY KEY (customer_id),
    support_tickets PRIMARY KEY (ticket_id)
  )

  RELATIONSHIPS (
    support_tickets(customer_id) REFERENCES customers
  )

  FACTS (
    support_tickets.ticket_amount AS amount,

    -- PRIVATE entity-level fact: total contract value per customer
    -- Cannot be queried directly; used only inside the derived dimension below
    PRIVATE customers.total_contract_value AS SUM(support_tickets.amount)
  )

  DIMENSIONS (
    customers.tier           AS tier
      WITH SYNONYMS ('customer tier', 'segment'),
    customers.customer_name  AS customer_name
      WITH SYNONYMS ('customer', 'account'),

    -- Derived dimension from a PRIVATE aggregated fact
    customers.value_segment AS (
      CASE
        WHEN customers.total_contract_value >= 200000 THEN 'high-value'
        WHEN customers.total_contract_value >= 50000  THEN 'mid-value'
        ELSE                                               'low-value'
      END
    )
    WITH SYNONYMS ('value segment', 'account segment'),

    support_tickets.priority    AS priority
      WITH SYNONYMS ('ticket priority', 'severity'),
    support_tickets.opened_date AS opened_date
      WITH SYNONYMS ('date', 'ticket date')
  )

  METRICS (
    support_tickets.total_tickets AS COUNT(ticket_id)
      WITH SYNONYMS ('tickets', 'ticket count', 'number of tickets'),
    support_tickets.total_revenue AS SUM(amount)
      WITH SYNONYMS ('revenue', 'contract value'),
    customers.customer_count AS COUNT(customer_id)
      WITH SYNONYMS ('customers', 'accounts')
  )

  COMMENT = 'Support ticket analytics. Includes a PRIVATE aggregated fact (total_contract_value) that drives the value_segment derived dimension — useful for demonstrating SYSTEM$EXPLAIN_SEMANTIC_QUERY on queries with inlined PRIVATE logic.'

  AI_SQL_GENERATION 'Use customers.tier for enterprise/mid-market/smb breakdowns. Use customers.value_segment for high/mid/low-value account segmentation. Use support_tickets.priority to filter or group by P1/P2/P3. The total_contract_value fact is PRIVATE — it powers value_segment internally.';

-- Generic domain test data — 10 records (no authoritative IDs)
--
-- EXPECTED MATCHES (same entity):
--   GN001 <-> GN002  : Near-identical name+address+phone (Acme Manufacturing / ACME MFG CO)
--   GN003 <-> GN004  : Abbreviation variants (National Insurance Associates / Natl Ins Assoc) + same phone
--   GN009 <-> GN010  : DBA variant (Robert Williams / Williams Plumbing dba Rob Williams) + same phone
--
-- EXPECTED NON-MATCHES (different entities):
--   GN005 vs GN006   : Smith & Associates — same name, DIFFERENT addresses (branches)
--   GN007 vs GN008   : Alpha Consulting vs Beta Technologies — different names, same address
--

CREATE OR REPLACE TABLE generic_source (
    source_id    VARCHAR,
    source_table VARCHAR DEFAULT 'gen_a',
    raw_name     VARCHAR,
    raw_address  VARCHAR,
    raw_phone    VARCHAR,
    raw_email    VARCHAR
);

INSERT INTO generic_source VALUES
('GN001', 'gen_a', 'Acme Manufacturing Company',   '100 Industrial Blvd, Springfield, IL 62701', '217-555-0100', 'info@acmemfg.com'),
('GN002', 'gen_b', 'ACME MFG CO',                  '100 Industrial Boulevard, Springfield, IL 62701', '217-555-0100', 'info@acmemfg.com'),
('GN003', 'gen_a', 'National Insurance Associates', '200 Finance Dr, Suite 300, Chicago, IL 60601', '312-555-0200', NULL),
('GN004', 'gen_b', 'Natl Ins Assoc',               '200 Finance Drive, Ste 300, Chicago, IL 60601', '312-555-0200', NULL),
('GN005', 'gen_a', 'Smith & Associates',            '300 Oak St, Springfield, IL 62701', '217-555-0300', 'springfield@smith.com'),
('GN006', 'gen_b', 'Smith & Associates',            '400 Lake Shore Dr, Chicago, IL 60611', '312-555-0400', 'chicago@smith.com'),
('GN007', 'gen_a', 'Alpha Consulting Group',        '500 Business Park, Suite 100, Peoria, IL 61602', '309-555-0500', NULL),
('GN008', 'gen_b', 'Beta Technologies LLC',         '500 Business Park, Suite 100, Peoria, IL 61602', '309-555-0600', NULL),
('GN009', 'gen_a', 'Robert Williams',               '800 Elm St, Champaign, IL 61820', '217-555-0900', NULL),
('GN010', 'gen_b', 'Williams Plumbing dba Rob Williams Services', '800 Elm Street, Champaign, IL 61820', '217-555-0900', NULL);

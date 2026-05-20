-- Retail/CPG domain test data — 12 records
--
-- EXPECTED MATCHES (same entity):
--   RT001 <-> RT002  : Same GLN 0012345678901 (Walmart Supercenter, store number format variation)
--   RT003 <-> RT004  : Same GTIN 00049000028904 (Coca-Cola 12pk, catalog description variation)
--   RT009 <-> RT010  : Same GLN 0022222222201 (CVS, store number normalization)
--
-- EXPECTED NON-MATCHES (different entities):
--   RT005 vs RT006   : Target — different store locations, different GLNs
--   RT007 vs RT008   : McDonald's corporate vs franchise at same address — different DUNS
--   RT011 vs RT012   : Different GTIN (24-pack vs 6-pack) — different products
--

CREATE OR REPLACE TABLE retail_source (
    source_id       VARCHAR,
    source_table    VARCHAR DEFAULT 'ret_a',
    raw_name        VARCHAR,
    raw_address     VARCHAR,
    raw_gtin        VARCHAR,
    raw_gln         VARCHAR,
    raw_supplier_id VARCHAR,
    raw_duns        VARCHAR,
    entity_type     VARCHAR
);

INSERT INTO retail_source VALUES
('RT001', 'ret_a', 'Walmart Supercenter #3456',        '100 Retail Blvd, Springfield, IL 62701', NULL, '0012345678901', 'SUP-001', '123456789', 'store'),
('RT002', 'ret_b', 'WALMART SUPERCENTER 3456',          '100 Retail Boulevard, Springfield, IL 62701', NULL, '0012345678901', 'SUP-001', '123456789', 'store'),
('RT003', 'ret_a', 'Coca-Cola Classic 12oz Can 12-Pack', NULL, '00049000028904', NULL, NULL, NULL, 'product'),
('RT004', 'ret_b', 'Coca Cola Classic 12pk 12 oz cans',  NULL, '00049000028904', NULL, NULL, NULL, 'product'),
('RT005', 'ret_a', 'Target T-1892',                     '200 Commerce Dr, Springfield, IL 62701', NULL, '0098765432101', NULL, '333333333', 'store'),
('RT006', 'ret_b', 'Target T-2104',                     '500 Lake Shore Dr, Chicago, IL 60611', NULL, '0098765432102', NULL, '333333333', 'store'),
('RT007', 'ret_a', 'McDonald''s Restaurant',             '300 Main St, Springfield, IL 62701', NULL, '0011111111101', NULL, '444444444', 'store'),
('RT008', 'ret_b', 'ABC Foods LLC dba McDonald''s',      '300 Main St, Springfield, IL 62701', NULL, '0011111111101', NULL, '555555555', 'store'),
('RT009', 'ret_a', 'CVS/pharmacy #08821',               '400 Elm St, Chicago, IL 60602', NULL, '0022222222201', NULL, '666666666', 'store'),
('RT010', 'ret_b', 'CVS Pharmacy Store 8821',            '400 Elm Street, Chicago, IL 60602', NULL, '0022222222201', NULL, '666666666', 'store'),
('RT011', 'ret_a', 'Coca-Cola Classic 12oz Can 24-Pack', NULL, '00049000028911', NULL, NULL, NULL, 'product'),
('RT012', 'ret_b', 'Coca-Cola Classic 12oz Can 6-Pack',  NULL, '00049000028898', NULL, NULL, NULL, 'product');

-- Pharma domain test data — 12 records
--
-- EXPECTED MATCHES (same entity):
--   PH001 <-> PH002  : Same NPI 1234567890 (CVS Pharmacy, same location, name variation)
--   PH003 <-> PH004  : Same DEA CD9876543  (Springfield Family Pharmacy/Phcy)
--   PH009 <-> PH010  : Same NPI 5555555555 (CVS, same address, store number format variation)
--
-- EXPECTED NON-MATCHES (different entities):
--   PH005 vs PH006   : Walgreens — same chain name, DIFFERENT addresses/NPIs (separate locations)
--   PH007 vs PH008   : Same street address, DIFFERENT suites (200 vs 310) and DIFFERENT NPIs
--   PH011 vs PH012   : Prescriber vs pharmacy at same address — different entity types
--

CREATE OR REPLACE TABLE pharma_source (
    source_id    VARCHAR,
    source_table VARCHAR DEFAULT 'pharma_a',
    raw_name     VARCHAR,
    raw_address  VARCHAR,
    raw_npi      VARCHAR,
    raw_dea      VARCHAR,
    raw_ncpdp    VARCHAR
);

INSERT INTO pharma_source VALUES
('PH001', 'pharma_a', 'CVS Pharmacy #4523',           '100 Main St, Suite 101, Springfield, IL 62701', '1234567890', 'AB1234567', '1234567'),
('PH002', 'pharma_b', 'CVS PHARMACY 4523',            '100 Main Street, Ste 101, Springfield, IL 62701', '1234567890', 'AB1234567', '1234567'),
('PH003', 'pharma_a', 'Springfield Family Pharmacy',   '200 Oak Ave, Springfield, IL 62702', NULL, 'CD9876543', '7654321'),
('PH004', 'pharma_b', 'Springfield Family Phcy LLC',   '200 Oak Avenue, Springfield, IL 62702', NULL, 'CD9876543', '7654321'),
('PH005', 'pharma_a', 'Walgreens',                     '300 Elm St, Springfield, IL 62701', '1111111111', 'EF1111111', '1111111'),
('PH006', 'pharma_b', 'Walgreens',                     '500 Pine St, Chicago, IL 60601', '2222222222', 'EF2222222', '2222222'),
('PH007', 'pharma_a', 'Dr. Smith Medical Practice',    '400 Medical Pkwy, Suite 200, Springfield, IL 62701', '3333333333', 'GH3333333', NULL),
('PH008', 'pharma_b', 'Springfield Urgent Care',       '400 Medical Pkwy, Suite 310, Springfield, IL 62701', '4444444444', 'IJ4444444', NULL),
('PH009', 'pharma_a', 'CVS/pharmacy #12345',           '600 Broadway, Chicago, IL 60602', '5555555555', 'KL5555555', '5555555'),
('PH010', 'pharma_b', 'CVS Pharmacy Store 12345',      '600 Broadway, Chicago, IL 60602', '5555555555', 'KL5555555', '5555555'),
('PH011', 'pharma_a', 'Dr. Jane Doe, MD',              '400 Medical Pkwy, Suite 200, Springfield, IL 62701', '6666666666', 'MN6666666', NULL),
('PH012', 'pharma_b', 'Springfield Medical Group Inc',  '400 Medical Pkwy, Suite 200, Springfield, IL 62701', '7777777777', NULL, NULL);

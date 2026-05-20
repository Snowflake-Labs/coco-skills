-- Healthcare provider domain test data — 12 records
--
-- EXPECTED MATCHES (same entity):
--   HC001 <-> HC002  : Same NPI 1234567890, both Type 1 (Dr. John Smith, name format variation)
--   HC003 <-> HC004  : Same NPI 9876543210, both Type 2 (Springfield Medical Group, case variation)
--   HC007 <-> HC008  : Same NPI 3333333333, Type 1, DIFFERENT addresses (multi-location provider)
--
-- EXPECTED NON-MATCHES (different entities):
--   HC005 vs HC006   : Type 1 individual vs Type 2 organization at same address — MUST NOT cross-match
--   HC009 vs HC010   : Same building, different suites (210 vs 220), different NPIs — separate practices
--   HC011 vs HC012   : Same NPI but we put different NPIs to test — actually same person, credential variation
--
-- NOTE: HC007 and HC008 share an NPI but are in different cities (Peoria vs Champaign).
-- Blocking by state+zip3 may separate them.  The skill must still resolve them via Tier 1 NPI match
-- if its blocking strategy covers them (e.g., compound blocking or NPI-prefix blocking).

CREATE OR REPLACE TABLE healthcare_source (
    source_id       VARCHAR,
    source_table    VARCHAR DEFAULT 'hc_a',
    raw_name        VARCHAR,
    raw_address     VARCHAR,
    raw_npi         VARCHAR,
    npi_type        VARCHAR,
    raw_taxonomy    VARCHAR
);

INSERT INTO healthcare_source VALUES
('HC001', 'hc_a', 'Smith, John A, MD',              '100 Medical Dr, Suite 200, Springfield, IL 62701', '1234567890', '1', '207R00000X'),
('HC002', 'hc_b', 'Dr. John Smith MD',               '100 Medical Drive, Ste 200, Springfield, IL 62701', '1234567890', '1', '207R00000X'),
('HC003', 'hc_a', 'Springfield Medical Group Inc',   '100 Medical Dr, Springfield, IL 62701', '9876543210', '2', '261QM0801X'),
('HC004', 'hc_b', 'SPRINGFIELD MEDICAL GROUP',       '100 Medical Drive, Springfield, IL 62701', '9876543210', '2', '261QM0801X'),
('HC005', 'hc_a', 'Johnson, Mary, DO',               '200 Health Pkwy, Suite 100, Chicago, IL 60601', '1111111111', '1', '208000000X'),
('HC006', 'hc_b', 'Chicago Health Associates PC',    '200 Health Pkwy, Suite 100, Chicago, IL 60601', '2222222222', '2', '261QP2300X'),
('HC007', 'hc_a', 'Williams, Robert, MD',            '300 Oak St, Suite 400, Peoria, IL 61602', '3333333333', '1', '208600000X'),
('HC008', 'hc_b', 'Williams, Robert, MD',            '500 Pine Ave, Suite 100, Champaign, IL 61820', '3333333333', '1', '208600000X'),
('HC009', 'hc_a', 'Davis Orthopedic Clinic',         '400 Medical Pkwy, Suite 210, Springfield, IL 62701', '4444444444', '2', '207X00000X'),
('HC010', 'hc_b', 'Springfield Physical Therapy LLC', '400 Medical Pkwy, Suite 220, Springfield, IL 62701', '5555555555', '2', '225100000X'),
('HC011', 'hc_a', 'Garcia, Maria Elena, NP',         '600 University Ave, Urbana, IL 61801', '6666666666', '1', '363L00000X'),
('HC012', 'hc_b', 'Maria Garcia ARNP',               '600 University Avenue, Urbana, IL 61801', '6666666666', '1', '363L00000X');

-- Financial services domain test data — 12 records
--
-- EXPECTED MATCHES (same entity):
--   FN001 <-> FN002  : Same LEI 784F5XWPLTWKTBV3E584 (Goldman Sachs, name/addr variation)
--   FN003 <-> FN004  : Same DUNS 987654321 (Midwest Financial Advisors, no LEI)
--   FN007 <-> FN008  : Same LEI 7LTWFZYICNSX8D621K86 (Deutsche Bank, German name variant)
--
-- EXPECTED NON-MATCHES (different entities):
--   FN005 vs FN006   : JPMorgan Chase & Co vs JPMorgan Chase Bank NA — parent vs subsidiary, DIFFERENT LEIs
--   FN009 vs FN010   : HSBC Holdings PLC (UK) vs HSBC Bank USA — different jurisdictions, DIFFERENT LEIs
--   FN011 vs FN012   : Both small firms at same address — DIFFERENT DUNS/Tax IDs
--

CREATE OR REPLACE TABLE financial_source (
    source_id    VARCHAR,
    source_table VARCHAR DEFAULT 'fin_a',
    raw_name     VARCHAR,
    raw_address  VARCHAR,
    raw_lei      VARCHAR,
    raw_duns     VARCHAR,
    raw_tax_id   VARCHAR,
    raw_crd      VARCHAR,
    raw_swift    VARCHAR
);

INSERT INTO financial_source VALUES
('FN001', 'fin_a', 'Goldman Sachs Group, Inc.',        '200 West St, New York, NY 10282', '784F5XWPLTWKTBV3E584', '001234567', '13-1234567', NULL, 'GSCMUS33'),
('FN002', 'fin_b', 'GOLDMAN SACHS GROUP INC',          '200 West Street, New York, NY 10282', '784F5XWPLTWKTBV3E584', '001234567', '13-1234567', NULL, 'GSCMUS33'),
('FN003', 'fin_a', 'Midwest Financial Advisors',       '100 Finance Blvd, Chicago, IL 60601', NULL, '987654321', '36-9876543', '1234567', NULL),
('FN004', 'fin_b', 'Midwest Financial Advisors LLC',   '100 Finance Boulevard, Chicago, IL 60601', NULL, '987654321', '36-9876543', '1234567', NULL),
('FN005', 'fin_a', 'JPMorgan Chase & Co.',             '383 Madison Ave, New York, NY 10179', '8I5DZWZKVSZI1NUHU748', '006969150', '13-2624428', NULL, 'CHASUS33'),
('FN006', 'fin_b', 'JPMorgan Chase Bank, N.A.',        '1111 Polaris Pkwy, Columbus, OH 43240', '7H6GLXDRUGQFU57RNE97', '006969224', '13-4994650', NULL, 'CHASUS33XXX'),
('FN007', 'fin_a', 'Deutsche Bank AG',                 'Taunusanlage 12, Frankfurt, Germany 60325', '7LTWFZYICNSX8D621K86', '314733909', NULL, NULL, 'DEUTDEFF'),
('FN008', 'fin_b', 'DEUTSCHE BANK AKTIENGESELLSCHAFT', 'Taunusanlage 12, 60325 Frankfurt am Main, DE', '7LTWFZYICNSX8D621K86', '314733909', NULL, NULL, 'DEUTDEFF'),
('FN009', 'fin_a', 'HSBC Holdings PLC',                '8 Canada Square, London, UK E14 5HQ', 'MLU0ZO3ML4LN2LL2TL39', '217479718', NULL, NULL, 'HSBCGB2L'),
('FN010', 'fin_b', 'HSBC Bank USA, N.A.',              '452 Fifth Ave, New York, NY 10018', 'CYYGQCGNHMHPSMRL3R97', '004977852', '13-4776207', NULL, 'HSBCUS33'),
('FN011', 'fin_a', 'Alpha Capital Advisors',           '50 State St, Suite 300, Boston, MA 02109', NULL, '111111111', '04-1111111', '9999999', NULL),
('FN012', 'fin_b', 'Beta Wealth Management Inc',       '50 State St, Suite 300, Boston, MA 02109', NULL, '222222222', '04-2222222', '8888888', NULL);

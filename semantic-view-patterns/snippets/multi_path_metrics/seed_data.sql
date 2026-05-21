-- Multi-Path Metrics: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO flights VALUES
    (1, 'SFO', 'SEA', true,  '2025-01-03 06:00:00', '2025-01-03 11:00:00'),
    (2, 'SEA', 'SFO', false, '2025-01-03 11:00:00', '2025-01-03 16:00:00'),
    (3, 'SEA', 'PVG', false, '2025-01-03 11:00:00', '2025-01-04 11:00:00'),
    (4, 'SFO', 'PVG', true,  '2025-01-03 06:00:00', '2025-01-04 11:00:00');

INSERT INTO weather VALUES
    ('SEA', 'rainy',  '2025-01-03 10:00:00', '2025-01-03 12:00:00'),
    ('SFO', 'sunny',  '2025-01-03 05:00:00', '2025-01-03 09:00:00'),
    ('SFO', 'sunny',  '2025-01-03 10:00:00', '2025-01-03 18:00:00'),
    ('PVG', 'cloudy', '2025-01-04 10:00:00', '2025-01-04 12:00:00');

-- Expected: departure weather conditions
--   Flight 1 (SFO dep 06:00) → SFO sunny (05:00-09:00) → late=true
--   Flight 2 (SEA dep 11:00) → SEA rainy (10:00-12:00) → late=false
--   Flight 3 (SEA dep 11:00) → SEA rainy (10:00-12:00) → late=false
--   Flight 4 (SFO dep 06:00) → SFO sunny (05:00-09:00) → late=true
-- Expected: arrival weather conditions
--   Flight 1 (SEA arr 11:00) → SEA rainy → late=true
--   Flight 2 (SFO arr 16:00) → SFO sunny → late=false
--   Flight 3 (PVG arr Jan04 11:00) → PVG cloudy → late=false
--   Flight 4 (PVG arr Jan04 11:00) → PVG cloudy → late=true

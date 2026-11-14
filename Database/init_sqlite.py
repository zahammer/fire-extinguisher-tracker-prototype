#!/usr/bin/env python3
"""
MTU Fire Extinguisher Management System - SQLite Initializer
Generates a portable 'mtu_fire_safety.db' directly in the project folder.
Hardened with NFPA 10 / MIOSHA Part 8 engine-level validation and immutability triggers.
"""

import sqlite3
from pathlib import Path

DB_FILE = Path(__file__).parent / "mtu_fire_safety.db"

SCHEMA_AND_SEED = """
PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- 0. TEARDOWN
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_extinguisher_operational_health;
DROP VIEW IF EXISTS v_service_status;
DROP TABLE IF EXISTS inspections_monthly;
DROP TABLE IF EXISTS services;
DROP TABLE IF EXISTS service_types;
DROP TABLE IF EXISTS deployments;
DROP TABLE IF EXISTS locations;
DROP TABLE IF EXISTS extinguishers;
DROP TABLE IF EXISTS users;

-- -----------------------------------------------------------------------------
-- 1. USERS TABLE (MTU SSO & RBAC)
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    user_id     TEXT PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1
);

-- -----------------------------------------------------------------------------
-- 2. LOCATIONS TABLE (ADA § 308 & NFPA 10 § 6.1.3)
-- -----------------------------------------------------------------------------
CREATE TABLE locations (
    location_id         TEXT PRIMARY KEY,
    building_code       TEXT NOT NULL,
    floor_number        INTEGER NOT NULL,
    closest_room_number TEXT NOT NULL,
    mounting_type       TEXT NOT NULL,
    has_signage         INTEGER NOT NULL DEFAULT 1,
    is_accessible_ada   INTEGER NOT NULL DEFAULT 1,
    notes               TEXT
);

-- -----------------------------------------------------------------------------
-- 3. EXTINGUISHERS TABLE (DOT & NFPA 10 Asset Tracking)
-- -----------------------------------------------------------------------------
CREATE TABLE extinguishers (
    extinguisher_id   TEXT PRIMARY KEY,
    serial_number     TEXT NOT NULL UNIQUE,
    manufacturer      TEXT NOT NULL,
    model_number      TEXT,
    extinguisher_type TEXT NOT NULL,
    capacity_rating   TEXT NOT NULL,
    manufacture_year  INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'In Storage', 'Out for Service', 'Condemned')),
    notes             TEXT
);

-- -----------------------------------------------------------------------------
-- 4. DEPLOYMENTS TABLE
-- -----------------------------------------------------------------------------
CREATE TABLE deployments (
    deployment_id   TEXT PRIMARY KEY,
    extinguisher_id TEXT NOT NULL,
    location_id     TEXT NOT NULL,
    installed_date  TEXT NOT NULL,
    removed_date    TEXT,
    is_current      INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (extinguisher_id) REFERENCES extinguishers(extinguisher_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON UPDATE CASCADE ON DELETE RESTRICT
);

-- -----------------------------------------------------------------------------
-- 5. SERVICE TYPES LOOKUP
-- -----------------------------------------------------------------------------
CREATE TABLE service_types (
    service_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name    TEXT NOT NULL UNIQUE,
    interval_months INTEGER NOT NULL,
    description     TEXT,
    nfpa_reference  TEXT
);

-- -----------------------------------------------------------------------------
-- 6. SERVICES TABLE (Certified Maintenance Logs)
-- -----------------------------------------------------------------------------
CREATE TABLE services (
    service_id            TEXT PRIMARY KEY,
    extinguisher_id       TEXT NOT NULL,
    service_type_id       INTEGER NOT NULL,
    service_date          TEXT NOT NULL,
    next_service_due      TEXT NOT NULL,
    vendor_company        TEXT,
    technician_license_no TEXT,
    certificate_url       TEXT,
    notes                 TEXT,
    FOREIGN KEY (extinguisher_id) REFERENCES extinguishers(extinguisher_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (service_type_id) REFERENCES service_types(service_type_id) ON UPDATE CASCADE ON DELETE RESTRICT
);

-- -----------------------------------------------------------------------------
-- 7. INSPECTIONS_MONTHLY TABLE (MIOSHA Part 8 Rule 835 / 29 CFR § 1910.157)
-- -----------------------------------------------------------------------------
CREATE TABLE inspections_monthly (
    inspection_id           TEXT PRIMARY KEY,
    extinguisher_id         TEXT NOT NULL,
    inspector_id            TEXT NOT NULL,
    inspection_timestamp    TEXT NOT NULL,
    is_present              INTEGER NOT NULL,
    is_unobstructed         INTEGER NOT NULL,
    pin_tamper_seal_intact  INTEGER NOT NULL,
    gauge_in_green          INTEGER NOT NULL,
    physical_condition_pass INTEGER NOT NULL,
    heft_check_pass         INTEGER NOT NULL,
    overall_result          TEXT NOT NULL CHECK (overall_result IN ('Pass', 'Fail', 'Needs Attention')),
    remediation_notes       TEXT,
    FOREIGN KEY (extinguisher_id) REFERENCES extinguishers(extinguisher_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (inspector_id) REFERENCES users(user_id) ON UPDATE CASCADE ON DELETE RESTRICT
);

-- -----------------------------------------------------------------------------
-- 8. COMPLIANCE VIEWS (Dynamic SLA Tracking)
-- -----------------------------------------------------------------------------
CREATE VIEW v_service_status AS
SELECT 
    e.extinguisher_id,
    st.service_name,
    s.next_service_due,
    CAST(julianday(s.next_service_due) - julianday('now') AS INTEGER) AS days_remaining,
    CASE 
        WHEN (julianday(s.next_service_due) - julianday('now')) < -7 THEN '>1 week past due'
        WHEN (julianday(s.next_service_due) - julianday('now')) BETWEEN -7 AND -0.0001 THEN '<1 week past due'
        WHEN (julianday(s.next_service_due) - julianday('now')) BETWEEN 0 AND 7 THEN '<1 week till due'
        ELSE 'OK'
    END AS service_due_status
FROM extinguishers e
JOIN services s ON e.extinguisher_id = s.extinguisher_id
JOIN service_types st ON s.service_type_id = st.service_type_id;

CREATE VIEW v_extinguisher_operational_health AS
SELECT 
    e.extinguisher_id,
    e.status AS physical_status,
    v.service_name,
    v.next_service_due,
    v.days_remaining,
    v.service_due_status,
    CASE 
        WHEN e.status IN ('Out for Service', 'Condemned') THEN 'Impaired - Out of Service'
        WHEN v.days_remaining < 0 THEN 'Impaired - Certification Expired'
        WHEN v.days_remaining BETWEEN 0 AND 7 THEN 'Warning - Service Imminent'
        ELSE 'Operational & Compliant'
    END AS operational_state
FROM extinguishers e
LEFT JOIN v_service_status v ON e.extinguisher_id = v.extinguisher_id;

-- -----------------------------------------------------------------------------
-- 9. COMPLIANCE & INTEGRITY TRIGGERS (PUT YOUR LOGIC RULES HERE)
-- -----------------------------------------------------------------------------

-- A. Logic Gate: Abort any inspection recording "Pass" if core physical checks failed
CREATE TRIGGER trg_validate_inspection_logic
BEFORE INSERT ON inspections_monthly
FOR EACH ROW
WHEN (
    (NEW.is_present = 0 OR NEW.pin_tamper_seal_intact = 0 OR NEW.gauge_in_green = 0 OR 
     NEW.physical_condition_pass = 0 OR NEW.heft_check_pass = 0)
    AND NEW.overall_result = 'Pass'
)
BEGIN
    SELECT RAISE(ABORT, 'Audit Violation: Cannot record Pass when mandatory NFPA 10 visual checks have failed.');
END;

-- B. Immutability: Block modifying historical visual inspection checks
CREATE TRIGGER trg_inspections_no_update
BEFORE UPDATE ON inspections_monthly
BEGIN
    SELECT RAISE(ABORT, 'Integrity Violation: inspection records are legally immutable under MIOSHA Part 8.');
END;

-- C. Immutability: Block deleting inspection checks
CREATE TRIGGER trg_inspections_no_delete
BEFORE DELETE ON inspections_monthly
BEGIN
    SELECT RAISE(ABORT, 'Integrity Violation: inspection records cannot be deleted under NFPA 10 retention rules.');
END;

-- D. Immutability: Block modifying or deleting certified vendor service records
CREATE TRIGGER trg_services_no_update
BEFORE UPDATE ON services
BEGIN
    SELECT RAISE(ABORT, 'Integrity Violation: certified service records cannot be modified.');
END;

CREATE TRIGGER trg_services_no_delete
BEFORE DELETE ON services
BEGIN
    SELECT RAISE(ABORT, 'Integrity Violation: certified service records cannot be deleted.');
END;

-- -----------------------------------------------------------------------------
-- 10. MOCK SEED DATA
-- -----------------------------------------------------------------------------
INSERT INTO users (user_id, username, full_name, email, role, is_active) VALUES
('usr-001', 'ejbach', 'Ethan Bach', 'ejbach@mtu.edu', 'Safety Admin', 1),
('usr-002', 'zahammer', 'Zach Hammer', 'zahammer@mtu.edu', 'Facilities Lead', 1),
('usr-003', 'slspring', 'Spencer Springer', 'slspring@mtu.edu', 'Inspector', 1),
('usr-004', 'facilities_tech', 'Campus Facilities Tech', 'facilities@mtu.edu', 'Inspector', 1);

INSERT INTO locations (location_id, building_code, floor_number, closest_room_number, mounting_type, has_signage, is_accessible_ada, notes) VALUES
('LOC-DOW-201', 'DOW', 2, 'Room 214', 'Recessed Cabinet', 1, 1, 'North hallway corridor outside lab'),
('LOC-EERC-101', 'EERC', 1, 'Room 123', 'Wall Bracket', 1, 1, 'East stairwell entrance'),
('LOC-MEEM-301', 'MEEM', 3, 'Room 302', 'Surface Cabinet', 1, 1, 'South freight elevator vestibule'),
('LOC-REKHI-102', 'REKHI', 1, 'Room 112', 'Wall Bracket', 1, 1, 'Computer Science main corridor');

INSERT INTO extinguishers (extinguisher_id, serial_number, manufacturer, model_number, extinguisher_type, capacity_rating, manufacture_year, status, notes) VALUES
('EXT-AMX-001', 'SN-AMX-94821', 'Amerex', 'B402', 'ABC Dry Chemical', '10 lb - 4A:80B:C', 2021, 'Active', 'Standard wall mount ABC unit'),
('EXT-ANS-002', 'SN-ANS-11048', 'Ansul', 'Sentry AA10S', 'ABC Dry Chemical', '10 lb - 4A:80B:C', 2019, 'Active', 'Due for inspection soon'),
('EXT-BDG-003', 'SN-BDG-55102', 'Badger', 'Extra B10M', 'ABC Dry Chemical', '10 lb - 4A:80B:C', 2018, 'Active', 'Recent minor past-due check'),
('EXT-AMX-004', 'SN-AMX-33019', 'Amerex', '330', 'CO2', '10 lb - 10B:C', 2020, 'Out for Service', 'Discharged shell; awaiting vendor pickup');

INSERT INTO deployments (deployment_id, extinguisher_id, location_id, installed_date, removed_date, is_current) VALUES
('DEP-001', 'EXT-AMX-001', 'LOC-DOW-201', '2023-08-15', NULL, 1),
('DEP-002', 'EXT-ANS-002', 'LOC-EERC-101', '2022-09-01', NULL, 1),
('DEP-003', 'EXT-BDG-003', 'LOC-MEEM-301', '2021-05-10', NULL, 1),
('DEP-004', 'EXT-AMX-004', 'LOC-REKHI-102', '2021-08-20', '2026-10-15', 0);

INSERT INTO service_types (service_type_id, service_name, interval_months, description, nfpa_reference) VALUES
(1, 'Annual Maintenance', 12, 'Annual mechanical and agent verification', 'NFPA 10 § 7.3.1.1'),
(2, '6-Year Internal Examination', 72, 'Complete teardown and internal shell inspection', 'NFPA 10 § 7.3.1.2'),
(3, '12-Year Hydrostatic Test', 144, 'Pressure test verifying cylinder shell integrity', 'NFPA 10 § 8.3.1');

INSERT INTO services (service_id, extinguisher_id, service_type_id, service_date, next_service_due, vendor_company, technician_license_no, certificate_url, notes) VALUES
('SRV-001', 'EXT-AMX-001', 1, '2026-01-15', '2027-01-15', 'Superior Fire & Safety', 'MI-TECH-8821', 'https://storage.mtu.edu/certs/SRV-001.pdf', 'Status: OK'),
('SRV-002', 'EXT-ANS-002', 1, '2025-11-10', '2026-11-10', 'Superior Fire & Safety', 'MI-TECH-8821', 'https://storage.mtu.edu/certs/SRV-002.pdf', 'Status: <1 week till due'),
('SRV-003', 'EXT-BDG-003', 1, '2025-11-03', '2026-11-03', 'Keweenaw Fire Protection', 'MI-TECH-4412', 'https://storage.mtu.edu/certs/SRV-003.pdf', 'Status: <1 week past due'),
('SRV-004', 'EXT-AMX-004', 2, '2020-10-01', '2026-10-01', 'Keweenaw Fire Protection', 'MI-TECH-4412', 'https://storage.mtu.edu/certs/SRV-004.pdf', 'Status: >1 week past due');

INSERT INTO inspections_monthly (inspection_id, extinguisher_id, inspector_id, inspection_timestamp, is_present, is_unobstructed, pin_tamper_seal_intact, gauge_in_green, physical_condition_pass, heft_check_pass, overall_result, remediation_notes) VALUES
('INSP-001', 'EXT-AMX-001', 'usr-003', '2026-11-01 09:15:00', 1, 1, 1, 1, 1, 1, 'Pass', NULL),
('INSP-002', 'EXT-ANS-002', 'usr-003', '2026-11-01 09:40:00', 1, 1, 1, 1, 1, 1, 'Pass', NULL),
('INSP-003', 'EXT-BDG-003', 'usr-004', '2026-11-02 14:10:00', 1, 0, 1, 1, 1, 1, 'Needs Attention', 'Obstruction cleared; custodial cart moved.'),
('INSP-004', 'EXT-AMX-004', 'usr-002', '2026-10-14 16:05:00', 1, 1, 0, 0, 1, 0, 'Fail', 'Pressure gauge discharged; unit pulled.');
"""

def initialize_database():
    print(f"[*] Initializing SQLite database at: {DB_FILE.resolve()}")
    if DB_FILE.exists():
        DB_FILE.unlink()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.executescript(SCHEMA_AND_SEED)
    conn.commit()
    print("[+] Database initialized, integrity triggers loaded, and mock data seeded.")

    print("\n--- Operational Health & Impairment Overview ---")
    cursor.execute("SELECT extinguisher_id, physical_status, service_name, days_remaining, operational_state FROM v_extinguisher_operational_health;")
    rows = cursor.fetchall()

    header = f"{'ID':<14} | {'Asset Status':<16} | {'Service':<28} | {'Days':<6} | {'Operational State'}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r[0]:<14} | {r[1]:<16} | {str(r[2]):<28} | {str(r[3]):<6} | {r[4]}")

    conn.close()
    print(f"\n[+] Ready for packaging: {DB_FILE.name}")

if __name__ == "__main__":
    initialize_database()
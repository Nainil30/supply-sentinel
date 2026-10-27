"""DuckDB schema for a snapshot (spec 01 section 2).

Only primary keys are declared. Foreign keys are checked by `validate.py` instead, because
DuckDB's FK enforcement blocks the UPDATE/DELETE statements that scenario ops rely on.
"""

import duckdb

TABLES = [
    "meta", "platforms", "suppliers", "parts", "bom", "alternates", "shipments_history",
    "demand_forecast", "service_demand", "inventory", "purchase_orders", "supplier_commits",
    "eol_notices", "forecast_accuracy",
]  # fmt: skip

# Free-text columns that come from outside the company; the agent must treat them as data.
UNTRUSTED_FIELDS = {"supplier_commits": "note", "eol_notices": "raw_text"}

DDL = """
CREATE TYPE lifecycle_phase AS ENUM ('NPI', 'SUSTAIN', 'EOL_ANNOUNCED', 'EOL');
CREATE TYPE risk_tier AS ENUM ('LOW', 'MEDIUM', 'HIGH');
CREATE TYPE part_status AS ENUM ('ACTIVE', 'NRND', 'EOL_NOTICE', 'OBSOLETE');
CREATE TYPE qual_status AS ENUM ('QUALIFIED', 'IN_QUAL', 'NOT_STARTED');
CREATE TYPE bucket_type AS ENUM ('WEEK', 'MONTH');
CREATE TYPE channel AS ENUM ('PRODUCTION', 'SERVICE');
CREATE TYPE location AS ENUM ('CM_HUB', 'SERVICE_DEPOT');
CREATE TYPE po_status AS ENUM ('OPEN', 'PARTIAL', 'CLOSED');

CREATE TABLE meta (
    snapshot_id VARCHAR, as_of_date DATE, seed INTEGER,
    generator_version VARCHAR, created_at TIMESTAMP);
CREATE TABLE platforms (
    platform_id VARCHAR PRIMARY KEY, description VARCHAR, lifecycle_phase lifecycle_phase,
    launch_date DATE, last_ship_date DATE, eosl_date DATE, asp_usd DOUBLE);
CREATE TABLE suppliers (
    supplier_id VARCHAR PRIMARY KEY, name VARCHAR, commodity VARCHAR, region VARCHAR,
    otd_rate DOUBLE, risk_tier risk_tier);
CREATE TABLE parts (
    part_id VARCHAR PRIMARY KEY, description VARCHAR, commodity VARCHAR, supplier_id VARCHAR,
    unit_cost_usd DOUBLE, lead_time_weeks INTEGER, moq INTEGER, order_multiple INTEGER,
    lifecycle_status part_status, shelf_life_months INTEGER);
CREATE TABLE bom (
    platform_id VARCHAR, part_id VARCHAR, qty_per INTEGER,
    effective_from DATE, effective_to DATE);
CREATE TABLE alternates (
    part_id VARCHAR, alt_part_id VARCHAR, qualification_status qual_status,
    qual_lead_time_weeks INTEGER, notes VARCHAR);
CREATE TABLE shipments_history (
    platform_id VARCHAR, week_start DATE, units INTEGER, channel channel);
CREATE TABLE demand_forecast (
    platform_id VARCHAR, bucket_start DATE, bucket_type bucket_type, units DOUBLE,
    forecast_version DATE);
CREATE TABLE service_demand (platform_id VARCHAR, year INTEGER, spares_units INTEGER);
CREATE TABLE inventory (
    part_id VARCHAR, location location, on_hand_units INTEGER, quality_hold_units INTEGER);
CREATE TABLE purchase_orders (
    po_id VARCHAR PRIMARY KEY, part_id VARCHAR, supplier_id VARCHAR, qty_units INTEGER,
    need_week_idx INTEGER, promise_week_idx INTEGER, status po_status,
    cancel_window_weeks INTEGER, unit_cost_usd DOUBLE);
CREATE TABLE supplier_commits (
    part_id VARCHAR, supplier_id VARCHAR, week_idx INTEGER, commit_units INTEGER, note VARCHAR);
CREATE TABLE eol_notices (
    notice_id VARCHAR PRIMARY KEY, part_id VARCHAR, supplier_id VARCHAR, notice_date DATE,
    ltb_date DATE, last_ship_date DATE, replacement_part_id VARCHAR, raw_text VARCHAR);
CREATE TABLE forecast_accuracy (
    platform_id VARCHAR, lag_weeks INTEGER, mape DOUBLE, bias DOUBLE, sigma_units DOUBLE);
"""


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)

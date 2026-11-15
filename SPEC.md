# SPEC.md — GridPulse (Real-Time Peak-Energy Micro-Bounties)

## 1. Overview & Sustainability Goal
GridPulse turns any apartment, home, or dorm room into a virtual power plant node. Electric grids emit the most CO₂ during short "peak demand" windows (4 PM–9 PM) when fossil-fuel peaker plants spin up. GridPulse tracks real-time household wattage against regional grid carbon intensity, uses Gemini Vision to analyze utility bills and recommend high-impact appliance shifts, and pays instant sub-cent micro-bounties on Solana Devnet whenever a user reduces wattage during a dirty grid spike.

## 2. Tech Stack & Repository Structure
- `backend/` — Python 3.11+ FastAPI application (`uvicorn`), async endpoints, background telemetry simulator.
- `frontend/` — Next.js (App Router) + TypeScript + Tailwind CSS + Recharts/Lucide icons, cleanly styled with a dark-mode climate/telemetry aesthetic.
- `deploy/` — Production `Dockerfile`, `docker-compose.yml`, and `Caddyfile` (configured for `PUBLIC_DOMAIN` with automatic HTTPS) for one-command deployment on a Vultr Ubuntu VPS.
- `README.md` — Complete setup guide plus a dedicated **"How We Used Every Sponsor"** section (explicitly documenting Cursor, Grok Bot PR/issue workflow, Gemini API, Tiger Data, Solana, MongoDB Atlas, Vultr, and GoDaddy Registry).

## 3. Database Architecture (Dual-Database Split)
### A. Tiger Data (`TIGER_POSTGRES_URL`) — High-Frequency Time-Series
1. **`energy_telemetry` hypertable:**
   - Columns: `time TIMESTAMPTZ NOT NULL`, `home_id TEXT NOT NULL`, `watts DOUBLE PRECISION NOT NULL`, `grid_carbon_gco2_kwh DOUBLE PRECISION NOT NULL`, `is_peak_event BOOLEAN DEFAULT FALSE`.
   - Convert via `SELECT create_hypertable('energy_telemetry', 'time', if_not_exists => TRUE);`
   - Enable compression (`timescaledb.compress = true`) and add a compression policy.
2. **`telemetry_1min_cagg` Continuous Aggregate:**
   - `CREATE MATERIALIZED VIEW telemetry_1min_cagg WITH (timescaledb.continuous) AS SELECT time_bucket('1 minute', time) AS bucket, home_id, AVG(watts) AS avg_watts, AVG(grid_carbon_gco2_kwh) AS avg_carbon, SUM(watts) / 60000.0 AS kwh_used FROM energy_telemetry GROUP BY bucket, home_id WITH NO DATA;`
   - Refresh automatically via `add_continuous_aggregate_policy` and query with real-time aggregation enabled (`timescaledb.materialized_only = false`) so charts show live second-by-second data alongside historical rollups.

### B. MongoDB Atlas (`MONGODB_URI`) — Unstructured Documents & User State
1. `users` collection: `home_id`, `display_name`, `solana_wallet`, `total_co2_saved_grams`, `total_sol_earned`, `streak_days`.
2. `parsed_bills` collection: stores the structured JSON extracted by Gemini Vision from uploaded utility bills (utility provider, rate per kWh, peak hours, top appliances, personalized reduction plan).
3. `payout_receipts` collection: stores verified peak-event reductions, wattage drop %, CO₂ avoided, and Solana Devnet transaction signatures.

## 4. Core Features & API Endpoints
1. **Background Telemetry Simulator (`GET /api/telemetry/live`):**
   - Continuously inserts 1-second readings into Tiger Data for a demo household (`home_demo_01`) plus 4 neighborhood peers.
   - Queries `telemetry_1min_cagg` and recent raw points to power live frontend charts of household Watts, Grid Carbon Intensity (gCO₂/kWh), and cumulative CO₂ avoided.
2. **Gemini Utility Bill & Appliance Analyzer (`POST /api/gemini/analyze-bill`):**
   - Accepts an uploaded image (or uses `assets/sample_bill.png` via `POST /api/gemini/analyze-sample`).
   - Calls Gemini (`gemini-3.8-flash`) with structured output to extract `utility_name`, `kwh_rate_usd`, `monthly_kwh`, `peak_window`, and 3 actionable load-shifting tips with estimated monthly CO₂ & dollar savings.
   - Saves the document to MongoDB Atlas and returns it to the frontend.
3. **One-Click Demo Loop: "Trigger Dirty Grid Spike" (`POST /api/demo/trigger-spike` & `POST /api/demo/reduce-load`):**
   - **Step 1 (`trigger-spike`):** Spikes simulated regional grid carbon intensity from `210` to `680 gCO₂/kWh` and flags an active Peak Alert banner on the dashboard.
   - **Step 2 (`reduce-load`):** User clicks **"Shift Load Now (Pause EV & AC)"** in the UI. Household draw drops from `3,400W` to `850W` (a 75% reduction), writes the drop to Tiger Data, calculates exact grams of CO₂ avoided, triggers a real Solana Devnet micro-payout (`0.001 SOL`) to the user's wallet, logs the receipt in MongoDB Atlas, and displays a live clickable **Solscan Devnet Receipt** card.
4. **Assets Generator:**
   - Include a script that generates `assets/sample_bill.png` (a clean, realistic mock electric utility bill image using Pillow) so the Gemini Vision analyzer works out-of-the-box without external files.

## 5. Verification Script (`backend/verify_all.py`)
Write an automated script `backend/verify_all.py` that:
1. Connects to Tiger Data, creates the hypertable and `telemetry_1min_cagg` continuous aggregate, inserts sample rows, and verifies a `SELECT` query returns aggregated data.
2. Connects to MongoDB Atlas and verifies read/write to `users` and `parsed_bills`.
3. Generates `assets/sample_bill.png` and sends it to the Gemini API, asserting valid structured JSON output.
4. Executes a test Solana Devnet micro-transfer and prints the confirmed transaction signature.
# GridPulse

GridPulse turns a home into a virtual power plant node. It records second-by-second wattage against regional grid carbon intensity, reads a utility bill with Gemini Vision, and pays a 0.001 SOL micro-bounty on Solana Devnet when the household cuts load during a dirty-grid spike.

The evening peak (about 4 PM–9 PM) is when fossil peaker plants spin up and each kilowatt-hour is dirtier. GridPulse makes that window visible, then pays for shifting EV charging and air conditioning out of it.

## Architecture

| Piece | Where it lives | Role |
| --- | --- | --- |
| FastAPI | `backend/main.py` | Live telemetry, bill analysis, spike demo, payouts |
| Tiger Data | `backend/tiger.py` | `energy_telemetry` hypertable and `telemetry_1min_cagg` |
| MongoDB Atlas | `backend/mongo.py` | Users, parsed bills, payout receipts, leaderboard |
| Gemini | `backend/gemini_bill.py` | Structured bill extraction |
| Solana Devnet | `backend/solana_payout.py` | 0.001 SOL transfer and Solscan receipt |
| Next.js dashboard | `frontend/` | Charts, spike buttons, bill analyzer |
| Vultr edge | `deploy/` | Dockerfile, Compose, Caddy HTTPS |

Telemetry for `home_demo_01` and four neighborhood peers is inserted every second. Carbon starts at 210 gCO₂/kWh and household load at 3,400 W. **Trigger Dirty Grid Spike** jumps carbon to 680 gCO₂/kWh. **Shift Load Now (Pause EV & AC)** drops the home to 850 W (a 75% reduction), writes that drop to Tiger Data, computes grams avoided as `(watts_reduced / 1000) * hours * carbon`, and sends 0.001 SOL.

## Prerequisites

- Python 3.11 or newer
- Node.js 22
- Sponsor credentials in `.env` (see `.env.example`)

Tiger Data, MongoDB Atlas, Gemini, and Solana must be reachable from the machine running the API. Atlas and Tiger Cloud both need this machine’s IP on their allowlists, or an allow-all rule for a demo.

## Setup

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\python -m pip install -r backend\requirements.txt
.venv\Scripts\python -m backend.sample_bill
```

macOS or Linux:

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m backend.sample_bill
```

`backend/sample_bill.py` writes `assets/sample_bill.png`, a Pioneer Valley Electric statement the vision model can read without any outside image. The API also generates that file the first time it is missing.

If `SOLANA_PRIVATE_KEY` is empty, the backend creates a Devnet keypair, saves it into `.env`, and requests an airdrop the first time a payout needs funds. Leave the key blank on a fresh checkout.

The Atlas URI may still contain the angle brackets copied from the connection-string placeholder. `backend/config.py` strips that wrapper and percent-encodes the password before Motor connects.

### Run the API

From the repository root:

```powershell
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Startup creates the hypertable, compression policy, and continuous aggregate, seeds Atlas profiles, and starts the one-second simulator. Interactive docs are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### Run the dashboard

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Next.js proxies `/api/*` to `http://127.0.0.1:8000` (override with `API_PROXY_URL`).

## Demo loop

1. Watch household watts and grid carbon on the live chart. Readings come from Tiger Data, including the 1-minute continuous aggregate table under the buttons.
2. Click **Trigger Dirty Grid Spike**. Carbon moves from 210 to 680 gCO₂/kWh and the peak-alert banner turns on.
3. Click **Shift Load Now (Pause EV & AC)**. Load falls from 3,400 W to 850 W. The API writes the drop, scores CO₂ avoided over the spike window (at least one minute), sends 0.001 SOL to the demo user’s Devnet wallet, and stores the receipt in Atlas.
4. Open the **Solscan Devnet Receipt** card. The link is `https://solscan.io/tx/<signature>?cluster=devnet`.
5. Click **Analyze sample bill** (or upload another bill photo). Gemini returns the utility, rate, monthly kWh, peak window, and three load-shifting tips. The document is saved in `parsed_bills`.

The neighborhood board reads `GET /api/leaderboard`. The profile card reads `GET /api/user/home_demo_01`.

## Verify every sponsor

With the API stopped or still running (the script talks to the databases directly):

```powershell
.venv\Scripts\python backend\verify_all.py
```

The script exits `0` only after all four checks pass:

1. Tiger Data: create the hypertable and `telemetry_1min_cagg`, insert rows, and `SELECT` aggregated buckets.
2. MongoDB Atlas: write and read `users` and `parsed_bills`.
3. Gemini: generate `assets/sample_bill.png`, send it to the vision model, and require the structured bill JSON (3 tips).
4. Solana Devnet: confirm a 0.001 SOL transfer and print the signature plus Solscan URL.

`gemini-3.8-flash` is tried first. If it is busy, the same request is retried, then sent to `gemini-2.5-flash`. When both of those return a model or capacity error, the call continues to `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`, and `gemini-3.1-flash-lite`.

## Troubleshooting

**Atlas says `bad auth`.** The app reaches the cluster, then MongoDB rejects the username or password in `MONGODB_URI`. Angle brackets copied from the Atlas placeholder are stripped before connecting. Replace the URI with the current database-user password from Atlas Database Access and rerun `verify_all.py`.

**Atlas closes the TLS handshake.** From this network, TCP to the shard on port 27017 connects and then the peer sends no TLS bytes (`UNEXPECTED_EOF_WHILE_READING`). The public address seen during setup was `128.119.202.137`. In Atlas Network Access, allow that address (or `0.0.0.0/0` for a demo), confirm the cluster is not paused, and rerun the verifier from a network that allows outbound port 27017. Tiger Data on its own port is unaffected.

**Devnet airdrop returns HTTP 410.** `requestAirdrop` on `api.devnet.solana.com` is closed. The treasury public key already saved for this checkout is `7at4TP9v2QrQDtRP7T2dAmre9Ap2REepVFSS7NcZhvzt`. Request devnet SOL for that address at [https://faucet.solana.com](https://faucet.solana.com), then run the verifier again. The 0.001 SOL transfer itself is a normal confirmed Devnet transaction. Other public Devnet RPCs either return the same 410 or reject `requestAirdrop`.

## Deploy on a Vultr VPS

The stack is one Compose project: the API, the Next.js app, and Caddy. Caddy terminates HTTPS for `PUBLIC_DOMAIN`.

1. Create an Ubuntu VPS on Vultr and note its public IPv4 address.
2. In GoDaddy, add an A record for `gridpulse.xyz` (the domain in `PUBLIC_DOMAIN`) pointing at that address. Wait until the record resolves.
3. Install Docker and the Compose plugin on the VPS. Clone this repo, copy `.env`, and fill in the same sponsor credentials. Allow the VPS address in Atlas and Tiger Cloud.
4. From the repo root:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

Caddy loads `deploy/Caddyfile`, obtains a certificate for `{$PUBLIC_DOMAIN}`, sends `/api/*` to the API, and sends everything else to Next.js. Open `https://gridpulse.xyz`.

`deploy/Dockerfile` has two targets. `backend` is Python 3.11 and uvicorn. `frontend` is a Next.js production build. Compose selects the target per service.

## How we used every sponsor

### Cursor

The application was implemented in Cursor from `SPEC.md` and `AGENTS.md`: FastAPI modules, the Next.js dashboard, and the Vultr deploy files live in one workspace so the telemetry API and the buttons that call it stay in sync. `.env` stays local and is gitignored.

### Grok Bot PR and issue workflow

Grok Bot is the review path for this repo. File an issue that names the failing sponsor check or the dashboard step (spike, shift, or bill). Ask Grok Bot to trace it through `backend/verify_all.py` and the matching route in `backend/main.py`, then open a pull request that changes only that path. Review comments stay on the PR; the bot updates the branch until `python backend/verify_all.py` exits 0. That keeps sponsor integrations from drifting into stubbed responses.

### Gemini API

`backend/gemini_bill.py` uses the official `google-genai` SDK (`from google import genai`). `POST /api/gemini/analyze-bill` accepts an upload. `POST /api/gemini/analyze-sample` sends `assets/sample_bill.png`. Both calls use Pydantic `response_schema` so the model must return `utility_name`, `kwh_rate_usd`, `monthly_kwh`, `peak_window`, and exactly three tips with monthly CO₂ and dollar savings. The model order is `gemini-3.8-flash`, then `gemini-2.5-flash`, then later flash models if those two are unavailable. Parsed documents are stored in the Atlas `parsed_bills` collection.

### Tiger Data

`backend/tiger.py` uses `psycopg` (v3) against `TIGER_POSTGRES_URL`. `energy_telemetry` is a hypertable on `time` (`create_hypertable(..., if_not_exists => TRUE)`) with compression enabled and a 7-day compression policy. `telemetry_1min_cagg` is a real continuous aggregate:

```sql
SELECT time_bucket('1 minute', time) AS bucket,
       home_id,
       AVG(watts) AS avg_watts,
       AVG(grid_carbon_gco2_kwh) AS avg_carbon,
       SUM(watts) / 60000.0 AS kwh_used
FROM energy_telemetry
GROUP BY bucket, home_id
```

`add_continuous_aggregate_policy` refreshes it, and `timescaledb.materialized_only = false` lets the dashboard chart the current minute alongside the rollup. `GET /api/telemetry/live` reads both the raw hypertable and the aggregate.

### Solana

`backend/solana_payout.py` uses `solders` and `solana` against `SOLANA_RPC_URL` (Devnet). An empty `SOLANA_PRIVATE_KEY` generates a treasury keypair, writes it to `.env`, and requests a Devnet airdrop when the balance cannot cover 0.001 SOL plus fees. A verified shift calls `transfer` for 1,000,000 lamports to the user’s wallet, waits until the signature is confirmed, and returns `https://solscan.io/tx/<sig>?cluster=devnet`. The signature is also stored on the Atlas payout receipt.

### MongoDB Atlas

`backend/mongo.py` uses Motor. The database name comes from `MONGODB_DB_NAME` (default `gridpulse`).

- `users`: `home_id`, `display_name`, `solana_wallet`, `total_co2_saved_grams`, `total_sol_earned`, `streak_days`
- `parsed_bills`: Gemini’s structured bill JSON
- `payout_receipts`: drop percent, CO₂ avoided, and the Devnet signature

Profiles for the demo home and four peers are created on startup. The leaderboard sorts `users` by CO₂ avoided.

### Vultr

`deploy/docker-compose.yml` is the one-command production layout for an Ubuntu VPS: API, Next.js, and Caddy, with the API healthcheck gating the frontend. The image is built from `deploy/Dockerfile`. Point the VPS security group at 80 and 443 and let Caddy handle certificates. No sponsor response is baked into the image; the container reads `.env` at runtime.

### GoDaddy Registry

`PUBLIC_DOMAIN` is `gridpulse.xyz`, registered at GoDaddy. Caddy’s site address is `{$PUBLIC_DOMAIN}`, so the certificate and the public URL follow that name. DNS is an A record from the GoDaddy zone to the Vultr VPS. The dashboard and `/api/*` then share one HTTPS origin.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Process is up |
| GET | `/api/telemetry/live` | Raw points, 1-minute aggregate, peer watts, peak flags |
| GET | `/api/leaderboard` | Atlas homes ranked by CO₂ avoided |
| GET | `/api/user/{home_id}` | Profile plus latest payout receipt |
| POST | `/api/gemini/analyze-sample` | Vision on `assets/sample_bill.png` |
| POST | `/api/gemini/analyze-bill` | Vision on an uploaded image |
| GET | `/api/gemini/sample-bill` | The generated bill image |
| POST | `/api/demo/trigger-spike` | Carbon 210 → 680, peak alert on |
| POST | `/api/demo/reduce-load` | 3,400 W → 850 W, CO₂ math, 0.001 SOL, receipt |

## Project layout

```
backend/    FastAPI app, sponsor clients, verify_all.py
frontend/   Next.js App Router dashboard
deploy/     Dockerfile, docker-compose.yml, Caddyfile
assets/     sample_bill.png (generated)
```

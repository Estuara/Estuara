# Estuara

**Marine electrification planning intelligence for Lagos waterways.**

Estuara uses real vessel movement evidence, geospatial analysis and renewable-energy modelling to help identify suitable electric ferry routes and plan charging infrastructure. This repository begins with the operational foundation: converting ferry GPS observations into defensible trips, stops and route geometry.

The pilot geography is **Lagos State's water-transport network**. It is not restricted to any single LGA. Field collection can begin on accessible routes, but the data model, analysis and transition planning must represent the wider Lagos waterways system.

## What works now

- GPS telemetry CSV upload
- flexible recognition of common column names
- coordinate, timestamp, vessel and speed validation
- explicit source status and provenance
- multi-vessel trip reconstruction
- threshold-based stop detection
- distance, duration and speed summaries
- unsupervised landing-point discovery (DBSCAN over detected stops), producing
  unverified candidates for later human review
- route, stop and landing-point GeoJSON
- interactive MapLibre workspace
- a simulated Lagos pilot dataset, visibly labelled as simulated

## Repository

```text
apps/api       FastAPI mobility analysis service
apps/web       Next.js and MapLibre product interface
data/samples   clearly labelled sample telemetry
docs           architecture, data contract and team workflow
```

## Run locally

Requirements: Python 3.11+, Node.js 20+ and pnpm.

### 1. API

```bash
cd apps/api
uv sync --dev
uv run uvicorn estuara_api.main:app --reload --port 8000
```

API documentation is available at `http://localhost:8000/docs`.

### 2. Web app

In a second terminal:

```bash
cd apps/web
pnpm install
pnpm dev
```

Open `http://localhost:3000`. The page automatically analyses the supplied simulated pilot CSV through the real API. Use **Import GPS data** to analyse another CSV.

### Windows PowerShell

The commands above work in the VS Code PowerShell terminal after installing `uv` and `pnpm`. If needed:

```powershell
winget install --id=astral-sh.uv -e
npm install -g pnpm
```

## Test and verify

```bash
cd apps/api
uv run pytest
uv run ruff check src tests

cd ../web
pnpm lint
pnpm typecheck
pnpm build
```

## Deploy the team preview to Cloudflare

The hosted preview runs as a single Cloudflare Worker: the exported Next.js
interface is served by Workers Static Assets, and `/api/*` runs the FastAPI
service on Python Workers. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for
the rationale, requirements (Workers Paid) and exact commands.

The current Cloudflare account is on the Free plan, which cannot host the
Python analysis Worker (compressed-bundle and CPU limits), so no preview URL
is live yet. The Worker itself is fully verified one step short of that: the
bundle uploads, and the complete sample analysis plus an 11-point browser
smoke test pass against `wrangler dev`, which uses the production runtime.

## Analyse a file without the interface

```bash
curl -X POST http://localhost:8000/api/v1/mobility/analyse \
  -F "file=@data/samples/lagos_pilot_simulated.csv" \
  -F "data_status=simulated"
```

Read [the data contract](docs/DATA_CONTRACT.md) before collecting the first pilot tracks. Read [the landing-point discovery walkthrough](docs/learning/01_landing_point_discovery.md) to understand how recurring stops become unverified landing-point candidates. The next product slice is a human-verification registry that separates verified terminals, informal landing points, temporary operational stops and rejected detections.

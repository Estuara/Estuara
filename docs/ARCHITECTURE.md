# Estuara architecture

## Product boundary

Estuara is marine electrification planning intelligence. The first release establishes trusted movement evidence. It is not a ticketing app, navigation app, vessel marketplace, environmental super-platform, or boat manufacturer.

## Pilot geography

The pilot covers Lagos State's water-transport network. No LGA is hardcoded as the pilot. Individual field exercises may begin on selected accessible corridors, but route intelligence, terminal evidence, energy scenarios and charging planning must scale across Lagos waterways.

## First vertical slice

```text
GPS CSV
  -> schema resolution
  -> validation and provenance
  -> per-vessel chronological stream
  -> trip reconstruction
  -> stop detection
  -> spatial clustering (landing-point discovery)
  -> route, stop and landing-point GeoJSON
  -> API response
  -> web map and operational table
```

This slice implements the **Observe** stage of Observe -> Analyse -> Plan -> Transition,
plus the first step into **Analyse**: turning recurring stop evidence into
unverified landing-point candidates.

## Applications

- `apps/api`: FastAPI service and mobility-domain logic.
- `apps/web`: Next.js decision-support interface using MapLibre.

## Current analytical rules

- A new trip begins after a configurable observation gap. The default is 30 minutes.
- A detected stop is a continuous low-speed segment. The defaults are no more than 1.5 knots for at least 2 minutes.
- Distance is calculated between consecutive WGS 84 coordinates using the haversine formula.
- These rules are transparent baseline methods, not trained models.
- A detected stop is evidence for review, not an automatically verified terminal.
- Landing-point candidates are discovered by DBSCAN clustering (haversine
  distance) over detected stops across trips and vessels — implemented with
  NumPy so the algorithm runs in the Cloudflare Pyodide runtime, with
  scikit-learn kept as a dev-only reference to verify equivalence. This is
  unsupervised clustering, not supervised training with labelled targets, and it produces
  no verified terminal — every candidate is `unverified` until a human review
  process confirms it. See `docs/learning/01_landing_point_discovery.md`.

## Data status

Every analysis identifies its source as one of: `real`, `user-provided`, `public`, `estimated`, `modelled`, or `simulated`. The supplied Lagos pilot data is explicitly simulated.

## Deliberately deferred

- accounts, teams and permissions
- PostGIS persistence and object storage
- live IoT ingestion and offline device synchronisation
- verified terminal registry (human verification of landing-point candidates)
- vessel and propulsion specifications
- energy and battery modelling
- route suitability scoring
- charging-hub optimisation
- solar and storage scenarios

These require the mobility evidence model first. They should be added as coherent slices rather than empty navigation pages.

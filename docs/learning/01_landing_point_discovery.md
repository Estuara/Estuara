# 01 — Landing-point discovery

This is Estuara's first genuine GeoAI capability: a fitted, unsupervised model
that turns repeated vessel stops into candidate landing-point locations. It
lives in [`apps/api/src/estuara_api/mobility/landing_points.py`](../../apps/api/src/estuara_api/mobility/landing_points.py)
and is wired into the existing analysis pipeline in
[`analysis.py`](../../apps/api/src/estuara_api/mobility/analysis.py).

## What we built

`analyse_csv` already turned raw GPS rows into trips and threshold-detected
stops (`_detect_stops`). A stop is one vessel sitting still for a while during
one trip — it says nothing about *place*. If the same location keeps showing
up across many different stops, many different trips, and ideally many
different vessels, that recurrence is evidence of a real landing location:
a terminal, jetty, or informal stopping point that isn't yet in any registry.

`LandingPointClusterer` finds that recurrence. It fits a DBSCAN model over all
detected stops in an analysis and groups stops that are geographically close
into "landing-point candidates." Stops that never recur near enough to others
are left as noise — they stay evidence of a single event, not a place.

This is explicitly **not** a verified terminal registry. Every candidate
carries `verification_status: "unverified"` and nothing in the pipeline
upgrades that status automatically. Human review (a later capability) is what
turns "candidate" into "terminal."

## Input and output

Input — one `StopObservation` per detected stop (built from `StopSummary` in
`_discover_landing_points`):

```python
StopObservation(
    stop_id="EST-01-T003-S02",
    trip_id="EST-01-T003",
    vessel_id="EST-01",
    latitude=6.4550,
    longitude=3.4050,
    arrived_at=datetime(...),
    departed_at=datetime(...),
    dwell_minutes=6.2,
)
```

Output — one `LandingPointCandidate` per discovered cluster:

```python
LandingPointCandidate(
    candidate_id="LP-001",
    latitude=6.4550048,
    longitude=3.4050046,
    visit_count=54,
    trip_count=50,
    vessel_count=4,
    mean_dwell_minutes=6.05,
    first_observed_at=datetime(2026, 9, 8, 0, 9, 25, tzinfo=UTC),
    last_observed_at=datetime(2026, 9, 12, 2, 55, 24, tzinfo=UTC),
    evidence_score=1.0,
    verification_status="unverified",
)
```

Running the supplied simulated Lagos dataset through `analyse_csv` with
default parameters (`radius_meters=120`, `min_visits=2`) over its 152
detected stops produced **4 candidates and 1 noise point** — see the results
section at the end of this run for the full numbers.

## Concepts I need to understand

- **Observation** — one recorded fact: here, one detected stop (a row with
  lat/lon, time, vessel, dwell).
- **Feature** — a measurable property of an observation used by a model.
  For DBSCAN, the feature is just the stop's coordinate, converted to
  radians (see below).
- **Unsupervised learning** — finding structure in data with no labelled
  "correct answers." DBSCAN never sees a confirmed terminal; it only sees
  coordinates and infers where they cluster.
- **Clustering** — grouping observations so members of a group are more
  similar (here: geographically closer) to each other than to non-members.
- **DBSCAN** (Density-Based Spatial Clustering of Applications with Noise) —
  a clustering algorithm that grows a cluster from a point if enough other
  points (`min_samples`) lie within a distance (`eps`) of it, and repeats
  from those neighbors. Points that never satisfy this density condition are
  left unclustered ("noise"). Unlike k-means, it does not need the number of
  clusters specified up front — which matters here because we don't know
  how many landing points exist.
- **Epsilon / cluster radius** — the `eps` parameter: the maximum distance
  between two stops for them to be considered part of the same
  neighborhood. Exposed to the product as `radius_meters`.
- **Minimum samples** — the `min_samples` parameter: how many stops must be
  within `eps` of a point before it counts as a dense region. Exposed as
  `min_visits`.
- **Haversine distance** — the great-circle distance between two points on a
  sphere given their latitude/longitude. Needed because degrees of latitude
  and longitude are not fixed-size units of distance on Earth's surface.
- **Cluster** — a group of stops DBSCAN judged dense enough to represent one
  location. Becomes one `LandingPointCandidate`.
- **Noise** — a stop DBSCAN could not attach to any dense cluster. Preserved,
  not discarded, and not reported as a candidate.
- **Centroid** — the mean latitude/longitude of a cluster's member stops;
  used as the candidate's representative coordinate.
- **Fitting** — running the algorithm over training data to produce a
  result. For DBSCAN there is no separate "training" and "inference" step —
  `fit()` computes the final cluster assignment directly (technically
  `fit_predict`, since DBSCAN has no notion of predicting on new, unseen
  points from an already-fitted model).
- **Evaluation** — measuring how correct a model's output is. We can't do
  this yet — see below.

## Walk through the real class

Open [`landing_points.py`](../../apps/api/src/estuara_api/mobility/landing_points.py).

- **Why coordinates are converted to radians before clustering**
  (`_cluster_labels`): scikit-learn's `haversine` metric expects inputs in
  radians and returns distances in radians. `eps` is therefore also
  converted: `eps_radians = radius_meters / EARTH_RADIUS_METERS`. This is
  the one non-obvious geospatial step in the whole file — if you skip it and
  hand DBSCAN raw lat/lon degrees with a Euclidean metric, "120 meters" would
  silently mean something different at every latitude, and north-south vs
  east-west distances would be distorted differently.

- **Why `min_visits` has a floor of 2**
  (`__init__`): a single stop cannot be a "recurring pattern" by definition —
  DBSCAN's own `min_samples` semantics require at least this to form a
  cluster at all. Rejecting `min_visits < 2` at construction time turns a
  silently-meaningless configuration into an explicit `LandingPointConfigError`.

- **Why the model refuses results before `fit()`**
  (`_require_fitted`, called from `get_candidates`/`get_report`): a class
  that returns empty lists or `None` when unfitted invites callers to
  mistake "not run yet" for "ran and found nothing." Raising
  `ModelNotFittedError` makes the two cases impossible to confuse.

- **Why `evidence_score` is not called "confidence"**
  (`_evidence_score`): it is a hand-written, explainable heuristic —
  0.6 × (visits scaled against `min_visits`) + 0.4 × (distinct vessels,
  capped at 3) — not a statistically calibrated probability. It exists to
  help a human reviewer triage which unverified candidates to check first.
  Calling it "confidence" would imply it was validated against ground truth,
  which doesn't exist yet.

- **Why candidates are built by iterating `sorted(set(labels) - {-1})`**
  (`_build_candidates`): DBSCAN labels noise as `-1` and clusters as
  `0, 1, 2, ...` in an order that has no product meaning. Excluding `-1` and
  reassigning stable `LP-001`, `LP-002`, ... IDs by sorted rank keeps output
  deterministic and keeps noise out of the candidate list entirely.

## What changes when parameters change

- **Radius too small** — real recurring stops that are a few meters apart
  (GPS jitter, slightly different mooring spots) get split into separate,
  smaller clusters or pushed into noise. You'd undercount visits per real
  landing point and possibly miss it as a candidate entirely if visits per
  fragment fall below `min_visits`.
- **Radius too large** — two genuinely different nearby landing points (e.g.
  a jetty and an informal spot 300 m down the shore) can merge into one
  candidate, hiding real structure and producing a misleading centroid
  between two actual places.
- **Minimum visits too low** — a stop that happened to occur near just one
  or two other unrelated stops (coincidence, not a real pattern) can become
  a "candidate," producing false positives that waste reviewer time.
- **Minimum visits too high** — genuinely real but less-frequently-used
  landing points (informal or seasonal stops) never accumulate enough visits
  to be reported, so real locations disappear from the results (false
  negatives).

## How we will evaluate it later

Right now there is nothing to score against — no verified Lagos terminal
list exists in the system. Once the planned human-verification registry
exists (see "next capability" below), it will support:

- **Candidate precision** — of all candidates the model proposed, what
  fraction were confirmed as real landing locations by reviewers?
- **Terminal recall** — of all known real landing locations, what fraction
  did the model surface as a candidate at all?
- **Spatial-offset measurement** — how far is a candidate's centroid from
  the verified location's true coordinate? This tells us if `radius_meters`
  is systematically too coarse or too fine.
- **Parameter sensitivity** — re-running `fit()` across a grid of
  `radius_meters` / `min_visits` combinations against the same verified set,
  to see which settings maximize precision and recall together.
- **Stability across dates and vessels** — re-running on disjoint time
  windows or vessel subsets to check whether the same real locations keep
  getting discovered, or whether results are an artifact of one vessel's
  particular route.

## My practical exercise

1. **Change a parameter and predict first.** In
   `apps/api/src/estuara_api/mobility/analysis.py`, `analyse_csv`'s default
   `landing_radius_meters=120.0` — before running anything, predict whether
   raising it to `400.0` will produce more or fewer candidates on the
   simulated dataset, and whether cluster sizes (visit counts) will grow or
   shrink. Then run:

   ```bash
   cd apps/api
   uv run python -c "
   from pathlib import Path
   from estuara_api.mobility.analysis import analyse_csv
   content = Path('../../data/samples/lagos_pilot_simulated.csv').read_bytes()
   result = analyse_csv(content, 'lagos_pilot_simulated.csv', data_status='simulated', landing_radius_meters=400.0)
   print(result.landing_point_report)
   for c in result.landing_points:
       print(c.candidate_id, c.visit_count, c.vessel_count)
   "
   ```

   Compare the output to your prediction.

2. **Add one isolated stop and check it becomes noise.** Open
   `apps/api/tests/test_landing_points.py` and look at
   `test_isolated_stop_remains_noise`. Add a *new* stop of your own — pick
   coordinates at least a kilometer from `GROUP_A` and `GROUP_B` — to that
   test's `stops` list, and assert `report.noise_point_count` increases by
   one. Run:

   ```bash
   cd apps/api
   uv run pytest tests/test_landing_points.py -v
   ```

3. **Explain why a candidate is not automatically a verified terminal.**
   Write two or three sentences (in your own words, doesn't need to go
   anywhere) covering: what DBSCAN actually observed (coordinates recurring
   in GPS traces), what it did *not* observe (whether there is a real jetty,
   whether it's safe or legal to land there, whether it's seasonal), and why
   `verification_status` stays `"unverified"` until a person checks it in
   the field or against other evidence.

## Results from this run (simulated Lagos pilot dataset)

Default parameters (`radius_meters=120`, `min_visits=2`) over
`data/samples/lagos_pilot_simulated.csv`:

| Metric | Value |
| --- | --- |
| Vessels | 6 |
| Trips | 80 |
| Detected stops (training points) | 152 |
| Clusters discovered | 4 |
| Noise points | 1 |
| Candidates | LP-001 (54 visits, 4 vessels), LP-002 (34 visits, 3 vessels), LP-003 (28 visits, 2 vessels), LP-004 (35 visits, 3 vessels) |

All four candidates are `unverified` and the dataset itself remains labelled
`simulated` end to end — through `Provenance.data_status`, the API response,
and the UI's status badge.

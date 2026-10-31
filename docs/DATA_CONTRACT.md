# GPS telemetry data contract v0.1

Estuara accepts UTF-8 CSV files. One row represents one timestamped vessel position.

| Canonical field | Required | Accepted aliases | Unit / format |
| --- | --- | --- | --- |
| `timestamp` | Yes | `time`, `datetime`, `recorded_at`, `date_time` | ISO 8601 preferred |
| `latitude` | Yes | `lat`, `y`, `gps_latitude` | decimal degrees, WGS 84 |
| `longitude` | Yes | `lon`, `lng`, `long`, `x`, `gps_longitude` | decimal degrees, WGS 84 |
| `vessel_id` | Yes | `vessel`, `boat_id`, `boat`, `device_id` | stable text identifier |
| `speed` | No | `speed_knots`, `sog`, `velocity` | knots |

## Two kinds of unusable data

Rows are classified as **rejected** (unusable at all) or **flagged** (retained,
but marked as suspect). Both are counted per reason in the API response, and
flagged rows keep their original values in the quality report.

### Rejected rows

These cannot contribute to any analysis:

- no parseable timestamp;
- coordinates outside the globe (latitude outside ±90, longitude outside ±180);
- missing or empty vessel identifier;
- negative reported speed (a contradiction in a speed-over-ground reading);
- duplicate `vessel_id` + `timestamp` rows are removed: exact duplicates
  (same coordinates and speed) are counted once as duplication; conflicting
  duplicates (differing coordinates or speed) are counted as conflicts and the
  later row is discarded.

### Flagged rows (plausibility screening)

Well-formed rows that are physically suspicious are __not__ dropped. Each
affected row gets one primary reason, its data-row number and its original
value. The configurable threshold is `max_plausible_speed_knots`
(default 40 kn, accepted range 20-80 kn; see the rationale in
`apps/api/src/estuara_api/mobility/quality.py`). A flag means "inspect this",
never "proven invalid".

- **Implausible reported speed** — the reported speed exceeds the threshold.
  The position is kept; the speed used for analysis is replaced with the
  speed derived from neighbouring fixes (`speed_source: derived_replacement`).
  Reported values are preserved in provenance and in the quality report.
- **Position jump** — from the previous *trusted* fix, the fix would require
  more than the threshold speed **and** a displacement of more than 250 m
  (`min_position_jump_km = 0.25`). Both conditions must hold. The row is excluded
  from trip geometry, distance and stop detection so one GPS glitch cannot
  inflate a trip, but the record itself is retained and reported.
  Comparison is against the last *trusted* fix, not simply the previous row,
  so the fix after a glitch is not blamed for the glitch. If three or more
  consecutive fixes all disagree with the trusted fix but agree with each
  other, the track is treated as having genuinely moved and continuity is
  accepted with a warning (`jump_reset_after = 3`).

Thresholds are screening heuristics, not vessel specifications. The 40 kn
default leaves headroom above typical Lagos commuter-ferry top speeds
(~8-25 kn) while catching spikes such as the 65 kn value injected into the
simulated sample; the 250 m displacement floor keeps ordinary GNSS jitter
between closely spaced fixes from being flagged.

## Speed derivation when speed is absent

When a row has no speed value (no speed column at all, a missing value, or a
non-numeric value), speed is derived from consecutive fixes of the same
vessel:

`speed = haversine distance(fix n-1, fix n) / elapsed time`, in knots.

- Derivation is only performed between two fixes of the same vessel within
  `trip_gap_minutes` (default 30): averaging the speed over a long observation
  gap would say nothing about the vessel's speed at either moment.
- A fix with no usable predecessor (a vessel's first fix, or the first fix
  after a trip gap) borrows the next derived value (backfill) rather than
  being forced to zero, since a moving vessel's first observation is not
  evidence that it was stationary.
- Every derivation is recorded as a transformation and counted as
  `speeds_derived` in the response; each row's speed is labelled
  `reported` or `derived` in provenance.

## Processing and provenance

- Raw uploaded files are never rewritten by the analysis endpoint. All
  screening and derivation happens in memory; original values survive in the
  quality report and in `reported_speed` in provenance.
- The response reports `input_rows`, `rejected_rows`, `flagged_rows`,
  `deduplicated_rows`, `speeds_derived`, `speeds_replaced` and
  `excluded_from_geometry_rows`, together with a human-readable list of
  transformations. These counts are what the web interface displays, so the
  interface always reflects the API's actual accounting.
- Deployment-wide limits are read from configuration (`ESTUARA_MAX_UPLOAD_MB`,
  default 50 locally; `ESTUARA_MAX_ROWS`, default 200000) — see
  `docs/DEPLOYMENT.md` for the Cloudflare-specific values.

## Production additions

Device telemetry will later require device ID, accuracy, course over ground, fix status, sequence number and upload/batch metadata. Hardware payloads must support store-and-forward operation when connectivity is lost.

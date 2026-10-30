# GPS telemetry data contract v0.1

Estuara accepts UTF-8 CSV files. One row represents one timestamped vessel position.

| Canonical field | Required | Accepted aliases | Unit / format |
| --- | --- | --- | --- |
| `timestamp` | Yes | `time`, `datetime`, `recorded_at`, `date_time` | ISO 8601 preferred |
| `latitude` | Yes | `lat`, `y`, `gps_latitude` | decimal degrees, WGS 84 |
| `longitude` | Yes | `lon`, `lng`, `long`, `x`, `gps_longitude` | decimal degrees, WGS 84 |
| `vessel_id` | Yes | `vessel`, `boat_id`, `boat`, `device_id` | stable text identifier |
| `speed` | No | `speed_knots`, `sog`, `velocity` | knots |

## Validation behaviour

- Invalid timestamps and coordinates outside geographic bounds are rejected and counted.
- Duplicate timestamps for the same vessel are removed and reported.
- Negative speeds are rejected.
- When speed is absent, it is set to zero and the transformation is recorded. This permits ingestion but makes stop analysis unreliable; production hardware should always report speed over ground.
- Raw uploaded files are not rewritten by the analysis endpoint.

## Production additions

Device telemetry will later require device ID, accuracy, course over ground, fix status, sequence number and upload/batch metadata. Hardware payloads must support store-and-forward operation when connectivity is lost.


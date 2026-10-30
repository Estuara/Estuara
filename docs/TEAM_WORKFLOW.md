# Team workflow

## Current ownership

| Area | Lead | First responsibility |
| --- | --- | --- |
| Product and geospatial system | Razaq Muiz Olasunkanmi | data contract, mobility analysis, mapping, field methodology and product decisions |
| Hardware and electrical system | Imoleayo Babatunde | GNSS/IoT device design, power budget, communications, enclosure and later charging/energy assumptions |
| Shared | Team | operator access, pilot planning, evidence review, GSC submission and demonstrations |

## Working rule

Every change should answer a verified product requirement. Keep measured, public, estimated, modelled and simulated data distinguishable in code and presentations.

## Recommended branches

- `main`: reviewed, runnable work.
- `feature/mobility-*`: route, trip or map changes.
- `feature/device-*`: telemetry and hardware changes.
- `feature/energy-*`: vessel, charging or renewable models.

Pull requests should state what changed, how it was tested, what assumptions were introduced and which data status is displayed.


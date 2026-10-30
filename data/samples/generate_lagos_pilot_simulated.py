"""Generates the simulated Lagos pilot telemetry CSV.

This is SIMULATED data (data_status="simulated"), not a record of real vessel
movement. It exists so the mobility pipeline, landing-clustering and feature
pipelines have enough repeated structure to be meaningfully exercised:
multiple vessels, multiple days, and a handful of landing points that are
genuinely visited more than once. A few deliberately bad observations are
injected near the end so the anomaly-detection experiment has known ground
truth to check itself against.

Run: uv run --project apps/api python data/samples/generate_lagos_pilot_simulated.py
"""

from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta, timezone

random.seed(42)

# Four simulated landing points on the Lagos lagoon (fictional pilot
# corridor, not a claim about any real, verified jetty).
LANDINGS = {
    "A": (6.4550, 3.4050),  # west lagoon
    "B": (6.4520, 3.3900),  # near-shore crossing point
    "C": (6.4680, 3.4210),  # north crossing point
    "D": (6.4390, 3.4180),  # south crossing point
}

# Each route is a pair of landings a vessel shuttles between.
ROUTES = {
    "EST-01": ("A", "B"),
    "EST-02": ("A", "B"),
    "EST-03": ("A", "C"),
    "EST-04": ("B", "D"),
    "EST-05": ("C", "D"),
    "EST-06": ("A", "D"),
}

DAYS = 5
TRIPS_PER_VESSEL_PER_DAY = 3
SAMPLE_INTERVAL_SECONDS = 120
CRUISE_SPEED_KNOTS = (7.0, 12.0)
KNOTS_TO_MPS = 0.5144447


def _destination_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Move (lat, lon) by distance_m metres along bearing_deg using a local flat-earth approximation.

    Good enough at this pilot's scale (a few km, near the equator) - not a
    replacement for a proper geodesic when distances get large.
    """
    earth_radius_m = 6371008.8
    bearing = math.radians(bearing_deg)
    d_lat = (distance_m * math.cos(bearing)) / earth_radius_m
    d_lon = (distance_m * math.sin(bearing)) / (earth_radius_m * math.cos(math.radians(lat)))
    return lat + math.degrees(d_lat), lon + math.degrees(d_lon)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6371008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    value = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_m * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return math.degrees(math.atan2(y, x))


def generate_leg(vessel_id: str, origin_key: str, dest_key: str, start_time: datetime, rows: list[dict]) -> datetime:
    origin_lat, origin_lon = LANDINGS[origin_key]
    dest_lat, dest_lon = LANDINGS[dest_key]

    # Jitter the moored position slightly so repeated visits to the "same"
    # landing form a small cloud of points rather than one exact coordinate -
    # this is what makes DBSCAN's epsilon parameter meaningful later.
    lat = origin_lat + random.uniform(-0.00012, 0.00012)
    lon = origin_lon + random.uniform(-0.00012, 0.00012)
    t = start_time

    dwell_minutes = random.uniform(3, 11)
    dwell_steps = max(1, int(dwell_minutes * 60 / SAMPLE_INTERVAL_SECONDS))
    for _ in range(dwell_steps):
        rows.append(_row(vessel_id, t, lat, lon, random.uniform(0.0, 0.4)))
        t += timedelta(seconds=SAMPLE_INTERVAL_SECONDS)

    bearing = _bearing_deg(lat, lon, dest_lat, dest_lon)
    total_distance_m = _haversine_m(lat, lon, dest_lat, dest_lon)
    travelled = 0.0
    while travelled < total_distance_m:
        speed_knots = random.uniform(*CRUISE_SPEED_KNOTS)
        step_distance_m = speed_knots * KNOTS_TO_MPS * SAMPLE_INTERVAL_SECONDS
        travelled = min(travelled + step_distance_m, total_distance_m)
        lat, lon = _destination_point(lat, lon, bearing, step_distance_m)
        rows.append(_row(vessel_id, t, lat, lon, round(speed_knots, 2)))
        t += timedelta(seconds=SAMPLE_INTERVAL_SECONDS)

    lat, lon = dest_lat + random.uniform(-0.00012, 0.00012), dest_lon + random.uniform(-0.00012, 0.00012)
    dwell_minutes = random.uniform(3, 11)
    dwell_steps = max(1, int(dwell_minutes * 60 / SAMPLE_INTERVAL_SECONDS))
    for _ in range(dwell_steps):
        rows.append(_row(vessel_id, t, lat, lon, random.uniform(0.0, 0.4)))
        t += timedelta(seconds=SAMPLE_INTERVAL_SECONDS)

    return t


def _row(vessel_id: str, t: datetime, lat: float, lon: float, speed: float) -> dict:
    return {
        "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": round(lat, 7),
        "longitude": round(lon, 7),
        "speed": round(speed, 2),
        "vessel_id": vessel_id,
    }


def build_rows() -> list[dict]:
    rows: list[dict] = []
    base_day = datetime(2026, 9, 8, tzinfo=timezone.utc)
    for day in range(DAYS):
        day_start = base_day + timedelta(days=day)
        for vessel_id, (a, b) in ROUTES.items():
            t = day_start + timedelta(minutes=random.uniform(0, 45))
            here, there = a, b
            for _ in range(TRIPS_PER_VESSEL_PER_DAY):
                t = generate_leg(vessel_id, here, there, t, rows)
                t += timedelta(minutes=random.uniform(20, 60))  # gap that starts a new trip
                here, there = there, here

    # --- Deliberately injected anomalies (ground truth for the anomaly
    # detection experiment in experiments/anomaly_detection/) ---
    anomaly_time = base_day + timedelta(days=2, hours=10)
    rows.append(_row("EST-02", anomaly_time, 6.4600, 3.4000, 2.0))
    # 1) Impossible speed: a single observation claiming 65 knots.
    rows.append(_row("EST-02", anomaly_time + timedelta(seconds=SAMPLE_INTERVAL_SECONDS), 6.4610, 3.4005, 65.0))
    rows.append(_row("EST-02", anomaly_time + timedelta(seconds=2 * SAMPLE_INTERVAL_SECONDS), 6.4612, 3.4006, 6.0))

    jump_time = base_day + timedelta(days=3, hours=9)
    rows.append(_row("EST-04", jump_time, 6.4520, 3.3900, 5.0))
    # 2) Coordinate jump: next fix ~40km away one sample later (GPS glitch, not real travel).
    rows.append(_row("EST-04", jump_time + timedelta(seconds=SAMPLE_INTERVAL_SECONDS), 6.700, 3.700, 5.0))
    rows.append(_row("EST-04", jump_time + timedelta(seconds=2 * SAMPLE_INTERVAL_SECONDS), 6.4525, 3.3905, 5.0))

    stuck_time = base_day + timedelta(days=4, hours=14)
    # 3) Abnormally long dwell at a non-landing location (~70 minutes stationary mid-lagoon).
    stuck_lat, stuck_lon = 6.4470, 3.4050
    for i in range(35):
        rows.append(_row("EST-06", stuck_time + timedelta(seconds=i * SAMPLE_INTERVAL_SECONDS), stuck_lat, stuck_lon, 0.1))

    rows.sort(key=lambda r: (r["vessel_id"], r["timestamp"]))
    return rows


def main() -> None:
    rows = build_rows()
    out_path = "data/samples/lagos_pilot_simulated.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "latitude", "longitude", "speed", "vessel_id"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} observations to {out_path}")


if __name__ == "__main__":
    main()

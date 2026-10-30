from pathlib import Path

from estuara_api.mobility.analysis import (
    MobilityDataError,
    analyse_csv,
)

SAMPLE_CSV_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "samples" / "lagos_pilot_simulated.csv"
)

VALID_CSV = b"""timestamp,latitude,longitude,speed,vessel_id
2026-09-15T08:00:00Z,6.5790,3.9830,0.2,V001
2026-09-15T08:02:00Z,6.5790,3.9830,0.1,V001
2026-09-15T08:03:00Z,6.5800,3.9820,8.0,V001
2026-09-15T08:06:00Z,6.5900,3.9700,10.0,V001
2026-09-15T09:00:00Z,6.5900,3.9700,0.2,V001
2026-09-15T09:03:00Z,6.5900,3.9700,0.1,V001
"""


def test_analysis_reconstructs_trips_and_stops() -> None:
    result = analyse_csv(VALID_CSV, "pilot.csv", data_status="simulated", trip_gap_minutes=30)
    assert result.summary.vessels == 1
    assert result.summary.trips == 2
    assert result.summary.detected_stops == 2
    assert result.provenance.data_status == "simulated"
    assert result.routes_geojson["type"] == "FeatureCollection"


def test_alias_columns_are_accepted() -> None:
    content = b"datetime,lat,lng,sog,boat_id\n2026-01-01T00:00:00Z,6.5,3.9,5,B1\n"
    result = analyse_csv(content, "aliases.csv")
    assert result.summary.observations == 1
    assert result.trips[0].vessel_id == "B1"
    assert result.routes_geojson["features"] == []
    assert "cannot form route geometry" in result.warnings[0]


def test_invalid_coordinates_are_reported_not_silently_kept() -> None:
    content = VALID_CSV + b"2026-09-15T10:00:00Z,999,3.9,4,V001\n"
    result = analyse_csv(content, "mixed.csv")
    assert result.provenance.rejected_rows == 1
    assert result.warnings


def test_missing_required_column_fails_clearly() -> None:
    content = b"timestamp,latitude,speed,vessel_id\n2026-01-01T00:00:00Z,6.5,5,B1\n"
    try:
        analyse_csv(content, "bad.csv")
    except MobilityDataError as exc:
        assert "longitude" in str(exc)
    else:
        raise AssertionError("Expected missing longitude to fail")


def test_landing_points_are_exposed_alongside_stops() -> None:
    result = analyse_csv(VALID_CSV, "pilot.csv", data_status="simulated")
    assert result.landing_point_report.algorithm == "DBSCAN"
    assert result.landing_point_report.training_point_count == len(result.stops)
    assert result.landing_points_geojson["type"] == "FeatureCollection"
    for candidate in result.landing_points:
        assert candidate.verification_status == "unverified"


def test_simulated_lagos_pilot_dataset_produces_landing_candidates() -> None:
    content = SAMPLE_CSV_PATH.read_bytes()
    result = analyse_csv(content, "lagos_pilot_simulated.csv", data_status="simulated")
    assert result.provenance.data_status == "simulated"
    assert result.summary.vessels > 0
    assert result.landing_point_report.training_point_count == len(result.stops)
    assert result.landing_point_report.cluster_count == len(result.landing_points)
    assert all(c.verification_status == "unverified" for c in result.landing_points)

from datetime import UTC, datetime

import pytest

from estuara_api.mobility.landing_points import (
    LandingPointClusterer,
    LandingPointConfigError,
    ModelNotFittedError,
    StopObservation,
)

# Two tight groups of stops ~11 m apart (well inside a 120 m radius), and one
# isolated stop roughly 1.1 km from both, which is well outside it.
GROUP_A = (6.4550, 3.4050)
GROUP_B = (6.5000, 3.5000)
ISOLATED = (6.4650, 3.4150)


def _stop(
    stop_id: str,
    trip_id: str,
    vessel_id: str,
    coords: tuple[float, float],
    jitter: float,
    dwell_minutes: float = 5.0,
    hour: int = 8,
) -> StopObservation:
    lat, lon = coords
    arrived = datetime(2026, 9, 15, hour, 0, tzinfo=UTC)
    return StopObservation(
        stop_id=stop_id,
        trip_id=trip_id,
        vessel_id=vessel_id,
        latitude=lat + jitter,
        longitude=lon + jitter,
        arrived_at=arrived,
        departed_at=arrived,
        dwell_minutes=dwell_minutes,
    )


def test_two_repeated_groups_form_two_clusters() -> None:
    stops = [
        _stop("S1", "T1", "V1", GROUP_A, 0.0000),
        _stop("S2", "T2", "V1", GROUP_A, 0.00003),
        _stop("S3", "T3", "V2", GROUP_A, -0.00003),
        _stop("S4", "T4", "V1", GROUP_B, 0.0000),
        _stop("S5", "T5", "V2", GROUP_B, 0.00003),
    ]
    model = LandingPointClusterer(radius_meters=120, min_visits=2).fit(stops)
    candidates = model.get_candidates()
    assert len(candidates) == 2
    assert {c.visit_count for c in candidates} == {3, 2}


def test_isolated_stop_remains_noise() -> None:
    stops = [
        _stop("S1", "T1", "V1", GROUP_A, 0.0000),
        _stop("S2", "T2", "V1", GROUP_A, 0.00003),
        _stop("S3", "T3", "V2", GROUP_A, -0.00003),
        _stop("S4", "T4", "V1", ISOLATED, 0.0000),
    ]
    model = LandingPointClusterer(radius_meters=120, min_visits=2).fit(stops)
    report = model.get_report()
    assert len(model.get_candidates()) == 1
    assert report.noise_point_count == 1
    assert report.training_point_count == 4


def test_empty_training_data_is_handled_safely() -> None:
    model = LandingPointClusterer(radius_meters=120, min_visits=2).fit([])
    assert model.get_candidates() == []
    report = model.get_report()
    assert report.training_point_count == 0
    assert report.cluster_count == 0
    assert report.noise_point_count == 0


@pytest.mark.parametrize(
    "radius_meters,min_visits",
    [(0, 2), (-5, 2), (120, 1), (120, 0)],
)
def test_invalid_configuration_is_rejected(radius_meters: float, min_visits: int) -> None:
    with pytest.raises(LandingPointConfigError):
        LandingPointClusterer(radius_meters=radius_meters, min_visits=min_visits)


def test_results_are_refused_before_fit() -> None:
    model = LandingPointClusterer(radius_meters=120, min_visits=2)
    with pytest.raises(ModelNotFittedError):
        model.get_candidates()
    with pytest.raises(ModelNotFittedError):
        model.get_report()


def test_geographic_distance_handling_respects_radius() -> None:
    # ~90 m apart: should cluster at a 120 m radius, not at a 50 m radius.
    near_stops = [
        _stop("S1", "T1", "V1", GROUP_A, 0.0),
        _stop("S2", "T2", "V1", (GROUP_A[0] + 0.0008, GROUP_A[1]), 0.0),
    ]
    clustered = LandingPointClusterer(radius_meters=120, min_visits=2).fit(near_stops)
    assert len(clustered.get_candidates()) == 1

    separated = LandingPointClusterer(radius_meters=50, min_visits=2).fit(near_stops)
    assert len(separated.get_candidates()) == 0
    assert separated.get_report().noise_point_count == 2


def test_candidate_aggregates_across_vessels_and_trips() -> None:
    stops = [
        _stop("S1", "T1", "V1", GROUP_A, 0.0, dwell_minutes=4.0),
        _stop("S2", "T2", "V2", GROUP_A, 0.00002, dwell_minutes=6.0),
        _stop("S3", "T3", "V2", GROUP_A, -0.00002, dwell_minutes=8.0),
    ]
    model = LandingPointClusterer(radius_meters=120, min_visits=2).fit(stops)
    candidate = model.get_candidates()[0]
    assert candidate.visit_count == 3
    assert candidate.trip_count == 3
    assert candidate.vessel_count == 2
    assert candidate.mean_dwell_minutes == pytest.approx(6.0)
    assert candidate.verification_status == "unverified"


def test_model_report_metadata() -> None:
    stops = [
        _stop("S1", "T1", "V1", GROUP_A, 0.0),
        _stop("S2", "T2", "V1", GROUP_A, 0.00002),
    ]
    report = LandingPointClusterer(radius_meters=150, min_visits=2).fit(stops).get_report()
    assert report.model_name == "landing_point_clusterer"
    assert report.algorithm == "DBSCAN"
    assert report.distance_metric == "haversine"
    assert report.radius_meters == 150
    assert report.min_visits == 2
    assert report.trained_at.tzinfo is not None

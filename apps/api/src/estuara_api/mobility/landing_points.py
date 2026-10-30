"""Unsupervised discovery of recurring landing-point candidates from detected stops.

A detected stop (see `analysis.py`) is one vessel's low-speed dwell during one
trip. It is not yet evidence of a place. `LandingPointClusterer` fits a DBSCAN
model over many stops, across many trips and vessels, to find coordinates
where stops keep recurring closely together. Those recurring locations become
"landing-point candidates": possible terminals or informal landing points that
still require human verification.

This is unsupervised clustering, not supervised training. There are no
labelled landing points to learn from yet, so the model cannot report
precision, recall or accuracy. It can only describe the structure it found in
the stop observations it was given.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import numpy as np
from sklearn.cluster import DBSCAN

EARTH_RADIUS_METERS = 6_371_008.8

MODEL_NAME = "landing_point_clusterer"
MODEL_VERSION = "0.1.0"
ALGORITHM = "DBSCAN"
DISTANCE_METRIC = "haversine"

VerificationStatus = Literal["unverified"]


class LandingPointConfigError(ValueError):
    """Raised when the clusterer is configured with unusable parameters."""


class ModelNotFittedError(RuntimeError):
    """Raised when candidates or a report are requested before `fit()` runs."""


@dataclass(frozen=True)
class StopObservation:
    """One detected, low-speed vessel dwell. The clustering model's raw input."""

    stop_id: str
    trip_id: str
    vessel_id: str
    latitude: float
    longitude: float
    arrived_at: datetime
    departed_at: datetime
    dwell_minutes: float


@dataclass(frozen=True)
class LandingPointCandidate:
    """A recurring location discovered by the model. Not a verified terminal."""

    candidate_id: str
    latitude: float
    longitude: float
    visit_count: int
    trip_count: int
    vessel_count: int
    mean_dwell_minutes: float
    first_observed_at: datetime
    last_observed_at: datetime
    evidence_score: float
    verification_status: VerificationStatus = "unverified"


@dataclass(frozen=True)
class ClusteringReport:
    """Metadata describing how the model was fitted, not how well it performs."""

    model_name: str
    model_version: str
    algorithm: str
    distance_metric: str
    training_point_count: int
    cluster_count: int
    noise_point_count: int
    radius_meters: float
    min_visits: int
    trained_at: datetime


def _evidence_score(visit_count: int, vessel_count: int, min_visits: int) -> float:
    """A bounded, explainable prioritisation score in [0, 1]. Not a probability.

    It rewards two independent signals a real landing point should show:
    repeated visits well beyond the minimum, and use by more than one vessel
    (a single vessel idling repeatedly at the same spot is weaker evidence
    than several different vessels converging on it). Each signal saturates
    so no single very active vessel can dominate the score.
    """
    visit_component = min(visit_count / (min_visits * 4), 1.0)
    vessel_component = min(vessel_count / 3, 1.0)
    return round(0.6 * visit_component + 0.4 * vessel_component, 3)


class LandingPointClusterer:
    """Discovers recurring landing-point candidates from detected vessel stops.

    Fits a DBSCAN model using the haversine metric so the configured radius
    behaves as a real geographic distance rather than a distance in raw
    latitude/longitude degrees (which are not uniform in size on the Earth's
    surface). Isolated stops that never recur near enough to others remain
    noise and are not reported as candidates.
    """

    def __init__(self, radius_meters: float, min_visits: int) -> None:
        if radius_meters <= 0:
            raise LandingPointConfigError("radius_meters must be greater than zero.")
        if min_visits < 2:
            raise LandingPointConfigError(
                "min_visits must be at least 2; a single stop cannot be a recurring pattern."
            )
        self.radius_meters = float(radius_meters)
        self.min_visits = int(min_visits)
        self._fitted = False
        self._candidates: list[LandingPointCandidate] = []
        self._report: ClusteringReport | None = None

    def fit(self, stops: list[StopObservation]) -> LandingPointClusterer:
        """Fits the clustering model over the supplied stop observations."""
        labels = self._cluster_labels(stops)
        self._candidates = self._build_candidates(stops, labels)
        noise_count = int(np.sum(labels == -1)) if len(stops) else 0
        self._report = ClusteringReport(
            model_name=MODEL_NAME,
            model_version=MODEL_VERSION,
            algorithm=ALGORITHM,
            distance_metric=DISTANCE_METRIC,
            training_point_count=len(stops),
            cluster_count=len(self._candidates),
            noise_point_count=noise_count,
            radius_meters=self.radius_meters,
            min_visits=self.min_visits,
            trained_at=datetime.now(UTC),
        )
        self._fitted = True
        return self

    def get_candidates(self) -> list[LandingPointCandidate]:
        """Returns discovered candidates. Requires `fit()` to have run."""
        self._require_fitted()
        return list(self._candidates)

    def get_report(self) -> ClusteringReport:
        """Returns the training report. Requires `fit()` to have run."""
        self._require_fitted()
        assert self._report is not None  # guaranteed once _fitted is True
        return self._report

    def _require_fitted(self) -> None:
        if not self._fitted:
            raise ModelNotFittedError(
                "LandingPointClusterer has no results yet; call fit() first."
            )

    def _cluster_labels(self, stops: list[StopObservation]) -> np.ndarray:
        if not stops:
            return np.array([], dtype=int)
        coordinates_deg = np.array([[stop.latitude, stop.longitude] for stop in stops])
        coordinates_rad = np.radians(coordinates_deg)
        eps_radians = self.radius_meters / EARTH_RADIUS_METERS
        model = DBSCAN(eps=eps_radians, min_samples=self.min_visits, metric="haversine")
        return model.fit_predict(coordinates_rad)

    def _build_candidates(
        self, stops: list[StopObservation], labels: np.ndarray
    ) -> list[LandingPointCandidate]:
        candidates: list[LandingPointCandidate] = []
        for rank, cluster_id in enumerate(sorted(set(labels) - {-1}), start=1):
            members = [stop for stop, label in zip(stops, labels) if label == cluster_id]
            trips = {member.trip_id for member in members}
            vessels = {member.vessel_id for member in members}
            candidates.append(
                LandingPointCandidate(
                    candidate_id=f"LP-{rank:03d}",
                    latitude=round(float(np.mean([m.latitude for m in members])), 7),
                    longitude=round(float(np.mean([m.longitude for m in members])), 7),
                    visit_count=len(members),
                    trip_count=len(trips),
                    vessel_count=len(vessels),
                    mean_dwell_minutes=round(
                        float(np.mean([m.dwell_minutes for m in members])), 2
                    ),
                    first_observed_at=min(m.arrived_at for m in members),
                    last_observed_at=max(m.departed_at for m in members),
                    evidence_score=_evidence_score(len(members), len(vessels), self.min_visits),
                    verification_status="unverified",
                )
            )
        return candidates

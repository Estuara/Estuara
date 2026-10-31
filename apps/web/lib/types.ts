import type { FeatureCollection, LineString, Point } from "geojson";

export type Trip = {
  trip_id: string;
  vessel_id: string;
  started_at: string;
  ended_at: string;
  duration_minutes: number;
  distance_km: number;
  average_speed_knots: number;
  maximum_speed_knots: number;
  observation_count: number;
  stop_count: number;
  origin: [number, number];
  destination: [number, number];
};

export type Stop = {
  stop_id: string;
  trip_id: string;
  vessel_id: string;
  latitude: number;
  longitude: number;
  arrived_at: string;
  departed_at: string;
  duration_minutes: number;
  point_count: number;
};

export type LandingPointCandidate = {
  candidate_id: string;
  latitude: number;
  longitude: number;
  visit_count: number;
  trip_count: number;
  vessel_count: number;
  mean_dwell_minutes: number;
  first_observed_at: string;
  last_observed_at: string;
  evidence_score: number;
  verification_status: "unverified";
};

export type LandingPointModelReport = {
  model_name: string;
  model_version: string;
  algorithm: string;
  distance_metric: string;
  training_point_count: number;
  cluster_count: number;
  noise_point_count: number;
  radius_meters: number;
  min_visits: number;
  trained_at: string;
};

export type AnalysisSettings = {
  trip_gap_minutes: number;
  stop_speed_knots: number;
  stop_min_duration_minutes: number;
  landing_radius_meters: number;
  landing_min_visits: number;
  max_plausible_speed_knots: number;
};

export type ValidationIssue = {
  row: number | null;
  vessel_id: string | null;
  timestamp: string | null;
  field: string | null;
  code: string;
  disposition: "rejected" | "flagged";
  message: string;
  value: string | null;
};

export type DataQualityReport = {
  rejected_by_reason: Record<string, number>;
  flagged_by_reason: Record<string, number>;
  excluded_from_geometry_rows: number;
  speeds_derived: number;
  speeds_replaced: number;
  thresholds: {
    max_plausible_speed_knots: number;
    min_position_jump_km: number;
    jump_reset_after: number;
  };
  issues: ValidationIssue[];
  issues_truncated: boolean;
};

export type MobilityAnalysis = {
  analysis_id: string;
  status: "completed";
  summary: {
    vessels: number;
    trips: number;
    observations: number;
    detected_stops: number;
    total_distance_km: number;
    total_duration_hours: number;
    average_trip_distance_km: number;
  };
  trips: Trip[];
  stops: Stop[];
  landing_points: LandingPointCandidate[];
  landing_point_report: LandingPointModelReport;
  routes_geojson: FeatureCollection<LineString>;
  stops_geojson: FeatureCollection<Point>;
  landing_points_geojson: FeatureCollection<Point>;
  provenance: {
    source_filename: string;
    data_status: string;
    processed_at: string;
    input_rows: number;
    accepted_rows: number;
    rejected_rows: number;
    flagged_rows: number;
    transformations: string[];
    processing_seconds: number | null;
  };
  settings: AnalysisSettings;
  quality: DataQualityReport;
  warnings: string[];
};

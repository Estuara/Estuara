import type { AnalysisSettings, MobilityAnalysis } from "@/lib/types";

// "" means same origin: the Cloudflare preview serves the API from the same
// Worker as this page. `pnpm dev` talks to a local uvicorn by default.
export const API_URL =
  process.env.NEXT_PUBLIC_ESTUARA_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

export const SAMPLE_PATH = "/data/lagos_pilot_simulated.csv";
export const SAMPLE_FILENAME = "lagos_pilot_simulated.csv";
const REQUEST_TIMEOUT_MS = 120_000;

export const DEFAULT_SETTINGS: AnalysisSettings = {
  trip_gap_minutes: 30,
  stop_speed_knots: 1.5,
  stop_min_duration_minutes: 2,
  landing_radius_meters: 120,
  landing_min_visits: 2,
  max_plausible_speed_knots: 40,
};

// Mirrors the API's accepted ranges (apps/api/src/estuara_api/main.py).
export const SETTING_FIELDS: {
  key: keyof AnalysisSettings;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  help: string;
}[] = [
  { key: "trip_gap_minutes", label: "Trip gap", unit: "min", min: 5, max: 1440, step: 1, help: "A new movement segment starts after an observation gap longer than this." },
  { key: "stop_speed_knots", label: "Stop speed", unit: "kn", min: 0, max: 15, step: 0.1, help: "Observations at or below this speed count as stationary." },
  { key: "stop_min_duration_minutes", label: "Minimum stop", unit: "min", min: 0.5, max: 180, step: 0.5, help: "A stop must stay slow for at least this long." },
  { key: "landing_radius_meters", label: "Landing radius", unit: "m", min: 20, max: 2000, step: 10, help: "DBSCAN radius: stops closer than this can form one landing candidate." },
  { key: "landing_min_visits", label: "Minimum visits", unit: "stops", min: 2, max: 50, step: 1, help: "Stops needed before a location becomes a landing candidate." },
  { key: "max_plausible_speed_knots", label: "Plausible speed", unit: "kn", min: 20, max: 80, step: 1, help: "Speeds above this, and positions only reachable faster than this, are flagged. Keep it above the fleet's real top speed." },
];

export type ErrorKind = "connectivity" | "malformed_file" | "validation" | "api_failure";

// Render's free plan sleeps an idle service; the next request can take tens
// of seconds to answer while the instance boots.
export const COLD_START_HINT =
  "On free hosting the analysis service may be waking from standby, which can take up to a minute - wait a moment and try again.";

export class AnalysisError extends Error {
  constructor(
    readonly kind: ErrorKind,
    message: string,
    readonly code?: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

export const ERROR_TITLES: Record<ErrorKind, string> = {
  connectivity: "Cannot reach the analysis service",
  malformed_file: "File could not be read",
  validation: "Data failed validation",
  api_failure: "Analysis service error",
};

const MALFORMED_CODES = new Set(["malformed_csv", "empty_file", "unsupported_file", "file_too_large", "too_many_rows"]);
const VALIDATION_CODES = new Set(["missing_columns", "ambiguous_columns", "no_valid_rows", "invalid_parameters", "invalid_telemetry"]);

function kindForCode(code: string | undefined, status: number): ErrorKind {
  if (code && MALFORMED_CODES.has(code)) return "malformed_file";
  if (code && VALIDATION_CODES.has(code)) return "validation";
  if (status === 413 || status === 415) return "malformed_file";
  return "api_failure";
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_URL}${path}`, { ...init, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === "TimeoutError") {
      throw new AnalysisError("connectivity", `No response from the analysis service within ${REQUEST_TIMEOUT_MS / 1000} seconds. ${COLD_START_HINT}`);
    }
    throw new AnalysisError("connectivity", `The analysis service did not respond. ${COLD_START_HINT}`);
  }
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new AnalysisError("api_failure", `The analysis service returned an unreadable response (HTTP ${response.status}).`, undefined, response.status);
  }
}

export type Health = { status: string; version: string; runtime: string; limits: { max_upload_bytes: number; max_rows: number } };

export async function fetchHealth(): Promise<Health> {
  const response = await request("/api/v1/health");
  const body = (await readJson(response)) as Health;
  if (!response.ok || body.status !== "ok") {
    throw new AnalysisError("api_failure", `The analysis service is not ready (HTTP ${response.status}).`, undefined, response.status);
  }
  return body;
}

export async function analyseFile(
  file: File,
  dataStatus: "simulated" | "user-provided",
  settings: AnalysisSettings,
  maxUploadBytes?: number,
): Promise<MobilityAnalysis> {
  if (!file.name.toLowerCase().endsWith(".csv")) {
    throw new AnalysisError("malformed_file", "Choose a file with a .csv extension.", "unsupported_file");
  }
  if (file.size === 0) {
    throw new AnalysisError("malformed_file", "The selected file is empty.", "empty_file");
  }
  if (maxUploadBytes && file.size > maxUploadBytes) {
    const limit = (maxUploadBytes / 1024 / 1024).toFixed(0);
    const size = (file.size / 1024 / 1024).toFixed(1);
    throw new AnalysisError("malformed_file", `The file is ${size} MB; this deployment accepts up to ${limit} MB.`, "file_too_large");
  }
  const form = new FormData();
  form.append("file", file);
  form.append("data_status", dataStatus);
  for (const [key, value] of Object.entries(settings)) form.append(key, String(value));

  const response = await request("/api/v1/mobility/analyse", { method: "POST", body: form });
  const body = await readJson(response);
  if (!response.ok) {
    const detail = (body as { detail?: unknown }).detail;
    const structured = typeof detail === "object" && detail !== null ? (detail as { code?: string; message?: string }) : undefined;
    const message = structured?.message ?? (typeof detail === "string" ? detail : `Analysis failed (HTTP ${response.status}).`);
    throw new AnalysisError(kindForCode(structured?.code, response.status), message, structured?.code, response.status);
  }
  return body as MobilityAnalysis;
}

export async function loadSampleFile(): Promise<File> {
  let response: Response;
  try {
    response = await fetch(SAMPLE_PATH);
  } catch {
    throw new AnalysisError("connectivity", "The bundled sample dataset could not be downloaded.");
  }
  if (!response.ok) {
    throw new AnalysisError("api_failure", `The bundled sample dataset is missing from this deployment (HTTP ${response.status}).`, undefined, response.status);
  }
  return new File([await response.blob()], SAMPLE_FILENAME, { type: "text/csv" });
}

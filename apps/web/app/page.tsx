"use client";

import dynamic from "next/dynamic";
import { ChangeEvent, useCallback, useEffect, useMemo, useState } from "react";
import QualityPanel from "@/components/QualityPanel";
import SettingsModal from "@/components/SettingsModal";
import { AnalysisError, DEFAULT_SETTINGS, ERROR_TITLES, ErrorKind, analyseFile, fetchHealth, loadSampleFile } from "@/lib/api";
import type { AnalysisSettings, MobilityAnalysis } from "@/lib/types";

const MobilityMap = dynamic(() => import("@/components/MobilityMap"), { ssr: false });

type DataStatus = "simulated" | "user-provided";
type ErrorState = { kind: ErrorKind; message: string };

function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    overview: "M4 13h6V4H4v9Zm10 7h6v-9h-6v9ZM4 20h6v-3H4v3Zm10-13h6V4h-6v3Z",
    mobility: "M3 17h18M5 14l4-8 5 5 3-6 2 9M9 6h.01M14 11h.01M17 5h.01",
    fleet: "M3 17h18l-2 3H5l-2-3Zm2-3 2-7h10l2 7H5Zm5-7V4h4v3",
    routes: "M6 19c0-5 12-4 12-9 0-3-2-5-5-5M6 19l-2-2m2 2-2 2M13 5l2-2m-2 2 2 2",
    terminals: "M5 20V9l7-5 7 5v11M9 20v-6h6v6M3 20h18",
    charging: "M13 2 5 14h7l-1 8 8-12h-7l1-8Z",
    energy: "M12 22V8M5 14c0-4 3-7 7-7M19 14c0-4-3-7-7-7M7 5 5 3M17 5l2-2",
    data: "M4 6c0 2 16 2 16 0S4 4 4 6Zm0 0v6c0 2 16 2 16 0V6M4 12v6c0 2 16 2 16 0v-6",
  };
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={paths[name]} fill={name === "overview" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function toErrorState(reason: unknown): ErrorState {
  if (reason instanceof AnalysisError) return { kind: reason.kind, message: reason.message };
  return { kind: "api_failure", message: reason instanceof Error ? reason.message : "The analysis failed unexpectedly." };
}

function methodNote(settings: AnalysisSettings) {
  return (
    `Segments split after a ${settings.trip_gap_minutes}-minute observation gap; they are not verified terminal-to-terminal journeys. ` +
    `Stops require speeds at or below ${settings.stop_speed_knots} kn for at least ${settings.stop_min_duration_minutes} min. ` +
    `Landing candidates: DBSCAN, ${settings.landing_radius_meters} m radius, at least ${settings.landing_min_visits} stops. ` +
    `Speeds above ${settings.max_plausible_speed_knots} kn and unreachable positions are flagged.`
  );
}

export default function Home() {
  const [analysis, setAnalysis] = useState<MobilityAnalysis | null>(null);
  const [selectedTrip, setSelectedTrip] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ErrorState | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<AnalysisSettings>(DEFAULT_SETTINGS);
  const [dataset, setDataset] = useState<{ file: File; status: DataStatus } | null>(null);
  const [maxUploadBytes, setMaxUploadBytes] = useState<number | undefined>(undefined);
  const [reconnecting, setReconnecting] = useState(false);

  const analyse = useCallback(async (file: File, status: DataStatus, nextSettings: AnalysisSettings) => {
    setLoading(true);
    setError(null);
    // Close dialogs so progress and any error banner are visible.
    setUploadOpen(false);
    setSettingsOpen(false);
    try {
      const result = await analyseFile(file, status, nextSettings, maxUploadBytes);
      setAnalysis(result);
      setDataset({ file, status });
      setSettings(nextSettings);
      setSelectedTrip(result.trips[0]?.trip_id ?? null);
    } catch (reason) {
      // Keep the previous analysis on screen; the banner explains what failed.
      setError(toErrorState(reason));
    } finally {
      setLoading(false);
    }
  }, [maxUploadBytes]);

  const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Free hosting (Render) sleeps an idle API; poll the health check so
      // the automatic first analysis also covers the wake-up delay instead
      // of failing while the service is still starting.
      for (let attempt = 0; !cancelled; attempt++) {
        try {
          const health = await fetchHealth();
          if (cancelled) return;
          setMaxUploadBytes(health.limits.max_upload_bytes);
          setReconnecting(false);
          break;
        } catch {
          if (attempt >= 6) {
            setError(toErrorState(new AnalysisError("connectivity", "The analysis service did not come up after several attempts.")));
            setLoading(false);
            return;
          }
          setReconnecting(true);
          await wait(8_000);
        }
      }
      if (cancelled) return;
      try {
        const file = await loadSampleFile();
        if (!cancelled) await analyse(file, "simulated", DEFAULT_SETTINGS);
      } catch (reason) {
        if (cancelled) return;
        setError(toErrorState(reason));
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Auto-analyse the bundled sample once, after the API is reachable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = useMemo(
    () => analysis?.trips.find((trip) => trip.trip_id === selectedTrip) ?? null,
    [analysis, selectedTrip],
  );

  function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Reset so choosing the same file again still triggers a new analysis.
    event.target.value = "";
    if (file) void analyse(file, "user-provided", settings);
  }

  const uploadLimit = maxUploadBytes ? `${(maxUploadBytes / 1024 / 1024).toFixed(0)} MB` : "the deployment limit";
  const metrics = [
    ["Observed vessels", analysis?.summary.vessels ?? "—", "distinct vessel IDs"],
    ["Movement segments", analysis?.summary.trips ?? "—", "time-gap split · not verified journeys"],
    ["Distance observed", analysis ? `${analysis.summary.total_distance_km.toFixed(1)} km` : "—", "excludes flagged position jumps"],
    ["Stops detected", analysis?.summary.detected_stops ?? "—", "threshold-based"],
    ["Landing candidates", analysis?.landing_points.length ?? "—", "unverified, DBSCAN"],
  ];
  const warningCount = analysis?.warnings.length ?? 0;
  const transformationCount = analysis?.provenance.transformations.length ?? 0;

  return (
    <main className="app-frame">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">E</span><div><strong>ESTUARA</strong><small>BLUE MOBILITY INTELLIGENCE</small></div></div>
        <nav>
          <p>WORKSPACE</p>
          {[["overview", "Overview"], ["mobility", "Mobility"], ["fleet", "Fleet"], ["routes", "Routes"], ["terminals", "Terminals"]].map(([icon, label]) => (
            <button className={label === "Mobility" ? "active" : ""} key={label}><Icon name={icon} />{label}{label !== "Mobility" && <span>Soon</span>}</button>
          ))}
          <p>TRANSITION</p>
          {[["charging", "Charging"], ["energy", "Renewable energy"], ["data", "Data explorer"]].map(([icon, label]) => (
            <button key={label}><Icon name={icon} />{label}<span>Soon</span></button>
          ))}
        </nav>
        <div className="phase-card"><span>FOUNDATION RELEASE</span><strong>Observe</strong><p>GPS evidence before electrification assumptions.</p><div><i /></div><small>1 of 4 stages active</small></div>
        <div className="user"><span>TP</span><div><strong>Team preview</strong><small>Demo workspace · no sign-in</small></div></div>
      </aside>

      <section className="workspace">
        <header>
          <div><span className="crumb">ESTUARA / MOBILITY</span><h1>Marine movement, made legible.</h1><p>Reconstruct ferry operations from GPS observations before planning electric routes and charging infrastructure.</p></div>
          <div className="header-actions">
            <button className="secondary-button" onClick={() => setSettingsOpen(true)} disabled={!dataset || loading}>Analysis settings</button>
            <button className="upload-button" onClick={() => setUploadOpen(true)}>＋ Import GPS data</button>
          </div>
        </header>

        {error && (
          <div className={`error-banner ${error.kind}`} role="alert">
            <strong>{ERROR_TITLES[error.kind]}</strong>
            <span>{error.message}{analysis && " The previous results are still shown."}</span>
            <button onClick={() => setError(null)} aria-label="Dismiss">×</button>
          </div>
        )}
        {reconnecting && !error && (
          <div className="connecting-note" role="status"><i className="loader small" /> Connecting to the analysis service… free hosting can take up to a minute to wake it.</div>
        )}
        {analysis && (
          <div className="quality-summary">
            <span className={`status ${analysis.provenance.data_status === "simulated" ? "simulated" : ""}`}>{analysis.provenance.data_status}</span>
            <span>{analysis.provenance.source_filename}</span>
            <span>{analysis.provenance.input_rows} rows · {analysis.provenance.rejected_rows} rejected · {analysis.provenance.flagged_rows} flagged</span>
            <a href="#quality">{warningCount} {warningCount === 1 ? "warning" : "warnings"} · {transformationCount} {transformationCount === 1 ? "transformation" : "transformations"} →</a>
            {loading && <span className="analysing"><i className="loader small" /> Re-analysing…</span>}
          </div>
        )}

        <section className="metric-grid">
          {metrics.map(([label, value, note], index) => <article key={label}><div><span>{label}</span><small>0{index + 1}</small></div><strong>{loading && !analysis ? <i className="loader" /> : value}</strong><p>{note}</p></article>)}
        </section>

        <section className="analysis-grid">
          <article className="map-panel"><div className="panel-heading"><div><span>NETWORK VIEW</span><h2>Observed movement</h2></div><div className="legend"><i className="route-line" /> Reconstructed route <i className="stop-dot" /> Detected stop <i className="landing-dot" /> Landing candidate (unverified)</div></div><MobilityMap analysis={analysis} selectedTrip={selectedTrip} onSelectTrip={setSelectedTrip} /></article>
          <article className="evidence-panel"><div className="panel-heading"><div><span>SEGMENT EVIDENCE</span><h2>{selected?.trip_id ?? "No segment selected"}</h2></div><span className="status">{analysis?.provenance.data_status ?? "waiting"}</span></div>
            {selected ? <>
              <div className="trip-hero"><span>{selected.vessel_id}</span><strong>{selected.distance_km.toFixed(2)} km</strong><p>{new Date(selected.started_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})} → {new Date(selected.ended_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</p></div>
              <dl><div><dt>Duration</dt><dd>{selected.duration_minutes.toFixed(0)} min</dd></div><div><dt>Average speed</dt><dd>{selected.average_speed_knots.toFixed(1)} kn</dd></div><div><dt>Peak speed</dt><dd>{selected.maximum_speed_knots.toFixed(1)} kn</dd></div><div><dt>Observations</dt><dd>{selected.observation_count}</dd></div><div><dt>Stops</dt><dd>{selected.stop_count}</dd></div></dl>
            </> : <p className="empty">Import telemetry, then select a segment in the table or on the map.</p>}
            {analysis && <div className="evidence-note"><span>METHOD NOTE</span><p>{methodNote(analysis.settings)}</p></div>}
          </article>
        </section>

        <section className="trips-panel"><div className="panel-heading"><div><span>MOVEMENT SEGMENTS</span><h2>Operational record</h2></div><p>{analysis?.summary.observations ?? 0} observations used · {analysis?.provenance.rejected_rows ?? 0} rejected · {analysis?.quality.excluded_from_geometry_rows ?? 0} excluded from geometry · segments are time-gap splits, not verified terminal-to-terminal journeys</p></div><div className="table-wrap"><table><thead><tr><th>Segment</th><th>Vessel</th><th>Start</th><th>Duration</th><th>Distance</th><th>Avg. speed</th><th>Stops</th><th /></tr></thead><tbody>{analysis?.trips.map((trip) => <tr className={selectedTrip === trip.trip_id ? "selected" : ""} key={trip.trip_id} onClick={() => setSelectedTrip(trip.trip_id)}><td><strong>{trip.trip_id}</strong></td><td>{trip.vessel_id}</td><td>{new Date(trip.started_at).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"})}</td><td>{trip.duration_minutes.toFixed(0)} min</td><td>{trip.distance_km.toFixed(2)} km</td><td>{trip.average_speed_knots.toFixed(1)} kn</td><td>{trip.stop_count}</td><td>→</td></tr>)}</tbody></table></div></section>

        <section className="landing-panel">
          <div className="panel-heading">
            <div><span>LANDING-POINT DISCOVERY</span><h2>Unverified recurring locations</h2></div>
            <p>DBSCAN over haversine distance · not a verified terminal registry</p>
          </div>
          <div className="report-strip">
            <div><span className="label">Training stops</span><strong>{analysis?.landing_point_report.training_point_count ?? "—"}</strong></div>
            <div><span className="label">Clusters discovered</span><strong>{analysis?.landing_point_report.cluster_count ?? "—"}</strong></div>
            <div><span className="label">Noise observations</span><strong>{analysis?.landing_point_report.noise_point_count ?? "—"}</strong></div>
            <div><span className="label">Cluster radius</span><strong>{analysis ? `${analysis.landing_point_report.radius_meters.toFixed(0)} m` : "—"}</strong></div>
            <div><span className="label">Minimum visits</span><strong>{analysis?.landing_point_report.min_visits ?? "—"}</strong></div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Candidate</th><th>Visits</th><th>Trips</th><th>Vessels</th><th>Mean dwell</th><th>Evidence score</th><th>Status</th></tr></thead>
              <tbody>
                {analysis?.landing_points.length ? analysis.landing_points.map((candidate) => (
                  <tr key={candidate.candidate_id} className="static-row">
                    <td><strong>{candidate.candidate_id}</strong></td>
                    <td>{candidate.visit_count}</td>
                    <td>{candidate.trip_count}</td>
                    <td>{candidate.vessel_count}</td>
                    <td>{candidate.mean_dwell_minutes.toFixed(1)} min</td>
                    <td>{candidate.evidence_score.toFixed(2)}</td>
                    <td><span className="unverified-pill">Unverified</span></td>
                  </tr>
                )) : <tr className="static-row"><td colSpan={7} className="empty">No recurring landing-point candidates at the current radius and minimum-visit settings.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <div id="quality"><QualityPanel analysis={analysis} /></div>
      </section>

      {uploadOpen && <div className="modal-backdrop" onMouseDown={() => setUploadOpen(false)}><section className="modal" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={() => setUploadOpen(false)} aria-label="Close">×</button><span className="eyebrow">IMPORT TELEMETRY</span><h2>Add GPS observations</h2><p>Upload one CSV with timestamp, latitude, longitude, vessel ID and optional speed. The source is labelled user-provided and analysed with the current settings. Files are processed in memory and not stored.</p><label className="dropzone"><input type="file" accept=".csv,text/csv" onChange={upload} disabled={loading} /><strong>{loading ? "Analysing…" : "Choose a CSV file"}</strong><span>Maximum {uploadLimit} · raw data is not modified</span></label><div className="schema"><strong>Accepted column aliases</strong><code>timestamp/time/datetime · latitude/lat · longitude/lon/lng · vessel_id/boat_id/device_id · speed/sog (optional, knots; derived from positions if absent)</code></div></section></div>}
      {settingsOpen && dataset && <SettingsModal settings={settings} datasetName={dataset.file.name} onClose={() => setSettingsOpen(false)} onApply={(next) => void analyse(dataset.file, dataset.status, next)} />}
    </main>
  );
}
